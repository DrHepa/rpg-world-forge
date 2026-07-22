from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.file_stat import FileStat, descriptor_file_stat, path_file_stat
from isoworld.content.portability import is_portable_path_component
from worldforge.integrity import canonical_payload_hash

MAX_CONTRACT_BYTES = 16 * 1024 * 1024
MAX_ASSET_BYTES = 512 * 1024 * 1024


class AssetContractError(ValueError):
    """Raised when an M5 authoring artifact violates its safe-file contract."""


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


def read_json_object(path: str | Path, *, limit: int = MAX_CONTRACT_BYTES) -> dict[str, Any]:
    source = Path(path)
    descriptor: int | None = None
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
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
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AssetContractError(f"Could not read {source}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise AssetContractError(f"{source} must contain a JSON object")
    return value


def normalized_relative_path(value: object) -> PurePosixPath | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or not relative.parts
        or any(not is_portable_path_component(part) for part in relative.parts)
    ):
        return None
    return relative


def resolve_artifact(
    root: str | Path,
    relative: object,
    *,
    required: bool = True,
    max_bytes: int = MAX_ASSET_BYTES,
) -> Path | None:
    """Resolve one portable, non-linked artifact beneath ``root``.

    Every existing parent and the file itself are checked without following a
    symbolic link. Hard-linked files are rejected so a later mutation outside
    the production tree cannot silently change a hash-bound artifact.
    """

    normalized = normalized_relative_path(relative)
    if normalized is None:
        if required:
            raise AssetContractError(f"Unsafe artifact path: {relative!r}")
        return None
    base = Path(root).resolve()
    current = base
    for part in normalized.parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            if required:
                raise AssetContractError(f"Artifact parent is missing: {relative}") from None
            return None
        if not stat.S_ISDIR(info.st_mode) or current.is_symlink():
            raise AssetContractError(f"Artifact parent is not a safe directory: {relative}")
    target = current / normalized.parts[-1]
    try:
        info = target.lstat()
    except OSError:
        if required:
            raise AssetContractError(f"Artifact is missing: {relative}") from None
        return None
    if target.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AssetContractError(f"Artifact is not a standalone regular file: {relative}")
    if info.st_size > max_bytes:
        raise AssetContractError(f"Artifact exceeds the {max_bytes}-byte limit: {relative}")
    return target


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_reference(root: str | Path, relative: str) -> dict[str, Any]:
    path = resolve_artifact(root, relative)
    assert path is not None
    return {"file": relative, "sha256": sha256_file(path)}


def verify_artifact_reference(
    root: str | Path,
    reference: object,
    *,
    context: str,
    allowed_extra: frozenset[str] = frozenset(),
) -> Path:
    if not isinstance(reference, dict):
        raise AssetContractError(f"{context} must be an artifact reference")
    unknown = set(reference) - {"file", "sha256", "size"} - allowed_extra
    if unknown:
        raise AssetContractError(f"{context} contains unknown fields: {', '.join(sorted(unknown))}")
    path = resolve_artifact(root, reference.get("file"))
    assert path is not None
    expected = reference.get("sha256")
    actual = sha256_file(path)
    if not isinstance(expected, str) or expected != actual:
        raise AssetContractError(f"{context} SHA-256 does not match {reference.get('file')}")
    size = reference.get("size")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
        raise AssetContractError(f"{context} size must be a non-negative integer")
    if isinstance(size, int) and size != path.stat().st_size:
        raise AssetContractError(f"{context} size does not match {reference.get('file')}")
    return path


def encoded_json(value: object) -> bytes:
    try:
        document = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise AssetContractError(f"Could not encode strict JSON: {exc}") from exc
    return (document + "\n").encode("utf-8")


