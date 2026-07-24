from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from isoworld.content.file_stat import FileStat, descriptor_file_stat, path_file_stat
from isoworld.content.portability import portable_relative_path
from worldforge.studio.workspaces import (
    _close_descriptors,
    _close_windows_handles,
    _open_posix_ancestry,
    _open_windows_ancestry,
    _WindowsRelativeDirectoryApi,
)

MAX_JOB_FILE_BYTES = 64 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 10_000
_READ_CHUNK_BYTES = 1024 * 1024
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_POSIX_PINNED_DIRECTORY_IO = (
    os.name == "posix"
    and all(function in os.supports_dir_fd for function in (os.open, os.stat))
    and os.scandir in os.supports_fd
)


class JobPathError(ValueError):
    """A managed job path no longer satisfies the registered workspace boundary."""


@dataclass(frozen=True, slots=True)
class JobFileProof:
    relative: str
    device: int
    inode: int
    size: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "relative": self.relative,
            "identity": [self.device, self.inode],
            "size": self.size,
            "sha256": self.sha256,
        }


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _same_state(left: FileStat, right: FileStat) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_nlink,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_nlink,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def verify_root(path: Path, expected_identity: tuple[int, int]) -> None:
    if "\x00" in str(path) or not path.is_absolute():
        raise JobPathError("registered world root is unavailable")
    if os.name == "posix":
        if not _POSIX_PINNED_DIRECTORY_IO:
            raise JobPathError("secure POSIX managed job traversal is unavailable")
        descriptors: list[int] = []
        try:
            descriptors, identities = _open_posix_ancestry(
                path,
                context="registered world root",
            )
            if identities[-1] != expected_identity:
                raise JobPathError("registered world root identity changed")
        except JobPathError:
            raise
        except (OSError, ValueError) as exc:
            raise JobPathError("registered world root is unavailable") from exc
        finally:
            try:
                _close_descriptors(descriptors)
            except OSError as exc:
                raise JobPathError("registered world root handles could not be released") from exc
        return
    if os.name == "nt":
        handles: list[int] = []
        api: _WindowsRelativeDirectoryApi | None = None
        try:
            api = _WindowsRelativeDirectoryApi()
            handles, identities = _open_windows_ancestry(
                api,
                path,
                context="registered world root",
            )
            if identities[-1] != expected_identity:
                raise JobPathError("registered world root identity changed")
        except JobPathError:
            raise
        except (OSError, ValueError) as exc:
            raise JobPathError("registered world root is unavailable") from exc
        finally:
            if api is not None:
                try:
                    _close_windows_handles(api, handles)
                except OSError as exc:
                    raise JobPathError(
                        "registered world root handles could not be released"
                    ) from exc
        return
    raise JobPathError("secure managed job traversal is unsupported")


def _matched_name(names: Iterable[str], component: str) -> str:
    target_key = unicodedata.normalize("NFC", component).casefold()
    matches: list[str] = []
    for index, name in enumerate(names):
        if index >= MAX_DIRECTORY_ENTRIES:
            raise JobPathError("workspace directory exceeds the managed scan bound")
        key = unicodedata.normalize("NFC", name).casefold()
        if key == target_key:
            matches.append(name)
    if len(matches) != 1 or matches[0] != component:
        raise JobPathError("workspace path has an NFC/casefold collision or mismatch")
    return component


def _entry(current: Path, component: str) -> tuple[Path, FileStat]:
    try:
        with os.scandir(current) as entries:
            _matched_name((entry.name for entry in entries), component)
    except JobPathError:
        raise
    except (OSError, ValueError) as exc:
        raise JobPathError("workspace path parent is unavailable") from exc
    path = current / component
    try:
        exact = path_file_stat(path)
    except (OSError, ValueError) as exc:
        raise JobPathError("workspace path is unavailable") from exc
    return path, exact


