from __future__ import annotations

import ctypes
import os
import stat
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from isoworld.content.portability import portable_path_key, portable_relative_path
from worldforge.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
    windows_handle_file_stat,
)

RetainedTreeHook = Callable[[str, str | None], None]
DirectoryExclusion = Callable[[str], bool]


class RetainedTreeError(ValueError):
    """Raised when a tree cannot be captured through stable retained identities."""


class RetainedTreeCapacityError(RetainedTreeError):
    """Raised when a bounded retained census observes one entry over capacity."""

    def __init__(self, maximum_entries: int) -> None:
        self.maximum_entries = maximum_entries
        super().__init__(f"retained directory exceeds {maximum_entries} entries")


@dataclass(frozen=True, slots=True)
class RetainedTreeSnapshot:
    root: Path
    root_identity: tuple[int, int]
    directories: tuple[str, ...]
    files: dict[str, bytes]


@dataclass(frozen=True, slots=True)
class RetainedDirectoryFileCensus:
    """Exact direct-file names retained under one stable directory identity."""

    root: Path
    root_identity: tuple[int, int]
    names: tuple[str, ...]


@dataclass(slots=True)
class _RetainedDirectory:
    relative: str
    name: str
    descriptor: int
    parent_descriptor: int
    identity: tuple[int, int]
    names: tuple[str, ...] = ()


@dataclass(slots=True)
class _RetainedFile:
    relative: str
    name: str
    descriptor: int
    parent_descriptor: int
    state: tuple[int, int, int, int, int, int, int]
    payload: bytes


def _invoke_hook(
    hook: RetainedTreeHook | None,
    event: str,
    relative: str | None = None,
) -> None:
    if hook is not None:
        hook(event, relative)


def _entry_state(info: FileStat) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _topology_state(info: FileStat) -> tuple[int, int, int, int, int, int, int, int]:
    return (*_entry_state(info), int(getattr(info, "st_file_attributes", 0)))


def _validated_component(value: str, *, context: str) -> str:
    if (
        type(value) is not str
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise RetainedTreeError(f"{context} must be one portable path component")
    _portable_relative(value)
    return value


def _validate_named_components(
    container_name: str,
    child_names: Sequence[str],
) -> tuple[str, tuple[str, ...]]:
    container = _validated_component(container_name, context="container name")
    children = tuple(_validated_component(name, context="child name") for name in child_names)
    if not children:
        raise RetainedTreeError("at least one retained child name is required")
    keys: dict[tuple[str, ...], str] = {}
    for name in (container, *children):
        key = portable_path_key(PurePosixPath(name))
        previous = keys.setdefault(key, name)
        if previous != name:
            raise RetainedTreeError(
                f"retained named-child component collision: {previous!r} and {name!r}"
            )
    if len(set(children)) != len(children):
        raise RetainedTreeError("retained child names must be unique")
    return container, children


def _directory_valid(info: FileStat) -> bool:
    return not is_link_or_reparse(info) and stat.S_ISDIR(info.st_mode)


def _file_valid(info: FileStat) -> bool:
    return not is_link_or_reparse(info) and stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def _entry_kind(info: FileStat, relative: str) -> str:
    if is_link_or_reparse(info):
        raise RetainedTreeError(f"tree entry is linked or reparse-backed: {relative}")
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    if stat.S_ISREG(info.st_mode):
        if info.st_nlink != 1:
            raise RetainedTreeError(f"tree file has multiple hard links: {relative}")
        return "file"
    raise RetainedTreeError(f"tree entry is special: {relative}")


def _portable_relative(relative: str) -> str:
    try:
        path = portable_relative_path(relative)
    except UnicodeError as exc:
        raise RetainedTreeError(f"tree path is not portable UTF-8 text: {relative!r}") from exc
    if path is None:
        raise RetainedTreeError(f"tree path is not portable: {relative!r}")
    return path.as_posix()


def _register_portable_path(
    relative: str,
    portable_paths: dict[tuple[str, ...], str],
) -> None:
    portable = _portable_relative(relative)
    key = portable_path_key(PurePosixPath(portable))
    previous = portable_paths.setdefault(key, portable)
    if previous != portable:
        raise RetainedTreeError(f"portable path collision: {previous!r} and {portable!r}")


def _read_descriptor(descriptor: int, relative: str) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise RetainedTreeError(f"could not read retained tree file {relative}: {exc}") from exc


def _read_descriptor_bounded(descriptor: int, relative: str, maximum_bytes: int) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, maximum_bytes + 1 - len(payload)),
            )
            if not chunk:
                return bytes(payload)
            payload.extend(chunk)
        raise RetainedTreeError(f"retained tree file size exceeded its bound: {relative}")
    except RetainedTreeError:
        raise
    except OSError as exc:
        raise RetainedTreeError(f"could not read retained tree file {relative}: {exc}") from exc


def _posix_open_flags(*, directory: bool) -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise RetainedTreeError("safe retained POSIX tree primitives are unavailable")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    if directory:
        flags |= os.O_DIRECTORY
    else:
        flags |= getattr(os, "O_BINARY", 0)
    return flags


def _posix_directory_entries(
    directory: _RetainedDirectory,
    *,
    exclude_directory: DirectoryExclusion | None,
    portable_paths: dict[tuple[str, ...], str] | None,
    maximum_entries: int | None = None,
) -> list[tuple[str, FileStat, str]]:
    try:
        if maximum_entries is None:
            raw_names = os.listdir(directory.descriptor)
        else:
            raw_names = []
            with os.scandir(directory.descriptor) as iterator:
                for entry in iterator:
                    raw_names.append(entry.name)
                    if len(raw_names) > maximum_entries:
                        break
    except OSError as exc:
        raise RetainedTreeError(
            f"could not enumerate retained tree directory {directory.relative or '.'}: {exc}"
        ) from exc
    entries: list[tuple[str, FileStat, str]] = []
    for name in sorted(raw_names, key=os.fsencode):
        if type(name) is not str or not name or name in {".", ".."}:
            raise RetainedTreeError("retained tree directory returned an invalid name")
        relative = f"{directory.relative}/{name}" if directory.relative else name
        if portable_paths is not None:
            _register_portable_path(relative, portable_paths)
        else:
            _portable_relative(relative)
        try:
            info = os.stat(name, dir_fd=directory.descriptor, follow_symlinks=False)
        except OSError as exc:
            raise RetainedTreeError(
                f"tree entry changed during enumeration {relative}: {exc}"
            ) from exc
        kind = _entry_kind(info, relative)
        if kind == "directory" and exclude_directory is not None and exclude_directory(relative):
            continue
        entries.append((name, info, kind))
        if maximum_entries is not None and len(entries) > maximum_entries:
            raise RetainedTreeCapacityError(maximum_entries)
    return entries


def _posix_direct_census(
    descriptor: int,
    *,
    context: str,
) -> tuple[
    tuple[tuple[str, tuple[int, int, int, int, int, int, int, int]], ...],
    dict[str, FileStat],
]:
    try:
        raw_names = os.listdir(descriptor)
    except OSError as exc:
        raise RetainedTreeError(f"could not enumerate retained {context}: {exc}") from exc
    names = sorted(raw_names, key=os.fsencode)
    if len(names) != len(set(names)):
        raise RetainedTreeError(f"retained {context} returned duplicate names")
    states: list[tuple[str, tuple[int, int, int, int, int, int, int, int]]] = []
    infos: dict[str, FileStat] = {}
    for name in names:
        if type(name) is not str or not name or name in {".", ".."}:
            raise RetainedTreeError(f"retained {context} returned an invalid name")
        try:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            raise RetainedTreeError(
                f"retained {context} entry changed during census: {name}: {exc}"
            ) from exc
        states.append((name, _topology_state(info)))
        infos[name] = info
    return tuple(states), infos


def _exact_named_info(
    infos: dict[str, FileStat],
    name: str,
    *,
    context: str,
) -> FileStat | None:
    if any(candidate != name and candidate.casefold() == name.casefold() for candidate in infos):
        raise RetainedTreeError(f"{context} has a portable collision for {name}")
    info = infos.get(name)
    if info is not None:
        return info
    if any(candidate.casefold() == name.casefold() for candidate in infos):
        raise RetainedTreeError(f"{context} has non-canonical path spelling for {name}")
    return None


def _close_descriptors(descriptors: Sequence[int]) -> None:
    primary = sys.exception()
    cleanup_error: OSError | None = None
    for descriptor in reversed(tuple(descriptors)):
        try:
            os.close(descriptor)
        except OSError as exc:
            if primary is not None:
                primary.add_note(f"retained tree descriptor cleanup failed: {exc}")
            elif cleanup_error is None:
                cleanup_error = exc
    if cleanup_error is not None:
        raise RetainedTreeError(
            f"could not close retained tree descriptor: {cleanup_error}"
        ) from cleanup_error


def _validated_census_capacity(maximum_entries: int) -> int:
    if (
        isinstance(maximum_entries, bool)
        or not isinstance(maximum_entries, int)
        or maximum_entries < 1
    ):
        raise ValueError("maximum_entries must be a positive retained census bound")
    return maximum_entries


def _validated_census_authority(
    source: Path,
    authority_root: str | Path | None,
    expected_authority_identity: tuple[int, int] | None,
) -> tuple[int, tuple[int, int]] | None:
    if (authority_root is None) != (expected_authority_identity is None):
        raise ValueError("authority_root and expected_authority_identity must be provided together")
    if authority_root is None or expected_authority_identity is None:
        return None
    authority = Path(os.path.abspath(os.fspath(authority_root)))
    if not source.is_relative_to(authority):
        raise ValueError("retained census root must be contained by its authority root")
    if (
        type(expected_authority_identity) is not tuple
        or len(expected_authority_identity) != 2
        or any(type(value) is not int or value < 0 for value in expected_authority_identity)
    ):
        raise ValueError("expected_authority_identity must be a non-negative identity pair")
    return len(authority.parts) - 1, expected_authority_identity


