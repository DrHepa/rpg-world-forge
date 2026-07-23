"""Import immutable composed bundles into standalone generated games."""

from __future__ import annotations

import os
import shutil
import stat
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

import isoworld.content.resource_snapshot as resource_snapshot_module
from isoworld.content.composed_catalog import (
    CATALOG_GENERATION_FORMAT,
    CATALOG_GENERATION_NAME,
    CATALOG_GENERATION_STAGE_PREFIX,
    CATALOG_GENERATIONS_RELATIVE_PATH,
    ComposedCatalogError,
    ComposedCatalogRelease,
    ComposedCatalogState,
    load_composed_catalog,
    load_composed_catalog_state,
    validate_cross_catalog_world_hashes,
    verify_composed_release,
)
from isoworld.content.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    path_file_stat,
)
from isoworld.content.models import RUNTIME_API_VERSION
from isoworld.render.pyray_2_5d import PYRAY_2_5D_ADAPTER, PYRAY_2_5D_KEY
from isoworld.render.pyray_3d import PYRAY_3D_V1_ADAPTER, PYRAY_3D_V1_KEY
from isoworld.runtime_adapter import StaticRuntimeAdapterRegistry
from isoworld.runtime_io import RuntimeIOError, read_json_object
from worldforge.bundle import BundleError, _load_verified_catalog
from worldforge.composed_bundle import (
    COMPOSED_BUNDLE_MANIFEST,
    ComposedBundleError,
    LoadedComposedRuntimeBundle,
    validate_composed_runtime_bundle_manifest,
    verify_composed_runtime_bundle,
    verify_installed_composed_runtime_bundle,
)
from worldforge.directory_publish import directory_identity, publish_directory_noreplace
from worldforge.game_boundary import GameBoundaryError, audit_game_repository
from worldforge.game_lock import GameMutationLockError, exclusive_game_mutation
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.repository_boundary import (
    RepositoryBoundaryError,
    require_standalone_game_root,
)

BUILTIN_COMPOSED_ADAPTERS = StaticRuntimeAdapterRegistry(
    (
        (PYRAY_2_5D_KEY, PYRAY_2_5D_ADAPTER),
        (PYRAY_3D_V1_KEY, PYRAY_3D_V1_ADAPTER),
    )
)


class ComposedGameError(ValueError):
    """Raised when a composed release cannot be imported safely."""


def _close_descriptor(descriptor: int, *, context: str) -> None:
    primary = sys.exception()
    try:
        os.close(descriptor)
    except OSError as cleanup_error:
        if not resource_snapshot_module.note_cleanup_failure(
            primary,
            cleanup_error,
            context=context,
        ):
            raise ComposedGameError(f"{context} failed: {cleanup_error}") from cleanup_error


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _same_file_state(left: FileStat, right: FileStat) -> bool:
    return (
        file_identity(left) == file_identity(right)
        and left.st_mode == right.st_mode
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
        and getattr(left, "st_file_attributes", 0) == getattr(right, "st_file_attributes", 0)
    )


def _catalog_entry(bundle: LoadedComposedRuntimeBundle[object], path: str) -> dict[str, Any]:
    documents = bundle.registered.documents
    composition = documents.composition
    profile = documents.presentation_profile
    adapter = documents.runtime_adapter
    manifest = bundle.manifest
    return {
        "world_id": composition["world_id"],
        "world_content_hash": composition["world_content_hash"],
        "release_id": composition["release_id"],
        "profile_id": profile["id"],
        "profile_hash": profile["content_hash"],
        "adapter_id": adapter["id"],
        "adapter_version": adapter["version"],
        "adapter_hash": adapter["content_hash"],
        "composition_hash": composition["content_hash"],
        "bundle_id": manifest["bundle_id"],
        "bundle_version": manifest["bundle_version"],
        "bundle_hash": manifest["bundle_hash"],
        "path": path,
    }


def _target_path(root: Path, entry: dict[str, Any]) -> Path:
    return root.joinpath(
        "game_data",
        "compositions",
        entry["world_id"],
        entry["release_id"],
        entry["profile_id"],
        entry["adapter_id"],
        entry["adapter_version"],
        entry["bundle_id"],
        entry["bundle_version"],
    )


