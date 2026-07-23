from __future__ import annotations

import copy
import ctypes
import json
import math
import os
import re
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from isoworld.content.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    path_file_stat,
)
from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.models import RUNTIME_API_VERSION, SUPPORTED_RUNTIME_FEATURES
from isoworld.content.portability import (
    is_portable_path_component,
    portable_path_key,
    portable_relative_path,
)
from isoworld.content.renderpack import RenderPackError, load_renderpack
from isoworld.runtime_adapter import (
    RuntimeAdapterKey,
    RuntimeAdapterRegistryError,
    StaticRuntimeAdapterRegistry,
)
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.asset_io import AssetContractError, resolve_artifact
from worldforge.assetpack import AssetPackError, verify_assetpack
from worldforge.game_boundary_policy import validate_lexical_directory_root
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash

RUNTIME_CAPABILITY_CATALOG_FORMAT = "rpg-world-forge.runtime_capability_catalog"
RUNTIME_PRESENTATION_PROFILE_FORMAT = "rpg-world-forge.runtime_presentation_profile"
RUNTIME_ADAPTER_FORMAT = "rpg-world-forge.runtime_adapter"
RUNTIME_COMPOSITION_FORMAT = "rpg-world-forge.runtime_composition"
RUNTIME_COMPATIBILITY_REPORT_FORMAT = "rpg-world-forge.runtime_compatibility_report"
RUNTIME_COMPOSITION_CONTRACT_VERSION = 1

RUNTIME_CAPABILITY_CATALOG_FIELDS = frozenset(
    {"format", "format_version", "capabilities", "content_hash"}
)
RUNTIME_CAPABILITY_FIELDS = frozenset({"id", "domain", "determinism"})
RUNTIME_PRESENTATION_PROFILE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "mode",
        "layers",
        "required_packs",
        "required_capability_ids",
        "content_hash",
    }
)
RUNTIME_ADAPTER_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "version",
        "state",
        "runtime_api",
        "platforms",
        "presentation_modes",
        "capability_ids",
        "components",
        "budgets",
        "content_hash",
    }
)
RUNTIME_COMPOSITION_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "world_id",
        "world_content_hash",
        "release_id",
        "profile",
        "capability_catalog_hash",
        "adapter",
        "packs",
        "required_capability_ids",
        "slot_owners",
        "content_hash",
    }
)
RUNTIME_COMPATIBILITY_REPORT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "composition_hash",
        "world_content_hash",
        "profile_hash",
        "capability_catalog_hash",
        "adapter_hash",
        "pack_hashes",
        "platform",
        "checks",
        "compatible",
        "content_hash",
    }
)

PLATFORMS = ("linux_x86_64", "windows_x86_64")
PRESENTATION_MODES = (
    "2_5d",
    "2_5d_over_3d",
    "2d",
    "2d_over_2_5d",
    "2d_over_3d",
    "3d",
)
PRESENTATION_LAYERS = ("2_5d", "2d", "3d")
PACK_KINDS = ("assetpack", "renderpack", "worldpack")
SLOT_PLANES = ("audio", "ui_overlay", "world_base", "world_overlay")
SLOT_REPRESENTATIONS = ("2_5d", "2d", "3d", "audio")

COMPATIBILITY_CHECK_IDS = (
    "adapter_state",
    "capability_coverage",
    "m5_pack_integrity",
    "pack_profile",
    "platform_support",
    "runtime_api",
    "semantic_slot_ownership",
    "world_identity",
)
COMPATIBILITY_ISSUE_CODES = (
    "adapter_not_verified",
    "asset_binding_missing",
    "capability_missing",
    "pack_hash_mismatch",
    "pack_kind_missing",
    "pack_unverified",
    "platform_unsupported",
    "profile_mismatch",
    "representation_mismatch",
    "runtime_api_incompatible",
    "semantic_slot_duplicate",
    "semantic_slot_missing",
    "world_identity_mismatch",
)

_SIMULATION_CAPABILITIES = {
    "action_replay",
    "actor_needs",
    "collision_gltf",
    "construction",
    "contextual_interactions",
    "costed_abilities",
    "delayed_consequences",
    "directed_relationships",
    "grid_movement",
    "hierarchical_goals",
    "path_navigation",
    "playable_actor_switching",
    "resource_economy",
    "schedules",
    "simulation_fixed_step",
    "versioned_persistence",
    "world_clock",
}
_NARRATIVE_CAPABILITIES = {
    "conditional_dialogue",
    "personal_campaigns",
    "reactive_quests",
    "timed_scenes",
    "typed_knowledge",
}
_CONTENT_CAPABILITIES = {
    "catalog_multi_world_v1",
    "content_assetpack_v1",
    "content_renderpack_v1",
    "content_worldpack_v1_v5",
    "locales",
}
_PACKAGING_CAPABILITIES = {"packaging_standalone"}
_PRESENTATION_CAPABILITIES = {
    "animation_gltf",
    "presentation_audio",
    "presentation_ui_2d",
    "presentation_world_2_5d",
    "presentation_world_2d",
    "presentation_world_3d",
    "presentation_world_mixed",
}


def _capability_table() -> Mapping[str, Mapping[str, str]]:
    entries: dict[str, Mapping[str, str]] = {}
    for domain, capability_ids in (
        ("content", _CONTENT_CAPABILITIES),
        ("narrative", _NARRATIVE_CAPABILITIES),
        ("packaging", _PACKAGING_CAPABILITIES),
        ("presentation", _PRESENTATION_CAPABILITIES),
        ("simulation", _SIMULATION_CAPABILITIES),
    ):
        determinism = "deterministic_presentation" if domain == "presentation" else "deterministic"
        for capability_id in capability_ids:
            entries[capability_id] = MappingProxyType(
                {"domain": domain, "determinism": determinism}
            )
    expected = set(SUPPORTED_RUNTIME_FEATURES) | {
        "animation_gltf",
        "catalog_multi_world_v1",
        "collision_gltf",
        "content_assetpack_v1",
        "content_renderpack_v1",
        "content_worldpack_v1_v5",
        "packaging_standalone",
        "presentation_audio",
        "presentation_ui_2d",
        "presentation_world_2d",
        "presentation_world_2_5d",
        "presentation_world_3d",
        "presentation_world_mixed",
        "simulation_fixed_step",
    }
    if set(entries) != expected:  # pragma: no cover - import-time source parity guard
        raise RuntimeError("runtime capability table does not match the supported runtime surface")
    return MappingProxyType(dict(sorted(entries.items())))


RUNTIME_CAPABILITIES = _capability_table()


def _profile(
    mode: str,
    layers: tuple[str, ...],
    packs: tuple[str, ...],
    capabilities: set[str],
) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "mode": mode,
            "layers": layers,
            "required_packs": packs,
            "required_capability_ids": tuple(sorted(capabilities)),
        }
    )


PRESENTATION_PROFILES: Mapping[str, Mapping[str, object]] = MappingProxyType(
    {
        "profile_2_5d": _profile(
            "2_5d",
            ("2_5d",),
            ("renderpack",),
            {
                "content_renderpack_v1",
                "content_worldpack_v1_v5",
                "presentation_world_2_5d",
            },
        ),
        "profile_2_5d_over_3d": _profile(
            "2_5d_over_3d",
            ("3d", "2_5d"),
            ("assetpack", "renderpack"),
            {
                "animation_gltf",
                "collision_gltf",
                "content_assetpack_v1",
                "content_renderpack_v1",
                "content_worldpack_v1_v5",
                "presentation_world_2_5d",
                "presentation_world_3d",
                "presentation_world_mixed",
            },
        ),
        "profile_2d": _profile(
            "2d",
            ("2d",),
            ("renderpack",),
            {
                "content_renderpack_v1",
                "content_worldpack_v1_v5",
                "presentation_world_2d",
            },
        ),
        "profile_2d_over_2_5d": _profile(
            "2d_over_2_5d",
            ("2_5d", "2d"),
            ("renderpack",),
            {
                "content_renderpack_v1",
                "content_worldpack_v1_v5",
                "presentation_world_2_5d",
                "presentation_world_2d",
                "presentation_world_mixed",
            },
        ),
        "profile_2d_over_3d": _profile(
            "2d_over_3d",
            ("3d", "2d"),
            ("assetpack", "renderpack"),
            {
                "animation_gltf",
                "collision_gltf",
                "content_assetpack_v1",
                "content_renderpack_v1",
                "content_worldpack_v1_v5",
                "presentation_world_2d",
                "presentation_world_3d",
                "presentation_world_mixed",
            },
        ),
        "profile_3d": _profile(
            "3d",
            ("3d",),
            ("assetpack",),
            {
                "animation_gltf",
                "collision_gltf",
                "content_assetpack_v1",
                "content_worldpack_v1_v5",
                "presentation_world_3d",
            },
        ),
    }
)

