from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SourceProjectError(ValueError):
    """Raised when source files cannot be read safely."""


@dataclass(frozen=True, slots=True)
class SourceProject:
    manifest_path: Path
    world: dict[str, Any]
    collections: dict[str, list[dict[str, Any]]]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceProjectError(f"Could not read {path}: {exc}") from exc


def _resolve_inside(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise SourceProjectError(f"Path escapes the project root: {relative}")
    return target


def load_source_project(manifest_path: str | Path) -> SourceProject:
    manifest = Path(manifest_path).resolve()
    raw = _read_json(manifest)
    if not isinstance(raw, dict):
        raise SourceProjectError("The manifest must be a JSON object")
    if raw.get("format") != "isoworld.source_manifest":
        raise SourceProjectError("Unknown source manifest format")
    if raw.get("format_version") != 1:
        raise SourceProjectError("Unsupported source manifest version")

    root = manifest.parent
    world_path = _resolve_inside(root, raw.get("world", ""))
    world = _read_json(world_path)
    if not isinstance(world, dict):
        raise SourceProjectError("world.json must contain an object")

    raw_collections = raw.get("collections")
    if not isinstance(raw_collections, dict):
        raise SourceProjectError("collections must be an object")
    collections: dict[str, list[dict[str, Any]]] = {}
    for collection, files in raw_collections.items():
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            raise SourceProjectError(f"collections/{collection} must be a list of paths")
        items: list[dict[str, Any]] = []
        for relative in files:
            item = _read_json(_resolve_inside(root, relative))
            if not isinstance(item, dict):
                raise SourceProjectError(f"{relative} must contain an object")
            items.append(item)
        collections[collection] = items
    return SourceProject(manifest_path=manifest, world=world, collections=collections)
