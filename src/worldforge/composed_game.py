"""Import immutable composed bundles into standalone generated games."""

from __future__ import annotations

import os
import re
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
from isoworld.runtime_io import RuntimeIOError, decode_json_object, read_json_object
from worldforge.bundle import (
    MAX_CATALOG_BYTES,
    WORLD_CATALOG,
    BundleError,
    _assert_game_path_component,
    _audit_catalog_storage,
    _load_verified_catalog,
    _read_json,
    _validate_catalog_document,
    _verify_shared_assets,
)
from worldforge.composed_bundle import (
    COMPOSED_BUNDLE_MANIFEST,
    ComposedBundleError,
    LoadedComposedRuntimeBundle,
    validate_composed_runtime_bundle_manifest,
    verify_composed_runtime_bundle,
    verify_installed_composed_runtime_bundle,
)
from worldforge.directory_publish import (
    DirectoryClaim,
    DirectoryPublishError,
    append_append_only_journal,
    claim_directory_noreplace,
    create_append_only_journal,
    directory_identity,
    fsync_directory,
    publish_directory_noreplace,
    read_append_only_journal,
)
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


CATALOG_PUBLICATION_JOURNAL = Path("game_data/.composed-catalog-publication.json")
CATALOG_PUBLICATION_JOURNAL_FORMAT = "isoworld.composed_catalog_publication"
CATALOG_PUBLICATION_JOURNAL_VERSION = 1
COMPOSED_IMPORT_JOURNAL_VERSION = 2
_MAX_CATALOG_JOURNAL_RECORD_BYTES = 16 * 1024 * 1024
_MAX_CATALOG_JOURNAL_FILE_BYTES = _MAX_CATALOG_JOURNAL_RECORD_BYTES * 16
_CATALOG_JOURNAL_FIELDS = {
    "format",
    "format_version",
    "operation_id",
    "state",
    "generation_hash",
    "directory_identity",
    "document",
}


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


def _directory_chain(boundary: Path, leaf: Path) -> tuple[Path, ...]:
    try:
        relative = leaf.relative_to(boundary)
    except ValueError as exc:
        raise ComposedGameError("composed import directory escaped game_data") from exc
    chain = [boundary]
    current = boundary
    for part in relative.parts:
        current /= part
        chain.append(current)
    return tuple(chain)


def _require_real_directory(path: Path, *, context: str) -> None:
    try:
        info = path_file_stat(path)
    except OSError as exc:
        raise ComposedGameError(f"could not inspect {context}: {exc}") from exc
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise ComposedGameError(f"{context} is not a real directory")


def _optional_directory_identity(
    path: Path,
    *,
    context: str,
) -> tuple[int, int] | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ComposedGameError(f"could not inspect {context}: {exc}") from exc
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise ComposedGameError(f"{context} is not a real directory")
    return file_identity(info)


def _first_missing_import_path(
    root: Path,
    destination: Path,
) -> tuple[Path, tuple[tuple[Path, tuple[int, int]], ...]]:
    game_data = root / "game_data"
    chain: list[tuple[Path, tuple[int, int]]] = []
    game_data_info = path_file_stat(game_data)
    if _is_link_or_reparse(game_data_info) or not stat.S_ISDIR(game_data_info.st_mode):
        raise ComposedGameError("game data root is not a real directory")
    chain.append((game_data, file_identity(game_data_info)))
    for path in _directory_chain(game_data, destination)[1:]:
        try:
            info = path_file_stat(path)
        except FileNotFoundError:
            return path, tuple(chain)
        except OSError as exc:
            raise ComposedGameError(f"could not inspect composed import path: {exc}") from exc
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ComposedGameError("composed import path is not a real directory")
        chain.append((path, file_identity(info)))
    raise ComposedGameError("derived composed release destination already exists")


def _require_import_chain(
    chain: tuple[tuple[Path, tuple[int, int]], ...],
) -> None:
    for path, expected_identity in chain:
        try:
            info = path_file_stat(path)
        except OSError as exc:
            raise ComposedGameError(f"composed import ancestor changed: {path}: {exc}") from exc
        if (
            _is_link_or_reparse(info)
            or not stat.S_ISDIR(info.st_mode)
            or file_identity(info) != expected_identity
        ):
            raise ComposedGameError("composed import ancestor identity changed")


def _import_stage_paths(
    publication_root: Path,
    destination: Path,
    operation_id: str,
) -> tuple[Path, Path]:
    stage_root = publication_root.parent / (f".{publication_root.name}.import-{operation_id}")
    relative = destination.relative_to(publication_root)
    staged_bundle = stage_root.joinpath(*relative.parts)
    return stage_root, staged_bundle


def _existing_import_stages(
    root: Path,
    destination: Path,
) -> tuple[tuple[str, Path, Path, Path], ...]:
    game_data = root / "game_data"
    stages: list[tuple[str, Path, Path, Path]] = []
    for publication_root in _directory_chain(game_data, destination)[1:]:
        parent = publication_root.parent
        if not parent.exists() and not parent.is_symlink():
            continue
        _require_real_directory(parent, context="composed import stage scan parent")
        prefix = f".{publication_root.name}.import-"
        expected_shape = re.compile(rf"^{re.escape(prefix)}(?P<operation_id>[0-9a-f]{{32}})$")
        for candidate in parent.iterdir():
            if not candidate.name.startswith(prefix):
                continue
            matched = expected_shape.fullmatch(candidate.name)
            if matched is None:
                raise ComposedGameError(
                    "composed import staging directory name is not an exact "
                    f"{prefix}<32 lowercase hex> shape"
                )
            _require_real_directory(
                candidate,
                context="composed import staging directory",
            )
            operation_id = matched.group("operation_id")
            relative = destination.relative_to(publication_root)
            stages.append(
                (
                    operation_id,
                    publication_root,
                    candidate,
                    candidate.joinpath(*relative.parts),
                )
            )
    if len(stages) > 1:
        raise ComposedGameError("composed import has multiple staging directories")
    return tuple(stages)