def _open_posix_directory_ancestry(
    root: Path,
) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    descriptors: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        descriptor = os.open(root.anchor, _posix_open_flags(directory=True))
        descriptors.append(descriptor)
        info = descriptor_file_stat(descriptor)
        if not _directory_valid(info):
            raise RetainedTreeError("retained directory ancestry is unsafe")
        identities.append(file_identity(info))
        for component in root.parts[1:]:
            descriptor = os.open(
                component,
                _posix_open_flags(directory=True),
                dir_fd=descriptors[-1],
            )
            descriptors.append(descriptor)
            info = descriptor_file_stat(descriptor)
            if not _directory_valid(info):
                raise RetainedTreeError("retained directory ancestry is unsafe")
            identities.append(file_identity(info))
        lexical = path_file_stat(root)
        if not _directory_valid(lexical) or file_identity(lexical) != identities[-1]:
            raise RetainedTreeError("retained directory ancestry changed during retention")
        return descriptors, tuple(identities)
    except BaseException as primary:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError as exc:
                primary.add_note(f"retained directory ancestry cleanup failed: {exc}")
        raise


def _posix_file_census_once(
    descriptor: int,
    *,
    maximum_entries: int,
) -> tuple[tuple[str, tuple[int, int, int, int, int, int, int, int]], ...]:
    try:
        raw_names: list[str] = []
        with os.scandir(descriptor) as iterator:
            for entry in iterator:
                raw_names.append(entry.name)
                if len(raw_names) > maximum_entries:
                    break
    except OSError as exc:
        raise RetainedTreeError(f"could not enumerate retained directory: {exc}") from exc
    names = sorted(raw_names, key=os.fsencode)
    if len(names) != len(set(names)):
        raise RetainedTreeError("retained directory returned duplicate names")
    portable_paths: dict[tuple[str, ...], str] = {}
    states: list[tuple[str, tuple[int, int, int, int, int, int, int, int]]] = []
    for name in names:
        if type(name) is not str or not name or name in {".", ".."}:
            raise RetainedTreeError("retained directory returned an invalid name")
        _register_portable_path(name, portable_paths)
        try:
            initial = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            raise RetainedTreeError(
                f"retained directory entry changed during census: {name}: {exc}"
            ) from exc
        if not _file_valid(initial):
            raise RetainedTreeError(
                f"retained directory entry is not a standalone regular file: {name}"
            )
        retained: int | None = None
        try:
            retained = os.open(
                name,
                _posix_open_flags(directory=False),
                dir_fd=descriptor,
            )
            opened = descriptor_file_stat(retained)
            named = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (
                not _file_valid(opened)
                or not _file_valid(named)
                or _topology_state(initial) != _topology_state(opened)
                or _topology_state(opened) != _topology_state(named)
            ):
                raise RetainedTreeError(
                    f"retained directory file binding changed during census: {name}"
                )
            states.append((name, _topology_state(opened)))
        except RetainedTreeError:
            raise
        except OSError as exc:
            raise RetainedTreeError(
                f"could not retain directory file during census: {name}: {exc}"
            ) from exc
        finally:
            if retained is not None:
                os.close(retained)
        if len(states) > maximum_entries:
            raise RetainedTreeCapacityError(maximum_entries)
    return tuple(states)


def _capture_posix_directory_file_census(
    root: Path,
    *,
    maximum_entries: int,
    authority: tuple[int, tuple[int, int]] | None,
    verification_hook: RetainedTreeHook | None,
) -> RetainedDirectoryFileCensus:
    descriptors: list[int] = []
    try:
        descriptors, ancestry = _open_posix_directory_ancestry(root)
        if authority is not None and ancestry[authority[0]] != authority[1]:
            raise RetainedTreeError("retained directory authority changed")
        root_descriptor = descriptors[-1]
        root_identity = ancestry[-1]
        _invoke_hook(verification_hook, "after_root_retained")
        initial = _posix_file_census_once(
            root_descriptor,
            maximum_entries=maximum_entries,
        )
        _invoke_hook(verification_hook, "after_initial_census")
        _invoke_hook(verification_hook, "before_final_verification")
        final = _posix_file_census_once(
            root_descriptor,
            maximum_entries=maximum_entries,
        )
        if final != initial:
            raise RetainedTreeError("retained directory file census changed")
        verification: list[int] = []
        try:
            verification, visible_ancestry = _open_posix_directory_ancestry(root)
            if visible_ancestry != ancestry:
                raise RetainedTreeError("retained directory ancestry changed")
            for descriptor, expected_identity in zip(descriptors, ancestry, strict=True):
                info = descriptor_file_stat(descriptor)
                if not _directory_valid(info) or file_identity(info) != expected_identity:
                    raise RetainedTreeError("retained directory ancestry changed")
        except RetainedTreeError:
            raise
        except OSError as exc:
            raise RetainedTreeError(f"retained directory ancestry changed: {exc}") from exc
        finally:
            _close_descriptors(verification)
        _invoke_hook(verification_hook, "after_final_verification")
        return RetainedDirectoryFileCensus(
            root=root,
            root_identity=root_identity,
            names=tuple(name for name, _state in initial),
        )
    except (RetainedTreeError, RetainedTreeCapacityError):
        raise
    except OSError as exc:
        raise RetainedTreeError(f"could not retain directory file census {root}: {exc}") from exc
    finally:
        _close_descriptors(descriptors)


def _snapshot_expectation(
    source: Path,
    expected: RetainedTreeSnapshot,
) -> tuple[dict[str, dict[str, str]], int, int, int]:
    if not isinstance(expected, RetainedTreeSnapshot) or expected.root != source:
        raise ValueError("expected snapshot must describe the exact retained tree root")
    if (
        type(expected.root_identity) is not tuple
        or len(expected.root_identity) != 2
        or any(type(value) is not int or value < 0 for value in expected.root_identity)
    ):
        raise ValueError("expected snapshot root identity is invalid")
    directories = expected.directories
    if (
        type(directories) is not tuple
        or not directories
        or directories[0] != ""
        or len(directories) != len(set(directories))
        or tuple(sorted(directories)) != directories
    ):
        raise ValueError("expected snapshot directory inventory is invalid")
    directory_set = set(directories)
    children: dict[str, dict[str, str]] = {relative: {} for relative in directories}
    portable_paths: dict[tuple[str, ...], str] = {}

    def register(relative: str, kind: str) -> None:
        _register_portable_path(relative, portable_paths)
        parent, _, name = relative.rpartition("/")
        if parent not in directory_set or name in children[parent]:
            raise ValueError("expected snapshot topology is invalid")
        children[parent][name] = kind

    for relative in directories[1:]:
        if type(relative) is not str or not relative:
            raise ValueError("expected snapshot directory inventory is invalid")
        register(relative, "directory")
    total_bytes = 0
    maximum_file_bytes = 0
    if type(expected.files) is not dict:
        raise ValueError("expected snapshot file inventory is invalid")
    for relative, payload in expected.files.items():
        if type(relative) is not str or not relative or type(payload) is not bytes:
            raise ValueError("expected snapshot file inventory is invalid")
        register(relative, "file")
        size = len(payload)
        total_bytes += size
        maximum_file_bytes = max(maximum_file_bytes, size)
    maximum_entries = len(directories) - 1 + len(expected.files)
    return children, maximum_entries, maximum_file_bytes, total_bytes