_ID_PATTERN = re.compile(r"^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$")
_SLOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*){1,2}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_ISSUE_PATH_PATTERN = re.compile(r"^[a-z0-9_/:-]+$")
_RUNTIME_API_KEYS = frozenset({"minimum", "maximum_exclusive"})
_COMPONENT_KEYS = frozenset({"animation", "engine", "packager", "physics", "renderer"})
_COMPONENT_VALUE_KEYS = frozenset({"id", "version"})
_BUDGET_KEYS = frozenset(
    {
        "max_assets",
        "max_bindings",
        "max_draw_calls",
        "max_loaded_bytes",
        "max_triangles",
        "target_frame_milliseconds",
    }
)
_PROFILE_REF_KEYS = frozenset({"id", "content_hash"})
_ADAPTER_REF_KEYS = frozenset({"id", "version", "content_hash"})
_PACK_REF_KEYS = frozenset({"path", "format", "format_version", "content_hash"})
_SLOT_OWNER_KEYS = frozenset({"slot", "plane", "pack", "asset_id", "representation"})
_CHECK_KEYS = frozenset({"id", "passed", "issues"})
_ISSUE_KEYS = frozenset({"code", "path"})
_PACK_FORMATS = {
    "assetpack": ("rpg-world-forge.assetpack", {1}),
    "renderpack": ("isoworld.renderpack", {1}),
    "worldpack": ("isoworld.worldpack", {1, 2, 3, 4, 5}),
}
_RUNTIME_DOCUMENT_FIELDS = (
    "capability_catalog",
    "presentation_profile",
    "runtime_adapter",
    "composition",
)
_RUNTIME_DOCUMENT_MAX_BYTES = 16 * 1024 * 1024
_PINNED_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_PINNED_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_SAFE_POSIX_DOCUMENT_IO = (
    os.name == "posix"
    and all(
        getattr(os, flag, 0) != 0
        for flag in (
            "O_DIRECTORY",
            "O_NOFOLLOW",
            "O_NONBLOCK",
        )
    )
    and all(function in os.supports_dir_fd for function in (os.open, os.stat))
    and os.stat in os.supports_follow_symlinks
)

T = TypeVar("T")


