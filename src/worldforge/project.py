from __future__ import annotations

import json
from collections.abc import Mapping
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


def source_manifest_documents(
    value: object,
) -> tuple[str, dict[str, tuple[str, ...]]]:
    """Return the document index declared by one closed source manifest."""

    if not isinstance(value, dict):
        raise SourceProjectError("The manifest must be a JSON object")
    expected = {"format", "format_version", "world", "collections"}
    missing = expected - set(value)
    unknown = set(value) - expected
    if missing:
        raise SourceProjectError(f"The manifest is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise SourceProjectError(
            f"The manifest contains unknown fields: {', '.join(sorted(unknown))}"
        )
    if value["format"] != "isoworld.source_manifest":
        raise SourceProjectError("Unknown source manifest format")
    if isinstance(value["format_version"], bool) or value["format_version"] != 1:
        raise SourceProjectError("Unsupported source manifest version")
    world = value["world"]
    if not isinstance(world, str) or not world:
        raise SourceProjectError("world must be a non-empty path")
    raw_collections = value["collections"]
    if not isinstance(raw_collections, dict):
        raise SourceProjectError("collections must be an object")
    collections: dict[str, tuple[str, ...]] = {}
    for collection, files in raw_collections.items():
        if not isinstance(collection, str) or not collection:
            raise SourceProjectError("collection names must be non-empty strings")
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            raise SourceProjectError(f"collections/{collection} must be a list of paths")
        collections[collection] = tuple(files)
    return world, collections


def source_project_from_documents(
    manifest_path: str | Path,
    manifest: object,
    documents: Mapping[str, object],
) -> SourceProject:
    """Build the domain source project from an already captured document snapshot."""

    world_path, collection_paths = source_manifest_documents(manifest)
    try:
        world = documents[world_path]
    except KeyError as exc:
        raise SourceProjectError(f"Source document is missing: {world_path}") from exc
    if not isinstance(world, dict):
        raise SourceProjectError("world.json must contain an object")
    collections: dict[str, list[dict[str, Any]]] = {}
    for collection, paths in collection_paths.items():
        items: list[dict[str, Any]] = []
        for relative in paths:
            try:
                item = documents[relative]
            except KeyError as exc:
                raise SourceProjectError(f"Source document is missing: {relative}") from exc
            if not isinstance(item, dict):
                raise SourceProjectError(f"{relative} must contain an object")
            items.append(item)
        collections[collection] = items
    return SourceProject(Path(manifest_path), world, collections)


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
    world_relative, collection_paths = source_manifest_documents(raw)

    root = manifest.parent
    documents: dict[str, object] = {
        world_relative: _read_json(_resolve_inside(root, world_relative))
    }
    for files in collection_paths.values():
        for relative in files:
            documents[relative] = _read_json(_resolve_inside(root, relative))
    return source_project_from_documents(manifest, raw, documents)
