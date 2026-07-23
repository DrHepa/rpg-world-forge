"""Immutable M6 composition bundle construction and verification."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
import unicodedata
import uuid
import warnings
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Generic, TypeVar

from isoworld.content.file_stat import descriptor_file_stat, path_file_stat
from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.models import RUNTIME_API_VERSION, WorldPack
from isoworld.content.portability import (
    is_portable_path_component,
    portable_path_key,
    portable_relative_path,
)
from isoworld.content.renderpack import RenderPack, RenderPackError, load_renderpack
from isoworld.content.resource_snapshot import (
    MAX_OWNED_RESOURCE_BYTES,
    MaterializedResource,
    ResourceSnapshotError,
    ResourceSnapshotOwner,
    note_cleanup_failure,
)
from isoworld.render.composition_plan import (
    CompositionPlanError,
    PackSlotBinding,
    validate_composition_slot_ownership,
)
from isoworld.runtime_adapter import StaticRuntimeAdapterRegistry
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.assetpack import AssetPackError, verify_assetpack
from worldforge.directory_publish import (
    DirectoryIdentity,
    DirectoryPublishError,
    directory_identity,
    publish_directory_noreplace,
    quarantine_and_remove_owned_directory,
)
from worldforge.game_boundary import (
    FORBIDDEN_GAME_JSON_FORMATS,
    authoring_metadata_detail,
)
from worldforge.game_boundary_policy import validate_lexical_directory_root
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.repository_boundary import (
    RepositoryBoundaryError,
    assert_new_repository_target,
    require_standalone_composed_bundle_root,
)
from worldforge.runtime_composition import (
    PLATFORMS,
    RUNTIME_ADAPTER_FORMAT,
    RUNTIME_CAPABILITY_CATALOG_FORMAT,
    RUNTIME_COMPATIBILITY_REPORT_FORMAT,
    RUNTIME_COMPOSITION_FORMAT,
    RUNTIME_PRESENTATION_PROFILE_FORMAT,
    RegisteredRuntimeComposition,
    RuntimeCompositionError,
    RuntimeCompositionVerification,
    load_registered_runtime_composition,
    validate_runtime_adapter,
    validate_runtime_capability_catalog,
    validate_runtime_compatibility_report,
    validate_runtime_composition,
    validate_runtime_presentation_profile,
)

COMPOSED_BUNDLE_FORMAT = "rpg-world-forge.composed_runtime_bundle"
COMPOSED_BUNDLE_FORMAT_VERSION = 1
COMPOSED_BUNDLE_MANIFEST = "composed-bundle.manifest.json"
COMPOSED_BUNDLE_JOURNAL_FORMAT = "rpg-world-forge.composed_bundle_journal"
COMPOSED_BUNDLE_JOURNAL_VERSION = 1

COMPOSITION_PATH = "contracts/runtime-composition.json"
PROFILE_PATH = "contracts/runtime-presentation-profile.json"
CATALOG_PATH = "contracts/runtime-capability-catalog.json"
ADAPTER_PATH = "contracts/runtime-adapter.json"
REPORT_PATH = "evidence/runtime-compatibility-report.json"
WORLDPACK_PATH = "packs/worldpack/worldpack.json"
RENDERPACK_PATH = "packs/renderpack/renderpack.json"
ASSETPACK_PATH = "packs/assetpack/assetpack.json"

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_JSON_BYTES = 64 * 1024 * 1024
MAX_BUNDLE_FILES = 100_000
MAX_BUNDLE_BYTES = 16 * 1024 * 1024 * 1024
MAX_PATH_BYTES = 1024
MAX_LICENSE_FILES = 1_000
MAX_LICENSE_BYTES = 4 * 1024 * 1024

_ID = re.compile(r"^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_ALLOWED_ROOTS = frozenset({"contracts", "evidence", "packs", "licenses"})
_ALLOWED_MEDIA_TYPES = frozenset(
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
        "model/gltf-binary",
        "text/html",
        "text/markdown",
        "text/plain",
        "text/x-glsl",
    }
)
_LICENSE_MEDIA = {
    ".html": "text/html",
    ".json": "application/json",
    ".md": "text/markdown",
    ".rst": "text/plain",
    ".txt": "text/plain",
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
        "mcp",
        "prompts",
        "providers",
        "receipts",
        "recipes",
        "references",
        "requests",
        "source",
        "specs",
        "weights",
        "workflows",
    }
)
_FORBIDDEN_FILENAMES = frozenset(
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
_FORBIDDEN_WEIGHT_SUFFIXES = frozenset({".ckpt", ".gguf", ".pt", ".pth", ".safetensors"})
_MANIFEST_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "bundle_id",
        "bundle_version",
        "compatibility_target",
        "contracts",
        "compatibility_evidence",
        "packs",
        "files",
        "licenses",
        "bundle_hash",
    }
)
_FILE_FIELDS = frozenset({"path", "sha256", "size", "media_type"})
_BASE_REF_FIELDS = frozenset({"path", "format", "format_version", "content_hash"})
_PROFILE_REF_FIELDS = _BASE_REF_FIELDS | {"id"}
_ADAPTER_REF_FIELDS = _PROFILE_REF_FIELDS | {"version"}
_COMPOSITION_REF_FIELDS = _BASE_REF_FIELDS | {"world_id", "release_id"}
_REPORT_REF_FIELDS = _BASE_REF_FIELDS | {"composition_hash"}
_WORLDPACK_REF_FIELDS = _BASE_REF_FIELDS | {"world_id"}
_CONTENT_PACK_REF_FIELDS = _BASE_REF_FIELDS | {"world_content_hash"}
_CONTRACT_FIELDS = frozenset(
    {
        "runtime_composition",
        "presentation_profile",
        "capability_catalog",
        "runtime_adapter",
    }
)
_PACK_FIELDS = frozenset({"worldpack", "renderpack", "assetpack"})
_TARGET_FIELDS = frozenset({"platform", "runtime_api_version"})
_JOURNAL_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "stage_name",
        "destination_name",
        "stage_identity",
        "platform",
        "runtime_api_version",
        "bundle_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"device", "inode"})

T = TypeVar("T")


class ComposedBundleError(ValueError):
    """Raised when an immutable M6 composed bundle cannot be trusted."""


def _close_descriptor(descriptor: int, *, context: str) -> None:
    primary = sys.exception()
    try:
        os.close(descriptor)
    except OSError as cleanup_error:
        if not note_cleanup_failure(primary, cleanup_error, context=context):
            raise ComposedBundleError(f"{context} failed: {cleanup_error}") from cleanup_error


class _PublicationOutcome(Enum):
    STAGE_OWNED = "stage_owned"
    DESTINATION_OWNED = "destination_owned"
    UNCERTAIN = "uncertain"


def _immutable(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_immutable(child) for child in value)
    return value


@dataclass(frozen=True, slots=True, repr=False)
class LoadedComposedRuntimeBundle(Generic[T]):
    """One verified bundle backed only by private, identity-owned snapshots."""

    _owner: ResourceSnapshotOwner = field(repr=False, compare=False)
    _manifest_bytes: bytes = field(repr=False, compare=False)
    _assetpack_bytes: bytes | None = field(repr=False, compare=False)
    worldpack: WorldPack
    renderpack: RenderPack | None
    registered: RegisteredRuntimeComposition[T]
    verification: RuntimeCompositionVerification
    _closed: bool = field(default=False, init=False, repr=False, compare=False)

    @property
    def manifest(self) -> Mapping[str, object]:
        return _immutable(decode_json_object(self._manifest_bytes, source=COMPOSED_BUNDLE_MANIFEST))  # type: ignore[return-value]

    @property
    def assetpack(self) -> Mapping[str, object] | None:
        if self._assetpack_bytes is None:
            return None
        return _immutable(decode_json_object(self._assetpack_bytes, source=ASSETPACK_PATH))  # type: ignore[return-value]

    @property
    def bundle_id(self) -> str:
        return str(self.manifest["bundle_id"])

    @property
    def bundle_version(self) -> str:
        return str(self.manifest["bundle_version"])

    @property
    def bundle_hash(self) -> str:
        return str(self.manifest["bundle_hash"])

    @property
    def closed(self) -> bool:
        return self._closed

    def __copy__(self) -> LoadedComposedRuntimeBundle[T]:
        raise TypeError("LoadedComposedRuntimeBundle owns non-copyable snapshots")

    def __deepcopy__(self, memo: dict[int, object]) -> LoadedComposedRuntimeBundle[T]:
        del memo
        raise TypeError("LoadedComposedRuntimeBundle owns non-copyable snapshots")

    def __enter__(self) -> LoadedComposedRuntimeBundle[T]:
        if self._closed:
            raise ComposedBundleError("composed bundle snapshot is already closed")
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, traceback
        try:
            self.close()
        except ComposedBundleError as cleanup_error:
            if not note_cleanup_failure(
                exc,
                cleanup_error,
                context="composed bundle snapshot cleanup",
            ):
                raise

    def __del__(self) -> None:
        if getattr(self, "_closed", True):
            return
        try:
            self.close()
        except Exception as exc:
            warnings.warn(
                f"Could not finalize composed bundle snapshot: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )

    def close(self) -> None:
        if self._closed:
            return
        try:
            try:
                if self.renderpack is not None:
                    self.renderpack.close()
            finally:
                primary = sys.exception()
                try:
                    self._owner.close()
                except ResourceSnapshotError as cleanup_error:
                    if not note_cleanup_failure(
                        primary,
                        cleanup_error,
                        context="outer composed snapshot cleanup",
                    ):
                        raise
        except (RenderPackError, ResourceSnapshotError) as cleanup_error:
            raise ComposedBundleError(
                f"could not close composed bundle snapshot: {cleanup_error}"
            ) from cleanup_error
        object.__setattr__(self, "_closed", True)


def _exact(value: object, fields: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ComposedBundleError(f"{context} must be an object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise ComposedBundleError(
            f"{context} has invalid fields; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _identifier(value: object, context: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ComposedBundleError(f"{context} is invalid")
    assert isinstance(value, str)
    return value


def _semver(value: object, context: str) -> str:
    if type(value) is not str or _SEMVER.fullmatch(value) is None:
        raise ComposedBundleError(f"{context} must be stable SemVer")
    assert isinstance(value, str)
    return value


def _digest(value: object, context: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ComposedBundleError(f"{context} must be a lowercase SHA-256")
    assert isinstance(value, str)
    return value


def _integer(value: object, context: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ComposedBundleError(f"{context} must be in {minimum}..{maximum}")
    return value


def _portable_path(value: object, context: str) -> str:
    if type(value) is not str:
        raise ComposedBundleError(f"{context} must be a portable relative path")
    assert isinstance(value, str)
    relative = portable_relative_path(value)
    if (
        relative is None
        or value != relative.as_posix()
        or unicodedata.normalize("NFC", value) != value
        or len(value.encode("utf-8")) > MAX_PATH_BYTES
        or relative.parts[0] not in _ALLOWED_ROOTS
    ):
        raise ComposedBundleError(f"{context} must be a portable relative path")
    for component in relative.parts:
        folded = component.casefold()
        if (
            not is_portable_path_component(component)
            or folded.split(".", 1)[0] in _WINDOWS_RESERVED
            or component.startswith(".")
            or folded in _FORBIDDEN_FILENAMES
            or Path(folded).suffix in _FORBIDDEN_WEIGHT_SUFFIXES
        ):
            raise ComposedBundleError(f"{context} is unsafe on supported platforms")
    for component in relative.parts[1:]:
        if component.casefold() in _FORBIDDEN_PATH_COMPONENTS:
            raise ComposedBundleError(f"{context} exposes an authoring-only path")
    return value


def _canonical_hash(manifest: dict[str, Any]) -> str:
    return canonical_payload_hash(manifest, hash_field="bundle_hash")


def _validate_file_records(value: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ComposedBundleError(f"{context} must be a non-empty list")
    if len(value) > MAX_BUNDLE_FILES:
        raise ComposedBundleError(f"{context} exceeds the file-count bound")
    records: list[dict[str, Any]] = []
    exact_paths: set[str] = set()
    collision_paths: dict[tuple[str, ...], str] = {}
    prefixes: dict[tuple[str, ...], str] = {}
    total = 0
    for index, item in enumerate(value):
        record = _exact(item, _FILE_FIELDS, f"{context}/{index}")
        path = _portable_path(record["path"], f"{context}/{index}/path")
        if path in exact_paths:
            raise ComposedBundleError(f"{context} contains duplicate path {path}")
        relative = PurePosixPath(path)
        collision = portable_path_key(relative)
        previous = collision_paths.setdefault(collision, path)
        if previous != path:
            raise ComposedBundleError(
                f"{context} contains an NFC/casefold collision: {previous!r}, {path!r}"
            )
        parts: list[str] = []
        for component in relative.parts:
            parts.append(component)
            key = tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)
            prefix = "/".join(parts)
            prior_prefix = prefixes.setdefault(key, prefix)
            if prior_prefix != prefix:
                raise ComposedBundleError(
                    f"{context} contains an NFC/casefold prefix collision: "
                    f"{prior_prefix!r}, {prefix!r}"
                )
        exact_paths.add(path)
        _digest(record["sha256"], f"{context}/{index}/sha256")
        size = _integer(
            record["size"],
            f"{context}/{index}/size",
            minimum=0,
            maximum=MAX_OWNED_RESOURCE_BYTES,
        )
        total += size
        if total > MAX_BUNDLE_BYTES:
            raise ComposedBundleError(f"{context} exceeds the total byte bound")
        if record["media_type"] not in _ALLOWED_MEDIA_TYPES:
            raise ComposedBundleError(f"{context}/{index}/media_type is unsupported")
        records.append(record)
    if [record["path"] for record in records] != sorted(exact_paths):
        raise ComposedBundleError(f"{context} must be sorted by path")
    return records


def _validate_ref(
    value: object,
    fields: frozenset[str],
    *,
    context: str,
    path: str,
    format_name: str,
    format_version: int | None = 1,
) -> dict[str, Any]:
    reference = _exact(value, fields, context)
    if reference["path"] != path or reference["format"] != format_name:
        raise ComposedBundleError(f"{context} has an unsupported path or format")
    version = reference["format_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise ComposedBundleError(f"{context}/format_version must be an integer")
    if format_version is not None and version != format_version:
        raise ComposedBundleError(f"{context}/format_version is unsupported")
    _digest(reference["content_hash"], f"{context}/content_hash")
    return reference


def validate_composed_runtime_bundle_manifest(value: object) -> dict[str, Any]:
    """Validate one closed format-v1 composed-bundle manifest."""

    manifest = _exact(value, _MANIFEST_FIELDS, "composed bundle manifest")
    if (
        manifest["format"] != COMPOSED_BUNDLE_FORMAT
        or manifest["format_version"] != COMPOSED_BUNDLE_FORMAT_VERSION
        or isinstance(manifest["format_version"], bool)
    ):
        raise ComposedBundleError("unknown composed runtime bundle format")
    _identifier(manifest["bundle_id"], "bundle_id")
    _semver(manifest["bundle_version"], "bundle_version")

    target = _exact(
        manifest["compatibility_target"],
        _TARGET_FIELDS,
        "compatibility_target",
    )
    if target["platform"] not in PLATFORMS:
        raise ComposedBundleError("compatibility_target/platform is unsupported")
    _semver(target["runtime_api_version"], "compatibility_target/runtime_api_version")

    contracts = _exact(manifest["contracts"], _CONTRACT_FIELDS, "contracts")
    composition = _validate_ref(
        contracts["runtime_composition"],
        _COMPOSITION_REF_FIELDS,
        context="contracts/runtime_composition",
        path=COMPOSITION_PATH,
        format_name=RUNTIME_COMPOSITION_FORMAT,
    )
    _identifier(composition["world_id"], "contracts/runtime_composition/world_id")
    _semver(composition["release_id"], "contracts/runtime_composition/release_id")
    profile = _validate_ref(
        contracts["presentation_profile"],
        _PROFILE_REF_FIELDS,
        context="contracts/presentation_profile",
        path=PROFILE_PATH,
        format_name=RUNTIME_PRESENTATION_PROFILE_FORMAT,
    )
    _identifier(profile["id"], "contracts/presentation_profile/id")
    _validate_ref(
        contracts["capability_catalog"],
        _BASE_REF_FIELDS,
        context="contracts/capability_catalog",
        path=CATALOG_PATH,
        format_name=RUNTIME_CAPABILITY_CATALOG_FORMAT,
    )
    adapter = _validate_ref(
        contracts["runtime_adapter"],
        _ADAPTER_REF_FIELDS,
        context="contracts/runtime_adapter",
        path=ADAPTER_PATH,
        format_name=RUNTIME_ADAPTER_FORMAT,
    )
    _identifier(adapter["id"], "contracts/runtime_adapter/id")
    _semver(adapter["version"], "contracts/runtime_adapter/version")

    evidence = _validate_ref(
        manifest["compatibility_evidence"],
        _REPORT_REF_FIELDS,
        context="compatibility_evidence",
        path=REPORT_PATH,
        format_name=RUNTIME_COMPATIBILITY_REPORT_FORMAT,
    )
    _digest(evidence["composition_hash"], "compatibility_evidence/composition_hash")
    if evidence["composition_hash"] != composition["content_hash"]:
        raise ComposedBundleError("compatibility evidence is bound to a different composition")

    packs = _exact(manifest["packs"], _PACK_FIELDS, "packs")
    worldpack = _validate_ref(
        packs["worldpack"],
        _WORLDPACK_REF_FIELDS,
        context="packs/worldpack",
        path=WORLDPACK_PATH,
        format_name="isoworld.worldpack",
        format_version=None,
    )
    _integer(
        worldpack["format_version"],
        "packs/worldpack/format_version",
        minimum=1,
        maximum=5,
    )
    _identifier(worldpack["world_id"], "packs/worldpack/world_id")
    selected = 0
    for kind, expected_path, expected_format in (
        ("renderpack", RENDERPACK_PATH, "isoworld.renderpack"),
        ("assetpack", ASSETPACK_PATH, "rpg-world-forge.assetpack"),
    ):
        raw = packs[kind]
        if raw is None:
            continue
        selected += 1
        reference = _validate_ref(
            raw,
            _CONTENT_PACK_REF_FIELDS,
            context=f"packs/{kind}",
            path=expected_path,
            format_name=expected_format,
        )
        _digest(reference["world_content_hash"], f"packs/{kind}/world_content_hash")
    if selected == 0:
        raise ComposedBundleError("at least one renderpack or assetpack must be selected")

    files = _validate_file_records(manifest["files"], "files")
    licenses = _validate_file_records(manifest["licenses"], "licenses")
    if len(licenses) > MAX_LICENSE_FILES:
        raise ComposedBundleError("licenses exceeds the license-count bound")
    if any(not record["path"].startswith("licenses/") for record in licenses):
        raise ComposedBundleError("licenses may contain only licenses/** records")
    for record in licenses:
        parts = PurePosixPath(record["path"]).parts
        media_type = _LICENSE_MEDIA.get(Path(parts[-1]).suffix.casefold())
        if (
            len(parts) != 2
            or media_type is None
            or record["media_type"] != media_type
            or record["size"] > MAX_LICENSE_BYTES
        ):
            raise ComposedBundleError(
                "licenses must be bounded approved notice files directly under licenses/"
            )
    license_files = [record for record in files if record["path"].startswith("licenses/")]
    if licenses != license_files:
        raise ComposedBundleError(
            "licenses must exactly equal the non-empty licenses/** file subset"
        )
    expected_fixed = {
        COMPOSITION_PATH,
        PROFILE_PATH,
        CATALOG_PATH,
        ADAPTER_PATH,
        REPORT_PATH,
        WORLDPACK_PATH,
    }
    if packs["renderpack"] is not None:
        expected_fixed.add(RENDERPACK_PATH)
    if packs["assetpack"] is not None:
        expected_fixed.add(ASSETPACK_PATH)
    paths = {record["path"] for record in files}
    missing_fixed = expected_fixed - paths
    if missing_fixed:
        raise ComposedBundleError(f"files is missing fixed payloads: {sorted(missing_fixed)}")
    if packs["renderpack"] is None and any(path.startswith("packs/renderpack/") for path in paths):
        raise ComposedBundleError("files contains an unselected renderpack")
    if packs["assetpack"] is None and any(path.startswith("packs/assetpack/") for path in paths):
        raise ComposedBundleError("files contains an unselected assetpack")
    if {path for path in paths if path.startswith("contracts/")} != {
        COMPOSITION_PATH,
        PROFILE_PATH,
        CATALOG_PATH,
        ADAPTER_PATH,
    }:
        raise ComposedBundleError("contracts/ must contain exactly the four fixed contracts")
    if {path for path in paths if path.startswith("evidence/")} != {REPORT_PATH}:
        raise ComposedBundleError("evidence/ must contain exactly the compatibility report")
    if {path for path in paths if path.startswith("packs/worldpack/")} != {WORLDPACK_PATH}:
        raise ComposedBundleError("packs/worldpack/ must contain exactly the worldpack")
    allowed_prefixes = (
        "contracts/",
        "evidence/",
        "packs/worldpack/",
        "licenses/",
        *(("packs/renderpack/",) if packs["renderpack"] is not None else ()),
        *(("packs/assetpack/",) if packs["assetpack"] is not None else ()),
    )
    if any(not path.startswith(allowed_prefixes) for path in paths):
        raise ComposedBundleError("files contains a payload outside the exact layout")

    _digest(manifest["bundle_hash"], "bundle_hash")
    if _canonical_hash(manifest) != manifest["bundle_hash"]:
        raise ComposedBundleError("bundle_hash does not match the canonical manifest")
    return manifest


def _decode(payload: bytes, *, source: str | Path) -> dict[str, Any]:
    try:
        return decode_json_object(payload, source=source)
    except RuntimeIOError as exc:
        raise ComposedBundleError(str(exc)) from exc


def _read_bounded(path: Path, *, limit: int, context: str) -> bytes:
    descriptor: int | None = None
    try:
        before_path = path_file_stat(path)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        before = descriptor_file_stat(descriptor)
        before_state = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            stat.S_IFMT(before.st_mode),
            before.st_nlink,
        )
        path_state = (
            before_path.st_dev,
            before_path.st_ino,
            before_path.st_size,
            before_path.st_mtime_ns,
            before_path.st_ctime_ns,
            stat.S_IFMT(before_path.st_mode),
            before_path.st_nlink,
        )
        if (
            stat.S_ISLNK(before_path.st_mode)
            or bool(
                getattr(before_path, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before_state != path_state
        ):
            raise OSError("not a standalone regular file")
        if before.st_size > limit:
            raise OSError(f"exceeds the {limit}-byte limit")
        pieces: list[bytes] = []
        total = 0
        while total < before.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, before.st_size - total))
            if not chunk:
                raise OSError("ended before the captured size")
            pieces.append(chunk)
            total += len(chunk)
        if os.read(descriptor, 1):
            raise OSError("grew while reading")
        after = descriptor_file_stat(descriptor)
        after_path = path_file_stat(path)
        after_state = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            stat.S_IFMT(after.st_mode),
            after.st_nlink,
        )
        after_path_state = (
            after_path.st_dev,
            after_path.st_ino,
            after_path.st_size,
            after_path.st_mtime_ns,
            after_path.st_ctime_ns,
            stat.S_IFMT(after_path.st_mode),
            after_path.st_nlink,
        )
        if after_state != before_state or after_path_state != before_state:
            raise OSError("identity changed while reading")
        payload = b"".join(pieces)
    except OSError as exc:
        raise ComposedBundleError(f"Could not read {context}: {exc}") from exc
    finally:
        if descriptor is not None:
            _close_descriptor(descriptor, context=f"{context} descriptor cleanup")
    if len(payload) > limit:
        raise ComposedBundleError(f"{context} exceeds the {limit}-byte limit")
    return payload


def _runtime_boundary(document: dict[str, Any], context: str) -> None:
    format_name = document.get("format")
    if format_name in FORBIDDEN_GAME_JSON_FORMATS:
        raise ComposedBundleError(f"{context} contains authoring-only format {format_name!r}")
    detail = authoring_metadata_detail(document)
    if detail is not None:
        raise ComposedBundleError(f"{context} contains {detail}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        _close_descriptor(descriptor, context="directory fsync descriptor cleanup")


def _fsync_tree_directories(root: Path) -> None:
    _, relative_directories = _walk_exact_tree(root)
    directories = [root / PurePosixPath(relative) for relative in relative_directories]
    for directory in sorted(
        directories,
        key=lambda value: (len(value.parts), value.as_posix()),
        reverse=True,
    ):
        _fsync_directory(directory)
    _fsync_directory(root)


def _expected_directories(paths: set[str]) -> set[str]:
    return {
        parent.as_posix()
        for path in paths
        for parent in PurePosixPath(path).parents
        if parent.as_posix() != "."
    }


def _walk_exact_tree(root: Path) -> tuple[set[str], set[str]]:
    lexical_issues = validate_lexical_directory_root(root)
    if lexical_issues:
        raise ComposedBundleError(f"composed bundle root is unsafe: {', '.join(lexical_issues)}")
    files: set[str] = set()
    directories: set[str] = set()
    total = 0
    for current, names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        names.sort()
        file_names.sort()
        for name in names:
            path = current_path / name
            try:
                info = path.lstat()
            except OSError as exc:
                raise ComposedBundleError(
                    f"Could not inspect bundle directory {path}: {exc}"
                ) from exc
            if (
                stat.S_ISLNK(info.st_mode)
                or bool(
                    getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
                or not stat.S_ISDIR(info.st_mode)
            ):
                raise ComposedBundleError(
                    f"bundle contains a link, reparse point, or special directory: {path}"
                )
            relative = path.relative_to(root).as_posix()
            _portable_path(f"{relative}/placeholder", "bundle directory")
            directories.add(relative)
            if len(directories) > MAX_BUNDLE_FILES * 4:
                raise ComposedBundleError("bundle contains too many directories")
        for name in file_names:
            path = current_path / name
            try:
                info = path.lstat()
            except OSError as exc:
                raise ComposedBundleError(f"Could not inspect bundle file {path}: {exc}") from exc
            if (
                stat.S_ISLNK(info.st_mode)
                or bool(
                    getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
                or not stat.S_ISREG(info.st_mode)
            ):
                raise ComposedBundleError(
                    f"bundle contains a link, reparse point, or special file: {path}"
                )
            if info.st_nlink != 1:
                raise ComposedBundleError(f"bundle contains a hard-linked file: {path}")
            if info.st_size > MAX_OWNED_RESOURCE_BYTES:
                raise ComposedBundleError(f"bundle contains an oversized file: {path}")
            total += info.st_size
            if total > MAX_BUNDLE_BYTES:
                raise ComposedBundleError("bundle exceeds the total byte bound")
            relative = path.relative_to(root).as_posix()
            if relative != COMPOSED_BUNDLE_MANIFEST:
                _portable_path(relative, "bundle file")
            files.add(relative)
            if len(files) > MAX_BUNDLE_FILES + 1:
                raise ComposedBundleError("bundle contains too many files")
    return files, directories


def _close_owner_after_failure(
    owner: ResourceSnapshotOwner,
    original: BaseException,
) -> None:
    try:
        owner.close()
    except ResourceSnapshotError as cleanup_error:
        note_cleanup_failure(
            original,
            cleanup_error,
            context="private composed bundle snapshot cleanup",
        )


def _snapshot_bundle_root(
    source_root: Path,
    *,
    expected_bundle_hash: str,
) -> tuple[ResourceSnapshotOwner, dict[str, Any], list[dict[str, Any]]]:
    _digest(expected_bundle_hash, "expected_bundle_hash")
    owner = ResourceSnapshotOwner()
    manifest_relative = PurePosixPath(COMPOSED_BUNDLE_MANIFEST)
    try:
        manifest_capture = owner.materialize_file(
            source_root,
            manifest_relative,
            limit=MAX_MANIFEST_BYTES,
        )
        manifest_bytes = _read_bounded(
            manifest_capture.path,
            limit=MAX_MANIFEST_BYTES,
            context="captured composed bundle manifest",
        )
        manifest = validate_composed_runtime_bundle_manifest(
            _decode(manifest_bytes, source=COMPOSED_BUNDLE_MANIFEST)
        )
        if manifest_bytes != canonical_json_bytes(manifest):
            raise ComposedBundleError("composed bundle manifest is not canonically serialized")
        if manifest["bundle_hash"] != expected_bundle_hash:
            raise ComposedBundleError("composed bundle does not match the expected immutable hash")
        records = list(manifest["files"])
        expected_files = {
            COMPOSED_BUNDLE_MANIFEST,
            *(record["path"] for record in records),
        }
        expected_directories = _expected_directories(expected_files)
        actual_files, actual_directories = _walk_exact_tree(source_root)
        if actual_files != expected_files or actual_directories != expected_directories:
            raise ComposedBundleError(
                "composed bundle tree mismatch; "
                f"missing_files={sorted(expected_files - actual_files)}, "
                f"extra_files={sorted(actual_files - expected_files)}, "
                f"missing_directories={sorted(expected_directories - actual_directories)}, "
                f"extra_directories={sorted(actual_directories - expected_directories)}"
            )

        for record in records:
            relative = PurePosixPath(record["path"])
            captured = owner.materialize_file(
                source_root,
                relative,
                limit=max(1, record["size"]),
            )
            if captured.size != record["size"] or captured.sha256 != record["sha256"]:
                raise ComposedBundleError(
                    f"bundle file does not match its exact inventory: {record['path']}"
                )
        after_files, after_directories = _walk_exact_tree(source_root)
        if after_files != actual_files or after_directories != actual_directories:
            raise ComposedBundleError("composed bundle tree changed while snapshotting")
        return owner, manifest, records
    except BaseException as original:
        _close_owner_after_failure(owner, original)
        if isinstance(original, ComposedBundleError):
            raise
        if isinstance(original, (OSError, ResourceSnapshotError)):
            raise ComposedBundleError(str(original)) from original
        raise


def _record_by_path(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {record["path"]: record for record in records}


def _require_ref_matches_record(
    reference: dict[str, Any],
    records: dict[str, dict[str, Any]],
    *,
    context: str,
) -> None:
    record = records.get(reference["path"])
    if record is None:
        raise ComposedBundleError(f"{context} is absent from the file inventory")
    if record["media_type"] != "application/json":
        raise ComposedBundleError(f"{context} must be inventoried as application/json")


def _validate_json_payloads(
    owner: ResourceSnapshotOwner,
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    documents: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["media_type"] != "application/json":
            continue
        relative = PurePosixPath(record["path"])
        payload = _read_bounded(
            owner.resolve_file(relative),
            limit=min(MAX_RUNTIME_JSON_BYTES, max(1, record["size"])),
            context=f"runtime JSON {record['path']}",
        )
        document = _decode(payload, source=record["path"])
        _runtime_boundary(document, record["path"])
        documents[record["path"]] = document
    return documents


def _exact_pack_inventory(
    *,
    kind: str,
    document: dict[str, Any],
    records: dict[str, dict[str, Any]],
) -> None:
    manifest_path = RENDERPACK_PATH if kind == "renderpack" else ASSETPACK_PATH
    prefix = f"packs/{kind}/"
    referenced = {manifest_path}
    for asset_index, asset in enumerate(document.get("assets", [])):
        if not isinstance(asset, dict):
            continue
        for file_index, item in enumerate(asset.get("files", [])):
            if not isinstance(item, dict):
                continue
            raw_path = item.get("path")
            normalized = portable_relative_path(raw_path)
            if normalized is None:
                raise ComposedBundleError(
                    f"{kind} asset path is not portable: assets/{asset_index}/files/{file_index}"
                )
            bundled_path = f"{prefix}{normalized.as_posix()}"
            referenced.add(bundled_path)
            record = records.get(bundled_path)
            if record is None:
                raise ComposedBundleError(
                    f"{kind} referenced file is absent from bundle inventory: {raw_path}"
                )
            if (
                record["sha256"] != item.get("sha256")
                or record["media_type"] != item.get("media_type")
                or (kind == "assetpack" and record["size"] != item.get("size"))
            ):
                raise ComposedBundleError(
                    f"{kind} referenced file metadata disagrees with inventory: {raw_path}"
                )
    actual = {path for path in records if path.startswith(prefix)}
    if referenced != actual:
        raise ComposedBundleError(
            f"{kind} payload inventory is not exact; "
            f"missing={sorted(referenced - actual)}, extra={sorted(actual - referenced)}"
        )


def _load_from_snapshot(
    owner: ResourceSnapshotOwner,
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    platform: str,
    runtime_api_version: str,
    registry: StaticRuntimeAdapterRegistry[T],
) -> LoadedComposedRuntimeBundle[T]:
    target = manifest["compatibility_target"]
    if target["platform"] != platform:
        raise ComposedBundleError(
            "requested platform does not match the sealed compatibility target"
        )
    if target["runtime_api_version"] != runtime_api_version:
        raise ComposedBundleError(
            "requested runtime API does not match the sealed compatibility target"
        )
    if type(registry) is not StaticRuntimeAdapterRegistry:
        raise TypeError("registry must be a StaticRuntimeAdapterRegistry")

    records_by_path = _record_by_path(records)
    for reference in manifest["contracts"].values():
        _require_ref_matches_record(
            reference,
            records_by_path,
            context=str(reference["path"]),
        )
    _require_ref_matches_record(
        manifest["compatibility_evidence"],
        records_by_path,
        context=REPORT_PATH,
    )
    for reference in manifest["packs"].values():
        if reference is not None:
            _require_ref_matches_record(
                reference,
                records_by_path,
                context=str(reference["path"]),
            )

    documents = _validate_json_payloads(owner, records)
    try:
        catalog = validate_runtime_capability_catalog(documents[CATALOG_PATH])
        profile = validate_runtime_presentation_profile(documents[PROFILE_PATH])
        adapter = validate_runtime_adapter(documents[ADAPTER_PATH])
        composition = validate_runtime_composition(documents[COMPOSITION_PATH])
        report = validate_runtime_compatibility_report(documents[REPORT_PATH])
    except (KeyError, RuntimeCompositionError) as exc:
        raise ComposedBundleError(f"Bundled M6 contract is invalid: {exc}") from exc

    contracts = manifest["contracts"]
    expected_contract_values = (
        (
            contracts["capability_catalog"],
            catalog,
            (),
        ),
        (
            contracts["presentation_profile"],
            profile,
            ("id",),
        ),
        (
            contracts["runtime_adapter"],
            adapter,
            ("id", "version"),
        ),
        (
            contracts["runtime_composition"],
            composition,
            ("world_id", "release_id"),
        ),
    )
    for reference, document, identity_fields in expected_contract_values:
        for field_name in ("format", "format_version", "content_hash", *identity_fields):
            if reference[field_name] != document[field_name]:
                raise ComposedBundleError(
                    f"manifest contract reference disagrees with {reference['path']}: {field_name}"
                )
    if report["content_hash"] != manifest["compatibility_evidence"]["content_hash"]:
        raise ComposedBundleError("manifest compatibility evidence hash disagrees with its report")
    if report["composition_hash"] != composition["content_hash"]:
        raise ComposedBundleError("compatibility report targets a different composition")

    packs = composition["packs"]
    if packs["worldpack"]["path"] != WORLDPACK_PATH:
        raise ComposedBundleError("composition worldpack path is not the bundled fixed path")
    if ("renderpack" in packs) != (manifest["packs"]["renderpack"] is not None):
        raise ComposedBundleError("composition and bundle disagree on renderpack selection")
    if ("assetpack" in packs) != (manifest["packs"]["assetpack"] is not None):
        raise ComposedBundleError("composition and bundle disagree on assetpack selection")
    if "renderpack" in packs and packs["renderpack"]["path"] != RENDERPACK_PATH:
        raise ComposedBundleError("composition renderpack path is not the bundled fixed path")
    if "assetpack" in packs and packs["assetpack"]["path"] != ASSETPACK_PATH:
        raise ComposedBundleError("composition assetpack path is not the bundled fixed path")

    root = owner.root
    try:
        registered = load_registered_runtime_composition(
            root,
            capability_catalog_path=CATALOG_PATH,
            presentation_profile_path=PROFILE_PATH,
            runtime_adapter_path=ADAPTER_PATH,
            composition_path=COMPOSITION_PATH,
            platform=platform,
            runtime_api_version=runtime_api_version,
            registry=registry,
        )
    except RuntimeCompositionError as exc:
        raise ComposedBundleError(
            f"Bundled runtime composition is not statically loadable: {exc}"
        ) from exc
    verification = registered.verification
    report_bytes = canonical_json_bytes(verification.report)
    bundled_report_bytes = _read_bounded(
        owner.resolve_file(PurePosixPath(REPORT_PATH)),
        limit=MAX_MANIFEST_BYTES,
        context="bundled compatibility report",
    )
    if bundled_report_bytes != report_bytes:
        raise ComposedBundleError(
            "bundled compatibility evidence is not the freshly recomputed canonical report"
        )
    if not verification.compatible:
        raise ComposedBundleError("composed runtime bundle is statically incompatible")

    worldpack_path = owner.resolve_file(PurePosixPath(WORLDPACK_PATH))
    try:
        worldpack = load_worldpack(worldpack_path)
    except (OSError, WorldPackError) as exc:
        raise ComposedBundleError(f"Bundled worldpack is invalid: {exc}") from exc
    worldpack_raw = documents[WORLDPACK_PATH]
    worldpack_ref = manifest["packs"]["worldpack"]
    if (
        worldpack_raw.get("format") != worldpack_ref["format"]
        or worldpack.format_version != worldpack_ref["format_version"]
        or worldpack.world_id != worldpack_ref["world_id"]
        or worldpack.content_hash != worldpack_ref["content_hash"]
    ):
        raise ComposedBundleError("manifest worldpack reference disagrees with the pack")
    if (
        composition["world_id"] != worldpack.world_id
        or composition["world_content_hash"] != worldpack.content_hash
        or packs["worldpack"]["content_hash"] != worldpack.content_hash
    ):
        raise ComposedBundleError("world identity disagrees across composition and worldpack")

    renderpack: RenderPack | None = None
    assetpack_bytes: bytes | None = None
    binding_evidence: list[PackSlotBinding] = []
    try:
        if manifest["packs"]["renderpack"] is not None:
            renderpack_document = documents[RENDERPACK_PATH]
            _exact_pack_inventory(
                kind="renderpack",
                document=renderpack_document,
                records=records_by_path,
            )
            renderpack = load_renderpack(
                owner.resolve_file(PurePosixPath(RENDERPACK_PATH)),
                worldpack,
            )
            reference = manifest["packs"]["renderpack"]
            if (
                renderpack_document.get("format") != reference["format"]
                or renderpack_document.get("format_version") != reference["format_version"]
                or renderpack.content_hash != reference["content_hash"]
                or renderpack.world_content_hash != reference["world_content_hash"]
                or packs["renderpack"]["content_hash"] != renderpack.content_hash
            ):
                raise ComposedBundleError(
                    "manifest/composition renderpack references disagree with the pack"
                )
            render_asset_kinds = {asset.id: asset.kind for asset in renderpack.assets}
            binding_evidence.extend(
                PackSlotBinding(
                    "renderpack",
                    binding.slot,
                    binding.asset_id,
                    render_asset_kinds[binding.asset_id],
                    None,
                )
                for binding in renderpack.bindings
            )
        if manifest["packs"]["assetpack"] is not None:
            assetpack_document = documents[ASSETPACK_PATH]
            _exact_pack_inventory(
                kind="assetpack",
                document=assetpack_document,
                records=records_by_path,
            )
            verified_assetpack = verify_assetpack(
                owner.resolve_file(PurePosixPath(ASSETPACK_PATH)),
                worldpack_path,
            )
            reference = manifest["packs"]["assetpack"]
            if (
                verified_assetpack["format"] != reference["format"]
                or verified_assetpack["format_version"] != reference["format_version"]
                or verified_assetpack["content_hash"] != reference["content_hash"]
                or verified_assetpack["world_content_hash"] != reference["world_content_hash"]
                or packs["assetpack"]["content_hash"] != verified_assetpack["content_hash"]
            ):
                raise ComposedBundleError(
                    "manifest/composition assetpack references disagree with the pack"
                )
            asset_by_id = {asset["id"]: asset for asset in verified_assetpack["assets"]}
            binding_evidence.extend(
                PackSlotBinding(
                    "assetpack",
                    binding["slot"],
                    binding["asset_id"],
                    asset_by_id[binding["asset_id"]]["kind"],
                    binding["representation"],
                )
                for binding in verified_assetpack["bindings"]
            )
            assetpack_bytes = _read_bounded(
                owner.resolve_file(PurePosixPath(ASSETPACK_PATH)),
                limit=MAX_MANIFEST_BYTES,
                context="bundled assetpack",
            )
        validate_composition_slot_ownership(
            profile["layers"],
            composition["slot_owners"],
            binding_evidence,
        )
    except (AssetPackError, CompositionPlanError, RenderPackError, OSError) as exc:
        if renderpack is not None:
            try:
                renderpack.close()
            except RenderPackError as cleanup_error:
                note_cleanup_failure(
                    exc,
                    cleanup_error,
                    context="renderpack snapshot cleanup",
                )
        if isinstance(exc, ComposedBundleError):
            raise
        if isinstance(exc, CompositionPlanError):
            raise ComposedBundleError(f"Bundled slot ownership is invalid: {exc}") from exc
        raise ComposedBundleError(f"Bundled content pack is invalid: {exc}") from exc
    except BaseException as original:
        if renderpack is not None:
            try:
                renderpack.close()
            except RenderPackError as cleanup:
                note_cleanup_failure(
                    original,
                    cleanup,
                    context="renderpack snapshot cleanup",
                )
        raise

    try:
        for record in records:
            owner.resolve_file(PurePosixPath(record["path"]))
    except BaseException as original:
        if renderpack is not None:
            try:
                renderpack.close()
            except RenderPackError as cleanup_error:
                note_cleanup_failure(
                    original,
                    cleanup_error,
                    context="renderpack snapshot cleanup",
                )
        raise
    manifest_bytes = canonical_json_bytes(manifest)
    return LoadedComposedRuntimeBundle(
        _owner=owner,
        _manifest_bytes=manifest_bytes,
        _assetpack_bytes=assetpack_bytes,
        worldpack=worldpack,
        renderpack=renderpack,
        registered=registered,
        verification=verification,
    )


def load_composed_runtime_bundle(
    path: str | Path,
    *,
    expected_bundle_hash: str,
    platform: str,
    runtime_api_version: str,
    registry: StaticRuntimeAdapterRegistry[T],
) -> LoadedComposedRuntimeBundle[T]:
    """Load a hash-pinned bundle into private snapshots without invoking its adapter."""

    if platform not in PLATFORMS:
        raise ComposedBundleError("platform is unsupported")
    _semver(runtime_api_version, "runtime_api_version")
    try:
        root = require_standalone_composed_bundle_root(path)
    except RepositoryBoundaryError as exc:
        raise ComposedBundleError(str(exc)) from exc
    owner, manifest, records = _snapshot_bundle_root(
        root,
        expected_bundle_hash=expected_bundle_hash,
    )
    try:
        return _load_from_snapshot(
            owner,
            manifest,
            records,
            platform=platform,
            runtime_api_version=runtime_api_version,
            registry=registry,
        )
    except BaseException as original:
        _close_owner_after_failure(owner, original)
        raise


def verify_composed_runtime_bundle(
    path: str | Path,
    *,
    expected_bundle_hash: str,
    platform: str,
    runtime_api_version: str,
    registry: StaticRuntimeAdapterRegistry[T],
) -> LoadedComposedRuntimeBundle[T]:
    """Verify and own one composed bundle; callers must close the returned value."""

    return load_composed_runtime_bundle(
        path,
        expected_bundle_hash=expected_bundle_hash,
        platform=platform,
        runtime_api_version=runtime_api_version,
        registry=registry,
    )


def verify_installed_composed_runtime_bundle(
    path: str | Path,
    *,
    expected_directory_identity: DirectoryIdentity,
    expected_bundle_hash: str,
    platform: str,
    runtime_api_version: str,
    registry: StaticRuntimeAdapterRegistry[T],
) -> LoadedComposedRuntimeBundle[T]:
    """Verify an already-published bundle while retaining its exact directory identity."""

    if platform not in PLATFORMS:
        raise ComposedBundleError("platform is unsupported")
    _semver(runtime_api_version, "runtime_api_version")
    root_input = Path(path)
    if root_input.is_symlink():
        raise ComposedBundleError("installed composed bundle root cannot be a symbolic link")
    try:
        root = root_input.resolve(strict=True)
    except OSError as exc:
        raise ComposedBundleError(str(exc)) from exc
    if root != root_input.absolute():
        raise ComposedBundleError("installed composed bundle path cannot contain symbolic links")
    try:
        before_identity = directory_identity(
            root,
            context="installed composed bundle",
        )
    except (DirectoryPublishError, OSError) as exc:
        raise ComposedBundleError(str(exc)) from exc
    if before_identity != expected_directory_identity:
        raise ComposedBundleError("installed composed bundle directory identity changed")
    owner, manifest, records = _snapshot_bundle_root(
        root,
        expected_bundle_hash=expected_bundle_hash,
    )
    try:
        loaded = _load_from_snapshot(
            owner,
            manifest,
            records,
            platform=platform,
            runtime_api_version=runtime_api_version,
            registry=registry,
        )
    except BaseException as original:
        _close_owner_after_failure(owner, original)
        raise
    try:
        after_identity = directory_identity(
            root,
            context="installed composed bundle after snapshot",
        )
        if after_identity != expected_directory_identity:
            raise ComposedBundleError(
                "installed composed bundle directory identity changed during verification"
            )
    except (ComposedBundleError, DirectoryPublishError, OSError) as original:
        try:
            loaded.close()
        except ComposedBundleError as cleanup:
            note_cleanup_failure(
                original,
                cleanup,
                context="installed composed bundle cleanup",
            )
        if isinstance(original, ComposedBundleError):
            raise
        raise ComposedBundleError(str(original)) from original
    return loaded


def _identity_document(identity: DirectoryIdentity) -> dict[str, int]:
    return {"device": identity[0], "inode": identity[1]}


def _identity_from_document(value: object, context: str) -> DirectoryIdentity:
    document = _exact(value, _IDENTITY_FIELDS, context)
    return (
        _integer(document["device"], f"{context}/device", minimum=0, maximum=2**64 - 1),
        _integer(document["inode"], f"{context}/inode", minimum=0, maximum=2**64 - 1),
    )


def _journal_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.composed-bundle.journal.json"


def _lock_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.composed-bundle.lock"


@contextmanager
def _destination_lock(destination: Path) -> Iterator[None]:
    path = _lock_path(destination)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
        info = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ComposedBundleError("composed bundle lock is unsafe")
        if info.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
            _fsync_directory(path.parent)
        elif info.st_size != 1:
            raise ComposedBundleError("composed bundle lock has invalid contents")
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, 1) != b"\0":
            raise ComposedBundleError("composed bundle lock has invalid contents")
        try:
            if os.name == "nt":  # pragma: no cover - Windows CI
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ComposedBundleError("another composed bundle publication is in progress") from exc
        yield
    except ComposedBundleError:
        raise
    except OSError as exc:
        raise ComposedBundleError(f"Could not acquire composed bundle lock: {exc}") from exc
    finally:
        if "descriptor" in locals():
            _close_descriptor(
                descriptor,
                context="composed bundle lock descriptor cleanup",
            )


def _journal_document(
    *,
    operation_id: str,
    state: str,
    stage: Path,
    destination: Path,
    stage_identity: DirectoryIdentity,
    platform: str,
    runtime_api_version: str,
    bundle_hash: str | None,
) -> dict[str, Any]:
    return {
        "format": COMPOSED_BUNDLE_JOURNAL_FORMAT,
        "format_version": COMPOSED_BUNDLE_JOURNAL_VERSION,
        "operation_id": operation_id,
        "state": state,
        "stage_name": stage.name,
        "destination_name": destination.name,
        "stage_identity": _identity_document(stage_identity),
        "platform": platform,
        "runtime_api_version": runtime_api_version,
        "bundle_hash": bundle_hash,
    }


def _validate_journal(value: object, destination: Path) -> dict[str, Any]:
    journal = _exact(value, _JOURNAL_FIELDS, "composed bundle journal")
    if (
        journal["format"] != COMPOSED_BUNDLE_JOURNAL_FORMAT
        or journal["format_version"] != COMPOSED_BUNDLE_JOURNAL_VERSION
        or isinstance(journal["format_version"], bool)
    ):
        raise ComposedBundleError("unknown composed bundle journal format")
    operation_id = journal["operation_id"]
    if type(operation_id) is not str or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        raise ComposedBundleError("journal operation_id is invalid")
    if journal["state"] not in {"copying", "ready"}:
        raise ComposedBundleError("journal state is invalid")
    if journal["destination_name"] != destination.name:
        raise ComposedBundleError("journal destination identity is invalid")
    stage_name = journal["stage_name"]
    if (
        type(stage_name) is not str
        or "/" in stage_name
        or "\\" in stage_name
        or not stage_name.startswith(f".{destination.name}.composed-")
        or not is_portable_path_component(stage_name)
    ):
        raise ComposedBundleError("journal stage name is invalid")
    _identity_from_document(journal["stage_identity"], "journal/stage_identity")
    if journal["platform"] not in PLATFORMS:
        raise ComposedBundleError("journal platform is invalid")
    _semver(journal["runtime_api_version"], "journal/runtime_api_version")
    if journal["state"] == "copying":
        if journal["bundle_hash"] is not None:
            raise ComposedBundleError("copying journal must not claim a bundle hash")
    else:
        _digest(journal["bundle_hash"], "journal/bundle_hash")
    return journal


def _write_journal(
    path: Path,
    document: dict[str, Any],
    *,
    create: bool,
) -> tuple[int, int]:
    payload = canonical_json_bytes(document)
    if create:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise ComposedBundleError("composed bundle recovery journal already exists") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ComposedBundleError("new composed bundle journal is unsafe")
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fsync(descriptor)
            identity = (info.st_dev, info.st_ino)
        finally:
            _close_descriptor(
                descriptor,
                context="composed bundle journal descriptor cleanup",
            )
        _fsync_directory(path.parent)
        return identity

    temporary = path.parent / f".{path.name}.replace-{uuid.uuid4().hex}"
    try:
        identity = _write_journal(temporary, document, create=True)
        os.replace(temporary, path)
        current = path.lstat()
        if (current.st_dev, current.st_ino) != identity:
            raise ComposedBundleError("replaced composed bundle journal changed identity")
        _fsync_directory(path.parent)
        return identity
    finally:
        try:
            info = temporary.lstat()
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                temporary.unlink()


def _read_journal(
    path: Path,
    destination: Path,
) -> tuple[dict[str, Any], tuple[int, int]] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ComposedBundleError(f"Could not inspect composed bundle journal: {exc}") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_size > MAX_MANIFEST_BYTES
    ):
        raise ComposedBundleError("composed bundle journal is unsafe")
    payload = _read_bounded(
        path,
        limit=MAX_MANIFEST_BYTES,
        context="composed bundle journal",
    )
    document = _validate_journal(_decode(payload, source=path), destination)
    if payload != canonical_json_bytes(document):
        raise ComposedBundleError("composed bundle journal is not canonical")
    current = path.lstat()
    identity = (info.st_dev, info.st_ino)
    if (current.st_dev, current.st_ino) != identity:
        raise ComposedBundleError("composed bundle journal changed while reading")
    return document, identity


def _remove_owned_journal(path: Path, identity: tuple[int, int]) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError:
        return
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or (current.st_dev, current.st_ino) != identity
    ):
        raise ComposedBundleError("refused to remove a replaced composed bundle journal")
    path.unlink()
    _fsync_directory(path.parent)


def _verify_incomplete_stage(path: Path) -> None:
    _walk_exact_tree(path)


def _optional_directory_identity(
    path: Path,
    *,
    context: str,
) -> DirectoryIdentity | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ComposedBundleError(f"Could not inspect {context} {path}: {exc}") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        or not stat.S_ISDIR(info.st_mode)
    ):
        raise ComposedBundleError(f"{context} must be a real directory: {path}")
    return info.st_dev, info.st_ino


def _recover_journal(
    destination: Path,
    *,
    platform: str,
    runtime_api_version: str,
    registry: StaticRuntimeAdapterRegistry[T],
) -> None:
    path = _journal_path(destination)
    loaded = _read_journal(path, destination)
    if loaded is None:
        return
    journal, journal_identity = loaded
    if journal["platform"] != platform or journal["runtime_api_version"] != runtime_api_version:
        raise ComposedBundleError("existing journal targets a different platform or runtime API")
    stage = destination.parent / journal["stage_name"]
    expected_identity = _identity_from_document(
        journal["stage_identity"],
        "journal/stage_identity",
    )
    stage_identity = _optional_directory_identity(
        stage,
        context="recovery stage",
    )
    destination_identity = _optional_directory_identity(
        destination,
        context="recovery destination",
    )

    if journal["state"] == "copying":
        if destination_identity is not None or stage_identity is None:
            raise ComposedBundleError(
                "incomplete journal state changed; preserving journal and filesystem"
            )
        if stage_identity != expected_identity:
            raise ComposedBundleError(
                "incomplete stage identity changed; preserving journal and filesystem"
            )
        quarantine_and_remove_owned_directory(
            stage,
            expected_identity,
            verify=_verify_incomplete_stage,
        )
        if (
            _optional_directory_identity(stage, context="cleaned incomplete stage") is not None
            or _optional_directory_identity(
                destination,
                context="destination after incomplete cleanup",
            )
            is not None
        ):
            raise ComposedBundleError(
                "publication paths changed after incomplete cleanup; preserving journal"
            )
        _remove_owned_journal(path, journal_identity)
        return

    bundle_hash = journal["bundle_hash"]
    assert isinstance(bundle_hash, str)
    if destination_identity is not None and stage_identity is None:
        if destination_identity != expected_identity:
            raise ComposedBundleError("published destination identity changed; preserving journal")
        with verify_composed_runtime_bundle(
            destination,
            expected_bundle_hash=bundle_hash,
            platform=platform,
            runtime_api_version=runtime_api_version,
            registry=registry,
        ):
            pass
        if (
            _optional_directory_identity(
                destination,
                context="verified recovered destination",
            )
            != expected_identity
            or _optional_directory_identity(stage, context="recovered absent stage") is not None
        ):
            raise ComposedBundleError(
                "published destination changed after verification; preserving journal"
            )
        _remove_owned_journal(path, journal_identity)
        return
    if stage_identity is not None and destination_identity is None:
        if stage_identity != expected_identity:
            raise ComposedBundleError("ready stage identity changed; preserving journal")

        def verify_ready(candidate: Path) -> None:
            with verify_composed_runtime_bundle(
                candidate,
                expected_bundle_hash=bundle_hash,
                platform=platform,
                runtime_api_version=runtime_api_version,
                registry=registry,
            ):
                pass

        quarantine_and_remove_owned_directory(
            stage,
            expected_identity,
            verify=verify_ready,
        )
        if (
            _optional_directory_identity(stage, context="cleaned ready stage") is not None
            or _optional_directory_identity(
                destination,
                context="destination after ready cleanup",
            )
            is not None
        ):
            raise ComposedBundleError(
                "publication paths changed after ready cleanup; preserving journal"
            )
        _remove_owned_journal(path, journal_identity)
        return
    raise ComposedBundleError(
        "ready journal has an ambiguous stage/destination state; preserving it"
    )


def _ensure_stage_parent(stage: Path, relative: PurePosixPath) -> Path:
    current = stage
    for part in relative.parts[:-1]:
        current /= part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ComposedBundleError(f"staging parent is unsafe: {current}")
        if os.name == "posix":
            os.chmod(current, 0o700)
    return current


def _copy_captured_to_stage(
    owner: ResourceSnapshotOwner,
    captured: MaterializedResource,
    destination_relative: PurePosixPath,
    stage: Path,
) -> None:
    parent = _ensure_stage_parent(stage, destination_relative)
    target = parent / destination_relative.name
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(target, flags, 0o600)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size != 0:
            raise ComposedBundleError(f"staging target is not exclusive: {target}")
        source = owner.resolve_file(destination_relative)
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short write while copying staged payload")
                    view = view[written:]
        if size != captured.size or digest.hexdigest() != captured.sha256:
            raise ComposedBundleError(
                f"captured payload changed while copying: {destination_relative}"
            )
        owner.resolve_file(destination_relative)
        os.fsync(descriptor)
        sealed = os.fstat(descriptor)
        current = target.lstat()
        if (
            (sealed.st_dev, sealed.st_ino) != (opened.st_dev, opened.st_ino)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            or sealed.st_size != captured.size
            or current.st_size != captured.size
            or sealed.st_nlink != 1
            or current.st_nlink != 1
        ):
            raise ComposedBundleError(f"staging target changed while copying: {target}")
    except OSError as exc:
        raise ComposedBundleError(f"Could not stage {destination_relative}: {exc}") from exc
    finally:
        if descriptor is not None:
            _close_descriptor(
                descriptor,
                context="staged payload descriptor cleanup",
            )


def _capture_source(
    owner: ResourceSnapshotOwner,
    stage: Path,
    source: str | Path,
    destination_relative: str,
    *,
    limit: int,
) -> MaterializedResource:
    source_path = Path(source)
    source_absolute = source_path if source_path.is_absolute() else Path.cwd() / source_path
    destination = PurePosixPath(destination_relative)
    captured = owner.materialize_file(
        source_absolute.parent,
        PurePosixPath(source_absolute.name),
        destination,
        limit=limit,
    )
    _copy_captured_to_stage(owner, captured, destination, stage)
    return captured


def _capture_pack_resources(
    *,
    owner: ResourceSnapshotOwner,
    stage: Path,
    source_manifest: Path,
    document: dict[str, Any],
    kind: str,
    media_types: dict[str, str],
) -> None:
    seen: dict[tuple[str, ...], str] = {}
    for asset_index, asset in enumerate(document.get("assets", [])):
        if not isinstance(asset, dict):
            continue
        files = asset.get("files", [])
        if not isinstance(files, list):
            continue
        for file_index, item in enumerate(files):
            if not isinstance(item, dict):
                continue
            normalized = portable_relative_path(item.get("path"))
            if normalized is None:
                raise ComposedBundleError(
                    f"{kind} source path is unsafe: assets/{asset_index}/files/{file_index}"
                )
            collision = portable_path_key(normalized)
            prior = seen.setdefault(collision, normalized.as_posix())
            if prior != normalized.as_posix():
                raise ComposedBundleError(
                    f"{kind} source paths collide under NFC/casefold: "
                    f"{prior!r}, {normalized.as_posix()!r}"
                )
            target = f"packs/{kind}/{normalized.as_posix()}"
            declared_size = item.get("size")
            limit = (
                declared_size
                if kind == "assetpack"
                and isinstance(declared_size, int)
                and not isinstance(declared_size, bool)
                and declared_size > 0
                else MAX_OWNED_RESOURCE_BYTES
            )
            if limit > MAX_OWNED_RESOURCE_BYTES:
                raise ComposedBundleError(f"{kind} source file exceeds the bundle limit")
            captured = _capture_source(
                owner,
                stage,
                source_manifest.parent / Path(*normalized.parts),
                target,
                limit=max(1, limit),
            )
            if captured.sha256 != item.get("sha256"):
                raise ComposedBundleError(
                    f"{kind} source hash disagrees with {normalized.as_posix()}"
                )
            if kind == "assetpack" and captured.size != declared_size:
                raise ComposedBundleError(
                    f"{kind} source size disagrees with {normalized.as_posix()}"
                )
            media_type = item.get("media_type")
            if media_type not in _ALLOWED_MEDIA_TYPES:
                raise ComposedBundleError(
                    f"{kind} source media type is unsupported: {normalized.as_posix()}"
                )
            media_types[target] = media_type


def _write_new_stage_file(stage: Path, relative: str, payload: bytes) -> None:
    relative_path = PurePosixPath(relative)
    parent = _ensure_stage_parent(stage, relative_path)
    path = parent / relative_path.name
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        current = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            or info.st_size != len(payload)
        ):
            raise ComposedBundleError(f"new staged file changed while writing: {path}")
    finally:
        _close_descriptor(
            descriptor,
            context="new staged file descriptor cleanup",
        )


def _file_record(path: Path, relative: str, media_type: str) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ComposedBundleError(f"staged payload is unsafe: {relative}")
    return {
        "path": relative,
        "sha256": _sha256(path),
        "size": info.st_size,
        "media_type": media_type,
    }


def _license_targets(
    license_sources: Mapping[str, str | Path],
) -> tuple[tuple[str, Path, str], ...]:
    if not isinstance(license_sources, Mapping) or not license_sources:
        raise ComposedBundleError("license_sources must be a non-empty explicit mapping")
    if len(license_sources) > MAX_LICENSE_FILES:
        raise ComposedBundleError("license_sources exceeds the license-count bound")
    results: list[tuple[str, Path, str]] = []
    collisions: set[str] = set()
    for name, source in license_sources.items():
        if (
            type(name) is not str
            or not is_portable_path_component(name)
            or name.startswith(".")
            or "/" in name
            or "\\" in name
        ):
            raise ComposedBundleError(f"license notice name is unsafe: {name!r}")
        media_type = _LICENSE_MEDIA.get(Path(name).suffix.casefold())
        if media_type is None:
            raise ComposedBundleError(f"license notice type is not approved: {name}")
        folded = unicodedata.normalize("NFC", name).casefold()
        if folded in collisions:
            raise ComposedBundleError("license notice names collide under NFC/casefold")
        collisions.add(folded)
        results.append((f"licenses/{name}", Path(source), media_type))
    return tuple(sorted(results, key=lambda item: item[0]))


def _publication_outcome(
    stage: Path,
    destination: Path,
    *,
    expected_identity: DirectoryIdentity,
    expected_bundle_hash: str,
    platform: str,
    runtime_api_version: str,
    registry: StaticRuntimeAdapterRegistry[T],
) -> _PublicationOutcome:
    """Classify a failed publication without following either path."""

    try:
        destination_identity = _optional_directory_identity(
            destination,
            context="failed publication destination",
        )
    except Exception:
        return _PublicationOutcome.UNCERTAIN

    if destination_identity == expected_identity:
        try:
            with verify_composed_runtime_bundle(
                destination,
                expected_bundle_hash=expected_bundle_hash,
                platform=platform,
                runtime_api_version=runtime_api_version,
                registry=registry,
            ):
                pass
        except Exception:
            return _PublicationOutcome.UNCERTAIN
        return _PublicationOutcome.DESTINATION_OWNED

    try:
        stage_identity = _optional_directory_identity(
            stage,
            context="failed publication stage",
        )
    except Exception:
        return _PublicationOutcome.UNCERTAIN
    if destination_identity is None and stage_identity == expected_identity:
        return _PublicationOutcome.STAGE_OWNED
    return _PublicationOutcome.UNCERTAIN


def _cleanup_stage(
    stage: Path,
    identity: DirectoryIdentity,
    *,
    ready_hash: str | None,
    platform: str,
    runtime_api_version: str,
    registry: StaticRuntimeAdapterRegistry[T],
) -> bool:
    current_identity = _optional_directory_identity(
        stage,
        context="cleanup stage",
    )
    if current_identity is None:
        return False
    if current_identity != identity:
        raise ComposedBundleError("cleanup stage identity changed")

    def verify(candidate: Path) -> None:
        if ready_hash is None:
            _verify_incomplete_stage(candidate)
            return
        with verify_composed_runtime_bundle(
            candidate,
            expected_bundle_hash=ready_hash,
            platform=platform,
            runtime_api_version=runtime_api_version,
            registry=registry,
        ):
            pass

    quarantine_and_remove_owned_directory(stage, identity, verify=verify)
    return True


def build_composed_runtime_bundle(
    capability_catalog_path: str | Path,
    presentation_profile_path: str | Path,
    runtime_adapter_path: str | Path,
    composition_path: str | Path,
    worldpack_path: str | Path,
    destination: str | Path,
    *,
    bundle_id: str,
    bundle_version: str,
    platform: str,
    registry: StaticRuntimeAdapterRegistry[T],
    license_sources: Mapping[str, str | Path],
    renderpack_path: str | Path | None = None,
    assetpack_path: str | Path | None = None,
    runtime_api_version: str = RUNTIME_API_VERSION,
) -> LoadedComposedRuntimeBundle[T]:
    """Build, verify, and exclusively publish one immutable composed bundle.

    Compatibility evidence is intentionally not accepted as an input. The
    builder computes it from the exact staged contracts and packs.
    """

    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        raise ComposedBundleError("composed bundle publication supports only Linux and Windows")
    _identifier(bundle_id, "bundle_id")
    _semver(bundle_version, "bundle_version")
    if platform not in PLATFORMS:
        raise ComposedBundleError("platform is unsupported")
    _semver(runtime_api_version, "runtime_api_version")
    if type(registry) is not StaticRuntimeAdapterRegistry:
        raise TypeError("registry must be a StaticRuntimeAdapterRegistry")
    if renderpack_path is None and assetpack_path is None:
        raise ComposedBundleError("at least one renderpack or assetpack is required")
    licenses = _license_targets(license_sources)

    destination_input = Path(destination)
    destination_absolute = (
        destination_input if destination_input.is_absolute() else Path.cwd() / destination_input
    )
    if (
        not is_portable_path_component(destination_absolute.name)
        or destination_absolute.name.startswith(".")
        or len(destination_absolute.name.encode("utf-8")) > 160
    ):
        raise ComposedBundleError("bundle destination name is not portable")
    destination_absolute.parent.mkdir(parents=True, exist_ok=True)
    lexical_issues = validate_lexical_directory_root(destination_absolute.parent)
    if lexical_issues:
        raise ComposedBundleError(
            f"bundle destination parent is unsafe: {', '.join(lexical_issues)}"
        )

    with _destination_lock(destination_absolute):
        _recover_journal(
            destination_absolute,
            platform=platform,
            runtime_api_version=runtime_api_version,
            registry=registry,
        )
        try:
            destination_path = assert_new_repository_target(
                destination_absolute,
                repository_type="composed bundle",
            )
        except RepositoryBoundaryError as exc:
            raise ComposedBundleError(str(exc)) from exc

        operation_id = uuid.uuid4().hex
        stage = destination_path.parent / (f".{destination_path.name}.composed-{operation_id}")
        stage.mkdir(mode=0o700)
        stage_identity = directory_identity(stage, context="composed bundle stage")
        journal_path = _journal_path(destination_path)
        journal_identity: tuple[int, int] | None = None
        ready_hash: str | None = None
        publication_started = False
        capture_owner: ResourceSnapshotOwner | None = None
        verified_stage: LoadedComposedRuntimeBundle[T] | None = None
        published_bundle: LoadedComposedRuntimeBundle[T] | None = None
        try:
            copying_journal = _journal_document(
                operation_id=operation_id,
                state="copying",
                stage=stage,
                destination=destination_path,
                stage_identity=stage_identity,
                platform=platform,
                runtime_api_version=runtime_api_version,
                bundle_hash=None,
            )
            journal_identity = _write_journal(
                journal_path,
                copying_journal,
                create=True,
            )
            capture_owner = ResourceSnapshotOwner()
            media_types: dict[str, str] = {}
            document_sources = (
                (capability_catalog_path, CATALOG_PATH),
                (presentation_profile_path, PROFILE_PATH),
                (runtime_adapter_path, ADAPTER_PATH),
                (composition_path, COMPOSITION_PATH),
                (worldpack_path, WORLDPACK_PATH),
            )
            for source, target in document_sources:
                _capture_source(
                    capture_owner,
                    stage,
                    source,
                    target,
                    limit=(
                        MAX_RUNTIME_JSON_BYTES if target == WORLDPACK_PATH else MAX_MANIFEST_BYTES
                    ),
                )
                media_types[target] = "application/json"
            if renderpack_path is not None:
                _capture_source(
                    capture_owner,
                    stage,
                    renderpack_path,
                    RENDERPACK_PATH,
                    limit=MAX_MANIFEST_BYTES,
                )
                media_types[RENDERPACK_PATH] = "application/json"
            if assetpack_path is not None:
                _capture_source(
                    capture_owner,
                    stage,
                    assetpack_path,
                    ASSETPACK_PATH,
                    limit=MAX_MANIFEST_BYTES,
                )
                media_types[ASSETPACK_PATH] = "application/json"

            catalog = validate_runtime_capability_catalog(
                _decode(
                    _read_bounded(
                        stage / CATALOG_PATH,
                        limit=MAX_MANIFEST_BYTES,
                        context="staged capability catalog",
                    ),
                    source=CATALOG_PATH,
                )
            )
            profile = validate_runtime_presentation_profile(
                _decode(
                    _read_bounded(
                        stage / PROFILE_PATH,
                        limit=MAX_MANIFEST_BYTES,
                        context="staged presentation profile",
                    ),
                    source=PROFILE_PATH,
                )
            )
            adapter = validate_runtime_adapter(
                _decode(
                    _read_bounded(
                        stage / ADAPTER_PATH,
                        limit=MAX_MANIFEST_BYTES,
                        context="staged runtime adapter",
                    ),
                    source=ADAPTER_PATH,
                )
            )
            composition = validate_runtime_composition(
                _decode(
                    _read_bounded(
                        stage / COMPOSITION_PATH,
                        limit=MAX_MANIFEST_BYTES,
                        context="staged runtime composition",
                    ),
                    source=COMPOSITION_PATH,
                )
            )
            worldpack_raw = _decode(
                _read_bounded(
                    stage / WORLDPACK_PATH,
                    limit=MAX_RUNTIME_JSON_BYTES,
                    context="staged worldpack",
                ),
                source=WORLDPACK_PATH,
            )
            composition_packs = composition["packs"]
            if composition_packs["worldpack"]["path"] != WORLDPACK_PATH:
                raise ComposedBundleError(
                    "composition must reference the fixed bundled worldpack path"
                )
            if ("renderpack" in composition_packs) != (renderpack_path is not None):
                raise ComposedBundleError(
                    "composition renderpack selection does not match builder inputs"
                )
            if ("assetpack" in composition_packs) != (assetpack_path is not None):
                raise ComposedBundleError(
                    "composition assetpack selection does not match builder inputs"
                )

            renderpack_document: dict[str, Any] | None = None
            if renderpack_path is not None:
                if composition_packs["renderpack"]["path"] != RENDERPACK_PATH:
                    raise ComposedBundleError(
                        "composition must reference the fixed bundled renderpack path"
                    )
                renderpack_document = _decode(
                    _read_bounded(
                        stage / RENDERPACK_PATH,
                        limit=MAX_MANIFEST_BYTES,
                        context="staged renderpack",
                    ),
                    source=RENDERPACK_PATH,
                )
                _capture_pack_resources(
                    owner=capture_owner,
                    stage=stage,
                    source_manifest=Path(renderpack_path).absolute(),
                    document=renderpack_document,
                    kind="renderpack",
                    media_types=media_types,
                )
            assetpack_document: dict[str, Any] | None = None
            if assetpack_path is not None:
                if composition_packs["assetpack"]["path"] != ASSETPACK_PATH:
                    raise ComposedBundleError(
                        "composition must reference the fixed bundled assetpack path"
                    )
                assetpack_document = _decode(
                    _read_bounded(
                        stage / ASSETPACK_PATH,
                        limit=MAX_MANIFEST_BYTES,
                        context="staged assetpack",
                    ),
                    source=ASSETPACK_PATH,
                )
                _capture_pack_resources(
                    owner=capture_owner,
                    stage=stage,
                    source_manifest=Path(assetpack_path).absolute(),
                    document=assetpack_document,
                    kind="assetpack",
                    media_types=media_types,
                )
            for target, source, media_type in licenses:
                _capture_source(
                    capture_owner,
                    stage,
                    source,
                    target,
                    limit=MAX_LICENSE_BYTES,
                )
                media_types[target] = media_type
            capture_owner.close()
            capture_owner = None

            try:
                registered = load_registered_runtime_composition(
                    stage,
                    capability_catalog_path=CATALOG_PATH,
                    presentation_profile_path=PROFILE_PATH,
                    runtime_adapter_path=ADAPTER_PATH,
                    composition_path=COMPOSITION_PATH,
                    platform=platform,
                    runtime_api_version=runtime_api_version,
                    registry=registry,
                )
            except RuntimeCompositionError as exc:
                raise ComposedBundleError(
                    f"staged runtime composition is not compatible: {exc}"
                ) from exc
            report = registered.verification.report
            if not registered.verification.compatible:
                raise ComposedBundleError("staged runtime composition is incompatible")
            _write_new_stage_file(stage, REPORT_PATH, canonical_json_bytes(report))
            media_types[REPORT_PATH] = "application/json"

            try:
                worldpack = load_worldpack(stage / WORLDPACK_PATH)
            except (OSError, WorldPackError) as exc:
                raise ComposedBundleError(f"staged worldpack is invalid: {exc}") from exc
            if worldpack_raw.get("format") != "isoworld.worldpack":
                raise ComposedBundleError("staged worldpack format is invalid")

            files = [
                _file_record(stage / path, path, media_types[path]) for path in sorted(media_types)
            ]
            license_records = [record for record in files if record["path"].startswith("licenses/")]
            manifest: dict[str, Any] = {
                "format": COMPOSED_BUNDLE_FORMAT,
                "format_version": COMPOSED_BUNDLE_FORMAT_VERSION,
                "bundle_id": bundle_id,
                "bundle_version": bundle_version,
                "compatibility_target": {
                    "platform": platform,
                    "runtime_api_version": runtime_api_version,
                },
                "contracts": {
                    "runtime_composition": {
                        "path": COMPOSITION_PATH,
                        "format": composition["format"],
                        "format_version": composition["format_version"],
                        "world_id": composition["world_id"],
                        "release_id": composition["release_id"],
                        "content_hash": composition["content_hash"],
                    },
                    "presentation_profile": {
                        "path": PROFILE_PATH,
                        "format": profile["format"],
                        "format_version": profile["format_version"],
                        "id": profile["id"],
                        "content_hash": profile["content_hash"],
                    },
                    "capability_catalog": {
                        "path": CATALOG_PATH,
                        "format": catalog["format"],
                        "format_version": catalog["format_version"],
                        "content_hash": catalog["content_hash"],
                    },
                    "runtime_adapter": {
                        "path": ADAPTER_PATH,
                        "format": adapter["format"],
                        "format_version": adapter["format_version"],
                        "id": adapter["id"],
                        "version": adapter["version"],
                        "content_hash": adapter["content_hash"],
                    },
                },
                "compatibility_evidence": {
                    "path": REPORT_PATH,
                    "format": report["format"],
                    "format_version": report["format_version"],
                    "composition_hash": report["composition_hash"],
                    "content_hash": report["content_hash"],
                },
                "packs": {
                    "worldpack": {
                        "path": WORLDPACK_PATH,
                        "format": worldpack_raw["format"],
                        "format_version": worldpack.format_version,
                        "world_id": worldpack.world_id,
                        "content_hash": worldpack.content_hash,
                    },
                    "renderpack": (
                        None
                        if renderpack_document is None
                        else {
                            "path": RENDERPACK_PATH,
                            "format": renderpack_document["format"],
                            "format_version": renderpack_document["format_version"],
                            "world_content_hash": renderpack_document["world_content_hash"],
                            "content_hash": renderpack_document["content_hash"],
                        }
                    ),
                    "assetpack": (
                        None
                        if assetpack_document is None
                        else {
                            "path": ASSETPACK_PATH,
                            "format": assetpack_document["format"],
                            "format_version": assetpack_document["format_version"],
                            "world_content_hash": assetpack_document["world_content_hash"],
                            "content_hash": assetpack_document["content_hash"],
                        }
                    ),
                },
                "files": files,
                "licenses": license_records,
            }
            manifest["bundle_hash"] = _canonical_hash(manifest)
            validate_composed_runtime_bundle_manifest(manifest)
            _write_new_stage_file(
                stage,
                COMPOSED_BUNDLE_MANIFEST,
                canonical_json_bytes(manifest),
            )
            _fsync_tree_directories(stage)
            ready_hash = manifest["bundle_hash"]

            verified_stage = verify_composed_runtime_bundle(
                stage,
                expected_bundle_hash=ready_hash,
                platform=platform,
                runtime_api_version=runtime_api_version,
                registry=registry,
            )
            verified_stage.close()
            verified_stage = None
            ready_journal = _journal_document(
                operation_id=operation_id,
                state="ready",
                stage=stage,
                destination=destination_path,
                stage_identity=stage_identity,
                platform=platform,
                runtime_api_version=runtime_api_version,
                bundle_hash=ready_hash,
            )
            journal_identity = _write_journal(
                journal_path,
                ready_journal,
                create=False,
            )
            publication_started = True
            try:
                published_identity = publish_directory_noreplace(stage, destination_path)
            except FileExistsError as exc:
                raise ComposedBundleError(
                    f"bundle destination already exists: {destination_path}"
                ) from exc
            except DirectoryPublishError as exc:
                raise ComposedBundleError(str(exc)) from exc
            if published_identity != stage_identity:
                raise ComposedBundleError("published bundle identity changed")
            _fsync_directory(destination_path.parent)
            published_bundle = load_composed_runtime_bundle(
                destination_path,
                expected_bundle_hash=ready_hash,
                platform=platform,
                runtime_api_version=runtime_api_version,
                registry=registry,
            )
            if (
                _optional_directory_identity(
                    destination_path,
                    context="verified published destination",
                )
                != stage_identity
                or _optional_directory_identity(
                    stage,
                    context="published absent stage",
                )
                is not None
            ):
                raise ComposedBundleError(
                    "published bundle changed before recovery journal cleanup"
                )
            assert journal_identity is not None
            _remove_owned_journal(journal_path, journal_identity)
            result = published_bundle
            published_bundle = None
            return result
        except BaseException as original:
            cleanup_errors: list[str] = []
            if capture_owner is not None:
                try:
                    capture_owner.close()
                except ResourceSnapshotError as exc:
                    cleanup_errors.append(f"source snapshot cleanup: {exc}")
            if verified_stage is not None:
                try:
                    verified_stage.close()
                except ComposedBundleError as exc:
                    cleanup_errors.append(f"verified stage cleanup: {exc}")
            if published_bundle is not None:
                try:
                    published_bundle.close()
                except ComposedBundleError as exc:
                    cleanup_errors.append(f"published snapshot cleanup: {exc}")
            cleanup_owned_stage = True
            if publication_started:
                if ready_hash is None:
                    cleanup_owned_stage = False
                else:
                    cleanup_owned_stage = (
                        _publication_outcome(
                            stage,
                            destination_path,
                            expected_identity=stage_identity,
                            expected_bundle_hash=ready_hash,
                            platform=platform,
                            runtime_api_version=runtime_api_version,
                            registry=registry,
                        )
                        is _PublicationOutcome.STAGE_OWNED
                    )
            if cleanup_owned_stage:
                try:
                    stage_removed = _cleanup_stage(
                        stage,
                        stage_identity,
                        ready_hash=ready_hash,
                        platform=platform,
                        runtime_api_version=runtime_api_version,
                        registry=registry,
                    )
                except (ComposedBundleError, OSError) as exc:
                    cleanup_errors.append(f"stage cleanup: {exc}")
                else:
                    if stage_removed and journal_identity is not None:
                        try:
                            if (
                                _optional_directory_identity(
                                    stage,
                                    context="removed cleanup stage",
                                )
                                is not None
                                or _optional_directory_identity(
                                    destination_path,
                                    context="destination after stage cleanup",
                                )
                                is not None
                            ):
                                raise ComposedBundleError(
                                    "publication paths changed after cleanup; preserving journal"
                                )
                            _remove_owned_journal(journal_path, journal_identity)
                        except (ComposedBundleError, OSError) as exc:
                            cleanup_errors.append(f"journal cleanup: {exc}")
            if cleanup_errors:
                note_cleanup_failure(
                    original,
                    ComposedBundleError("; ".join(cleanup_errors)),
                    context="composed bundle cleanup",
                )
            if isinstance(original, ComposedBundleError):
                raise
            if isinstance(
                original,
                (
                    AssetPackError,
                    DirectoryPublishError,
                    OSError,
                    RenderPackError,
                    ResourceSnapshotError,
                    RuntimeCompositionError,
                ),
            ):
                raise ComposedBundleError(str(original)) from original
            raise


__all__ = [
    "COMPOSED_BUNDLE_FORMAT",
    "COMPOSED_BUNDLE_FORMAT_VERSION",
    "COMPOSED_BUNDLE_MANIFEST",
    "ComposedBundleError",
    "LoadedComposedRuntimeBundle",
    "build_composed_runtime_bundle",
    "load_composed_runtime_bundle",
    "validate_composed_runtime_bundle_manifest",
    "verify_composed_runtime_bundle",
    "verify_installed_composed_runtime_bundle",
]
