from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from isoworld.content.asset_contracts import (
    ASSET_KINDS,
    ASSET_RUNTIME_OUTPUT_CONTRACTS,  # noqa: F401
    AUDIO_ASSET_KINDS,
    GLB_OUTPUT_ROLES,  # noqa: F401
    KIND_REPRESENTATIONS,
    OUTPUT_ROLE_MEDIA,
    REPRESENTATIONS,
    THREE_D_ASSET_KINDS,  # noqa: F401
    TWO_D_ASSET_KINDS,  # noqa: F401
    AssetRuntimeOutputContract,  # noqa: F401
    runtime_output_contract_issue,
)
from worldforge.asset_io import (
    AssetContractError,
    read_json_object,
    require_content_hash,
    verify_artifact_reference,
)
from worldforge.validation import ID_PATTERN, PLACEHOLDER_PATTERN

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SEMANTIC_SLOT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z][a-z0-9_]*){1,2}$")
ROUTES = {"openai", "modly"}
EXECUTORS = {"openai_image", "blender_mcp", "modly_cli_mcp", "human", "procedural"}


PRODUCTION_OPERATIONS = {
    "image_generate",
    "image_edit",
    "concept_reference",
    "model_from_reference",
    "retopology",
    "uv_unwrap",
    "material_bake",
    "rig",
    "animate",
    "collision",
    "export_glb",
    "refine",
    "capability_execute",
    "workflow_run",
    "process_run",
}
_FORBIDDEN_KEY_PARTS = {
    "access_token",
    "api_key",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "credential",
    "password",
    "private_key",
    "raw_transcript",
    "secret",
    "signed_url",
    "provider_raw_transcript",
    "provider_response",
    "mcp_transcript",
    "inline_code",
    "python_code",
    "script_body",
    "shell_command",
}
_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{12,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
)


@dataclass(frozen=True, slots=True)
class ContractIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _issue(path: str, message: str) -> ContractIssue:
    return ContractIssue(path, message)


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None


def _exact_fields(
    raw: dict[str, Any], expected: set[str] | frozenset[str], *, path: str
) -> list[ContractIssue]:
    missing = sorted(set(expected) - set(raw))
    unknown = sorted(set(raw) - set(expected))
    issues: list[ContractIssue] = []
    if missing:
        issues.append(_issue(path, f"missing fields: {', '.join(missing)}"))
    if unknown:
        issues.append(_issue(path, f"unknown fields: {', '.join(unknown)}"))
    return issues


def _required_text(
    raw: dict[str, Any], fields: Iterable[str], *, base: str = ""
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    for field in fields:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            issues.append(_issue(f"{base}{field}", "non-empty text is required"))
    return issues


def _sorted_unique_strings(value: object, *, required: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not required)
        and all(isinstance(item, str) and item for item in value)
        and value == sorted(set(value))
    )


