#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unicodedata
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from isoworld.content.file_stat import descriptor_file_stat, path_file_stat  # noqa: E402
from isoworld.content.portability import (  # noqa: E402
    portable_path_key,
    portable_relative_path,
)

PINNED_TOOLS = {
    "build": "1.5.0",
    "setuptools": "83.0.0",
    "wheel": "0.47.0",
}
PUBLIC_DATA_DIRECTORIES = ("contracts", "schemas")
CANONICAL_PUBLIC_DATA_ROOT = PurePosixPath("share/world-forge")
LEGACY_PUBLIC_DATA_ROOT = PurePosixPath("share/rpg-world-forge")
EXPECTED_DISTRIBUTION_NAME = "world-forge"
EXPECTED_PROJECT_VERSION = "0.7.0"
EXPECTED_WHEEL_DIST_INFO_FILES = frozenset(
    {
        "METADATA",
        "RECORD",
        "WHEEL",
        "entry_points.txt",
        "licenses/LICENSE",
        "top_level.txt",
    }
)
EXPECTED_WHEEL_DIST_INFO_DIRECTORIES = frozenset({"", "licenses"})
EXPECTED_WHEEL_DATA_SUBTREES = (
    ("data", "share", "world-forge"),
    ("data", "share", "rpg-world-forge"),
)
RESERVED_WHEEL_ROOT_METADATA_FILES = frozenset(
    {
        "pkg-info",
        "metadata",
        "wheel",
        "record",
        "entry_points.txt",
        "top_level.txt",
        "egg-info",
    }
)
RESERVED_WHEEL_NAMESPACE_SUFFIXES = (".dist-info", ".data", ".egg-info")
MAX_RELEASE_ARCHIVE_PATH_BYTES = 1024


class ReleaseBuildError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseIdentity:
    name: str
    version: str
    archive_name: str
    archive_version: str
    dist_info_root: str
    data_root: str
    sdist_root: str
    sdist_filename: str
    wheel_filename: str


def _expected_release_identity() -> ReleaseIdentity:
    name = EXPECTED_DISTRIBUTION_NAME
    version = EXPECTED_PROJECT_VERSION
    archive_name = re.sub(r"[-_.]+", "_", name).casefold()
    archive_version = _normalized_wheel_version(version)
    sdist_root = f"{archive_name}-{version}"
    return ReleaseIdentity(
        name=name,
        version=version,
        archive_name=archive_name,
        archive_version=archive_version,
        dist_info_root=f"{archive_name}-{archive_version}.dist-info",
        data_root=f"{archive_name}-{archive_version}.data",
        sdist_root=sdist_root,
        sdist_filename=f"{sdist_root}.tar.gz",
        wheel_filename=f"{archive_name}-{archive_version}-py3-none-any.whl",
    )


def _require_expected_release_identity(identity: ReleaseIdentity) -> None:
    if identity != _expected_release_identity():
        raise ReleaseBuildError(
            "release identity does not match trusted canonical project identity"
        )


def _release_identity(source_root: Path) -> ReleaseIdentity:
    pyproject = source_root / "pyproject.toml"
    try:
        raw = _read_release_source_file(pyproject, "pyproject.toml")
        document = tomllib.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseBuildError(f"trusted pyproject metadata is invalid: {exc}") from exc
    project = document.get("project")
    if not isinstance(project, dict):
        raise ReleaseBuildError("trusted pyproject metadata has no [project] table")
    name = project.get("name")
    version = project.get("version")
    if name != EXPECTED_DISTRIBUTION_NAME:
        raise ReleaseBuildError(f"trusted project name must be {EXPECTED_DISTRIBUTION_NAME!r}")
    if version != EXPECTED_PROJECT_VERSION:
        raise ReleaseBuildError(f"trusted project version must be {EXPECTED_PROJECT_VERSION!r}")
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list) or any(not isinstance(item, str) for item in dynamic):
        raise ReleaseBuildError("trusted project dynamic metadata must be a string list")
    if "version" in dynamic:
        raise ReleaseBuildError("trusted project version must not be dynamic")
    return _expected_release_identity()


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        raise ReleaseBuildError(f"command failed: {' '.join(command)}\n{detail}")
    return completed


def _verify_toolchain() -> None:
    mismatches: list[str] = []
    for name, expected in PINNED_TOOLS.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{name} is not installed")
            continue
        if actual != expected:
            mismatches.append(f"{name}=={actual}, expected {name}=={expected}")
    if mismatches:
        raise ReleaseBuildError("audited build toolchain mismatch: " + "; ".join(mismatches))


def _require_supported_platform(platform_name: str | None = None) -> None:
    platform_name = sys.platform if platform_name is None else platform_name
    if platform_name != "win32" and not platform_name.startswith("linux"):
        raise ReleaseBuildError(
            "reproducible release publication is supported only on Linux and Windows"
        )
    if not hasattr(os, "link"):
        raise ReleaseBuildError("exclusive hard-link publication is unavailable")


def _head_oid(repo: Path) -> str:
    completed = _run(["git", "rev-parse", "--verify", "HEAD^{commit}"], cwd=repo)
    oid = completed.stdout.strip().lower()
    if len(oid) not in {40, 64} or any(character not in "0123456789abcdef" for character in oid):
        raise ReleaseBuildError("could not resolve HEAD to an immutable commit object")
    return oid


def _source_date_epoch(repo: Path, commit_oid: str) -> int:
    completed = _run(["git", "log", "-1", "--format=%ct", commit_oid], cwd=repo)
    value = completed.stdout.strip()
    if not value.isdigit():
        raise ReleaseBuildError(f"could not derive SOURCE_DATE_EPOCH from {commit_oid}")
    return int(value)