def _authorized_import_parent(
    root: Path,
    publication_root: Path,
    entries: list[dict[str, Any]],
) -> None:
    game_data = root / "game_data"
    authorized = {game_data}
    for item in entries:
        relative = PurePosixPath(str(item["path"]))
        installed = root.joinpath(*relative.parts)
        authorized.update(_directory_chain(game_data, installed))
    for path in _directory_chain(game_data, publication_root.parent):
        _require_real_directory(path, context="recoverable composed import ancestor")
        if path not in authorized:
            raise ComposedGameError("recoverable composed import stage has an unauthorised parent")


def _copy_owned_import_stage(
    bundle: LoadedComposedRuntimeBundle[object],
    stage_root: Path,
    staged_bundle: Path,
) -> None:
    if staged_bundle == stage_root:
        _copy_owned_bundle(bundle, stage_root, create_root=False)
        return
    current = stage_root
    for part in staged_bundle.relative_to(stage_root).parts[:-1]:
        current /= part
        current.mkdir(mode=0o700)
    _copy_owned_bundle(bundle, staged_bundle)
    for current_path, _directories, _files in os.walk(stage_root, topdown=False):
        fsync_directory(
            Path(current_path),
            context="composed import stage tree",
        )
    fsync_directory(
        stage_root.parent,
        context="composed import stage parent",
    )


def _require_windows_stage_parent(
    parent: Path,
    expected_identity: tuple[int, int],
    *,
    context: str,
) -> None:
    try:
        current = directory_identity(parent, context=f"{context} parent")
    except (DirectoryPublishError, OSError) as exc:
        raise ComposedGameError(f"{context} parent identity changed") from exc
    if current != expected_identity:
        raise ComposedGameError(f"{context} parent identity changed")


@contextmanager
def _pin_windows_stage_parent(
    stage: Path,
    *,
    expected_parent_identity: tuple[int, int],
    context: str,
) -> Iterator[None]:
    parent = stage.parent
    _require_windows_stage_parent(
        parent,
        expected_parent_identity,
        context=context,
    )
    try:
        parent_handle = resource_snapshot_module._windows_lock_directory(  # noqa: SLF001
            parent
        )
    except resource_snapshot_module.ResourceSnapshotError as exc:
        raise ComposedGameError(f"could not pin {context} parent: {exc}") from exc
    try:
        _require_windows_stage_parent(
            parent,
            expected_parent_identity,
            context=context,
        )
        yield
        _require_windows_stage_parent(
            parent,
            expected_parent_identity,
            context=context,
        )
    finally:
        active_error = sys.exception()
        try:
            resource_snapshot_module._windows_close_handle(  # noqa: SLF001
                parent_handle
            )
        except resource_snapshot_module.ResourceSnapshotError as exc:
            if not resource_snapshot_module.note_cleanup_failure(
                active_error,
                exc,
                context=f"{context} parent handle cleanup",
            ):
                raise ComposedGameError(f"could not close {context} parent handle: {exc}") from exc


@contextmanager
def _private_import_stage(
    stage_root: Path,
    *,
    expected_parent_identity: tuple[int, int],
) -> Iterator[tuple[tuple[int, int], DirectoryClaim | None]]:
    platform = _generation_platform()
    if platform == "posix":
        try:
            with claim_directory_noreplace(
                stage_root,
                expected_parent_identity=expected_parent_identity,
            ) as claim:
                yield claim.identity, claim
        except DirectoryPublishError as exc:
            raise ComposedGameError(str(exc)) from exc
        return
    if platform == "windows":
        with _pin_windows_stage_parent(
            stage_root,
            expected_parent_identity=expected_parent_identity,
            context="composed import stage",
        ):
            _create_generation_stage(stage_root)
            identity = directory_identity(stage_root, context="composed import stage root")
            yield identity, None
        return
    _create_generation_stage(stage_root)
    identity = directory_identity(stage_root, context="composed import stage root")
    yield identity, None


def _verify_import_stage_envelope(stage_root: Path, staged_bundle: Path) -> tuple[int, int]:
    root_identity = directory_identity(stage_root, context="composed import stage root")
    current = stage_root
    for part in staged_bundle.relative_to(stage_root).parts:
        try:
            children = tuple(current.iterdir())
        except OSError as exc:
            raise ComposedGameError(
                f"could not inspect composed import stage envelope: {exc}"
            ) from exc
        if len(children) != 1 or children[0].name != part:
            raise ComposedGameError("composed import stage envelope is not exact")
        current = current / part
        _require_real_directory(current, context="composed import stage envelope")
    if current != staged_bundle:
        raise ComposedGameError("composed import stage envelope is inconsistent")
    if directory_identity(stage_root, context="composed import stage root") != root_identity:
        raise ComposedGameError("composed import stage root identity changed")
    return root_identity


def _composition_recovery_ancestors(
    root: Path,
    destination_parent: Path,
) -> tuple[Path, ...]:
    game_data = root / "game_data"
    chain = _directory_chain(game_data, destination_parent)
    for path in chain:
        _require_real_directory(path, context="recoverable composed import ancestor")
    return chain[:-1]


def _fsync_modified_ancestors(
    paths: tuple[Path, ...],
    *,
    context: str,
) -> None:
    unique = sorted(set(paths), key=lambda path: (len(path.parts), str(path)), reverse=True)
    for path in unique:
        fsync_directory(path, context=context)


def _entry_and_paths(
    bundle: LoadedComposedRuntimeBundle[object],
    root: Path,
) -> tuple[dict[str, Any], Path]:
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
    return entry, destination


def _copy_owned_bundle(
    bundle: LoadedComposedRuntimeBundle[object],
    stage: Path,
    *,
    create_root: bool = True,
) -> None:
    manifest = bundle.manifest
    if create_root:
        stage.mkdir(mode=0o700)
    else:
        _require_real_directory(stage, context="composed import stage")
        try:
            if any(stage.iterdir()):
                raise ComposedGameError("composed import stage is not empty")
        except OSError as exc:
            raise ComposedGameError(f"could not inspect composed import stage: {exc}") from exc
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
    _fsync_composed_bundle_tree(stage)


def _open_claimed_subdirectory(
    parent_fd: int,
    name: str,
    *,
    expected_identity: tuple[int, int] | None,
) -> tuple[int, tuple[int, int]]:
    try:
        if expected_identity is None:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise ComposedGameError(
            f"could not open private composed import directory {name!r}: {exc}"
        ) from exc
    try:
        info = descriptor_file_stat(descriptor)
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise ComposedGameError(f"private composed import entry is not a directory: {name!r}")
        identity = file_identity(info)
        if expected_identity is not None and identity != expected_identity:
            raise ComposedGameError(f"private composed import directory identity changed: {name!r}")
        return descriptor, identity
    except BaseException:
        _close_descriptor(
            descriptor,
            context="private composed import directory cleanup",
        )
        raise