def prepare_output_path(path: str | Path) -> Path:
    """Create and verify output parents without accepting a symbolic-link hop."""

    absolute = Path(os.path.abspath(Path(path)))
    parent = absolute.parent
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
                raise AssetContractError(
                    f"Could not verify output parent {current}: {exc}"
                ) from exc
        except OSError as exc:
            raise AssetContractError(f"Could not verify output parent {current}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise AssetContractError(f"Output parent is not a safe directory: {current}")
    return absolute


def _unlink_owned_file(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path.lstat()
    except OSError:
        return
    if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
        try:
            path.unlink()
        except OSError:
            pass


_DIR_FD_PUBLICATION = os.name == "posix" and all(
    function in os.supports_dir_fd for function in (os.open, os.link, os.rename, os.stat, os.unlink)
)


def _safe_directory_identity(path: Path) -> tuple[int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise AssetContractError(f"Could not verify output parent {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise AssetContractError(f"Output parent is not a safe directory: {path}")
    return info.st_dev, info.st_ino


@contextmanager
def _open_verified_output_parent(path: Path) -> Iterator[tuple[int | None, tuple[int, int]]]:
    """Pin a checked output parent so later path replacement cannot redirect writes."""

    expected = _safe_directory_identity(path)
    if not _DIR_FD_PUBLICATION:
        yield None, expected
        if _safe_directory_identity(path) != expected:
            raise AssetContractError(f"Output parent changed during publication: {path}")
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
        raise AssetContractError(f"Could not pin output parent {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != expected:
            raise AssetContractError(f"Output parent changed during publication: {path}")
        if _safe_directory_identity(path) != expected:
            raise AssetContractError(f"Output parent changed during publication: {path}")
        yield descriptor, expected
    finally:
        os.close(descriptor)


def _entry_info(parent_fd: int | None, parent: Path, name: str) -> FileStat | None:
    try:
        if parent_fd is not None:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return path_file_stat(parent / name)
    except FileNotFoundError:
        return None


def _unlink_owned_entry(
    parent_fd: int | None,
    parent: Path,
    name: str,
    identity: tuple[int, int],
) -> None:
    try:
        info = _entry_info(parent_fd, parent, name)
    except OSError:
        return
    if info is None or not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity:
        return
    try:
        if parent_fd is not None:
            os.unlink(name, dir_fd=parent_fd)
        else:
            (parent / name).unlink()
    except OSError:
        pass


def _create_temporary_entry(parent_fd: int | None, parent: Path, prefix: str) -> tuple[int, str]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for _ in range(100):
        name = f"{prefix}{secrets.token_hex(8)}"
        try:
            if parent_fd is not None:
                return os.open(name, flags, 0o600, dir_fd=parent_fd), name
            return os.open(parent / name, flags, 0o600), name
        except FileExistsError:
            continue
    raise AssetContractError(f"Could not allocate a temporary output in {parent}")


def _read_json_object_entry(parent_fd: int | None, parent: Path, name: str) -> dict[str, Any]:
    if parent_fd is None:
        return read_json_object(parent / name)
    descriptor: int | None = None
    source = parent / name
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("not a standalone regular file")
        if info.st_size > MAX_CONTRACT_BYTES:
            raise OSError(f"exceeds the {MAX_CONTRACT_BYTES}-byte limit")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(MAX_CONTRACT_BYTES + 1)
        if len(payload) > MAX_CONTRACT_BYTES:
            raise OSError(f"exceeds the {MAX_CONTRACT_BYTES}-byte limit")
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite_json_constant,
            parse_float=_parse_finite_json_float,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AssetContractError(f"Could not read {source}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise AssetContractError(f"{source} must contain a JSON object")
    return value


@contextmanager
def _exclusive_write_lock(
    destination: Path,
    parent_fd: int | None,
    parent_identity: tuple[int, int],
) -> Iterator[None]:
    """Serialize cooperating replacements of one contract file."""

    lock = destination.with_name(f".{destination.name}.lock")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        if parent_fd is not None:
            descriptor = os.open(lock.name, flags, 0o600, dir_fd=parent_fd)
        else:
            if _safe_directory_identity(destination.parent) != parent_identity:
                raise AssetContractError(
                    f"Output parent changed during publication: {destination.parent}"
                )
            descriptor = os.open(lock, flags, 0o600)
    except FileExistsError as exc:
        raise AssetContractError(f"Another writer is updating {destination}") from exc
    except OSError as exc:
        raise AssetContractError(f"Could not lock {destination}: {exc}") from exc
    info = descriptor_file_stat(descriptor)
    identity = (info.st_dev, info.st_ino)
    os.close(descriptor)
    try:
        yield
    finally:
        _unlink_owned_entry(parent_fd, destination.parent, lock.name, identity)


def write_json_atomic(
    path: str | Path,
    value: object,
    *,
    overwrite: bool = False,
    expected_content_hash: str | None = None,
) -> None:
    """Publish strict JSON atomically, optionally using content-hash CAS."""

    if expected_content_hash is not None and not overwrite:
        raise AssetContractError("expected_content_hash requires overwrite=True")
    if expected_content_hash is not None and (
        not isinstance(expected_content_hash, str)
        or len(expected_content_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_content_hash)
    ):
        raise AssetContractError("expected_content_hash must be a lowercase SHA-256 digest")
    payload = encoded_json(value)
    requested_destination = Path(path)
    destination = prepare_output_path(requested_destination)
    with _open_verified_output_parent(destination.parent) as (parent_fd, parent_identity):
        try:
            existing = _entry_info(parent_fd, destination.parent, destination.name)
        except OSError as exc:
            raise AssetContractError(
                f"Could not inspect output {requested_destination}: {exc}"
            ) from exc
        if existing is not None and stat.S_ISLNK(existing.st_mode):
            raise AssetContractError(f"Refusing to replace symbolic link {requested_destination}")
        if existing is not None and not overwrite:
            raise AssetContractError(f"Refusing to overwrite {requested_destination}")

        descriptor, temporary_name = _create_temporary_entry(
            parent_fd,
            destination.parent,
            f".{destination.name}.",
        )
        temporary_info = descriptor_file_stat(descriptor)
        temporary_identity = (temporary_info.st_dev, temporary_info.st_ino)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            if not overwrite:
                try:
                    if parent_fd is not None:
                        os.link(
                            temporary_name,
                            destination.name,
                            src_dir_fd=parent_fd,
                            dst_dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                    else:
                        os.link(destination.parent / temporary_name, destination)
                except FileExistsError as exc:
                    raise AssetContractError(
                        f"Refusing to overwrite {requested_destination}"
                    ) from exc
            else:
                with _exclusive_write_lock(destination, parent_fd, parent_identity):
                    current_info = _entry_info(parent_fd, destination.parent, destination.name)
                    if current_info is not None and stat.S_ISLNK(current_info.st_mode):
                        raise AssetContractError(
                            f"Refusing to replace symbolic link {requested_destination}"
                        )
                    if expected_content_hash is not None:
                        current = _read_json_object_entry(
                            parent_fd,
                            destination.parent,
                            destination.name,
                        )
                        if current.get("content_hash") != expected_content_hash:
                            raise AssetContractError(
                                f"Content changed before publishing {requested_destination}"
                            )
                    if parent_fd is not None:
                        os.rename(
                            temporary_name,
                            destination.name,
                            src_dir_fd=parent_fd,
                            dst_dir_fd=parent_fd,
                        )
                    else:
                        if _safe_directory_identity(destination.parent) != parent_identity:
                            raise AssetContractError(
                                f"Output parent changed during publication: {destination.parent}"
                            )
                        os.replace(destination.parent / temporary_name, destination)
        finally:
            _unlink_owned_entry(
                parent_fd,
                destination.parent,
                temporary_name,
                temporary_identity,
            )


def bind_content_hash(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["content_hash"] = canonical_payload_hash(result)
    return result


def require_content_hash(payload: dict[str, Any], *, context: str) -> None:
    expected = payload.get("content_hash")
    if not isinstance(expected, str) or expected != canonical_payload_hash(payload):
        raise AssetContractError(f"{context} content hash does not match its contents")
