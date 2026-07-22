from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from isoworld.content.file_stat import FileStat, descriptor_file_stat, path_file_stat
from isoworld.content.portability import portable_relative_path

MAX_JOB_FILE_BYTES = 64 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 10_000
_READ_CHUNK_BYTES = 1024 * 1024


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
    try:
        info = path_file_stat(path)
    except OSError as exc:
        raise JobPathError("registered world root is unavailable") from exc
    if (
        _is_link_or_reparse(info)
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != expected_identity
    ):
        raise JobPathError("registered world root identity changed")


def _entry(current: Path, component: str) -> tuple[Path, FileStat]:
    target_key = unicodedata.normalize("NFC", component).casefold()
    matches: list[str] = []
    exact: FileStat | None = None
    try:
        with os.scandir(current) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_DIRECTORY_ENTRIES:
                    raise JobPathError("workspace directory exceeds the managed scan bound")
                key = unicodedata.normalize("NFC", entry.name).casefold()
                if key != target_key:
                    continue
                matches.append(entry.name)
                if entry.name == component:
                    exact = entry.stat(follow_symlinks=False)
    except JobPathError:
        raise
    except OSError as exc:
        raise JobPathError("workspace path parent is unavailable") from exc
    if len(matches) != 1 or matches[0] != component or exact is None:
        raise JobPathError("workspace path has an NFC/casefold collision or mismatch")
    return current / component, exact


def _digest_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, _READ_CHUNK_BYTES)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


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
    current = world_root
    for component in relative.parts[:-1]:
        current, info = _entry(current, component)
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise JobPathError("managed job path parent is not a plain directory")
    path, path_before = _entry(current, relative.name)
    if (
        _is_link_or_reparse(path_before)
        or not stat.S_ISREG(path_before.st_mode)
        or path_before.st_nlink != 1
    ):
        raise JobPathError("managed job input must be a standalone regular file")
    if path_before.st_size > limit:
        raise JobPathError("managed job input exceeds the file-size bound")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = descriptor_file_stat(descriptor)
        if (
            _is_link_or_reparse(before)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > limit
            or not _same_state(path_before, before)
        ):
            raise JobPathError("managed job input identity changed before reading")
        first = _digest_descriptor(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        second = _digest_descriptor(descriptor)
        after = descriptor_file_stat(descriptor)
        path_after = path_file_stat(path)
        if first != second or not _same_state(before, after) or not _same_state(before, path_after):
            raise JobPathError("managed job input changed while reading")
        verify_root(world_root, world_identity)
        return JobFileProof(
            relative=relative.as_posix(),
            device=before.st_dev,
            inode=before.st_ino,
            size=before.st_size,
            sha256=first,
        )
    except JobPathError:
        raise
    except OSError as exc:
        raise JobPathError("managed job input could not be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


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
