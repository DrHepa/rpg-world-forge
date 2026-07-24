from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.file_stat import (
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
)
from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.media import media_signature_matches
from isoworld.content.models import WorldPack
from isoworld.content.portability import is_portable_path_component
from isoworld.content.publication_journal import audit_publication_journals
from isoworld.content.renderpack import RenderPack, RenderPackError, load_renderpack
from worldforge.directory_publish import (
    DirectoryIdentity,
    DirectoryPublishError,
    append_append_only_journal,
    create_append_only_journal,
    directory_identity,
    fsync_directory,
    open_expected_directory,
    publish_directory_noreplace,
    quarantine_and_remove_owned_directory,
    read_append_only_journal,
    remove_owned_empty_directory,
)
from worldforge.game_boundary import GameBoundaryError, audit_game_repository
from worldforge.game_lock import GameMutationLockError, exclusive_game_mutation
from worldforge.game_scaffold import verify_game_runtime_snapshot
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.repository_boundary import (
    assert_new_repository_target,
    require_standalone_bundle_root,
    require_standalone_game_root,
)

__all__ = [
    "BUNDLE_FORMAT",
    "BUNDLE_FORMAT_VERSION",
    "BUNDLE_MANIFEST",
    "CATALOG_FORMAT",
    "CATALOG_FORMAT_VERSION",
    "WORLD_CATALOG",
    "BundleError",
    "VerifiedRuntimeBundle",
    "export_runtime_bundle",
    "import_runtime_bundle",
    "verify_game_catalog_compatibility",
    "verify_runtime_bundle",
]

BUNDLE_FORMAT = "isoworld.runtime_bundle"
BUNDLE_FORMAT_VERSION = 1
CATALOG_FORMAT = "isoworld.world_catalog"
CATALOG_FORMAT_VERSION = 1
BUNDLE_MANIFEST = "bundle.manifest.json"
WORLD_CATALOG = "game_data/worlds.lock.json"
SHARED_ASSET_LOCK = "game_data/shared.lock.json"
IMPORT_JOURNAL = "game_data/bundle-import.journal.json"
IMPORT_JOURNAL_FORMAT = "isoworld.bundle_import_journal"
IMPORT_JOURNAL_FORMAT_VERSION = 1

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WORLD_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
RELEASE_ID_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_BUNDLE_FILES = 100_000
MAX_BUNDLE_FILE_BYTES = 512 * 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024 * 1024
MAX_BUNDLE_PATH_BYTES = 1024
MAX_LICENSE_BYTES = 4 * 1024 * 1024
MAX_SHARED_FILES = 10_000
MAX_SHARED_BYTES = 8 * 1024 * 1024 * 1024

_WINDOWS_RESERVED_NAMES = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)

_MANIFEST_KEYS = frozenset(
    {
        "format",
        "format_version",
        "world_id",
        "release_id",
        "source_hashes",
        "worldpack",
        "renderpack",
        "required_runtime_features",
        "files",
        "licenses",
        "bundle_hash",
    }
)
_SOURCE_HASH_KEYS = frozenset({"worldpack_content_hash", "renderpack_content_hash"})
_IMPORT_JOURNAL_KEYS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "world_id",
        "release_id",
        "temporary",
        "destination",
        "bundle_hash",
        "directory_identity",
        "created_directories",
        "catalog_before_hash",
        "catalog_after_hash",
    }
)
_IDENTITY_KEYS = frozenset({"device", "inode"})
_CREATED_DIRECTORY_KEYS = frozenset({"path", "device", "inode"})
_WORLDPACK_KEYS = frozenset({"path", "format_version", "content_hash"})
_RENDERPACK_KEYS = frozenset({"path", "format_version", "content_hash", "world_content_hash"})
_FILE_KEYS = frozenset({"path", "sha256", "size", "media_type"})
_CATALOG_KEYS = frozenset({"format", "format_version", "releases"})
_RELEASE_KEYS = frozenset(
    {
        "world_id",
        "release_id",
        "bundle_hash",
        "path",
        "worldpack_hash",
        "renderpack_hash",
        "required_runtime_features",
    }
)
_SHARED_LOCK_KEYS = frozenset(
    {"format", "format_version", "files", "notices_sha256", "content_hash"}
)
_SHARED_MEDIA_TYPES = frozenset(
    {
        "application/json",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "font/otf",
        "font/ttf",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/x-glsl",
    }
)
_SHARED_EXTENSION_MEDIA_TYPES = {
    ".frag": "text/x-glsl",
    ".fs": "text/x-glsl",
    ".glsl": "text/x-glsl",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".json": "application/json",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".otf": "font/otf",
    ".png": "image/png",
    ".ttf": "font/ttf",
    ".vert": "text/x-glsl",
    ".vs": "text/x-glsl",
    ".wav": "audio/wav",
    ".webp": "image/webp",
}

_FORBIDDEN_PATH_COMPONENTS = frozenset(
    {
        ".agents",
        ".worldforge",
        "authoring",
        "bibles",
        "candidates",
        "claims",
        "credentials",
        "phase_reports",
        "prompts",
        "recipes",
        "receipts",
        "references",
        "requests",
        "source",
        "specs",
        "weights",
        "workflows",
        "work",
    }
)
_FORBIDDEN_RUNTIME_FILENAMES = frozenset(
    {
        ".cursorrules",
        "agents.md",
        "agents.override.md",
        "claude.md",
        "copilot-instructions.md",
        "gemini.md",
        "skill.md",
    }
)
_FORBIDDEN_JSON_KEYS = frozenset(
    {
        "api_key",
        "candidate_file",
        "checkpoint",
        "credentials",
        "extension_id",
        "extension_version",
        "model",
        "model_id",
        "model_name",
        "model_version",
        "negative_prompt",
        "provider",
        "provider_id",
        "recipe",
        "specification_file",
        "weights",
        "workflow",
        "workflow_file",
    }
)
_FORBIDDEN_JSON_FORMATS = frozenset(
    {
        "isoworld.source_manifest",
        "rpg-world-forge.asset_manifest",
        "rpg-world-forge.asset_inventory",
        "rpg-world-forge.asset_license_record",
        "rpg-world-forge.asset_processing_receipt",
        "rpg-world-forge.asset_processing_recipe",
        "rpg-world-forge.asset_production_receipt",
        "rpg-world-forge.asset_production_request",
        "rpg-world-forge.asset_qa_report",
        "rpg-world-forge.asset_spec",
        "rpg-world-forge.asset_target",
        "rpg-world-forge.audio_bible",
        "rpg-world-forge.narrative_analysis",
        "rpg-world-forge.phase_catalog",
        "rpg-world-forge.phase_report",
        "rpg-world-forge.project",
        "rpg-world-forge.reopen_log",
        "rpg-world-forge.task_claim",
        "rpg-world-forge.workflow_status",
        "rpg-world-forge.visual_bible",
    }
)
_MEDIA_EXTENSIONS = {
    "application/json": ".json",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "font/otf": ".otf",
    "font/ttf": ".ttf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "text/x-glsl": ".glsl",
}
_LICENSE_MEDIA_TYPES = {
    ".html": "text/html",
    ".json": "application/json",
    ".md": "text/markdown",
    ".rst": "text/plain",
    ".txt": "text/plain",
}


class BundleError(ValueError):
    """Raised when a runtime bundle cannot be exported, verified, or imported safely."""