def _descriptor_entry(descriptor: int, component: str) -> FileStat:
    try:
        with os.scandir(descriptor) as entries:
            name = _matched_name((entry.name for entry in entries), component)
        return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    except JobPathError:
        raise
    except (OSError, ValueError) as exc:
        raise JobPathError("workspace path is unavailable") from exc


def _digest_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, _READ_CHUNK_BYTES)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def _validate_directory(info: FileStat) -> tuple[int, int]:
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise JobPathError("managed job path parent is not a plain directory")
    return info.st_dev, info.st_ino


def _validate_file(info: FileStat, *, limit: int) -> None:
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise JobPathError("managed job input must be a standalone regular file")
    if info.st_size > limit:
        raise JobPathError("managed job input exceeds the file-size bound")


def _read_proof(
    descriptor: int,
    path_before: FileStat,
    path_after: Callable[[], FileStat],
    *,
    relative: PurePosixPath,
    limit: int,
) -> JobFileProof:
    before = descriptor_file_stat(descriptor)
    _validate_file(before, limit=limit)
    if not _same_state(path_before, before):
        raise JobPathError("managed job input identity changed before reading")
    first = _digest_descriptor(descriptor)
    os.lseek(descriptor, 0, os.SEEK_SET)
    second = _digest_descriptor(descriptor)
    after = descriptor_file_stat(descriptor)
    try:
        visible_after = path_after()
    except (OSError, ValueError) as exc:
        raise JobPathError("managed job input changed while reading") from exc
    if first != second or not _same_state(before, after) or not _same_state(before, visible_after):
        raise JobPathError("managed job input changed while reading")
    return JobFileProof(
        relative=relative.as_posix(),
        device=before.st_dev,
        inode=before.st_ino,
        size=before.st_size,
        sha256=first,
    )


def _verify_workspace_file_posix(
    world_root: Path,
    relative: PurePosixPath,
    *,
    world_identity: tuple[int, int],
    limit: int,
) -> JobFileProof:
    if not _POSIX_PINNED_DIRECTORY_IO:
        raise JobPathError("secure POSIX managed job traversal is unavailable")
    descriptors: list[int] = []
    file_descriptor: int | None = None
    identities: list[tuple[int, int]] = []
    try:
        descriptors, root_identities = _open_posix_ancestry(
            world_root,
            context="managed job world root",
        )
        identities.extend(root_identities)
        if identities[-1] != world_identity:
            raise JobPathError("registered world root identity changed")
        current = world_root
        current_descriptor = descriptors[-1]
        for component in relative.parts[:-1]:
            path_before = _descriptor_entry(current_descriptor, component)
            expected = _validate_directory(path_before)
            child_descriptor = os.open(
                component,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=current_descriptor,
            )
            descriptors.append(child_descriptor)
            opened = descriptor_file_stat(child_descriptor)
            if _validate_directory(opened) != expected:
                raise JobPathError("managed job path parent identity changed")
            current /= component
            identities.append(expected)
            current_descriptor = child_descriptor
        path_before = _descriptor_entry(current_descriptor, relative.name)
        _validate_file(path_before, limit=limit)
        file_descriptor = os.open(
            relative.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=current_descriptor,
        )
        proof = _read_proof(
            file_descriptor,
            path_before,
            lambda: os.stat(
                relative.name,
                dir_fd=current_descriptor,
                follow_symlinks=False,
            ),
            relative=relative,
            limit=limit,
        )
        verification, visible_identities = _open_posix_ancestry(
            current,
            context="managed job visible ancestry",
        )
        try:
            if visible_identities != tuple(identities):
                raise JobPathError("managed job directory ancestry changed")
        finally:
            _close_descriptors(verification)
        return proof
    except JobPathError:
        raise
    except (OSError, ValueError) as exc:
        raise JobPathError("managed job input could not be read safely") from exc
    finally:
        close_errors: list[OSError] = []
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError as exc:
                close_errors.append(exc)
        try:
            _close_descriptors(descriptors)
        except OSError as exc:
            close_errors.append(exc)
        if close_errors:
            raise JobPathError("managed job descriptors could not be released") from close_errors[0]


