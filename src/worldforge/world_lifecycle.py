from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.portability import is_portable_path_component
from worldforge.repository_boundary import assert_new_repository_target
from worldforge.scaffold import ScaffoldError
from worldforge.validation import BCP47_PATTERN, ID_PATTERN
from worldforge.workflow import (
    PHASE_INDEX,
    WorkflowError,
    initial_status,
    phase_catalog,
    validate_workflow_status,
)
from worldforge.world_lock import exclusive_world_lifecycle

PROJECT_FORMAT = "rpg-world-forge.project"
PROJECT_VERSION = 2
PROJECT_KIND = "world"
STABLE_SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_ROOT_COPY_FILES = (".gitignore", "LICENSE", "SECURITY.md")
_CONTROL_COPY_FILES = ("DECISIONS.md",)
_ASSET_COPY_DIRECTORIES = ("references", "recipes", "licenses")
_SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    "credentials.json",
}
_VCS_CONTROL_NAMES = frozenset({".git", ".hg", ".svn"})
_INVALIDATED_STATUS_FIELDS = (
    "asset_inventory",
    "asset_manifest",
    "asset_target",
    "assetpack",
    "audio_bible",
    "compatibility_report",
    "release_hash",
    "release_package",
    "renderpack",
    "visual_bible",
    "worldpack_hash",
    "worldpack_path",
)
_MAX_CONTROL_BYTES = 4 * 1024 * 1024
_replace_file = os.replace
_PROJECT_REQUIRED_KEYS = {
    "approval_mode",
    "asset_generation",
    "format",
    "format_version",
    "lead_agent",
    "project_kind",
    "runtime_ai",
    "title",
    "tool_repository",
    "world_id",
    "world_version",
}
_PROJECT_ALLOWED_KEYS = _PROJECT_REQUIRED_KEYS | {"derived_from", "language"}
_ASSET_GENERATION_KEYS = {
    "enabled_routes",
    "local_model_route",
    "runtime_inference",
}
_DERIVED_FROM_KEYS = {"world_content_hash", "world_id", "world_version"}
_GENERATION_ROUTES = {"modly", "openai"}


@dataclass(frozen=True, order=True, slots=True)
class StableSemVer:
    """A stable SemVer value without prerelease or build metadata."""

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: object) -> StableSemVer:
        if not isinstance(value, str):
            raise ValueError("version must be a stable MAJOR.MINOR.PATCH string")
        match = STABLE_SEMVER_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError("version must be stable SemVer MAJOR.MINOR.PATCH")
        try:
            parts = tuple(int(component) for component in match.groups())
        except ValueError as exc:
            raise ValueError("version components are too large") from exc
        return cls(*parts)

    def bump(self, part: object) -> StableSemVer:
        if part == "major":
            return StableSemVer(self.major + 1, 0, 0)
        if part == "minor":
            return StableSemVer(self.major, self.minor + 1, 0)
        if part == "patch":
            return StableSemVer(self.major, self.minor, self.patch + 1)
        raise ValueError("part must be major, minor, or patch")

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class WorldProjectInspection:
    root: Path
    world_id: str
    title: str
    world_version: str | None
    legacy: bool
    current_phase: str | None
    revision: int
    canon_locked: bool
    worldpack_hash: str | None


@dataclass(frozen=True, slots=True)
class _ProjectFiles:
    inspection: WorldProjectInspection
    project: dict[str, Any]
    world: dict[str, Any]
    status: dict[str, Any]


