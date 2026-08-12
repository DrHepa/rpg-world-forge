from __future__ import annotations

from pathlib import Path

from worldforge.asset_io import AssetContractError, read_json_object
from worldforge.creation_contracts import (
    CREATION_PROJECT_FORMAT,
    CreationContractError,
    load_creation_project,
)

LEGACY_PROJECT_FORMAT = "rpg-world-forge.project"


class CreationRouteError(ValueError):
    """Raised when a project root cannot be routed by an exact persisted format."""


def route_creation_project(project_root: str | Path) -> str:
    """Return ``generic`` or ``legacy`` from exact validated project identity."""

    requested = Path(project_root)
    if requested.is_symlink():
        raise CreationRouteError("project root cannot be a symbolic link")
    root = requested.resolve()
    if not root.is_dir():
        raise CreationRouteError(f"project root does not exist: {requested}")

    generic_marker = root / "project.json"
    legacy_marker = root / ".worldforge/project.json"
    generic_exists = generic_marker.exists() or generic_marker.is_symlink()
    legacy_exists = legacy_marker.exists() or legacy_marker.is_symlink()
    if generic_exists and legacy_exists:
        raise CreationRouteError("project root has conflicting generic and legacy identities")
    if generic_exists:
        try:
            loaded = load_creation_project(generic_marker)
        except CreationContractError as exc:
            raise CreationRouteError(f"invalid generic creation project: {exc}") from exc
        if loaded.project["format"] != CREATION_PROJECT_FORMAT:
            raise CreationRouteError("unsupported project boundary format")
        return "generic"
    if legacy_exists:
        try:
            marker = read_json_object(legacy_marker)
        except AssetContractError as exc:
            raise CreationRouteError(f"invalid legacy project marker: {exc}") from exc
        if marker.get("format") != LEGACY_PROJECT_FORMAT:
            raise CreationRouteError("unsupported project boundary format")
        version = marker.get("format_version")
        if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2, 3}:
            raise CreationRouteError("unsupported legacy project version")
        return "legacy"
    raise CreationRouteError("unsupported project boundary: no exact project marker")