@dataclass(frozen=True, slots=True)
class VerifiedRuntimeBundle:
    root: Path
    manifest: dict[str, Any]
    worldpack: WorldPack
    renderpack: RenderPack

    def close(self) -> None:
        self.renderpack.close()

    def __enter__(self) -> VerifiedRuntimeBundle:
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, traceback: object) -> None:
        try:
            self.close()
        except BaseException as cleanup_error:
            if exc is not None:
                exc.add_note(f"Runtime bundle cleanup failed: {cleanup_error}")
                return
            raise

    @property
    def world_id(self) -> str:
        return self.manifest["world_id"]

    @property
    def release_id(self) -> str:
        return self.manifest["release_id"]

    @property
    def bundle_hash(self) -> str:
        return self.manifest["bundle_hash"]


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path, *, limit: int, context: str) -> dict[str, Any]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise BundleError(f"{context} must be a regular file: {path}")
        if info.st_size > limit:
            raise BundleError(f"{context} exceeds the {limit}-byte limit: {path}")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except BundleError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise BundleError(f"Could not read {context} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BundleError(f"{context} must contain a JSON object: {path}")
    return value


def _pretty_json(value: dict[str, Any]) -> bytes:
    return canonical_json_bytes(value)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exact_keys(value: Any, expected: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleError(f"{context} must be an object")
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if extra:
            details.append(f"unexpected {extra}")
        raise BundleError(f"{context} has invalid fields: {', '.join(details)}")
    return value


def _valid_sha256(value: Any, context: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise BundleError(f"{context} must be a lowercase SHA-256 digest")
    return value


def _validate_world_id(value: Any, context: str = "world_id") -> str:
    if (
        not isinstance(value, str)
        or WORLD_ID_PATTERN.fullmatch(value) is None
        or not is_portable_path_component(value)
    ):
        raise BundleError(f"{context} is invalid")
    return value


def _validate_release_id(value: Any, context: str = "release_id") -> str:
    if (
        not isinstance(value, str)
        or RELEASE_ID_PATTERN.fullmatch(value) is None
        or len(value.encode("ascii")) > 64
    ):
        raise BundleError(f"{context} must be an immutable MAJOR.MINOR.PATCH release ID")
    return value


def _relative_posix_path(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise BundleError(f"{context} must be a non-empty relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BundleError(f"{context} is not a normalized contained POSIX path: {value!r}")
    if (
        unicodedata.normalize("NFC", value) != value
        or len(value.encode("utf-8")) > MAX_BUNDLE_PATH_BYTES
    ):
        raise BundleError(f"{context} is not a portable normalized path: {value!r}")
    for component in path.parts:
        encoded = component.encode("utf-8")
        device_name = component.split(".", 1)[0].casefold()
        if (
            len(encoded) > 255
            or component.endswith((" ", "."))
            or any(ord(character) < 32 or character in '<>:"|?*' for character in component)
            or device_name in _WINDOWS_RESERVED_NAMES
        ):
            raise BundleError(f"{context} is not portable across game platforms: {value!r}")
    return value


def _bundle_payload_path(value: Any, context: str) -> str:
    relative = _relative_posix_path(value, context)
    parts = PurePosixPath(relative).parts
    if relative in {"worldpack.json", "renderpack.json"}:
        return relative
    if len(parts) < 2 or parts[0] not in {"assets", "licenses"}:
        raise BundleError(f"{context} is outside the runtime bundle roots: {relative}")
    for component in parts:
        folded = component.casefold()
        stem = PurePosixPath(component).stem.casefold()
        if (
            folded.startswith(".")
            or folded in _FORBIDDEN_PATH_COMPONENTS
            or stem in _FORBIDDEN_PATH_COMPONENTS
            or folded in _FORBIDDEN_RUNTIME_FILENAMES
        ):
            raise BundleError(f"{context} exposes an authoring-only path: {relative}")
    return relative


def _regular_source(root: Path, relative: str, context: str) -> Path:
    normalized = _relative_posix_path(relative, context)
    cursor = root
    for index, part in enumerate(PurePosixPath(normalized).parts):
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise BundleError(f"{context} is missing: {relative}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise BundleError(f"{context} may not use symlinks: {relative}")
        if index < len(PurePosixPath(normalized).parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                raise BundleError(f"{context} has a non-directory parent: {relative}")
        elif not stat.S_ISREG(info.st_mode):
            raise BundleError(f"{context} must be a regular file: {relative}")
    return cursor


def _walk_regular_files(root: Path, context: str) -> tuple[list[Path], set[str]]:
    if root.is_symlink() or not root.is_dir():
        raise BundleError(f"{context} must be a real directory: {root}")
    files: list[Path] = []
    directories: set[str] = set()
    total_size = 0
    for current, names, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        names.sort()
        filenames.sort()
        for name in names:
            path = current_path / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise BundleError(f"{context} contains a symlink or special entry: {path}")
            directories.add(path.relative_to(root).as_posix())
            if len(directories) > MAX_BUNDLE_FILES * 4:
                raise BundleError(f"{context} contains too many directories")
        for name in filenames:
            path = current_path / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise BundleError(f"{context} contains a symlink or special file: {path}")
            if info.st_nlink != 1:
                raise BundleError(f"{context} contains a mutable hard-linked file: {path}")
            if info.st_size > MAX_BUNDLE_FILE_BYTES:
                raise BundleError(f"{context} contains an oversized file: {path}")
            total_size += info.st_size
            if total_size > MAX_BUNDLE_BYTES:
                raise BundleError(f"{context} exceeds the total bundle byte limit")
            files.append(path)
            if len(files) > MAX_BUNDLE_FILES + 1:
                raise BundleError(f"{context} contains too many files")
    return files, directories


def _scan_json_runtime_boundary(value: Any, context: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in _FORBIDDEN_JSON_KEYS:
                raise BundleError(f"{context} contains authoring/provider metadata key {key!r}")
            if key == "format" and isinstance(child, str) and child in _FORBIDDEN_JSON_FORMATS:
                raise BundleError(f"{context} contains authoring-only format {child!r}")
            _scan_json_runtime_boundary(child, context)
    elif isinstance(value, list):
        for child in value:
            _scan_json_runtime_boundary(child, context)


def _validate_worldpack_envelope(raw: dict[str, Any], context: str) -> None:
    expected = {"format", "format_version", "world", "collections", "content_hash"}
    version = raw.get("format_version")
    if isinstance(version, int) and not isinstance(version, bool) and version >= 5:
        expected.add("runtime_requirements")
    _exact_keys(raw, frozenset(expected), context)


def _validate_renderpack_shape(raw: dict[str, Any], context: str) -> None:
    _exact_keys(
        raw,
        frozenset(
            {
                "format",
                "format_version",
                "world_id",
                "world_content_hash",
                "assets",
                "bindings",
                "content_hash",
            }
        ),
        context,
    )
    assets = raw.get("assets")
    if not isinstance(assets, list):
        raise BundleError(f"{context}/assets must be a list")
    for asset_index, asset in enumerate(assets):
        value = _exact_keys(
            asset,
            frozenset({"id", "kind", "files"}),
            f"{context}/assets/{asset_index}",
        )
        files = value["files"]
        if not isinstance(files, list):
            raise BundleError(f"{context}/assets/{asset_index}/files must be a list")
        for file_index, item in enumerate(files):
            _exact_keys(
                item,
                frozenset({"role", "path", "sha256", "media_type"}),
                f"{context}/assets/{asset_index}/files/{file_index}",
            )
    bindings = raw.get("bindings")
    if not isinstance(bindings, list):
        raise BundleError(f"{context}/bindings must be a list")
    allowed_binding = frozenset({"slot", "asset_id", "clip", "moving_clip", "scale", "layer"})
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict):
            raise BundleError(f"{context}/bindings/{index} must be an object")
        if not {"slot", "asset_id"} <= set(binding) or not set(binding) <= allowed_binding:
            raise BundleError(f"{context}/bindings/{index} has invalid fields")


def _validate_clipset_shape(raw: dict[str, Any], context: str) -> None:
    _exact_keys(raw, frozenset({"format", "format_version", "clips"}), context)
    clips = raw.get("clips")
    if not isinstance(clips, list):
        raise BundleError(f"{context}/clips must be a list")
    for clip_index, clip in enumerate(clips):
        value = _exact_keys(
            clip,
            frozenset({"id", "pivot", "loop", "frames"}),
            f"{context}/clips/{clip_index}",
        )
        frames = value["frames"]
        if not isinstance(frames, list):
            raise BundleError(f"{context}/clips/{clip_index}/frames must be a list")
        for frame_index, frame in enumerate(frames):
            _exact_keys(
                frame,
                frozenset({"x", "y", "width", "height", "duration_ticks"}),
                f"{context}/clips/{clip_index}/frames/{frame_index}",
            )


def _runtime_features(worldpack_raw: dict[str, Any]) -> list[str]:
    requirements = worldpack_raw.get("runtime_requirements")
    if requirements is None:
        # Versions before v5 had capabilities but no formal compatibility contract.
        return []
    requirements = _exact_keys(
        requirements,
        frozenset({"runtime_api", "required_features", "optional_features"}),
        "runtime_requirements",
    )
    _exact_keys(
        requirements["runtime_api"],
        frozenset({"minimum", "maximum_exclusive"}),
        "runtime_requirements/runtime_api",
    )
    capabilities = requirements.get("required_features")
    context = "runtime_requirements/required_features"
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) and WORLD_ID_PATTERN.fullmatch(item) for item in capabilities
    ):
        raise BundleError(f"{context} must be a list of runtime feature IDs")
    if capabilities != sorted(set(capabilities)):
        raise BundleError(f"{context} must be sorted and contain no duplicates")
    optional = requirements["optional_features"]
    if (
        not isinstance(optional, list)
        or not all(isinstance(item, str) and WORLD_ID_PATTERN.fullmatch(item) for item in optional)
        or optional != sorted(set(optional))
    ):
        raise BundleError(
            "runtime_requirements/optional_features must be sorted unique runtime feature IDs"
        )
    return list(capabilities)


def _asset_extension(role: str, media_type: str) -> str:
    if role == "clipset" and media_type == "application/json":
        return ".clips.json"
    try:
        return _MEDIA_EXTENSIONS[media_type]
    except KeyError as exc:
        raise BundleError(f"Unsupported runtime media type: {media_type}") from exc


def _license_media_type(path: Path) -> str:
    try:
        return _LICENSE_MEDIA_TYPES[path.suffix.casefold()]
    except KeyError as exc:
        raise BundleError(f"Unsupported license notice extension: {path.name}") from exc


def _validate_license_record(record: dict[str, Any], context: str) -> None:
    path = PurePosixPath(record["path"])
    expected_media_type = _LICENSE_MEDIA_TYPES.get(path.suffix.casefold())
    if expected_media_type is None or record["media_type"] != expected_media_type:
        raise BundleError(f"{context} must use an approved license notice format")
    if record["size"] > MAX_LICENSE_BYTES:
        raise BundleError(f"{context} exceeds the {MAX_LICENSE_BYTES}-byte license limit")


def _verify_license_payload(root: Path, record: dict[str, Any]) -> None:
    relative = record["path"]
    path = root / PurePosixPath(relative)
    if record["media_type"] == "application/json":
        document = _read_json(
            path,
            limit=MAX_LICENSE_BYTES,
            context=f"license inventory file {relative}",
        )
        _scan_json_runtime_boundary(document, relative)
        return
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BundleError(f"License notice is not UTF-8 text: {relative}") from exc
    if "\x00" in text:
        raise BundleError(f"License notice contains NUL bytes: {relative}")


def _file_record(root: Path, relative: str, media_type: str) -> dict[str, Any]:
    path = root / PurePosixPath(relative)
    size = path.stat().st_size
    return {
        "path": relative,
        "sha256": _sha256_file(path),
        "size": size,
        "media_type": media_type,
    }


def _canonical_bundle_hash(manifest: dict[str, Any]) -> str:
    return canonical_payload_hash(manifest, hash_field="bundle_hash")


def _validate_file_records(raw: Any, context: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise BundleError(f"{context} must be a non-empty list")
    if len(raw) > MAX_BUNDLE_FILES:
        raise BundleError(f"{context} exceeds the {MAX_BUNDLE_FILES}-file limit")
    records: list[dict[str, Any]] = []
    paths: set[str] = set()
    folded_paths: set[str] = set()
    folded_prefixes: dict[str, str] = {}
    total_size = 0
    for index, value in enumerate(raw):
        record = _exact_keys(value, _FILE_KEYS, f"{context}/{index}")
        relative = _bundle_payload_path(record["path"], f"{context}/{index}/path")
        if relative in paths:
            raise BundleError(f"{context} contains duplicate path {relative}")
        if relative.casefold() in folded_paths:
            raise BundleError(f"{context} contains a case-insensitive path collision: {relative}")
        paths.add(relative)
        folded_paths.add(relative.casefold())
        prefix: list[str] = []
        for component in PurePosixPath(relative).parts:
            prefix.append(component)
            exact_prefix = "/".join(prefix)
            folded_prefix = exact_prefix.casefold()
            previous = folded_prefixes.setdefault(folded_prefix, exact_prefix)
            if previous != exact_prefix:
                raise BundleError(
                    f"{context} contains a case-insensitive prefix collision: "
                    f"{previous!r} and {exact_prefix!r}"
                )
        _valid_sha256(record["sha256"], f"{context}/{index}/sha256")
        size = record["size"]
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 <= size <= MAX_BUNDLE_FILE_BYTES
        ):
            raise BundleError(f"{context}/{index}/size must be in 0..{MAX_BUNDLE_FILE_BYTES}")
        media_type = record["media_type"]
        if (
            not isinstance(media_type, str)
            or not media_type
            or any(character.isspace() for character in media_type)
        ):
            raise BundleError(f"{context}/{index}/media_type is invalid")
        total_size += size
        if total_size > MAX_BUNDLE_BYTES:
            raise BundleError(f"{context} exceeds the total bundle byte limit")
        records.append(record)
    if [record["path"] for record in records] != sorted(paths):
        raise BundleError(f"{context} must be sorted by path")
    return records


def _validate_manifest_runtime_metadata(manifest: dict[str, Any]) -> None:
    """Validate manifest fields derived only from loaded runtime documents."""

    if manifest["format"] != BUNDLE_FORMAT or manifest["format_version"] != BUNDLE_FORMAT_VERSION:
        raise BundleError("Unknown runtime bundle format")
    _validate_world_id(manifest["world_id"])
    _validate_release_id(manifest["release_id"])
    source_hashes = _exact_keys(manifest["source_hashes"], _SOURCE_HASH_KEYS, "source_hashes")
    _valid_sha256(source_hashes["worldpack_content_hash"], "source_hashes/worldpack_content_hash")
    _valid_sha256(source_hashes["renderpack_content_hash"], "source_hashes/renderpack_content_hash")

    worldpack = _exact_keys(manifest["worldpack"], _WORLDPACK_KEYS, "worldpack")
    if worldpack["path"] != "worldpack.json":
        raise BundleError("worldpack/path must be worldpack.json")
    if isinstance(worldpack["format_version"], bool) or not isinstance(
        worldpack["format_version"], int
    ):
        raise BundleError("worldpack/format_version must be an integer")
    _valid_sha256(worldpack["content_hash"], "worldpack/content_hash")

    renderpack = _exact_keys(manifest["renderpack"], _RENDERPACK_KEYS, "renderpack")
    if renderpack["path"] != "renderpack.json" or renderpack["format_version"] != 1:
        raise BundleError("renderpack metadata has an unsupported path or format version")
    _valid_sha256(renderpack["content_hash"], "renderpack/content_hash")
    _valid_sha256(renderpack["world_content_hash"], "renderpack/world_content_hash")

    features = manifest["required_runtime_features"]
    if (
        not isinstance(features, list)
        or not all(isinstance(item, str) and WORLD_ID_PATTERN.fullmatch(item) for item in features)
        or features != sorted(set(features))
    ):
        raise BundleError("required_runtime_features must be a sorted unique list of IDs")


def _validate_manifest(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = _exact_keys(raw, _MANIFEST_KEYS, "bundle manifest")
    _validate_manifest_runtime_metadata(manifest)
    files = _validate_file_records(manifest["files"], "files")
    licenses = _validate_file_records(manifest["licenses"], "licenses")
    if any(not record["path"].startswith("licenses/") for record in licenses):
        raise BundleError("license inventory may contain only licenses/** files")
    for index, record in enumerate(licenses):
        _validate_license_record(record, f"licenses/{index}")
    license_files = [record for record in files if record["path"].startswith("licenses/")]
    if licenses != license_files:
        raise BundleError("license inventory must exactly match licenses/** file records")
    _valid_sha256(manifest["bundle_hash"], "bundle_hash")
    if _canonical_bundle_hash(manifest) != manifest["bundle_hash"]:
        raise BundleError("The canonical bundle hash does not match the manifest")
    return files, licenses


def _load_runtime_payload_sources(
    worldpack_path: Path,
    renderpack_path: Path,
) -> tuple[WorldPack, RenderPack, dict[str, Any], dict[str, Any]]:
    if worldpack_path.is_symlink() or renderpack_path.is_symlink():
        raise BundleError("Worldpack and renderpack inputs may not be symlinks")
    worldpack_raw = _read_json(
        worldpack_path,
        limit=64 * 1024 * 1024,
        context="source worldpack",
    )
    renderpack_raw = _read_json(
        renderpack_path,
        limit=16 * 1024 * 1024,
        context="source renderpack",
    )
    _scan_json_runtime_boundary(worldpack_raw, "source worldpack")
    _scan_json_runtime_boundary(renderpack_raw, "source renderpack")
    _validate_worldpack_envelope(worldpack_raw, "source worldpack")
    _validate_renderpack_shape(renderpack_raw, "source renderpack")
    _runtime_features(worldpack_raw)
    try:
        source_worldpack = load_worldpack(worldpack_path)
        source_renderpack = load_renderpack(renderpack_path, source_worldpack)
    except (WorldPackError, RenderPackError) as exc:
        raise BundleError(f"Source runtime content is invalid: {exc}") from exc
    return source_worldpack, source_renderpack, worldpack_raw, renderpack_raw


def _stage_runtime_payload(
    worldpack_path: Path,
    renderpack_path: Path,
    stage: Path,
    *,
    validation_root: Path | None = None,
) -> tuple[WorldPack, RenderPack, dict[str, Any], dict[str, Any], dict[str, str]]:
    source_worldpack, source_renderpack, worldpack_raw, renderpack_raw = (
        _load_runtime_payload_sources(worldpack_path, renderpack_path)
    )
    try:
        result = _stage_runtime_payload_from_loaded(
            stage,
            source_worldpack,
            source_renderpack,
            worldpack_raw,
            renderpack_raw,
            validation_root=validation_root,
        )
    except BaseException as original_error:
        try:
            source_renderpack.close()
        except RenderPackError as cleanup_error:
            original_error.add_note(f"Source renderpack cleanup failed: {cleanup_error}")
        raise
    else:
        source_renderpack.close()
        return result


def _stage_runtime_payload_from_loaded(
    stage: Path,
    source_worldpack: WorldPack,
    source_renderpack: RenderPack,
    worldpack_raw: dict[str, Any],
    renderpack_raw: dict[str, Any],
    *,
    validation_root: Path | None = None,
) -> tuple[WorldPack, RenderPack, dict[str, Any], dict[str, Any], dict[str, str]]:
    read_root = stage if validation_root is None else validation_root
    canonical_worldpack = _pretty_json(worldpack_raw)
    (stage / "worldpack.json").write_bytes(canonical_worldpack)

    bundled_renderpack = copy.deepcopy(renderpack_raw)
    assets = bundled_renderpack.get("assets")
    if not isinstance(assets, list):
        raise BundleError("Source renderpack assets must be a list")
    source_root = source_renderpack.root
    asset_media: dict[str, str] = {}
    for raw_asset in sorted(
        assets,
        key=lambda item: item.get("id", "") if isinstance(item, dict) else "",
    ):
        if not isinstance(raw_asset, dict) or not isinstance(raw_asset.get("id"), str):
            raise BundleError("Source renderpack contains an invalid asset")
        raw_files = raw_asset.get("files")
        if not isinstance(raw_files, list):
            raise BundleError(f"Asset {raw_asset['id']} files must be a list")
        raw_files.sort(
            key=lambda item: (
                item.get("role", "") if isinstance(item, dict) else "",
                item.get("path", "") if isinstance(item, dict) else "",
            )
        )
        for index, raw_file in enumerate(raw_files):
            if not isinstance(raw_file, dict):
                raise BundleError(f"Asset {raw_asset['id']} contains invalid file metadata")
            role = raw_file.get("role")
            media_type = raw_file.get("media_type")
            source_relative = raw_file.get("path")
            if not isinstance(role, str) or not isinstance(media_type, str):
                raise BundleError(f"Asset {raw_asset['id']} has invalid role/media type")
            source = _regular_source(
                source_root,
                source_relative,
                f"renderpack asset {raw_asset['id']}",
            )
            extension = _asset_extension(role, media_type)
            destination_relative = (
                PurePosixPath("assets") / raw_asset["id"] / f"{index:02d}_{role}{extension}"
            ).as_posix()
            _bundle_payload_path(destination_relative, "renderpack asset destination")
            destination = stage / PurePosixPath(destination_relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            validation_path = read_root / PurePosixPath(destination_relative)
            if _sha256_file(validation_path) != raw_file.get("sha256"):
                raise BundleError(f"Asset changed while exporting: {source_relative}")
            if not media_signature_matches(validation_path, media_type):
                raise BundleError(
                    f"Asset contents do not match declared media type: {source_relative}"
                )
            raw_file["path"] = destination_relative
            asset_media[destination_relative] = media_type
    assets.sort(key=lambda item: item["id"])
    bindings = bundled_renderpack.get("bindings")
    if isinstance(bindings, list):
        bindings.sort(key=lambda item: item.get("slot", "") if isinstance(item, dict) else "")
    bundled_renderpack["content_hash"] = canonical_payload_hash(bundled_renderpack)
    (stage / "renderpack.json").write_bytes(_pretty_json(bundled_renderpack))

    try:
        bundled_worldpack = load_worldpack(read_root / "worldpack.json")
        bundled_render = load_renderpack(read_root / "renderpack.json", bundled_worldpack)
    except (WorldPackError, RenderPackError) as exc:
        raise BundleError(f"Staged runtime content is invalid: {exc}") from exc
    return (
        bundled_worldpack,
        bundled_render,
        worldpack_raw,
        renderpack_raw,
        asset_media,
    )


def _license_sources(licenses_directory: Path) -> list[tuple[Path, str, str]]:
    files, _ = _walk_regular_files(licenses_directory, "license directory")
    if not files:
        raise BundleError("The runtime bundle requires at least one license file")
    if len(files) > MAX_BUNDLE_FILES - 3:
        raise BundleError("The license directory contains too many files for one bundle")
    result: list[tuple[Path, str, str]] = []
    folded_paths: set[str] = set()
    for source in files:
        relative_source = source.relative_to(licenses_directory).as_posix()
        relative = f"licenses/{relative_source}"
        _bundle_payload_path(relative, "license path")
        if relative.casefold() in folded_paths:
            raise BundleError(f"License paths collide on case-insensitive platforms: {relative}")
        folded_paths.add(relative.casefold())
        media_type = _license_media_type(source)
        info = source.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BundleError(f"license directory contains a mutable or special file: {source}")
        record = {
            "path": relative,
            "sha256": "0" * 64,
            "size": info.st_size,
            "media_type": media_type,
        }
        _validate_license_record(record, f"license source {relative}")
        if media_type == "application/json":
            document = _read_json(
                source,
                limit=MAX_LICENSE_BYTES,
                context=f"license source {relative}",
            )
            _scan_json_runtime_boundary(document, relative)
            result.append((source, relative, media_type))
            continue
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise BundleError(f"License notice is not UTF-8 text: {relative}") from exc
        if "\x00" in text:
            raise BundleError(f"License notice contains NUL bytes: {relative}")
        result.append((source, relative, media_type))
    return result


def _copy_licenses(
    licenses_directory: Path,
    stage: Path,
    *,
    validation_root: Path | None = None,
) -> dict[str, str]:
    read_root = stage if validation_root is None else validation_root
    sources = _license_sources(licenses_directory)
    result: dict[str, str] = {}
    for source, relative, media_type in sources:
        destination = stage / PurePosixPath(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        result[relative] = media_type
        _verify_license_payload(
            read_root,
            _file_record(read_root, relative, media_type),
        )
    return result


def _preflight_runtime_bundle_inputs(
    worldpack_path: Path,
    renderpack_path: Path,
    licenses_directory: Path,
    release_id: str,
) -> None:
    """Reject all source-contract errors before allocating a private export stage."""

    source_worldpack, source_renderpack, worldpack_raw, renderpack_raw = (
        _load_runtime_payload_sources(
            worldpack_path,
            renderpack_path,
        )
    )
    try:
        _validate_manifest_runtime_metadata(
            {
                "format": BUNDLE_FORMAT,
                "format_version": BUNDLE_FORMAT_VERSION,
                "world_id": source_worldpack.world_id,
                "release_id": release_id,
                "source_hashes": {
                    "worldpack_content_hash": source_worldpack.content_hash,
                    "renderpack_content_hash": renderpack_raw["content_hash"],
                },
                "worldpack": {
                    "path": "worldpack.json",
                    "format_version": source_worldpack.format_version,
                    "content_hash": source_worldpack.content_hash,
                },
                "renderpack": {
                    "path": "renderpack.json",
                    "format_version": renderpack_raw["format_version"],
                    "content_hash": source_renderpack.content_hash,
                    "world_content_hash": source_renderpack.world_content_hash,
                },
                "required_runtime_features": _runtime_features(worldpack_raw),
            }
        )
        _license_sources(licenses_directory)
    except BaseException as original_error:
        try:
            source_renderpack.close()
        except RenderPackError as cleanup_error:
            original_error.add_note(f"Source renderpack cleanup failed: {cleanup_error}")
        raise
    else:
        source_renderpack.close()


def _populate_runtime_bundle(
    source_worldpack_path: Path,
    source_renderpack_path: Path,
    source_licenses_directory: Path,
    output_root: Path,
    release_id: str,
    *,
    validation_root: Path | None = None,
) -> dict[str, Any]:
    read_root = output_root if validation_root is None else validation_root
    worldpack, renderpack, worldpack_raw, source_renderpack_raw, asset_media = (
        _stage_runtime_payload(
            source_worldpack_path,
            source_renderpack_path,
            output_root,
            validation_root=read_root,
        )
    )
    bundled_renderpack_hash = renderpack.content_hash
    bundled_world_content_hash = renderpack.world_content_hash
    renderpack.close()
    license_media = _copy_licenses(
        source_licenses_directory,
        output_root,
        validation_root=read_root,
    )
    media_types = {
        "worldpack.json": "application/json",
        "renderpack.json": "application/json",
        **asset_media,
        **license_media,
    }
    files = [
        _file_record(read_root, relative, media_types[relative]) for relative in sorted(media_types)
    ]
    licenses = [record for record in files if record["path"].startswith("licenses/")]
    manifest: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "format_version": BUNDLE_FORMAT_VERSION,
        "world_id": worldpack.world_id,
        "release_id": release_id,
        "source_hashes": {
            "worldpack_content_hash": worldpack.content_hash,
            "renderpack_content_hash": source_renderpack_raw["content_hash"],
        },
        "worldpack": {
            "path": "worldpack.json",
            "format_version": worldpack.format_version,
            "content_hash": worldpack.content_hash,
        },
        "renderpack": {
            "path": "renderpack.json",
            "format_version": source_renderpack_raw["format_version"],
            "content_hash": bundled_renderpack_hash,
            "world_content_hash": bundled_world_content_hash,
        },
        "required_runtime_features": _runtime_features(worldpack_raw),
        "files": files,
        "licenses": licenses,
    }
    manifest["bundle_hash"] = _canonical_bundle_hash(manifest)
    (output_root / BUNDLE_MANIFEST).write_bytes(_pretty_json(manifest))
    return manifest


def _payload_file_state(info: Any) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _close_payload_descriptor(descriptor: int, *, context: str) -> None:
    primary = sys.exception()
    try:
        os.close(descriptor)
    except OSError as cleanup_error:
        detail = f"{context} failed: {cleanup_error}"
        if primary is not None:
            primary.add_note(detail)
        else:
            raise BundleError(detail) from cleanup_error


def _fsync_regular_payload(descriptor: int, relative: str) -> None:
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise BundleError(f"Could not durably flush bundle payload {relative}: {exc}") from exc


def _claimed_directory_entries(directory_descriptor: int) -> list[tuple[str, Any]]:
    try:
        with os.scandir(directory_descriptor) as entries:
            return sorted(
                (
                    entry.name,
                    entry.stat(follow_symlinks=False),
                )
                for entry in entries
            )
    except OSError as exc:
        raise BundleError(f"Could not inspect claimed bundle payload tree: {exc}") from exc


def _fsync_claimed_payload_tree(
    directory_descriptor: int,
    *,
    prefix: str = "",
) -> tuple[str, ...]:
    entries = _claimed_directory_entries(directory_descriptor)
    before = {name: _payload_file_state(info) for name, info in entries}
    flushed: list[str] = []
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for name, named_before in entries:
        relative = f"{prefix}/{name}" if prefix else name
        if is_link_or_reparse(named_before):
            raise BundleError(f"Claimed bundle payload became link-like: {relative}")
        descriptor: int | None = None
        if stat.S_ISREG(named_before.st_mode):
            if named_before.st_nlink != 1:
                raise BundleError(f"Claimed bundle payload has multiple links: {relative}")
            try:
                descriptor = os.open(
                    name,
                    file_flags,
                    dir_fd=directory_descriptor,
                )
                opened = descriptor_file_stat(descriptor)
                if (
                    is_link_or_reparse(opened)
                    or not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1
                    or _payload_file_state(opened) != _payload_file_state(named_before)
                ):
                    raise BundleError(f"Claimed bundle payload identity changed: {relative}")
                _fsync_regular_payload(descriptor, relative)
                after = descriptor_file_stat(descriptor)
                named_after = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if _payload_file_state(after) != _payload_file_state(opened) or _payload_file_state(
                    named_after
                ) != _payload_file_state(opened):
                    raise BundleError(f"Claimed bundle payload changed while flushing: {relative}")
                flushed.append(relative)
            except BundleError:
                raise
            except OSError as exc:
                raise BundleError(
                    f"Could not retain claimed bundle payload {relative}: {exc}"
                ) from exc
            finally:
                if descriptor is not None:
                    _close_payload_descriptor(
                        descriptor,
                        context=f"Claimed bundle payload descriptor cleanup for {relative}",
                    )
            continue
        if not stat.S_ISDIR(named_before.st_mode):
            raise BundleError(f"Claimed bundle payload is not a regular file: {relative}")
        try:
            descriptor = os.open(
                name,
                directory_flags,
                dir_fd=directory_descriptor,
            )
            opened = descriptor_file_stat(descriptor)
            if (
                is_link_or_reparse(opened)
                or not stat.S_ISDIR(opened.st_mode)
                or file_identity(opened) != file_identity(named_before)
            ):
                raise BundleError(f"Claimed bundle directory identity changed: {relative}")
            flushed.extend(
                _fsync_claimed_payload_tree(
                    descriptor,
                    prefix=relative,
                )
            )
            try:
                os.fsync(descriptor)
            except OSError as exc:
                raise BundleError(
                    f"Could not durably flush claimed bundle directory {relative}: {exc}"
                ) from exc
            after = descriptor_file_stat(descriptor)
            named_after = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                file_identity(after) != file_identity(opened)
                or file_identity(named_after) != file_identity(opened)
                or not stat.S_ISDIR(after.st_mode)
                or is_link_or_reparse(named_after)
            ):
                raise BundleError(f"Claimed bundle directory changed while flushing: {relative}")
        except BundleError:
            raise
        except OSError as exc:
            raise BundleError(
                f"Could not retain claimed bundle directory {relative}: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                _close_payload_descriptor(
                    descriptor,
                    context=f"Claimed bundle directory descriptor cleanup for {relative}",
                )

    after = {
        name: _payload_file_state(info)
        for name, info in _claimed_directory_entries(directory_descriptor)
    }
    if after != before:
        raise BundleError("Claimed bundle payload tree changed while flushing")
    return tuple(flushed)


def export_runtime_bundle(
    worldpack_path: str | Path,
    renderpack_path: str | Path,
    destination: str | Path,
    *,
    release_id: str,
    licenses_directory: str | Path,
) -> VerifiedRuntimeBundle:
    """Export verified runtime inputs into a new, immutable, content-addressed bundle."""

    release_id = _validate_release_id(release_id)
    try:
        destination_path = assert_new_repository_target(
            destination,
            repository_type="bundle",
        )
    except ValueError as exc:
        raise BundleError(str(exc)) from exc
    source_worldpack_path = Path(worldpack_path)
    source_renderpack_path = Path(renderpack_path)
    source_licenses_directory = Path(licenses_directory)
    _preflight_runtime_bundle_inputs(
        source_worldpack_path,
        source_renderpack_path,
        source_licenses_directory,
        release_id,
    )
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    stage = destination_path.parent / f".{destination_path.name}.export-{uuid.uuid4().hex}"
    if stage.exists():
        raise BundleError(f"Temporary export path unexpectedly exists: {stage}")
    stage.mkdir()
    stage_identity = directory_identity(stage, context="bundle export stage")
    installed = False
    verified: VerifiedRuntimeBundle | None = None
    published_verified: VerifiedRuntimeBundle | None = None
    try:
        manifest = _populate_runtime_bundle(
            source_worldpack_path,
            source_renderpack_path,
            source_licenses_directory,
            stage,
            release_id,
        )
        with open_expected_directory(stage, stage_identity) as retained:
            flushed_payloads = _fsync_claimed_payload_tree(retained.fd)
            expected_payloads = {
                BUNDLE_MANIFEST,
                *(record["path"] for record in manifest["files"]),
            }
            if (
                len(flushed_payloads) != len(set(flushed_payloads))
                or set(flushed_payloads) != expected_payloads
            ):
                raise BundleError(
                    "Durably flushed bundle payload inventory does not match the manifest"
                )
            os.fsync(retained.fd)
            os.fsync(retained.parent_fd)
            retained.require_binding()
        verified = verify_runtime_bundle(stage, expected_bundle_hash=manifest["bundle_hash"])
        verified.close()
        verified = None
        try:
            published_identity = publish_directory_noreplace(
                stage,
                destination_path,
                expected_source_identity=stage_identity,
            )
        except FileExistsError as exc:
            raise BundleError(f"Bundle destination already exists: {destination_path}") from exc
        except DirectoryPublishError as exc:
            raise BundleError(str(exc)) from exc
        published_verified = verify_runtime_bundle(
            destination_path,
            expected_bundle_hash=manifest["bundle_hash"],
        )
        if (
            directory_identity(destination_path, context="published bundle export")
            != published_identity
        ):
            raise BundleError("Published bundle export identity changed during verification")
        installed = True
        result = published_verified
        published_verified = None
        return result
    except Exception as original_error:
        snapshot_cleanup_errors: list[RenderPackError] = []
        for snapshot in (verified, published_verified):
            if snapshot is None:
                continue
            try:
                snapshot.close()
            except RenderPackError as cleanup_error:
                snapshot_cleanup_errors.append(cleanup_error)
        if not installed and (stage.exists() or stage.is_symlink()):
            original_error.add_note(
                f"Incomplete private bundle export stage retained for explicit inspection: {stage}"
            )
        if snapshot_cleanup_errors:
            original_error.add_note(
                "Bundle export verified snapshot cleanup could not complete: "
                + "; ".join(str(error) for error in snapshot_cleanup_errors)
            )
        raise


def verify_runtime_bundle(
    bundle_path: str | Path,
    *,
    expected_bundle_hash: str | None = None,
) -> VerifiedRuntimeBundle:
    """Verify a bundle; the returned context owns a private renderpack snapshot."""

    root = Path(bundle_path).absolute()
    files_on_disk, directories_on_disk = _walk_regular_files(root, "runtime bundle")
    manifest_path = root / BUNDLE_MANIFEST
    if manifest_path not in files_on_disk:
        raise BundleError(f"Runtime bundle is missing {BUNDLE_MANIFEST}")
    manifest = _read_json(
        manifest_path,
        limit=MAX_MANIFEST_BYTES,
        context="bundle manifest",
    )
    records, _ = _validate_manifest(manifest)
    if expected_bundle_hash is not None:
        _valid_sha256(expected_bundle_hash, "expected_bundle_hash")
        if manifest["bundle_hash"] != expected_bundle_hash:
            raise BundleError("Runtime bundle does not match the expected immutable hash")

    expected_files = {BUNDLE_MANIFEST, *(record["path"] for record in records)}
    actual_files = {path.relative_to(root).as_posix() for path in files_on_disk}
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise BundleError(f"Bundle tree mismatch; missing={missing}, extra={extra}")
    expected_directories = {
        parent.as_posix()
        for relative in expected_files
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    if directories_on_disk != expected_directories:
        missing = sorted(expected_directories - directories_on_disk)
        extra = sorted(directories_on_disk - expected_directories)
        raise BundleError(f"Bundle directory tree mismatch; missing={missing}, extra={extra}")

    records_by_path = {record["path"]: record for record in records}
    for required_json in ("worldpack.json", "renderpack.json"):
        record = records_by_path.get(required_json)
        if record is None or record["media_type"] != "application/json":
            raise BundleError(f"{required_json} must be inventoried as application/json")
    for relative, record in records_by_path.items():
        path = root / PurePosixPath(relative)
        size = path.stat().st_size
        if size != record["size"]:
            raise BundleError(f"Bundle file size mismatch: {relative}")
        if _sha256_file(path) != record["sha256"]:
            raise BundleError(f"Bundle file hash mismatch: {relative}")
        if relative.startswith("assets/") and not media_signature_matches(
            path,
            record["media_type"],
        ):
            raise BundleError(f"Bundle asset contents do not match declared media type: {relative}")
    for record in manifest["licenses"]:
        _verify_license_payload(root, record)

    if manifest_path.read_bytes() != _pretty_json(manifest):
        raise BundleError("Bundle manifest is not canonically serialized")

    worldpack_raw = _read_json(
        root / "worldpack.json",
        limit=64 * 1024 * 1024,
        context="bundled worldpack",
    )
    renderpack_raw = _read_json(
        root / "renderpack.json",
        limit=16 * 1024 * 1024,
        context="bundled renderpack",
    )
    _scan_json_runtime_boundary(worldpack_raw, "bundled worldpack")
    _scan_json_runtime_boundary(renderpack_raw, "bundled renderpack")
    _validate_worldpack_envelope(worldpack_raw, "bundled worldpack")
    _validate_renderpack_shape(renderpack_raw, "bundled renderpack")
    try:
        worldpack = load_worldpack(root / "worldpack.json")
        renderpack = load_renderpack(root / "renderpack.json", worldpack)
    except (WorldPackError, RenderPackError) as exc:
        raise BundleError(f"Bundled runtime content is invalid: {exc}") from exc
    try:
        return _finish_runtime_bundle_verification(
            root,
            manifest,
            records_by_path,
            worldpack_raw,
            worldpack,
            renderpack,
        )
    except Exception as original:
        cleanup_error: BaseException | None = None
        try:
            renderpack.close()
        except BaseException as exc:
            cleanup_error = exc
            original.add_note(f"Bundle verification snapshot cleanup failed: {exc}")
        if cleanup_error is not None:
            raise original from cleanup_error
        raise


def _finish_runtime_bundle_verification(
    root: Path,
    manifest: dict[str, Any],
    records_by_path: dict[str, dict[str, Any]],
    worldpack_raw: dict[str, Any],
    worldpack: WorldPack,
    renderpack: RenderPack,
) -> VerifiedRuntimeBundle:
    if worldpack.world_id != manifest["world_id"] or renderpack.world_id != manifest["world_id"]:
        raise BundleError("World IDs disagree across the bundle")
    if worldpack.format_version != manifest["worldpack"]["format_version"]:
        raise BundleError("Worldpack format version disagrees with the manifest")
    if worldpack.content_hash != manifest["worldpack"]["content_hash"]:
        raise BundleError("Worldpack hash disagrees with the manifest")
    if worldpack.content_hash != manifest["source_hashes"]["worldpack_content_hash"]:
        raise BundleError("Source worldpack hash disagrees with the bundled worldpack")
    if renderpack.content_hash != manifest["renderpack"]["content_hash"]:
        raise BundleError("Renderpack hash disagrees with the manifest")
    if renderpack.world_content_hash != worldpack.content_hash:
        raise BundleError("Renderpack was built for a different worldpack")
    if manifest["renderpack"]["world_content_hash"] != worldpack.content_hash:
        raise BundleError("Manifest renderpack binding disagrees with the worldpack")
    if _runtime_features(worldpack_raw) != manifest["required_runtime_features"]:
        raise BundleError("Runtime feature requirements disagree with the worldpack")

    referenced_assets: dict[str, tuple[str, str]] = {}
    for asset in renderpack.assets:
        for item in asset.files:
            relative = _bundle_payload_path(item.path, f"renderpack asset {asset.id}")
            if not relative.startswith("assets/"):
                raise BundleError(f"Renderpack asset is outside assets/**: {relative}")
            current = (item.sha256, item.media_type)
            if relative in referenced_assets and referenced_assets[relative] != current:
                raise BundleError(f"Renderpack gives conflicting metadata for {relative}")
            referenced_assets[relative] = current
    manifest_assets = {
        path: (record["sha256"], record["media_type"])
        for path, record in records_by_path.items()
        if path.startswith("assets/")
    }
    if referenced_assets != manifest_assets:
        raise BundleError("Asset inventory does not exactly match renderpack references")

    for relative, record in records_by_path.items():
        if record["media_type"] != "application/json":
            continue
        document = _read_json(
            root / PurePosixPath(relative),
            limit=MAX_MANIFEST_BYTES,
            context=f"runtime JSON {relative}",
        )
        _scan_json_runtime_boundary(document, relative)
        if relative.startswith("assets/") and document.get("format") != "isoworld.clipset":
            raise BundleError(f"Runtime asset JSON is not a clipset: {relative}")
        if relative.startswith("assets/"):
            _validate_clipset_shape(document, relative)
    return VerifiedRuntimeBundle(root, manifest, worldpack, renderpack)


def _catalog_release(verified: VerifiedRuntimeBundle) -> dict[str, Any]:
    relative = PurePosixPath(
        "game_data", "worlds", verified.world_id, verified.release_id
    ).as_posix()
    return {
        "world_id": verified.world_id,
        "release_id": verified.release_id,
        "bundle_hash": verified.bundle_hash,
        "path": relative,
        "worldpack_hash": verified.worldpack.content_hash,
        "renderpack_hash": verified.renderpack.content_hash,
        "required_runtime_features": list(verified.manifest["required_runtime_features"]),
    }


def _validate_catalog_document(raw: dict[str, Any]) -> list[dict[str, Any]]:
    catalog = _exact_keys(raw, _CATALOG_KEYS, "world catalog")
    if catalog["format"] != CATALOG_FORMAT or catalog["format_version"] != CATALOG_FORMAT_VERSION:
        raise BundleError("Unknown world catalog format")
    releases = catalog["releases"]
    if not isinstance(releases, list):
        raise BundleError("world catalog releases must be a list")
    validated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    hashes: set[str] = set()
    for index, value in enumerate(releases):
        release = _exact_keys(value, _RELEASE_KEYS, f"releases/{index}")
        world_id = _validate_world_id(release["world_id"], f"releases/{index}/world_id")
        release_id = _validate_release_id(release["release_id"], f"releases/{index}/release_id")
        bundle_hash = _valid_sha256(release["bundle_hash"], f"releases/{index}/bundle_hash")
        expected_path = PurePosixPath("game_data", "worlds", world_id, release_id).as_posix()
        if release["path"] != expected_path:
            raise BundleError(f"releases/{index}/path is not the derived game data path")
        _valid_sha256(release["worldpack_hash"], f"releases/{index}/worldpack_hash")
        _valid_sha256(release["renderpack_hash"], f"releases/{index}/renderpack_hash")
        features = release["required_runtime_features"]
        if (
            not isinstance(features, list)
            or not all(
                isinstance(item, str) and WORLD_ID_PATTERN.fullmatch(item) for item in features
            )
            or features != sorted(set(features))
        ):
            raise BundleError(f"releases/{index}/required_runtime_features is not sorted/unique")
        key = (world_id, release_id)
        if key in seen:
            raise BundleError(f"World catalog duplicates {world_id}/{release_id}")
        if bundle_hash in hashes:
            raise BundleError("World catalog reuses one bundle hash for multiple releases")
        seen.add(key)
        hashes.add(bundle_hash)
        validated.append(release)
    if [(item["world_id"], item["release_id"]) for item in validated] != sorted(seen):
        raise BundleError("World catalog releases must be sorted by world_id and release_id")
    return validated


def _assert_game_path_component(path: Path, *, directory: bool) -> None:
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if stat.S_ISLNK(info.st_mode) or not expected:
        kind = "directory" if directory else "file"
        raise BundleError(f"Game path must be a real {kind}: {path}")
    if not directory and info.st_nlink != 1:
        raise BundleError(f"Game control file may not be hard-linked: {path}")


def _audit_catalog_storage(
    game_root: Path,
    releases: list[dict[str, Any]],
    *,
    allowed_empty_world: str | None = None,
) -> None:
    worlds_root = game_root / "game_data/worlds"
    expected: dict[str, set[str]] = {}
    for release in releases:
        expected.setdefault(release["world_id"], set()).add(release["release_id"])
    if not worlds_root.exists() and not worlds_root.is_symlink():
        if releases:
            raise BundleError("World catalog refers to a missing game_data/worlds directory")
        return
    _assert_game_path_component(game_root / "game_data", directory=True)
    _assert_game_path_component(worlds_root, directory=True)
    actual_worlds: set[str] = set()
    for world_path in sorted(worlds_root.iterdir()):
        _assert_game_path_component(world_path, directory=True)
        actual_worlds.add(world_path.name)
        if world_path.name not in expected:
            if world_path.name == allowed_empty_world and not any(world_path.iterdir()):
                actual_worlds.remove(world_path.name)
                continue
            raise BundleError(f"Unmanaged world directory in game data: {world_path.name}")
        actual_releases: set[str] = set()
        for release_path in sorted(world_path.iterdir()):
            _assert_game_path_component(release_path, directory=True)
            actual_releases.add(release_path.name)
            if release_path.name not in expected[world_path.name]:
                raise BundleError(
                    "Unmanaged release directory in game data: "
                    f"{world_path.name}/{release_path.name}"
                )
        if actual_releases != expected[world_path.name]:
            raise BundleError(f"Catalog/storage mismatch for world {world_path.name}")
    if actual_worlds != set(expected):
        raise BundleError("World catalog and game_data/worlds disagree")


def _shared_asset_path(value: Any, context: str) -> str:
    relative = _relative_posix_path(value, context)
    parts = PurePosixPath(relative).parts
    if parts[:2] != ("game_data", "shared") or len(parts) < 3:
        raise BundleError(f"{context} must be below game_data/shared")
    for component in parts[2:]:
        folded = component.casefold()
        stem = PurePosixPath(component).stem.casefold()
        if (
            folded.startswith(".")
            or folded in _FORBIDDEN_PATH_COMPONENTS
            or stem in _FORBIDDEN_PATH_COMPONENTS
            or folded in _FORBIDDEN_RUNTIME_FILENAMES
        ):
            raise BundleError(f"{context} exposes an authoring-only path")
    return relative


def _verify_shared_assets(game_root: Path) -> list[dict[str, Any]]:
    lock_path = game_root / SHARED_ASSET_LOCK
    _assert_game_path_component(lock_path, directory=False)
    document = _read_json(lock_path, limit=MAX_MANIFEST_BYTES, context="shared asset lock")
    _exact_keys(document, _SHARED_LOCK_KEYS, "shared asset lock")
    if document["format"] != "isoworld.shared_assets" or document["format_version"] != 1:
        raise BundleError("Unknown shared asset lock format")
    _valid_sha256(document["content_hash"], "shared asset lock/content_hash")
    notices_hash = _valid_sha256(document["notices_sha256"], "shared asset lock/notices_sha256")
    if document["content_hash"] != canonical_payload_hash(document):
        raise BundleError("Shared asset lock content hash does not verify")
    if lock_path.read_bytes() != _pretty_json(document):
        raise BundleError("Shared asset lock is not canonically serialized")
    notices_path = _regular_source(
        game_root,
        "THIRD_PARTY_NOTICES.md",
        "shared asset notices",
    )
    if notices_path.stat().st_nlink != 1 or _sha256_file(notices_path) != notices_hash:
        raise BundleError("Shared asset lock does not match THIRD_PARTY_NOTICES.md")
    raw_files = document["files"]
    if not isinstance(raw_files, list) or len(raw_files) > MAX_SHARED_FILES:
        raise BundleError("Shared asset files must be a bounded list")
    records: list[dict[str, Any]] = []
    paths: set[str] = set()
    folded_prefixes: dict[str, str] = {}
    total = 0
    for index, raw in enumerate(raw_files):
        record = _exact_keys(raw, _FILE_KEYS, f"shared files/{index}")
        relative = _shared_asset_path(record["path"], f"shared files/{index}/path")
        if relative in paths:
            raise BundleError(f"Shared asset lock duplicates path: {relative}")
        paths.add(relative)
        prefix: list[str] = []
        for component in PurePosixPath(relative).parts:
            prefix.append(component)
            exact = "/".join(prefix)
            folded = exact.casefold()
            previous = folded_prefixes.setdefault(folded, exact)
            if previous != exact:
                raise BundleError(
                    f"Shared asset paths collide case-insensitively: {previous!r} and {exact!r}"
                )
        _valid_sha256(record["sha256"], f"shared files/{index}/sha256")
        size = record["size"]
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 <= size <= MAX_BUNDLE_FILE_BYTES
        ):
            raise BundleError(f"shared files/{index}/size is invalid")
        if record["media_type"] not in _SHARED_MEDIA_TYPES:
            raise BundleError(f"shared files/{index}/media_type is unsupported")
        expected_media = _SHARED_EXTENSION_MEDIA_TYPES.get(
            PurePosixPath(relative).suffix.casefold()
        )
        if expected_media is None or record["media_type"] != expected_media:
            raise BundleError(f"shared files/{index} extension and media_type disagree")
        total += size
        if total > MAX_SHARED_BYTES:
            raise BundleError("Shared assets exceed the total-byte limit")
        records.append(record)
    if [record["path"] for record in records] != sorted(paths):
        raise BundleError("Shared asset records must be sorted by path")

    shared_root = game_root / "game_data/shared"
    if not records:
        if shared_root.exists() or shared_root.is_symlink():
            raise BundleError("Empty shared asset lock requires no shared directory")
        return []
    files, directories = _walk_regular_files(shared_root, "shared asset root")
    actual_files = {path.relative_to(game_root).as_posix() for path in files}
    if actual_files != paths:
        raise BundleError("Shared asset tree differs from its lock")
    expected_directories = {
        parent.relative_to(PurePosixPath("game_data/shared")).as_posix()
        for path in paths
        for parent in PurePosixPath(path).parents
        if PurePosixPath("game_data/shared") in parent.parents
    }
    if directories != expected_directories:
        raise BundleError("Shared asset directory tree differs from its lock")
    for record in records:
        path = _regular_source(game_root, record["path"], "shared asset")
        if path.stat().st_nlink != 1:
            raise BundleError(f"Shared asset may not be hard-linked: {record['path']}")
        if path.stat().st_size != record["size"] or _sha256_file(path) != record["sha256"]:
            raise BundleError(f"Shared asset failed size/hash verification: {record['path']}")
        if not media_signature_matches(path, record["media_type"]):
            raise BundleError(f"Shared asset bytes disagree with media type: {record['path']}")
        if record["media_type"] == "application/json":
            value = _read_json(path, limit=MAX_MANIFEST_BYTES, context=record["path"])
            _scan_json_runtime_boundary(value, record["path"])
    return records


def _audit_game_data_root(
    game_root: Path,
    releases: list[dict[str, Any]],
    shared_assets: list[dict[str, Any]],
    *,
    has_composed_releases: bool,
    allowed_empty_world: str | None = None,
) -> None:
    game_data = game_root / "game_data"
    _assert_game_path_component(game_data, directory=True)
    expected = {"compositions.lock.json", "shared.lock.json", "worlds.lock.json"}
    journal_audit = audit_publication_journals(game_root)
    if journal_audit.issues:
        raise BundleError(f"Publication journal is invalid: {journal_audit.issues[0]}")
    expected.update(
        path.name
        for path in journal_audit.terminal_paths
        if path.parent == PurePosixPath("game_data")
    )
    if releases or allowed_empty_world is not None:
        expected.add("worlds")
    if shared_assets:
        expected.add("shared")
    if has_composed_releases:
        expected.add("compositions")
        expected.add("compositions.d")
    actual: set[str] = set()
    for entry in game_data.iterdir():
        info = entry.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise BundleError(f"Game data contains a symbolic link: {entry.name}")
        actual.add(entry.name)
    if actual != expected:
        raise BundleError(
            "Game data root differs from the locked allowlist; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _load_verified_catalog(
    game_root: Path,
    *,
    allowed_empty_world: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from isoworld.content.composed_catalog import (
        ComposedCatalogError,
        load_composed_catalog,
        validate_cross_catalog_world_hashes,
        verify_composed_release,
    )

    shared_assets = _verify_shared_assets(game_root)
    catalog_path = game_root / WORLD_CATALOG
    _assert_game_path_component(catalog_path, directory=False)
    if not catalog_path.exists():
        catalog = {
            "format": CATALOG_FORMAT,
            "format_version": CATALOG_FORMAT_VERSION,
            "releases": [],
        }
        releases: list[dict[str, Any]] = []
    else:
        catalog = _read_json(catalog_path, limit=MAX_CATALOG_BYTES, context="world catalog")
        releases = _validate_catalog_document(catalog)
        if catalog_path.read_bytes() != _pretty_json(catalog):
            raise BundleError("World catalog is not canonically serialized")
    _audit_catalog_storage(
        game_root,
        releases,
        allowed_empty_world=allowed_empty_world,
    )
    try:
        composed_releases = load_composed_catalog(game_root)
        for composed in composed_releases:
            with verify_composed_release(composed, game_root):
                pass
        validate_cross_catalog_world_hashes(tuple(releases), composed_releases)
    except ComposedCatalogError as exc:
        raise BundleError(str(exc)) from exc
    _audit_game_data_root(
        game_root,
        releases,
        shared_assets,
        has_composed_releases=bool(composed_releases),
        allowed_empty_world=allowed_empty_world,
    )
    for release in releases:
        with verify_runtime_bundle(
            game_root / PurePosixPath(release["path"]),
            expected_bundle_hash=release["bundle_hash"],
        ) as verified:
            expected = _catalog_release(verified)
        if expected != release:
            raise BundleError(
                "Catalog metadata disagrees with imported bundle "
                f"{release['world_id']}/{release['release_id']}"
            )
    return catalog, releases


def _write_catalog_atomic(path: Path, catalog: dict[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.tmp-{uuid.uuid4().hex}"
    primary_error: BaseException | None = None
    try:
        with temporary.open("xb") as target:
            target.write(_pretty_json(catalog))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent, context="world catalog parent")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError as cleanup_error:
            if primary_error is not None:
                primary_error.add_note(f"World catalog temporary cleanup failed: {cleanup_error}")
            else:
                raise


def _identity_document(identity: DirectoryIdentity) -> dict[str, int]:
    return {"device": identity[0], "inode": identity[1]}


def _identity_from_document(value: Any, context: str) -> DirectoryIdentity:
    record = _exact_keys(value, _IDENTITY_KEYS, context)
    device = record["device"]
    inode = record["inode"]
    if (
        isinstance(device, bool)
        or not isinstance(device, int)
        or device < 0
        or isinstance(inode, bool)
        or not isinstance(inode, int)
        or inode < 0
    ):
        raise BundleError(f"{context} must contain non-negative integer identities")
    return device, inode


def _catalog_snapshot(root: Path) -> tuple[dict[str, Any], str]:
    catalog_path = root / WORLD_CATALOG
    _assert_game_path_component(catalog_path, directory=False)
    if not catalog_path.exists():
        catalog: dict[str, Any] = {
            "format": CATALOG_FORMAT,
            "format_version": CATALOG_FORMAT_VERSION,
            "releases": [],
        }
    else:
        catalog = _read_json(catalog_path, limit=MAX_CATALOG_BYTES, context="world catalog")
        _validate_catalog_document(catalog)
        if catalog_path.read_bytes() != _pretty_json(catalog):
            raise BundleError("World catalog is not canonically serialized")
    return catalog, _sha256_bytes(_pretty_json(catalog))


def _validate_import_journal_document(journal: dict[str, Any]) -> dict[str, Any]:
    if (
        journal["format"] != IMPORT_JOURNAL_FORMAT
        or journal["format_version"] != IMPORT_JOURNAL_FORMAT_VERSION
    ):
        raise BundleError("Unknown bundle import journal format")
    operation_id = journal["operation_id"]
    if not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        raise BundleError("Bundle import journal has an invalid operation_id")
    if journal["state"] not in {"intent", "copying", "ready", "committed"}:
        raise BundleError("Bundle import journal has an invalid state")
    world_id = _validate_world_id(journal["world_id"], "journal world_id")
    release_id = _validate_release_id(journal["release_id"], "journal release_id")
    temporary = _relative_posix_path(journal["temporary"], "journal temporary")
    destination = _relative_posix_path(journal["destination"], "journal destination")
    expected_temporary = f"game_data/worlds/{world_id}/.{release_id}.import-{operation_id}"
    expected_destination = f"game_data/worlds/{world_id}/{release_id}"
    if temporary != expected_temporary or destination != expected_destination:
        raise BundleError("Bundle import journal paths do not match its release identity")
    _valid_sha256(journal["bundle_hash"], "journal bundle_hash")
    _valid_sha256(journal["catalog_before_hash"], "journal catalog_before_hash")
    _valid_sha256(journal["catalog_after_hash"], "journal catalog_after_hash")
    if journal["state"] == "intent":
        if journal["directory_identity"] is not None:
            raise BundleError("Intent bundle import journal must not claim a directory identity")
    elif journal["state"] in {"copying", "ready"} and journal["directory_identity"] is None:
        raise BundleError("Non-intent bundle import journal must bind a directory identity")
    elif journal["directory_identity"] is not None:
        _identity_from_document(journal["directory_identity"], "journal directory_identity")
    created = journal["created_directories"]
    if not isinstance(created, list):
        raise BundleError("journal created_directories must be a list")
    allowed_created = {
        "game_data",
        "game_data/worlds",
        f"game_data/worlds/{world_id}",
    }
    seen: set[str] = set()
    for index, item in enumerate(created):
        record = _exact_keys(
            item,
            _CREATED_DIRECTORY_KEYS,
            f"journal created_directories/{index}",
        )
        relative = _relative_posix_path(
            record["path"],
            f"journal created_directories/{index}/path",
        )
        if relative not in allowed_created or relative in seen:
            raise BundleError("Bundle import journal has an invalid created directory")
        _identity_from_document(
            {"device": record["device"], "inode": record["inode"]},
            f"journal created_directories/{index}",
        )
        seen.add(relative)
    return journal


def _decode_import_journal_payload(path: Path, payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError) as exc:
        raise BundleError(f"Could not read bundle import journal {path}: {exc}") from exc
    journal = _exact_keys(
        value,
        _IMPORT_JOURNAL_KEYS,
        "bundle import journal",
    )
    if payload != _pretty_json(journal):
        raise BundleError("Bundle import journal record is not canonically serialized")
    return _validate_import_journal_document(journal)


def _read_import_journal_record(
    path: Path,
) -> tuple[dict[str, Any], DirectoryIdentity, bytes] | None:
    try:
        loaded = read_append_only_journal(
            path,
            max_record_bytes=MAX_CATALOG_BYTES,
            max_file_bytes=MAX_CATALOG_BYTES * 16,
        )
    except DirectoryPublishError as exc:
        raise BundleError(str(exc)) from exc
    if loaded is None:
        return None
    payload, identity = loaded
    return _decode_import_journal_payload(path, payload), identity, payload


def _write_import_journal(path: Path, journal: dict[str, Any]) -> None:
    payload = _pretty_json(_validate_import_journal_document(journal))
    try:
        create_append_only_journal(
            path,
            payload,
            max_record_bytes=MAX_CATALOG_BYTES,
        )
    except FileExistsError as exc:
        existing = _read_import_journal_record(path)
        if existing is None or existing[0]["state"] != "committed":
            raise BundleError(f"Unrecovered bundle import journal already exists: {path}") from exc
        try:
            append_append_only_journal(
                path,
                expected_identity=existing[1],
                expected_payload=existing[2],
                updated_payload=payload,
                max_record_bytes=MAX_CATALOG_BYTES,
                max_file_bytes=MAX_CATALOG_BYTES * 16,
            )
        except DirectoryPublishError as append_error:
            raise BundleError(str(append_error)) from append_error
    except DirectoryPublishError as exc:
        raise BundleError(str(exc)) from exc
    try:
        fsync_directory(path.parent, context="bundle import journal parent")
    except DirectoryPublishError as exc:
        raise BundleError(str(exc)) from exc


def _replace_import_journal(
    root: Path,
    current: dict[str, Any],
    updated: dict[str, Any],
) -> None:
    path = root / IMPORT_JOURNAL
    loaded = _read_import_journal_record(path)
    expected_payload = _pretty_json(current)
    if loaded is None or loaded[0] != current or loaded[2] != expected_payload:
        raise BundleError("Bundle import journal changed before state transition")
    updated_payload = _pretty_json(_validate_import_journal_document(updated))
    try:
        append_append_only_journal(
            path,
            expected_identity=loaded[1],
            expected_payload=expected_payload,
            updated_payload=updated_payload,
            max_record_bytes=MAX_CATALOG_BYTES,
            max_file_bytes=MAX_CATALOG_BYTES * 16,
        )
    except DirectoryPublishError as exc:
        raise BundleError(str(exc)) from exc


def _read_import_journal(root: Path) -> dict[str, Any] | None:
    loaded = _read_import_journal_record(root / IMPORT_JOURNAL)
    if loaded is None or loaded[0]["state"] == "committed":
        return None
    return loaded[0]


def _audit_game_repository_for_import(root: Path) -> list[Any]:
    journal = _read_import_journal_record(root / IMPORT_JOURNAL)
    composed_path = Path("game_data/.composed-catalog-publication.json")
    if (root / composed_path).exists() or (root / composed_path).is_symlink():
        from worldforge.composed_game import (
            ComposedGameError,
            _read_catalog_journal_record,
        )

        try:
            composed_journal = _read_catalog_journal_record(root)
        except ComposedGameError as exc:
            raise BundleError(str(exc)) from exc
        if composed_journal is None or composed_journal[0]["state"] != "committed":
            raise BundleError("Active composed catalog journal blocks legacy bundle import")
    try:
        findings = audit_game_repository(root)
    except GameBoundaryError as exc:
        raise BundleError(f"Could not audit the game repository: {exc}") from exc
    recoverable_journal = journal is not None
    return [
        finding
        for finding in findings
        if not (
            recoverable_journal
            and finding.path == Path(IMPORT_JOURNAL)
            and finding.rule in {"active_publication_journal", "partial_publication_journal"}
        )
    ]


def _remove_import_journal(root: Path, journal: dict[str, Any]) -> None:
    if journal["state"] == "committed":
        return
    _replace_import_journal(
        root,
        journal,
        {**journal, "state": "committed"},
    )


def _journal_path(root: Path, relative: str) -> Path:
    return root / PurePosixPath(relative)


def _journal_bundle_release(
    path: Path,
    identity: DirectoryIdentity,
    bundle_hash: str,
) -> dict[str, Any]:
    try:
        if directory_identity(path, context="journalled bundle") != identity:
            raise BundleError("Journalled bundle directory identity changed")
        with verify_runtime_bundle(path, expected_bundle_hash=bundle_hash) as verified:
            release = _catalog_release(verified)
        if directory_identity(path, context="journalled bundle") != identity:
            raise BundleError("Journalled bundle directory changed during verification")
    except DirectoryPublishError as exc:
        raise BundleError(str(exc)) from exc
    return release


def _verify_journal_bundle(
    path: Path,
    identity: DirectoryIdentity,
    bundle_hash: str,
) -> None:
    _journal_bundle_release(path, identity, bundle_hash)


def _rollback_journal_bundle(
    path: Path,
    identity: DirectoryIdentity,
    bundle_hash: str,
) -> None:
    try:
        quarantine_and_remove_owned_directory(
            path,
            identity,
            verify=lambda candidate: _verify_journal_bundle(
                candidate,
                identity,
                bundle_hash,
            ),
        )
    except DirectoryPublishError as exc:
        raise BundleError(f"Could not safely roll back bundle import: {exc}") from exc


def _roll_forward_published_journal_bundle(
    root: Path,
    journal: dict[str, Any],
    catalog_before: dict[str, Any],
    destination: Path,
    identity: DirectoryIdentity,
) -> Path:
    """Commit the exact catalog transaction for one verified published journal."""

    release = _journal_bundle_release(destination, identity, journal["bundle_hash"])
    if (
        release["world_id"] != journal["world_id"]
        or release["release_id"] != journal["release_id"]
        or release["path"] != journal["destination"]
    ):
        raise BundleError("Journalled bundle metadata disagrees with its destination")
    releases = _validate_catalog_document(catalog_before)
    updated_releases = [*releases, release]
    updated_releases.sort(key=lambda item: (item["world_id"], item["release_id"]))
    catalog_after = {
        "format": CATALOG_FORMAT,
        "format_version": CATALOG_FORMAT_VERSION,
        "releases": updated_releases,
    }
    _validate_catalog_document(catalog_after)
    if _sha256_bytes(_pretty_json(catalog_after)) != journal["catalog_after_hash"]:
        raise BundleError("Recovered catalog transaction disagrees with its journal")
    _write_catalog_atomic(root / WORLD_CATALOG, catalog_after)
    _, written_hash = _catalog_snapshot(root)
    if written_hash != journal["catalog_after_hash"]:
        raise BundleError("Recovered catalog transaction did not commit exactly")
    _verify_journal_bundle(destination, identity, journal["bundle_hash"])
    _remove_import_journal(root, journal)
    return destination


def _remove_created_directories(root: Path, records: list[dict[str, Any]]) -> None:
    for record in reversed(records):
        identity = _identity_from_document(
            {"device": record["device"], "inode": record["inode"]},
            "journal created directory",
        )
        try:
            remove_owned_empty_directory(_journal_path(root, record["path"]), identity)
        except DirectoryPublishError as exc:
            raise BundleError(f"Could not safely clean import directories: {exc}") from exc


def _recover_import_journal(root: Path) -> Path | None:
    loaded = _read_import_journal_record(root / IMPORT_JOURNAL)
    if loaded is None:
        return None
    journal = loaded[0]
    catalog, catalog_hash = _catalog_snapshot(root)
    temporary = _journal_path(root, journal["temporary"])
    destination = _journal_path(root, journal["destination"])
    temporary_exists = temporary.exists() or temporary.is_symlink()
    destination_exists = destination.exists() or destination.is_symlink()

    if journal["state"] == "committed":
        if journal["directory_identity"] is not None:
            return None
        if temporary_exists or destination_exists or catalog_hash != journal["catalog_before_hash"]:
            raise BundleError(
                "Aborted intent bundle import journal disagrees with storage; "
                "preserving terminal evidence"
            )
        try:
            _audit_catalog_storage(
                root,
                _validate_catalog_document(catalog),
                allowed_empty_world=journal["world_id"],
            )
        except (BundleError, OSError) as exc:
            raise BundleError(
                "Aborted intent bundle import has non-empty or unauthorised "
                "derived ancestor evidence"
            ) from exc
        return destination.parent

    if journal["state"] == "intent":
        if temporary_exists or destination_exists:
            raise BundleError(
                "Intent bundle import journal has an ambiguous claimed directory; "
                "preserving journal and filesystem"
            )
        for record in journal["created_directories"]:
            created_path = _journal_path(root, record["path"])
            expected_created_identity = _identity_from_document(
                {"device": record["device"], "inode": record["inode"]},
                "intent journal created directory",
            )
            if (
                directory_identity(
                    created_path,
                    context="intent journal created directory",
                )
                != expected_created_identity
            ):
                raise BundleError("Intent journal created directory identity changed")
        try:
            releases = _validate_catalog_document(catalog)
            _audit_catalog_storage(
                root,
                releases,
                allowed_empty_world=journal["world_id"],
            )
        except (BundleError, OSError) as exc:
            raise BundleError(
                "Intent bundle import journal has non-empty or unauthorised "
                "derived ancestor evidence; preserving journal and filesystem"
            ) from exc
        _remove_import_journal(root, journal)
        return destination.parent

    identity = _identity_from_document(
        journal["directory_identity"],
        "journal directory_identity",
    )

    if catalog_hash == journal["catalog_after_hash"]:
        if journal["state"] != "ready" or temporary_exists or not destination_exists:
            raise BundleError("Committed bundle import journal disagrees with storage")
        _verify_journal_bundle(destination, identity, journal["bundle_hash"])
        _remove_import_journal(root, journal)
        return destination
    if catalog_hash != journal["catalog_before_hash"]:
        raise BundleError("Bundle import journal disagrees with the current catalog")
    if temporary_exists and destination_exists:
        raise BundleError("Bundle import journal has both staged and published directories")
    if journal["state"] == "copying" and destination_exists:
        if temporary_exists:
            raise BundleError("Bundle import journal has both staged and published directories")
        _journal_bundle_release(destination, identity, journal["bundle_hash"])
        ready_journal = {**journal, "state": "ready"}
        _replace_import_journal(root, journal, ready_journal)
        return _roll_forward_published_journal_bundle(
            root,
            ready_journal,
            catalog,
            destination,
            identity,
        )
    if journal["state"] == "copying" and temporary_exists:
        if directory_identity(temporary, context="recoverable bundle import stage") != identity:
            raise BundleError(
                "Recoverable bundle import stage identity changed; preserving evidence"
            )
        _journal_bundle_release(temporary, identity, journal["bundle_hash"])
        ready_journal = {**journal, "state": "ready"}
        _replace_import_journal(root, journal, ready_journal)
        journal = ready_journal
    if journal["state"] == "ready" and destination_exists:
        return _roll_forward_published_journal_bundle(
            root,
            journal,
            catalog,
            destination,
            identity,
        )
    if journal["state"] == "ready" and temporary_exists:
        if directory_identity(temporary, context="ready bundle import stage") != identity:
            raise BundleError("Ready bundle import stage identity changed; preserving evidence")
        _journal_bundle_release(temporary, identity, journal["bundle_hash"])
        try:
            published_identity = publish_directory_noreplace(
                temporary,
                destination,
                expected_source_identity=identity,
            )
        except FileExistsError as exc:
            raise BundleError(
                "Ready bundle import destination became occupied; preserving evidence"
            ) from exc
        except DirectoryPublishError as exc:
            raise BundleError(str(exc)) from exc
        if published_identity != identity:
            raise BundleError("Recovered bundle import identity changed during publication")
        _verify_journal_bundle(destination, identity, journal["bundle_hash"])
        return _roll_forward_published_journal_bundle(
            root,
            journal,
            catalog,
            destination,
            identity,
        )
    raise BundleError(
        "Bundle import journal has incomplete or missing identity-bound evidence; "
        "preserving journal and filesystem"
    )


def _ensure_runtime_compatible(
    worldpack: WorldPack,
    runtime_api_version: str,
    runtime_features: Iterable[str],
    context: str,
) -> None:
    try:
        compatibility = worldpack.compatibility_with(
            runtime_api_version,
            runtime_features,
        )
    except ValueError as exc:
        raise BundleError(f"The game runtime contract is invalid: {exc}") from exc
    if not compatibility.compatible:
        raise BundleError(
            f"Runtime is incompatible with {context}: "
            f"api_compatible={compatibility.api_compatible}, "
            f"missing_features={list(compatibility.missing_required_features)}"
        )


def verify_game_catalog_compatibility(
    game_root: str | Path,
    runtime_api_version: str,
    runtime_features: Iterable[str],
    *,
    allowed_empty_world: str | None = None,
) -> None:
    """Verify every installed release against a proposed runtime contract."""

    root = Path(game_root).resolve()
    _, releases = _load_verified_catalog(
        root,
        allowed_empty_world=allowed_empty_world,
    )
    for release in releases:
        installed = load_worldpack(root / PurePosixPath(release["path"]) / "worldpack.json")
        _ensure_runtime_compatible(
            installed,
            runtime_api_version,
            runtime_features,
            f"installed {release['world_id']}/{release['release_id']}",
        )


def import_runtime_bundle(
    bundle_path: str | Path,
    game_root: str | Path,
    *,
    expected_bundle_hash: str,
) -> Path:
    """Atomically copy a verified bundle into an existing clean game repository."""

    _valid_sha256(expected_bundle_hash, "expected_bundle_hash")
    verified = verify_runtime_bundle(bundle_path, expected_bundle_hash=expected_bundle_hash)
    primary_error: BaseException | None = None
    imported: Path | None = None
    try:
        imported = _import_runtime_bundle_from_verified(verified, game_root)
    except BaseException as exc:
        primary_error = exc

    cleanup_error: BaseException | None = None
    try:
        verified.close()
    except BaseException as exc:
        cleanup_error = exc

    if primary_error is not None:
        if cleanup_error is not None:
            detail = f"Runtime bundle cleanup failed: {cleanup_error}"
            primary_error.add_note(detail)
            raise primary_error from cleanup_error
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    assert imported is not None
    return imported


def _import_runtime_bundle_from_verified(
    verified: VerifiedRuntimeBundle,
    game_root: str | Path,
) -> Path:
    try:
        require_standalone_bundle_root(verified.root)
    except (OSError, ValueError) as exc:
        raise BundleError(str(exc)) from exc
    try:
        root = require_standalone_game_root(game_root)
    except (OSError, ValueError) as exc:
        raise BundleError(str(exc)) from exc
    bundle_root = verified.root.resolve()
    if bundle_root == root or root in bundle_root.parents or bundle_root in root.parents:
        raise BundleError("The source bundle and game repository must be external and disjoint")
    findings = _audit_game_repository_for_import(root)
    if findings:
        raise BundleError(f"Refusing to import into a boundary-invalid game: {findings[0]}")
    try:
        with exclusive_game_mutation(root, "bundle-import"):
            recovered = _recover_import_journal(root)
            runtime_contract = verify_game_runtime_snapshot(root)
            _ensure_runtime_compatible(
                verified.worldpack,
                runtime_contract["runtime_api_version"],
                runtime_contract["supported_runtime_features"],
                f"candidate {verified.world_id}/{verified.release_id}",
            )
            expected_destination = (
                root / "game_data/worlds" / verified.world_id / verified.release_id
            )
            if recovered == expected_destination:
                with verify_runtime_bundle(
                    recovered,
                    expected_bundle_hash=verified.bundle_hash,
                ):
                    pass
                verify_game_catalog_compatibility(
                    root,
                    runtime_contract["runtime_api_version"],
                    runtime_contract["supported_runtime_features"],
                )
                return recovered
            return _import_verified_bundle(
                verified,
                root,
                runtime_api_version=runtime_contract["runtime_api_version"],
                runtime_features=runtime_contract["supported_runtime_features"],
                allowed_empty_world=(
                    verified.world_id if recovered == expected_destination.parent else None
                ),
            )
    except GameMutationLockError as exc:
        raise BundleError(str(exc)) from exc


def _import_verified_bundle(
    verified: VerifiedRuntimeBundle,
    root: Path,
    *,
    runtime_api_version: str,
    runtime_features: Iterable[str],
    allowed_empty_world: str | None = None,
) -> Path:
    catalog_before, releases = _load_verified_catalog(
        root,
        allowed_empty_world=allowed_empty_world,
    )
    verify_game_catalog_compatibility(
        root,
        runtime_api_version,
        runtime_features,
        allowed_empty_world=allowed_empty_world,
    )
    key = (verified.world_id, verified.release_id)
    if any((item["world_id"], item["release_id"]) == key for item in releases):
        raise BundleError(
            f"World release is already imported: {verified.world_id}/{verified.release_id}"
        )
    if any(item["bundle_hash"] == verified.bundle_hash for item in releases):
        raise BundleError("The same immutable bundle is already catalogued under another release")

    game_data = root / "game_data"
    worlds_root = game_data / "worlds"
    world_root = worlds_root / verified.world_id
    destination = world_root / verified.release_id
    for existing in (game_data, worlds_root, world_root, destination):
        _assert_game_path_component(existing, directory=True)
    if destination.exists() or destination.is_symlink():
        raise BundleError(f"Import destination already exists: {destination}")

    updated_releases = [*releases, _catalog_release(verified)]
    updated_releases.sort(key=lambda item: (item["world_id"], item["release_id"]))
    catalog_after = {
        "format": CATALOG_FORMAT,
        "format_version": CATALOG_FORMAT_VERSION,
        "releases": updated_releases,
    }
    _validate_catalog_document(catalog_after)

    operation_id = uuid.uuid4().hex
    temporary = world_root / f".{verified.release_id}.import-{operation_id}"
    temporary_identity: DirectoryIdentity | None = None
    created: list[dict[str, Any]] = []
    journal: dict[str, Any] | None = None
    catalog_commit_failed = False
    try:
        journal = {
            "format": IMPORT_JOURNAL_FORMAT,
            "format_version": IMPORT_JOURNAL_FORMAT_VERSION,
            "operation_id": operation_id,
            "state": "intent",
            "world_id": verified.world_id,
            "release_id": verified.release_id,
            "temporary": temporary.relative_to(root).as_posix(),
            "destination": destination.relative_to(root).as_posix(),
            "bundle_hash": verified.bundle_hash,
            "directory_identity": None,
            "created_directories": [],
            "catalog_before_hash": _sha256_bytes(_pretty_json(catalog_before)),
            "catalog_after_hash": _sha256_bytes(_pretty_json(catalog_after)),
        }
        _write_import_journal(root / IMPORT_JOURNAL, journal)
        for directory in (game_data, worlds_root, world_root):
            if directory.exists():
                _assert_game_path_component(directory, directory=True)
                continue
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise BundleError(f"Import directory appeared concurrently: {directory}") from exc
            identity = directory_identity(directory, context="created import directory")
            fsync_directory(
                directory,
                context="created bundle import ancestor",
            )
            fsync_directory(
                directory.parent,
                context="bundle import ancestor parent",
            )
            if (
                directory_identity(
                    directory,
                    context="durable created import directory",
                )
                != identity
            ):
                raise BundleError("Created import directory identity changed")
            created.append(
                {
                    "path": directory.relative_to(root).as_posix(),
                    "device": identity[0],
                    "inode": identity[1],
                }
            )
        temporary.mkdir(mode=0o700)
        temporary_identity = directory_identity(temporary, context="staged bundle import")
        fsync_directory(temporary, context="staged bundle import")
        fsync_directory(temporary.parent, context="staged bundle import parent")
        if (
            directory_identity(temporary, context="durable staged bundle import")
            != temporary_identity
        ):
            raise BundleError("Staged bundle import identity changed")
        copying_journal = {
            **journal,
            "state": "copying",
            "directory_identity": _identity_document(temporary_identity),
            "created_directories": created,
        }
        _replace_import_journal(root, journal, copying_journal)
        journal = copying_journal
        shutil.copytree(verified.root, temporary, symlinks=False, dirs_exist_ok=True)
        with open_expected_directory(temporary, temporary_identity) as retained:
            _fsync_claimed_payload_tree(retained.fd)
            os.fsync(retained.fd)
            os.fsync(retained.parent_fd)
            retained.require_binding()
        with verify_runtime_bundle(
            temporary,
            expected_bundle_hash=verified.bundle_hash,
        ):
            pass
        ready_journal = {
            **journal,
            "state": "ready",
            "directory_identity": _identity_document(temporary_identity),
        }
        _replace_import_journal(root, journal, ready_journal)
        journal = ready_journal
        try:
            published_identity = publish_directory_noreplace(
                temporary,
                destination,
                expected_source_identity=temporary_identity,
            )
        except FileExistsError as exc:
            raise BundleError(f"Import destination already exists: {destination}") from exc
        except DirectoryPublishError as exc:
            raise BundleError(str(exc)) from exc
        if published_identity != temporary_identity:
            raise BundleError("Published bundle identity disagrees with its journal")
        _verify_journal_bundle(destination, published_identity, verified.bundle_hash)
        try:
            _write_catalog_atomic(root / WORLD_CATALOG, catalog_after)
        except BaseException as catalog_error:
            catalog_commit_failed = True
            catalog_error.add_note(
                "Verified published bundle and exact ready journal retained for "
                "catalog roll-forward on retry"
            )
            raise
        _remove_import_journal(root, journal)
        return destination
    except Exception as original_error:
        if catalog_commit_failed:
            raise
        if journal is not None and (root / IMPORT_JOURNAL).exists():
            recovered: Path | None = None
            try:
                recovered = _recover_import_journal(root)
            except Exception as recovery_error:
                original_error.add_note(
                    "Bundle import recovery could not complete; "
                    f"journalled internal evidence retained: {recovery_error}"
                )
            if recovered == destination:
                return destination
        elif temporary_identity is not None and (temporary.exists() or temporary.is_symlink()):
            original_error.add_note(
                f"Unjournalled private bundle import stage retained without mutation: {temporary}"
            )
        elif created:
            original_error.add_note(
                "Journal-free import namespace directories retained without mutation: "
                + ", ".join(record["path"] for record in created)
            )
        raise