def _claimed_parent_descriptor(
    root_fd: int,
    parts: tuple[str, ...],
    identities: dict[tuple[str, ...], tuple[int, int]],
) -> int:
    try:
        current = os.dup(root_fd)
    except OSError as exc:
        raise ComposedGameError(f"could not retain private composed import root: {exc}") from exc
    try:
        prefix: tuple[str, ...] = ()
        for part in parts:
            prefix = (*prefix, part)
            child, identity = _open_claimed_subdirectory(
                current,
                part,
                expected_identity=identities.get(prefix),
            )
            identities.setdefault(prefix, identity)
            _close_descriptor(
                current,
                context="private composed import ancestor cleanup",
            )
            current = child
        return current
    except BaseException:
        _close_descriptor(
            current,
            context="private composed import ancestor cleanup",
        )
        raise


def _copy_claimed_file(
    parent_fd: int,
    name: str,
    *,
    source: Path | None = None,
    payload: bytes | None = None,
    expected_size: int,
) -> None:
    if (source is None) == (payload is None):
        raise ComposedGameError("private composed import source is ambiguous")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o644,
            dir_fd=parent_fd,
        )
        if source is not None:
            with source.open("rb") as input_file:
                while chunk := input_file.read(1024 * 1024):
                    view = memoryview(chunk)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("short private composed import write")
                        view = view[written:]
        else:
            assert payload is not None
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short private composed import write")
                view = view[written:]
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
        info = descriptor_file_stat(descriptor)
        if (
            _is_link_or_reparse(info)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size != expected_size
        ):
            raise ComposedGameError("private composed import file changed while writing")
        os.fsync(parent_fd)
    except ComposedGameError:
        raise
    except OSError as exc:
        raise ComposedGameError(
            f"could not write private composed import file {name!r}: {exc}"
        ) from exc
    finally:
        if descriptor is not None:
            _close_descriptor(
                descriptor,
                context="private composed import file cleanup",
            )


def _copy_owned_bundle_into_claim(
    bundle: LoadedComposedRuntimeBundle[object],
    claim: DirectoryClaim,
    relative: PurePosixPath,
) -> None:
    identities = {(): claim.identity}
    envelope_parts = tuple(relative.parts)
    envelope_fd = _claimed_parent_descriptor(
        claim.fd,
        envelope_parts,
        identities,
    )
    try:
        for record in bundle.manifest["files"]:
            source_relative = PurePosixPath(str(record["path"]))
            source = bundle._owner.resolve_file(source_relative)  # noqa: SLF001
            parent_fd = _claimed_parent_descriptor(
                claim.fd,
                (*envelope_parts, *source_relative.parts[:-1]),
                identities,
            )
            try:
                _copy_claimed_file(
                    parent_fd,
                    source_relative.name,
                    source=source,
                    expected_size=int(record["size"]),
                )
            finally:
                _close_descriptor(
                    parent_fd,
                    context="private composed import payload parent cleanup",
                )
        _copy_claimed_file(
            envelope_fd,
            COMPOSED_BUNDLE_MANIFEST,
            payload=bundle._manifest_bytes,  # noqa: SLF001
            expected_size=len(bundle._manifest_bytes),  # noqa: SLF001
        )
        os.fsync(envelope_fd)
        claim.fsync()
    finally:
        _close_descriptor(
            envelope_fd,
            context="private composed import bundle directory cleanup",
        )


def _fsync_composed_bundle_tree(root: Path) -> None:
    for current, _directories, _files in os.walk(root, topdown=False):
        fsync_directory(
            Path(current),
            context="composed bundle directory",
        )
    fsync_directory(
        root.parent,
        context="composed bundle publication parent",
    )


def _catalog_state(root: Path) -> ComposedCatalogState:
    try:
        return load_composed_catalog_state(root, allow_incomplete=True)
    except ComposedCatalogError as exc:
        raise ComposedGameError(str(exc)) from exc


def _legacy_releases_for_recovery(root: Path) -> list[dict[str, Any]]:
    _verify_shared_assets(root)
    path = root / WORLD_CATALOG
    _assert_game_path_component(path, directory=False)
    if not path.exists():
        releases: list[dict[str, Any]] = []
    else:
        document = _read_json(
            path,
            limit=MAX_CATALOG_BYTES,
            context="world catalog",
        )
        releases = _validate_catalog_document(document)
        if path.read_bytes() != canonical_json_bytes(document):
            raise BundleError("World catalog is not canonically serialized")
    _audit_catalog_storage(root, releases)
    return releases


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


def _catalog_journal_document(
    *,
    operation_id: str,
    state: str,
    generation_hash: str,
    directory_identity_value: tuple[int, int] | None,
    document: dict[str, Any],
    format_version: int = CATALOG_PUBLICATION_JOURNAL_VERSION,
) -> dict[str, Any]:
    return {
        "format": CATALOG_PUBLICATION_JOURNAL_FORMAT,
        "format_version": format_version,
        "operation_id": operation_id,
        "state": state,
        "generation_hash": generation_hash,
        "directory_identity": (
            None
            if directory_identity_value is None
            else {
                "device": directory_identity_value[0],
                "inode": directory_identity_value[1],
            }
        ),
        "document": document,
    }