def _entry_and_paths(
    bundle: LoadedComposedRuntimeBundle[object],
    root: Path,
) -> tuple[dict[str, Any], Path, Path]:
    documents = bundle.registered.documents
    composition = documents.composition
    profile = documents.presentation_profile
    adapter = documents.runtime_adapter
    relative = (
        "game_data/compositions/"
        f"{composition['world_id']}/{composition['release_id']}/{profile['id']}/"
        f"{adapter['id']}/{adapter['version']}/{bundle.bundle_id}/{bundle.bundle_version}"
    )
    entry = _catalog_entry(bundle, relative)
    destination = _target_path(root, entry)
    stage = destination.parent / f".{destination.name}.import-{bundle.bundle_hash}"
    return entry, destination, stage


def _copy_owned_bundle(
    bundle: LoadedComposedRuntimeBundle[object],
    stage: Path,
) -> None:
    manifest = bundle.manifest
    stage.mkdir(mode=0o700)
    for record in manifest["files"]:
        relative = PurePosixPath(str(record["path"]))
        source = bundle._owner.resolve_file(relative)  # noqa: SLF001 - same trust boundary
        target = stage.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as input_file, target.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != record["size"]:
            raise ComposedGameError(f"staged composed payload changed: {relative}")
    manifest_target = stage / COMPOSED_BUNDLE_MANIFEST
    with manifest_target.open("xb") as output_file:
        output_file.write(bundle._manifest_bytes)  # noqa: SLF001 - exact owned bytes
        output_file.flush()
        os.fsync(output_file.fileno())
    for current, _directories, _files in os.walk(stage, topdown=False):
        descriptor = os.open(current, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            _close_descriptor(
                descriptor,
                context="staged composed bundle directory cleanup",
            )


def _catalog_state(root: Path) -> ComposedCatalogState:
    try:
        return load_composed_catalog_state(root, allow_incomplete=True)
    except ComposedCatalogError as exc:
        raise ComposedGameError(str(exc)) from exc


def _release_entry(release: ComposedCatalogRelease) -> dict[str, Any]:
    return {
        field: getattr(release, field)
        for field in (
            "world_id",
            "world_content_hash",
            "release_id",
            "profile_id",
            "profile_hash",
            "adapter_id",
            "adapter_version",
            "adapter_hash",
            "composition_hash",
            "bundle_id",
            "bundle_version",
            "bundle_hash",
            "path",
        )
    }


def _require_one_world_hash(entries: list[dict[str, Any]]) -> None:
    known: dict[tuple[str, str], str] = {}
    for entry in entries:
        key = (str(entry["world_id"]), str(entry["release_id"]))
        digest = str(entry["world_content_hash"])
        previous = known.setdefault(key, digest)
        if previous != digest:
            raise ComposedGameError(
                "composed catalog maps one world/release to multiple world content hashes"
            )


def _write_generation_payload(
    stage: Path,
    payload: bytes,
    *,
    directory_descriptor: int | None,
) -> None:
    descriptor: int | None = None
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        if directory_descriptor is not None:
            descriptor = os.open(
                CATALOG_GENERATION_NAME,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        else:
            descriptor = os.open(stage / CATALOG_GENERATION_NAME, flags, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short composed catalog generation write")
            view = view[written:]
        os.fsync(descriptor)
        info = descriptor_file_stat(descriptor)
        if (
            _is_link_or_reparse(info)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size != len(payload)
        ):
            raise ComposedGameError("composed catalog generation file changed while writing")
        if directory_descriptor is not None:
            os.fsync(directory_descriptor)
    finally:
        if descriptor is not None:
            _close_descriptor(
                descriptor,
                context="composed catalog generation payload cleanup",
            )


def _generation_platform() -> str:
    if sys.platform.startswith("linux") and os.name == "posix":
        return "posix"
    if os.name == "nt":
        return "windows"
    return "unsupported"


@contextmanager
def _pin_generation_directory(
    path: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> Iterator[tuple[tuple[int, int], int | None]]:
    identity = directory_identity(path, context="catalog generation directory")
    if expected_identity is not None and identity != expected_identity:
        raise ComposedGameError("composed catalog generation directory identity changed")
    directory_descriptor: int | None = None
    windows_handle: int | None = None
    platform = _generation_platform()
    try:
        if platform == "posix":
            if os.open not in os.supports_dir_fd:
                raise ComposedGameError("secure composed catalog generation I/O is unavailable")
            directory_descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = descriptor_file_stat(directory_descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (
                    opened.st_dev,
                    opened.st_ino,
                )
                != identity
            ):
                raise ComposedGameError("composed catalog generation directory identity changed")
        elif platform == "windows":
            try:
                windows_handle = resource_snapshot_module._windows_lock_directory(  # noqa: SLF001
                    path
                )
            except resource_snapshot_module.ResourceSnapshotError as exc:
                raise ComposedGameError(
                    f"could not pin Windows catalog generation directory: {exc}"
                ) from exc
            if (
                directory_identity(
                    path,
                    context="catalog generation directory",
                )
                != identity
            ):
                raise ComposedGameError("composed catalog generation directory identity changed")
        else:
            raise ComposedGameError(
                "secure composed catalog generation I/O supports Linux and Windows"
            )
        yield identity, directory_descriptor
        if directory_descriptor is not None:
            opened = descriptor_file_stat(directory_descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or (
                    opened.st_dev,
                    opened.st_ino,
                )
                != identity
            ):
                raise ComposedGameError("composed catalog generation directory identity changed")
        if directory_identity(path, context="catalog generation directory") != identity:
            raise ComposedGameError("composed catalog generation directory identity changed")
    finally:
        if directory_descriptor is not None:
            _close_descriptor(
                directory_descriptor,
                context="composed catalog generation directory cleanup",
            )
        if windows_handle is not None:
            active_error = sys.exception()
            try:
                resource_snapshot_module._windows_close_handle(  # noqa: SLF001
                    windows_handle
                )
            except resource_snapshot_module.ResourceSnapshotError as exc:
                if not resource_snapshot_module.note_cleanup_failure(
                    active_error,
                    exc,
                    context="Windows catalog generation handle cleanup",
                ):
                    raise ComposedGameError(
                        f"could not close Windows catalog generation handle: {exc}"
                    ) from exc


def _verify_generation_directory(
    path: Path,
    payload: bytes,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> tuple[int, int]:
    with _pin_generation_directory(
        path,
        expected_identity=expected_identity,
    ) as (identity, directory_descriptor):
        try:
            if directory_descriptor is not None:
                names = tuple(os.listdir(directory_descriptor))
            else:
                names = tuple(child.name for child in path.iterdir())
        except OSError as exc:
            raise ComposedGameError(
                f"could not inspect composed catalog generation: {exc}"
            ) from exc
        if names != (CATALOG_GENERATION_NAME,):
            raise ComposedGameError("composed catalog generation directory is not exact")
        descriptor: int | None = None
        try:
            flags = (
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOINHERIT", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            if directory_descriptor is not None:
                before_path = os.stat(
                    CATALOG_GENERATION_NAME,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                descriptor = os.open(
                    CATALOG_GENERATION_NAME,
                    flags,
                    dir_fd=directory_descriptor,
                )
            else:
                payload_path = path / CATALOG_GENERATION_NAME
                before_path = path_file_stat(payload_path)
                descriptor = os.open(payload_path, flags)
            before = descriptor_file_stat(descriptor)
            if (
                _is_link_or_reparse(before_path)
                or _is_link_or_reparse(before)
                or not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size != len(payload)
                or not _same_file_state(before_path, before)
            ):
                raise ComposedGameError("composed catalog generation payload identity changed")
            chunks: list[bytes] = []
            remaining = len(payload) + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if b"".join(chunks) != payload:
                raise ComposedGameError("composed catalog generation payload hash changed")
            after = descriptor_file_stat(descriptor)
            if directory_descriptor is not None:
                after_path = os.stat(
                    CATALOG_GENERATION_NAME,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            else:
                after_path = path_file_stat(path / CATALOG_GENERATION_NAME)
            if not _same_file_state(before, after) or not _same_file_state(
                before,
                after_path,
            ):
                raise ComposedGameError("composed catalog generation payload identity changed")
        finally:
            if descriptor is not None:
                _close_descriptor(
                    descriptor,
                    context="composed catalog generation verification cleanup",
                )
        return identity


def _create_generation_stage(path: Path) -> None:
    platform = _generation_platform()
    if platform == "posix":
        path.mkdir(mode=0o700)
        return
    if platform == "windows":
        try:
            resource_snapshot_module._windows_create_private_directory(path)  # noqa: SLF001
        except resource_snapshot_module.ResourceSnapshotError as exc:
            raise ComposedGameError(
                f"could not create private Windows catalog generation stage: {exc}"
            ) from exc
        return
    raise ComposedGameError(
        "secure composed catalog generation creation supports Linux and Windows"
    )


def _generation_stage(
    generations_root: Path,
    generation_hash: str,
    payload: bytes,
) -> tuple[Path, tuple[int, int]]:
    prefix = f"{CATALOG_GENERATION_STAGE_PREFIX}{generation_hash}-"
    try:
        stages = tuple(
            path
            for path in generations_root.iterdir()
            if path.name.startswith(CATALOG_GENERATION_STAGE_PREFIX)
        )
    except OSError as exc:
        raise ComposedGameError(f"could not inspect composed catalog stages: {exc}") from exc
    matching = tuple(path for path in stages if path.name.startswith(prefix))
    if len(stages) != len(matching) or len(matching) > 1:
        raise ComposedGameError("composed catalog has conflicting unpublished generations")
    if matching:
        stage = matching[0]
        return stage, _verify_generation_directory(stage, payload)

    stage = generations_root / f"{prefix}{uuid.uuid4().hex}"
    _create_generation_stage(stage)
    identity = directory_identity(stage, context="catalog generation stage")
    with _pin_generation_directory(
        stage,
        expected_identity=identity,
    ) as (_pinned_identity, directory_descriptor):
        _write_generation_payload(
            stage,
            payload,
            directory_descriptor=directory_descriptor,
        )
        verified = _verify_generation_directory(
            stage,
            payload,
            expected_identity=identity,
        )
    return stage, verified


def _publish_catalog_generation(
    root: Path,
    state: ComposedCatalogState,
    entries: list[dict[str, Any]],
) -> ComposedCatalogState:
    _require_one_world_hash(entries)
    entries.sort(
        key=lambda item: (
            item["world_id"],
            item["release_id"],
            item["profile_id"],
            item["adapter_id"],
            item["adapter_version"],
            item["bundle_id"],
            item["bundle_version"],
        )
    )
    document: dict[str, Any] = {
        "format": CATALOG_GENERATION_FORMAT,
        "format_version": 1,
        "previous_hash": state.head_hash,
        "entries": entries,
    }
    document["content_hash"] = canonical_payload_hash(document)
    generation_hash = str(document["content_hash"])
    payload = canonical_json_bytes(document)
    expected_entries = tuple(entries)
    generations_root = root / CATALOG_GENERATIONS_RELATIVE_PATH
    generation = generations_root / generation_hash

    current = _catalog_state(root)
    current_entries = tuple(_release_entry(entry) for entry in current.entries)
    if current.head_hash != state.head_hash or current_entries != tuple(
        _release_entry(entry) for entry in state.entries
    ):
        if current.head_hash == generation_hash and current_entries == expected_entries:
            return current
        raise ComposedGameError("composed catalog head changed before immutable publication")

    try:
        generations_root.mkdir(mode=0o700)
    except FileExistsError:
        root_info = path_file_stat(generations_root)
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise ComposedGameError("composed catalog generation root is unsafe") from None
    generations_identity = directory_identity(
        generations_root,
        context="catalog generation root",
    )
    stage, stage_identity = _generation_stage(
        generations_root,
        generation_hash,
        payload,
    )
    try:
        published_identity = publish_directory_noreplace(stage, generation)
    except FileExistsError:
        current = _catalog_state(root)
        current_entries = tuple(_release_entry(entry) for entry in current.entries)
        if (
            not stage.exists()
            and not stage.is_symlink()
            and current.head_hash == generation_hash
            and current_entries == expected_entries
        ):
            return current
        raise ComposedGameError(
            "immutable composed catalog generation name is already occupied"
        ) from None
    if published_identity != stage_identity:
        raise ComposedGameError("published composed catalog generation identity changed")
    _verify_generation_directory(
        generation,
        payload,
        expected_identity=published_identity,
    )
    if (
        directory_identity(generations_root, context="catalog generation root")
        != generations_identity
    ):
        raise ComposedGameError("composed catalog generation root identity changed")
    if os.name == "posix":
        descriptor = os.open(
            generations_root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            _close_descriptor(
                descriptor,
                context="composed catalog generation root cleanup",
            )
    published = _catalog_state(root)
    published_entries = tuple(_release_entry(entry) for entry in published.entries)
    if published.head_hash != generation_hash or published_entries != expected_entries:
        raise ComposedGameError("immutable composed catalog publication did not become canonical")
    _verify_generation_directory(
        generation,
        payload,
        expected_identity=published_identity,
    )
    return published


def _platform_from_manifest(path: Path) -> str:
    try:
        manifest = validate_composed_runtime_bundle_manifest(
            read_json_object(path / COMPOSED_BUNDLE_MANIFEST)
        )
    except (ComposedBundleError, RuntimeIOError) as exc:
        raise ComposedGameError(str(exc)) from exc
    target = manifest["compatibility_target"]
    return str(target["platform"])


def import_composed_bundle(
    bundle_path: str | Path,
    game_root: str | Path,
    *,
    expected_bundle_hash: str,
) -> Path:
    """Verify, copy, and exclusively publish one composed release."""

    source = Path(bundle_path)
    platform = _platform_from_manifest(source)
    try:
        bundle = verify_composed_runtime_bundle(
            source,
            expected_bundle_hash=expected_bundle_hash,
            platform=platform,
            runtime_api_version=RUNTIME_API_VERSION,
            registry=BUILTIN_COMPOSED_ADAPTERS,
        )
    except (ComposedBundleError, OSError) as exc:
        raise ComposedGameError(str(exc)) from exc
    with bundle:
        return _import_verified(bundle, game_root)


def _import_verified(
    bundle: LoadedComposedRuntimeBundle[object],
    game_root: str | Path,
) -> Path:
    try:
        root = require_standalone_game_root(game_root)
    except (OSError, RepositoryBoundaryError) as exc:
        raise ComposedGameError(str(exc)) from exc
    source_root = bundle._owner.root  # noqa: SLF001 - exact captured source
    if root == source_root or root in source_root.parents or source_root in root.parents:
        raise ComposedGameError("source bundle and game repository must be disjoint")
    try:
        with exclusive_game_mutation(root, "composed-bundle-import"):
            recovered = _recover_import(root, bundle)
            if recovered is not None:
                return recovered
            try:
                findings = audit_game_repository(root)
            except GameBoundaryError as exc:
                raise ComposedGameError(str(exc)) from exc
            if findings:
                raise ComposedGameError(f"refusing boundary-invalid game: {findings[0]}")
            return _publish_verified(bundle, root)
    except GameMutationLockError as exc:
        raise ComposedGameError(str(exc)) from exc


def _publish_verified(
    bundle: LoadedComposedRuntimeBundle[object],
    root: Path,
) -> Path:
    state = _catalog_state(root)
    existing_entries = [_release_entry(release) for release in state.entries]
    entry, destination, stage = _entry_and_paths(bundle, root)
    _require_one_world_hash([*existing_entries, entry])
    if any(item == entry for item in existing_entries):
        raise ComposedGameError("the exact composed release is already imported")
    if any(item.get("bundle_hash") == bundle.bundle_hash for item in existing_entries):
        raise ComposedGameError("the immutable composed bundle hash is already catalogued")
    _legacy_document, legacy = _load_verified_catalog(root)
    for legacy_entry in legacy:
        if (
            legacy_entry["world_id"] == entry["world_id"]
            and legacy_entry["release_id"] == entry["release_id"]
            and legacy_entry["worldpack_hash"] != entry["world_content_hash"]
        ):
            raise ComposedGameError(
                "legacy and composed catalogs disagree on world content identity"
            )
    if destination.exists() or destination.is_symlink():
        raise ComposedGameError("derived composed release destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if stage.exists() or stage.is_symlink():
        raise ComposedGameError("derived composed release staging path already exists")
    _copy_owned_bundle(bundle, stage)
    stage_identity = directory_identity(stage, context="composed import stage")
    published_identity = publish_directory_noreplace(stage, destination)
    if published_identity != stage_identity:
        raise ComposedGameError("published composed directory identity changed")
    _verify_import_candidate(destination, bundle, entry)
    _publish_catalog_generation(root, state, [*existing_entries, entry])
    _verify_game_postconditions(root, bundle.bundle_hash)
    return destination


def _verify_game_postconditions(root: Path, bundle_hash: str) -> None:
    try:
        _legacy_document, legacy = _load_verified_catalog(root)
    except BundleError as exc:
        raise ComposedGameError(f"imported composed catalog is invalid: {exc}") from exc
    try:
        findings = audit_game_repository(root)
    except GameBoundaryError as exc:
        raise ComposedGameError(str(exc)) from exc
    if findings:
        raise ComposedGameError(f"imported game violates its boundary: {findings[0]}")
    releases = load_composed_catalog(root)
    try:
        validate_cross_catalog_world_hashes(tuple(legacy), releases)
    except ComposedCatalogError as exc:
        raise ComposedGameError(str(exc)) from exc
    selected = tuple(item for item in releases if item.bundle_hash == bundle_hash)
    if len(selected) != 1:
        raise ComposedGameError("imported composed release is not uniquely catalogued")
    with verify_composed_release(selected[0], root):
        pass


def _verify_import_candidate(
    path: Path,
    bundle: LoadedComposedRuntimeBundle[object],
    expected_entry: dict[str, Any],
) -> tuple[int, int]:
    identity = directory_identity(path, context="recoverable composed import")
    platform = _platform_from_manifest(path)
    verified = verify_installed_composed_runtime_bundle(
        path,
        expected_directory_identity=identity,
        expected_bundle_hash=bundle.bundle_hash,
        platform=platform,
        runtime_api_version=RUNTIME_API_VERSION,
        registry=BUILTIN_COMPOSED_ADAPTERS,
    )
    with verified:
        if _catalog_entry(verified, expected_entry["path"]) != expected_entry:
            raise ComposedGameError("recoverable composed import identity is inconsistent")
    return identity


def _recover_import(
    root: Path,
    bundle: LoadedComposedRuntimeBundle[object],
) -> Path | None:
    state = _catalog_state(root)
    entries = [_release_entry(release) for release in state.entries]
    entry, destination, stage = _entry_and_paths(bundle, root)
    _require_one_world_hash([*entries, entry])
    exact_matches = [item for item in entries if item == entry]
    hash_matches = [item for item in entries if item.get("bundle_hash") == bundle.bundle_hash]

    if exact_matches:
        if hash_matches != exact_matches:
            raise ComposedGameError("recovered composed catalog identity is inconsistent")
        if stage.exists() or stage.is_symlink():
            raise ComposedGameError("committed composed import retains a staging directory")
        _verify_import_candidate(destination, bundle, entry)
        _verify_game_postconditions(root, bundle.bundle_hash)
        return destination
    if hash_matches:
        raise ComposedGameError("recovered composed catalog identity is inconsistent")

    destination_exists = destination.exists() or destination.is_symlink()
    stage_exists = stage.exists() or stage.is_symlink()
    if destination_exists and stage_exists:
        raise ComposedGameError("composed import has both staging and destination state")
    if not destination_exists and not stage_exists:
        return None

    candidate = destination if destination_exists else stage
    candidate_identity = _verify_import_candidate(candidate, bundle, entry)
    if stage_exists:
        destination.parent.mkdir(parents=True, exist_ok=True)
        published_identity = publish_directory_noreplace(stage, destination)
        if published_identity != candidate_identity:
            raise ComposedGameError("recovered composed directory identity changed")
    _verify_import_candidate(destination, bundle, entry)
    _publish_catalog_generation(root, state, [*entries, entry])
    _verify_game_postconditions(root, bundle.bundle_hash)
    return destination


__all__ = [
    "BUILTIN_COMPOSED_ADAPTERS",
    "ComposedGameError",
    "import_composed_bundle",
]
