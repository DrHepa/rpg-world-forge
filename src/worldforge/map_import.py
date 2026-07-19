from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from worldforge.validation import ID_PATTERN


class MapImportError(ValueError):
    """Raised when an external map cannot be converted safely."""


SYMBOLS = ".#~^+*=-_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
TILED_FLIP_MASK = 0x0FFFFFFF


def _read_object(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MapImportError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MapImportError(f"{path} must contain a JSON object")
    return value


def load_mapping(path: str | Path) -> dict[int, str]:
    raw = _read_object(path)
    mapping: dict[int, str] = {}
    for key, value in raw.items():
        try:
            numeric = int(key)
        except ValueError as exc:
            raise MapImportError(f"Mapping key is not an integer: {key}") from exc
        if numeric < 0 or not isinstance(value, str) or not value:
            raise MapImportError(f"Invalid mapping entry: {key}")
        mapping[numeric] = value
    if not mapping:
        raise MapImportError("The tile mapping cannot be empty")
    return mapping


def _internal_map(
    *,
    map_id: str,
    display_name: str,
    width: int,
    height: int,
    values: list[int],
    mapping: dict[int, str],
    default_tile: str | None,
) -> dict[str, Any]:
    if not ID_PATTERN.fullmatch(map_id):
        raise MapImportError("map_id must use 2..64-character ASCII snake_case")
    if not display_name.strip():
        raise MapImportError("display_name cannot be empty")
    if width <= 0 or height <= 0 or len(values) != width * height:
        raise MapImportError("Layer dimensions and tile data length do not match")
    tile_ids: list[str] = []
    for value in values:
        if value == 0 and default_tile is not None:
            tile_ids.append(default_tile)
        elif value in mapping:
            tile_ids.append(mapping[value])
        else:
            raise MapImportError(f"No internal tile mapping for external value {value}")
    unique = sorted(set(tile_ids))
    for tile_id in unique:
        if not ID_PATTERN.fullmatch(tile_id):
            raise MapImportError(f"Invalid internal tile ID: {tile_id}")
    if len(unique) > len(SYMBOLS):
        raise MapImportError("The map uses too many tile types for the row encoding")
    tile_to_symbol = {tile_id: SYMBOLS[index] for index, tile_id in enumerate(unique)}
    rows = [
        "".join(tile_to_symbol[value] for value in tile_ids[offset : offset + width])
        for offset in range(0, len(tile_ids), width)
    ]
    return {
        "id": map_id,
        "display_name": display_name,
        "width": width,
        "height": height,
        "legend": {symbol: tile_id for tile_id, symbol in tile_to_symbol.items()},
        "rows": rows,
        "import": {"source_format": "external", "manual_overrides": []},
    }


def import_tiled(
    raw: dict[str, Any],
    *,
    map_id: str,
    display_name: str,
    mapping: dict[int, str],
    layer_name: str | None = None,
    default_tile: str | None = None,
) -> dict[str, Any]:
    if raw.get("infinite") is True:
        raise MapImportError("Infinite/chunked Tiled maps are not supported in M1")
    layers = raw.get("layers")
    if not isinstance(layers, list):
        raise MapImportError("Tiled map has no layers")
    tile_layers = [
        layer
        for layer in layers
        if isinstance(layer, dict)
        and layer.get("type") == "tilelayer"
        and (layer_name is None or layer.get("name") == layer_name)
    ]
    if not tile_layers:
        raise MapImportError("No matching finite Tiled tile layer")
    layer = tile_layers[0]
    data = layer.get("data")
    if not isinstance(data, list) or not all(isinstance(value, int) for value in data):
        raise MapImportError("Tiled layer data must be an uncompressed JSON array")
    width = int(layer.get("width", raw.get("width", 0)))
    height = int(layer.get("height", raw.get("height", 0)))
    result = _internal_map(
        map_id=map_id,
        display_name=display_name,
        width=width,
        height=height,
        values=[value & TILED_FLIP_MASK for value in data],
        mapping=mapping,
        default_tile=default_tile,
    )
    result["import"] = {
        "source_format": "tiled-json",
        "layer": layer.get("name", ""),
        "manual_overrides": [],
    }
    return result


def import_ldtk(
    raw: dict[str, Any],
    *,
    map_id: str,
    display_name: str,
    mapping: dict[int, str],
    layer_name: str | None = None,
    level_name: str | None = None,
    default_tile: str | None = None,
) -> dict[str, Any]:
    levels = raw.get("levels")
    if not isinstance(levels, list) or not levels:
        raise MapImportError("LDtk project has no embedded levels")
    candidates = [
        level
        for level in levels
        if isinstance(level, dict)
        and (
            level_name is None
            or level.get("identifier") == level_name
            or level.get("iid") == level_name
        )
    ]
    if not candidates:
        raise MapImportError("No matching embedded LDtk level")
    level = candidates[0]
    layers = level.get("layerInstances")
    if not isinstance(layers, list):
        raise MapImportError("External LDtk level files are not supported in M1")
    layer_candidates = [
        layer
        for layer in layers
        if isinstance(layer, dict)
        and (
            layer_name is None
            or layer.get("__identifier") == layer_name
            or layer.get("iid") == layer_name
        )
    ]
    if not layer_candidates:
        raise MapImportError("No matching LDtk layer")
    layer = layer_candidates[0]
    width = int(layer.get("__cWid", 0))
    height = int(layer.get("__cHei", 0))
    int_grid = layer.get("intGridCsv")
    if isinstance(int_grid, list) and int_grid:
        if not all(isinstance(value, int) for value in int_grid):
            raise MapImportError("LDtk IntGrid values must be integers")
        values = list(int_grid)
    else:
        grid_size = int(layer.get("__gridSize", 0))
        if grid_size <= 0:
            raise MapImportError("LDtk tile layer has no valid grid size")
        values = [0] * (width * height)
        tiles = list(layer.get("autoLayerTiles", [])) + list(layer.get("gridTiles", []))
        for tile in tiles:
            try:
                x = int(tile["px"][0]) // grid_size
                y = int(tile["px"][1]) // grid_size
                value = int(tile["t"])
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                raise MapImportError("Malformed LDtk grid tile") from exc
            if 0 <= x < width and 0 <= y < height:
                values[y * width + x] = value
    result = _internal_map(
        map_id=map_id,
        display_name=display_name,
        width=width,
        height=height,
        values=values,
        mapping=mapping,
        default_tile=default_tile,
    )
    result["import"] = {
        "source_format": "ldtk-json",
        "level": level.get("identifier", level.get("iid", "")),
        "layer": layer.get("__identifier", layer.get("iid", "")),
        "manual_overrides": [],
    }
    return result


def import_map_file(
    source: str | Path,
    *,
    source_format: str,
    map_id: str,
    display_name: str,
    mapping: dict[int, str],
    layer_name: str | None = None,
    level_name: str | None = None,
    default_tile: str | None = None,
) -> dict[str, Any]:
    source_path = Path(source)
    raw = _read_object(source_path)
    detected = source_format
    if detected == "auto":
        detected = "ldtk" if "levels" in raw and "defs" in raw else "tiled"
    if detected == "tiled":
        result = import_tiled(
            raw,
            map_id=map_id,
            display_name=display_name,
            mapping=mapping,
            layer_name=layer_name,
            default_tile=default_tile,
        )
    elif detected == "ldtk":
        result = import_ldtk(
            raw,
            map_id=map_id,
            display_name=display_name,
            mapping=mapping,
            layer_name=layer_name,
            level_name=level_name,
            default_tile=default_tile,
        )
    else:
        raise MapImportError(f"Unknown map format: {source_format}")
    mapping_canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":"))
    result["import"].update(
        {
            "source_name": source_path.name,
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            "mapping_sha256": hashlib.sha256(mapping_canonical.encode("utf-8")).hexdigest(),
        }
    )
    return result


def write_imported_map(path: str | Path, value: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
