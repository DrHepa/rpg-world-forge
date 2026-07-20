from __future__ import annotations

import stat
from pathlib import Path

FORGE_ROOT = Path(__file__).resolve().parents[2]


def repository_kind(path: str | Path) -> str | None:
    """Classify a repository root from control-plane markers without executing it."""

    candidate_input = Path(path)
    if candidate_input.is_symlink():
        return "unsafe"
    candidate = candidate_input.resolve()
    if candidate == FORGE_ROOT:
        return "forge"
    if (candidate / ".worldforge/project.json").is_file():
        return "world"
    game_markers = (
        candidate / "runtime.lock.json",
        candidate / "platform.lock.json",
        candidate / "game_data/worlds.lock.json",
        candidate / "src/game",
        candidate / "src/isoworld",
    )
    if all(marker.exists() and not marker.is_symlink() for marker in game_markers):
        return "game"
    bundle_markers = (
        candidate / "bundle.manifest.json",
        candidate / "worldpack.json",
        candidate / "renderpack.json",
    )
    if all(marker.is_file() and not marker.is_symlink() for marker in bundle_markers):
        return "bundle"
    return None


def assert_new_repository_target(
    target: str | Path,
    *,
    repository_type: str,
) -> Path:
    """Reject a new repository target that would mix repository responsibilities."""

    target_input = Path(target)
    if target_input.exists() or target_input.is_symlink():
        raise ValueError(f"The target already exists: {target_input}")
    destination = target_input.resolve()
    if destination == FORGE_ROOT or FORGE_ROOT in destination.parents:
        raise ValueError(f"The {repository_type} repository must live outside the Forge repository")
    for ancestor in (target_input.parent, *target_input.parent.parents):
        kind = repository_kind(ancestor)
        if kind in {"world", "game", "bundle", "forge", "unsafe"}:
            raise ValueError(
                f"The {repository_type} repository cannot be nested inside a {kind} repository"
            )
    return destination


def require_standalone_bundle_root(path: str | Path) -> Path:
    """Return an external runtime-bundle root with no repository ancestor."""

    root_input = Path(path)
    if root_input.is_symlink():
        raise ValueError("The bundle root cannot be a symbolic link")
    root = root_input.resolve()
    if repository_kind(root) != "bundle":
        raise ValueError("The source is not a recognizable runtime bundle")
    for ancestor in root.parents:
        kind = repository_kind(ancestor)
        if kind in {"world", "game", "bundle", "forge", "unsafe"}:
            raise ValueError(f"The runtime bundle cannot be nested inside a {kind} repository")
    return root


def require_standalone_game_root(path: str | Path) -> Path:
    """Return a resolved standalone game root after checking its fixed identity markers."""

    root_input = Path(path)
    if root_input.is_symlink():
        raise ValueError("The game repository root cannot be a symbolic link")
    root = root_input.resolve()
    if repository_kind(root) != "game":
        raise ValueError("The target is not a recognizable standalone game repository")
    for ancestor in root.parents:
        kind = repository_kind(ancestor)
        if kind in {"world", "game", "bundle", "forge", "unsafe"}:
            raise ValueError(f"The game repository cannot be nested inside a {kind} repository")
    required_files = (
        root / "runtime.lock.json",
        root / "platform.lock.json",
        root / "game_data/worlds.lock.json",
    )
    for file in required_files:
        info = file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"Game identity file is unsafe: {file}")
    for directory in (root / "src/game", root / "src/isoworld", root / "game_data"):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or directory.is_symlink():
            raise ValueError(f"Game identity directory is unsafe: {directory}")
    return root
