from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worldforge.validation import ID_PATTERN, PLACEHOLDER_PATTERN

ASSET_KINDS = {
    "font",
    "music",
    "portrait",
    "shader",
    "sfx",
    "sprite",
    "spritesheet",
    "tileset",
    "ui",
    "vfx",
}
ASSET_STATUSES = {"planned", "generated", "approved", "processed"}
ASSET_ORIGINS = {
    "codex_assisted",
    "gpt_image",
    "human",
    "local_model",
    "procedural",
    "third_party",
}
AI_ORIGINS = {"codex_assisted", "gpt_image", "local_model"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class AssetIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class AssetManifestError(ValueError):
    """Raised when an asset manifest cannot be read or initialized."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetManifestError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AssetManifestError(f"{path} must contain a JSON object")
    return value


def _resolve_inside(root: Path, relative: str) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def _walk_strings(value: Any, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}/{index}")


def init_asset_manifest(worldpack_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    pack_path = Path(worldpack_path)
    pack = _read_json(pack_path)
    if pack.get("format") != "isoworld.worldpack" or pack.get("format_version") not in {1, 2, 3}:
        raise AssetManifestError("The input file is not a compatible worldpack")
    content_hash = pack.get("content_hash")
    if not isinstance(content_hash, str) or not SHA256_PATTERN.fullmatch(content_hash):
        raise AssetManifestError("The worldpack does not contain a valid hash")

    output = Path(output_path)
    if output.exists():
        raise AssetManifestError(f"The asset manifest already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    for directory in ("specs", "generated", "processed", "references", "recipes"):
        (output.parent / directory).mkdir(exist_ok=True)

    manifest: dict[str, Any] = {
        "format": "rpg-world-forge.asset_manifest",
        "format_version": 1,
        "world_id": pack["world"]["id"],
        "world_content_hash": content_hash,
        "phase": "art_direction",
        "assets": [],
    }
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def validate_asset_manifest(
    manifest_path: str | Path,
    *,
    profile: str = "draft",
    worldpack_path: str | Path | None = None,
) -> list[AssetIssue]:
    if profile not in {"draft", "release"}:
        raise AssetManifestError("profile must be draft or release")
    path = Path(manifest_path)
    raw = _read_json(path)
    root = path.parent.resolve()
    issues: list[AssetIssue] = []

    if raw.get("format") != "rpg-world-forge.asset_manifest":
        issues.append(AssetIssue("format", "unknown format"))
    if raw.get("format_version") != 1:
        issues.append(AssetIssue("format_version", "unsupported version"))
    if not isinstance(raw.get("world_id"), str) or not ID_PATTERN.fullmatch(raw["world_id"]):
        issues.append(AssetIssue("world_id", "invalid world ID"))
    content_hash = raw.get("world_content_hash")
    if not isinstance(content_hash, str) or not SHA256_PATTERN.fullmatch(content_hash):
        issues.append(AssetIssue("world_content_hash", "invalid SHA-256 hash"))

    if worldpack_path is not None:
        pack = _read_json(Path(worldpack_path))
        if pack.get("world", {}).get("id") != raw.get("world_id"):
            issues.append(AssetIssue("world_id", "does not match the worldpack"))
        if pack.get("content_hash") != content_hash:
            issues.append(
                AssetIssue(
                    "world_content_hash",
                    "canon changed; restart or migrate the asset plan",
                )
            )

    assets = raw.get("assets")
    if not isinstance(assets, list):
        return issues + [AssetIssue("assets", "must be a list")]
    if profile == "release" and not assets:
        issues.append(AssetIssue("assets", "a release must contain assets"))

    seen: set[str] = set()
    for index, asset in enumerate(assets):
        item_path = f"assets/{index}"
        if not isinstance(asset, dict):
            issues.append(AssetIssue(item_path, "must be an object"))
            continue
        asset_id = asset.get("id")
        if not isinstance(asset_id, str) or not ID_PATTERN.fullmatch(asset_id):
            issues.append(AssetIssue(f"{item_path}/id", "invalid ID"))
        elif asset_id in seen:
            issues.append(AssetIssue(f"{item_path}/id", f"duplicate ID: {asset_id}"))
        else:
            seen.add(asset_id)
        if asset.get("kind") not in ASSET_KINDS:
            issues.append(AssetIssue(f"{item_path}/kind", "unknown asset kind"))
        status = asset.get("status")
        if status not in ASSET_STATUSES:
            issues.append(AssetIssue(f"{item_path}/status", "unknown status"))
        if profile == "release" and status != "processed":
            issues.append(AssetIssue(f"{item_path}/status", "release requires processed status"))
        specification_file = asset.get("specification_file")
        specification = _resolve_inside(root, specification_file)
        if specification is None:
            issues.append(AssetIssue(f"{item_path}/specification_file", "unsafe or missing path"))
        elif not specification.is_file():
            issues.append(AssetIssue(f"{item_path}/specification_file", "file does not exist"))

        provenance = asset.get("provenance")
        if status in {"generated", "approved", "processed"} or profile == "release":
            if not isinstance(provenance, dict):
                issues.append(AssetIssue(f"{item_path}/provenance", "provenance is required"))
                provenance = {}
            origin = provenance.get("origin")
            if origin not in ASSET_ORIGINS:
                issues.append(AssetIssue(f"{item_path}/provenance/origin", "unknown origin"))
            if origin in AI_ORIGINS:
                for field in ("model_id", "model_version", "recipe_file"):
                    if not provenance.get(field):
                        issues.append(
                            AssetIssue(
                                f"{item_path}/provenance/{field}",
                                "required for assisted generation",
                            )
                        )
                recipe = _resolve_inside(root, provenance.get("recipe_file"))
                if recipe is None:
                    issues.append(
                        AssetIssue(
                            f"{item_path}/provenance/recipe_file",
                            "unsafe or missing path",
                        )
                    )
                elif not recipe.is_file():
                    issues.append(
                        AssetIssue(f"{item_path}/provenance/recipe_file", "file does not exist")
                    )

        if status in {"approved", "processed"} or profile == "release":
            license_data = asset.get("license")
            if not isinstance(license_data, dict):
                issues.append(AssetIssue(f"{item_path}/license", "license record is required"))
            else:
                for field in (
                    "asset_license",
                    "source_license",
                    "model_license",
                    "weights_license",
                    "dataset_license",
                ):
                    if not license_data.get(field):
                        issues.append(
                            AssetIssue(f"{item_path}/license/{field}", "value is required")
                        )
            if not asset.get("approved_by"):
                issues.append(AssetIssue(f"{item_path}/approved_by", "human approval is required"))

        if status == "processed" or profile == "release":
            runtime_file = _resolve_inside(root, asset.get("runtime_file"))
            if runtime_file is None or not runtime_file.is_file():
                issues.append(AssetIssue(f"{item_path}/runtime_file", "processed file is missing"))
            expected_hash = asset.get("sha256")
            if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(expected_hash):
                issues.append(AssetIssue(f"{item_path}/sha256", "invalid SHA-256 hash"))
            elif runtime_file is not None and runtime_file.is_file():
                actual = hashlib.sha256(runtime_file.read_bytes()).hexdigest()
                if actual != expected_hash:
                    issues.append(AssetIssue(f"{item_path}/sha256", "does not match the file"))

        for value_path, value in _walk_strings(asset, item_path):
            if PLACEHOLDER_PATTERN.search(value):
                issues.append(AssetIssue(value_path, "unresolved placeholder"))

    return issues