def _git_archive(repo: Path, commit_oid: str) -> bytes:
    completed = subprocess.run(
        ["git", "archive", "--format=tar", commit_oid],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ReleaseBuildError(
            f"git archive {commit_oid} failed\n"
            + completed.stderr.decode("utf-8", errors="replace")
        )
    return completed.stdout


def _portable_archive_key(parts: tuple[str, ...]) -> tuple[str, ...]:
    return portable_path_key(PurePosixPath(*parts))


def _archive_member_parts(member: tarfile.TarInfo) -> tuple[str, ...]:
    name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise ReleaseBuildError(f"unsafe archive member: {member.name}")
    parts = tuple(name.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise ReleaseBuildError(f"unsafe archive member: {member.name}")
    normalized = PurePosixPath(*parts)
    if normalized.is_absolute() or normalized.parts != parts:
        raise ReleaseBuildError(f"unsafe archive member: {member.name}")
    return parts


def _extract_archive(archive: bytes, destination: Path) -> None:
    entries: list[tuple[tarfile.TarInfo, tuple[str, ...], bytes | None]] = []
    portable_paths: dict[tuple[str, ...], tuple[str, ...]] = {}
    member_paths: set[tuple[str, ...]] = set()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source.getmembers():
            if not member.isdir() and not member.isfile():
                raise ReleaseBuildError(f"archive member is not a regular file: {member.name}")
            parts = _archive_member_parts(member)
            if parts in member_paths:
                raise ReleaseBuildError(f"duplicate archive member: {member.name}")
            member_paths.add(parts)
            for length in range(1, len(parts) + 1):
                path = parts[:length]
                key = _portable_archive_key(path)
                previous = portable_paths.get(key)
                if previous is not None and previous != path:
                    raise ReleaseBuildError(
                        "portable archive path collision: "
                        f"{'/'.join(previous)!r} and {'/'.join(path)!r}"
                    )
                portable_paths[key] = path
            payload: bytes | None = None
            if member.isfile():
                extracted = source.extractfile(member)
                if extracted is None:
                    raise ReleaseBuildError(f"could not read archive member: {member.name}")
                payload = extracted.read()
            entries.append((member, parts, payload))

    entry_types = {parts: member.isdir() for member, parts, _payload in entries}
    for parts, is_directory in entry_types.items():
        for length in range(1, len(parts)):
            ancestor = parts[:length]
            if ancestor in entry_types and not entry_types[ancestor]:
                raise ReleaseBuildError(
                    f"archive file is used as a directory: {'/'.join(ancestor)}"
                )
        if not is_directory and any(
            len(candidate) > len(parts) and candidate[: len(parts)] == parts
            for candidate in entry_types
        ):
            raise ReleaseBuildError(f"archive file is used as a directory: {'/'.join(parts)}")

    destination.mkdir(parents=True)
    directories = {
        parts[:length]
        for _member, parts, _payload in entries
        for length in range(1, len(parts) + 1)
        if length < len(parts) or entry_types.get(parts, False)
    }
    for parts in sorted(directories, key=lambda item: (len(item), item)):
        target = destination.joinpath(*parts)
        try:
            target.mkdir(mode=0o755)
        except FileExistsError:
            if not target.is_dir() or target.is_symlink():
                raise ReleaseBuildError(f"unsafe archive directory: {'/'.join(parts)}") from None

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for member, parts, payload in sorted(entries, key=lambda item: item[1]):
        if member.isdir():
            continue
        assert payload is not None
        target = destination.joinpath(*parts)
        descriptor: int | None = None
        try:
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(payload)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
        except OSError as exc:
            raise ReleaseBuildError(
                f"could not extract archive member {member.name}: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _build_environment(epoch: int, environment_root: Path) -> dict[str, str]:
    home = environment_root / "home"
    temporary = environment_root / "tmp"
    xdg = environment_root / "xdg"
    for directory in (home, temporary, xdg):
        directory.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "TMPDIR": str(temporary),
        "XDG_CACHE_HOME": str(xdg / "cache"),
        "XDG_CONFIG_HOME": str(xdg / "config"),
        "XDG_DATA_HOME": str(xdg / "data"),
        "SOURCE_DATE_EPOCH": str(epoch),
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "TZ": "UTC",
        "LC_ALL": "C",
        "LANG": "C",
    }
    for name, value in os.environ.items():
        canonical_name = name.upper()
        if canonical_name in {"SYSTEMROOT", "WINDIR"}:
            env[canonical_name] = value
    return env


def _read_release_source_file(path: Path, relative: str) -> bytes:
    descriptor: int | None = None
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ReleaseBuildError(f"release public data is not regular: {relative}")
        if before.st_nlink != 1:
            raise ReleaseBuildError(f"release public data is hard-linked: {relative}")
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_nlink,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if opened_identity != before_identity:
            raise ReleaseBuildError(f"release public data identity changed: {relative}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        named = path.lstat()
        for info in (after, named):
            if (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ) != opened_identity:
                raise ReleaseBuildError(f"release public data identity changed: {relative}")
        return b"".join(chunks)
    except ReleaseBuildError:
        raise
    except OSError as exc:
        raise ReleaseBuildError(f"could not read release public data {relative}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _release_public_data_inventory(source_root: Path) -> dict[str, bytes]:
    inventory: dict[str, bytes] = {}
    portable_paths: dict[tuple[str, ...], str] = {}
    for directory_name in PUBLIC_DATA_DIRECTORIES:
        source_directory = source_root / directory_name
        try:
            directory_info = source_directory.lstat()
        except OSError as exc:
            raise ReleaseBuildError(
                f"release public data directory is missing: {directory_name}"
            ) from exc
        if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(directory_info.st_mode):
            raise ReleaseBuildError(f"release public data directory is unsafe: {directory_name}")
        for path in sorted(source_directory.rglob("*"), key=lambda item: item.as_posix()):
            relative = path.relative_to(source_root).as_posix()
            parts = PurePosixPath(relative).parts
            key = _portable_archive_key(parts)
            previous = portable_paths.setdefault(key, relative)
            if previous != relative:
                raise ReleaseBuildError(
                    f"release public data path collision: {previous!r} and {relative!r}"
                )
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode) and not path.is_symlink():
                continue
            inventory[relative] = _read_release_source_file(path, relative)
    if "contracts/catalog.json" not in inventory or not any(
        relative.startswith("schemas/") for relative in inventory
    ):
        raise ReleaseBuildError("release public data inventory is incomplete")
    return inventory


def _write_release_public_tree(
    source_root: Path,
    relative_root: PurePosixPath,
    inventory: dict[str, bytes],
) -> None:
    target_root = source_root.joinpath(*relative_root.parts)
    if target_root.exists() or target_root.is_symlink():
        raise ReleaseBuildError(f"release public data target already exists: {relative_root}")
    target_root.mkdir(parents=True)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    for relative, payload in sorted(inventory.items()):
        target = target_root.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor: int | None = None
        try:
            descriptor = os.open(target, flags, 0o644)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except OSError as exc:
            raise ReleaseBuildError(
                f"could not prepare release public data {relative_root}/{relative}: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)


def _prepare_release_source(source_root: Path) -> None:
    inventory = _release_public_data_inventory(source_root)
    _write_release_public_tree(source_root, CANONICAL_PUBLIC_DATA_ROOT, inventory)
    _write_release_public_tree(source_root, LEGACY_PUBLIC_DATA_ROOT, inventory)


def _metadata_identity(payload: bytes, *, context: str) -> tuple[str, str]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ReleaseBuildError(f"{context} is not UTF-8: {exc}") from exc
    headers: dict[str, list[str]] = {"name": [], "version": []}
    for line in text.splitlines():
        if not line:
            break
        if line[:1].isspace():
            continue
        field, separator, value = line.partition(":")
        key = field.casefold()
        if separator and key in headers:
            headers[key].append(value.strip())
    if len(headers["name"]) != 1 or not headers["name"][0]:
        raise ReleaseBuildError(f"{context} must contain exactly one Name field")
    if len(headers["version"]) != 1 or not headers["version"][0]:
        raise ReleaseBuildError(f"{context} must contain exactly one Version field")
    return headers["name"][0], headers["version"][0]


def _require_metadata_identity(
    payload: bytes,
    *,
    context: str,
    identity: ReleaseIdentity,
) -> None:
    _require_expected_release_identity(identity)
    name, version = _metadata_identity(payload, context=context)
    if name != identity.name:
        raise ReleaseBuildError(f"{context} name does not match expected release identity")
    if version != identity.version:
        raise ReleaseBuildError(f"{context} version does not match expected release identity")


def _strict_catalog_object(raw: bytes) -> dict[str, object]:
    def reject_constant(value: str) -> object:
        raise ReleaseBuildError(f"embedded contract catalog has non-finite value {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseBuildError(f"embedded contract catalog has duplicate key {key!r}")
            result[key] = value
        return result

    try:
        text = raw.decode("utf-8", errors="strict")
        document = json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseBuildError(f"embedded contract catalog is invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ReleaseBuildError("embedded contract catalog root must be an object")
    canonical = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if raw != canonical:
        raise ReleaseBuildError("embedded contract catalog must use canonical JSON bytes")
    return document


def _public_inventory_path(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ReleaseBuildError(f"{context} is not a portable public data path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or not value.startswith(("contracts/", "schemas/"))
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ReleaseBuildError(f"{context} is not a portable public data path")
    return value


def _expected_public_inventory(inventory: dict[str, bytes]) -> tuple[set[str], set[str]]:
    catalog_relative = "contracts/catalog.json"
    catalog_raw = inventory.get(catalog_relative)
    if catalog_raw is None:
        raise ReleaseBuildError("public data bridge is incomplete: embedded catalog is missing")
    catalog = _strict_catalog_object(catalog_raw)
    if set(catalog) != {"format", "format_version", "contracts"}:
        raise ReleaseBuildError("embedded contract catalog has an invalid top-level shape")
    if (
        catalog.get("format") != "rpg-world-forge.contract_catalog"
        or catalog.get("format_version") != 1
    ):
        raise ReleaseBuildError("embedded contract catalog has an unsupported identity")
    contracts = catalog.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise ReleaseBuildError("embedded contract catalog has no contract entries")
    expected = {catalog_relative, "contracts/README.md"}
    portable: dict[tuple[str, ...], str] = {}
    for index, entry in enumerate(contracts):
        if not isinstance(entry, dict):
            raise ReleaseBuildError(f"embedded contract catalog entry {index} is not an object")
        expected.add(
            _public_inventory_path(
                entry.get("schema"),
                context=f"embedded contract catalog entry {index} schema",
            )
        )
        for field in ("fixtures", "tests", "docs"):
            values = entry.get(field)
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ReleaseBuildError(
                    f"embedded contract catalog entry {index} {field} is not a string list"
                )
            for value in values:
                if value.startswith(("contracts/", "schemas/")):
                    expected.add(
                        _public_inventory_path(
                            value,
                            context=f"embedded contract catalog entry {index} {field}",
                        )
                    )
    for relative in sorted(expected):
        parts = PurePosixPath(relative).parts
        key = _portable_archive_key(parts)
        previous = portable.setdefault(key, relative)
        if previous != relative:
            raise ReleaseBuildError(
                f"embedded public data path collision: {previous!r} and {relative!r}"
            )
    directories = {""}
    for relative in expected:
        parts = PurePosixPath(relative).parts[:-1]
        for length in range(1, len(parts) + 1):
            directories.add(PurePosixPath(*parts[:length]).as_posix())
    return expected, directories


def _portable_member_parts(name: str, *, directory: bool) -> tuple[str, ...]:
    raw = name[:-1] if directory and name.endswith("/") else name
    if not raw or (not directory and name.endswith("/")):
        raise ReleaseBuildError(f"unsafe release archive member: {name}")
    raw_parts = tuple(raw.split("/"))
    # Retain the precise diagnostic relied on by release evidence while the
    # canonical portability helper remains the source of truth for validity.
    if any(part.rstrip(" .") != part for part in raw_parts):
        raise ReleaseBuildError(
            f"unsafe release archive member has Win32 trailing-dot/space alias: {name}"
        )
    try:
        encoded = raw.encode("utf-8", errors="strict")
        relative = portable_relative_path(raw)
    except UnicodeError as exc:
        raise ReleaseBuildError(f"unsafe release archive member: {name}") from exc
    if relative is None or len(encoded) > MAX_RELEASE_ARCHIVE_PATH_BYTES:
        raise ReleaseBuildError(f"unsafe release archive member: {name}")
    return relative.parts


def _contains_path_marker(parts: tuple[str, ...], marker: tuple[str, ...]) -> bool:
    return any(
        parts[index : index + len(marker)] == marker
        for index in range(0, len(parts) - len(marker) + 1)
    )


def _capture_archive_public_trees(
    entries: list[tuple[str, bool, bytes | None]],
    *,
    canonical_prefix: tuple[str, ...],
    legacy_prefix: tuple[str, ...],
) -> dict[str, tuple[dict[str, bytes], set[str]]]:
    locations = {
        "canonical": (canonical_prefix, ("share", "world-forge")),
        "legacy": (legacy_prefix, ("share", "rpg-world-forge")),
    }
    trees = {label: (dict[str, bytes](), {""}) for label in locations}
    seen: set[tuple[str, ...]] = set()
    portable: dict[tuple[str, ...], tuple[str, ...]] = {}
    found = {label: False for label in locations}
    for name, is_directory, payload in entries:
        parts = _portable_member_parts(name, directory=is_directory)
        if parts in seen:
            raise ReleaseBuildError(f"duplicate release archive member: {name}")
        seen.add(parts)
        key = _portable_archive_key(parts)
        previous = portable.setdefault(key, parts)
        if previous != parts:
            raise ReleaseBuildError(
                "portable release archive collision: "
                f"{'/'.join(previous)!r} and {'/'.join(parts)!r}"
            )
        matched: str | None = None
        for label, (prefix, _marker) in locations.items():
            if parts[: len(prefix)] == prefix:
                matched = label
                relative_parts = parts[len(prefix) :]
                found[label] = True
                files, directories = trees[label]
                if not relative_parts:
                    if not is_directory:
                        raise ReleaseBuildError(f"{label} public data root must be a directory")
                    break
                relative = PurePosixPath(*relative_parts).as_posix()
                for length in range(1, len(relative_parts)):
                    directories.add(PurePosixPath(*relative_parts[:length]).as_posix())
                if is_directory:
                    directories.add(relative)
                else:
                    assert payload is not None
                    files[relative] = payload
                break
        for label, (_prefix, marker) in locations.items():
            if _contains_path_marker(parts, marker) and matched != label:
                raise ReleaseBuildError(f"misplaced public data location for {label}: {name}")
    if not all(found.values()):
        raise ReleaseBuildError(
            "public data bridge is incomplete: exact public data location missing"
        )
    return trees


def _validate_archive_public_trees(
    trees: dict[str, tuple[dict[str, bytes], set[str]]],
    *,
    archive_kind: str,
) -> None:
    canonical_files, canonical_directories = trees["canonical"]
    expected_files, expected_directories = _expected_public_inventory(canonical_files)
    for label in ("canonical", "legacy"):
        files, directories = trees[label]
        missing = expected_files - set(files)
        extra = set(files) - expected_files
        missing_directories = expected_directories - directories
        extra_directories = directories - expected_directories
        if missing:
            raise ReleaseBuildError(
                f"{archive_kind} {label} missing public data file {sorted(missing)[0]}"
            )
        if extra:
            raise ReleaseBuildError(
                f"{archive_kind} {label} has unexpected public data file {sorted(extra)[0]}"
            )
        if missing_directories:
            raise ReleaseBuildError(
                f"{archive_kind} {label} missing public data directory "
                f"{sorted(missing_directories)[0]}"
            )
        if extra_directories:
            raise ReleaseBuildError(
                f"{archive_kind} {label} has unexpected public data directory "
                f"{sorted(extra_directories)[0]}"
            )
    legacy_files, legacy_directories = trees["legacy"]
    if canonical_files != legacy_files or canonical_directories != legacy_directories:
        raise ReleaseBuildError(f"{archive_kind} public data bridge is divergent")


def _require_sdist_identity(
    path: Path,
    entries: list[tuple[str, bool, bytes | None]],
    identity: ReleaseIdentity,
) -> None:
    _require_expected_release_identity(identity)
    if path.name != identity.sdist_filename:
        raise ReleaseBuildError(
            f"sdist filename does not match expected release identity: {path.name}"
        )
    roots = {
        _portable_member_parts(name, directory=is_directory)[0]
        for name, is_directory, _payload in entries
    }
    if roots != {identity.sdist_root}:
        raise ReleaseBuildError("sdist root does not match expected release identity")
    pkg_info_path = f"{identity.sdist_root}/PKG-INFO"
    payloads = {name: payload for name, is_directory, payload in entries if not is_directory}
    pkg_info = payloads.get(pkg_info_path)
    if pkg_info is None:
        raise ReleaseBuildError("sdist root is missing PKG-INFO")
    _require_metadata_identity(
        pkg_info,
        context="sdist PKG-INFO",
        identity=identity,
    )


def _read_sdist_entries(path: Path) -> list[tuple[str, bool, bytes | None]]:
    entries: list[tuple[str, bool, bytes | None]] = []
    with tarfile.open(path, mode="r:gz") as archive:
        for info in archive.getmembers():
            if not info.isdir() and not info.isfile():
                raise ReleaseBuildError(
                    f"sdist member is not a regular file or directory: {info.name}"
                )
            payload: bytes | None = None
            if info.isfile():
                extracted = archive.extractfile(info)
                if extracted is None:
                    raise ReleaseBuildError(f"could not read sdist member: {info.name}")
                payload = extracted.read()
            entries.append((info.name, info.isdir(), payload))
    return entries


def _verify_sdist_public_data(path: Path, identity: ReleaseIdentity) -> None:
    _require_expected_release_identity(identity)
    entries = _read_sdist_entries(path)
    _require_sdist_identity(path, entries, identity)
    trees = _capture_archive_public_trees(
        entries,
        canonical_prefix=(identity.sdist_root, *CANONICAL_PUBLIC_DATA_ROOT.parts),
        legacy_prefix=(identity.sdist_root, *LEGACY_PUBLIC_DATA_ROOT.parts),
    )
    _validate_archive_public_trees(trees, archive_kind="sdist")


def _verify_wheel_public_data(path: Path, identity: ReleaseIdentity) -> None:
    _require_expected_release_identity(identity)
    if path.name != identity.wheel_filename:
        raise ReleaseBuildError(
            f"wheel filename does not match expected release identity: {path.name}"
        )
    entries: list[tuple[str, bool, bytes | None]] = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        _wheel_dist_info_identity(
            infos,
            read_member=archive.read,
            identity=identity,
        )
        for info in infos:
            is_directory = info.filename.endswith("/")
            entries.append(
                (
                    info.filename,
                    is_directory,
                    None if is_directory else archive.read(info),
                )
            )
    trees = _capture_archive_public_trees(
        entries,
        canonical_prefix=(
            identity.data_root,
            "data",
            *CANONICAL_PUBLIC_DATA_ROOT.parts,
        ),
        legacy_prefix=(
            identity.data_root,
            "data",
            *LEGACY_PUBLIC_DATA_ROOT.parts,
        ),
    )
    _validate_archive_public_trees(trees, archive_kind="wheel")


def _build_from_source(source_root: Path, output_root: Path, epoch: int) -> tuple[Path, Path]:
    identity = _release_identity(source_root)
    _prepare_release_source(source_root)
    dist = output_root / "dist"
    dist.mkdir(parents=True)
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--sdist",
            "--wheel",
            "--outdir",
            str(dist),
        ],
        cwd=source_root,
        env=_build_environment(epoch, output_root / "environment"),
    )
    sdists = sorted(dist.glob("*.tar.gz"))
    wheels = sorted(dist.glob("*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        raise ReleaseBuildError("build did not produce exactly one sdist and one wheel")
    if sdists[0].name != identity.sdist_filename:
        raise ReleaseBuildError("build produced an unexpected sdist filename")
    if wheels[0].name != identity.wheel_filename:
        raise ReleaseBuildError("build produced an unexpected wheel filename")
    canonical_sdist = output_root / identity.sdist_filename
    canonical_wheel = output_root / identity.wheel_filename
    _canonicalize_sdist(sdists[0], canonical_sdist, epoch, identity)
    _canonicalize_wheel(wheels[0], canonical_wheel, identity)
    _verify_sdist_public_data(canonical_sdist, identity)
    _verify_wheel_public_data(canonical_wheel, identity)
    return canonical_sdist, canonical_wheel


def _normalized_tarinfo(info: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    normalized = tarfile.TarInfo(info.name)
    normalized.type = info.type
    normalized.linkname = info.linkname
    normalized.mtime = epoch
    normalized.uid = 0
    normalized.gid = 0
    normalized.uname = ""
    normalized.gname = ""
    normalized.pax_headers = {}
    if info.isdir():
        normalized.mode = 0o755
    elif info.mode & 0o111:
        normalized.mode = 0o755
    else:
        normalized.mode = 0o644
    if info.isfile():
        normalized.size = info.size
    return normalized


def _canonicalize_sdist(
    source: Path,
    destination: Path,
    epoch: int,
    identity: ReleaseIdentity,
) -> None:
    _require_expected_release_identity(identity)
    if source.name != identity.sdist_filename or destination.name != identity.sdist_filename:
        raise ReleaseBuildError("sdist filename does not match expected release identity")
    with tarfile.open(source, mode="r:gz") as archive:
        entries = sorted(archive.getmembers(), key=lambda item: item.name)
        payloads: dict[str, bytes] = {}
        seen: set[str] = set()
        for entry in entries:
            if entry.name in seen:
                raise ReleaseBuildError(f"duplicate sdist member: {entry.name}")
            seen.add(entry.name)
            if not entry.isdir() and not entry.isfile():
                raise ReleaseBuildError(f"sdist member is not a regular file: {entry.name}")
            if entry.isfile():
                extracted = archive.extractfile(entry)
                if extracted is None:
                    raise ReleaseBuildError(f"could not read sdist member: {entry.name}")
                payloads[entry.name] = extracted.read()
    _require_sdist_identity(
        source,
        [(entry.name, entry.isdir(), payloads.get(entry.name)) for entry in entries],
        identity,
    )
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.USTAR_FORMAT) as target:
        for entry in entries:
            normalized = _normalized_tarinfo(entry, epoch)
            if entry.isfile():
                payload = payloads[entry.name]
                normalized.size = len(payload)
                target.addfile(normalized, io.BytesIO(payload))
            else:
                target.addfile(normalized)
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as gzipped:
            gzipped.write(tar_buffer.getvalue())


def _record_line(path: str, data: bytes) -> list[str]:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return [path, f"sha256={digest}", str(len(data))]


def _render_record(rows: Iterable[list[str]]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerows(rows)
    return text.getvalue().encode("utf-8")


def _zip_datetime() -> tuple[int, int, int, int, int, int]:
    return (1980, 1, 1, 0, 0, 0)


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _normalized_wheel_version(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "_", value).casefold()


def _wheel_member_is_directory(info: zipfile.ZipInfo) -> bool:
    """Validate one ZIP member's declared kind before trusting its name.

    A zero Unix type field means that the creator omitted it. In that case the
    conventional trailing slash defines a directory and a non-slash name defines
    a regular file; a set DOS directory bit must still agree with the slash.
    """

    name_marks_directory = info.filename.endswith("/")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    unix_kind = stat.S_IFMT(unix_mode)
    dos_marks_directory = bool(info.external_attr & 0x10)
    if unix_kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ReleaseBuildError(
            f"wheel member kind is not a regular file or directory: {info.filename}"
        )
    if name_marks_directory:
        if unix_kind == stat.S_IFREG:
            raise ReleaseBuildError(
                f"wheel member kind conflicts with directory name: {info.filename}"
            )
        return True
    if unix_kind == stat.S_IFDIR or dos_marks_directory:
        raise ReleaseBuildError(f"wheel member kind conflicts with file name: {info.filename}")
    return False


def _wheel_dist_info_identity(
    infos: Iterable[zipfile.ZipInfo],
    *,
    read_member: Callable[[zipfile.ZipInfo], bytes],
    identity: ReleaseIdentity,
) -> tuple[str, str]:
    _require_expected_release_identity(identity)
    members = tuple((info, _wheel_member_is_directory(info)) for info in infos)
    member_names = tuple(info.filename for info, _is_directory in members)
    if len(member_names) != len(set(member_names)):
        raise ReleaseBuildError("wheel contains duplicate members")
    parsed_members: list[tuple[zipfile.ZipInfo, str, bool, tuple[str, ...]]] = []
    portable_paths: dict[tuple[str, ...], tuple[str, ...]] = {}
    portable_kinds: dict[tuple[str, ...], bool] = {}
    approved_anchor_paths = {
        (identity.dist_info_root, *PurePosixPath(relative).parts)
        for relative in EXPECTED_WHEEL_DIST_INFO_FILES
        if PurePosixPath(relative).name.casefold() in RESERVED_WHEEL_ROOT_METADATA_FILES
    }
    for info, is_directory in members:
        name = info.filename
        parts = _portable_member_parts(name, directory=is_directory)
        for length in range(1, len(parts) + 1):
            prefix = parts[:length]
            key = _portable_archive_key(prefix)
            previous = portable_paths.setdefault(key, prefix)
            if previous != prefix:
                raise ReleaseBuildError(
                    "portable wheel member collision: "
                    f"{'/'.join(previous)!r} and {'/'.join(prefix)!r}"
                )
            requires_directory = length < len(parts) or is_directory
            if key in portable_kinds and portable_kinds[key] != requires_directory:
                raise ReleaseBuildError(f"wheel file is used as a directory: {'/'.join(prefix)}")
            portable_kinds[key] = requires_directory
        if len(parts) == 1 and parts[0].casefold() in RESERVED_WHEEL_ROOT_METADATA_FILES:
            raise ReleaseBuildError(f"wheel contains reserved wheel-root metadata: {name}")
        for index, part in enumerate(parts):
            folded_part = part.casefold()
            if not folded_part.endswith(RESERVED_WHEEL_NAMESPACE_SUFFIXES):
                continue
            if index == 0:
                # Top-level namespaces are checked below against the exact release
                # identity and their closed inventories. No nested component may
                # claim a distribution metadata namespace.
                continue
            raise ReleaseBuildError(
                f"wheel contains reserved wheel metadata namespace {part!r}: {name}"
            )
        folded_root = parts[0].casefold()
        foreign_metadata_root = folded_root.endswith(RESERVED_WHEEL_NAMESPACE_SUFFIXES) and parts[
            0
        ] not in {identity.dist_info_root, identity.data_root}
        if not foreign_metadata_root:
            for part in parts:
                if part.casefold() not in RESERVED_WHEEL_ROOT_METADATA_FILES:
                    continue
                if not is_directory and parts in approved_anchor_paths:
                    continue
                raise ReleaseBuildError(
                    f"wheel contains reserved wheel metadata anchor {part!r}: {name}"
                )
        parsed_members.append((info, name, is_directory, parts))

    dist_info_roots = {
        parts[0]
        for _info, _name, _is_directory, parts in parsed_members
        if parts[0].casefold().endswith(".dist-info")
    }
    if len(dist_info_roots) != 1:
        raise ReleaseBuildError("wheel must contain exactly one .dist-info root")
    dist_info_root = next(iter(dist_info_roots))
    if dist_info_root != identity.dist_info_root:
        raise ReleaseBuildError("wheel .dist-info root does not match expected release identity")
    metadata_path = f"{dist_info_root}/METADATA"
    record_path = f"{dist_info_root}/RECORD"

    dist_info_files: set[str] = set()
    dist_info_directories: set[str] = set()
    data_roots: set[str] = set()
    for _info, _name, is_directory, parts in parsed_members:
        root = parts[0]
        folded_root = root.casefold()
        if folded_root.endswith(".egg-info") or folded_root == "egg-info":
            raise ReleaseBuildError(f"wheel contains foreign top-level metadata namespace: {root}")
        if folded_root.endswith(".data"):
            data_roots.add(root)
        if root != dist_info_root:
            continue
        relative = PurePosixPath(*parts[1:]).as_posix() if len(parts) > 1 else ""
        if is_directory:
            dist_info_directories.add(relative)
        else:
            dist_info_files.add(relative)

    missing_dist_info = EXPECTED_WHEEL_DIST_INFO_FILES - dist_info_files
    extra_dist_info = dist_info_files - EXPECTED_WHEEL_DIST_INFO_FILES
    extra_dist_info_directories = dist_info_directories - EXPECTED_WHEEL_DIST_INFO_DIRECTORIES
    if missing_dist_info:
        missing = sorted(missing_dist_info)[0]
        raise ReleaseBuildError(f"wheel .dist-info metadata inventory is missing {missing}")
    if extra_dist_info:
        raise ReleaseBuildError(
            f"wheel has unexpected .dist-info metadata entry: {sorted(extra_dist_info)[0]}"
        )
    if extra_dist_info_directories:
        raise ReleaseBuildError(
            "wheel has unexpected .dist-info metadata entry: "
            f"{sorted(extra_dist_info_directories)[0]}/"
        )

    foreign_data_roots = data_roots - {identity.data_root}
    if foreign_data_roots:
        raise ReleaseBuildError(
            f"wheel contains foreign .data root: {sorted(foreign_data_roots)[0]}"
        )
    if data_roots != {identity.data_root}:
        raise ReleaseBuildError("wheel is missing the expected .data root")

    for _info, name, is_directory, parts in parsed_members:
        if parts[0] != identity.data_root:
            continue
        relative = parts[1:]
        if is_directory:
            allowed = any(
                relative == prefix[: len(relative)] or relative[: len(prefix)] == prefix
                for prefix in EXPECTED_WHEEL_DATA_SUBTREES
            )
        else:
            allowed = any(
                len(relative) > len(prefix) and relative[: len(prefix)] == prefix
                for prefix in EXPECTED_WHEEL_DATA_SUBTREES
            )
        if not allowed:
            raise ReleaseBuildError(f"wheel has unexpected .data entry: {name}")

    public_entries = [
        (
            info.filename,
            is_directory,
            None if is_directory else read_member(info),
        )
        for info, _name, is_directory, _parts in parsed_members
    ]
    public_trees = _capture_archive_public_trees(
        public_entries,
        canonical_prefix=(
            identity.data_root,
            "data",
            *CANONICAL_PUBLIC_DATA_ROOT.parts,
        ),
        legacy_prefix=(
            identity.data_root,
            "data",
            *LEGACY_PUBLIC_DATA_ROOT.parts,
        ),
    )
    _validate_archive_public_trees(public_trees, archive_kind="wheel")

    metadata_info = next(
        info for info, name, _is_directory, _parts in parsed_members if name == metadata_path
    )
    _require_metadata_identity(
        read_member(metadata_info),
        context="wheel METADATA",
        identity=identity,
    )
    return dist_info_root, record_path


def _canonicalize_wheel(
    source: Path,
    destination: Path,
    identity: ReleaseIdentity,
) -> None:
    _require_expected_release_identity(identity)
    if source.name != identity.wheel_filename or destination.name != identity.wheel_filename:
        raise ReleaseBuildError("wheel filename does not match expected release identity")
    members: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        _dist_info_root, record_path = _wheel_dist_info_identity(
            infos,
            read_member=archive.read,
            identity=identity,
        )
        for info in infos:
            if info.filename.endswith("/"):
                continue
            if info.filename in members:
                raise ReleaseBuildError(f"duplicate wheel member: {info.filename}")
            members[info.filename] = archive.read(info)
            mode = (info.external_attr >> 16) & 0o777
            modes[info.filename] = mode or 0o644
    rows = [_record_line(name, members[name]) for name in sorted(members) if name != record_path]
    rows.append([record_path, "", ""])
    members[record_path] = _render_record(rows)
    modes[record_path] = 0o644

    with zipfile.ZipFile(
        destination, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(filename=name, date_time=_zip_datetime())
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if modes.get(name, 0o644) & 0o111 else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.internal_attr = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, members[name])
    _verify_wheel_record(destination, identity)


def _verify_wheel_record(path: Path, identity: ReleaseIdentity) -> None:
    _require_expected_release_identity(identity)
    if path.name != identity.wheel_filename:
        raise ReleaseBuildError("wheel filename does not match expected release identity")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        _dist_info_root, record_path = _wheel_dist_info_identity(
            infos,
            read_member=archive.read,
            identity=identity,
        )
        names = [info.filename for info in infos if not info.filename.endswith("/")]
        info_by_name = {info.filename: info for info in infos}
        try:
            rows = list(
                csv.reader(io.StringIO(archive.read(info_by_name[record_path]).decode("utf-8")))
            )
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ReleaseBuildError(f"wheel RECORD is invalid: {exc}") from exc
        if any(len(row) != 3 for row in rows):
            raise ReleaseBuildError("wheel RECORD rows must contain exactly three fields")
        records: dict[str, tuple[str, str]] = {}
        for name, digest, size in rows:
            if name in records:
                raise ReleaseBuildError(f"wheel RECORD contains duplicate path: {name}")
            records[name] = (digest, size)
        if set(records) != set(names):
            raise ReleaseBuildError("wheel RECORD paths do not exactly match wheel members")
        for name in names:
            digest, size = records[name]
            if name == record_path:
                if digest or size:
                    raise ReleaseBuildError("wheel RECORD must not hash itself")
                continue
            expected = _record_line(name, archive.read(info_by_name[name]))
            if [name, digest, size] != expected:
                raise ReleaseBuildError(f"wheel RECORD does not match member: {name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_bytes(left: Path, right: Path) -> bool:
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_stream, right.open("rb") as right_stream:
        while True:
            left_chunk = left_stream.read(1024 * 1024)
            right_chunk = right_stream.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _entry_identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReleaseBuildError(f"could not inspect release path {path}: {exc}") from exc
    return info.st_dev, info.st_ino


def _unlink_owned_file(path: Path, identity: tuple[int, int]) -> None:
    try:
        info = path_file_stat(path)
    except OSError:
        return
    if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == identity:
        try:
            path.unlink()
        except OSError:
            pass


def _stage_artifact(source: Path, output_dir: Path) -> tuple[Path, tuple[int, int]]:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    stage: Path | None = None
    for _ in range(100):
        candidate = output_dir / f".{source.name}.stage-{secrets.token_hex(8)}"
        try:
            descriptor = os.open(candidate, flags, 0o600)
            stage = candidate
            break
        except FileExistsError:
            continue
        except OSError as exc:
            raise ReleaseBuildError(
                f"could not stage release artifact {source.name}: {exc}"
            ) from exc
    if descriptor is None or stage is None:
        raise ReleaseBuildError(f"could not allocate release staging file for {source.name}")
    opened = descriptor_file_stat(descriptor)
    identity = (opened.st_dev, opened.st_ino)
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            descriptor = None
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            if hasattr(os, "fchmod"):
                os.fchmod(output_stream.fileno(), 0o644)
            else:
                stage.chmod(0o644)
            os.fsync(output_stream.fileno())
            staged_info = descriptor_file_stat(output_stream.fileno())
            if (staged_info.st_dev, staged_info.st_ino) != identity:
                raise ReleaseBuildError(f"release staging identity changed: {stage}")
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        _unlink_owned_file(stage, identity)
        raise
    return stage, identity


def _publish_verified(
    first: tuple[Path, Path], second: tuple[Path, Path], output_dir: Path
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_info = output_dir.lstat()
    if output_dir.is_symlink() or not stat.S_ISDIR(output_info.st_mode):
        raise ReleaseBuildError(f"release output is not a safe directory: {output_dir}")
    verified: list[Path] = []
    for left, right in zip(first, second, strict=True):
        if left.name != right.name or not _same_bytes(left, right):
            raise ReleaseBuildError(f"non-reproducible artifact: {left.name}")
        target = output_dir / left.name
        if _entry_identity(target) is not None:
            raise ReleaseBuildError(f"refusing to replace existing artifact: {target}")
        verified.append(left)

    stages: list[tuple[Path, tuple[int, int]]] = []
    published: list[tuple[Path, tuple[int, int]]] = []
    try:
        for source in verified:
            stages.append(_stage_artifact(source, output_dir))
        for source, (stage, identity) in zip(verified, stages, strict=True):
            target = output_dir / source.name
            try:
                os.link(stage, target)
            except FileExistsError as exc:
                raise ReleaseBuildError(f"refusing to replace existing artifact: {target}") from exc
            except OSError as exc:
                raise ReleaseBuildError(
                    f"could not publish release artifact {target}: {exc}"
                ) from exc
            published.append((target, identity))
            if _entry_identity(target) != identity:
                raise ReleaseBuildError(
                    f"release artifact identity changed during publication: {target}"
                )
        return [path for path, _identity in published]
    except Exception:
        for target, identity in reversed(published):
            _unlink_owned_file(target, identity)
        raise
    finally:
        for stage, identity in stages:
            _unlink_owned_file(stage, identity)


def build_release(repo: Path, output_dir: Path) -> list[Path]:
    repo = repo.resolve()
    output_dir = Path(os.path.abspath(output_dir))
    _require_supported_platform()
    _verify_toolchain()
    commit_oid = _head_oid(repo)
    epoch = _source_date_epoch(repo, commit_oid)
    archive = _git_archive(repo, commit_oid)
    with tempfile.TemporaryDirectory(prefix="rwf-release-") as scratch_text:
        scratch = Path(scratch_text)
        source_a = scratch / "source-a"
        source_b = scratch / "source-b"
        build_a = scratch / "build-a"
        build_b = scratch / "build-b"
        _extract_archive(archive, source_a)
        _extract_archive(archive, source_b)
        first = _build_from_source(source_a, build_a, epoch)
        second = _build_from_source(source_b, build_b, epoch)
        return _publish_verified(first, second, output_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build reproducible World Forge releases")
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory where verified artifacts are published without replacement",
    )
    args = parser.parse_args(argv)
    try:
        artifacts = build_release(ROOT, args.output_dir)
    except ReleaseBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for artifact in artifacts:
        print(f"artifact={artifact} sha256={_sha256(artifact)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