def _capture_posix(
    root: Path,
    *,
    exclude_directory: DirectoryExclusion | None,
    verification_hook: RetainedTreeHook | None,
    expected: RetainedTreeSnapshot | None = None,
) -> RetainedTreeSnapshot:
    retained_directories: list[_RetainedDirectory] = []
    retained_files: list[_RetainedFile] = []
    descriptors: list[int] = []
    portable_paths: dict[tuple[str, ...], str] = {}
    expected_children: dict[str, dict[str, str]] | None = None
    maximum_entries = maximum_file_bytes = maximum_total_bytes = 0
    observed_entries = observed_bytes = 0
    if expected is not None:
        (
            expected_children,
            maximum_entries,
            maximum_file_bytes,
            maximum_total_bytes,
        ) = _snapshot_expectation(root, expected)
    try:
        parent_descriptor = os.open(root.parent, _posix_open_flags(directory=True))
        descriptors.append(parent_descriptor)
        parent_info = descriptor_file_stat(parent_descriptor)
        root_descriptor = os.open(
            root.name,
            _posix_open_flags(directory=True),
            dir_fd=parent_descriptor,
        )
        descriptors.append(root_descriptor)
        root_info = descriptor_file_stat(root_descriptor)
        root_named = os.stat(
            root.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        lexical_parent = path_file_stat(root.parent)
        lexical_root = path_file_stat(root)
        if (
            not _directory_valid(parent_info)
            or not _directory_valid(root_info)
            or not _directory_valid(root_named)
            or not _directory_valid(lexical_parent)
            or not _directory_valid(lexical_root)
            or file_identity(parent_info) != file_identity(lexical_parent)
            or file_identity(root_info) != file_identity(root_named)
            or file_identity(root_info) != file_identity(lexical_root)
            or (expected is not None and file_identity(root_info) != expected.root_identity)
        ):
            raise RetainedTreeError("tree root must resolve to one retained real directory")
        retained_directories.append(
            _RetainedDirectory(
                relative="",
                name=root.name,
                descriptor=root_descriptor,
                parent_descriptor=parent_descriptor,
                identity=file_identity(root_info),
            )
        )
        _invoke_hook(verification_hook, "after_root_retained")

        index = 0
        while index < len(retained_directories):
            directory = retained_directories[index]
            index += 1
            entries = _posix_directory_entries(
                directory,
                exclude_directory=exclude_directory,
                portable_paths=portable_paths,
                maximum_entries=(
                    len(expected_children[directory.relative])
                    if expected_children is not None
                    else None
                ),
            )
            if expected_children is not None:
                actual_children = {name: kind for name, _info, kind in entries}
                if actual_children != expected_children[directory.relative]:
                    raise RetainedTreeError(
                        f"retained tree inventory changed: {directory.relative or '.'}"
                    )
            directory.names = tuple(name for name, _info, _kind in entries)
            for name, initial, kind in entries:
                relative = f"{directory.relative}/{name}" if directory.relative else name
                observed_entries += 1
                if expected is not None and observed_entries > maximum_entries:
                    raise RetainedTreeCapacityError(maximum_entries)
                try:
                    child_descriptor = os.open(
                        name,
                        _posix_open_flags(directory=kind == "directory"),
                        dir_fd=directory.descriptor,
                    )
                    descriptors.append(child_descriptor)
                    opened = descriptor_file_stat(child_descriptor)
                    named = os.stat(
                        name,
                        dir_fd=directory.descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise RetainedTreeError(
                        f"could not retain tree entry {relative}: {exc}"
                    ) from exc
                if kind == "directory":
                    if (
                        not _directory_valid(opened)
                        or not _directory_valid(named)
                        or file_identity(initial) != file_identity(opened)
                        or file_identity(opened) != file_identity(named)
                    ):
                        raise RetainedTreeError(
                            f"tree directory changed before retention: {relative}"
                        )
                    retained_directories.append(
                        _RetainedDirectory(
                            relative=relative,
                            name=name,
                            descriptor=child_descriptor,
                            parent_descriptor=directory.descriptor,
                            identity=file_identity(opened),
                        )
                    )
                    _invoke_hook(verification_hook, "after_directory_retained", relative)
                    continue
                if (
                    not _file_valid(opened)
                    or not _file_valid(named)
                    or _entry_state(initial) != _entry_state(opened)
                    or _entry_state(opened) != _entry_state(named)
                ):
                    raise RetainedTreeError(f"tree file changed before retention: {relative}")
                if expected is not None:
                    expected_size = len(expected.files[relative])
                    if opened.st_size != expected_size:
                        raise RetainedTreeError(
                            f"retained tree file size changed before read: {relative}"
                        )
                    if opened.st_size > maximum_file_bytes:
                        raise RetainedTreeError(
                            f"retained tree file size exceeded its bound: {relative}"
                        )
                    observed_bytes += opened.st_size
                    if observed_bytes > maximum_total_bytes:
                        raise RetainedTreeError(
                            "retained tree aggregate bytes exceeded their bound"
                        )
                _invoke_hook(verification_hook, "after_file_retained", relative)
                payload = (
                    _read_descriptor_bounded(child_descriptor, relative, expected_size)
                    if expected is not None
                    else _read_descriptor(child_descriptor, relative)
                )
                retained = descriptor_file_stat(child_descriptor)
                if (
                    not _file_valid(retained)
                    or _entry_state(opened) != _entry_state(retained)
                    or retained.st_size != len(payload)
                ):
                    raise RetainedTreeError(f"tree file changed during retained read: {relative}")
                retained_files.append(
                    _RetainedFile(
                        relative=relative,
                        name=name,
                        descriptor=child_descriptor,
                        parent_descriptor=directory.descriptor,
                        state=_entry_state(opened),
                        payload=payload,
                    )
                )
                _invoke_hook(verification_hook, "after_file_read", relative)

        _invoke_hook(verification_hook, "before_final_verification")
        retained_parent = descriptor_file_stat(parent_descriptor)
        retained_root = descriptor_file_stat(root_descriptor)
        rebound_root = os.open(
            root.name,
            _posix_open_flags(directory=True),
            dir_fd=parent_descriptor,
        )
        try:
            rebound_root_info = descriptor_file_stat(rebound_root)
        finally:
            os.close(rebound_root)
        lexical_parent = path_file_stat(root.parent)
        lexical_root = path_file_stat(root)
        if (
            not _directory_valid(retained_parent)
            or not _directory_valid(retained_root)
            or not _directory_valid(rebound_root_info)
            or not _directory_valid(lexical_parent)
            or not _directory_valid(lexical_root)
            or file_identity(retained_parent) != file_identity(lexical_parent)
            or file_identity(retained_root) != retained_directories[0].identity
            or file_identity(rebound_root_info) != retained_directories[0].identity
            or file_identity(lexical_root) != retained_directories[0].identity
        ):
            raise RetainedTreeError("tree root name no longer resolves to the retained root")

        for directory in retained_directories:
            retained = descriptor_file_stat(directory.descriptor)
            current_entries = _posix_directory_entries(
                directory,
                exclude_directory=exclude_directory,
                portable_paths=None,
                maximum_entries=(
                    len(expected_children[directory.relative])
                    if expected_children is not None
                    else None
                ),
            )
            current_names = tuple(name for name, _info, _kind in current_entries)
            if (
                not _directory_valid(retained)
                or file_identity(retained) != directory.identity
                or current_names != directory.names
            ):
                raise RetainedTreeError(
                    f"retained tree directory changed: {directory.relative or '.'}"
                )
            if directory.relative:
                rebound = os.open(
                    directory.name,
                    _posix_open_flags(directory=True),
                    dir_fd=directory.parent_descriptor,
                )
                try:
                    rebound_info = descriptor_file_stat(rebound)
                finally:
                    os.close(rebound)
                if (
                    not _directory_valid(rebound_info)
                    or file_identity(rebound_info) != directory.identity
                ):
                    raise RetainedTreeError(f"tree directory binding changed: {directory.relative}")

        files: dict[str, bytes] = {}
        for retained_file in retained_files:
            retained = descriptor_file_stat(retained_file.descriptor)
            rebound = os.open(
                retained_file.name,
                _posix_open_flags(directory=False),
                dir_fd=retained_file.parent_descriptor,
            )
            try:
                rebound_info = descriptor_file_stat(rebound)
            finally:
                os.close(rebound)
            if (
                not _file_valid(retained)
                or not _file_valid(rebound_info)
                or _entry_state(retained) != retained_file.state
                or _entry_state(rebound_info) != retained_file.state
                or (
                    _read_descriptor_bounded(
                        retained_file.descriptor,
                        retained_file.relative,
                        len(retained_file.payload),
                    )
                    if expected is not None
                    else _read_descriptor(retained_file.descriptor, retained_file.relative)
                )
                != retained_file.payload
                or _entry_state(descriptor_file_stat(retained_file.descriptor))
                != retained_file.state
            ):
                raise RetainedTreeError(
                    f"tree file binding or bytes changed: {retained_file.relative}"
                )
            files[retained_file.relative] = retained_file.payload
        _invoke_hook(verification_hook, "after_final_verification")
        return RetainedTreeSnapshot(
            root=root,
            root_identity=retained_directories[0].identity,
            directories=tuple(sorted(directory.relative for directory in retained_directories)),
            files=dict(sorted(files.items())),
        )
    except RetainedTreeError:
        raise
    except OSError as exc:
        raise RetainedTreeError(f"could not retain tree {root}: {exc}") from exc
    finally:
        _close_descriptors(descriptors)


def _capture_named_posix(
    prefix: Path,
    *,
    container_name: str,
    child_names: tuple[str, ...],
    verification_hook: RetainedTreeHook | None,
) -> dict[str, RetainedTreeSnapshot | None]:
    descriptors: list[int] = []
    candidate_descriptors: dict[str, int] = {}
    candidate_identities: dict[str, tuple[int, int]] = {}
    snapshots: dict[str, RetainedTreeSnapshot | None] = {name: None for name in child_names}
    try:
        parent_descriptor = os.open(prefix.parent, _posix_open_flags(directory=True))
        descriptors.append(parent_descriptor)
        prefix_descriptor = os.open(
            prefix.name,
            _posix_open_flags(directory=True),
            dir_fd=parent_descriptor,
        )
        descriptors.append(prefix_descriptor)
        parent_info = descriptor_file_stat(parent_descriptor)
        prefix_info = descriptor_file_stat(prefix_descriptor)
        named_prefix = os.stat(
            prefix.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        lexical_parent = path_file_stat(prefix.parent)
        lexical_prefix = path_file_stat(prefix)
        if (
            not _directory_valid(parent_info)
            or not _directory_valid(prefix_info)
            or not _directory_valid(named_prefix)
            or not _directory_valid(lexical_parent)
            or not _directory_valid(lexical_prefix)
            or file_identity(parent_info) != file_identity(lexical_parent)
            or file_identity(prefix_info) != file_identity(named_prefix)
            or file_identity(prefix_info) != file_identity(lexical_prefix)
        ):
            raise RetainedTreeError(
                "named-child prefix must resolve to one retained real directory"
            )
        prefix_identity = file_identity(prefix_info)
        prefix_census, prefix_infos = _posix_direct_census(
            prefix_descriptor,
            context="named-child prefix",
        )
        _invoke_hook(verification_hook, "after_prefix_census")
        container_initial = _exact_named_info(
            prefix_infos,
            container_name,
            context="named-child prefix",
        )
        container_descriptor: int | None = None
        container_identity: tuple[int, int] | None = None
        container_census: tuple[tuple[str, tuple[int, int, int, int, int, int, int, int]], ...] = ()
        if container_initial is not None:
            if not _directory_valid(container_initial):
                raise RetainedTreeError(
                    f"named-child container is not a retained real directory: {container_name}"
                )
            container_descriptor = os.open(
                container_name,
                _posix_open_flags(directory=True),
                dir_fd=prefix_descriptor,
            )
            descriptors.append(container_descriptor)
            container_opened = descriptor_file_stat(container_descriptor)
            container_named = os.stat(
                container_name,
                dir_fd=prefix_descriptor,
                follow_symlinks=False,
            )
            if (
                not _directory_valid(container_opened)
                or not _directory_valid(container_named)
                or file_identity(container_initial) != file_identity(container_opened)
                or file_identity(container_opened) != file_identity(container_named)
            ):
                raise RetainedTreeError(
                    f"named-child container changed before retention: {container_name}"
                )
            container_identity = file_identity(container_opened)
            container_census, container_infos = _posix_direct_census(
                container_descriptor,
                context="named-child container",
            )
            _invoke_hook(verification_hook, "after_share_census")
            for child_name in child_names:
                child_initial = _exact_named_info(
                    container_infos,
                    child_name,
                    context="named-child container",
                )
                if child_initial is None:
                    continue
                if not _directory_valid(child_initial):
                    raise RetainedTreeError(
                        f"named child is not a retained real directory: {child_name}"
                    )
                child_descriptor = os.open(
                    child_name,
                    _posix_open_flags(directory=True),
                    dir_fd=container_descriptor,
                )
                descriptors.append(child_descriptor)
                child_opened = descriptor_file_stat(child_descriptor)
                child_named = os.stat(
                    child_name,
                    dir_fd=container_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not _directory_valid(child_opened)
                    or not _directory_valid(child_named)
                    or file_identity(child_initial) != file_identity(child_opened)
                    or file_identity(child_opened) != file_identity(child_named)
                ):
                    raise RetainedTreeError(f"named child changed before retention: {child_name}")
                candidate_descriptors[child_name] = child_descriptor
                candidate_identities[child_name] = file_identity(child_opened)
                _invoke_hook(
                    verification_hook,
                    "after_candidate_retained",
                    child_name,
                )
                snapshot = capture_retained_tree(
                    prefix / container_name / child_name,
                )
                _invoke_hook(
                    verification_hook,
                    "before_candidate_binding_verification",
                    child_name,
                )
                rebound = os.open(
                    child_name,
                    _posix_open_flags(directory=True),
                    dir_fd=container_descriptor,
                )
                try:
                    rebound_info = descriptor_file_stat(rebound)
                finally:
                    os.close(rebound)
                retained_info = descriptor_file_stat(child_descriptor)
                expected_identity = candidate_identities[child_name]
                if (
                    snapshot.root_identity != expected_identity
                    or not _directory_valid(retained_info)
                    or not _directory_valid(rebound_info)
                    or file_identity(retained_info) != expected_identity
                    or file_identity(rebound_info) != expected_identity
                ):
                    raise RetainedTreeError(
                        f"named child binding changed during retained capture: {child_name}"
                    )
                snapshots[child_name] = snapshot
        else:
            _invoke_hook(verification_hook, "after_share_census")

        _invoke_hook(verification_hook, "before_final_prefix_verification")
        final_prefix_census, _final_prefix_infos = _posix_direct_census(
            prefix_descriptor,
            context="named-child prefix",
        )
        retained_parent = descriptor_file_stat(parent_descriptor)
        retained_prefix = descriptor_file_stat(prefix_descriptor)
        rebound_prefix = os.open(
            prefix.name,
            _posix_open_flags(directory=True),
            dir_fd=parent_descriptor,
        )
        try:
            rebound_prefix_info = descriptor_file_stat(rebound_prefix)
        finally:
            os.close(rebound_prefix)
        lexical_parent = path_file_stat(prefix.parent)
        lexical_prefix = path_file_stat(prefix)
        if (
            final_prefix_census != prefix_census
            or not _directory_valid(retained_parent)
            or not _directory_valid(retained_prefix)
            or not _directory_valid(rebound_prefix_info)
            or not _directory_valid(lexical_parent)
            or not _directory_valid(lexical_prefix)
            or file_identity(retained_parent) != file_identity(lexical_parent)
            or file_identity(retained_prefix) != prefix_identity
            or file_identity(rebound_prefix_info) != prefix_identity
            or file_identity(lexical_prefix) != prefix_identity
        ):
            raise RetainedTreeError("named-child prefix topology changed")
        if container_descriptor is not None:
            assert container_identity is not None
            final_container_census, _final_container_infos = _posix_direct_census(
                container_descriptor,
                context="named-child container",
            )
            rebound_container = os.open(
                container_name,
                _posix_open_flags(directory=True),
                dir_fd=prefix_descriptor,
            )
            try:
                rebound_container_info = descriptor_file_stat(rebound_container)
            finally:
                os.close(rebound_container)
            retained_container = descriptor_file_stat(container_descriptor)
            if (
                final_container_census != container_census
                or not _directory_valid(retained_container)
                or not _directory_valid(rebound_container_info)
                or file_identity(retained_container) != container_identity
                or file_identity(rebound_container_info) != container_identity
            ):
                raise RetainedTreeError("named-child container topology changed")
            for child_name, child_descriptor in candidate_descriptors.items():
                rebound = os.open(
                    child_name,
                    _posix_open_flags(directory=True),
                    dir_fd=container_descriptor,
                )
                try:
                    rebound_info = descriptor_file_stat(rebound)
                finally:
                    os.close(rebound)
                retained = descriptor_file_stat(child_descriptor)
                expected_identity = candidate_identities[child_name]
                if (
                    not _directory_valid(retained)
                    or not _directory_valid(rebound_info)
                    or file_identity(retained) != expected_identity
                    or file_identity(rebound_info) != expected_identity
                ):
                    raise RetainedTreeError(
                        f"named child binding changed before return: {child_name}"
                    )
        _invoke_hook(verification_hook, "after_final_prefix_verification")
        return snapshots
    except RetainedTreeError:
        raise
    except OSError as exc:
        raise RetainedTreeError(f"could not retain named children below {prefix}: {exc}") from exc
    finally:
        _close_descriptors(descriptors)


class _WindowsUnicodeString(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ushort),
        ("maximum_length", ctypes.c_ushort),
        ("buffer", ctypes.c_void_p),
    ]


class _WindowsObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("root_directory", ctypes.c_void_p),
        ("object_name", ctypes.POINTER(_WindowsUnicodeString)),
        ("attributes", ctypes.c_ulong),
        ("security_descriptor", ctypes.c_void_p),
        ("security_quality_of_service", ctypes.c_void_p),
    ]


class _WindowsIoStatusBlock(ctypes.Structure):
    _fields_ = [
        ("status", ctypes.c_void_p),
        ("information", ctypes.c_size_t),
    ]


class _WindowsTreeApi:
    _FILE_READ_DATA = 0x00000001
    _FILE_LIST_DIRECTORY = 0x00000001
    _FILE_READ_ATTRIBUTES = 0x00000080
    _SYNCHRONIZE = 0x00100000
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _FILE_SHARE_ALL = _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE
    _FILE_SHARE_MODE = _FILE_SHARE_ALL
    _OPEN_EXISTING = 3
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_OPEN = 1
    _FILE_DIRECTORY_FILE = 0x00000001
    _FILE_NON_DIRECTORY_FILE = 0x00000040
    _FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _FILE_OPEN_REPARSE_POINT = 0x00200000
    _FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
    _OBJ_CASE_INSENSITIVE = 0x00000040
    _FILE_NAMES_INFORMATION = 12
    _FILE_BEGIN = 0
    _STATUS_BUFFER_OVERFLOW = 0x80000005
    _STATUS_NO_MORE_FILES = 0x80000006
    _STATUS_NOT_A_DIRECTORY = 0xC0000103
    _QUERY_BUFFER_BYTES = 64 * 1024

    def __init__(self) -> None:
        win_dll = getattr(ctypes, "WinDLL", None)
        if os.name != "nt" or win_dll is None:
            raise OSError("native Windows retained-tree APIs are unavailable")
        self._kernel32 = win_dll("kernel32", use_last_error=True)
        self._ntdll = win_dll("ntdll", use_last_error=True)
        self._invalid_handle = ctypes.c_void_p(-1).value

        self._create_file = self._kernel32.CreateFileW
        self._create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        self._create_file.restype = ctypes.c_void_p
        self._close_handle = self._kernel32.CloseHandle
        self._close_handle.argtypes = [ctypes.c_void_p]
        self._close_handle.restype = ctypes.c_int
        self._read_file = self._kernel32.ReadFile
        self._read_file.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        self._read_file.restype = ctypes.c_int
        self._set_file_pointer = self._kernel32.SetFilePointerEx
        self._set_file_pointer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.POINTER(ctypes.c_int64),
            ctypes.c_uint32,
        ]
        self._set_file_pointer.restype = ctypes.c_int
        self._nt_create_file = self._ntdll.NtCreateFile
        self._nt_create_file.argtypes = [
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_uint32,
            ctypes.POINTER(_WindowsObjectAttributes),
            ctypes.POINTER(_WindowsIoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._nt_create_file.restype = ctypes.c_long
        self._nt_query_directory = self._ntdll.NtQueryDirectoryFile
        self._nt_query_directory.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(_WindowsIoStatusBlock),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_ubyte,
            ctypes.c_void_p,
            ctypes.c_ubyte,
        ]
        self._nt_query_directory.restype = ctypes.c_long
        self._rtl_nt_status_to_dos_error = self._ntdll.RtlNtStatusToDosError
        self._rtl_nt_status_to_dos_error.argtypes = [ctypes.c_long]
        self._rtl_nt_status_to_dos_error.restype = ctypes.c_ulong

    @staticmethod
    def _unsigned_status(status: int) -> int:
        return ctypes.c_uint32(status).value

    def _nt_error(self, status: int, context: str) -> OSError:
        mapped = int(self._rtl_nt_status_to_dos_error(ctypes.c_long(ctypes.c_int32(status).value)))
        error = ctypes.WinError(mapped)
        return OSError(error.errno, f"{context}: {error.strerror}", None, mapped)

    @staticmethod
    def _validate_component(name: str) -> None:
        if (
            type(name) is not str
            or not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
        ):
            raise RetainedTreeError("Windows returned an invalid tree path component")

    def open_path_directory(self, path: Path, *, share: int | None = None) -> int:
        handle = self._create_file(
            str(path),
            self._FILE_LIST_DIRECTORY | self._FILE_READ_ATTRIBUTES | self._SYNCHRONIZE,
            self._FILE_SHARE_MODE if share is None else share,
            None,
            self._OPEN_EXISTING,
            self._FILE_FLAG_OPEN_REPARSE_POINT | self._FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle in {None, self._invalid_handle}:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    def _nt_open(
        self,
        parent_handle: int,
        name: str,
        *,
        directory: bool,
    ) -> tuple[int | None, int]:
        self._validate_component(name)
        encoded_name = name.encode("utf-16-le", errors="strict")
        if len(encoded_name) > 0xFFFC:
            raise RetainedTreeError("Windows returned an overlong tree path component")
        name_buffer = ctypes.create_unicode_buffer(name)
        unicode_name = _WindowsUnicodeString(
            length=len(encoded_name),
            maximum_length=len(encoded_name) + ctypes.sizeof(ctypes.c_wchar),
            buffer=ctypes.cast(name_buffer, ctypes.c_void_p),
        )
        attributes = _WindowsObjectAttributes(
            length=ctypes.sizeof(_WindowsObjectAttributes),
            root_directory=ctypes.c_void_p(parent_handle),
            object_name=ctypes.pointer(unicode_name),
            attributes=self._OBJ_CASE_INSENSITIVE,
            security_descriptor=None,
            security_quality_of_service=None,
        )
        io_status = _WindowsIoStatusBlock()
        opened = ctypes.c_void_p()
        access = (
            (self._FILE_LIST_DIRECTORY if directory else self._FILE_READ_DATA)
            | self._FILE_READ_ATTRIBUTES
            | self._SYNCHRONIZE
        )
        options = self._FILE_DIRECTORY_FILE if directory else self._FILE_NON_DIRECTORY_FILE
        options |= (
            self._FILE_SYNCHRONOUS_IO_NONALERT
            | self._FILE_OPEN_REPARSE_POINT
            | self._FILE_OPEN_FOR_BACKUP_INTENT
        )
        status = int(
            self._nt_create_file(
                ctypes.byref(opened),
                access,
                ctypes.byref(attributes),
                ctypes.byref(io_status),
                None,
                0,
                self._FILE_SHARE_MODE,
                self._FILE_OPEN,
                options,
                None,
                0,
            )
        )
        unsigned = self._unsigned_status(status)
        if status < 0:
            return None, unsigned
        if opened.value in {None, self._invalid_handle}:
            raise RetainedTreeError(f"Windows returned no retained handle for {name}")
        return int(opened.value), unsigned

    def open_relative(self, parent_handle: int, name: str, *, directory: bool) -> int:
        handle, status = self._nt_open(parent_handle, name, directory=directory)
        if handle is None:
            raise self._nt_error(status, f"could not retain Windows tree entry {name}")
        return handle

    def _query_names(
        self,
        directory_handle: int,
        relative: str,
        *,
        maximum_entries: int | None = None,
    ) -> tuple[str, ...]:
        names: list[str] = []
        first_query = True
        while True:
            io_status = _WindowsIoStatusBlock()
            buffer = ctypes.create_string_buffer(self._QUERY_BUFFER_BYTES)
            status = int(
                self._nt_query_directory(
                    ctypes.c_void_p(directory_handle),
                    None,
                    None,
                    None,
                    ctypes.byref(io_status),
                    buffer,
                    len(buffer),
                    self._FILE_NAMES_INFORMATION,
                    0,
                    None,
                    int(first_query),
                )
            )
            first_query = False
            unsigned = self._unsigned_status(status)
            if unsigned == self._STATUS_NO_MORE_FILES:
                break
            if unsigned not in {0, self._STATUS_BUFFER_OVERFLOW}:
                raise self._nt_error(
                    unsigned,
                    f"could not enumerate Windows tree directory {relative or '.'}",
                )
            used = int(io_status.information)
            if used < 0 or used > len(buffer):
                raise RetainedTreeError(
                    f"Windows returned an invalid directory inventory for {relative or '.'}"
                )
            offset = 0
            while offset < used:
                if used - offset < 12:
                    raise RetainedTreeError(
                        f"Windows returned a truncated directory entry for {relative or '.'}"
                    )
                next_offset = int.from_bytes(buffer.raw[offset : offset + 4], "little")
                name_bytes = int.from_bytes(buffer.raw[offset + 8 : offset + 12], "little")
                name_end = offset + 12 + name_bytes
                if (
                    name_bytes % 2
                    or name_end > used
                    or (
                        next_offset != 0
                        and (next_offset < 12 + name_bytes or offset + next_offset > used)
                    )
                ):
                    raise RetainedTreeError(
                        f"Windows returned an invalid directory entry for {relative or '.'}"
                    )
                try:
                    name = buffer.raw[offset + 12 : name_end].decode(
                        "utf-16-le",
                        errors="strict",
                    )
                except UnicodeError as exc:
                    raise RetainedTreeError(
                        f"Windows returned a non-Unicode tree entry name: {exc}"
                    ) from exc
                if name not in {".", ".."}:
                    self._validate_component(name)
                    names.append(name)
                    if maximum_entries is not None and len(names) > maximum_entries:
                        return tuple(sorted(names, key=lambda item: item.encode("utf-8")))
                if next_offset == 0:
                    break
                offset += next_offset
            if unsigned == 0 and used == 0:
                break
        if len(names) != len(set(names)):
            raise RetainedTreeError(
                f"Windows returned duplicate directory entries for {relative or '.'}"
            )
        return tuple(sorted(names, key=lambda item: item.encode("utf-8")))

    def open_directory_entries(
        self,
        directory_handle: int,
        relative: str,
        *,
        exclude_directory: DirectoryExclusion | None,
        portable_paths: dict[tuple[str, ...], str] | None,
        strict_entries: bool = True,
        maximum_entries: int | None = None,
    ) -> tuple[tuple[str, ...], list[tuple[str, int, str, FileStat]]]:
        names: list[str] = []
        entries: list[tuple[str, int, str, FileStat]] = []
        opened_handles: list[int] = []
        try:
            for name in self._query_names(
                directory_handle,
                relative,
                maximum_entries=maximum_entries,
            ):
                child_relative = f"{relative}/{name}" if relative else name
                if portable_paths is not None:
                    _register_portable_path(child_relative, portable_paths)
                else:
                    _portable_relative(child_relative)
                directory_child, status = self._nt_open(
                    directory_handle,
                    name,
                    directory=True,
                )
                if directory_child is not None:
                    handle = directory_child
                    expected_kind = "directory"
                elif status == self._STATUS_NOT_A_DIRECTORY:
                    file_child, file_status = self._nt_open(
                        directory_handle,
                        name,
                        directory=False,
                    )
                    if file_child is None:
                        raise self._nt_error(
                            file_status,
                            f"could not retain Windows tree entry {child_relative}",
                        )
                    handle = file_child
                    expected_kind = "file"
                else:
                    raise self._nt_error(
                        status,
                        f"could not retain Windows tree entry {child_relative}",
                    )
                opened_handles.append(handle)
                info = windows_handle_file_stat(handle)
                kind = _entry_kind(info, child_relative) if strict_entries else expected_kind
                if strict_entries and kind != expected_kind:
                    raise RetainedTreeError(
                        f"tree entry type changed during retention: {child_relative}"
                    )
                if (
                    kind == "directory"
                    and exclude_directory is not None
                    and exclude_directory(child_relative)
                ):
                    self.close(handle)
                    opened_handles.pop()
                    continue
                names.append(name)
                entries.append((name, handle, kind, info))
                if maximum_entries is not None and len(entries) > maximum_entries:
                    raise RetainedTreeCapacityError(maximum_entries)
            return tuple(names), entries
        except BaseException:
            self.close_many(opened_handles)
            raise

    def read_file(self, handle: int, relative: str) -> bytes:
        position = ctypes.c_int64()
        if not self._set_file_pointer(
            ctypes.c_void_p(handle),
            0,
            ctypes.byref(position),
            self._FILE_BEGIN,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        chunks: list[bytes] = []
        while True:
            buffer = ctypes.create_string_buffer(1024 * 1024)
            read = ctypes.c_uint32()
            if not self._read_file(
                ctypes.c_void_p(handle),
                buffer,
                len(buffer),
                ctypes.byref(read),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            count = int(read.value)
            if count == 0:
                return b"".join(chunks)
            chunks.append(buffer.raw[:count])

    def read_file_bounded(self, handle: int, relative: str, maximum_bytes: int) -> bytes:
        position = ctypes.c_int64()
        if not self._set_file_pointer(
            ctypes.c_void_p(handle),
            0,
            ctypes.byref(position),
            self._FILE_BEGIN,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        payload = bytearray()
        while len(payload) <= maximum_bytes:
            requested = min(1024 * 1024, maximum_bytes + 1 - len(payload))
            buffer = ctypes.create_string_buffer(requested)
            read = ctypes.c_uint32()
            if not self._read_file(
                ctypes.c_void_p(handle),
                buffer,
                len(buffer),
                ctypes.byref(read),
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            count = int(read.value)
            if count == 0:
                return bytes(payload)
            payload.extend(buffer.raw[:count])
        raise RetainedTreeError(f"retained tree file size exceeded its bound: {relative}")

    def close(self, handle: int) -> None:
        if not self._close_handle(ctypes.c_void_p(handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close_many(self, handles: Sequence[int]) -> None:
        primary = sys.exception()
        cleanup_error: OSError | None = None
        for handle in reversed(tuple(handles)):
            try:
                self.close(handle)
            except OSError as exc:
                if primary is not None:
                    primary.add_note(f"retained Windows handle cleanup failed: {exc}")
                elif cleanup_error is None:
                    cleanup_error = exc
        if cleanup_error is not None:
            raise RetainedTreeError(
                f"could not close retained Windows tree handle: {cleanup_error}"
            ) from cleanup_error


def _capture_windows(
    root: Path,
    *,
    exclude_directory: DirectoryExclusion | None,
    verification_hook: RetainedTreeHook | None,
    expected: RetainedTreeSnapshot | None = None,
) -> RetainedTreeSnapshot:
    try:
        api = _WindowsTreeApi()
    except OSError as exc:
        raise RetainedTreeError(f"safe Windows directory retention is unavailable: {exc}") from exc
    handles: list[int] = []
    retained_directories: list[_RetainedDirectory] = []
    retained_files: list[_RetainedFile] = []
    portable_paths: dict[tuple[str, ...], str] = {}
    expected_children: dict[str, dict[str, str]] | None = None
    maximum_entries = maximum_file_bytes = maximum_total_bytes = 0
    observed_entries = observed_bytes = 0
    if expected is not None:
        (
            expected_children,
            maximum_entries,
            maximum_file_bytes,
            maximum_total_bytes,
        ) = _snapshot_expectation(root, expected)
    try:
        parent_handle = api.open_path_directory(root.parent)
        handles.append(parent_handle)
        parent_info = windows_handle_file_stat(parent_handle)
        root_handle = api.open_relative(parent_handle, root.name, directory=True)
        handles.append(root_handle)
        root_info = windows_handle_file_stat(root_handle)
        lexical_parent = path_file_stat(root.parent)
        lexical_root = path_file_stat(root)
        if (
            not _directory_valid(parent_info)
            or not _directory_valid(root_info)
            or not _directory_valid(lexical_parent)
            or not _directory_valid(lexical_root)
            or file_identity(parent_info) != file_identity(lexical_parent)
            or file_identity(root_info) != file_identity(lexical_root)
            or (expected is not None and file_identity(root_info) != expected.root_identity)
        ):
            raise RetainedTreeError("tree root must resolve to one retained real directory")
        retained_directories.append(
            _RetainedDirectory(
                relative="",
                name=root.name,
                descriptor=root_handle,
                parent_descriptor=parent_handle,
                identity=file_identity(root_info),
            )
        )
        _invoke_hook(verification_hook, "after_root_retained")

        index = 0
        while index < len(retained_directories):
            directory = retained_directories[index]
            index += 1
            names, entries = api.open_directory_entries(
                directory.descriptor,
                directory.relative,
                exclude_directory=exclude_directory,
                portable_paths=portable_paths,
                maximum_entries=(
                    len(expected_children[directory.relative])
                    if expected_children is not None
                    else None
                ),
            )
            if expected_children is not None:
                actual_children = {name: kind for name, _handle, kind, _info in entries}
                if actual_children != expected_children[directory.relative]:
                    raise RetainedTreeError(
                        f"retained tree inventory changed: {directory.relative or '.'}"
                    )
            directory.names = names
            handles.extend(handle for _name, handle, _kind, _info in entries)
            for name, child_handle, kind, opened in entries:
                relative = f"{directory.relative}/{name}" if directory.relative else name
                observed_entries += 1
                if expected is not None and observed_entries > maximum_entries:
                    raise RetainedTreeCapacityError(maximum_entries)
                rebound = api.open_relative(
                    directory.descriptor,
                    name,
                    directory=kind == "directory",
                )
                try:
                    rebound_info = windows_handle_file_stat(rebound)
                finally:
                    api.close(rebound)
                if kind == "directory":
                    if (
                        not _directory_valid(opened)
                        or not _directory_valid(rebound_info)
                        or file_identity(opened) != file_identity(rebound_info)
                    ):
                        raise RetainedTreeError(
                            f"tree directory changed before retention: {relative}"
                        )
                    retained_directories.append(
                        _RetainedDirectory(
                            relative=relative,
                            name=name,
                            descriptor=child_handle,
                            parent_descriptor=directory.descriptor,
                            identity=file_identity(opened),
                        )
                    )
                    _invoke_hook(verification_hook, "after_directory_retained", relative)
                    continue
                if (
                    not _file_valid(opened)
                    or not _file_valid(rebound_info)
                    or _entry_state(opened) != _entry_state(rebound_info)
                ):
                    raise RetainedTreeError(f"tree file changed before retention: {relative}")
                if expected is not None:
                    expected_size = len(expected.files[relative])
                    if opened.st_size != expected_size:
                        raise RetainedTreeError(
                            f"retained tree file size changed before read: {relative}"
                        )
                    if opened.st_size > maximum_file_bytes:
                        raise RetainedTreeError(
                            f"retained tree file size exceeded its bound: {relative}"
                        )
                    observed_bytes += opened.st_size
                    if observed_bytes > maximum_total_bytes:
                        raise RetainedTreeError(
                            "retained tree aggregate bytes exceeded their bound"
                        )
                _invoke_hook(verification_hook, "after_file_retained", relative)
                payload = (
                    api.read_file_bounded(child_handle, relative, expected_size)
                    if expected is not None
                    else api.read_file(child_handle, relative)
                )
                retained = windows_handle_file_stat(child_handle)
                if (
                    not _file_valid(retained)
                    or _entry_state(opened) != _entry_state(retained)
                    or retained.st_size != len(payload)
                ):
                    raise RetainedTreeError(f"tree file changed during retained read: {relative}")
                retained_files.append(
                    _RetainedFile(
                        relative=relative,
                        name=name,
                        descriptor=child_handle,
                        parent_descriptor=directory.descriptor,
                        state=_entry_state(opened),
                        payload=payload,
                    )
                )
                _invoke_hook(verification_hook, "after_file_read", relative)

        _invoke_hook(verification_hook, "before_final_verification")
        retained_parent = windows_handle_file_stat(parent_handle)
        retained_root = windows_handle_file_stat(root_handle)
        rebound_root = api.open_relative(parent_handle, root.name, directory=True)
        try:
            rebound_root_info = windows_handle_file_stat(rebound_root)
        finally:
            api.close(rebound_root)
        lexical_parent = path_file_stat(root.parent)
        lexical_root = path_file_stat(root)
        if (
            not _directory_valid(retained_parent)
            or not _directory_valid(retained_root)
            or not _directory_valid(rebound_root_info)
            or not _directory_valid(lexical_parent)
            or not _directory_valid(lexical_root)
            or file_identity(retained_parent) != file_identity(lexical_parent)
            or file_identity(retained_root) != retained_directories[0].identity
            or file_identity(rebound_root_info) != retained_directories[0].identity
            or file_identity(lexical_root) != retained_directories[0].identity
        ):
            raise RetainedTreeError(
                "tree root name no longer resolves to the retained Windows root"
            )

        for directory in retained_directories:
            retained = windows_handle_file_stat(directory.descriptor)
            current_names, current_entries = api.open_directory_entries(
                directory.descriptor,
                directory.relative,
                exclude_directory=exclude_directory,
                portable_paths=None,
                maximum_entries=(
                    len(expected_children[directory.relative])
                    if expected_children is not None
                    else None
                ),
            )
            api.close_many([handle for _name, handle, _kind, _info in current_entries])
            if (
                not _directory_valid(retained)
                or file_identity(retained) != directory.identity
                or current_names != directory.names
            ):
                raise RetainedTreeError(
                    f"retained tree directory changed: {directory.relative or '.'}"
                )
            if directory.relative:
                rebound = api.open_relative(
                    directory.parent_descriptor,
                    directory.name,
                    directory=True,
                )
                try:
                    rebound_info = windows_handle_file_stat(rebound)
                finally:
                    api.close(rebound)
                if (
                    not _directory_valid(rebound_info)
                    or file_identity(rebound_info) != directory.identity
                ):
                    raise RetainedTreeError(f"tree directory binding changed: {directory.relative}")

        files: dict[str, bytes] = {}
        for retained_file in retained_files:
            retained = windows_handle_file_stat(retained_file.descriptor)
            rebound = api.open_relative(
                retained_file.parent_descriptor,
                retained_file.name,
                directory=False,
            )
            try:
                rebound_info = windows_handle_file_stat(rebound)
            finally:
                api.close(rebound)
            if (
                not _file_valid(retained)
                or not _file_valid(rebound_info)
                or _entry_state(retained) != retained_file.state
                or _entry_state(rebound_info) != retained_file.state
                or (
                    api.read_file_bounded(
                        retained_file.descriptor,
                        retained_file.relative,
                        len(retained_file.payload),
                    )
                    if expected is not None
                    else api.read_file(retained_file.descriptor, retained_file.relative)
                )
                != retained_file.payload
                or _entry_state(windows_handle_file_stat(retained_file.descriptor))
                != retained_file.state
            ):
                raise RetainedTreeError(
                    f"tree file binding or bytes changed: {retained_file.relative}"
                )
            files[retained_file.relative] = retained_file.payload
        _invoke_hook(verification_hook, "after_final_verification")
        return RetainedTreeSnapshot(
            root=root,
            root_identity=retained_directories[0].identity,
            directories=tuple(sorted(directory.relative for directory in retained_directories)),
            files=dict(sorted(files.items())),
        )
    except RetainedTreeError:
        raise
    except OSError as exc:
        raise RetainedTreeError(f"could not retain Windows tree {root}: {exc}") from exc
    finally:
        api.close_many(handles)


def _windows_direct_census(
    api: _WindowsTreeApi,
    directory_handle: int,
    *,
    context: str,
) -> tuple[
    tuple[tuple[str, tuple[int, int, int, int, int, int, int, int]], ...],
    dict[str, FileStat],
    dict[str, int],
]:
    names, entries = api.open_directory_entries(
        directory_handle,
        context,
        exclude_directory=None,
        portable_paths=None,
        strict_entries=False,
    )
    infos = {name: info for name, _handle, _kind, info in entries}
    handles = {name: handle for name, handle, _kind, _info in entries}
    states = tuple((name, _topology_state(infos[name])) for name in names)
    return states, infos, handles


def _open_windows_directory_ancestry(
    api: _WindowsTreeApi,
    root: Path,
) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    handles: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        handle = api.open_path_directory(Path(root.anchor))
        handles.append(handle)
        info = windows_handle_file_stat(handle)
        if not _directory_valid(info):
            raise RetainedTreeError("retained directory ancestry is unsafe")
        identities.append(file_identity(info))
        for component in root.parts[1:]:
            handle = api.open_relative(handles[-1], component, directory=True)
            handles.append(handle)
            info = windows_handle_file_stat(handle)
            if not _directory_valid(info):
                raise RetainedTreeError("retained directory ancestry is unsafe")
            identities.append(file_identity(info))
        lexical = path_file_stat(root)
        if not _directory_valid(lexical) or file_identity(lexical) != identities[-1]:
            raise RetainedTreeError("retained directory ancestry changed during retention")
        return handles, tuple(identities)
    except BaseException:
        api.close_many(handles)
        raise


def _windows_file_census_once(
    api: _WindowsTreeApi,
    directory_handle: int,
    *,
    maximum_entries: int,
) -> tuple[tuple[str, tuple[int, int, int, int, int, int, int, int]], ...]:
    names = api._query_names(
        directory_handle,
        "retained directory census",
        maximum_entries=maximum_entries,
    )
    portable_paths: dict[tuple[str, ...], str] = {}
    states: list[tuple[str, tuple[int, int, int, int, int, int, int, int]]] = []
    for name in names:
        _register_portable_path(name, portable_paths)
        handles: list[int] = []
        try:
            directory_child, status = api._nt_open(
                directory_handle,
                name,
                directory=True,
            )
            if directory_child is not None:
                handles.append(directory_child)
                windows_handle_file_stat(directory_child)
                raise RetainedTreeError(
                    f"retained directory entry is not a standalone regular file: {name}"
                )
            if status != api._STATUS_NOT_A_DIRECTORY:
                raise api._nt_error(
                    status,
                    f"could not retain Windows directory entry {name}",
                )
            retained, file_status = api._nt_open(
                directory_handle,
                name,
                directory=False,
            )
            if retained is None:
                raise api._nt_error(
                    file_status,
                    f"could not retain Windows directory file {name}",
                )
            handles.append(retained)
            opened = windows_handle_file_stat(retained)
            if not _file_valid(opened):
                raise RetainedTreeError(
                    f"retained directory entry is not a standalone regular file: {name}"
                )
            rebound = api.open_relative(directory_handle, name, directory=False)
            handles.append(rebound)
            rebound_info = windows_handle_file_stat(rebound)
            retained_info = windows_handle_file_stat(retained)
            if (
                not _file_valid(retained_info)
                or not _file_valid(rebound_info)
                or _topology_state(opened) != _topology_state(retained_info)
                or _topology_state(retained_info) != _topology_state(rebound_info)
            ):
                raise RetainedTreeError(
                    f"retained directory file binding changed during census: {name}"
                )
            states.append((name, _topology_state(retained_info)))
        except RetainedTreeError:
            raise
        except OSError as exc:
            raise RetainedTreeError(
                f"could not retain Windows directory file during census: {name}: {exc}"
            ) from exc
        finally:
            api.close_many(handles)
        if len(states) > maximum_entries:
            raise RetainedTreeCapacityError(maximum_entries)
    return tuple(states)


def _capture_windows_directory_file_census(
    root: Path,
    *,
    maximum_entries: int,
    authority: tuple[int, tuple[int, int]] | None,
    verification_hook: RetainedTreeHook | None,
) -> RetainedDirectoryFileCensus:
    try:
        api = _WindowsTreeApi()
    except OSError as exc:
        raise RetainedTreeError(f"safe Windows directory retention is unavailable: {exc}") from exc
    handles: list[int] = []
    try:
        handles, ancestry = _open_windows_directory_ancestry(api, root)
        if authority is not None and ancestry[authority[0]] != authority[1]:
            raise RetainedTreeError("retained directory authority changed")
        root_handle = handles[-1]
        root_identity = ancestry[-1]
        _invoke_hook(verification_hook, "after_root_retained")
        initial = _windows_file_census_once(
            api,
            root_handle,
            maximum_entries=maximum_entries,
        )
        _invoke_hook(verification_hook, "after_initial_census")
        _invoke_hook(verification_hook, "before_final_verification")
        final = _windows_file_census_once(
            api,
            root_handle,
            maximum_entries=maximum_entries,
        )
        if final != initial:
            raise RetainedTreeError("retained directory file census changed")
        verification: list[int] = []
        try:
            verification, visible_ancestry = _open_windows_directory_ancestry(api, root)
            if visible_ancestry != ancestry:
                raise RetainedTreeError("retained directory ancestry changed")
            for handle, expected_identity in zip(handles, ancestry, strict=True):
                info = windows_handle_file_stat(handle)
                if not _directory_valid(info) or file_identity(info) != expected_identity:
                    raise RetainedTreeError("retained directory ancestry changed")
        except RetainedTreeError:
            raise
        except OSError as exc:
            raise RetainedTreeError(f"retained directory ancestry changed: {exc}") from exc
        finally:
            api.close_many(verification)
        _invoke_hook(verification_hook, "after_final_verification")
        return RetainedDirectoryFileCensus(
            root=root,
            root_identity=root_identity,
            names=tuple(name for name, _state in initial),
        )
    except RetainedTreeError:
        raise
    except OSError as exc:
        raise RetainedTreeError(
            f"could not retain Windows directory file census {root}: {exc}"
        ) from exc
    finally:
        api.close_many(handles)


def _capture_named_windows(
    prefix: Path,
    *,
    container_name: str,
    child_names: tuple[str, ...],
    verification_hook: RetainedTreeHook | None,
) -> dict[str, RetainedTreeSnapshot | None]:
    try:
        api = _WindowsTreeApi()
    except OSError as exc:
        raise RetainedTreeError(
            f"safe Windows named-child retention is unavailable: {exc}"
        ) from exc
    handles: list[int] = []
    candidate_handles: dict[str, int] = {}
    candidate_identities: dict[str, tuple[int, int]] = {}
    snapshots: dict[str, RetainedTreeSnapshot | None] = {name: None for name in child_names}
    try:
        parent_handle = api.open_path_directory(prefix.parent)
        handles.append(parent_handle)
        prefix_handle = api.open_relative(parent_handle, prefix.name, directory=True)
        handles.append(prefix_handle)
        parent_info = windows_handle_file_stat(parent_handle)
        prefix_info = windows_handle_file_stat(prefix_handle)
        lexical_parent = path_file_stat(prefix.parent)
        lexical_prefix = path_file_stat(prefix)
        if (
            not _directory_valid(parent_info)
            or not _directory_valid(prefix_info)
            or not _directory_valid(lexical_parent)
            or not _directory_valid(lexical_prefix)
            or file_identity(parent_info) != file_identity(lexical_parent)
            or file_identity(prefix_info) != file_identity(lexical_prefix)
        ):
            raise RetainedTreeError(
                "named-child prefix must resolve to one retained real directory"
            )
        prefix_identity = file_identity(prefix_info)
        prefix_census, prefix_infos, prefix_entry_handles = _windows_direct_census(
            api,
            prefix_handle,
            context="named-child prefix",
        )
        handles.extend(prefix_entry_handles.values())
        _invoke_hook(verification_hook, "after_prefix_census")
        container_initial = _exact_named_info(
            prefix_infos,
            container_name,
            context="named-child prefix",
        )
        container_handle = prefix_entry_handles.get(container_name)
        container_identity: tuple[int, int] | None = None
        container_census: tuple[tuple[str, tuple[int, int, int, int, int, int, int, int]], ...] = ()
        if container_initial is None:
            if container_handle is not None:
                raise RetainedTreeError("named-child prefix returned inconsistent container census")
            _invoke_hook(verification_hook, "after_share_census")
        else:
            if container_handle is None or not _directory_valid(container_initial):
                raise RetainedTreeError(
                    f"named-child container is not a retained real directory: {container_name}"
                )
            container_opened = windows_handle_file_stat(container_handle)
            rebound_container = api.open_relative(
                prefix_handle,
                container_name,
                directory=True,
            )
            try:
                rebound_container_info = windows_handle_file_stat(rebound_container)
            finally:
                api.close(rebound_container)
            if (
                not _directory_valid(container_opened)
                or not _directory_valid(rebound_container_info)
                or file_identity(container_initial) != file_identity(container_opened)
                or file_identity(container_opened) != file_identity(rebound_container_info)
            ):
                raise RetainedTreeError(
                    f"named-child container changed before retention: {container_name}"
                )
            container_identity = file_identity(container_opened)
            container_census, container_infos, container_entry_handles = _windows_direct_census(
                api,
                container_handle,
                context="named-child container",
            )
            handles.extend(container_entry_handles.values())
            _invoke_hook(verification_hook, "after_share_census")
            for child_name in child_names:
                child_initial = _exact_named_info(
                    container_infos,
                    child_name,
                    context="named-child container",
                )
                child_handle = container_entry_handles.get(child_name)
                if child_initial is None:
                    if child_handle is not None:
                        raise RetainedTreeError(
                            "named-child container returned inconsistent child census"
                        )
                    continue
                if child_handle is None or not _directory_valid(child_initial):
                    raise RetainedTreeError(
                        f"named child is not a retained real directory: {child_name}"
                    )
                candidate_handles[child_name] = child_handle
                child_opened = windows_handle_file_stat(child_handle)
                rebound_child = api.open_relative(
                    container_handle,
                    child_name,
                    directory=True,
                )
                try:
                    rebound_child_info = windows_handle_file_stat(rebound_child)
                finally:
                    api.close(rebound_child)
                if (
                    not _directory_valid(child_opened)
                    or not _directory_valid(rebound_child_info)
                    or file_identity(child_initial) != file_identity(child_opened)
                    or file_identity(child_opened) != file_identity(rebound_child_info)
                ):
                    raise RetainedTreeError(f"named child changed before retention: {child_name}")
                candidate_identities[child_name] = file_identity(child_opened)
            for child_name in child_names:
                child_handle = candidate_handles.get(child_name)
                if child_handle is None:
                    continue
                _invoke_hook(
                    verification_hook,
                    "after_candidate_retained",
                    child_name,
                )
                snapshot = capture_retained_tree(
                    prefix / container_name / child_name,
                )
                _invoke_hook(
                    verification_hook,
                    "before_candidate_binding_verification",
                    child_name,
                )
                rebound_child = api.open_relative(
                    container_handle,
                    child_name,
                    directory=True,
                )
                try:
                    rebound_child_info = windows_handle_file_stat(rebound_child)
                finally:
                    api.close(rebound_child)
                retained_child = windows_handle_file_stat(child_handle)
                expected_identity = candidate_identities[child_name]
                if (
                    snapshot.root_identity != expected_identity
                    or not _directory_valid(retained_child)
                    or not _directory_valid(rebound_child_info)
                    or file_identity(retained_child) != expected_identity
                    or file_identity(rebound_child_info) != expected_identity
                ):
                    raise RetainedTreeError(
                        f"named child binding changed during retained capture: {child_name}"
                    )
                snapshots[child_name] = snapshot

        _invoke_hook(verification_hook, "before_final_prefix_verification")
        final_prefix_census, _prefix_infos, final_prefix_handles = _windows_direct_census(
            api,
            prefix_handle,
            context="named-child prefix",
        )
        api.close_many(tuple(final_prefix_handles.values()))
        retained_parent = windows_handle_file_stat(parent_handle)
        retained_prefix = windows_handle_file_stat(prefix_handle)
        rebound_prefix = api.open_relative(parent_handle, prefix.name, directory=True)
        try:
            rebound_prefix_info = windows_handle_file_stat(rebound_prefix)
        finally:
            api.close(rebound_prefix)
        lexical_parent = path_file_stat(prefix.parent)
        lexical_prefix = path_file_stat(prefix)
        if (
            final_prefix_census != prefix_census
            or not _directory_valid(retained_parent)
            or not _directory_valid(retained_prefix)
            or not _directory_valid(rebound_prefix_info)
            or not _directory_valid(lexical_parent)
            or not _directory_valid(lexical_prefix)
            or file_identity(retained_parent) != file_identity(lexical_parent)
            or file_identity(retained_prefix) != prefix_identity
            or file_identity(rebound_prefix_info) != prefix_identity
            or file_identity(lexical_prefix) != prefix_identity
        ):
            raise RetainedTreeError("named-child prefix topology changed")
        if container_handle is not None:
            assert container_identity is not None
            final_container_census, _container_infos, final_container_handles = (
                _windows_direct_census(
                    api,
                    container_handle,
                    context="named-child container",
                )
            )
            api.close_many(tuple(final_container_handles.values()))
            retained_container = windows_handle_file_stat(container_handle)
            rebound_container = api.open_relative(
                prefix_handle,
                container_name,
                directory=True,
            )
            try:
                rebound_container_info = windows_handle_file_stat(rebound_container)
            finally:
                api.close(rebound_container)
            if (
                final_container_census != container_census
                or not _directory_valid(retained_container)
                or not _directory_valid(rebound_container_info)
                or file_identity(retained_container) != container_identity
                or file_identity(rebound_container_info) != container_identity
            ):
                raise RetainedTreeError("named-child container topology changed")
            for child_name, child_handle in candidate_handles.items():
                retained_child = windows_handle_file_stat(child_handle)
                rebound_child = api.open_relative(
                    container_handle,
                    child_name,
                    directory=True,
                )
                try:
                    rebound_child_info = windows_handle_file_stat(rebound_child)
                finally:
                    api.close(rebound_child)
                expected_identity = candidate_identities[child_name]
                if (
                    not _directory_valid(retained_child)
                    or not _directory_valid(rebound_child_info)
                    or file_identity(retained_child) != expected_identity
                    or file_identity(rebound_child_info) != expected_identity
                ):
                    raise RetainedTreeError(
                        f"named child binding changed before return: {child_name}"
                    )
        _invoke_hook(verification_hook, "after_final_prefix_verification")
        return snapshots
    except RetainedTreeError:
        raise
    except OSError as exc:
        raise RetainedTreeError(
            f"could not retain Windows named children below {prefix}: {exc}"
        ) from exc
    finally:
        api.close_many(handles)


def capture_retained_tree(
    root: str | Path,
    *,
    exclude_directory: DirectoryExclusion | None = None,
    verification_hook: RetainedTreeHook | None = None,
) -> RetainedTreeSnapshot:
    """Capture exact tree topology and file bytes through retained identities."""

    source = Path(os.path.abspath(os.fspath(root)))
    if not source.name:
        raise RetainedTreeError("filesystem root cannot be used as a retained tree root")
    if os.name == "nt":
        return _capture_windows(
            source,
            exclude_directory=exclude_directory,
            verification_hook=verification_hook,
        )
    return _capture_posix(
        source,
        exclude_directory=exclude_directory,
        verification_hook=verification_hook,
    )


def verify_retained_tree_snapshot(
    root: str | Path,
    expected: RetainedTreeSnapshot,
    *,
    verification_hook: RetainedTreeHook | None = None,
) -> None:
    """Verify one exact snapshot without reading beyond its trusted inventory."""

    source = Path(os.path.abspath(os.fspath(root)))
    if not source.name:
        raise RetainedTreeError("filesystem root cannot be used as a retained tree root")
    if os.name == "nt":
        actual = _capture_windows(
            source,
            exclude_directory=None,
            verification_hook=verification_hook,
            expected=expected,
        )
    else:
        actual = _capture_posix(
            source,
            exclude_directory=None,
            verification_hook=verification_hook,
            expected=expected,
        )
    if actual != expected:
        raise RetainedTreeError("retained tree snapshot changed")


def capture_retained_directory_file_census(
    root: str | Path,
    *,
    maximum_entries: int,
    authority_root: str | Path | None = None,
    expected_authority_identity: tuple[int, int] | None = None,
    verification_hook: RetainedTreeHook | None = None,
) -> RetainedDirectoryFileCensus:
    """Capture an exact bounded direct-file census through retained ancestry.

    Every observed entry is lstat/handle-bound, portable, collision-free, a
    standalone regular file, and revalidated before a snapshot is returned.
    The first entry beyond ``maximum_entries`` is validated and then fails
    closed without returning a partial census.
    """

    maximum = _validated_census_capacity(maximum_entries)
    source = Path(os.path.abspath(os.fspath(root)))
    if not source.name:
        raise RetainedTreeError("filesystem root cannot be used as a retained census root")
    authority = _validated_census_authority(
        source,
        authority_root,
        expected_authority_identity,
    )
    if os.name == "nt":
        return _capture_windows_directory_file_census(
            source,
            maximum_entries=maximum,
            authority=authority,
            verification_hook=verification_hook,
        )
    if os.name != "posix":
        raise RetainedTreeError("safe retained directory census primitives are unavailable")
    return _capture_posix_directory_file_census(
        source,
        maximum_entries=maximum,
        authority=authority,
        verification_hook=verification_hook,
    )


def capture_retained_named_child_trees(
    prefix: str | Path,
    *,
    container_name: str,
    child_names: Sequence[str],
    verification_hook: RetainedTreeHook | None = None,
) -> dict[str, RetainedTreeSnapshot | None]:
    """Capture named child trees from one retained prefix/container census."""

    source = Path(os.path.abspath(os.fspath(prefix)))
    if not source.name:
        raise RetainedTreeError("filesystem root cannot be a named-child prefix")
    container, children = _validate_named_components(container_name, child_names)
    if os.name == "nt":
        return _capture_named_windows(
            source,
            container_name=container,
            child_names=children,
            verification_hook=verification_hook,
        )
    return _capture_named_posix(
        source,
        container_name=container,
        child_names=children,
        verification_hook=verification_hook,
    )


__all__ = [
    "DirectoryExclusion",
    "RetainedDirectoryFileCensus",
    "RetainedTreeError",
    "RetainedTreeCapacityError",
    "RetainedTreeHook",
    "RetainedTreeSnapshot",
    "capture_retained_directory_file_census",
    "capture_retained_named_child_trees",
    "capture_retained_tree",
    "verify_retained_tree_snapshot",
]