def _scan_sensitive(value: Any, path: str = "") -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
            if normalized in _FORBIDDEN_KEY_PARTS or any(
                part in normalized
                for part in (
                    "api_key",
                    "authorization",
                    "bearer",
                    "cookie",
                    "credential",
                    "password",
                    "private_key",
                    "raw_transcript",
                    "secret",
                    "signed_url",
                    "token",
                )
            ):
                issues.append(
                    _issue(f"{path}/{key}".lstrip("/"), "credential-like field is forbidden")
                )
            issues.extend(_scan_sensitive(child, f"{path}/{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issues.extend(_scan_sensitive(child, f"{path}/{index}"))
    elif isinstance(value, str):
        if _URL_PATTERN.search(value):
            issues.append(_issue(path.lstrip("/"), "URLs are forbidden in sanitized contracts"))
        if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            issues.append(_issue(path.lstrip("/"), "credential-like value is forbidden"))
        if PLACEHOLDER_PATTERN.search(value):
            issues.append(_issue(path.lstrip("/"), "unresolved placeholder"))
    return issues


def _base_contract_issues(
    raw: dict[str, Any],
    *,
    expected_format: str,
    version: int = 1,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    if raw.get("format") != expected_format:
        issues.append(_issue("format", f"expected {expected_format}"))
    if raw.get("format_version") != version:
        issues.append(_issue("format_version", f"expected version {version}"))
    try:
        require_content_hash(raw, context=expected_format)
    except AssetContractError as exc:
        issues.append(_issue("content_hash", str(exc)))
    return issues


def validate_asset_target(path: str | Path) -> list[ContractIssue]:
    try:
        raw = read_json_object(path)
    except AssetContractError as exc:
        return [_issue("target", str(exc))]
    issues = _base_contract_issues(raw, expected_format="rpg-world-forge.asset_target")
    issues.extend(
        _exact_fields(
            raw,
            {
                "format",
                "format_version",
                "id",
                "world_id",
                "world_content_hash",
                "dimension",
                "delivery_profile",
                "runtime_adapter",
                "coordinate_system",
                "content_hash",
            },
            path="target",
        )
    )
    issues.extend(
        _required_text(
            raw, ("id", "world_id", "world_content_hash", "dimension", "delivery_profile")
        )
    )
    dimension = raw.get("dimension")
    delivery = raw.get("delivery_profile")
    if not isinstance(dimension, str) or dimension not in {"2d", "2_5d", "3d"}:
        issues.append(_issue("dimension", "must be 2d, 2_5d, or 3d"))
    if not isinstance(delivery, str) or delivery not in {"renderpack_v1", "assetpack_v1"}:
        issues.append(_issue("delivery_profile", "unknown delivery profile"))
    if dimension == "3d" and delivery != "assetpack_v1":
        issues.append(_issue("delivery_profile", "3d targets require assetpack_v1"))
    if isinstance(dimension, str) and dimension in {"2d", "2_5d"} and delivery != "renderpack_v1":
        issues.append(_issue("delivery_profile", "2d targets require renderpack_v1"))
    for field in ("id", "world_id"):
        if not isinstance(raw.get(field), str) or ID_PATTERN.fullmatch(raw[field]) is None:
            issues.append(_issue(field, "invalid portable ID"))
    if not _valid_hash(raw.get("world_content_hash")):
        issues.append(_issue("world_content_hash", "invalid SHA-256"))
    coordinates = raw.get("coordinate_system")
    if not isinstance(coordinates, dict):
        issues.append(_issue("coordinate_system", "must be an object"))
    elif dimension == "3d":
        if coordinates != {
            "handedness": "right",
            "up_axis": "Y",
            "forward_axis": "-Z",
            "units_per_meter": 1.0,
        }:
            issues.append(
                _issue(
                    "coordinate_system",
                    "3d handoff requires right-handed Y-up, -Z-forward meter units",
                )
            )
        if raw.get("runtime_adapter") is not None:
            issues.append(
                _issue("runtime_adapter", "3d handoff is engine-neutral and must be null")
            )
    elif dimension == "2d":
        if coordinates != {
            "origin": "top_left",
            "x_axis": "right",
            "y_axis": "down",
            "pixels_per_unit": 32,
        }:
            issues.append(_issue("coordinate_system", "does not match the 2d target contract"))
        if raw.get("runtime_adapter") != "isoworld_raylib_2_5d":
            issues.append(_issue("runtime_adapter", "2d target requires isoworld_raylib_2_5d"))
    elif dimension == "2_5d":
        if coordinates != {
            "origin": "tile_anchor",
            "x_axis": "east",
            "y_axis": "south",
            "up_axis": "screen_up",
            "tile_width_pixels": 64,
            "tile_height_pixels": 32,
        }:
            issues.append(_issue("coordinate_system", "does not match the 2.5d target contract"))
        if raw.get("runtime_adapter") != "isoworld_raylib_2_5d":
            issues.append(_issue("runtime_adapter", "2.5d target requires isoworld_raylib_2_5d"))
    issues.extend(_scan_sensitive(raw))
    return issues


def _validate_bible(path: str | Path, *, kind: str) -> list[ContractIssue]:
    expected = f"rpg-world-forge.{kind}_bible"
    try:
        raw = read_json_object(path)
    except AssetContractError as exc:
        return [_issue(kind, str(exc))]
    issues = _base_contract_issues(raw, expected_format=expected)
    common_fields = {
        "format",
        "format_version",
        "world_id",
        "world_content_hash",
        "target_id",
        "target_hash",
        "acceptance_tests",
        "approved_by",
        "content_hash",
    }
    decision_fields = (
        {"camera", "resolution", "style", "silhouettes", "animation", "ui", "vfx"}
        if kind == "visual"
        else {"format_policy", "mix", "timbral_families", "ambience", "music", "sfx"}
    )
    issues.extend(_exact_fields(raw, common_fields | decision_fields, path=kind))
    issues.extend(
        _required_text(
            raw,
            ("world_id", "world_content_hash", "target_id", "target_hash", "approved_by"),
        )
    )
    for field in ("world_id", "target_id"):
        value = raw.get(field)
        if not isinstance(value, str) or ID_PATTERN.fullmatch(value) is None:
            issues.append(_issue(field, "invalid portable ID"))
    if not _valid_hash(raw.get("world_content_hash")):
        issues.append(_issue("world_content_hash", "invalid SHA-256"))
    if not _valid_hash(raw.get("target_hash")):
        issues.append(_issue("target_hash", "invalid SHA-256"))
    tests = raw.get("acceptance_tests")
    if not _sorted_unique_strings(tests, required=True):
        issues.append(_issue("acceptance_tests", "must be a non-empty sorted unique string list"))
    if kind == "visual":
        for field in ("camera", "resolution", "style", "silhouettes", "animation", "ui", "vfx"):
            if not isinstance(raw.get(field), dict) or not raw[field]:
                issues.append(_issue(field, "an applicable visual decision object is required"))
    else:
        for field in ("format_policy", "mix", "timbral_families", "ambience", "music", "sfx"):
            if not isinstance(raw.get(field), (dict, list)) or not raw[field]:
                issues.append(_issue(field, "an applicable audio decision is required"))
    issues.extend(_scan_sensitive(raw))
    return issues


def validate_asset_bibles(
    visual_path: str | Path,
    audio_path: str | Path,
    target_path: str | Path,
) -> list[ContractIssue]:
    issues = [*validate_asset_target(target_path)]
    target: dict[str, Any] | None = None
    if not issues:
        target = read_json_object(target_path)
    for label, path in (("visual", visual_path), ("audio", audio_path)):
        current = _validate_bible(path, kind=label)
        issues.extend(current)
        if not current and target is not None:
            bible = read_json_object(path)
            if bible.get("world_id") != target.get("world_id"):
                issues.append(_issue(label, "world_id does not match the target"))
            if bible.get("world_content_hash") != target.get("world_content_hash"):
                issues.append(_issue(label, "world content hash does not match the target"))
            if bible.get("target_id") != target.get("id"):
                issues.append(_issue(label, "target_id does not match the target"))
            if bible.get("target_hash") != target.get("content_hash"):
                issues.append(_issue(label, "target hash does not match the target"))
    return issues


def validate_asset_spec(
    path: str | Path,
    *,
    expected_id: str | None = None,
    expected_kind: str | None = None,
    target_hash: str | None = None,
) -> list[ContractIssue]:
    try:
        raw = read_json_object(path)
    except AssetContractError as exc:
        return [_issue("specification", str(exc))]
    if raw.get("format") != "rpg-world-forge.asset_spec":
        return [_issue("format", "unknown asset specification format")]
    version = raw.get("format_version")
    if version == 1:
        issues = _required_text(raw, ("id", "kind", "purpose"))
        if expected_id is not None and raw.get("id") != expected_id:
            issues.append(_issue("id", "does not match the asset"))
        if expected_kind is not None and raw.get("kind") != expected_kind:
            issues.append(_issue("kind", "does not match the asset"))
        return issues
    if version != 2:
        return [_issue("format_version", "unsupported asset specification version")]
    issues = _base_contract_issues(
        raw,
        expected_format="rpg-world-forge.asset_spec",
        version=2,
    )
    issues.extend(
        _exact_fields(
            raw,
            {
                "format",
                "format_version",
                "id",
                "kind",
                "representation",
                "target_id",
                "target_hash",
                "inventory_hash",
                "visual_bible_hash",
                "audio_bible_hash",
                "purpose",
                "canonical_sources",
                "acceptance_criteria",
                "semantic_slots",
                "technical",
                "production",
                "expected_outputs",
                "content_hash",
            },
            path="specification",
        )
    )
    issues.extend(
        _required_text(
            raw,
            (
                "id",
                "kind",
                "representation",
                "target_id",
                "target_hash",
                "inventory_hash",
                "visual_bible_hash",
                "audio_bible_hash",
                "purpose",
            ),
        )
    )
    for field in ("id", "target_id"):
        value = raw.get(field)
        if not isinstance(value, str) or ID_PATTERN.fullmatch(value) is None:
            issues.append(_issue(field, "invalid portable ID"))
    for field in ("target_hash", "inventory_hash", "visual_bible_hash", "audio_bible_hash"):
        if not _valid_hash(raw.get(field)):
            issues.append(_issue(field, "invalid SHA-256"))
    if expected_id is not None and raw.get("id") != expected_id:
        issues.append(_issue("id", "does not match the asset"))
    if expected_kind is not None and raw.get("kind") != expected_kind:
        issues.append(_issue("kind", "does not match the asset"))
    if target_hash is not None and raw.get("target_hash") != target_hash:
        issues.append(_issue("target_hash", "does not match the asset target"))
    representation = raw.get("representation")
    if not isinstance(representation, str) or representation not in REPRESENTATIONS:
        issues.append(_issue("representation", "unknown representation"))
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in ASSET_KINDS:
        issues.append(_issue("kind", "unknown asset kind"))
    elif isinstance(representation, str) and representation not in KIND_REPRESENTATIONS[kind]:
        allowed = ", ".join(sorted(KIND_REPRESENTATIONS[kind]))
        issues.append(_issue("representation", f"{kind} assets require {allowed} representation"))
    for field in ("canonical_sources", "acceptance_criteria", "semantic_slots"):
        if not _sorted_unique_strings(raw.get(field), required=field != "semantic_slots"):
            issues.append(_issue(field, "must be a sorted unique string list"))
    acceptance_criteria = raw.get("acceptance_criteria")
    if isinstance(acceptance_criteria, list):
        for index, criterion in enumerate(acceptance_criteria):
            if not isinstance(criterion, str) or ID_PATTERN.fullmatch(criterion) is None:
                issues.append(
                    _issue(
                        f"acceptance_criteria/{index}",
                        "must be a portable QA check ID",
                    )
                )
    semantic_slots = raw.get("semantic_slots")
    if isinstance(semantic_slots, list):
        for index, slot in enumerate(semantic_slots):
            if not isinstance(slot, str) or SEMANTIC_SLOT_PATTERN.fullmatch(slot) is None:
                issues.append(_issue(f"semantic_slots/{index}", "invalid semantic slot"))
    technical = raw.get("technical")
    if not isinstance(technical, dict):
        issues.append(_issue("technical", "must be an object"))
    else:
        kind = raw.get("kind")
        if (
            isinstance(representation, str)
            and representation in {"2d", "2_5d"}
            and isinstance(kind, str)
            and kind
            in {
                "font",
                "shader",
            }
        ):
            allowed_technical = {"runtime_format", "memory_budget_bytes"}
        elif isinstance(representation, str) and representation in {"2d", "2_5d"}:
            allowed_technical = {
                "runtime_format",
                "memory_budget_bytes",
                "width",
                "height",
                "alpha_mode",
                "pivot",
                "directions",
                "actions",
                "frame_layout",
                "palette",
                "cell_layout",
                "frames",
                "fps",
                "padding",
            }
        elif representation == "audio":
            allowed_technical = {
                "runtime_format",
                "memory_budget_bytes",
                "sample_rate",
                "channels",
                "duration_seconds_max",
                "loop",
                "integrated_lufs",
                "peak",
                "event",
                "variants",
                "priority",
                "max_distance",
                "cooldown_seconds",
            }
        elif representation == "3d":
            allowed_technical = {
                "runtime_format",
                "memory_budget_bytes",
                "physical_dimensions_m",
                "budgets",
                "lod_count",
                "collision_policy",
                "required_animations",
            }
        else:
            allowed_technical = set(technical)
        unknown_technical = sorted(set(technical) - allowed_technical)
        if unknown_technical:
            issues.append(_issue("technical", f"unknown fields: {', '.join(unknown_technical)}"))
        budget = technical.get("memory_budget_bytes")
        if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
            issues.append(_issue("technical/memory_budget_bytes", "must be a positive integer"))
        if (
            isinstance(representation, str)
            and representation in {"2d", "2_5d"}
            and isinstance(kind, str)
            and kind
            in {
                "portrait",
                "sprite",
                "spritesheet",
                "tileset",
                "ui",
                "vfx",
            }
        ):
            for field in ("width", "height"):
                value = technical.get(field)
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    issues.append(_issue(f"technical/{field}", "must be a positive integer"))
            if technical.get("runtime_format") != "png":
                issues.append(_issue("technical/runtime_format", "2d runtime output must be png"))
            if not isinstance(technical.get("alpha_mode"), str) or technical.get(
                "alpha_mode"
            ) not in {"opaque", "mask", "blend"}:
                issues.append(_issue("technical/alpha_mode", "must be opaque, mask, or blend"))
            pivot = technical.get("pivot")
            if pivot is not None and (
                not isinstance(pivot, list)
                or len(pivot) != 2
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not -32768 <= value <= 32768
                    for value in pivot
                )
            ):
                issues.append(_issue("technical/pivot", "must contain two bounded integers"))
            directions = technical.get("directions")
            if directions is not None and (
                isinstance(directions, bool)
                or not isinstance(directions, int)
                or not 1 <= directions <= 64
            ):
                issues.append(_issue("technical/directions", "must be in 1..64"))
            actions = technical.get("actions")
            if actions is not None and not _sorted_unique_strings(actions):
                issues.append(_issue("technical/actions", "must be a sorted unique string list"))
            elif isinstance(actions, list) and any(
                ID_PATTERN.fullmatch(action) is None for action in actions
            ):
                issues.append(_issue("technical/actions", "must contain portable IDs"))
            frame_layout = technical.get("frame_layout")
            if frame_layout is not None and (
                not isinstance(frame_layout, str) or not frame_layout or len(frame_layout) > 2048
            ):
                issues.append(_issue("technical/frame_layout", "must be non-empty bounded text"))
            cell_layout = technical.get("cell_layout")
            if cell_layout is not None and (
                not isinstance(cell_layout, str) or not cell_layout or len(cell_layout) > 2048
            ):
                issues.append(_issue("technical/cell_layout", "must be non-empty bounded text"))
            palette = technical.get("palette")
            if palette is not None and (
                not _sorted_unique_strings(palette)
                or any(
                    re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", color) is None
                    for color in palette
                )
            ):
                issues.append(
                    _issue("technical/palette", "must contain sorted unique RGB/RGBA colors")
                )
            for field, maximum in (("frames", 65536), ("padding", 4096)):
                value = technical.get(field)
                minimum = 1 if field == "frames" else 0
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not minimum <= value <= maximum
                ):
                    issues.append(_issue(f"technical/{field}", f"must be in {minimum}..{maximum}"))
            fps = technical.get("fps")
            if fps is not None and (
                isinstance(fps, bool)
                or not isinstance(fps, (int, float))
                or not math.isfinite(fps)
                or not 0 < fps <= 240
            ):
                issues.append(_issue("technical/fps", "must be in (0, 240]"))
        elif (
            isinstance(representation, str)
            and representation in {"2d", "2_5d"}
            and isinstance(kind, str)
            and kind in {"font", "shader"}
        ):
            formats = {"font": {"otf", "ttf"}, "shader": {"glsl"}}
            runtime_format = technical.get("runtime_format")
            if not isinstance(runtime_format, str) or runtime_format not in formats[kind]:
                issues.append(_issue("technical/runtime_format", f"is invalid for {kind}"))
        elif representation == "audio":
            sample_rate = technical.get("sample_rate")
            channels = technical.get("channels")
            if (
                isinstance(sample_rate, bool)
                or not isinstance(sample_rate, int)
                or not 8000 <= sample_rate <= 192000
            ):
                issues.append(_issue("technical/sample_rate", "must be in 8000..192000"))
            if (
                not isinstance(channels, int)
                or isinstance(channels, bool)
                or channels not in {1, 2}
            ):
                issues.append(_issue("technical/channels", "must be 1 or 2"))
            if technical.get("runtime_format") != "wav":
                issues.append(
                    _issue("technical/runtime_format", "audio runtime output must be wav")
                )
            duration = technical.get("duration_seconds_max")
            if duration is not None and (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or not 0 < duration <= 86400
            ):
                issues.append(_issue("technical/duration_seconds_max", "must be in (0, 86400]"))
            loop = technical.get("loop")
            if loop is not None and not isinstance(loop, bool):
                issues.append(_issue("technical/loop", "must be boolean"))
            loudness = technical.get("integrated_lufs")
            if loudness is not None and (
                isinstance(loudness, bool)
                or not isinstance(loudness, (int, float))
                or not math.isfinite(loudness)
                or not -70 <= loudness <= 0
            ):
                issues.append(_issue("technical/integrated_lufs", "must be in -70..0"))
            peak = technical.get("peak")
            if peak is not None and (
                isinstance(peak, bool) or not isinstance(peak, int) or not 1 <= peak <= 32767
            ):
                issues.append(_issue("technical/peak", "must be in 1..32767"))
            event = technical.get("event")
            if event is not None and (
                not isinstance(event, str) or SEMANTIC_SLOT_PATTERN.fullmatch(event) is None
            ):
                issues.append(_issue("technical/event", "must be a semantic event slot"))
            for field, minimum, maximum in (
                ("variants", 1, 256),
                ("priority", 0, 255),
            ):
                value = technical.get(field)
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not minimum <= value <= maximum
                ):
                    issues.append(_issue(f"technical/{field}", f"must be in {minimum}..{maximum}"))
            for field, minimum, maximum in (
                ("max_distance", 0, 1_000_000),
                ("cooldown_seconds", 0, 86_400),
            ):
                value = technical.get(field)
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not minimum <= value <= maximum
                ):
                    issues.append(_issue(f"technical/{field}", f"must be in {minimum}..{maximum}"))
        elif representation == "3d":
            if technical.get("runtime_format") != "glb":
                issues.append(_issue("technical/runtime_format", "3d runtime output must be glb"))
            dimensions = technical.get("physical_dimensions_m")
            if (
                not isinstance(dimensions, list)
                or len(dimensions) != 3
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
                    for value in dimensions
                )
            ):
                issues.append(
                    _issue(
                        "technical/physical_dimensions_m", "three positive dimensions are required"
                    )
                )
            budgets = technical.get("budgets")
            if not isinstance(budgets, dict):
                issues.append(_issue("technical/budgets", "3d budgets are required"))
            else:
                allowed_budgets = {
                    "max_vertices",
                    "max_triangles",
                    "max_materials",
                    "max_texture_size",
                    "max_bones",
                    "max_influences",
                }
                unknown_budgets = sorted(set(budgets) - allowed_budgets)
                if unknown_budgets:
                    issues.append(
                        _issue(
                            "technical/budgets",
                            f"unknown fields: {', '.join(unknown_budgets)}",
                        )
                    )
                for field in ("max_vertices", "max_triangles", "max_materials", "max_texture_size"):
                    value = budgets.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                        issues.append(
                            _issue(f"technical/budgets/{field}", "must be a positive integer")
                        )
                for field, maximum in (("max_bones", 4096), ("max_influences", 16)):
                    value = budgets.get(field)
                    if value is not None and (
                        isinstance(value, bool)
                        or not isinstance(value, int)
                        or not 0 <= value <= maximum
                    ):
                        issues.append(
                            _issue(
                                f"technical/budgets/{field}",
                                f"must be in 0..{maximum}",
                            )
                        )
            lod_count = technical.get("lod_count")
            if lod_count is not None and (
                isinstance(lod_count, bool)
                or not isinstance(lod_count, int)
                or not 1 <= lod_count <= 16
            ):
                issues.append(_issue("technical/lod_count", "must be in 1..16"))
            collision_policy = technical.get("collision_policy")
            if collision_policy is not None and (
                not isinstance(collision_policy, str)
                or collision_policy
                not in {"none", "primitive", "convex", "triangle_mesh", "separate_glb"}
            ):
                issues.append(_issue("technical/collision_policy", "is invalid"))
            required_animations = technical.get("required_animations")
            if required_animations is not None and not _sorted_unique_strings(required_animations):
                issues.append(
                    _issue(
                        "technical/required_animations",
                        "must be a sorted unique string list",
                    )
                )
            elif isinstance(required_animations, list) and any(
                ID_PATTERN.fullmatch(animation) is None for animation in required_animations
            ):
                issues.append(_issue("technical/required_animations", "must contain portable IDs"))
    production = raw.get("production")
    if not isinstance(production, dict):
        issues.append(_issue("production", "must be an object"))
    else:
        issues.extend(
            _exact_fields(
                production,
                {"allowed_routes", "allowed_executors"},
                path="production",
            )
        )
        routes = production.get("allowed_routes")
        executors = production.get("allowed_executors")
        if not _sorted_unique_strings(routes, required=True) or any(
            route not in ROUTES for route in routes
        ):
            issues.append(
                _issue("production/allowed_routes", "must contain sorted unique known routes")
            )
        if not _sorted_unique_strings(executors, required=True) or any(
            executor not in EXECUTORS for executor in executors
        ):
            issues.append(
                _issue("production/allowed_executors", "must contain sorted unique known executors")
            )
        if (
            isinstance(routes, list)
            and "modly" in routes
            and (not isinstance(executors, list) or "modly_cli_mcp" not in executors)
        ):
            issues.append(_issue("production", "the modly route requires modly_cli_mcp"))
    expected_outputs = raw.get("expected_outputs")
    if not isinstance(expected_outputs, list) or not expected_outputs:
        issues.append(_issue("expected_outputs", "at least one output contract is required"))
    else:
        seen_outputs: set[tuple[str, str]] = set()
        for index, output in enumerate(expected_outputs):
            if not isinstance(output, dict):
                issues.append(_issue(f"expected_outputs/{index}", "must be an object"))
                continue
            issues.extend(
                _exact_fields(
                    output,
                    {"role", "media_type"},
                    path=f"expected_outputs/{index}",
                )
            )
            role = output.get("role")
            media_type = output.get("media_type")
            if not isinstance(role, str) or role not in OUTPUT_ROLE_MEDIA:
                issues.append(_issue(f"expected_outputs/{index}/role", "unknown output role"))
                continue
            if not isinstance(media_type, str) or media_type not in OUTPUT_ROLE_MEDIA[role]:
                issues.append(
                    _issue(
                        f"expected_outputs/{index}/media_type",
                        "is incompatible with the output role",
                    )
                )
            if isinstance(role, str) and isinstance(media_type, str):
                pair = (role, media_type)
                if pair in seen_outputs:
                    issues.append(_issue(f"expected_outputs/{index}", "duplicate output contract"))
                seen_outputs.add(pair)
        if isinstance(kind, str) and kind in ASSET_KINDS:
            roles = [
                output.get("role")
                for output in expected_outputs
                if isinstance(output, dict) and isinstance(output.get("role"), str)
            ]
            media_by_role = {
                output.get("role"): output.get("media_type")
                for output in expected_outputs
                if isinstance(output, dict)
                and isinstance(output.get("role"), str)
                and isinstance(output.get("media_type"), str)
            }
            role_issue = runtime_output_contract_issue(kind, representation, roles)
            if role_issue is not None:
                issues.append(_issue("expected_outputs", role_issue))
            if kind in {"portrait", "sprite", "ui", "vfx"}:
                if media_by_role.get("texture") != "image/png":
                    issues.append(
                        _issue(
                            "expected_outputs",
                            f"{kind} requires texture=image/png",
                        )
                    )
            elif kind in {"spritesheet", "tileset"}:
                if (
                    media_by_role.get("clipset") != "application/json"
                    or media_by_role.get("texture") != "image/png"
                ):
                    issues.append(
                        _issue(
                            "expected_outputs",
                            f"{kind} requires clipset=application/json and texture=image/png",
                        )
                    )
            elif kind in AUDIO_ASSET_KINDS:
                if media_by_role.get("audio") != "audio/wav":
                    issues.append(_issue("expected_outputs", f"{kind} requires audio=audio/wav"))
            elif kind == "font":
                runtime_format = technical.get("runtime_format")
                expected_media = (
                    f"font/{runtime_format}" if runtime_format in {"otf", "ttf"} else None
                )
                if media_by_role.get("font") != expected_media:
                    issues.append(
                        _issue(
                            "expected_outputs",
                            "font output must match its runtime format",
                        )
                    )
    issues.extend(_scan_sensitive(raw))
    return issues