class RuntimeCompositionError(ValueError):
    """Raised when an M6 composition contract is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeCompositionDocuments:
    """Canonical immutable snapshots of one explicit four-document composition."""

    _capability_catalog_bytes: bytes
    _presentation_profile_bytes: bytes
    _runtime_adapter_bytes: bytes
    _composition_bytes: bytes

    @staticmethod
    def _decode(payload: bytes) -> dict[str, Any]:
        value = json.loads(payload.decode("utf-8"))
        assert isinstance(value, dict)
        return value

    @property
    def capability_catalog(self) -> dict[str, Any]:
        return self._decode(self._capability_catalog_bytes)

    @property
    def presentation_profile(self) -> dict[str, Any]:
        return self._decode(self._presentation_profile_bytes)

    @property
    def runtime_adapter(self) -> dict[str, Any]:
        return self._decode(self._runtime_adapter_bytes)

    @property
    def composition(self) -> dict[str, Any]:
        return self._decode(self._composition_bytes)


@dataclass(frozen=True, slots=True, order=True)
class RuntimeCompatibilityIssue:
    check_id: str
    code: str
    path: str


@dataclass(frozen=True, slots=True)
class RuntimeCompositionVerification:
    compatible: bool
    issues: tuple[RuntimeCompatibilityIssue, ...]
    _report_bytes: bytes

    @property
    def report(self) -> dict[str, Any]:
        """Return a detached report value while retaining an immutable result."""

        value = json.loads(self._report_bytes.decode("utf-8"))
        assert isinstance(value, dict)
        return value

    @property
    def content_hash(self) -> str:
        return str(self.report["content_hash"])


@dataclass(frozen=True, slots=True)
class RegisteredRuntimeComposition(Generic[T]):
    """A statically compatible composition and its exact opaque registry value."""

    documents: RuntimeCompositionDocuments
    verification: RuntimeCompositionVerification
    adapter_key: RuntimeAdapterKey
    adapter_value: T


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeCompositionError(f"{context} must be an object")
    return value


def _exact_keys(value: dict[str, Any], expected: frozenset[str], context: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise RuntimeCompositionError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise RuntimeCompositionError(f"{context} is missing fields: {', '.join(sorted(missing))}")


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeCompositionError(f"{context} must be a non-empty string")
    return value


def _identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise RuntimeCompositionError(f"{context} must be a portable lowercase ID")
    return value


def _sha256(value: object, context: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise RuntimeCompositionError(f"{context} must be a lowercase SHA-256")
    return value


def _semver(value: object, context: str) -> str:
    if not isinstance(value, str) or _SEMVER_PATTERN.fullmatch(value) is None:
        raise RuntimeCompositionError(f"{context} must be strict MAJOR.MINOR.PATCH")
    return value


def _semver_key(value: str) -> tuple[int, int, int]:
    match = _SEMVER_PATTERN.fullmatch(value)
    if match is None:  # pragma: no cover - callers validate
        raise ValueError(value)
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _portable_path(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeCompositionError(f"{context} must be a portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or not path.parts
        or any(not is_portable_path_component(part) for part in path.parts)
    ):
        raise RuntimeCompositionError(f"{context} must be a portable relative path")
    return value


def _sorted_unique_identifiers(
    value: object,
    context: str,
    *,
    allowed: set[str] | frozenset[str] | None = None,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeCompositionError(f"{context} must be an array of IDs")
    if not allow_empty and not value:
        raise RuntimeCompositionError(f"{context} must not be empty")
    for index, item in enumerate(value):
        _identifier(item, f"{context}/{index}")
    if value != sorted(set(value)):
        raise RuntimeCompositionError(f"{context} must be sorted unique IDs")
    if allowed is not None:
        unknown = set(value) - set(allowed)
        if unknown:
            raise RuntimeCompositionError(
                f"{context} contains unknown capability IDs: {', '.join(sorted(unknown))}"
            )
    return value


def _verify_contract_identity(
    value: dict[str, Any],
    *,
    format_name: str,
    context: str,
) -> None:
    format_version = value.get("format_version")
    if (
        value.get("format") != format_name
        or isinstance(format_version, bool)
        or not isinstance(format_version, int)
        or format_version != RUNTIME_COMPOSITION_CONTRACT_VERSION
    ):
        raise RuntimeCompositionError(f"{context} format or format_version is unsupported")
    _sha256(value.get("content_hash"), f"{context}.content_hash")
    if canonical_payload_hash(value) != value["content_hash"]:
        raise RuntimeCompositionError(f"{context} content hash does not match its contents")


def validate_runtime_capability_catalog(value: object) -> dict[str, Any]:
    catalog = _object(value, "runtime capability catalog")
    _exact_keys(catalog, RUNTIME_CAPABILITY_CATALOG_FIELDS, "runtime capability catalog")
    _verify_contract_identity(
        catalog,
        format_name=RUNTIME_CAPABILITY_CATALOG_FORMAT,
        context="runtime capability catalog",
    )
    capabilities = catalog.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise RuntimeCompositionError("runtime capability catalog.capabilities must be non-empty")
    actual: dict[str, dict[str, str]] = {}
    for index, raw in enumerate(capabilities):
        context = f"runtime capability catalog.capabilities/{index}"
        entry = _object(raw, context)
        _exact_keys(entry, RUNTIME_CAPABILITY_FIELDS, context)
        capability_id = _identifier(entry.get("id"), f"{context}.id")
        if capability_id in actual:
            raise RuntimeCompositionError(f"{context}.id duplicates {capability_id}")
        domain = _string(entry.get("domain"), f"{context}.domain")
        determinism = _string(entry.get("determinism"), f"{context}.determinism")
        if domain not in {"content", "narrative", "packaging", "presentation", "simulation"}:
            raise RuntimeCompositionError(f"{context}.domain is unsupported")
        if determinism not in {"deterministic", "deterministic_presentation"}:
            raise RuntimeCompositionError(f"{context}.determinism is unsupported")
        actual[capability_id] = {"domain": domain, "determinism": determinism}
    if [entry["id"] for entry in capabilities] != sorted(actual):
        raise RuntimeCompositionError(
            "runtime capability catalog.capabilities are not in canonical ID order"
        )
    expected = {key: dict(item) for key, item in RUNTIME_CAPABILITIES.items()}
    if actual != expected:
        raise RuntimeCompositionError(
            "runtime capability catalog does not match the exact static capability catalog"
        )
    return copy.deepcopy(catalog)


def validate_runtime_presentation_profile(value: object) -> dict[str, Any]:
    profile = _object(value, "runtime presentation profile")
    _exact_keys(profile, RUNTIME_PRESENTATION_PROFILE_FIELDS, "runtime presentation profile")
    _verify_contract_identity(
        profile,
        format_name=RUNTIME_PRESENTATION_PROFILE_FORMAT,
        context="runtime presentation profile",
    )
    profile_id = _identifier(profile.get("id"), "runtime presentation profile.id")
    mode = _string(profile.get("mode"), "runtime presentation profile.mode")
    layers = profile.get("layers")
    packs = profile.get("required_packs")
    if (
        not isinstance(layers, list)
        or not layers
        or any(layer not in PRESENTATION_LAYERS for layer in layers)
        or len(set(layers)) != len(layers)
    ):
        raise RuntimeCompositionError("runtime presentation profile.layers are invalid")
    if (
        not isinstance(packs, list)
        or not packs
        or any(pack not in {"assetpack", "renderpack"} for pack in packs)
        or packs != sorted(set(packs))
    ):
        raise RuntimeCompositionError(
            "runtime presentation profile.required_packs must be sorted unique pack kinds"
        )
    capabilities = _sorted_unique_identifiers(
        profile.get("required_capability_ids"),
        "runtime presentation profile.required_capability_ids",
        allowed=set(RUNTIME_CAPABILITIES),
        allow_empty=False,
    )
    expected = PRESENTATION_PROFILES.get(profile_id)
    actual = {
        "mode": mode,
        "layers": tuple(layers),
        "required_packs": tuple(packs),
        "required_capability_ids": tuple(capabilities),
    }
    if expected is None or actual != dict(expected):
        raise RuntimeCompositionError(
            "runtime presentation profile does not match its exact static profile"
        )
    return copy.deepcopy(profile)


def validate_runtime_adapter(value: object) -> dict[str, Any]:
    adapter = _object(value, "runtime adapter")
    _exact_keys(adapter, RUNTIME_ADAPTER_FIELDS, "runtime adapter")
    _verify_contract_identity(
        adapter,
        format_name=RUNTIME_ADAPTER_FORMAT,
        context="runtime adapter",
    )
    _identifier(adapter.get("id"), "runtime adapter.id")
    _semver(adapter.get("version"), "runtime adapter.version")
    if adapter.get("state") not in {"declared", "verified"}:
        raise RuntimeCompositionError("runtime adapter.state must be declared or verified")
    runtime_api = _object(adapter.get("runtime_api"), "runtime adapter.runtime_api")
    _exact_keys(runtime_api, _RUNTIME_API_KEYS, "runtime adapter.runtime_api")
    minimum = _semver(runtime_api.get("minimum"), "runtime adapter.runtime_api.minimum")
    maximum = _semver(
        runtime_api.get("maximum_exclusive"),
        "runtime adapter.runtime_api.maximum_exclusive",
    )
    if _semver_key(minimum) >= _semver_key(maximum):
        raise RuntimeCompositionError("runtime adapter.runtime_api range is empty")
    platforms = adapter.get("platforms")
    if (
        not isinstance(platforms, list)
        or not platforms
        or any(item not in PLATFORMS for item in platforms)
        or platforms != sorted(set(platforms))
    ):
        raise RuntimeCompositionError(
            "runtime adapter.platforms must be sorted supported platforms"
        )
    modes = adapter.get("presentation_modes")
    if (
        not isinstance(modes, list)
        or not modes
        or any(item not in PRESENTATION_MODES for item in modes)
        or modes != sorted(set(modes))
    ):
        raise RuntimeCompositionError(
            "runtime adapter.presentation_modes must be sorted supported modes"
        )
    _sorted_unique_identifiers(
        adapter.get("capability_ids"),
        "runtime adapter.capability_ids",
        allowed=set(RUNTIME_CAPABILITIES),
        allow_empty=False,
    )
    components = _object(adapter.get("components"), "runtime adapter.components")
    _exact_keys(components, _COMPONENT_KEYS, "runtime adapter.components")
    for component_name in sorted(_COMPONENT_KEYS):
        component = _object(
            components.get(component_name),
            f"runtime adapter.components.{component_name}",
        )
        _exact_keys(
            component,
            _COMPONENT_VALUE_KEYS,
            f"runtime adapter.components.{component_name}",
        )
        _identifier(
            component.get("id"),
            f"runtime adapter.components.{component_name}.id",
        )
        _semver(
            component.get("version"),
            f"runtime adapter.components.{component_name}.version",
        )
    budgets = _object(adapter.get("budgets"), "runtime adapter.budgets")
    _exact_keys(budgets, _BUDGET_KEYS, "runtime adapter.budgets")
    for field in sorted(_BUDGET_KEYS - {"target_frame_milliseconds"}):
        number = budgets.get(field)
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= 2**63 - 1:
            raise RuntimeCompositionError(
                f"runtime adapter.budgets.{field} must be a positive bounded integer"
            )
    frame = budgets.get("target_frame_milliseconds")
    if (
        isinstance(frame, bool)
        or not isinstance(frame, int | float)
        or not math.isfinite(float(frame))
        or not 0 < float(frame) <= 1000
    ):
        raise RuntimeCompositionError(
            "runtime adapter.budgets.target_frame_milliseconds must be finite and positive"
        )
    return copy.deepcopy(adapter)


def _validate_pack_ref(kind: str, value: object) -> dict[str, Any]:
    context = f"runtime composition.packs.{kind}"
    reference = _object(value, context)
    _exact_keys(reference, _PACK_REF_KEYS, context)
    _portable_path(reference.get("path"), f"{context}.path")
    expected_format, versions = _PACK_FORMATS[kind]
    if reference.get("format") != expected_format:
        raise RuntimeCompositionError(f"{context}.format must be {expected_format}")
    version = reference.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in versions:
        raise RuntimeCompositionError(f"{context}.format_version is unsupported")
    _sha256(reference.get("content_hash"), f"{context}.content_hash")
    return reference


def validate_runtime_composition(value: object) -> dict[str, Any]:
    composition = _object(value, "runtime composition")
    _exact_keys(composition, RUNTIME_COMPOSITION_FIELDS, "runtime composition")
    _verify_contract_identity(
        composition,
        format_name=RUNTIME_COMPOSITION_FORMAT,
        context="runtime composition",
    )
    _identifier(composition.get("world_id"), "runtime composition.world_id")
    _sha256(composition.get("world_content_hash"), "runtime composition.world_content_hash")
    _semver(composition.get("release_id"), "runtime composition.release_id")
    profile = _object(composition.get("profile"), "runtime composition.profile")
    _exact_keys(profile, _PROFILE_REF_KEYS, "runtime composition.profile")
    profile_id = _identifier(profile.get("id"), "runtime composition.profile.id")
    if profile_id not in PRESENTATION_PROFILES:
        raise RuntimeCompositionError("runtime composition.profile.id is unknown")
    _sha256(profile.get("content_hash"), "runtime composition.profile.content_hash")
    _sha256(
        composition.get("capability_catalog_hash"),
        "runtime composition.capability_catalog_hash",
    )
    adapter = _object(composition.get("adapter"), "runtime composition.adapter")
    _exact_keys(adapter, _ADAPTER_REF_KEYS, "runtime composition.adapter")
    _identifier(adapter.get("id"), "runtime composition.adapter.id")
    _semver(adapter.get("version"), "runtime composition.adapter.version")
    _sha256(adapter.get("content_hash"), "runtime composition.adapter.content_hash")
    packs = _object(composition.get("packs"), "runtime composition.packs")
    unknown_packs = set(packs) - set(PACK_KINDS)
    if unknown_packs:
        raise RuntimeCompositionError(
            f"runtime composition.packs contains unknown fields: {', '.join(sorted(unknown_packs))}"
        )
    if "worldpack" not in packs:
        raise RuntimeCompositionError("runtime composition.packs is missing fields: worldpack")
    pack_paths: set[str] = set()
    for kind in sorted(packs):
        reference = _validate_pack_ref(kind, packs[kind])
        path = reference["path"]
        if path in pack_paths:
            raise RuntimeCompositionError("runtime composition pack paths must be unique")
        pack_paths.add(path)
    _sorted_unique_identifiers(
        composition.get("required_capability_ids"),
        "runtime composition.required_capability_ids",
        allowed=set(RUNTIME_CAPABILITIES),
        allow_empty=False,
    )
    owners = composition.get("slot_owners")
    if not isinstance(owners, list) or not owners:
        raise RuntimeCompositionError("runtime composition.slot_owners must be non-empty")
    slots: set[str] = set()
    owner_order: list[tuple[str, str, str, str, str]] = []
    for index, raw in enumerate(owners):
        context = f"runtime composition.slot_owners/{index}"
        owner = _object(raw, context)
        _exact_keys(owner, _SLOT_OWNER_KEYS, context)
        slot = owner.get("slot")
        if not isinstance(slot, str) or _SLOT_PATTERN.fullmatch(slot) is None:
            raise RuntimeCompositionError(f"{context}.slot is invalid")
        if slot in slots:
            raise RuntimeCompositionError(f"{context}.slot is a duplicate semantic slot")
        slots.add(slot)
        plane = owner.get("plane")
        pack = owner.get("pack")
        representation = owner.get("representation")
        if plane not in SLOT_PLANES:
            raise RuntimeCompositionError(f"{context}.plane is unsupported")
        if pack not in {"assetpack", "renderpack"}:
            raise RuntimeCompositionError(f"{context}.pack is unsupported")
        if pack not in packs:
            raise RuntimeCompositionError(f"{context}.pack has no composition pack reference")
        _identifier(owner.get("asset_id"), f"{context}.asset_id")
        if representation not in SLOT_REPRESENTATIONS:
            raise RuntimeCompositionError(f"{context}.representation is unsupported")
        if representation == "3d" and pack != "assetpack":
            raise RuntimeCompositionError(f"{context} 3d ownership requires assetpack")
        if representation != "3d" and pack != "renderpack":
            raise RuntimeCompositionError(f"{context} non-3d ownership requires renderpack")
        if plane == "audio" and representation != "audio":
            raise RuntimeCompositionError(f"{context} audio plane requires audio representation")
        if plane == "ui_overlay" and representation != "2d":
            raise RuntimeCompositionError(f"{context} UI plane requires 2d representation")
        if plane.startswith("world_") and representation == "audio":
            raise RuntimeCompositionError(f"{context} world planes cannot own audio")
        owner_order.append((slot, plane, pack, owner["asset_id"], representation))
    if owner_order != sorted(owner_order):
        raise RuntimeCompositionError(
            "runtime composition.slot_owners are not in canonical sorted order"
        )
    return copy.deepcopy(composition)


def validate_runtime_compatibility_report(value: object) -> dict[str, Any]:
    report = _object(value, "runtime compatibility report")
    _exact_keys(
        report,
        RUNTIME_COMPATIBILITY_REPORT_FIELDS,
        "runtime compatibility report",
    )
    _verify_contract_identity(
        report,
        format_name=RUNTIME_COMPATIBILITY_REPORT_FORMAT,
        context="runtime compatibility report",
    )
    for field in (
        "composition_hash",
        "world_content_hash",
        "profile_hash",
        "capability_catalog_hash",
        "adapter_hash",
    ):
        _sha256(report.get(field), f"runtime compatibility report.{field}")
    pack_hashes = _object(
        report.get("pack_hashes"),
        "runtime compatibility report.pack_hashes",
    )
    if set(pack_hashes) - set(PACK_KINDS) or "worldpack" not in pack_hashes:
        raise RuntimeCompositionError(
            "runtime compatibility report.pack_hashes must contain only known packs and worldpack"
        )
    for kind, digest in pack_hashes.items():
        _sha256(digest, f"runtime compatibility report.pack_hashes.{kind}")
    if report.get("platform") not in PLATFORMS:
        raise RuntimeCompositionError("runtime compatibility report.platform is unsupported")
    checks = report.get("checks")
    if not isinstance(checks, list):
        raise RuntimeCompositionError("runtime compatibility report.checks must be an array")
    if [check.get("id") if isinstance(check, dict) else None for check in checks] != list(
        COMPATIBILITY_CHECK_IDS
    ):
        raise RuntimeCompositionError(
            "runtime compatibility report.checks must contain every static check in order"
        )
    for check_index, raw in enumerate(checks):
        context = f"runtime compatibility report.checks/{check_index}"
        check = _object(raw, context)
        _exact_keys(check, _CHECK_KEYS, context)
        if not isinstance(check.get("passed"), bool):
            raise RuntimeCompositionError(f"{context}.passed must be boolean")
        issues = check.get("issues")
        if not isinstance(issues, list):
            raise RuntimeCompositionError(f"{context}.issues must be an array")
        issue_order: list[tuple[str, str]] = []
        for issue_index, raw_issue in enumerate(issues):
            issue_context = f"{context}.issues/{issue_index}"
            issue = _object(raw_issue, issue_context)
            _exact_keys(issue, _ISSUE_KEYS, issue_context)
            if issue.get("code") not in COMPATIBILITY_ISSUE_CODES:
                raise RuntimeCompositionError(f"{issue_context}.code is unsupported")
            path = issue.get("path")
            if not isinstance(path, str) or _ISSUE_PATH_PATTERN.fullmatch(path) is None:
                raise RuntimeCompositionError(f"{issue_context}.path is invalid")
            issue_order.append((issue["code"], path))
        if issue_order != sorted(set(issue_order)):
            raise RuntimeCompositionError(f"{context}.issues must be sorted unique")
        if check["passed"] != (not issues):
            raise RuntimeCompositionError(f"{context}.passed does not match its issues")
    compatible = report.get("compatible")
    if not isinstance(compatible, bool) or compatible != all(check["passed"] for check in checks):
        raise RuntimeCompositionError(
            "runtime compatibility report.compatible must equal the conjunction of checks"
        )
    return copy.deepcopy(report)


def _append_issue(
    issues: dict[str, list[RuntimeCompatibilityIssue]],
    check_id: str,
    code: str,
    path: str,
) -> None:
    issues[check_id].append(RuntimeCompatibilityIssue(check_id, code, path))


def _path_for_pack(root: Path, reference: dict[str, Any], kind: str) -> Path | None:
    try:
        return resolve_artifact(root, reference["path"])
    except AssetContractError:
        return None


def _runtime_api_compatible(
    version: str,
    *,
    minimum: str,
    maximum_exclusive: str,
) -> bool:
    value = _semver_key(version)
    return _semver_key(minimum) <= value < _semver_key(maximum_exclusive)


def verify_runtime_composition(
    capability_catalog: object,
    presentation_profile: object,
    runtime_adapter: object,
    composition_document: object,
    *,
    root: str | Path,
    platform: str,
    runtime_api_version: str = RUNTIME_API_VERSION,
) -> RuntimeCompositionVerification:
    """Verify one hash-bound composition without changing its documents or M5 packs."""

    catalog = validate_runtime_capability_catalog(capability_catalog)
    profile = validate_runtime_presentation_profile(presentation_profile)
    adapter = validate_runtime_adapter(runtime_adapter)
    composition = validate_runtime_composition(composition_document)
    if platform not in PLATFORMS:
        raise RuntimeCompositionError("platform is unsupported")
    _semver(runtime_api_version, "runtime_api_version")

    issues: dict[str, list[RuntimeCompatibilityIssue]] = {
        check_id: [] for check_id in COMPATIBILITY_CHECK_IDS
    }
    if adapter["state"] != "verified":
        _append_issue(
            issues,
            "adapter_state",
            "adapter_not_verified",
            "adapter/state",
        )
    adapter_ref = composition["adapter"]
    if (
        adapter_ref["id"] != adapter["id"]
        or adapter_ref["version"] != adapter["version"]
        or adapter_ref["content_hash"] != adapter["content_hash"]
    ):
        _append_issue(
            issues,
            "adapter_state",
            "adapter_not_verified",
            "composition/adapter",
        )
    if composition["capability_catalog_hash"] != catalog["content_hash"]:
        _append_issue(
            issues,
            "capability_coverage",
            "capability_missing",
            "composition/capability_catalog_hash",
        )
    if (
        composition["profile"]["id"] != profile["id"]
        or composition["profile"]["content_hash"] != profile["content_hash"]
    ):
        _append_issue(
            issues,
            "pack_profile",
            "profile_mismatch",
            "composition/profile",
        )
    if profile["mode"] not in adapter["presentation_modes"]:
        _append_issue(
            issues,
            "pack_profile",
            "profile_mismatch",
            "adapter/presentation_modes",
        )
    if platform not in adapter["platforms"]:
        _append_issue(
            issues,
            "platform_support",
            "platform_unsupported",
            "adapter/platforms",
        )

    packs = composition["packs"]
    for required_pack in profile["required_packs"]:
        if required_pack not in packs:
            _append_issue(
                issues,
                "pack_profile",
                "pack_kind_missing",
                f"composition/packs/{required_pack}",
            )

    root_path = Path(root)
    worldpack = None
    worldpack_path = _path_for_pack(root_path, packs["worldpack"], "worldpack")
    if worldpack_path is None:
        _append_issue(
            issues,
            "m5_pack_integrity",
            "pack_unverified",
            "composition/packs/worldpack",
        )
    else:
        try:
            worldpack = load_worldpack(worldpack_path)
        except (OSError, WorldPackError):
            _append_issue(
                issues,
                "m5_pack_integrity",
                "pack_unverified",
                "composition/packs/worldpack",
            )
        else:
            if (
                worldpack.content_hash != packs["worldpack"]["content_hash"]
                or worldpack.format_version != packs["worldpack"]["format_version"]
            ):
                _append_issue(
                    issues,
                    "m5_pack_integrity",
                    "pack_hash_mismatch",
                    "composition/packs/worldpack",
                )
            if (
                worldpack.world_id != composition["world_id"]
                or worldpack.content_hash != composition["world_content_hash"]
            ):
                _append_issue(
                    issues,
                    "world_identity",
                    "world_identity_mismatch",
                    "composition/world",
                )

    render_bindings: dict[str, tuple[str, str | None]] | None = None
    if "renderpack" in packs:
        renderpack_path = _path_for_pack(root_path, packs["renderpack"], "renderpack")
        if renderpack_path is None or worldpack is None:
            _append_issue(
                issues,
                "m5_pack_integrity",
                "pack_unverified",
                "composition/packs/renderpack",
            )
        else:
            try:
                with load_renderpack(renderpack_path, worldpack) as loaded:
                    asset_kinds = {asset.id: asset.kind for asset in loaded.assets}
                    render_bindings = {
                        binding.slot: (binding.asset_id, asset_kinds.get(binding.asset_id))
                        for binding in loaded.bindings
                    }
                    loaded_hash = loaded.content_hash
                    loaded_world_id = loaded.world_id
                    loaded_world_hash = loaded.world_content_hash
            except (OSError, RenderPackError):
                _append_issue(
                    issues,
                    "m5_pack_integrity",
                    "pack_unverified",
                    "composition/packs/renderpack",
                )
            else:
                if loaded_hash != packs["renderpack"]["content_hash"]:
                    _append_issue(
                        issues,
                        "m5_pack_integrity",
                        "pack_hash_mismatch",
                        "composition/packs/renderpack",
                    )
                if (
                    loaded_world_id != composition["world_id"]
                    or loaded_world_hash != composition["world_content_hash"]
                ):
                    _append_issue(
                        issues,
                        "world_identity",
                        "world_identity_mismatch",
                        "composition/packs/renderpack",
                    )

    asset_bindings: dict[str, tuple[str, str]] | None = None
    if "assetpack" in packs:
        assetpack_path = _path_for_pack(root_path, packs["assetpack"], "assetpack")
        if assetpack_path is None or worldpack_path is None:
            _append_issue(
                issues,
                "m5_pack_integrity",
                "pack_unverified",
                "composition/packs/assetpack",
            )
        else:
            try:
                loaded_assetpack = verify_assetpack(assetpack_path, worldpack_path)
            except (OSError, AssetPackError):
                _append_issue(
                    issues,
                    "m5_pack_integrity",
                    "pack_unverified",
                    "composition/packs/assetpack",
                )
            else:
                asset_representations = {
                    asset["id"]: asset["representation"] for asset in loaded_assetpack["assets"]
                }
                asset_bindings = {
                    binding["slot"]: (
                        binding["asset_id"],
                        binding["representation"],
                    )
                    for binding in loaded_assetpack["bindings"]
                }
                if loaded_assetpack["content_hash"] != packs["assetpack"]["content_hash"]:
                    _append_issue(
                        issues,
                        "m5_pack_integrity",
                        "pack_hash_mismatch",
                        "composition/packs/assetpack",
                    )
                if (
                    loaded_assetpack["world_id"] != composition["world_id"]
                    or loaded_assetpack["world_content_hash"] != composition["world_content_hash"]
                ):
                    _append_issue(
                        issues,
                        "world_identity",
                        "world_identity_mismatch",
                        "composition/packs/assetpack",
                    )
                for slot, (asset_id, representation) in asset_bindings.items():
                    if asset_representations.get(asset_id) != representation:
                        _append_issue(
                            issues,
                            "semantic_slot_ownership",
                            "representation_mismatch",
                            f"assetpack/bindings/{slot}",
                        )

    expected_planes = {"world_base": profile["layers"][0]}
    if len(profile["layers"]) == 2:
        expected_planes["world_overlay"] = profile["layers"][1]
    seen_expected_planes: set[str] = set()
    for owner in composition["slot_owners"]:
        plane = owner["plane"]
        representation = owner["representation"]
        slot = owner["slot"]
        if plane in expected_planes:
            if representation != expected_planes[plane]:
                _append_issue(
                    issues,
                    "semantic_slot_ownership",
                    "profile_mismatch",
                    f"composition/slot_owners/{slot}",
                )
            else:
                seen_expected_planes.add(plane)
        elif plane == "world_overlay":
            _append_issue(
                issues,
                "semantic_slot_ownership",
                "profile_mismatch",
                f"composition/slot_owners/{slot}",
            )
        if (
            plane == "ui_overlay"
            and "presentation_ui_2d" not in composition["required_capability_ids"]
        ):
            _append_issue(
                issues,
                "capability_coverage",
                "capability_missing",
                "composition/required_capability_ids/presentation_ui_2d",
            )
        if plane == "audio" and "presentation_audio" not in composition["required_capability_ids"]:
            _append_issue(
                issues,
                "capability_coverage",
                "capability_missing",
                "composition/required_capability_ids/presentation_audio",
            )
        if owner["pack"] == "renderpack" and render_bindings is not None:
            binding = render_bindings.get(slot)
            if binding is None or binding[0] != owner["asset_id"]:
                _append_issue(
                    issues,
                    "semantic_slot_ownership",
                    "asset_binding_missing",
                    f"renderpack/bindings/{slot}",
                )
            elif (binding[1] in {"music", "sfx"}) != (representation == "audio"):
                _append_issue(
                    issues,
                    "semantic_slot_ownership",
                    "representation_mismatch",
                    f"renderpack/bindings/{slot}",
                )
        if owner["pack"] == "assetpack" and asset_bindings is not None:
            binding = asset_bindings.get(slot)
            if binding is None or binding[0] != owner["asset_id"]:
                _append_issue(
                    issues,
                    "semantic_slot_ownership",
                    "asset_binding_missing",
                    f"assetpack/bindings/{slot}",
                )
            elif binding[1] != representation:
                _append_issue(
                    issues,
                    "semantic_slot_ownership",
                    "representation_mismatch",
                    f"assetpack/bindings/{slot}",
                )
    for plane in sorted(set(expected_planes) - seen_expected_planes):
        _append_issue(
            issues,
            "semantic_slot_ownership",
            "semantic_slot_missing",
            f"composition/slot_owners/{plane}",
        )

    required_capabilities = set(profile["required_capability_ids"])
    if worldpack is not None:
        required_capabilities.update(worldpack.runtime_requirements.required_features)
        runtime_range = worldpack.runtime_requirements.runtime_api
        if not _runtime_api_compatible(
            runtime_api_version,
            minimum=runtime_range.minimum,
            maximum_exclusive=runtime_range.maximum_exclusive,
        ):
            _append_issue(
                issues,
                "runtime_api",
                "runtime_api_incompatible",
                "worldpack/runtime_requirements/runtime_api",
            )
    missing_declared = required_capabilities - set(composition["required_capability_ids"])
    for capability_id in sorted(missing_declared):
        _append_issue(
            issues,
            "capability_coverage",
            "capability_missing",
            f"composition/required_capability_ids/{capability_id}",
        )
    missing_adapter = set(composition["required_capability_ids"]) - set(adapter["capability_ids"])
    for capability_id in sorted(missing_adapter):
        _append_issue(
            issues,
            "capability_coverage",
            "capability_missing",
            f"adapter/capability_ids/{capability_id}",
        )
    if not _runtime_api_compatible(
        runtime_api_version,
        minimum=adapter["runtime_api"]["minimum"],
        maximum_exclusive=adapter["runtime_api"]["maximum_exclusive"],
    ):
        _append_issue(
            issues,
            "runtime_api",
            "runtime_api_incompatible",
            "adapter/runtime_api",
        )

    ordered_issues: list[RuntimeCompatibilityIssue] = []
    checks: list[dict[str, Any]] = []
    for check_id in COMPATIBILITY_CHECK_IDS:
        unique = sorted(set(issues[check_id]))
        ordered_issues.extend(unique)
        checks.append(
            {
                "id": check_id,
                "passed": not unique,
                "issues": [{"code": issue.code, "path": issue.path} for issue in unique],
            }
        )
    report: dict[str, Any] = {
        "format": RUNTIME_COMPATIBILITY_REPORT_FORMAT,
        "format_version": RUNTIME_COMPOSITION_CONTRACT_VERSION,
        "composition_hash": composition["content_hash"],
        "world_content_hash": composition["world_content_hash"],
        "profile_hash": profile["content_hash"],
        "capability_catalog_hash": catalog["content_hash"],
        "adapter_hash": adapter["content_hash"],
        "pack_hashes": {
            kind: reference["content_hash"] for kind, reference in sorted(packs.items())
        },
        "platform": platform,
        "checks": checks,
        "compatible": all(check["passed"] for check in checks),
    }
    report["content_hash"] = canonical_payload_hash(report)
    validate_runtime_compatibility_report(report)
    return RuntimeCompositionVerification(
        compatible=report["compatible"],
        issues=tuple(ordered_issues),
        _report_bytes=canonical_json_bytes(report),
    )


def _explicit_document_paths(
    *,
    capability_catalog_path: str,
    presentation_profile_path: str,
    runtime_adapter_path: str,
    composition_path: str,
) -> dict[str, PurePosixPath]:
    raw_paths = {
        "capability_catalog": capability_catalog_path,
        "presentation_profile": presentation_profile_path,
        "runtime_adapter": runtime_adapter_path,
        "composition": composition_path,
    }
    normalized: dict[str, PurePosixPath] = {}
    collision_keys: dict[tuple[str, ...], str] = {}
    for name in _RUNTIME_DOCUMENT_FIELDS:
        relative = portable_relative_path(raw_paths[name])
        if relative is None:
            raise RuntimeCompositionError(
                f"{name}_path must be a portable relative path beneath the explicit root"
            )
        key = portable_path_key(relative)
        prior = collision_keys.get(key)
        if prior is not None:
            raise RuntimeCompositionError(
                f"{name}_path has an NFC/casefold collision with {prior}_path"
            )
        normalized[name] = relative
        collision_keys[key] = name
    return normalized


def _explicit_document_root(root: str | Path) -> tuple[Path, tuple[int, int]]:
    root_path = Path(os.path.abspath(Path(root)))
    issues = validate_lexical_directory_root(root_path)
    if issues:
        raise RuntimeCompositionError(f"runtime composition document root is unsafe: {issues[0]}")
    try:
        identity = file_identity(path_file_stat(root_path))
    except OSError as exc:  # pragma: no cover - lexical validation reports normal failures
        raise RuntimeCompositionError("runtime composition document root is unavailable") from exc
    return root_path, identity


def _require_document_root_identity(root: Path, expected: tuple[int, int]) -> None:
    issues = validate_lexical_directory_root(root)
    if issues:
        raise RuntimeCompositionError(f"runtime composition document root changed: {issues[0]}")
    try:
        current = file_identity(path_file_stat(root))
    except OSError as exc:
        raise RuntimeCompositionError("runtime composition document root changed") from exc
    if current != expected:
        raise RuntimeCompositionError("runtime composition document root identity changed")


def _platform_name() -> str:
    return os.name


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _same_file_state(left: FileStat, right: FileStat) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_nlink,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
        getattr(left, "st_file_attributes", 0),
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_nlink,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
        getattr(right, "st_file_attributes", 0),
    )


def _require_plain_directory(info: FileStat, *, context: str) -> None:
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeCompositionError(f"{context} is not a plain directory")


def _document_decode_error(context: str, error: BaseException) -> RuntimeCompositionError:
    detail = str(error)
    folded = detail.casefold()
    if "duplicate json object key" in folded:
        code = "JSON_DUPLICATE_KEY"
    elif "non-finite json number" in folded:
        code = (
            "JSON_NONFINITE"
            if any(token in folded for token in ("nan", "infinity"))
            else "JSON_NUMBER_OVERFLOW"
        )
    elif "utf-8" in folded or "decode" in folded:
        code = "JSON_NOT_UTF8"
    elif "must contain a json object" in folded:
        code = "JSON_NOT_OBJECT"
    else:
        code = "JSON_INVALID"
    return RuntimeCompositionError(f"could not load {context}: {code}")


def _read_pinned_document_descriptor(
    descriptor: int,
    before_path: FileStat,
    *,
    context: str,
    after_path: Callable[[], FileStat],
) -> dict[str, Any]:
    active_descriptor: int | None = descriptor
    try:
        opened = descriptor_file_stat(descriptor)
        if (
            _is_link_or_reparse(opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size < 0
            or opened.st_size > _RUNTIME_DOCUMENT_MAX_BYTES
            or not _same_file_state(before_path, opened)
        ):
            raise RuntimeCompositionError(f"could not load {context}: JSON_CHANGED")
        with os.fdopen(descriptor, "rb") as stream:
            active_descriptor = None
            payload = stream.read(_RUNTIME_DOCUMENT_MAX_BYTES + 1)
            stream.seek(0)
            repeated = stream.read(_RUNTIME_DOCUMENT_MAX_BYTES + 1)
            after_descriptor = descriptor_file_stat(stream.fileno())
        if len(payload) > _RUNTIME_DOCUMENT_MAX_BYTES:
            raise RuntimeCompositionError(f"could not load {context}: JSON_TOO_LARGE")
        if payload != repeated or not _same_file_state(opened, after_descriptor):
            raise RuntimeCompositionError(f"could not load {context}: JSON_CHANGED")
        try:
            visible_after = after_path()
        except OSError as exc:
            raise RuntimeCompositionError(f"could not load {context}: JSON_CHANGED") from exc
        if _is_link_or_reparse(visible_after) or not _same_file_state(opened, visible_after):
            raise RuntimeCompositionError(f"could not load {context}: JSON_CHANGED")
        try:
            return decode_json_object(payload, source=context)
        except (RuntimeIOError, RecursionError) as exc:
            raise _document_decode_error(context, exc) from exc
    finally:
        if active_descriptor is not None:
            os.close(active_descriptor)


def _require_standalone_document(info: FileStat, *, context: str) -> None:
    if _is_link_or_reparse(info):
        raise RuntimeCompositionError(f"could not load {context}: JSON_SYMLINK")
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeCompositionError(f"could not load {context}: JSON_NOT_REGULAR")
    if info.st_nlink != 1:
        raise RuntimeCompositionError(f"could not load {context}: JSON_HARDLINK")
    if info.st_size < 0 or info.st_size > _RUNTIME_DOCUMENT_MAX_BYTES:
        raise RuntimeCompositionError(f"could not load {context}: JSON_TOO_LARGE")


def _verify_visible_directory_chain(
    chain: list[tuple[Path, tuple[int, int]]],
    *,
    context: str,
) -> None:
    for index, (path, expected) in enumerate(chain):
        changed = "root identity changed" if index == 0 else "parent identity changed"
        try:
            info = path_file_stat(path)
        except OSError as exc:
            raise RuntimeCompositionError(f"{context} {changed}") from exc
        _require_plain_directory(
            info,
            context=f"{context} {'root' if index == 0 else 'parent'}",
        )
        if file_identity(info) != expected:
            raise RuntimeCompositionError(f"{context} {changed}")


def _close_posix_directory_descriptors(descriptors: list[int]) -> None:
    active_error = sys.exception()
    first_error: OSError | None = None
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError as exc:
            if first_error is None:
                first_error = exc
    if first_error is None:
        return
    message = f"could not close pinned runtime document directories: {first_error}"
    if active_error is not None:
        active_error.add_note(message)
        return
    raise RuntimeCompositionError(message) from first_error


def _posix_entry_stat(parent_descriptor: int, name: str) -> FileStat:
    return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)


def _load_explicit_document_posix(
    root: Path,
    relative: PurePosixPath,
    *,
    context: str,
    root_identity: tuple[int, int],
) -> dict[str, Any]:
    if not _SAFE_POSIX_DOCUMENT_IO:
        raise RuntimeCompositionError("secure POSIX runtime document I/O is unavailable")
    descriptors: list[int] = []
    chain: list[tuple[Path, tuple[int, int]]] = []
    try:
        try:
            root_descriptor = os.open(root, _PINNED_DIRECTORY_FLAGS)
        except OSError as exc:
            raise RuntimeCompositionError(f"{context} root could not be pinned") from exc
        descriptors.append(root_descriptor)
        opened_root = descriptor_file_stat(root_descriptor)
        _require_plain_directory(opened_root, context=f"{context} root")
        if file_identity(opened_root) != root_identity:
            raise RuntimeCompositionError(f"{context} root identity changed")
        chain.append((root, root_identity))

        current_descriptor = root_descriptor
        current_path = root
        for component in relative.parts[:-1]:
            try:
                before = _posix_entry_stat(current_descriptor, component)
            except FileNotFoundError:
                raise RuntimeCompositionError(
                    f"{context} parent is unsafe or unavailable: JSON_MISSING"
                ) from None
            except OSError as exc:
                raise RuntimeCompositionError(f"{context} parent is unsafe or unavailable") from exc
            _require_plain_directory(before, context=f"{context} parent")
            try:
                child_descriptor = os.open(
                    component,
                    _PINNED_DIRECTORY_FLAGS,
                    dir_fd=current_descriptor,
                )
            except OSError as exc:
                raise RuntimeCompositionError(f"{context} parent could not be pinned") from exc
            descriptors.append(child_descriptor)
            opened = descriptor_file_stat(child_descriptor)
            try:
                visible_after = _posix_entry_stat(current_descriptor, component)
            except OSError as exc:
                raise RuntimeCompositionError(f"{context} parent identity changed") from exc
            _require_plain_directory(opened, context=f"{context} parent")
            if not _same_file_state(before, opened) or not _same_file_state(opened, visible_after):
                raise RuntimeCompositionError(f"{context} parent identity changed")
            current_descriptor = child_descriptor
            current_path /= component
            chain.append((current_path, file_identity(opened)))

        name = relative.name
        try:
            before_file = _posix_entry_stat(current_descriptor, name)
        except FileNotFoundError:
            raise RuntimeCompositionError(f"could not load {context}: JSON_MISSING") from None
        except OSError as exc:
            raise RuntimeCompositionError(f"could not load {context}: JSON_IO_ERROR") from exc
        _require_standalone_document(before_file, context=context)
        try:
            file_descriptor = os.open(
                name,
                _PINNED_FILE_FLAGS,
                dir_fd=current_descriptor,
            )
        except OSError as exc:
            raise RuntimeCompositionError(f"could not load {context}: JSON_IO_ERROR") from exc
        value = _read_pinned_document_descriptor(
            file_descriptor,
            before_file,
            context=context,
            after_path=lambda: _posix_entry_stat(current_descriptor, name),
        )
        _verify_visible_directory_chain(chain, context=context)
        return value
    finally:
        _close_posix_directory_descriptors(descriptors)


def _windows_open_directory_handle(path: Path) -> int:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise OSError("Windows directory handle APIs are unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x00000080,  # FILE_READ_ATTRIBUTES
        0x00000001 | 0x00000002,  # share reads/writes, never deletion
        None,
        3,  # OPEN_EXISTING
        0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in {None, invalid}:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error), str(path))
    return int(handle)


def _windows_close_directory_handle(handle: int) -> None:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise OSError("Windows directory handle APIs are unavailable")
    kernel32 = win_dll("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    if not close_handle(ctypes.c_void_p(handle)):
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error))


def _close_windows_directory_handles(handles: list[int]) -> None:
    active_error = sys.exception()
    first_error: OSError | None = None
    for handle in reversed(handles):
        try:
            _windows_close_directory_handle(handle)
        except OSError as exc:
            if first_error is None:
                first_error = exc
    if first_error is None:
        return
    message = f"could not close pinned Windows runtime document directories: {first_error}"
    if active_error is not None:
        active_error.add_note(message)
        return
    raise RuntimeCompositionError(message) from first_error


def _pin_windows_directory(
    path: Path,
    *,
    context: str,
    expected_identity: tuple[int, int] | None = None,
) -> tuple[int, FileStat]:
    try:
        before = path_file_stat(path)
    except OSError as exc:
        raise RuntimeCompositionError(f"{context} is unavailable") from exc
    _require_plain_directory(before, context=context)
    if expected_identity is not None and file_identity(before) != expected_identity:
        raise RuntimeCompositionError(f"{context} identity changed")
    try:
        handle = _windows_open_directory_handle(path)
    except OSError as exc:
        raise RuntimeCompositionError(f"{context} could not be pinned") from exc
    try:
        after = path_file_stat(path)
        _require_plain_directory(after, context=context)
        if not _same_file_state(before, after):
            raise RuntimeCompositionError(f"{context} identity changed")
        return handle, after
    except BaseException as validation_error:
        try:
            _windows_close_directory_handle(handle)
        except OSError as close_error:
            validation_error.add_note(f"could not close rejected Windows directory: {close_error}")
        raise


def _load_explicit_document_windows(
    root: Path,
    relative: PurePosixPath,
    *,
    context: str,
    root_identity: tuple[int, int],
) -> dict[str, Any]:
    handles: list[int] = []
    chain: list[tuple[Path, tuple[int, int]]] = []
    try:
        root_handle, root_info = _pin_windows_directory(
            root,
            context=f"{context} root",
            expected_identity=root_identity,
        )
        handles.append(root_handle)
        chain.append((root, file_identity(root_info)))
        current_path = root
        for component in relative.parts[:-1]:
            current_path /= component
            handle, info = _pin_windows_directory(
                current_path,
                context=f"{context} parent",
            )
            handles.append(handle)
            chain.append((current_path, file_identity(info)))

        path = current_path / relative.name
        try:
            before_file = path_file_stat(path)
        except FileNotFoundError:
            raise RuntimeCompositionError(f"could not load {context}: JSON_MISSING") from None
        except OSError as exc:
            raise RuntimeCompositionError(f"could not load {context}: JSON_IO_ERROR") from exc
        _require_standalone_document(before_file, context=context)
        try:
            file_descriptor = os.open(path, _PINNED_FILE_FLAGS)
        except OSError as exc:
            raise RuntimeCompositionError(f"could not load {context}: JSON_IO_ERROR") from exc
        value = _read_pinned_document_descriptor(
            file_descriptor,
            before_file,
            context=context,
            after_path=lambda: path_file_stat(path),
        )
        _verify_visible_directory_chain(chain, context=context)
        return value
    finally:
        _close_windows_directory_handles(handles)


def _load_explicit_document(
    root: Path,
    relative: PurePosixPath,
    *,
    context: str,
    root_identity: tuple[int, int],
) -> dict[str, Any]:
    _require_document_root_identity(root, root_identity)
    platform = _platform_name()
    if platform == "posix":
        value = _load_explicit_document_posix(
            root,
            relative,
            context=context,
            root_identity=root_identity,
        )
    elif platform == "nt":
        value = _load_explicit_document_windows(
            root,
            relative,
            context=context,
            root_identity=root_identity,
        )
    else:
        raise RuntimeCompositionError("secure runtime document I/O is unsupported")
    _require_document_root_identity(root, root_identity)
    return value


def _load_runtime_composition_document_set(
    root: Path,
    root_identity: tuple[int, int],
    paths: Mapping[str, PurePosixPath],
) -> RuntimeCompositionDocuments:
    values = {
        name: _load_explicit_document(
            root,
            paths[name],
            context=name.replace("_", " "),
            root_identity=root_identity,
        )
        for name in _RUNTIME_DOCUMENT_FIELDS
    }
    catalog = validate_runtime_capability_catalog(values["capability_catalog"])
    profile = validate_runtime_presentation_profile(values["presentation_profile"])
    adapter = validate_runtime_adapter(values["runtime_adapter"])
    composition = validate_runtime_composition(values["composition"])
    _require_document_root_identity(root, root_identity)
    return RuntimeCompositionDocuments(
        _capability_catalog_bytes=canonical_json_bytes(catalog),
        _presentation_profile_bytes=canonical_json_bytes(profile),
        _runtime_adapter_bytes=canonical_json_bytes(adapter),
        _composition_bytes=canonical_json_bytes(composition),
    )


def load_runtime_composition_documents(
    root: str | Path,
    *,
    capability_catalog_path: str,
    presentation_profile_path: str,
    runtime_adapter_path: str,
    composition_path: str,
) -> RuntimeCompositionDocuments:
    """Load four explicit strict JSON documents into detached canonical snapshots."""

    root_path, root_identity = _explicit_document_root(root)
    paths = _explicit_document_paths(
        capability_catalog_path=capability_catalog_path,
        presentation_profile_path=presentation_profile_path,
        runtime_adapter_path=runtime_adapter_path,
        composition_path=composition_path,
    )
    return _load_runtime_composition_document_set(root_path, root_identity, paths)


def _verify_runtime_composition_documents(
    documents: RuntimeCompositionDocuments,
    *,
    root: str | Path,
    platform: str,
    runtime_api_version: str,
) -> RuntimeCompositionVerification:
    return verify_runtime_composition(
        documents.capability_catalog,
        documents.presentation_profile,
        documents.runtime_adapter,
        documents.composition,
        root=root,
        platform=platform,
        runtime_api_version=runtime_api_version,
    )


def verify_runtime_composition_files(
    root: str | Path,
    *,
    capability_catalog_path: str,
    presentation_profile_path: str,
    runtime_adapter_path: str,
    composition_path: str,
    platform: str,
    runtime_api_version: str = RUNTIME_API_VERSION,
) -> RuntimeCompositionVerification:
    """Load explicit documents once and recompute their integral compatibility report."""

    root_path, root_identity = _explicit_document_root(root)
    paths = _explicit_document_paths(
        capability_catalog_path=capability_catalog_path,
        presentation_profile_path=presentation_profile_path,
        runtime_adapter_path=runtime_adapter_path,
        composition_path=composition_path,
    )
    documents = _load_runtime_composition_document_set(root_path, root_identity, paths)
    result = _verify_runtime_composition_documents(
        documents,
        root=root_path,
        platform=platform,
        runtime_api_version=runtime_api_version,
    )
    _require_document_root_identity(root_path, root_identity)
    return result


def load_registered_runtime_composition(
    root: str | Path,
    *,
    capability_catalog_path: str,
    presentation_profile_path: str,
    runtime_adapter_path: str,
    composition_path: str,
    platform: str,
    registry: StaticRuntimeAdapterRegistry[T],
    runtime_api_version: str = RUNTIME_API_VERSION,
) -> RegisteredRuntimeComposition[T]:
    """Require static compatibility, then resolve one exact code-owned adapter key."""

    if type(registry) is not StaticRuntimeAdapterRegistry:
        raise TypeError("registry must be a StaticRuntimeAdapterRegistry")
    root_path, root_identity = _explicit_document_root(root)
    paths = _explicit_document_paths(
        capability_catalog_path=capability_catalog_path,
        presentation_profile_path=presentation_profile_path,
        runtime_adapter_path=runtime_adapter_path,
        composition_path=composition_path,
    )
    documents = _load_runtime_composition_document_set(root_path, root_identity, paths)
    verification = _verify_runtime_composition_documents(
        documents,
        root=root_path,
        platform=platform,
        runtime_api_version=runtime_api_version,
    )
    _require_document_root_identity(root_path, root_identity)
    if not verification.compatible:
        raise RuntimeCompositionError("runtime composition is statically incompatible")
    adapter = documents.runtime_adapter
    key = RuntimeAdapterKey(
        id=adapter["id"],
        version=adapter["version"],
        content_hash=adapter["content_hash"],
    )
    try:
        value = StaticRuntimeAdapterRegistry.resolve(registry, key)
    except RuntimeAdapterRegistryError as exc:
        raise RuntimeCompositionError(
            "runtime composition has no exact code-owned adapter registration"
        ) from exc
    return RegisteredRuntimeComposition(
        documents=documents,
        verification=verification,
        adapter_key=key,
        adapter_value=value,
    )


__all__ = [
    "COMPATIBILITY_CHECK_IDS",
    "COMPATIBILITY_ISSUE_CODES",
    "PACK_KINDS",
    "PLATFORMS",
    "PRESENTATION_LAYERS",
    "PRESENTATION_MODES",
    "PRESENTATION_PROFILES",
    "RUNTIME_ADAPTER_FIELDS",
    "RUNTIME_ADAPTER_FORMAT",
    "RUNTIME_CAPABILITIES",
    "RUNTIME_CAPABILITY_CATALOG_FIELDS",
    "RUNTIME_CAPABILITY_CATALOG_FORMAT",
    "RUNTIME_CAPABILITY_FIELDS",
    "RUNTIME_COMPATIBILITY_REPORT_FIELDS",
    "RUNTIME_COMPATIBILITY_REPORT_FORMAT",
    "RUNTIME_COMPOSITION_CONTRACT_VERSION",
    "RUNTIME_COMPOSITION_FIELDS",
    "RUNTIME_COMPOSITION_FORMAT",
    "RUNTIME_PRESENTATION_PROFILE_FIELDS",
    "RUNTIME_PRESENTATION_PROFILE_FORMAT",
    "RegisteredRuntimeComposition",
    "RuntimeCompatibilityIssue",
    "RuntimeCompositionDocuments",
    "RuntimeCompositionError",
    "RuntimeCompositionVerification",
    "SLOT_PLANES",
    "SLOT_REPRESENTATIONS",
    "load_registered_runtime_composition",
    "load_runtime_composition_documents",
    "validate_runtime_adapter",
    "validate_runtime_capability_catalog",
    "validate_runtime_compatibility_report",
    "validate_runtime_composition",
    "validate_runtime_presentation_profile",
    "verify_runtime_composition",
    "verify_runtime_composition_files",
]
