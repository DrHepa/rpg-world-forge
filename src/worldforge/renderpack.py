from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from isoworld.content.loader import load_worldpack
from isoworld.content.renderpack import RenderPackError, load_renderpack
from worldforge.assets import (
    AssetManifestError,
    _read_json,
    _resolve_inside,
    validate_asset_manifest,
)
from worldforge.integrity import canonical_payload_hash


class RenderPackBuildError(ValueError):
    """Raised when approved assets cannot become a runtime renderpack."""


def build_renderpack(
    manifest_path: str | Path,
    worldpack_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    manifest_file = Path(manifest_path)
    worldpack_file = Path(worldpack_path)
    issues = validate_asset_manifest(
        manifest_file,
        profile="release",
        worldpack_path=worldpack_file,
    )
    if issues:
        raise RenderPackBuildError("; ".join(str(issue) for issue in issues))
    try:
        manifest = _read_json(manifest_file)
    except AssetManifestError as exc:
        raise RenderPackBuildError(str(exc)) from exc
    if manifest.get("format_version") != 2:
        raise RenderPackBuildError("Building a renderpack requires asset manifest version 2")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    source_root = manifest_file.parent.resolve()
    runtime_root = output.parent.resolve()
    compiled_assets: list[dict[str, Any]] = []
    for asset in sorted(manifest["assets"], key=lambda item: item["id"]):
        files: list[dict[str, Any]] = []
        for index, item in enumerate(asset["outputs"]):
            source = _resolve_inside(source_root, item["runtime_file"])
            if source is None or not source.is_file():
                raise RenderPackBuildError(f"Processed output disappeared: {item['runtime_file']}")
            relative = Path("runtime-assets") / asset["id"] / f"{index:02d}_{source.name}"
            destination = runtime_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            files.append(
                {
                    "role": item["role"],
                    "path": relative.as_posix(),
                    "sha256": item["sha256"],
                    "media_type": item["media_type"],
                }
            )
        compiled_assets.append({"id": asset["id"], "kind": asset["kind"], "files": files})

    payload: dict[str, Any] = {
        "format": "isoworld.renderpack",
        "format_version": 1,
        "world_id": manifest["world_id"],
        "world_content_hash": manifest["world_content_hash"],
        "assets": compiled_assets,
        "bindings": sorted(manifest["bindings"], key=lambda item: item["slot"]),
    }
    payload["content_hash"] = canonical_payload_hash(payload)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        load_renderpack(output, load_worldpack(worldpack_file))
    except RenderPackError as exc:
        raise RenderPackBuildError(f"Compiled renderpack failed runtime validation: {exc}") from exc
    return payload