def validate_asset_qa_report(
    path: str | Path,
    *,
    root: str | Path | None = None,
    expected_asset_id: str | None = None,
    expected_target_hash: str | None = None,
    expected_output_hashes: set[str] | None = None,
    expected_checks: set[str] | None = None,
) -> list[ContractIssue]:
    try:
        raw = read_json_object(path)
    except AssetContractError as exc:
        return [_issue("qa", str(exc))]
    issues = _base_contract_issues(raw, expected_format="rpg-world-forge.asset_qa_report")
    issues.extend(
        _exact_fields(
            raw,
            {
                "format",
                "format_version",
                "asset_id",
                "target_hash",
                "output_hashes",
                "checks",
                "blockers",
                "approved_by",
                "content_hash",
            },
            path="qa",
        )
    )
    issues.extend(_required_text(raw, ("asset_id", "target_hash", "approved_by")))
    if not isinstance(raw.get("asset_id"), str) or ID_PATTERN.fullmatch(raw["asset_id"]) is None:
        issues.append(_issue("asset_id", "invalid portable ID"))
    if expected_asset_id is not None and raw.get("asset_id") != expected_asset_id:
        issues.append(_issue("asset_id", "does not match the asset"))
    if expected_target_hash is not None and raw.get("target_hash") != expected_target_hash:
        issues.append(_issue("target_hash", "does not match the asset target"))
    output_hashes = raw.get("output_hashes")
    if not _sorted_unique_strings(output_hashes, required=True) or any(
        not _valid_hash(value) for value in output_hashes
    ):
        issues.append(_issue("output_hashes", "must be sorted unique SHA-256 digests"))
    elif expected_output_hashes is not None and set(output_hashes) != expected_output_hashes:
        issues.append(_issue("output_hashes", "does not match the processed outputs"))
    checks = raw.get("checks")
    if not isinstance(checks, list) or not checks:
        issues.append(_issue("checks", "at least one typed QA check is required"))
    else:
        seen: set[str] = set()
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                issues.append(_issue(f"checks/{index}", "must be an object"))
                continue
            issues.extend(
                _exact_fields(
                    check,
                    {"id", "passed", "evidence"},
                    path=f"checks/{index}",
                )
            )
            check_id = check.get("id")
            if (
                not isinstance(check_id, str)
                or ID_PATTERN.fullmatch(check_id) is None
                or check_id in seen
            ):
                issues.append(_issue(f"checks/{index}/id", "must be a unique non-empty ID"))
            else:
                seen.add(check_id)
            if check.get("passed") is not True:
                issues.append(_issue(f"checks/{index}/passed", "must be true for release"))
            evidence = check.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                issues.append(_issue(f"checks/{index}/evidence", "hash-bound evidence is required"))
            elif root is not None:
                for evidence_index, reference in enumerate(evidence):
                    try:
                        verify_artifact_reference(
                            root,
                            reference,
                            context=f"checks/{index}/evidence/{evidence_index}",
                        )
                    except AssetContractError as exc:
                        issues.append(_issue(f"checks/{index}/evidence/{evidence_index}", str(exc)))
        if expected_checks is not None and not expected_checks <= seen:
            missing = ", ".join(sorted(expected_checks - seen))
            issues.append(_issue("checks", f"missing specification acceptance checks: {missing}"))
    if raw.get("blockers") != []:
        issues.append(_issue("blockers", "must be empty for release"))
    issues.extend(_scan_sensitive(raw))
    return issues


