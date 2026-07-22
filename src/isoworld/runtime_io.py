from __future__ import annotations

import json
import math
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from isoworld.content.file_stat import FileStat, descriptor_file_stat, path_file_stat

MAX_JSON_BYTES = 16 * 1024 * 1024
_DIR_FD_PUBLICATION = os.name == "posix" and all(
    function in os.supports_dir_fd for function in (os.open, os.rename, os.stat)
)


class RuntimeIOError(ValueError):
    """Raised when runtime I/O violates its bounded file contract."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def decode_json_object(payload: bytes, *, source: str | Path) -> dict[str, Any]:
    """Decode strict UTF-8 JSON bytes while requiring an object root."""

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeIOError(f"Could not read {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeIOError(f"{source} must contain a JSON object")
    return value


def read_json_object(
    path: str | Path,
    *,
    limit: int = MAX_JSON_BYTES,
) -> dict[str, Any]:
    """Read one bounded UTF-8 JSON object without accepting ambiguous numbers or keys."""

    source = Path(path)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("not a standalone regular file")
        if info.st_size > limit:
            raise OSError(f"exceeds the {limit}-byte limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(limit + 1)
        if len(payload) > limit:
            raise OSError(f"exceeds the {limit}-byte limit")
    except OSError as exc:
        raise RuntimeIOError(f"Could not read {source}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return decode_json_object(payload, source=source)


def _encode_json(value: object) -> bytes:
    try:
        document = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeIOError(f"Could not encode strict JSON: {exc}") from exc
    return (document + "\n").encode("utf-8")


def _prepare_output_path(path: str | Path) -> Path:
    """Create output parents without traversing a symbolic-link directory."""

    destination = Path(os.path.abspath(Path(path)))
    parent = destination.parent
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir()
            except FileExistsError:
                pass
            try:
                info = current.lstat()
            except OSError as exc:
                raise RuntimeIOError(f"Could not verify output parent {current}: {exc}") from exc
        except OSError as exc:
            raise RuntimeIOError(f"Could not verify output parent {current}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeIOError(f"Output parent is not a safe directory: {current}")
    return destination


def _safe_directory_identity(path: Path) -> tuple[int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeIOError(f"Could not verify output parent {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeIOError(f"Output parent is not a safe directory: {path}")
    return info.st_dev, info.st_ino


def _verify_parent_identity(path: Path, expected: tuple[int, int]) -> None:
    if _safe_directory_identity(path) != expected:
        raise RuntimeIOError(f"Output parent changed during publication: {path}")


@contextmanager
def _open_verified_output_parent(path: Path) -> Iterator[tuple[int | None, tuple[int, int]]]:
    """Pin POSIX parents and revalidate the requested path on every platform."""

    expected = _safe_directory_identity(path)
    if not _DIR_FD_PUBLICATION:
        _verify_parent_identity(path, expected)
        try:
            yield None, expected
        finally:
            _verify_parent_identity(path, expected)
        return

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeIOError(f"Could not pin output parent {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != expected:
            raise RuntimeIOError(f"Output parent changed during publication: {path}")
        _verify_parent_identity(path, expected)
        try:
            yield descriptor, expected
        finally:
            _verify_parent_identity(path, expected)
    finally:
        os.close(descriptor)


def _entry_info(parent_fd: int | None, parent: Path, name: str) -> FileStat | None:
    try:
        if parent_fd is not None:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return path_file_stat(parent / name)
    except FileNotFoundError:
        return None


def _entry_identity(info: FileStat) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _validated_target_identity(
    info: FileStat | None,
    destination: Path,
) -> tuple[int, int] | None:
    if info is None:
        return None
    if _is_link_or_reparse(info):
        raise RuntimeIOError(f"Refusing to replace symbolic link {destination}")
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeIOError(f"Refusing to replace non-regular file {destination}")
    if info.st_nlink != 1:
        raise RuntimeIOError(f"Refusing to replace hard-linked file {destination}")
    return _entry_identity(info)


def _create_temporary_entry(
    parent_fd: int | None,
    parent: Path,
    prefix: str,
    parent_identity: tuple[int, int],
) -> tuple[int, str]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _ in range(100):
        _verify_parent_identity(parent, parent_identity)
        name = f"{prefix}{secrets.token_hex(16)}"
        try:
            if parent_fd is not None:
                descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
            else:
                descriptor = os.open(parent / name, flags, 0o600)
        except FileExistsError:
            continue
        except OSError as exc:
            raise RuntimeIOError(f"Could not create a temporary output in {parent}: {exc}") from exc
        try:
            _verify_parent_identity(parent, parent_identity)
        except RuntimeIOError:
            os.close(descriptor)
            # Portable Python has no inode-conditional unlink. Retain the empty entry
            # rather than risk deleting a foreign replacement after a parent swap.
            raise
        return descriptor, name
    raise RuntimeIOError(f"Could not allocate a temporary output in {parent}")


def _write_all(target: BinaryIO, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = target.write(remaining)
        if written is None or written <= 0:
            raise OSError("short write while publishing runtime JSON")
        remaining = remaining[written:]
    target.flush()
    os.fsync(target.fileno())


def _verify_owned_entry(
    parent_fd: int | None,
    parent: Path,
    name: str,
    identity: tuple[int, int],
) -> None:
    try:
        info = _entry_info(parent_fd, parent, name)
    except OSError as exc:
        raise RuntimeIOError(f"Could not verify temporary output {parent / name}: {exc}") from exc
    if (
        info is None
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or _entry_identity(info) != identity
    ):
        raise RuntimeIOError(f"Temporary output changed before publication: {parent / name}")


def _verify_lock_entry(
    descriptor: int,
    parent_fd: int | None,
    parent: Path,
    name: str,
    identity: tuple[int, int],
    parent_identity: tuple[int, int],
) -> None:
    _verify_parent_identity(parent, parent_identity)
    opened = descriptor_file_stat(descriptor)
    current = _entry_info(parent_fd, parent, name)
    if current is not None:
        _validated_target_identity(current, parent / name)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_nlink != 1
        or opened.st_size != 1
        or current is None
        or not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or _entry_identity(opened) != identity
        or _entry_identity(current) != identity
    ):
        raise RuntimeIOError(f"Persistence lock changed or is unsafe: {parent / name}")


def _open_lock_entry(
    parent_fd: int | None,
    parent: Path,
    name: str,
    parent_identity: tuple[int, int],
) -> tuple[int, tuple[int, int]]:
    _verify_parent_identity(parent, parent_identity)
    existing = _entry_info(parent_fd, parent, name)
    if existing is not None:
        _validated_target_identity(existing, parent / name)
    common = (
        os.O_RDWR
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    created = False
    try:
        if existing is None:
            try:
                if parent_fd is not None:
                    descriptor = os.open(
                        name,
                        common | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=parent_fd,
                    )
                else:
                    descriptor = os.open(parent / name, common | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                existing = _entry_info(parent_fd, parent, name)
                if existing is None:
                    raise RuntimeIOError(
                        f"Persistence lock changed during open: {parent / name}"
                    ) from None
                _validated_target_identity(existing, parent / name)
                if parent_fd is not None:
                    descriptor = os.open(name, common, dir_fd=parent_fd)
                else:
                    descriptor = os.open(parent / name, common)
        elif parent_fd is not None:
            descriptor = os.open(name, common, dir_fd=parent_fd)
        else:
            descriptor = os.open(parent / name, common)
    except RuntimeIOError:
        raise
    except OSError as exc:
        raise RuntimeIOError(f"Could not open persistence lock {parent / name}: {exc}") from exc

    try:
        info = descriptor_file_stat(descriptor)
        identity = _entry_identity(info)
        current = _entry_info(parent_fd, parent, name)
        if current is not None:
            _validated_target_identity(current, parent / name)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or current is None
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or _entry_identity(current) != identity
        ):
            raise RuntimeIOError(f"Persistence lock changed or is unsafe: {parent / name}")
        if info.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.write(descriptor, b"\0") != 1:
                raise OSError("short write while initializing persistence lock")
            os.fsync(descriptor)
        elif info.st_size != 1:
            raise RuntimeIOError(f"Persistence lock has invalid contents: {parent / name}")
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, 1) != b"\0":
            raise RuntimeIOError(f"Persistence lock has invalid contents: {parent / name}")
        if created:
            _fsync_parent(parent_fd)
        _verify_lock_entry(
            descriptor,
            parent_fd,
            parent,
            name,
            identity,
            parent_identity,
        )
        return descriptor, identity
    except Exception:
        os.close(descriptor)
        raise


def _acquire_os_lock(descriptor: int) -> None:
    try:
        if os.name == "nt":  # pragma: no cover - exercised by Windows CI
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise RuntimeIOError("Another writer is updating the persistence document") from exc


@contextmanager
def _held_destination_lock(
    destination: Path,
    parent_fd: int | None,
    parent_identity: tuple[int, int],
) -> Iterator[tuple[int, tuple[int, int]]]:
    name = f".{destination.name}.lock"
    descriptor, identity = _open_lock_entry(
        parent_fd,
        destination.parent,
        name,
        parent_identity,
    )
    try:
        _acquire_os_lock(descriptor)
        _verify_lock_entry(
            descriptor,
            parent_fd,
            destination.parent,
            name,
            identity,
            parent_identity,
        )
        yield descriptor, identity
    finally:
        # Closing the descriptor releases flock/LockFile locks even after a process crash.
        os.close(descriptor)


def _replace_entry(
    parent_fd: int | None,
    parent: Path,
    temporary_name: str,
    destination_name: str,
    parent_identity: tuple[int, int],
) -> None:
    _verify_parent_identity(parent, parent_identity)
    if parent_fd is not None:
        os.rename(
            temporary_name,
            destination_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
        return
    # Windows has no stdlib renameat equivalent. Identity checks bracket os.replace;
    # any detected path swap fails closed and is never reported as a successful save.
    _verify_parent_identity(parent, parent_identity)
    os.replace(parent / temporary_name, parent / destination_name)
    _verify_parent_identity(parent, parent_identity)


def _fsync_parent(parent_fd: int | None) -> None:
    # Windows has no portable directory-fsync primitive; POSIX uses the pinned dir fd.
    if parent_fd is not None:
        os.fsync(parent_fd)


def write_json_atomic(path: str | Path, value: object) -> None:
    """Atomically replace runtime JSON while failing closed on identity changes.

    A safe persistent sidecar carries an OS-managed lock; descriptor closure releases
    it after normal exit or a crash. Failed temporary entries are intentionally retained:
    portable Python has no inode-conditional unlink, so deletion by name could remove a
    foreign file swapped into that name during cleanup.
    """

    payload = _encode_json(value)
    requested_destination = Path(path)
    destination = _prepare_output_path(requested_destination)
    try:
        with _open_verified_output_parent(destination.parent) as (parent_fd, parent_identity):
            _verify_parent_identity(destination.parent, parent_identity)
            initial = _validated_target_identity(
                _entry_info(parent_fd, destination.parent, destination.name),
                requested_destination,
            )
            with _held_destination_lock(
                destination,
                parent_fd,
                parent_identity,
            ) as (lock_descriptor, lock_identity):
                _verify_parent_identity(destination.parent, parent_identity)
                current = _validated_target_identity(
                    _entry_info(parent_fd, destination.parent, destination.name),
                    requested_destination,
                )
                if current != initial:
                    raise RuntimeIOError(
                        f"Output changed before publication: {requested_destination}"
                    )
                descriptor, temporary_name = _create_temporary_entry(
                    parent_fd,
                    destination.parent,
                    f".{destination.name}.tmp.",
                    parent_identity,
                )
                temporary_identity: tuple[int, int] | None = None
                descriptor_owned = True
                try:
                    temporary_info = descriptor_file_stat(descriptor)
                    temporary_identity = _entry_identity(temporary_info)
                    if not stat.S_ISREG(temporary_info.st_mode) or temporary_info.st_nlink != 1:
                        raise RuntimeIOError("Temporary output is not a standalone regular file")
                    with os.fdopen(descriptor, "wb", buffering=0) as target:
                        descriptor_owned = False
                        _write_all(target, payload)
                    _verify_parent_identity(destination.parent, parent_identity)
                    _verify_lock_entry(
                        lock_descriptor,
                        parent_fd,
                        destination.parent,
                        f".{destination.name}.lock",
                        lock_identity,
                        parent_identity,
                    )
                    current = _validated_target_identity(
                        _entry_info(parent_fd, destination.parent, destination.name),
                        requested_destination,
                    )
                    if current != initial:
                        raise RuntimeIOError(
                            f"Output changed before publication: {requested_destination}"
                        )
                    _verify_owned_entry(
                        parent_fd,
                        destination.parent,
                        temporary_name,
                        temporary_identity,
                    )
                    _replace_entry(
                        parent_fd,
                        destination.parent,
                        temporary_name,
                        destination.name,
                        parent_identity,
                    )
                    _verify_parent_identity(destination.parent, parent_identity)
                    _verify_owned_entry(
                        parent_fd,
                        destination.parent,
                        destination.name,
                        temporary_identity,
                    )
                    _verify_lock_entry(
                        lock_descriptor,
                        parent_fd,
                        destination.parent,
                        f".{destination.name}.lock",
                        lock_identity,
                        parent_identity,
                    )
                    _fsync_parent(parent_fd)
                    _verify_parent_identity(destination.parent, parent_identity)
                    _verify_owned_entry(
                        parent_fd,
                        destination.parent,
                        destination.name,
                        temporary_identity,
                    )
                    _verify_lock_entry(
                        lock_descriptor,
                        parent_fd,
                        destination.parent,
                        f".{destination.name}.lock",
                        lock_identity,
                        parent_identity,
                    )
                finally:
                    if descriptor_owned:
                        os.close(descriptor)
                    # Never unlink by pathname here. Before publication, a failed or
                    # foreign-swapped temporary remains for explicit recovery; after
                    # publication the temporary name no longer exists.
    except RuntimeIOError:
        raise
    except OSError as exc:
        raise RuntimeIOError(f"Could not write {requested_destination}: {exc}") from exc