def _verify_workspace_file_windows(
    world_root: Path,
    relative: PurePosixPath,
    *,
    world_identity: tuple[int, int],
    limit: int,
) -> JobFileProof:
    handles: list[int] = []
    file_descriptor: int | None = None
    api: _WindowsRelativeDirectoryApi | None = None
    identities: list[tuple[int, int]] = []
    try:
        api = _WindowsRelativeDirectoryApi()
        handles, root_identities = _open_windows_ancestry(
            api,
            world_root,
            context="managed job world root",
        )
        identities.extend(root_identities)
        if identities[-1] != world_identity:
            raise JobPathError("registered world root identity changed")
        current = world_root
        for component in relative.parts[:-1]:
            child, before = _entry(current, component)
            expected = _validate_directory(before)
            handle = api.open_relative(
                handles[-1],
                component,
                context=f"managed job path component {child}",
                directory=True,
            )
            handles.append(handle)
            opened = api.state(
                handle,
                context=f"managed job path component {child}",
                directory=True,
            )
            if _validate_directory(opened) != expected:
                raise JobPathError("managed job path parent identity changed")
            current = child
            identities.append(expected)
        path, path_before = _entry(current, relative.name)
        _validate_file(path_before, limit=limit)
        file_handle = api.open_relative(
            handles[-1],
            relative.name,
            context=f"managed job input {path}",
            directory=False,
        )
        try:
            import msvcrt

            file_descriptor = msvcrt.open_osfhandle(
                file_handle,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except BaseException:
            api.close(file_handle)
            raise
        proof = _read_proof(
            file_descriptor,
            path_before,
            lambda: path_file_stat(path),
            relative=relative,
            limit=limit,
        )
        verification, visible_identities = _open_windows_ancestry(
            api,
            current,
            context="managed job visible ancestry",
        )
        try:
            if visible_identities != tuple(identities):
                raise JobPathError("managed job directory ancestry changed")
        finally:
            _close_windows_handles(api, verification)
        return proof
    except JobPathError:
        raise
    except (OSError, ValueError) as exc:
        raise JobPathError("managed job input could not be read safely") from exc
    finally:
        close_errors: list[OSError] = []
        if file_descriptor is not None:
            try:
                os.close(file_descriptor)
            except OSError as exc:
                close_errors.append(exc)
        if api is not None:
            try:
                _close_windows_handles(api, handles)
            except OSError as exc:
                close_errors.append(exc)
        if close_errors:
            raise JobPathError("managed job handles could not be released") from close_errors[0]


def verify_workspace_file(
    world_root: Path,
    relative: PurePosixPath,
    *,
    world_identity: tuple[int, int],
    limit: int = MAX_JOB_FILE_BYTES,
) -> JobFileProof:
    """Verify and hash one portable standalone file without following path links."""

    if portable_relative_path(relative.as_posix()) != relative:
        raise JobPathError("managed job path is not portable")
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_JOB_FILE_BYTES
    ):
        raise ValueError("managed job file limit is invalid")
    verify_root(world_root, world_identity)
    if os.name == "posix":
        return _verify_workspace_file_posix(
            world_root,
            relative,
            world_identity=world_identity,
            limit=limit,
        )
    if os.name == "nt":
        return _verify_workspace_file_windows(
            world_root,
            relative,
            world_identity=world_identity,
            limit=limit,
        )
    raise JobPathError("secure managed job traversal is unsupported")


def proof_matches(proof: JobFileProof, expected: object) -> bool:
    if not isinstance(expected, dict) or set(expected) != {
        "relative",
        "identity",
        "size",
        "sha256",
    }:
        return False
    identity = expected.get("identity")
    return (
        expected.get("relative") == proof.relative
        and isinstance(identity, list)
        and len(identity) == 2
        and identity == [proof.device, proof.inode]
        and expected.get("size") == proof.size
        and expected.get("sha256") == proof.sha256
    )