def validate_asset_license_record(
    path: str | Path,
    *,
    root: str | Path | None = None,
    expected_asset_id: str | None = None,
    expected_output_hashes: set[str] | None = None,
) -> list[ContractIssue]:
    try:
        raw = read_json_object(path)
    except AssetContractError as exc:
        return [_issue("license", str(exc))]
    issues = _base_contract_issues(raw, expected_format="rpg-world-forge.asset_license_record")
    issues.extend(
        _exact_fields(
            raw,
            {
                "format",
                "format_version",
                "asset_id",
                "output_hashes",
                "components",
                "notices",
                "approved_by",
                "content_hash",
            },
            path="license",
        )
    )
    issues.extend(_required_text(raw, ("asset_id", "approved_by")))
    if not isinstance(raw.get("asset_id"), str) or ID_PATTERN.fullmatch(raw["asset_id"]) is None:
        issues.append(_issue("asset_id", "invalid portable ID"))
    if expected_asset_id is not None and raw.get("asset_id") != expected_asset_id:
        issues.append(_issue("asset_id", "does not match the asset"))
    hashes = raw.get("output_hashes")
    if not _sorted_unique_strings(hashes, required=True) or any(
        not _valid_hash(value) for value in hashes
    ):
        issues.append(_issue("output_hashes", "must be a non-empty sorted unique SHA-256 list"))
    elif expected_output_hashes is not None and set(hashes) != expected_output_hashes:
        issues.append(_issue("output_hashes", "does not match the processed outputs"))
    components = raw.get("components")
    scopes: set[str] = set()
    if not isinstance(components, list) or not components:
        issues.append(_issue("components", "license components are required"))
    else:
        for index, component in enumerate(components):
            if not isinstance(component, dict):
                issues.append(_issue(f"components/{index}", "must be an object"))
                continue
            issues.extend(
                _exact_fields(
                    component,
                    {"scope", "license_expression", "redistribution", "evidence"},
                    path=f"components/{index}",
                )
            )
            issues.extend(
                _required_text(
                    component,
                    ("scope", "license_expression", "redistribution"),
                    base=f"components/{index}/",
                )
            )
            scope = component.get("scope")
            if isinstance(scope, str) and scope in {
                "asset",
                "dataset",
                "model",
                "output",
                "source",
                "weights",
            }:
                scopes.add(scope)
            else:
                issues.append(_issue(f"components/{index}/scope", "unknown license scope"))
            redistribution = component.get("redistribution")
            if not isinstance(redistribution, str) or redistribution not in {
                "notice_required",
                "permitted",
                "source_required",
            }:
                issues.append(
                    _issue(
                        f"components/{index}/redistribution",
                        "must explicitly permit release redistribution",
                    )
                )
            if root is not None:
                try:
                    verify_artifact_reference(
                        root,
                        component.get("evidence"),
                        context=f"components/{index}/evidence",
                    )
                except AssetContractError as exc:
                    issues.append(_issue(f"components/{index}/evidence", str(exc)))
    required_scopes = {"asset", "source", "model", "weights", "dataset", "output"}
    if not required_scopes <= scopes:
        issues.append(
            _issue(
                "components",
                f"missing license scopes: {', '.join(sorted(required_scopes - scopes))}",
            )
        )
    if root is not None:
        try:
            verify_artifact_reference(root, raw.get("notices"), context="notices")
        except AssetContractError as exc:
            issues.append(_issue("notices", str(exc)))
    issues.extend(_scan_sensitive(raw))
    return issues