def parse_stable_semver(value: object) -> StableSemVer:
    return StableSemVer.parse(value)


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _read_object(path: Path, *, error_type: type[ValueError]) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        if path.stat().st_size > _MAX_CONTROL_BYTES:
            raise OSError(f"exceeds {_MAX_CONTROL_BYTES} bytes")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise error_type(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise error_type(f"{path} must contain an object")
    return value


def _encoded_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _stage_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _commit_json_transaction(updates: dict[Path, object]) -> None:
    """Replace JSON files and restore every original on an observed failure."""

    originals: dict[Path, bytes | None] = {}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, value in updates.items():
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise WorkflowError(f"Transaction target is not a regular file: {path}")
                originals[path] = path.read_bytes()
            else:
                originals[path] = None
            staged[path] = _stage_bytes(path, _encoded_json(value))
        for path, temporary in staged.items():
            _replace_file(temporary, path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                restore = _stage_bytes(path, original)
                os.replace(restore, path)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _exclusive_lifecycle_lock(project_root: str | Path):
    return exclusive_world_lifecycle(project_root, error_type=WorkflowError)


def _validate_v2_project_control(project: dict[str, Any]) -> None:
    keys = set(project)
    missing = sorted(_PROJECT_REQUIRED_KEYS - keys)
    extra = sorted(keys - _PROJECT_ALLOWED_KEYS)
    if missing:
        raise WorkflowError(f"World-project control is missing fields: {', '.join(missing)}")
    if extra:
        raise WorkflowError(f"World-project control has unknown fields: {', '.join(extra)}")
    if not isinstance(project.get("lead_agent"), str) or not project["lead_agent"].strip():
        raise WorkflowError("World-project lead_agent must be a non-empty string")
    if not isinstance(project.get("approval_mode"), str) or not project["approval_mode"].strip():
        raise WorkflowError("World-project approval_mode must be a non-empty string")
    if project.get("runtime_ai") is not False:
        raise WorkflowError("World-project runtime_ai must be false")
    if project.get("tool_repository") != "rpg-world-forge":
        raise WorkflowError("World-project tool_repository must be rpg-world-forge")
    if "language" in project:
        language = project["language"]
        if not isinstance(language, str) or BCP47_PATTERN.fullmatch(language) is None:
            raise WorkflowError("World-project language must be a BCP47 language tag")

    generation = project.get("asset_generation")
    if not isinstance(generation, dict) or set(generation) != _ASSET_GENERATION_KEYS:
        raise WorkflowError("World-project asset_generation has an invalid shape")
    routes = generation.get("enabled_routes")
    if (
        not isinstance(routes, list)
        or not routes
        or not all(isinstance(route, str) and route in _GENERATION_ROUTES for route in routes)
        or len(routes) != len(set(routes))
    ):
        raise WorkflowError("World-project enabled_routes must be unique OpenAI/Modly routes")
    if generation.get("local_model_route") != "modly":
        raise WorkflowError("World-project local_model_route must be modly")
    if generation.get("runtime_inference") is not False:
        raise WorkflowError("World-project runtime_inference must be false")

    if "derived_from" not in project:
        return
    lineage = project["derived_from"]
    if not isinstance(lineage, dict) or set(lineage) != _DERIVED_FROM_KEYS:
        raise WorkflowError("World-project derived_from has an invalid shape")
    ancestor_id = lineage.get("world_id")
    if (
        not isinstance(ancestor_id, str)
        or ID_PATTERN.fullmatch(ancestor_id) is None
        or not is_portable_path_component(ancestor_id)
    ):
        raise WorkflowError("World-project derived_from world_id is invalid")
    try:
        parse_stable_semver(lineage.get("world_version"))
    except ValueError as exc:
        raise WorkflowError(f"World-project derived_from {exc}") from exc
    content_hash = lineage.get("world_content_hash")
    if content_hash is not None and (
        not isinstance(content_hash, str) or SHA256_PATTERN.fullmatch(content_hash) is None
    ):
        raise WorkflowError("World-project derived_from world_content_hash is invalid")


def _normalized_source_path(value: object, context: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise WorkflowError(f"{context} must be a non-empty relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part for part in path.parts)
    ):
        raise WorkflowError(f"{context} must be a normalized relative POSIX path")
    return path


def _require_regular_source_file(
    source_root: Path,
    relative: PurePosixPath,
    context: str,
) -> None:
    candidate = source_root
    for component in relative.parts:
        candidate /= component
        if candidate.is_symlink():
            raise WorkflowError(f"{context} cannot traverse a symbolic link")
    try:
        info = candidate.stat()
    except OSError as exc:
        raise WorkflowError(f"{context} does not reference a readable file") from exc
    if not stat.S_ISREG(info.st_mode):
        raise WorkflowError(f"{context} must reference a regular file")


def _validate_source_manifest(source_root: Path, manifest: dict[str, Any]) -> None:
    manifest_version = manifest.get("format_version")
    if (
        manifest.get("format") != "isoworld.source_manifest"
        or isinstance(manifest_version, bool)
        or not isinstance(manifest_version, int)
        or manifest_version != 1
    ):
        raise WorkflowError("Unsupported source manifest")
    if source_root.is_symlink() or not source_root.is_dir():
        raise WorkflowError("World source root must be a regular directory")

    world_path = _normalized_source_path(manifest.get("world"), "Source manifest world")
    if world_path != PurePosixPath("world.json"):
        raise WorkflowError("Source manifest world must reference world.json")
    _require_regular_source_file(source_root, world_path, "Source manifest world")

    collections = manifest.get("collections")
    if not isinstance(collections, dict):
        raise WorkflowError("Source manifest collections must be an object")
    for collection, entries in collections.items():
        if not isinstance(entries, list):
            raise WorkflowError(f"Source manifest collection {collection!r} must be a list")
        normalized: list[PurePosixPath] = []
        for index, entry in enumerate(entries):
            context = f"Source manifest collections/{collection}/{index}"
            relative = _normalized_source_path(entry, context)
            _require_regular_source_file(source_root, relative, context)
            normalized.append(relative)
        if len(normalized) != len(set(normalized)):
            raise WorkflowError(f"Source manifest collection {collection!r} has duplicate paths")


def _validate_status(
    status: dict[str, Any],
    world_id: str,
    *,
    world_version: str | None,
    legacy: bool,
) -> None:
    validate_workflow_status(status, expected_world_id=world_id)
    if not legacy and status.get("world_version") != world_version:
        raise WorkflowError("Project, world, and workflow status versions do not match")


def inspect_world_project_snapshot(
    project_root: str | Path,
    project: dict[str, Any],
    world: dict[str, Any],
    status: dict[str, Any],
    *,
    allow_legacy: bool = False,
    error_type: type[ValueError] = WorkflowError,
) -> WorldProjectInspection:
    """Validate already captured project controls without reading or locking the filesystem."""

    root = Path(project_root)
    if project.get("format") != PROJECT_FORMAT:
        raise error_type("Unknown world-project format")
    raw_version = project.get("format_version")
    raw_kind = project.get("project_kind")
    if raw_kind == "game":
        raise error_type("A game repository is not a world-authoring repository")
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise error_type("World-project format_version must be an integer")
    legacy = raw_version == 1
    if legacy:
        if not allow_legacy:
            raise error_type("Legacy world project requires explicit upgrade")
        if raw_kind not in {None, PROJECT_KIND}:
            raise error_type("Legacy project_kind must be world when present")
    elif raw_version != PROJECT_VERSION or raw_kind != PROJECT_KIND:
        raise error_type("Only world-project format version 2 is supported")
    else:
        try:
            _validate_v2_project_control(project)
        except WorkflowError as exc:
            raise error_type(str(exc)) from exc

    world_id = project.get("world_id")
    if (
        not isinstance(world_id, str)
        or ID_PATTERN.fullmatch(world_id) is None
        or not is_portable_path_component(world_id)
    ):
        raise error_type("The project has an invalid world_id")
    if world.get("id") != world_id:
        raise error_type("Project and world IDs do not match")
    title = project.get("title")
    if not isinstance(title, str) or not title.strip() or world.get("title") != title:
        raise error_type("Project and world titles must match and cannot be empty")
    world_language = world.get("language")
    if not isinstance(world_language, str) or BCP47_PATTERN.fullmatch(world_language) is None:
        raise error_type("World language must be a BCP47 language tag")
    project_language = project.get("language")
    if (
        isinstance(project_language, str)
        and project_language.casefold() != world_language.casefold()
    ):
        raise error_type("Project and world languages do not match")

    project_version = project.get("world_version")
    world_version = world.get("version")
    normalized_version: str | None = None
    if project_version is None and world_version is None and legacy:
        pass
    else:
        try:
            parsed_project = parse_stable_semver(project_version)
            parsed_world = parse_stable_semver(world_version)
        except ValueError as exc:
            raise error_type(str(exc)) from exc
        if parsed_project != parsed_world:
            raise error_type("Project and world versions do not match")
        normalized_version = str(parsed_project)

    try:
        _validate_status(
            status,
            world_id,
            world_version=normalized_version,
            legacy=legacy,
        )
    except WorkflowError as exc:
        raise error_type(str(exc)) from exc
    return WorldProjectInspection(
        root=root,
        world_id=world_id,
        title=title,
        world_version=normalized_version,
        legacy=legacy,
        current_phase=status["current_phase"],
        revision=status["revision"],
        canon_locked=status["canon_locked"],
        worldpack_hash=status.get("worldpack_hash"),
    )


def _inspect_files(
    project_root: str | Path,
    *,
    allow_legacy: bool,
    error_type: type[ValueError],
    _status: dict[str, Any] | None = None,
) -> _ProjectFiles:
    root_input = Path(project_root)
    if root_input.is_symlink():
        raise error_type("The world project root cannot be a symbolic link")
    root = root_input.resolve()
    if not root.is_dir():
        raise error_type(f"The world project does not exist: {root}")
    project = _read_object(root / ".worldforge/project.json", error_type=error_type)
    manifest = _read_object(root / "source/manifest.json", error_type=error_type)
    try:
        _validate_source_manifest(root / "source", manifest)
    except WorkflowError as exc:
        raise error_type(str(exc)) from exc
    world = _read_object(root / "source/world.json", error_type=error_type)
    status = (
        _read_object(root / ".worldforge/status.json", error_type=error_type)
        if _status is None
        else _status
    )

    inspection = inspect_world_project_snapshot(
        root,
        project,
        world,
        status,
        allow_legacy=allow_legacy,
        error_type=error_type,
    )
    return _ProjectFiles(inspection, project, world, status)


def _load_canonical_status_unlocked(
    project_root: str | Path,
    status: dict[str, Any],
) -> dict[str, Any]:
    """Bind a pre-read status to canonical v2 project controls; caller owns the lock."""

    return _inspect_files(
        project_root,
        allow_legacy=False,
        error_type=WorkflowError,
        _status=status,
    ).status


def inspect_world_project(project_root: str | Path) -> WorldProjectInspection:
    """Inspect a canonical v2 world-authoring repository without importing content."""

    with _exclusive_lifecycle_lock(project_root) as locked_root:
        return _inspect_files(
            locked_root,
            allow_legacy=False,
            error_type=WorkflowError,
        ).inspection


def _copy_regular_file(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise ScaffoldError(f"World source contains a symbolic link: {source}")
    try:
        mode = source.stat().st_mode
    except OSError as exc:
        raise ScaffoldError(f"Could not inspect world source {source}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise ScaffoldError(f"World source is not a regular file: {source}")
    if source.name.lower() in _SENSITIVE_FILENAMES:
        raise ScaffoldError(f"World source contains a credential-like file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, destination)
    except OSError as exc:
        raise ScaffoldError(f"Could not copy {source}: {exc}") from exc


def _copy_tree_strict(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise ScaffoldError(f"World source directory is missing or unsafe: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        entries = sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix())
    except OSError as exc:
        raise ScaffoldError(f"Could not inspect world source {source}: {exc}") from exc
    for entry in entries:
        relative = entry.relative_to(source)
        if any(part.casefold() in _VCS_CONTROL_NAMES for part in relative.parts):
            raise ScaffoldError(f"World source contains a VCS control entry: {entry}")
    for entry in entries:
        relative = entry.relative_to(source)
        if entry.is_symlink():
            raise ScaffoldError(f"World source contains a symbolic link: {entry}")
        try:
            mode = entry.stat().st_mode
        except OSError as exc:
            raise ScaffoldError(f"Could not inspect world source {entry}: {exc}") from exc
        target = destination / relative
        if stat.S_ISDIR(mode):
            target.mkdir(parents=True, exist_ok=True)
        elif stat.S_ISREG(mode):
            _copy_regular_file(entry, target)
        else:
            raise ScaffoldError(f"World source contains a non-regular entry: {entry}")


def _copy_asset_inputs(source_root: Path, target_root: Path) -> bool:
    source_assets = source_root / "assets"
    if not source_assets.exists():
        return False
    if source_assets.is_symlink() or not source_assets.is_dir():
        raise ScaffoldError("World assets root must be a regular directory")
    copied = False
    for directory in _ASSET_COPY_DIRECTORIES:
        source = source_assets / directory
        if source.exists():
            _copy_tree_strict(source, target_root / "assets" / directory)
            copied = True

    manifest_path = source_assets / "manifest.json"
    if manifest_path.exists():
        manifest = _read_object(manifest_path, error_type=ScaffoldError)
        bound_fields = {"world_id", "world_content_hash", "content_hash", "renderpack"}
        if (
            manifest.get("format") == "rpg-world-forge.asset_source_manifest"
            and manifest.get("format_version") == 1
            and not bound_fields.intersection(manifest)
        ):
            _copy_regular_file(manifest_path, target_root / "assets/manifest.json")
            copied = True
    return copied


def _write_world_authoring_docs(root: Path, title: str) -> None:
    (root / "README.md").write_text(
        f"# {title}\n\n"
        "This is an independent world-authoring repository created with RPG World Forge. "
        "It owns reviewed canon, structured source, workflow evidence, and asset-production "
        "inputs. It is not a game repository and contains no game runtime.\n\n"
        "A separate game repository may consume only an immutable, validated runtime bundle; "
        "it must not copy `source/`, `.worldforge/`, prompts, or authoring dependencies.\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        f"# Agents for {title}\n\n"
        "GPT is the principal authoring agent and owns integration of this world's canon.\n\n"
        "## Repository boundary\n\n"
        "- This is a world-authoring repository, not the Forge and not a game repository.\n"
        "- AI is permitted only for reviewed offline authoring and asset production.\n"
        "- A game consumes only the immutable runtime bundle produced after validation.\n"
        "- Never place prompts, provider SDKs, credentials, models, `source/`, or "
        "`.worldforge/` in a game repository or runtime bundle.\n"
        "- Record provenance, licenses, decisions, and validation evidence before release.\n",
        encoding="utf-8",
    )


def _reset_control_files(root: Path) -> None:
    (root / ".worldforge/TASKS.md").write_text(
        "# Tasks\n\nThe lead GPT maintains the derived world's active authoring backlog.\n",
        encoding="utf-8",
    )
    (root / ".worldforge/HANDOFF.md").write_text(
        "# Runtime-bundle handoff\n\nProduced only after canon, assets, and compatibility pass.\n",
        encoding="utf-8",
    )


def clone_world_project(
    source_root: str | Path,
    target_root: str | Path,
    *,
    world_id: str,
    title: str,
    version: str = "0.1.0",
) -> Path:
    """Create a new v2 world-authoring repository derived from another v2 world."""

    try:
        with _exclusive_lifecycle_lock(source_root) as locked_source:
            return _clone_world_project_locked(
                locked_source,
                target_root,
                world_id=world_id,
                title=title,
                version=version,
            )
    except WorkflowError as exc:
        raise ScaffoldError(str(exc)) from exc


def _clone_world_project_locked(
    source_root: Path,
    target_root: str | Path,
    *,
    world_id: str,
    title: str,
    version: str,
) -> Path:
    source = _inspect_files(
        source_root,
        allow_legacy=False,
        error_type=ScaffoldError,
    )
    try:
        target = assert_new_repository_target(target_root, repository_type="world")
    except ValueError as exc:
        raise ScaffoldError(str(exc)) from exc
    if source.inspection.root == target or source.inspection.root in target.parents:
        raise ScaffoldError("The clone target cannot be inside the source world project")
    if (
        not isinstance(world_id, str)
        or ID_PATTERN.fullmatch(world_id) is None
        or not is_portable_path_component(world_id)
    ):
        raise ScaffoldError("world_id must be portable 2..64-character ASCII snake_case")
    if world_id == source.inspection.world_id:
        raise ScaffoldError("A derived world must use a new world_id")
    if not isinstance(title, str) or not title.strip():
        raise ScaffoldError("title cannot be empty")
    try:
        target_version = str(parse_stable_semver(version))
    except ValueError as exc:
        raise ScaffoldError(str(exc)) from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.clone-", dir=target.parent))
    try:
        _copy_tree_strict(source.inspection.root / "source", staging / "source")
        for relative in _ROOT_COPY_FILES:
            candidate = source.inspection.root / relative
            if candidate.exists():
                _copy_regular_file(candidate, staging / relative)
        for relative in _CONTROL_COPY_FILES:
            candidate = source.inspection.root / ".worldforge" / relative
            if candidate.exists():
                _copy_regular_file(candidate, staging / ".worldforge" / relative)
        asset_inputs_copied = _copy_asset_inputs(source.inspection.root, staging)

        cloned_world = _read_object(staging / "source/world.json", error_type=ScaffoldError)
        cloned_world["id"] = world_id
        cloned_world["title"] = title.strip()
        cloned_world["version"] = target_version
        lineage = {
            "world_id": source.inspection.world_id,
            "world_version": source.inspection.world_version,
            "world_content_hash": source.inspection.worldpack_hash,
        }
        cloned_project = dict(source.project)
        cloned_project.update(
            {
                "format": PROJECT_FORMAT,
                "format_version": PROJECT_VERSION,
                "project_kind": PROJECT_KIND,
                "world_id": world_id,
                "title": title.strip(),
                "world_version": target_version,
                "derived_from": lineage,
            }
        )
        status = initial_status(world_id)
        status["world_version"] = target_version
        clone_log = {
            "format": "rpg-world-forge.clone_log",
            "format_version": 1,
            "entries": [
                {
                    "derived_from": lineage,
                    "world_id": world_id,
                    "world_version": target_version,
                    "title": title.strip(),
                    "asset_inputs_copied": asset_inputs_copied,
                }
            ],
        }
        phases = {
            "format": "rpg-world-forge.phase_catalog",
            "format_version": 1,
            "phases": phase_catalog(),
        }
        _commit_json_transaction(
            {
                staging / "source/world.json": cloned_world,
                staging / ".worldforge/project.json": cloned_project,
                staging / ".worldforge/status.json": status,
                staging / ".worldforge/phases.json": phases,
                staging / ".worldforge/clone_log.json": clone_log,
            }
        )
        _write_world_authoring_docs(staging, title.strip())
        _reset_control_files(staging)
        if target.exists():
            raise ScaffoldError(f"The target already exists: {target}")
        staging.rename(target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return target / "source/manifest.json"


def _load_version_log(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "format": "rpg-world-forge.version_log",
            "format_version": 1,
            "entries": [],
        }
    log = _read_object(path, error_type=WorkflowError)
    log_version = log.get("format_version")
    if (
        log.get("format") != "rpg-world-forge.version_log"
        or isinstance(log_version, bool)
        or not isinstance(log_version, int)
        or log_version != 1
    ):
        raise WorkflowError("Unsupported world version log")
    entries = log.get("entries")
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise WorkflowError("The world version log entries must be objects")
    return log


def _invalidated_status(status: dict[str, Any], version: str) -> dict[str, Any]:
    updated = dict(status)
    completed = updated["completed_phases"]
    current = updated["current_phase"]
    canon_index = PHASE_INDEX["p10_canon_lock"]
    reached_canon = (
        "p10_canon_lock" in completed
        or current is None
        or (current in PHASE_INDEX and PHASE_INDEX[current] > canon_index)
    )
    if reached_canon:
        updated["completed_phases"] = [
            item for item in completed if PHASE_INDEX[item] < canon_index
        ]
        updated["current_phase"] = "p10_canon_lock"
    updated["revision"] += 1
    updated["world_version"] = version
    updated["canon_locked"] = False
    for field in _INVALIDATED_STATUS_FIELDS:
        updated[field] = None
    return updated


def upgrade_legacy_world_project(
    project_root: str | Path,
    *,
    version: str,
    reason: str,
    approved_by: str,
) -> WorldProjectInspection:
    """Explicitly upgrade a legacy v1 authoring repository to the v2 world contract."""

    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowError("reason is required")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise WorkflowError("approved_by is required")
    try:
        normalized = str(parse_stable_semver(version))
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc
    with _exclusive_lifecycle_lock(project_root) as locked_root:
        files = _inspect_files(
            locked_root,
            allow_legacy=True,
            error_type=WorkflowError,
        )
        if not files.inspection.legacy:
            raise WorkflowError("The world project is already format version 2")

        project = dict(files.project)
        project.update(
            {
                "format_version": PROJECT_VERSION,
                "project_kind": PROJECT_KIND,
                "world_version": normalized,
            }
        )
        _validate_v2_project_control(project)
        world = dict(files.world)
        world["version"] = normalized
        status = _invalidated_status(files.status, normalized)
        log = _load_version_log(files.inspection.root / ".worldforge/version_log.json")
        log = dict(log)
        log["entries"] = [
            *log["entries"],
            {
                "from": files.inspection.world_version,
                "to": normalized,
                "part": "legacy_upgrade",
                "reason": reason.strip(),
                "approved_by": approved_by.strip(),
                "workflow_revision": status["revision"],
            },
        ]
        _commit_json_transaction(
            {
                files.inspection.root / ".worldforge/project.json": project,
                files.inspection.root / "source/world.json": world,
                files.inspection.root / ".worldforge/status.json": status,
                files.inspection.root / ".worldforge/version_log.json": log,
            }
        )
        return _inspect_files(
            files.inspection.root,
            allow_legacy=False,
            error_type=WorkflowError,
        ).inspection


def bump_world_version(
    project_root: str | Path,
    *,
    expected_version: str,
    part: str,
    reason: str,
    approved_by: str,
) -> str:
    """Bump a v2 world's version after an optimistic expected-version check."""

    try:
        expected = parse_stable_semver(expected_version)
    except ValueError as exc:
        raise WorkflowError(f"invalid expected_version: {exc}") from exc
    if not isinstance(part, str) or part not in {"major", "minor", "patch"}:
        raise WorkflowError("part must be major, minor, or patch")
    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowError("reason is required")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise WorkflowError("approved_by is required")

    with _exclusive_lifecycle_lock(project_root) as locked_root:
        files = _inspect_files(
            locked_root,
            allow_legacy=False,
            error_type=WorkflowError,
        )
        current = parse_stable_semver(files.inspection.world_version)
        if current != expected:
            raise WorkflowError(
                f"expected world version {expected}, found {files.inspection.world_version}"
            )
        new_version = str(current.bump(part))
        project = dict(files.project)
        project["world_version"] = new_version
        world = dict(files.world)
        world["version"] = new_version
        status = _invalidated_status(files.status, new_version)
        log_path = files.inspection.root / ".worldforge/version_log.json"
        log = _load_version_log(log_path)
        log = dict(log)
        log["entries"] = [
            *log["entries"],
            {
                "from": str(current),
                "to": new_version,
                "part": part,
                "reason": reason.strip(),
                "approved_by": approved_by.strip(),
                "workflow_revision": status["revision"],
            },
        ]
        _commit_json_transaction(
            {
                files.inspection.root / ".worldforge/project.json": project,
                files.inspection.root / "source/world.json": world,
                files.inspection.root / ".worldforge/status.json": status,
                log_path: log,
            }
        )
        return new_version
