"""Standalone verification for installed immutable composed releases."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform as host_platform
import re
import stat
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.assetpack import AssetPackError, load_assetpack
from isoworld.content.file_stat import is_link_or_reparse, path_file_stat
from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.models import RUNTIME_API_VERSION
from isoworld.content.portability import is_portable_path_component
from isoworld.content.renderpack import RenderPackError, load_renderpack
from isoworld.content.resource_snapshot import (
    MAX_OWNED_RESOURCE_BYTES,
    ResourceSnapshotError,
    ResourceSnapshotOwner,
    note_cleanup_failure,
)
from isoworld.render.composition_plan import (
    CompositionPlan,
    CompositionPlanError,
    PackSlotBinding,
    build_composition_plan,
    validate_composition_slot_ownership,
)

CATALOG_RELATIVE_PATH = Path("game_data/compositions.lock.json")
CATALOG_GENERATIONS_RELATIVE_PATH = Path("game_data/compositions.d")
CATALOG_GENERATION_NAME = "catalog.json"
CATALOG_GENERATION_STAGE_PREFIX = ".catalog-generation-"
MANIFEST_NAME = "composed-bundle.manifest.json"
CATALOG_FORMAT = "isoworld.composed_runtime_catalog"
CATALOG_GENERATION_FORMAT = "isoworld.composed_runtime_catalog_generation"
BUNDLE_FORMAT = "rpg-world-forge.composed_runtime_bundle"
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_FILES = 100_000
MAX_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
MAX_CATALOG_GENERATIONS = 10_000
MAX_LICENSE_FILES = 1_000
MAX_LICENSE_BYTES = 4 * 1024 * 1024
MAX_TREE_DEPTH = 64
MAX_TREE_DIRECTORIES = MAX_FILES * 4
MAX_TREE_NODES = MAX_FILES * 5
MAX_TREE_PATH_BYTES = 1024
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CATALOG_GENERATION_STAGE_PATTERN = re.compile(
    rf"^{re.escape(CATALOG_GENERATION_STAGE_PREFIX)}([0-9a-f]{{64}})-([0-9a-f]{{32}})$"
)
SLOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*){1,2}$")
CONTRACT_VERSION = 1
CAPABILITY_CATALOG_FIELDS = {"format", "format_version", "capabilities", "content_hash"}
CAPABILITY_FIELDS = {"id", "domain", "determinism"}
PROFILE_FIELDS = {
    "format",
    "format_version",
    "id",
    "mode",
    "layers",
    "required_packs",
    "required_capability_ids",
    "content_hash",
}
ADAPTER_FIELDS = {
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
COMPOSITION_FIELDS = {
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
COMPONENT_NAMES = {"animation", "engine", "packager", "physics", "renderer"}
BUDGET_FIELDS = {
    "max_assets",
    "max_bindings",
    "max_draw_calls",
    "max_loaded_bytes",
    "max_triangles",
    "target_frame_milliseconds",
}
TARGET_FRAME_MILLISECONDS_MAX = 1000
PRESENTATION_MODES = {
    "2_5d",
    "2_5d_over_3d",
    "2d",
    "2d_over_2_5d",
    "2d_over_3d",
    "3d",
}
PRESENTATION_LAYERS = {"2_5d", "2d", "3d"}
SLOT_PLANES = {"audio", "ui_overlay", "world_base", "world_overlay"}
SLOT_REPRESENTATIONS = {"2_5d", "2d", "3d", "audio"}
SUPPORTED_PLATFORMS = {"linux_x86_64", "windows_x86_64"}
PACK_FORMATS = {
    "assetpack": ("rpg-world-forge.assetpack", {1}),
    "renderpack": ("isoworld.renderpack", {1}),
    "worldpack": ("isoworld.worldpack", {1, 2, 3, 4, 5}),
}
COMPATIBILITY_REPORT_FIELDS = {
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
COMPATIBILITY_ISSUE_CODES = {
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
}
ISSUE_PATH_PATTERN = re.compile(r"^[a-z0-9_/:-]+$")
LICENSE_MEDIA_TYPES = {
    ".html": "text/html",
    ".json": "application/json",
    ".md": "text/markdown",
    ".rst": "text/plain",
    ".txt": "text/plain",
}
BUILTIN_2_5D_BUDGETS = {
    "max_assets": 5,
    "max_bindings": 3,
    "max_draw_calls": 1024,
    "max_loaded_bytes": 1_048_576,
    "max_triangles": 1,
    "target_frame_milliseconds": 1000,
}
BUILTIN_NATIVE_ADAPTER = (
    "isoworld_raylib_2_5d",
    "0.1.0",
    "2628adad118585ffc15c6509a49c92954660d7ea42788eed1ba69fef99e54fa8",
)
BUILTIN_2_5D_PROFILE = (
    "profile_2_5d",
    "d102a6a74eaea1b344fe18eac1364af277d91f8f617570a2ad5f9ce71c608b8b",
)
BUILTIN_CAPABILITY_CATALOG_HASH = "a24c8eb4f15101bd0988c6af5ba9c51bb28d087ad7300340ee971c26552e3b94"
FORBIDDEN_COMPONENTS = frozenset(
    {
        ".agents",
        ".world" + "forge",
        "authoring",
        "bibles",
        "candidates",
        "claims",
        "credentials",
        "mcp",
        "prompts",
        "providers",
        "receipts",
        "recipes",
        "references",
        "requests",
        "source",
        "specs",
        "weights",
        "workflows",
    }
)
FORBIDDEN_FORMATS = frozenset(
    {
        "isoworld.source_manifest",
        "rpg-world-forge.asset_processing_receipt",
        "rpg-world-forge.asset_processing_recipe",
        "rpg-world-forge.asset_production_receipt",
        "rpg-world-forge.asset_production_request",
        "rpg-world-forge.asset_spec",
        "rpg-world-forge.modly_capability_discovery",
        "rpg-world-forge.task_claim",
    }
)


class ComposedCatalogError(ValueError):
    """Raised when a composed release cannot be independently authorized."""


class _DuplicateKeyError(ValueError):
    pass


class _NonFiniteNumberError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_number(token: str) -> None:
    raise _NonFiniteNumberError(f"non-finite JSON number: {token}")


@dataclass(frozen=True, slots=True, order=True)
class ComposedCatalogRelease:
    world_id: str
    world_content_hash: str
    release_id: str
    profile_id: str
    profile_hash: str
    adapter_id: str
    adapter_version: str
    adapter_hash: str
    composition_hash: str
    bundle_id: str
    bundle_version: str
    bundle_hash: str
    path: str


@dataclass(frozen=True, slots=True)
class ComposedCatalogState:
    entries: tuple[ComposedCatalogRelease, ...]
    head_hash: str


@dataclass(frozen=True, slots=True)
class VerifiedComposedBundle:
    _owner: ResourceSnapshotOwner = field(repr=False, compare=False)
    release: ComposedCatalogRelease
    root: Path
    worldpack_path: Path
    renderpack_path: Path | None
    assetpack_path: Path | None
    manifest: dict[str, Any]
    composition: dict[str, Any]
    profile: dict[str, Any]
    adapter: dict[str, Any]
    presentation_plan: CompositionPlan
    native_compatible: bool
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    def __enter__(self) -> VerifiedComposedBundle:
        if self._closed:
            raise ComposedCatalogError("composed release snapshot is already closed")
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, traceback
        try:
            self.close()
        except ComposedCatalogError as cleanup_error:
            if not note_cleanup_failure(
                exc,
                cleanup_error,
                context="composed release snapshot cleanup",
            ):
                raise

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._owner.close()
        except ResourceSnapshotError as exc:
            raise ComposedCatalogError(f"could not close composed release snapshot: {exc}") from exc
        object.__setattr__(self, "_closed", True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_file_record(path: Path) -> tuple[int, str]:
    try:
        with ResourceSnapshotOwner() as owner:
            captured = owner.materialize_file(
                path.parent,
                PurePosixPath(path.name),
                limit=MAX_OWNED_RESOURCE_BYTES,
            )
            return captured.size, captured.sha256
    except (OSError, ResourceSnapshotError) as exc:
        raise ComposedCatalogError(f"could not hash stable file {path}: {exc}") from exc


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_hash(document: dict[str, Any], field: str) -> str:
    payload = dict(document)
    payload.pop(field, None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _read_object(path: Path, *, limit: int = MAX_JSON_BYTES) -> tuple[dict[str, Any], bytes]:
    try:
        with ResourceSnapshotOwner() as owner:
            captured = owner.materialize_file(
                path.parent,
                PurePosixPath(path.name),
                limit=limit,
            )
            data = captured.path.read_bytes()
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_number,
        )
    except ComposedCatalogError:
        raise
    except (
        OSError,
        ResourceSnapshotError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKeyError,
        _NonFiniteNumberError,
    ) as exc:
        raise ComposedCatalogError(f"could not decode JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComposedCatalogError(f"expected a JSON object: {path}")
    return value, data


def _exact(value: object, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ComposedCatalogError(f"{context} does not have the closed field set")
    return value


def _portable(value: object, context: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ComposedCatalogError(f"{context} is not a portable relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or len(value.encode("utf-8")) > 1024
    ):
        raise ComposedCatalogError(f"{context} is not canonical")
    for component in path.parts:
        if not is_portable_path_component(component):
            raise ComposedCatalogError(f"{context} contains an unsafe component")
    return path


def _digest(value: object, context: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ComposedCatalogError(f"{context} is not lowercase SHA-256")
    return value


def _identifier(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or ID_PATTERN.fullmatch(value) is None
        or not is_portable_path_component(value)
    ):
        raise ComposedCatalogError(f"{context} is not a portable identifier")
    return value


def _capability_ids(value: object, context: str) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        raise ComposedCatalogError(f"{context} must be a non-empty array of IDs")
    identifiers = [_identifier(item, f"{context}/{index}") for index, item in enumerate(value)]
    if identifiers != sorted(set(identifiers)):
        raise ComposedCatalogError(f"{context} must be sorted unique IDs")
    return frozenset(identifiers)


def _catalog_capability_ids(value: object) -> frozenset[str]:
    if not isinstance(value, list) or not value or len(value) > 10_000:
        raise ComposedCatalogError("capability catalog must contain a non-empty capability array")
    identifiers: list[str] = []
    for index, raw in enumerate(value):
        entry = _exact(raw, CAPABILITY_FIELDS, f"capability catalog entry {index}")
        identifiers.append(_identifier(entry.get("id"), f"capability catalog entry {index}/id"))
        if entry.get("domain") not in {
            "content",
            "narrative",
            "packaging",
            "presentation",
            "simulation",
        }:
            raise ComposedCatalogError(f"capability catalog entry {index}/domain is unsupported")
        if entry.get("determinism") not in {
            "deterministic",
            "deterministic_presentation",
        }:
            raise ComposedCatalogError(
                f"capability catalog entry {index}/determinism is unsupported"
            )
    if identifiers != sorted(set(identifiers)):
        raise ComposedCatalogError("capability catalog IDs must be sorted and unique")
    return frozenset(identifiers)


def _contract_identity(
    document: dict[str, Any],
    fields: set[str],
    *,
    format_name: str,
    context: str,
) -> None:
    _exact(document, fields, context)
    if document.get("format") != format_name or document.get("format_version") != CONTRACT_VERSION:
        raise ComposedCatalogError(f"{context} format or version is unsupported")
    _digest(document.get("content_hash"), f"{context}/content_hash")


def _string_list(
    value: object,
    context: str,
    *,
    allowed: set[str],
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise ComposedCatalogError(f"{context} must be a bounded non-empty array")
    if any(type(item) is not str or item not in allowed for item in value):
        raise ComposedCatalogError(f"{context} contains an unsupported value")
    if value != sorted(set(value)):
        raise ComposedCatalogError(f"{context} must be sorted and unique")
    return tuple(value)


def _positive_integer(value: object, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
        raise ComposedCatalogError(f"{context} must be a positive bounded integer")
    return value


def _positive_bounded_number(
    value: object,
    context: str,
    *,
    maximum: int,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ComposedCatalogError(
            f"{context} is invalid; expected a finite positive value at most {maximum}"
        )
    if isinstance(value, int) and not 0 < value <= maximum:
        raise ComposedCatalogError(
            f"{context} is invalid; expected a finite positive value at most {maximum}"
        )
    try:
        number = float(value)
    except OverflowError as exc:
        raise ComposedCatalogError(
            f"{context} is invalid; expected a finite positive value at most {maximum}"
        ) from exc
    if not math.isfinite(number) or not 0 < number <= maximum:
        raise ComposedCatalogError(
            f"{context} is invalid; expected a finite positive value at most {maximum}"
        )
    return number


def _validate_capability_catalog(document: dict[str, Any]) -> frozenset[str]:
    _contract_identity(
        document,
        CAPABILITY_CATALOG_FIELDS,
        format_name="rpg-world-forge.runtime_capability_catalog",
        context="runtime capability catalog",
    )
    return _catalog_capability_ids(document.get("capabilities"))


def _validate_profile(document: dict[str, Any]) -> frozenset[str]:
    _contract_identity(
        document,
        PROFILE_FIELDS,
        format_name="rpg-world-forge.runtime_presentation_profile",
        context="runtime presentation profile",
    )
    _identifier(document.get("id"), "runtime presentation profile/id")
    if document.get("mode") not in PRESENTATION_MODES:
        raise ComposedCatalogError("runtime presentation profile/mode is unsupported")
    layers = document.get("layers")
    if (
        not isinstance(layers, list)
        or not layers
        or len(layers) > len(PRESENTATION_LAYERS)
        or any(type(item) is not str or item not in PRESENTATION_LAYERS for item in layers)
        or len(set(layers)) != len(layers)
    ):
        raise ComposedCatalogError("runtime presentation profile/layers are invalid")
    _string_list(
        document.get("required_packs"),
        "runtime presentation profile/required_packs",
        allowed=set(PACK_FORMATS),
        maximum=len(PACK_FORMATS),
    )
    return _capability_ids(
        document.get("required_capability_ids"),
        "runtime presentation profile/required_capability_ids",
    )


def _validate_adapter(document: dict[str, Any]) -> frozenset[str]:
    _contract_identity(
        document,
        ADAPTER_FIELDS,
        format_name="rpg-world-forge.runtime_adapter",
        context="runtime adapter",
    )
    _identifier(document.get("id"), "runtime adapter/id")
    _semver(document.get("version"), "runtime adapter/version")
    if document.get("state") not in {"declared", "verified"}:
        raise ComposedCatalogError("runtime adapter/state is unsupported")
    runtime_api = _exact(
        document.get("runtime_api"),
        {"minimum", "maximum_exclusive"},
        "runtime adapter/runtime_api",
    )
    minimum = _semver(runtime_api.get("minimum"), "runtime adapter/runtime_api/minimum")
    maximum = _semver(
        runtime_api.get("maximum_exclusive"),
        "runtime adapter/runtime_api/maximum_exclusive",
    )
    if tuple(map(int, minimum.split("."))) >= tuple(map(int, maximum.split("."))):
        raise ComposedCatalogError("runtime adapter/runtime_api range is empty")
    _string_list(
        document.get("platforms"),
        "runtime adapter/platforms",
        allowed=SUPPORTED_PLATFORMS,
        maximum=len(SUPPORTED_PLATFORMS),
    )
    _string_list(
        document.get("presentation_modes"),
        "runtime adapter/presentation_modes",
        allowed=PRESENTATION_MODES,
        maximum=len(PRESENTATION_MODES),
    )
    capabilities = _capability_ids(document.get("capability_ids"), "runtime adapter/capability_ids")
    components = _exact(document.get("components"), COMPONENT_NAMES, "runtime adapter/components")
    for name in sorted(COMPONENT_NAMES):
        component = _exact(
            components.get(name),
            {"id", "version"},
            f"runtime adapter/components/{name}",
        )
        _identifier(component.get("id"), f"runtime adapter/components/{name}/id")
        _semver(component.get("version"), f"runtime adapter/components/{name}/version")
    budgets = _exact(document.get("budgets"), BUDGET_FIELDS, "runtime adapter/budgets")
    for budget_field in sorted(BUDGET_FIELDS - {"target_frame_milliseconds"}):
        _positive_integer(
            budgets.get(budget_field),
            f"runtime adapter/budgets/{budget_field}",
        )
    _positive_bounded_number(
        budgets.get("target_frame_milliseconds"),
        "runtime adapter/budgets/target_frame_milliseconds",
        maximum=TARGET_FRAME_MILLISECONDS_MAX,
    )
    return capabilities


def _validate_composition(document: dict[str, Any]) -> frozenset[str]:
    _contract_identity(
        document,
        COMPOSITION_FIELDS,
        format_name="rpg-world-forge.runtime_composition",
        context="runtime composition",
    )
    _identifier(document.get("world_id"), "runtime composition/world_id")
    _digest(document.get("world_content_hash"), "runtime composition/world_content_hash")
    _semver(document.get("release_id"), "runtime composition/release_id")
    profile = _exact(
        document.get("profile"),
        {"id", "content_hash"},
        "runtime composition/profile",
    )
    _identifier(profile.get("id"), "runtime composition/profile/id")
    _digest(profile.get("content_hash"), "runtime composition/profile/content_hash")
    _digest(
        document.get("capability_catalog_hash"),
        "runtime composition/capability_catalog_hash",
    )
    adapter = _exact(
        document.get("adapter"),
        {"id", "version", "content_hash"},
        "runtime composition/adapter",
    )
    _identifier(adapter.get("id"), "runtime composition/adapter/id")
    _semver(adapter.get("version"), "runtime composition/adapter/version")
    _digest(adapter.get("content_hash"), "runtime composition/adapter/content_hash")
    packs = document.get("packs")
    if (
        not isinstance(packs, dict)
        or "worldpack" not in packs
        or not set(packs) <= set(PACK_FORMATS)
    ):
        raise ComposedCatalogError("runtime composition/packs has an invalid closed field set")
    pack_paths: set[str] = set()
    for kind in sorted(packs):
        reference = _exact(
            packs[kind],
            {"path", "format", "format_version", "content_hash"},
            f"runtime composition/packs/{kind}",
        )
        path = _portable(reference.get("path"), f"runtime composition/packs/{kind}/path")
        expected_format, versions = PACK_FORMATS[kind]
        version = reference.get("format_version")
        if (
            reference.get("format") != expected_format
            or isinstance(version, bool)
            or not isinstance(version, int)
            or version not in versions
        ):
            raise ComposedCatalogError(f"runtime composition/packs/{kind} is unsupported")
        _digest(reference.get("content_hash"), f"runtime composition/packs/{kind}/content_hash")
        if path.as_posix() in pack_paths:
            raise ComposedCatalogError("runtime composition pack paths must be unique")
        pack_paths.add(path.as_posix())
    capabilities = _capability_ids(
        document.get("required_capability_ids"),
        "runtime composition/required_capability_ids",
    )
    owners = document.get("slot_owners")
    if not isinstance(owners, list) or not owners or len(owners) > 100_000:
        raise ComposedCatalogError("runtime composition/slot_owners must be bounded and non-empty")
    order: list[tuple[str, str, str, str, str]] = []
    slots: set[str] = set()
    for index, raw in enumerate(owners):
        context = f"runtime composition/slot_owners/{index}"
        owner = _exact(
            raw,
            {"slot", "plane", "pack", "asset_id", "representation"},
            context,
        )
        slot = owner.get("slot")
        plane = owner.get("plane")
        pack = owner.get("pack")
        representation = owner.get("representation")
        asset_id = _identifier(owner.get("asset_id"), f"{context}/asset_id")
        if not isinstance(slot, str) or SLOT_PATTERN.fullmatch(slot) is None or slot in slots:
            raise ComposedCatalogError(f"{context}/slot is invalid or duplicated")
        slots.add(slot)
        if plane not in SLOT_PLANES or pack not in {"assetpack", "renderpack"} or pack not in packs:
            raise ComposedCatalogError(f"{context} plane or pack is unsupported")
        if representation not in SLOT_REPRESENTATIONS:
            raise ComposedCatalogError(f"{context}/representation is unsupported")
        if (representation == "3d") != (pack == "assetpack"):
            raise ComposedCatalogError(f"{context} representation and pack disagree")
        if (
            (plane == "audio" and representation != "audio")
            or (plane == "ui_overlay" and representation != "2d")
            or (isinstance(plane, str) and plane.startswith("world_") and representation == "audio")
        ):
            raise ComposedCatalogError(f"{context} plane and representation disagree")
        order.append((slot, str(plane), str(pack), asset_id, str(representation)))
    if order != sorted(order):
        raise ComposedCatalogError("runtime composition/slot_owners are not canonically ordered")
    return capabilities


def _validate_compatibility_report(document: dict[str, Any]) -> None:
    _contract_identity(
        document,
        COMPATIBILITY_REPORT_FIELDS,
        format_name="rpg-world-forge.runtime_compatibility_report",
        context="runtime compatibility report",
    )
    for field_name in (
        "composition_hash",
        "world_content_hash",
        "profile_hash",
        "capability_catalog_hash",
        "adapter_hash",
    ):
        _digest(document.get(field_name), f"runtime compatibility report/{field_name}")
    pack_hashes = document.get("pack_hashes")
    if (
        not isinstance(pack_hashes, dict)
        or "worldpack" not in pack_hashes
        or not set(pack_hashes) <= set(PACK_FORMATS)
    ):
        raise ComposedCatalogError("runtime compatibility report/pack_hashes is invalid")
    for kind, digest in pack_hashes.items():
        _digest(digest, f"runtime compatibility report/pack_hashes/{kind}")
    if document.get("platform") not in SUPPORTED_PLATFORMS:
        raise ComposedCatalogError("runtime compatibility report/platform is unsupported")
    checks = document.get("checks")
    if not isinstance(checks, list) or [
        check.get("id") if isinstance(check, dict) else None for check in checks
    ] != list(COMPATIBILITY_CHECK_IDS):
        raise ComposedCatalogError(
            "runtime compatibility report/checks must contain every static check in order"
        )
    for check_index, raw_check in enumerate(checks):
        context = f"runtime compatibility report/checks/{check_index}"
        check = _exact(raw_check, {"id", "passed", "issues"}, context)
        if not isinstance(check.get("passed"), bool):
            raise ComposedCatalogError(f"{context}/passed must be boolean")
        issues = check.get("issues")
        if not isinstance(issues, list) or len(issues) > 100_000:
            raise ComposedCatalogError(f"{context}/issues must be a bounded array")
        issue_order: list[tuple[str, str]] = []
        for issue_index, raw_issue in enumerate(issues):
            issue_context = f"{context}/issues/{issue_index}"
            issue = _exact(raw_issue, {"code", "path"}, issue_context)
            code = issue.get("code")
            path = issue.get("path")
            if code not in COMPATIBILITY_ISSUE_CODES:
                raise ComposedCatalogError(f"{issue_context}/code is unsupported")
            if (
                not isinstance(path, str)
                or len(path.encode("utf-8")) > 1024
                or ISSUE_PATH_PATTERN.fullmatch(path) is None
            ):
                raise ComposedCatalogError(f"{issue_context}/path is invalid")
            issue_order.append((str(code), path))
        if issue_order != sorted(set(issue_order)):
            raise ComposedCatalogError(f"{context}/issues must be sorted unique")
        if check["passed"] != (not issues):
            raise ComposedCatalogError(f"{context}/passed disagrees with issues")
    compatible = document.get("compatible")
    if not isinstance(compatible, bool) or compatible != all(
        bool(check["passed"]) for check in checks
    ):
        raise ComposedCatalogError("runtime compatibility report/compatible disagrees with checks")


def _manifest_reference(
    value: object,
    fields: set[str],
    *,
    context: str,
    path: str,
    format_name: str,
    versions: set[int],
) -> dict[str, Any]:
    reference = _exact(value, fields, context)
    version = reference.get("format_version")
    if (
        reference.get("path") != path
        or reference.get("format") != format_name
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version not in versions
    ):
        raise ComposedCatalogError(f"{context} path, format, or version is unsupported")
    _digest(reference.get("content_hash"), f"{context}/content_hash")
    return reference


def _validate_manifest_authorization_shape(manifest: dict[str, Any]) -> None:
    target = _exact(
        manifest.get("compatibility_target"),
        {"platform", "runtime_api_version"},
        "compatibility target",
    )
    if target.get("platform") not in SUPPORTED_PLATFORMS:
        raise ComposedCatalogError("compatibility target platform is unsupported")
    _semver(target.get("runtime_api_version"), "compatibility target/runtime_api_version")
    contracts = _exact(
        manifest.get("contracts"),
        {
            "runtime_composition",
            "presentation_profile",
            "capability_catalog",
            "runtime_adapter",
        },
        "manifest contracts",
    )
    composition = _manifest_reference(
        contracts.get("runtime_composition"),
        {
            "path",
            "format",
            "format_version",
            "content_hash",
            "world_id",
            "release_id",
        },
        context="manifest contracts/runtime_composition",
        path="contracts/runtime-composition.json",
        format_name="rpg-world-forge.runtime_composition",
        versions={1},
    )
    _identifier(composition.get("world_id"), "manifest composition/world_id")
    _semver(composition.get("release_id"), "manifest composition/release_id")
    profile = _manifest_reference(
        contracts.get("presentation_profile"),
        {"path", "format", "format_version", "content_hash", "id"},
        context="manifest contracts/presentation_profile",
        path="contracts/runtime-presentation-profile.json",
        format_name="rpg-world-forge.runtime_presentation_profile",
        versions={1},
    )
    _identifier(profile.get("id"), "manifest profile/id")
    _manifest_reference(
        contracts.get("capability_catalog"),
        {"path", "format", "format_version", "content_hash"},
        context="manifest contracts/capability_catalog",
        path="contracts/runtime-capability-catalog.json",
        format_name="rpg-world-forge.runtime_capability_catalog",
        versions={1},
    )
    adapter = _manifest_reference(
        contracts.get("runtime_adapter"),
        {"path", "format", "format_version", "content_hash", "id", "version"},
        context="manifest contracts/runtime_adapter",
        path="contracts/runtime-adapter.json",
        format_name="rpg-world-forge.runtime_adapter",
        versions={1},
    )
    _identifier(adapter.get("id"), "manifest adapter/id")
    _semver(adapter.get("version"), "manifest adapter/version")
    evidence = _manifest_reference(
        manifest.get("compatibility_evidence"),
        {"path", "format", "format_version", "content_hash", "composition_hash"},
        context="manifest compatibility evidence",
        path="evidence/runtime-compatibility-report.json",
        format_name="rpg-world-forge.runtime_compatibility_report",
        versions={1},
    )
    _digest(evidence.get("composition_hash"), "manifest evidence/composition_hash")
    if evidence.get("composition_hash") != composition.get("content_hash"):
        raise ComposedCatalogError("manifest compatibility evidence is misbound")
    packs = _exact(
        manifest.get("packs"),
        {"worldpack", "renderpack", "assetpack"},
        "manifest packs",
    )
    worldpack = _manifest_reference(
        packs.get("worldpack"),
        {"path", "format", "format_version", "content_hash", "world_id"},
        context="manifest packs/worldpack",
        path="packs/worldpack/worldpack.json",
        format_name="isoworld.worldpack",
        versions={1, 2, 3, 4, 5},
    )
    _identifier(worldpack.get("world_id"), "manifest packs/worldpack/world_id")
    selected = 0
    for kind, expected_format in (
        ("renderpack", "isoworld.renderpack"),
        ("assetpack", "rpg-world-forge.assetpack"),
    ):
        raw = packs.get(kind)
        if raw is None:
            continue
        selected += 1
        reference = _manifest_reference(
            raw,
            {"path", "format", "format_version", "content_hash", "world_content_hash"},
            context=f"manifest packs/{kind}",
            path=f"packs/{kind}/{kind}.json",
            format_name=expected_format,
            versions={1},
        )
        _digest(
            reference.get("world_content_hash"),
            f"manifest packs/{kind}/world_content_hash",
        )
    if selected == 0:
        raise ComposedCatalogError("manifest selects no content presentation pack")


def _semver(value: object, context: str) -> str:
    if not isinstance(value, str) or SEMVER_PATTERN.fullmatch(value) is None:
        raise ComposedCatalogError(f"{context} is not canonical semantic version")
    return value


def _entry(raw: object, index: int) -> ComposedCatalogRelease:
    fields = {
        "world_id",
        "world_content_hash",
        "release_id",
        "profile_id",
        "profile_hash",
        "adapter_id",
        "adapter_version",
        "adapter_hash",
        "composition_hash",
        "bundle_id",
        "bundle_version",
        "bundle_hash",
        "path",
    }
    value = _exact(raw, fields, f"catalog entry {index}")
    world_id = _identifier(value["world_id"], f"entry {index}/world_id")
    release_id = _semver(value["release_id"], f"entry {index}/release_id")
    profile_id = _identifier(value["profile_id"], f"entry {index}/profile_id")
    adapter_id = _identifier(value["adapter_id"], f"entry {index}/adapter_id")
    adapter_version = _semver(value["adapter_version"], f"entry {index}/adapter_version")
    bundle_id = _identifier(value["bundle_id"], f"entry {index}/bundle_id")
    bundle_version = _semver(value["bundle_version"], f"entry {index}/bundle_version")
    expected = (
        "game_data/compositions/"
        f"{world_id}/{release_id}/{profile_id}/{adapter_id}/{adapter_version}/"
        f"{bundle_id}/{bundle_version}"
    )
    if value["path"] != expected:
        raise ComposedCatalogError(f"entry {index}/path must equal {expected!r}")
    return ComposedCatalogRelease(
        world_id,
        _digest(value["world_content_hash"], f"entry {index}/world_content_hash"),
        release_id,
        profile_id,
        _digest(value["profile_hash"], f"entry {index}/profile_hash"),
        adapter_id,
        adapter_version,
        _digest(value["adapter_hash"], f"entry {index}/adapter_hash"),
        _digest(value["composition_hash"], f"entry {index}/composition_hash"),
        bundle_id,
        bundle_version,
        _digest(value["bundle_hash"], f"entry {index}/bundle_hash"),
        expected,
    )


def _path_collision_guard(paths: list[str], context: str) -> None:
    seen: dict[str, str] = {}
    for path in paths:
        prefix: list[str] = []
        for component in PurePosixPath(path).parts:
            prefix.append(component)
            exact = "/".join(prefix)
            key = unicodedata.normalize("NFC", exact).casefold()
            previous = seen.setdefault(key, exact)
            if previous != exact:
                raise ComposedCatalogError(
                    f"{context} has a portable prefix collision: {previous!r}, {exact!r}"
                )


def _validate_entries(
    raw_entries: object,
    *,
    context: str,
) -> tuple[ComposedCatalogRelease, ...]:
    if not isinstance(raw_entries, list) or len(raw_entries) > 10_000:
        raise ComposedCatalogError(f"{context} entries must be bounded")
    entries = tuple(_entry(raw, index) for index, raw in enumerate(raw_entries))
    identity = [
        (
            item.world_id,
            item.release_id,
            item.profile_id,
            item.adapter_id,
            item.adapter_version,
            item.bundle_id,
            item.bundle_version,
        )
        for item in entries
    ]
    if identity != sorted(set(identity)):
        raise ComposedCatalogError(f"{context} entries are not unique and canonically ordered")
    _world_hashes(entries, context=context)
    for projection, label in (
        ([(item.profile_id, item.profile_hash) for item in entries], "profile"),
        (
            [(item.adapter_id, item.adapter_version, item.adapter_hash) for item in entries],
            "adapter",
        ),
        (
            [(item.bundle_id, item.bundle_version, item.bundle_hash) for item in entries],
            "bundle",
        ),
    ):
        keyed: dict[tuple[str, ...], str] = {}
        for row in projection:
            key, digest = tuple(row[:-1]), str(row[-1])
            previous = keyed.setdefault(key, digest)
            if previous != digest:
                raise ComposedCatalogError(f"{context} {label} identity maps to multiple hashes")
    if len({item.bundle_hash for item in entries}) != len(entries):
        raise ComposedCatalogError(f"{context} reuses a bundle hash")
    if len({item.path for item in entries}) != len(entries):
        raise ComposedCatalogError(f"{context} reuses a bundle path")
    _path_collision_guard([item.path for item in entries], context)
    return entries


def _world_hashes(
    entries: tuple[ComposedCatalogRelease, ...],
    *,
    context: str,
) -> dict[tuple[str, str], str]:
    known: dict[tuple[str, str], str] = {}
    for entry in entries:
        key = (entry.world_id, entry.release_id)
        previous = known.setdefault(key, entry.world_content_hash)
        if previous != entry.world_content_hash:
            raise ComposedCatalogError(
                f"{context} maps one world/release to multiple world content hashes"
            )
    return known


def _load_base_catalog(root: Path) -> tuple[str, tuple[ComposedCatalogRelease, ...]]:
    document, data = _read_object(root / CATALOG_RELATIVE_PATH)
    _exact(document, {"format", "format_version", "entries", "content_hash"}, "catalog")
    if document["format"] != CATALOG_FORMAT or document["format_version"] != 1:
        raise ComposedCatalogError("unknown composed runtime catalog format")
    if data != _canonical_bytes(document):
        raise ComposedCatalogError("composed runtime catalog bytes are not canonical")
    content_hash = _digest(document["content_hash"], "catalog/content_hash")
    if content_hash != _canonical_hash(document, "content_hash"):
        raise ComposedCatalogError("composed runtime catalog content hash does not verify")
    entries = _validate_entries(document["entries"], context="base composed catalog")
    if entries:
        raise ComposedCatalogError("base composed catalog must remain canonically empty")
    return content_hash, entries


def _load_catalog_generations(
    root: Path,
    *,
    base_hash: str,
    allow_incomplete: bool,
) -> ComposedCatalogState:
    generations_root = root / CATALOG_GENERATIONS_RELATIVE_PATH
    try:
        path_file_stat(generations_root)
    except FileNotFoundError:
        return ComposedCatalogState((), base_hash)
    except OSError as exc:
        raise ComposedCatalogError(
            f"could not inspect composed catalog generations: {exc}"
        ) from exc
    disk_files, disk_directories = _walk_exact_regular_tree(generations_root)
    top_level_names = {
        relative for relative in disk_directories if "/" not in PurePosixPath(relative).as_posix()
    }
    generation_names = {
        name for name in top_level_names if SHA256_PATTERN.fullmatch(name) is not None
    }
    stage_names = top_level_names - generation_names
    stage_hashes: dict[str, str] = {}
    for name in stage_names:
        match = CATALOG_GENERATION_STAGE_PATTERN.fullmatch(name)
        if match is None:
            raise ComposedCatalogError("composed catalog generation namespace is not exact")
        stage_hashes[name] = match.group(1)
    if (
        len(top_level_names) > MAX_CATALOG_GENERATIONS
        or disk_directories != top_level_names
        or disk_files != {f"{name}/{CATALOG_GENERATION_NAME}" for name in top_level_names}
    ):
        raise ComposedCatalogError("composed catalog generation namespace is not exact")
    if stage_names and not allow_incomplete:
        raise ComposedCatalogError("composed catalog contains an unpublished generation stage")
    if not generation_names and not stage_names:
        if allow_incomplete:
            return ComposedCatalogState((), base_hash)
        raise ComposedCatalogError("composed catalog generation root is empty")

    records: dict[str, tuple[str, tuple[ComposedCatalogRelease, ...]]] = {}
    for generation in sorted(top_level_names):
        digest = stage_hashes.get(generation, generation)
        _digest(digest, "composed catalog generation directory")
        path = generations_root / generation / CATALOG_GENERATION_NAME
        document, data = _read_object(path)
        _exact(
            document,
            {"format", "format_version", "previous_hash", "entries", "content_hash"},
            f"composed catalog generation {generation}",
        )
        if (
            document["format"] != CATALOG_GENERATION_FORMAT
            or document["format_version"] != 1
            or data != _canonical_bytes(document)
            or _digest(document["content_hash"], "catalog generation/content_hash") != digest
            or _canonical_hash(document, "content_hash") != digest
        ):
            raise ComposedCatalogError("composed catalog generation identity does not verify")
        previous_hash = _digest(
            document["previous_hash"],
            "composed catalog generation/previous_hash",
        )
        entries = _validate_entries(
            document["entries"],
            context=f"composed catalog generation {generation}",
        )
        if not entries:
            raise ComposedCatalogError("composed catalog generation cannot be empty")
        if generation in generation_names:
            records[generation] = previous_hash, entries

    after_files, after_directories = _walk_exact_regular_tree(generations_root)
    if after_files != disk_files or after_directories != disk_directories:
        raise ComposedCatalogError("composed catalog generation namespace changed while reading")

    children: dict[str, list[str]] = {}
    known_hashes = {base_hash, *records}
    for generation, (previous_hash, _entries) in records.items():
        if previous_hash not in known_hashes or previous_hash == generation:
            raise ComposedCatalogError("composed catalog generation chain is disconnected")
        children.setdefault(previous_hash, []).append(generation)
    if any(len(values) != 1 for values in children.values()):
        raise ComposedCatalogError("composed catalog generation chain has a fork")

    head_hash = base_hash
    entries: tuple[ComposedCatalogRelease, ...] = ()
    visited: set[str] = set()
    while successors := children.get(head_hash):
        generation = successors[0]
        if generation in visited:
            raise ComposedCatalogError("composed catalog generation chain has a cycle")
        next_entries = records[generation][1]
        if len(next_entries) != len(entries) + 1 or any(
            previous not in next_entries for previous in entries
        ):
            raise ComposedCatalogError(
                "composed catalog generation must append exactly one immutable entry"
            )
        visited.add(generation)
        head_hash = generation
        entries = next_entries
    if visited != set(records):
        raise ComposedCatalogError("composed catalog generation chain is disconnected")
    return ComposedCatalogState(entries, head_hash)


def load_composed_catalog_state(
    project_root: str | Path,
    *,
    allow_incomplete: bool = False,
) -> ComposedCatalogState:
    """Load the immutable catalog chain without authorizing bundle storage."""

    root = Path(project_root).resolve()
    base_hash, _entries = _load_base_catalog(root)
    return _load_catalog_generations(
        root,
        base_hash=base_hash,
        allow_incomplete=allow_incomplete,
    )


def load_composed_catalog(
    project_root: str | Path,
) -> tuple[ComposedCatalogRelease, ...]:
    root = Path(project_root).resolve()
    state = load_composed_catalog_state(root)
    _audit_storage(root, state.entries)
    return state.entries


def _audit_storage(root: Path, entries: tuple[ComposedCatalogRelease, ...]) -> None:
    tree = root / "game_data/compositions"
    if not entries:
        try:
            tree_info = path_file_stat(tree)
        except FileNotFoundError:
            tree_info = None
        except OSError as exc:
            raise ComposedCatalogError(f"could not inspect compositions storage: {exc}") from exc
        if tree_info is not None:
            raise ComposedCatalogError("empty composed catalog requires no compositions tree")
        return
    expected = {item.path.removeprefix("game_data/compositions/") for item in entries}
    expected_paths = tuple(PurePosixPath(value) for value in sorted(expected))
    files, directories = _walk_exact_regular_tree(tree)
    for relative in directories:
        pure_directory = PurePosixPath(relative)
        if not any(
            pure_directory == expected_path
            or pure_directory in expected_path.parents
            or expected_path in pure_directory.parents
            for expected_path in expected_paths
        ):
            raise ComposedCatalogError("compositions tree contains an unmanaged directory")
    for relative in files:
        relative_file = PurePosixPath(relative)
        if not any(expected_path in relative_file.parents for expected_path in expected_paths):
            raise ComposedCatalogError("compositions tree contains an unmanaged file")
    actual = {
        PurePosixPath(relative).parent.as_posix()
        for relative in files
        if PurePosixPath(relative).name == MANIFEST_NAME
    }
    if actual != expected:
        raise ComposedCatalogError("composed catalog and storage disagree")


def select_composed_release(
    entries: tuple[ComposedCatalogRelease, ...],
    *,
    world_id: str,
    release_id: str,
    profile_id: str,
    adapter_id: str | None = None,
    adapter_version: str | None = None,
    bundle_id: str | None = None,
    bundle_version: str | None = None,
) -> ComposedCatalogRelease:
    _world_hashes(entries, context="composed selection")
    if (adapter_id is None) != (adapter_version is None):
        raise ComposedCatalogError("adapter id and version must be selected together")
    if (bundle_id is None) != (bundle_version is None):
        raise ComposedCatalogError("bundle id and version must be selected together")
    matches = tuple(
        entry
        for entry in entries
        if entry.world_id == world_id
        and entry.release_id == release_id
        and entry.profile_id == profile_id
        and (adapter_id is None or entry.adapter_id == adapter_id)
        and (adapter_version is None or entry.adapter_version == adapter_version)
        and (bundle_id is None or entry.bundle_id == bundle_id)
        and (bundle_version is None or entry.bundle_version == bundle_version)
    )
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ComposedCatalogError("no composed release matches the exact selection")
    raise ComposedCatalogError("composed selection is ambiguous; specify adapter and bundle")


def _safe_file(root: Path, relative: str) -> Path:
    pure = _portable(relative, "bundle file path")
    candidate = root.joinpath(*pure.parts)
    current = root
    for component in pure.parts:
        current = current / component
        info = path_file_stat(current)
        if is_link_or_reparse(info):
            raise ComposedCatalogError(f"bundle path contains a link or reparse point: {relative}")
    info = path_file_stat(candidate)
    if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ComposedCatalogError(f"bundle payload is not a standalone file: {relative}")
    return candidate


def _implicit_directories(files: set[str]) -> set[str]:
    directories: set[str] = set()
    for relative in files:
        parent = PurePosixPath(relative).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories


def _walk_exact_regular_tree(
    root: Path,
    *,
    max_nodes: int = MAX_TREE_NODES,
    max_directories: int = MAX_TREE_DIRECTORIES,
    max_depth: int = MAX_TREE_DEPTH,
    max_path_bytes: int = MAX_TREE_PATH_BYTES,
) -> tuple[set[str], set[str]]:
    try:
        root_info = path_file_stat(root)
    except OSError as exc:
        raise ComposedCatalogError(f"could not inspect composed bundle root: {exc}") from exc
    if is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise ComposedCatalogError("composed bundle root must be a real directory")
    files: set[str] = set()
    directories: set[str] = set()
    nodes = 1
    for current, names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        names.sort()
        file_names.sort()
        current_info = path_file_stat(current_path)
        if is_link_or_reparse(current_info) or not stat.S_ISDIR(current_info.st_mode):
            raise ComposedCatalogError("composed bundle traversal reached an unsafe directory")
        relative_current = current_path.relative_to(root)
        if len(relative_current.parts) > max_depth:
            raise ComposedCatalogError("composed bundle exceeds the directory depth bound")
        prospective_nodes = nodes + len(names) + len(file_names)
        prospective_directories = len(directories) + len(names)
        if prospective_nodes > max_nodes:
            raise ComposedCatalogError("composed bundle exceeds the tree node bound")
        if prospective_directories > max_directories:
            raise ComposedCatalogError("composed bundle exceeds the directory bound")
        for name in names:
            path = current_path / name
            info = path_file_stat(path)
            if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise ComposedCatalogError(
                    f"composed bundle contains an unsafe directory entry: {path}"
                )
            relative = path.relative_to(root).as_posix()
            if (
                len(PurePosixPath(relative).parts) > max_depth
                or len(relative.encode("utf-8")) > max_path_bytes
            ):
                raise ComposedCatalogError("composed bundle path exceeds its bound")
            directories.add(relative)
        for name in file_names:
            path = current_path / name
            info = path_file_stat(path)
            if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ComposedCatalogError(f"composed bundle contains an unsafe file entry: {path}")
            relative = path.relative_to(root).as_posix()
            if (
                len(PurePosixPath(relative).parts) > max_depth
                or len(relative.encode("utf-8")) > max_path_bytes
            ):
                raise ComposedCatalogError("composed bundle path exceeds its bound")
            files.add(relative)
        nodes = prospective_nodes
    return files, directories


def _scan_boundary(value: object, context: str) -> None:
    if isinstance(value, dict):
        format_name = value.get("format")
        if isinstance(format_name, str) and format_name in FORBIDDEN_FORMATS:
            raise ComposedCatalogError(f"{context} contains authoring-only format")
        for key, child in value.items():
            if key.casefold() in {
                "api_key",
                "credentials",
                "model_id",
                "negative_prompt",
                "provider_id",
                "workflow_file",
            }:
                raise ComposedCatalogError(f"{context} contains provider metadata")
            _scan_boundary(child, context)
    elif isinstance(value, list):
        for child in value:
            _scan_boundary(child, context)


def _host_is_linux_x86_64() -> bool:
    return (
        os.name == "posix"
        and host_platform.system() == "Linux"
        and host_platform.machine().casefold() in {"amd64", "x86_64"}
    )


def _snapshot_bundle(
    source_root: Path,
    records: dict[str, tuple[str, int, str]],
    manifest_bytes: bytes,
) -> ResourceSnapshotOwner:
    owner = ResourceSnapshotOwner()
    completed = False
    try:
        try:
            manifest = owner.materialize_file(
                source_root,
                PurePosixPath(MANIFEST_NAME),
                limit=16 * 1024 * 1024,
            )
            if manifest.size != len(manifest_bytes) or manifest.sha256 != _sha256_bytes(
                manifest_bytes
            ):
                raise ComposedCatalogError("composed bundle manifest changed during capture")
            for relative, (digest, size, _media_type) in records.items():
                if size > MAX_OWNED_RESOURCE_BYTES:
                    raise ComposedCatalogError(
                        f"composed payload exceeds the snapshot bound: {relative}"
                    )
                captured = owner.materialize_file(
                    source_root,
                    PurePosixPath(relative),
                    limit=max(1, size),
                )
                if captured.size != size or captured.sha256 != digest:
                    raise ComposedCatalogError(
                        f"composed payload changed during capture: {relative}"
                    )
        except ComposedCatalogError:
            raise
        except (OSError, ResourceSnapshotError) as exc:
            raise ComposedCatalogError(str(exc)) from exc
        completed = True
        return owner
    finally:
        if not completed:
            primary = sys.exception()
            try:
                owner.close()
            except ResourceSnapshotError as cleanup:
                if not note_cleanup_failure(
                    primary,
                    cleanup,
                    context="composed snapshot cleanup",
                ):
                    raise ComposedCatalogError(
                        f"could not close failed composed snapshot: {cleanup}"
                    ) from cleanup


def verify_composed_release(
    release: ComposedCatalogRelease,
    project_root: str | Path,
) -> VerifiedComposedBundle:
    """Verify one installed release and transfer its private snapshot to the caller."""

    owner_out: list[ResourceSnapshotOwner] = []
    completed = False
    try:
        try:
            authorized = load_composed_catalog(project_root)
            if sum(item == release for item in authorized) != 1:
                raise ComposedCatalogError(
                    "selected composed release is not uniquely authorized by the catalog"
                )
            result = _verify_composed_release(release, project_root, owner_out)
        except ComposedCatalogError:
            raise
        except (OSError, ResourceSnapshotError) as exc:
            raise ComposedCatalogError(str(exc)) from exc
        completed = True
        return result
    finally:
        if not completed and owner_out:
            primary = sys.exception()
            try:
                owner_out[0].close()
            except ResourceSnapshotError as cleanup:
                if not note_cleanup_failure(
                    primary,
                    cleanup,
                    context="composed snapshot cleanup",
                ):
                    raise ComposedCatalogError(
                        f"could not close failed composed snapshot: {cleanup}"
                    ) from cleanup


def _verify_composed_release(
    release: ComposedCatalogRelease,
    project_root: str | Path,
    owner_out: list[ResourceSnapshotOwner],
) -> VerifiedComposedBundle:
    project = Path(project_root).resolve()
    root = project.joinpath(*_portable(release.path, "composed release path").parts)
    try:
        root_info = path_file_stat(root)
    except OSError as exc:
        raise ComposedCatalogError(f"installed composed bundle root is unsafe: {exc}") from exc
    if is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise ComposedCatalogError("installed composed bundle root is unsafe")
    manifest, manifest_bytes = _read_object(_safe_file(root, MANIFEST_NAME), limit=16 * 1024 * 1024)
    if manifest_bytes != _canonical_bytes(manifest):
        raise ComposedCatalogError("composed bundle manifest bytes are not canonical")
    _exact(
        manifest,
        {
            "format",
            "format_version",
            "bundle_id",
            "bundle_version",
            "compatibility_target",
            "contracts",
            "compatibility_evidence",
            "packs",
            "files",
            "licenses",
            "bundle_hash",
        },
        "composed bundle manifest",
    )
    if manifest["format"] != BUNDLE_FORMAT or manifest["format_version"] != 1:
        raise ComposedCatalogError("unknown composed bundle format")
    if (
        manifest["bundle_id"] != release.bundle_id
        or manifest["bundle_version"] != release.bundle_version
        or manifest["bundle_hash"] != release.bundle_hash
        or _canonical_hash(manifest, "bundle_hash") != release.bundle_hash
    ):
        raise ComposedCatalogError("catalog and composed bundle identity disagree")
    _validate_manifest_authorization_shape(manifest)
    raw_files = manifest["files"]
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > MAX_FILES:
        raise ComposedCatalogError("composed bundle file inventory is invalid")
    records: dict[str, tuple[str, int, str]] = {}
    file_records: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_files):
        value = _exact(raw, {"path", "sha256", "size", "media_type"}, f"files/{index}")
        path = _portable(value["path"], f"files/{index}/path").as_posix()
        if any(
            component.casefold() in FORBIDDEN_COMPONENTS for component in PurePosixPath(path).parts
        ):
            raise ComposedCatalogError("composed bundle exposes authoring-only paths")
        digest = _digest(value["sha256"], f"files/{index}/sha256")
        size = value["size"]
        media_type = value["media_type"]
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(media_type, str)
            or not media_type
            or path in records
        ):
            raise ComposedCatalogError(f"files/{index} metadata is invalid")
        records[path] = (digest, size, media_type)
        file_records.append(value)
    paths = list(records)
    if paths != sorted(paths):
        raise ComposedCatalogError("composed bundle files are not canonically ordered")
    raw_licenses = manifest["licenses"]
    if (
        not isinstance(raw_licenses, list)
        or not raw_licenses
        or len(raw_licenses) > MAX_LICENSE_FILES
    ):
        raise ComposedCatalogError("composed bundle licenses must be a bounded non-empty list")
    licenses: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_licenses):
        value = _exact(
            raw,
            {"path", "sha256", "size", "media_type"},
            f"licenses/{index}",
        )
        path = _portable(value["path"], f"licenses/{index}/path")
        expected_media_type = LICENSE_MEDIA_TYPES.get(Path(path.name).suffix.casefold())
        size = value["size"]
        if (
            len(path.parts) != 2
            or path.parts[0] != "licenses"
            or expected_media_type is None
            or value["media_type"] != expected_media_type
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 <= size <= MAX_LICENSE_BYTES
        ):
            raise ComposedCatalogError("composed bundle license record is invalid")
        _digest(value["sha256"], f"licenses/{index}/sha256")
        licenses.append(value)
    license_files = [
        record for record in file_records if str(record["path"]).startswith("licenses/")
    ]
    if licenses != license_files:
        raise ComposedCatalogError(
            "manifest licenses must exactly equal the regular license-file inventory subset"
        )
    _path_collision_guard(paths, "composed bundle")
    if sum(record[1] for record in records.values()) > MAX_TOTAL_BYTES:
        raise ComposedCatalogError("composed bundle exceeds its total-byte bound")
    expected_files = {MANIFEST_NAME, *records}
    expected_directories = _implicit_directories(expected_files)
    disk_files, disk_directories = _walk_exact_regular_tree(root)
    if disk_files != expected_files or disk_directories != expected_directories:
        raise ComposedCatalogError(
            "composed bundle namespace differs from its exact manifest inventory"
        )
    snapshot_owner = _snapshot_bundle(root, records, manifest_bytes)
    owner_out.append(snapshot_owner)
    after_files, after_directories = _walk_exact_regular_tree(root)
    if after_files != disk_files or after_directories != disk_directories:
        raise ComposedCatalogError("composed bundle namespace changed during capture")
    verified_root = snapshot_owner.root
    documents: dict[str, dict[str, Any]] = {}
    for relative, (digest, size, media_type) in records.items():
        path = _safe_file(verified_root, relative)
        if media_type == "application/json":
            document, data = _read_object(path)
            if len(data) != size or _sha256_bytes(data) != digest:
                raise ComposedCatalogError(f"composed payload failed size/hash: {relative}")
            if data != _canonical_bytes(document):
                raise ComposedCatalogError(f"composed JSON is not canonical: {relative}")
            _scan_boundary(document, relative)
            if "content_hash" in document and (
                _digest(document["content_hash"], f"{relative}/content_hash")
                != _canonical_hash(document, "content_hash")
            ):
                raise ComposedCatalogError(
                    f"composed JSON content hash does not verify: {relative}"
                )
            documents[relative] = document
        else:
            captured_size, captured_digest = _stable_file_record(path)
            if captured_size != size or captured_digest != digest:
                raise ComposedCatalogError(f"composed payload failed size/hash: {relative}")
    fixed = {
        "contracts/runtime-composition.json",
        "contracts/runtime-presentation-profile.json",
        "contracts/runtime-capability-catalog.json",
        "contracts/runtime-adapter.json",
        "evidence/runtime-compatibility-report.json",
        "packs/worldpack/worldpack.json",
    }
    if not fixed <= records.keys():
        raise ComposedCatalogError("composed bundle is missing fixed runtime documents")
    composition = documents["contracts/runtime-composition.json"]
    profile = documents["contracts/runtime-presentation-profile.json"]
    capability_catalog = documents["contracts/runtime-capability-catalog.json"]
    adapter = documents["contracts/runtime-adapter.json"]
    report = documents["evidence/runtime-compatibility-report.json"]
    catalog_ids = _validate_capability_catalog(capability_catalog)
    profile_ids = _validate_profile(profile)
    adapter_ids = _validate_adapter(adapter)
    required_ids = _validate_composition(composition)
    _validate_compatibility_report(report)
    if not profile_ids <= catalog_ids:
        raise ComposedCatalogError("profile capabilities are absent from the bundled catalog")
    if not adapter_ids <= catalog_ids:
        raise ComposedCatalogError("adapter capabilities are absent from the bundled catalog")
    if not required_ids <= catalog_ids:
        raise ComposedCatalogError("composition capabilities are absent from the bundled catalog")
    if (
        composition.get("content_hash") != release.composition_hash
        or composition.get("world_id") != release.world_id
        or composition.get("release_id") != release.release_id
        or composition.get("world_content_hash") != release.world_content_hash
        or profile.get("id") != release.profile_id
        or profile.get("content_hash") != release.profile_hash
        or adapter.get("id") != release.adapter_id
        or adapter.get("version") != release.adapter_version
        or adapter.get("content_hash") != release.adapter_hash
    ):
        raise ComposedCatalogError("catalog identity disagrees with bundled contracts")
    composition_profile = _exact(
        composition.get("profile"),
        {"id", "content_hash"},
        "composition/profile",
    )
    if composition_profile["id"] != profile.get("id") or composition_profile[
        "content_hash"
    ] != profile.get("content_hash"):
        raise ComposedCatalogError(
            "composition profile reference disagrees with the bundled profile"
        )
    composition_adapter = _exact(
        composition.get("adapter"),
        {"id", "version", "content_hash"},
        "composition/adapter",
    )
    if (
        composition_adapter["id"] != adapter.get("id")
        or composition_adapter["version"] != adapter.get("version")
        or composition_adapter["content_hash"] != adapter.get("content_hash")
    ):
        raise ComposedCatalogError(
            "composition adapter reference disagrees with the bundled adapter"
        )
    if _digest(
        composition.get("capability_catalog_hash"),
        "composition/capability_catalog_hash",
    ) != capability_catalog.get("content_hash"):
        raise ComposedCatalogError(
            "composition capability catalog hash disagrees with the bundled catalog"
        )
    contracts = manifest["contracts"]
    if not isinstance(contracts, dict):
        raise ComposedCatalogError("manifest contracts must be an object")
    for key, expected_path in (
        ("runtime_composition", "contracts/runtime-composition.json"),
        ("presentation_profile", "contracts/runtime-presentation-profile.json"),
        ("capability_catalog", "contracts/runtime-capability-catalog.json"),
        ("runtime_adapter", "contracts/runtime-adapter.json"),
    ):
        reference = contracts.get(key)
        if not isinstance(reference, dict) or reference.get("path") != expected_path:
            raise ComposedCatalogError("manifest contract reference is invalid")
        document = documents[expected_path]
        if reference.get("content_hash") != document.get("content_hash"):
            raise ComposedCatalogError("manifest contract hash correlation failed")
    packs = manifest["packs"]
    composition_packs = composition.get("packs")
    if not isinstance(packs, dict) or not isinstance(composition_packs, dict):
        raise ComposedCatalogError("composed pack references are invalid")
    for kind in ("worldpack", "renderpack", "assetpack"):
        manifest_reference = packs.get(kind)
        composition_reference = composition_packs.get(kind)
        if (manifest_reference is None) != (composition_reference is None):
            raise ComposedCatalogError(f"composition and manifest disagree on {kind}")
        if manifest_reference is None:
            continue
        if (
            not isinstance(manifest_reference, dict)
            or not isinstance(composition_reference, dict)
            or composition_reference.get("path") != f"packs/{kind}/{kind}.json"
            or composition_reference.get("content_hash") != manifest_reference.get("content_hash")
        ):
            raise ComposedCatalogError(f"composition and manifest {kind} references disagree")
    expected_pack_hashes = {
        kind: reference["content_hash"]
        for kind, reference in sorted(packs.items())
        if isinstance(reference, dict)
    }
    evidence = manifest["compatibility_evidence"]
    target = manifest["compatibility_target"]
    if (
        not isinstance(evidence, dict)
        or not isinstance(target, dict)
        or report.get("content_hash") != evidence.get("content_hash")
        or report.get("composition_hash") != composition.get("content_hash")
        or report.get("world_content_hash") != composition.get("world_content_hash")
        or report.get("profile_hash") != profile.get("content_hash")
        or report.get("capability_catalog_hash") != capability_catalog.get("content_hash")
        or report.get("adapter_hash") != adapter.get("content_hash")
        or report.get("pack_hashes") != expected_pack_hashes
        or report.get("platform") != target.get("platform")
    ):
        raise ComposedCatalogError(
            "compatibility report identity disagrees with bundled contracts and manifest"
        )
    world_ref = packs.get("worldpack")
    if not isinstance(world_ref, dict):
        raise ComposedCatalogError("composed worldpack reference is missing")
    worldpack_path = _safe_file(verified_root, "packs/worldpack/worldpack.json")
    try:
        worldpack = load_worldpack(worldpack_path)
    except (OSError, WorldPackError) as exc:
        raise ComposedCatalogError(f"composed worldpack is invalid: {exc}") from exc
    if (
        worldpack.world_id != release.world_id
        or worldpack.content_hash != release.world_content_hash
        or world_ref.get("content_hash") != worldpack.content_hash
        or not isinstance(composition_packs.get("worldpack"), dict)
        or composition_packs["worldpack"].get("content_hash") != worldpack.content_hash
    ):
        raise ComposedCatalogError("composed worldpack identity correlation failed")
    binding_evidence: list[PackSlotBinding] = []
    renderpack_path: Path | None = None
    if packs.get("renderpack") is not None:
        renderpack_path = _safe_file(verified_root, "packs/renderpack/renderpack.json")
        try:
            renderpack = load_renderpack(renderpack_path, worldpack)
        except (OSError, RenderPackError) as exc:
            raise ComposedCatalogError(f"composed renderpack is invalid: {exc}") from exc
        with renderpack:
            render_ref = packs["renderpack"]
            if (
                not isinstance(render_ref, dict)
                or render_ref.get("content_hash") != renderpack.content_hash
                or render_ref.get("world_content_hash") != worldpack.content_hash
                or not isinstance(composition_packs.get("renderpack"), dict)
                or composition_packs["renderpack"].get("content_hash") != renderpack.content_hash
            ):
                raise ComposedCatalogError("composed renderpack correlation failed")
            asset_kinds = {asset.id: asset.kind for asset in renderpack.assets}
            binding_evidence.extend(
                PackSlotBinding(
                    "renderpack",
                    binding.slot,
                    binding.asset_id,
                    asset_kinds[binding.asset_id],
                    None,
                )
                for binding in renderpack.bindings
            )
    assetpack_path: Path | None = None
    if packs.get("assetpack") is not None:
        assetpack_path = _safe_file(verified_root, "packs/assetpack/assetpack.json")
        try:
            assetpack = load_assetpack(assetpack_path, worldpack)
            with assetpack:
                asset_ref = packs["assetpack"]
                if (
                    not isinstance(asset_ref, dict)
                    or asset_ref.get("content_hash") != assetpack.content_hash
                    or asset_ref.get("world_content_hash") != worldpack.content_hash
                    or not isinstance(composition_packs.get("assetpack"), dict)
                    or composition_packs["assetpack"].get("content_hash") != assetpack.content_hash
                ):
                    raise ComposedCatalogError("composed assetpack correlation failed")
                asset_by_id = {asset["id"]: asset for asset in assetpack.document["assets"]}
                binding_evidence.extend(
                    PackSlotBinding(
                        "assetpack",
                        binding["slot"],
                        binding["asset_id"],
                        asset_by_id[binding["asset_id"]]["kind"],
                        binding["representation"],
                    )
                    for binding in assetpack.document["bindings"]
                )
        except (OSError, AssetPackError) as exc:
            raise ComposedCatalogError(f"composed assetpack is invalid: {exc}") from exc
    layers = profile.get("layers")
    owners = composition.get("slot_owners")
    if not isinstance(layers, list) or not isinstance(owners, list):
        raise ComposedCatalogError("composition has no deterministic presentation plan")
    try:
        validate_composition_slot_ownership(layers, owners, binding_evidence)
        plan = build_composition_plan(layers, owners)
    except CompositionPlanError as exc:
        raise ComposedCatalogError(f"composed slot ownership is invalid: {exc}") from exc
    target_platform = target.get("platform") if isinstance(target, dict) else None
    target_runtime_api = target.get("runtime_api_version") if isinstance(target, dict) else None
    adapter_key = (release.adapter_id, release.adapter_version, release.adapter_hash)
    profile_key = (release.profile_id, release.profile_hash)
    fresh_native_contract = (
        profile_key == BUILTIN_2_5D_PROFILE
        and capability_catalog.get("content_hash") == BUILTIN_CAPABILITY_CATALOG_HASH
        and adapter.get("state") == "verified"
        and adapter.get("budgets") == BUILTIN_2_5D_BUDGETS
        and required_ids == set(worldpack.runtime_requirements.required_features) | profile_ids
        and required_ids <= adapter_ids
        and required_ids <= catalog_ids
        and profile.get("required_packs") == ["renderpack"]
        and target_runtime_api == RUNTIME_API_VERSION
    )
    native_compatible = (
        adapter_key == BUILTIN_NATIVE_ADAPTER
        and fresh_native_contract
        and profile.get("mode") == "2_5d"
        and renderpack_path is not None
        and target_platform == "linux_x86_64"
        and _host_is_linux_x86_64()
    )
    return VerifiedComposedBundle(
        snapshot_owner,
        release,
        root,
        worldpack_path,
        renderpack_path,
        assetpack_path,
        manifest,
        composition,
        profile,
        adapter,
        plan,
        native_compatible,
    )


def validate_cross_catalog_world_hashes(
    legacy: tuple[Any, ...],
    composed: tuple[ComposedCatalogRelease, ...],
) -> None:
    """Enforce one world hash for each world/release across catalog formats."""

    composed_hashes = _world_hashes(
        composed,
        context="composed catalog",
    )
    if not composed_hashes:
        return
    known: dict[tuple[str, str], str] = {}
    for item in legacy:
        if isinstance(item, Mapping):
            key = (str(item.get("world_id")), str(item.get("release_id")))
            digest = str(item.get("worldpack_hash"))
        else:
            key = (str(item.world_id), str(item.release_id))
            digest = str(item.worldpack_hash)
        previous = known.setdefault(key, digest)
        if previous != digest:
            raise ComposedCatalogError(
                "legacy catalog maps one world/release to multiple world content hashes"
            )
    for key, digest in composed_hashes.items():
        previous = known.setdefault(key, digest)
        if previous != digest:
            raise ComposedCatalogError(
                "legacy and composed catalogs disagree on world content identity"
            )


__all__ = [
    "BUILTIN_NATIVE_ADAPTER",
    "CATALOG_GENERATION_FORMAT",
    "CATALOG_GENERATION_NAME",
    "CATALOG_GENERATION_STAGE_PREFIX",
    "CATALOG_GENERATIONS_RELATIVE_PATH",
    "CATALOG_RELATIVE_PATH",
    "ComposedCatalogError",
    "ComposedCatalogRelease",
    "ComposedCatalogState",
    "VerifiedComposedBundle",
    "load_composed_catalog",
    "load_composed_catalog_state",
    "select_composed_release",
    "validate_cross_catalog_world_hashes",
    "verify_composed_release",
]