def _validate_catalog_journal(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _CATALOG_JOURNAL_FIELDS:
        raise ComposedGameError("composed catalog publication journal is not closed")
    journal = value
    if (
        journal["format"] != CATALOG_PUBLICATION_JOURNAL_FORMAT
        or type(journal["format_version"]) is not int
        or journal["format_version"]
        not in {
            CATALOG_PUBLICATION_JOURNAL_VERSION,
            COMPOSED_IMPORT_JOURNAL_VERSION,
        }
    ):
        raise ComposedGameError("composed catalog publication journal format is invalid")
    operation_id = journal["operation_id"]
    if type(operation_id) is not str or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        raise ComposedGameError("composed catalog publication operation_id is invalid")
    if journal["state"] not in {"intent", "copying", "ready", "committed"}:
        raise ComposedGameError("composed catalog publication state is invalid")
    generation_hash = journal["generation_hash"]
    if type(generation_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", generation_hash) is None:
        raise ComposedGameError("composed catalog publication hash is invalid")
    document = journal["document"]
    if not isinstance(document, dict):
        raise ComposedGameError("composed catalog publication document is invalid")
    if (
        document.get("format") != CATALOG_GENERATION_FORMAT
        or type(document.get("format_version")) is not int
        or document.get("format_version") != 1
        or document.get("content_hash") != generation_hash
        or canonical_payload_hash(document) != generation_hash
    ):
        raise ComposedGameError("composed catalog publication document identity is invalid")
    identity = journal["directory_identity"]
    if journal["state"] == "intent":
        if identity is not None:
            raise ComposedGameError("catalog publication intent must not claim an identity")
    elif journal["state"] in {"copying", "ready"} or identity is not None:
        if (
            not isinstance(identity, dict)
            or set(identity) != {"device", "inode"}
            or type(identity.get("device")) is not int
            or type(identity.get("inode")) is not int
            or identity["device"] < 0
            or identity["inode"] < 0
        ):
            raise ComposedGameError("composed catalog publication identity is invalid")
    return journal


def _catalog_journal_identity(journal: dict[str, Any]) -> tuple[int, int]:
    identity = journal["directory_identity"]
    if not isinstance(identity, dict):
        raise ComposedGameError("composed catalog publication identity is unavailable")
    return int(identity["device"]), int(identity["inode"])


def _read_catalog_journal_record(
    root: Path,
) -> tuple[dict[str, Any], tuple[int, int], bytes] | None:
    path = root / CATALOG_PUBLICATION_JOURNAL
    try:
        loaded = read_append_only_journal(
            path,
            max_record_bytes=_MAX_CATALOG_JOURNAL_RECORD_BYTES,
            max_file_bytes=_MAX_CATALOG_JOURNAL_FILE_BYTES,
        )
    except DirectoryPublishError as exc:
        raise ComposedGameError(str(exc)) from exc
    if loaded is None:
        return None
    payload, identity = loaded
    try:
        value = decode_json_object(payload, source=path)
    except RuntimeIOError as exc:
        raise ComposedGameError(f"could not read composed catalog journal: {exc}") from exc
    journal = _validate_catalog_journal(value)
    if payload != canonical_json_bytes(journal):
        raise ComposedGameError("composed catalog publication journal record is not canonical")
    return journal, identity, payload


def _read_catalog_journal(
    root: Path,
) -> tuple[dict[str, Any], tuple[int, int], bytes] | None:
    loaded = _read_catalog_journal_record(root)
    if loaded is None or loaded[0]["state"] == "committed":
        return None
    return loaded


def _audit_game_repository_with_publication_journals(
    root: Path,
    *,
    allow_resumable_composed_import_intent: bool = False,
) -> list[Any]:
    from worldforge.bundle import IMPORT_JOURNAL, _read_import_journal_record

    bundle_journal = _read_import_journal_record(root / IMPORT_JOURNAL)
    if bundle_journal is not None:
        if bundle_journal[0]["state"] != "committed":
            raise ComposedGameError("active legacy bundle journal blocks composed bundle import")
    catalog_journal = _read_catalog_journal_record(root)
    try:
        findings = audit_game_repository(root)
    except GameBoundaryError as exc:
        raise ComposedGameError(str(exc)) from exc
    return [
        finding
        for finding in findings
        if not (
            catalog_journal is not None
            and finding.path == CATALOG_PUBLICATION_JOURNAL
            and (
                finding.rule == "partial_publication_journal"
                or (
                    allow_resumable_composed_import_intent
                    and catalog_journal[0]["format_version"] == COMPOSED_IMPORT_JOURNAL_VERSION
                    and catalog_journal[0]["state"] == "intent"
                    and finding.rule == "active_publication_journal"
                )
            )
        )
    ]


def _write_catalog_journal(
    root: Path,
    journal: dict[str, Any],
    *,
    create: bool,
    expected: tuple[dict[str, Any], tuple[int, int], bytes] | None = None,
) -> tuple[dict[str, Any], tuple[int, int], bytes]:
    path = root / CATALOG_PUBLICATION_JOURNAL
    payload = canonical_json_bytes(_validate_catalog_journal(journal))
    if create:
        try:
            identity = create_append_only_journal(
                path,
                payload,
                max_record_bytes=_MAX_CATALOG_JOURNAL_RECORD_BYTES,
            )
        except FileExistsError as exc:
            existing = _read_catalog_journal_record(root)
            if existing is None or existing[0]["state"] != "committed":
                raise ComposedGameError(
                    "composed catalog publication journal already exists"
                ) from exc
            try:
                identity = append_append_only_journal(
                    path,
                    expected_identity=existing[1],
                    expected_payload=existing[2],
                    updated_payload=payload,
                    max_record_bytes=_MAX_CATALOG_JOURNAL_RECORD_BYTES,
                    max_file_bytes=_MAX_CATALOG_JOURNAL_FILE_BYTES,
                )
            except DirectoryPublishError as append_error:
                raise ComposedGameError(str(append_error)) from append_error
        except DirectoryPublishError as exc:
            raise ComposedGameError(str(exc)) from exc
        fsync_directory(path.parent, context="composed catalog journal parent")
        return journal, identity, payload

    if expected is None:
        raise ComposedGameError("catalog journal transition requires exact prior state")
    expected_document, expected_identity, expected_payload = expected
    if expected_payload != canonical_json_bytes(expected_document):
        raise ComposedGameError("catalog journal transition prior bytes are inconsistent")
    loaded = _read_catalog_journal_record(root)
    if (
        loaded is None
        or loaded[0] != expected_document
        or loaded[1] != expected_identity
        or loaded[2] != expected_payload
    ):
        raise ComposedGameError("composed catalog journal changed before transition")
    try:
        identity = append_append_only_journal(
            path,
            expected_identity=expected_identity,
            expected_payload=expected_payload,
            updated_payload=payload,
            max_record_bytes=_MAX_CATALOG_JOURNAL_RECORD_BYTES,
            max_file_bytes=_MAX_CATALOG_JOURNAL_FILE_BYTES,
        )
    except DirectoryPublishError as exc:
        raise ComposedGameError(str(exc)) from exc
    return journal, identity, payload


def _remove_catalog_journal(
    root: Path,
    expected: tuple[dict[str, Any], tuple[int, int], bytes],
) -> None:
    journal, identity, payload = expected
    loaded = _read_catalog_journal_record(root)
    if loaded is None or loaded != (journal, identity, payload):
        raise ComposedGameError("composed catalog journal changed before cleanup")
    if journal["state"] == "committed":
        return
    _write_catalog_journal(
        root,
        {**journal, "state": "committed"},
        create=False,
        expected=expected,
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
            | getattr(os, "O_BINARY", 0)
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


@contextmanager
def _private_catalog_stage(
    stage: Path,
    *,
    expected_parent_identity: tuple[int, int],
) -> Iterator[tuple[tuple[int, int], int | None, DirectoryClaim | None]]:
    if _generation_platform() == "posix":
        try:
            with claim_directory_noreplace(
                stage,
                expected_parent_identity=expected_parent_identity,
            ) as claim:
                yield claim.identity, claim.fd, claim
        except DirectoryPublishError as exc:
            raise ComposedGameError(str(exc)) from exc
        return
    if _generation_platform() == "windows":
        with _pin_windows_stage_parent(
            stage,
            expected_parent_identity=expected_parent_identity,
            context="catalog generation stage",
        ):
            _create_generation_stage(stage)
            identity = directory_identity(stage, context="catalog generation stage")
            with _pin_generation_directory(
                stage,
                expected_identity=identity,
            ) as (_identity, descriptor):
                yield identity, descriptor, None
        return
    _create_generation_stage(stage)


def _fsync_catalog_state(
    root: Path,
    state: ComposedCatalogState,
) -> ComposedCatalogState:
    if not state.entries:
        return state
    generations_root = root / CATALOG_GENERATIONS_RELATIVE_PATH
    generation = generations_root / state.head_hash
    fsync_directory(
        generation,
        context="composed catalog generation",
    )
    fsync_directory(
        generations_root,
        context="composed catalog generation root",
    )
    if len(state.entries) == 1:
        fsync_directory(
            generations_root.parent,
            context="composed catalog parent",
        )
    current = _catalog_state(root)
    if current != state:
        raise ComposedGameError("composed catalog changed during durable metadata flush")
    return current


def _recover_catalog_publication(root: Path) -> None:
    loaded = _read_catalog_journal(root)
    if loaded is None:
        return
    journal, _journal_identity_value, _journal_payload = loaded
    if journal["format_version"] != CATALOG_PUBLICATION_JOURNAL_VERSION:
        raise ComposedGameError("active composed import journal requires bundle-bound recovery")
    generations_root = root / CATALOG_GENERATIONS_RELATIVE_PATH
    generation = generations_root / str(journal["generation_hash"])
    stage = generations_root / (
        f"{CATALOG_GENERATION_STAGE_PREFIX}{journal['generation_hash']}-{journal['operation_id']}"
    )
    stage_identity = _optional_directory_identity(
        stage,
        context="recoverable catalog stage",
    )
    generation_identity = _optional_directory_identity(
        generation,
        context="recoverable catalog generation",
    )
    if journal["state"] == "intent":
        if stage_identity is not None or generation_identity is not None:
            raise ComposedGameError(
                "catalog publication intent has ambiguous filesystem evidence; "
                "preserving journal and filesystem"
            )
        _remove_catalog_journal(root, loaded)
        return
    expected_identity = _catalog_journal_identity(journal)
    payload = canonical_json_bytes(journal["document"])
    if journal["state"] == "copying":
        recovery_path: Path | None = None
        if stage_identity == expected_identity and generation_identity is None:
            recovery_path = stage
        elif generation_identity == expected_identity and stage_identity is None:
            recovery_path = generation
        if recovery_path is None:
            raise ComposedGameError(
                "copying catalog journal has ambiguous or changed evidence; "
                "preserving journal and filesystem"
            )
        _verify_generation_directory(
            recovery_path,
            payload,
            expected_identity=expected_identity,
        )
        ready_document = _catalog_journal_document(
            operation_id=str(journal["operation_id"]),
            state="ready",
            generation_hash=str(journal["generation_hash"]),
            directory_identity_value=expected_identity,
            document=journal["document"],
        )
        loaded = _write_catalog_journal(
            root,
            ready_document,
            create=False,
            expected=loaded,
        )
        journal = ready_document
        stage_identity = _optional_directory_identity(
            stage,
            context="ready recoverable catalog stage",
        )
        generation_identity = _optional_directory_identity(
            generation,
            context="ready recoverable catalog generation",
        )
    if stage_identity == expected_identity and generation_identity is None:
        _verify_generation_directory(
            stage,
            payload,
            expected_identity=expected_identity,
        )
        try:
            with publish_directory_noreplace(
                stage,
                generation,
                expected_source_identity=expected_identity,
            ) as published_identity:
                if published_identity != expected_identity:
                    raise ComposedGameError("recovered catalog generation identity changed")
                _verify_generation_directory(
                    generation,
                    payload,
                    expected_identity=expected_identity,
                )
        except (DirectoryPublishError, FileExistsError) as exc:
            raise ComposedGameError(
                f"could not recover composed catalog publication: {exc}"
            ) from exc
    elif generation_identity == expected_identity and stage_identity is None:
        pass
    else:
        raise ComposedGameError(
            "ready catalog journal has ambiguous or changed evidence; "
            "preserving journal and filesystem"
        )
    _verify_generation_directory(
        generation,
        payload,
        expected_identity=expected_identity,
    )
    fsync_directory(generation, context="recoverable composed catalog generation")
    fsync_directory(generations_root, context="recoverable composed catalog root")
    fsync_directory(generations_root.parent, context="recoverable composed catalog parent")
    _verify_generation_directory(
        generation,
        payload,
        expected_identity=expected_identity,
    )
    if (
        _optional_directory_identity(
            stage,
            context="absent recoverable catalog stage",
        )
        is not None
    ):
        raise ComposedGameError("recovered catalog stage reappeared; preserving journal")
    _remove_catalog_journal(root, loaded)


def _import_journal_target(
    root: Path,
    journal: dict[str, Any],
    bundle: LoadedComposedRuntimeBundle[object],
) -> tuple[ComposedCatalogState, list[dict[str, Any]], dict[str, Any], Path]:
    state = _catalog_state(root)
    entries = [_release_entry(release) for release in state.entries]
    entry, destination = _entry_and_paths(bundle, root)
    expected = _catalog_generation_document(state, [*entries, entry])
    if journal["document"] != expected or journal["generation_hash"] != expected["content_hash"]:
        raise ComposedGameError(
            "composed import journal is not bound to the current catalog and bundle"
        )
    return state, entries, entry, destination


def _import_publication_evidence(
    root: Path,
    destination: Path,
    operation_id: str,
    expected_identity: tuple[int, int],
) -> tuple[
    tuple[Path, Path, Path] | None,
    Path | None,
]:
    game_data = root / "game_data"
    staged: list[tuple[Path, Path, Path]] = []
    published: list[Path] = []
    existing_stages = _existing_import_stages(root, destination)
    if existing_stages and existing_stages[0][0] != operation_id:
        raise ComposedGameError(
            "composed import staging directory operation_id disagrees with its journal; "
            "preserving evidence"
        )
    for publication_root in _directory_chain(game_data, destination)[1:]:
        stage_root, staged_bundle = _import_stage_paths(
            publication_root,
            destination,
            operation_id,
        )
        stage_identity = _optional_directory_identity(
            stage_root,
            context="recoverable composed import stage",
        )
        if stage_identity is not None:
            if stage_identity != expected_identity:
                raise ComposedGameError(
                    "composed import stage identity changed; preserving evidence"
                )
            staged.append((publication_root, stage_root, staged_bundle))
        publication_identity = _optional_directory_identity(
            publication_root,
            context="recoverable composed import publication root",
        )
        if publication_identity == expected_identity:
            published.append(publication_root)
    if len(staged) > 1 or len(published) > 1 or (staged and published):
        raise ComposedGameError(
            "composed import has ambiguous stage/destination evidence; preserving it"
        )
    return (staged[0] if staged else None), (published[0] if published else None)


def _recover_composed_import_publication(
    root: Path,
    bundle: LoadedComposedRuntimeBundle[object],
    loaded: tuple[dict[str, Any], tuple[int, int], bytes],
) -> Path | None:
    journal = loaded[0]
    if journal["format_version"] != COMPOSED_IMPORT_JOURNAL_VERSION:
        return None
    try:
        legacy_releases = _legacy_releases_for_recovery(root)
    except (BundleError, OSError) as exc:
        raise ComposedGameError(f"legacy catalog blocks composed import recovery: {exc}") from exc
    state, entries, entry, destination = _import_journal_target(root, journal, bundle)
    try:
        validate_cross_catalog_world_hashes(
            tuple(legacy_releases),
            state.entries,
        )
    except ComposedCatalogError as exc:
        raise ComposedGameError(str(exc)) from exc
    operation_id = str(journal["operation_id"])
    if journal["state"] == "intent":
        candidate_stages = _existing_import_stages(root, destination)
        if candidate_stages and candidate_stages[0][0] != operation_id:
            raise ComposedGameError(
                "composed import staging directory operation_id disagrees with its "
                "intent journal; preserving evidence"
            )
        if (
            bool(candidate_stages)
            or _optional_directory_identity(
                destination,
                context="intent composed import destination",
            )
            is not None
        ):
            raise ComposedGameError(
                "composed import intent has ambiguous filesystem evidence; preserving it"
            )
        return None

    expected_identity = _catalog_journal_identity(journal)
    staged, published_root = _import_publication_evidence(
        root,
        destination,
        operation_id,
        expected_identity,
    )
    candidate_identity: tuple[int, int]
    if journal["state"] == "copying":
        if staged is not None:
            _publication_root, _stage_root, staged_bundle = staged
            try:
                candidate_identity = _verify_import_candidate(staged_bundle, bundle, entry)
            except (ComposedBundleError, DirectoryPublishError, OSError) as exc:
                raise ComposedGameError(
                    "copying composed import stage is incomplete; preserving journal"
                ) from exc
        elif published_root is not None:
            candidate_identity = _verify_import_candidate(destination, bundle, entry)
        else:
            raise ComposedGameError(
                "copying composed import lost its exact journalled evidence; preserving journal"
            )
        ready = _catalog_journal_document(
            operation_id=operation_id,
            state="ready",
            generation_hash=str(journal["generation_hash"]),
            directory_identity_value=expected_identity,
            document=journal["document"],
            format_version=COMPOSED_IMPORT_JOURNAL_VERSION,
        )
        loaded = _write_catalog_journal(
            root,
            ready,
            create=False,
            expected=loaded,
        )
        journal = ready
    elif staged is not None:
        candidate_identity = _verify_import_candidate(staged[2], bundle, entry)
    elif published_root is not None:
        candidate_identity = _verify_import_candidate(destination, bundle, entry)
    else:
        raise ComposedGameError(
            "ready composed import lost its exact journalled evidence; preserving journal"
        )

    if staged is not None:
        publication_root, stage_root, staged_bundle = staged
        _authorized_import_parent(root, publication_root, entries)
        if (
            _optional_directory_identity(
                publication_root,
                context="composed import publication root",
            )
            is not None
        ):
            raise ComposedGameError(
                "composed import publication root is occupied; preserving stage"
            )
        _fsync_composed_bundle_tree(staged_bundle)
        if _verify_import_candidate(staged_bundle, bundle, entry) != candidate_identity:
            raise ComposedGameError("composed import stage changed before recovery publication")
        try:
            with publish_directory_noreplace(
                stage_root,
                publication_root,
                expected_source_identity=expected_identity,
            ) as published_identity:
                if published_identity != expected_identity:
                    raise ComposedGameError("recovered composed import root identity changed")
                if _verify_import_candidate(destination, bundle, entry) != candidate_identity:
                    raise ComposedGameError("recovered composed bundle identity changed")
        except (DirectoryPublishError, FileExistsError) as exc:
            raise ComposedGameError(
                f"could not recover composed import publication: {exc}"
            ) from exc
    if _verify_import_candidate(destination, bundle, entry) != candidate_identity:
        raise ComposedGameError("recovered composed bundle identity changed")
    _fsync_composed_bundle_tree(destination)
    _fsync_modified_ancestors(
        _composition_recovery_ancestors(root, destination.parent),
        context="recovered composed import ancestor",
    )
    if _verify_import_candidate(destination, bundle, entry) != candidate_identity:
        raise ComposedGameError("recovered composed bundle changed during durable flush")
    _remove_catalog_journal(root, loaded)
    return destination


def _recover_publication(
    root: Path,
    bundle: LoadedComposedRuntimeBundle[object],
) -> Path | None:
    loaded = _read_catalog_journal(root)
    if loaded is None:
        return None
    if loaded[0]["format_version"] == CATALOG_PUBLICATION_JOURNAL_VERSION:
        _recover_catalog_publication(root)
        return None
    return _recover_composed_import_publication(root, bundle, loaded)


def _catalog_generation_document(
    state: ComposedCatalogState,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
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
    return document


def _publish_catalog_generation(
    root: Path,
    state: ComposedCatalogState,
    entries: list[dict[str, Any]],
) -> ComposedCatalogState:
    _recover_catalog_publication(root)
    document = _catalog_generation_document(state, entries)
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
            return _fsync_catalog_state(root, current)
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
    if _generation_platform() == "unsupported":
        raise ComposedGameError("secure composed catalog publication supports Linux and Windows")
    operation_id = uuid.uuid4().hex
    stage = generations_root / (
        f"{CATALOG_GENERATION_STAGE_PREFIX}{generation_hash}-{operation_id}"
    )
    journal_state = _write_catalog_journal(
        root,
        _catalog_journal_document(
            operation_id=operation_id,
            state="intent",
            generation_hash=generation_hash,
            directory_identity_value=None,
            document=document,
        ),
        create=True,
    )
    try:
        with _private_catalog_stage(
            stage,
            expected_parent_identity=generations_identity,
        ) as (stage_identity, directory_descriptor, claim):
            copying_journal = _catalog_journal_document(
                operation_id=operation_id,
                state="copying",
                generation_hash=generation_hash,
                directory_identity_value=stage_identity,
                document=document,
            )
            journal_state = _write_catalog_journal(
                root,
                copying_journal,
                create=False,
                expected=journal_state,
            )
            _write_generation_payload(
                stage,
                payload,
                directory_descriptor=directory_descriptor,
            )
            _verify_generation_directory(
                stage,
                payload,
                expected_identity=stage_identity,
            )
            if claim is not None:
                claim.fsync()
                claim.require_binding()
            else:
                fsync_directory(stage, context="catalog generation stage")
            fsync_directory(generations_root, context="catalog generation root")
            _verify_generation_directory(
                stage,
                payload,
                expected_identity=stage_identity,
            )
            ready_journal = _catalog_journal_document(
                operation_id=operation_id,
                state="ready",
                generation_hash=generation_hash,
                directory_identity_value=stage_identity,
                document=document,
            )
            journal_state = _write_catalog_journal(
                root,
                ready_journal,
                create=False,
                expected=journal_state,
            )
        with publish_directory_noreplace(
            stage,
            generation,
            expected_source_identity=stage_identity,
        ) as published_identity:
            if published_identity != stage_identity:
                raise ComposedGameError("published composed catalog generation identity changed")
            _verify_generation_directory(
                generation,
                payload,
                expected_identity=published_identity,
            )
    except FileExistsError as exc:
        raise ComposedGameError(
            "private stage or immutable catalog generation is already occupied"
        ) from exc
    except DirectoryPublishError as exc:
        raise ComposedGameError(str(exc)) from exc
    if (
        directory_identity(generations_root, context="catalog generation root")
        != generations_identity
    ):
        raise ComposedGameError("composed catalog generation root identity changed")
    fsync_directory(
        generation,
        context="composed catalog generation",
    )
    fsync_directory(
        generations_root,
        context="composed catalog generation root",
    )
    if not state.entries:
        fsync_directory(
            generations_root.parent,
            context="composed catalog parent",
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
    _remove_catalog_journal(root, journal_state)
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
            _recover_publication(root, bundle)
            recovered = _recover_import(root, bundle)
            if recovered is not None:
                return recovered
            active_catalog_journal = _read_catalog_journal(root)
            resumable_intent = bool(
                active_catalog_journal is not None
                and active_catalog_journal[0]["format_version"] == COMPOSED_IMPORT_JOURNAL_VERSION
                and active_catalog_journal[0]["state"] == "intent"
            )
            findings = _audit_game_repository_with_publication_journals(
                root,
                allow_resumable_composed_import_intent=resumable_intent,
            )
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
    entry, destination = _entry_and_paths(bundle, root)
    _require_one_world_hash([*existing_entries, entry])
    if any(item == entry for item in existing_entries):
        raise ComposedGameError("the exact composed release is already imported")
    if any(item.get("bundle_hash") == bundle.bundle_hash for item in existing_entries):
        raise ComposedGameError("the immutable composed bundle hash is already catalogued")
    journal_state = _read_catalog_journal(root)
    try:
        if (
            journal_state is not None
            and journal_state[0]["format_version"] == COMPOSED_IMPORT_JOURNAL_VERSION
            and journal_state[0]["state"] == "intent"
        ):
            legacy = _legacy_releases_for_recovery(root)
        else:
            _legacy_document, legacy = _load_verified_catalog(root)
    except BundleError as exc:
        raise ComposedGameError(str(exc)) from exc
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
    publication_root, existing_chain = _first_missing_import_path(root, destination)
    target_document = _catalog_generation_document(state, [*existing_entries, entry])
    if journal_state is None:
        operation_id = uuid.uuid4().hex
        journal_state = _write_catalog_journal(
            root,
            _catalog_journal_document(
                operation_id=operation_id,
                state="intent",
                generation_hash=str(target_document["content_hash"]),
                directory_identity_value=None,
                document=target_document,
                format_version=COMPOSED_IMPORT_JOURNAL_VERSION,
            ),
            create=True,
        )
    else:
        if (
            journal_state[0]["format_version"] != COMPOSED_IMPORT_JOURNAL_VERSION
            or journal_state[0]["state"] != "intent"
        ):
            raise ComposedGameError("active composed catalog publication cannot resume this import")
        _import_journal_target(root, journal_state[0], bundle)
        operation_id = str(journal_state[0]["operation_id"])
    stage_root, staged_bundle = _import_stage_paths(
        publication_root,
        destination,
        operation_id,
    )
    try:
        with _private_import_stage(
            stage_root,
            expected_parent_identity=existing_chain[-1][1],
        ) as (stage_identity, claim):
            copying = _catalog_journal_document(
                operation_id=operation_id,
                state="copying",
                generation_hash=str(target_document["content_hash"]),
                directory_identity_value=stage_identity,
                document=target_document,
                format_version=COMPOSED_IMPORT_JOURNAL_VERSION,
            )
            journal_state = _write_catalog_journal(
                root,
                copying,
                create=False,
                expected=journal_state,
            )
            _require_import_chain(existing_chain)
            if claim is None:
                _copy_owned_import_stage(bundle, stage_root, staged_bundle)
            else:
                _copy_owned_bundle_into_claim(
                    bundle,
                    claim,
                    PurePosixPath(destination.relative_to(publication_root).as_posix()),
                )
                claim.require_binding()
            if _verify_import_stage_envelope(stage_root, staged_bundle) != stage_identity:
                raise ComposedGameError("composed import stage root identity changed")
            candidate_identity = _verify_import_candidate(staged_bundle, bundle, entry)
            _fsync_composed_bundle_tree(staged_bundle)
            _require_import_chain(existing_chain)
            if claim is not None:
                claim.require_binding()
            if _verify_import_candidate(staged_bundle, bundle, entry) != candidate_identity:
                raise ComposedGameError("staged composed bundle changed before publication")
            ready = _catalog_journal_document(
                operation_id=operation_id,
                state="ready",
                generation_hash=str(target_document["content_hash"]),
                directory_identity_value=stage_identity,
                document=target_document,
                format_version=COMPOSED_IMPORT_JOURNAL_VERSION,
            )
            journal_state = _write_catalog_journal(
                root,
                ready,
                create=False,
                expected=journal_state,
            )
            with publish_directory_noreplace(
                stage_root,
                publication_root,
                expected_source_identity=stage_identity,
            ) as published_identity:
                if published_identity != stage_identity:
                    raise ComposedGameError("published composed directory identity changed")
                if (
                    directory_identity(
                        destination,
                        context="published composed import",
                    )
                    != candidate_identity
                ):
                    raise ComposedGameError("published composed bundle identity changed")
                _verify_import_candidate(destination, bundle, entry)
    except FileExistsError as exc:
        raise ComposedGameError(
            "derived composed release staging or publication path already exists"
        ) from exc
    except DirectoryPublishError as exc:
        raise ComposedGameError(str(exc)) from exc
    _fsync_composed_bundle_tree(destination)
    _fsync_modified_ancestors(
        _composition_recovery_ancestors(root, destination.parent),
        context="composed import ancestor",
    )
    _verify_import_candidate(destination, bundle, entry)
    _remove_catalog_journal(root, journal_state)
    _publish_catalog_generation(root, state, [*existing_entries, entry])
    _verify_game_postconditions(root, bundle.bundle_hash)
    return destination


def _verify_game_postconditions(root: Path, bundle_hash: str) -> None:
    try:
        _legacy_document, legacy = _load_verified_catalog(root)
    except BundleError as exc:
        raise ComposedGameError(f"imported composed catalog is invalid: {exc}") from exc
    findings = _audit_game_repository_with_publication_journals(root)
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
    entry, destination = _entry_and_paths(bundle, root)
    _require_one_world_hash([*entries, entry])
    exact_matches = [item for item in entries if item == entry]
    hash_matches = [item for item in entries if item.get("bundle_hash") == bundle.bundle_hash]
    stages = _existing_import_stages(root, destination)

    if exact_matches:
        if hash_matches != exact_matches:
            raise ComposedGameError("recovered composed catalog identity is inconsistent")
        if stages:
            raise ComposedGameError("committed composed import retains a staging directory")
        identity = _verify_import_candidate(destination, bundle, entry)
        _fsync_composed_bundle_tree(destination)
        _fsync_modified_ancestors(
            _composition_recovery_ancestors(root, destination.parent),
            context="composed import ancestor",
        )
        if _verify_import_candidate(destination, bundle, entry) != identity:
            raise ComposedGameError("recovered composed directory identity changed")
        _fsync_catalog_state(root, state)
        _verify_game_postconditions(root, bundle.bundle_hash)
        return destination
    if hash_matches:
        raise ComposedGameError("recovered composed catalog identity is inconsistent")

    destination_exists = destination.exists() or destination.is_symlink()
    if destination_exists and stages:
        raise ComposedGameError("composed import has both staging and destination state")
    if not destination_exists and not stages:
        return None

    if stages:
        raise ComposedGameError(
            "unjournalled composed import stage requires explicit recovery; preserving it"
        )
    committed = _read_catalog_journal_record(root)
    if (
        committed is None
        or committed[0]["state"] != "committed"
        or committed[0]["format_version"] != COMPOSED_IMPORT_JOURNAL_VERSION
    ):
        raise ComposedGameError(
            "uncatalogued composed destination lacks committed publication evidence"
        )
    _import_journal_target(root, committed[0], bundle)
    expected_root_identity = _catalog_journal_identity(committed[0])
    staged, published_root = _import_publication_evidence(
        root,
        destination,
        str(committed[0]["operation_id"]),
        expected_root_identity,
    )
    if staged is not None or published_root is None:
        raise ComposedGameError(
            "committed composed destination evidence is ambiguous; preserving it"
        )
    candidate_identity = _verify_import_candidate(destination, bundle, entry)
    _fsync_composed_bundle_tree(destination)
    _fsync_modified_ancestors(
        _composition_recovery_ancestors(root, destination.parent),
        context="composed import ancestor",
    )
    if _verify_import_candidate(destination, bundle, entry) != candidate_identity:
        raise ComposedGameError("recovered composed directory identity changed")
    _publish_catalog_generation(root, state, [*entries, entry])
    _verify_game_postconditions(root, bundle.bundle_hash)
    return destination


__all__ = [
    "BUILTIN_COMPOSED_ADAPTERS",
    "ComposedGameError",
    "import_composed_bundle",
]
