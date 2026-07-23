#!/usr/bin/env python3
"""Assemble and verify deterministic, non-publishable Studio runtime resources.

The public CLI is intentionally fail-closed while the checked-in provenance
contract has open redistribution blockers.  The assembly core remains directly
testable with explicitly synthetic archives so archive and output invariants do
not depend on downloading or redistributing third-party runtime artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import posixpath
import re
import stat
import sys
import tarfile
import time
import unicodedata
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, NoReturn

try:
    from scripts.studio_runtime_sources import (
        DEFAULT_SOURCE,
        RuntimeSourcesError,
        load_strict_json_bytes,
        require_redistributable,
        validate_document,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from studio_runtime_sources import (  # type: ignore[no-redef]
        DEFAULT_SOURCE,
        RuntimeSourcesError,
        load_strict_json_bytes,
        require_redistributable,
        validate_document,
    )

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_MANIFEST = ROOT / "apps/studio/resources/runtime-manifest.json"
DEFAULT_ARCHIVE_NORMALIZATION = (
    ROOT / "apps/studio/packaging/runtime-archive-normalization-linux-x64.json"
)

PACKAGE_FORMAT = "rpg-world-forge.studio_runtime_package_manifest"
PACKAGE_FORMAT_VERSION = 1
PACKAGE_SCHEMA_ID = (
    "https://rpg-world-forge.local/schemas/studio-runtime-package-manifest.schema.json"
)
LAUNCH_FORMAT = "rpg-world-forge.studio_runtime_manifest"
LAUNCH_VERSION = 3
TARGET_IDS = ("linux-x64", "win32-x64")
CODEX_VERSION = "0.144.6"
PYTHON_VERSION = "3.12.13"
LINUX_PBS_SHA256 = "5854aa6ec71cad00334d5065633c210b2e7feb40956767a59a91791cadcf0b79"
LINUX_PBS_FILENAME = (
    "cpython-3.12.13+20260718-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
)
LINUX_PBS_SIZE = 34_199_823
NORMALIZATION_FORMAT = "rpg-world-forge.studio_runtime_archive_normalization"
NORMALIZATION_FORMAT_VERSION = 1
NORMALIZATION_PACKAGE_PATH = "runtime/python/linux-x64/runtime-archive-normalization.json"
NORMALIZATION_SIZE = 1_031_213
NORMALIZATION_SHA256 = "3c4fea7af2d435c036d412a56d7b762131e780560b339cbffe80e7637416db0e"
RUNTIME_SOURCES_FORMAT = "rpg-world-forge.studio_runtime_sources"
RUNTIME_SOURCES_FORMAT_VERSION = 1
RUNTIME_SOURCES_PACKAGE_PATH = "runtime-sources.json"
RUNTIME_SOURCES_SIZE = 13_717
RUNTIME_SOURCES_SHA256 = "99419da1ccc87cb8ea6c279e7e8e6bbc1d6b4d08eb6a67ae6ac7bf66d1182414"
FORGE_VERSION = "0.7.0"
SERVICE_MODULE = "worldforge.studio"
MCP_MODULE = "worldforge.studio.mcp_server"
PROTOCOL_RELATIVE = PurePosixPath("protocol/codex-app-server-0.144.6/manifest.json")
PACKAGE_MANIFEST_NAME = "runtime-package-manifest.json"
LAUNCH_MANIFEST_NAME = "runtime-manifest.json"

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 200_000
MAX_ARCHIVE_MEMBER_BYTES = 768 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_EXPANSION_RATIO = 128
MAX_ARCHIVE_DIRECTORIES = MAX_ARCHIVE_ENTRIES
MAX_ARCHIVE_NODES = MAX_ARCHIVE_ENTRIES
MAX_PACKAGE_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_PACKAGE_JSON_DEPTH = 4
MAX_PACKAGE_JSON_NODES = 100_000
MAX_PACKAGE_INVENTORY_ENTRIES = 16_384
MAX_PACKAGE_BLOCKER_CODES = 64
MAX_OUTPUT_FILES = MAX_PACKAGE_INVENTORY_ENTRIES
MAX_OUTPUT_DIRECTORIES = MAX_OUTPUT_FILES
MAX_OUTPUT_NODES = MAX_OUTPUT_FILES * 2 + 1
MAX_OUTPUT_BYTES = 6 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_ZIP_BYTES = 6 * 1024 * 1024 * 1024
MAX_PATH_BYTES = 1024
MAX_PATH_DEPTH = 64
MAX_SYMLINK_DEPTH = 64
MAX_UNICODE_STRING_NAME_BYTES = 65_532
READ_CHUNK_BYTES = 1024 * 1024
ZIP_MIN_EPOCH = 315_532_800
ZIP_MAX_EPOCH = 4_354_819_199

# The closed manifest shape contributes six JSON values per inventory row.
# These conservative byte/node envelopes make every schema-maximum inventory
# readable by the package parser rather than relying on typical short paths.
_PACKAGE_INVENTORY_ENTRY_MAX_CANONICAL_BYTES = 1_216
_PACKAGE_NON_INVENTORY_MAX_CANONICAL_BYTES = 1024 * 1024
_PACKAGE_INVENTORY_ENTRY_JSON_NODES = 6
_PACKAGE_NON_INVENTORY_MAX_JSON_NODES = 114
assert (
    MAX_PACKAGE_INVENTORY_ENTRIES * _PACKAGE_INVENTORY_ENTRY_MAX_CANONICAL_BYTES
    + _PACKAGE_NON_INVENTORY_MAX_CANONICAL_BYTES
    <= MAX_PACKAGE_MANIFEST_BYTES
)
assert (
    MAX_PACKAGE_INVENTORY_ENTRIES * _PACKAGE_INVENTORY_ENTRY_JSON_NODES
    + _PACKAGE_NON_INVENTORY_MAX_JSON_NODES
    <= MAX_PACKAGE_JSON_NODES
)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BLOCKER_RE = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
WINDOWS_RESERVED = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
WINDOWS_FORBIDDEN = frozenset('<>:"|?*')

_ReadHook = Callable[[Path, str], None]
_READ_TEST_HOOK: _ReadHook | None = None
_WRITE_TEST_HOOK: _ReadHook | None = None


class RuntimeAssemblyError(ValueError):
    """A redacted, machine-readable assembly contract failure."""

    def __init__(
        self,
        code: str,
        field: str,
        *,
        blockers: Iterable[str] = (),
        exit_code: int = 1,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.blockers = tuple(blockers)
        self.exit_code = exit_code

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "field": self.field}
        if self.blockers:
            result["open_blocker_codes"] = list(self.blockers)
        return result


@dataclass(frozen=True, slots=True)
class FilePin:
    """One exact regular archive member beneath a selected payload root."""

    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CasefoldDirectoryPair:
    """Two intentional case-sensitive archive directory spellings."""

    first: str
    second: str


@dataclass(frozen=True, slots=True)
class CasefoldFilePair:
    """Two intentional case-sensitive archive files retained by receipt."""

    first_sha256: str
    first_mode: int
    first_size: int
    first_source: str
    first_target: str
    second_sha256: str
    second_mode: int
    second_size: int
    second_source: str
    second_target: str


@dataclass(frozen=True, slots=True)
class MaterializationReceipt:
    """One exact regular or symlink-derived output in the pinned archive."""

    link: str | None
    mode: int
    sha256: str
    size: int
    source: str
    source_kind: str
    target: str


@dataclass(frozen=True, slots=True)
class ArchiveNormalization:
    """Exact target/archive receipt for portable regular-file materialization."""

    archive_sha256: str
    casefold_directories: tuple[CasefoldDirectoryPair, ...]
    casefold_files: tuple[CasefoldFilePair, ...]
    component: str
    files: tuple[MaterializationReceipt, ...]
    max_symlink_depth: int
    output_bytes: int
    output_file_count: int
    payload_root: str
    policy: str
    regular_file_count: int
    relative_symlink_count: int
    source_file_count: int
    target_id: str


@dataclass(frozen=True, slots=True)
class ArchiveSpec:
    """One exact source archive and the selected payload rooted within it."""

    component: str
    path: Path
    filename: str
    size: int
    sha256: str
    payload_root: str
    entrypoint: str
    expected_inventory: tuple[FilePin, ...] | None
    normalization: ArchiveNormalization | None = None


@dataclass(frozen=True, slots=True)
class AssemblyPlan:
    """Closed input plan for one explicitly non-publishable resources tree."""

    target_id: str
    assembly_kind: str
    runtime_sources_sha256: str
    source_date_epoch: int
    codex: ArchiveSpec
    python: ArchiveSpec
    forge_source_root: Path
    open_blocker_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AssemblyResult:
    output_root: Path
    manifest: dict[str, Any]
    files: int
    bytes: int


@dataclass(frozen=True, slots=True)
class _PayloadFile:
    path: str
    payload: bytes
    mode: int


@dataclass(frozen=True, slots=True)
class _MaterializedArchiveFile:
    """One selected archive source after safe symlink resolution."""

    output_path: str
    payload: bytes
    mode: int
    source_path: str
    target_path: str


@dataclass(frozen=True, slots=True)
class _OutputFile:
    payload: bytes
    mode: int
    component: str


@dataclass(frozen=True, slots=True)
class _FileState:
    identity: tuple[int, int]
    size: int
    mtime_ns: int
    ctime_ns: int
    mode_type: int
    nlink: int


@dataclass(frozen=True, slots=True)
class _WindowsHandleState:
    identity: tuple[int, int]
    size: int
    nlink: int
    is_directory: bool
    is_reparse: bool


@dataclass(frozen=True, slots=True)
class _WindowsBinding:
    parent: int
    name: str
    handle: int
    identity: tuple[int, int]
    is_directory: bool


def _fail(
    code: str,
    field: str,
    *,
    blockers: Iterable[str] = (),
    exit_code: int = 1,
) -> NoReturn:
    raise RuntimeAssemblyError(code, field, blockers=blockers, exit_code=exit_code)


def _identity(info: os.stat_result) -> tuple[int, int]:
    result = (info.st_dev, info.st_ino)
    if result == (0, 0):
        _fail("filesystem_identity_unavailable", "filesystem")
    return result


def _state(info: os.stat_result) -> _FileState:
    return _FileState(
        identity=_identity(info),
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
        mode_type=stat.S_IFMT(info.st_mode),
        nlink=info.st_nlink,
    )


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _lexical_absolute(path: Path, field: str) -> Path:
    raw = os.fspath(path)
    if (
        type(raw) is not str
        or not path.is_absolute()
        or raw != unicodedata.normalize("NFC", raw)
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw)
    ):
        _fail("invalid_path", field, exit_code=2)
    return Path(os.path.normpath(raw))


def _directory_chain(path: Path, field: str) -> tuple[tuple[Path, tuple[int, int]], ...]:
    absolute = _lexical_absolute(path, field)
    current = Path(absolute.anchor)
    records: list[tuple[Path, tuple[int, int]]] = []
    offset = 1 if absolute.anchor else 0
    if absolute.anchor:
        try:
            info = current.lstat()
        except OSError:
            _fail("unsafe_parent", field)
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            _fail("unsafe_parent", field)
        records.append((current, _identity(info)))
    for component in absolute.parts[offset:]:
        current /= component
        try:
            info = current.lstat()
        except OSError:
            _fail("unsafe_parent", field)
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            _fail("unsafe_parent", field)
        records.append((current, _identity(info)))
    return tuple(records)


def _require_directory_chain(
    records: tuple[tuple[Path, tuple[int, int]], ...],
    field: str,
) -> None:
    for path, expected in records:
        try:
            info = path.lstat()
        except OSError:
            _fail("filesystem_identity_changed", field)
        if (
            _is_link_or_reparse(info)
            or not stat.S_ISDIR(info.st_mode)
            or _identity(info) != expected
        ):
            _fail("filesystem_identity_changed", field)


def _invoke_read_hook(path: Path, phase: str) -> None:
    hook = _READ_TEST_HOOK
    if hook is not None:
        hook(path, phase)


def _invoke_write_hook(path: Path, phase: str) -> None:
    hook = _WRITE_TEST_HOOK
    if hook is not None:
        hook(path, phase)


def _read_pinned_regular(
    path: Path,
    *,
    field: str,
    max_bytes: int,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> bytes:
    absolute = _lexical_absolute(path, field)
    parents = _directory_chain(absolute.parent, field)
    try:
        before = absolute.lstat()
    except OSError:
        _fail("file_missing", field)
    if (
        _is_link_or_reparse(before)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size < 0
        or before.st_size > max_bytes
    ):
        _fail("file_unsafe", field)
    if expected_size is not None and before.st_size != expected_size:
        _fail("file_size_mismatch", field)
    before_state = _state(before)
    _invoke_read_hook(absolute, "after_lstat")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(absolute, flags)
    except OSError:
        _fail("file_unsafe", field)
    try:
        opened = os.fstat(descriptor)
        if (
            _is_link_or_reparse(opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _state(opened) != before_state
        ):
            _fail("filesystem_identity_changed", field)
        payload = bytearray()
        digest = hashlib.sha256()
        while len(payload) < before.st_size:
            chunk = os.read(descriptor, min(READ_CHUNK_BYTES, before.st_size - len(payload)))
            if not chunk:
                _fail("file_truncated", field)
            payload.extend(chunk)
            digest.update(chunk)
        if os.read(descriptor, 1):
            _fail("file_size_mismatch", field)
        _invoke_read_hook(absolute, "before_recheck")
        if _state(os.fstat(descriptor)) != before_state:
            _fail("filesystem_identity_changed", field)
    finally:
        os.close(descriptor)
    try:
        current = absolute.lstat()
    except OSError:
        _fail("filesystem_identity_changed", field)
    if _state(current) != before_state or _is_link_or_reparse(current):
        _fail("filesystem_identity_changed", field)
    _require_directory_chain(parents, field)
    actual_sha256 = digest.hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        _fail("file_digest_mismatch", field)
    return bytes(payload)


def _portable_path(value: object, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or value != unicodedata.normalize("NFC", value)
        or "\\" in value
        or value.startswith("/")
        or len(value.encode("utf-8", "strict")) > MAX_PATH_BYTES
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value)
    ):
        _fail("path_not_portable", field)
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or len(path.parts) > MAX_PATH_DEPTH:
        _fail("path_not_portable", field)
    for component in path.parts:
        lowered = component.casefold()
        if (
            component in {"", ".", ".."}
            or component.endswith((" ", "."))
            or lowered.split(".", 1)[0] in WINDOWS_RESERVED
            or any(character in WINDOWS_FORBIDDEN for character in component)
            or len(component.encode("utf-8", "strict")) > 255
        ):
            _fail("path_not_portable", field)
    return value


def _path_key(value: str) -> tuple[str, ...]:
    return tuple(component.casefold() for component in PurePosixPath(value).parts)


class _NamespaceBudget:
    def __init__(
        self,
        *,
        file_limit: int,
        directory_limit: int,
        node_limit: int,
        code: str,
        field: str,
    ) -> None:
        self.file_limit = file_limit
        self.directory_limit = directory_limit
        self.node_limit = node_limit
        self.code = code
        self.field = field
        self.files: set[str] = set()
        self.directories: set[str] = set()
        self.node_paths: set[str] = set()

    def add_file(self, value: str) -> None:
        parts = PurePosixPath(value).parts
        for count in range(1, len(parts)):
            self._add_directory("/".join(parts[:count]))
        if value in self.files:
            return
        if len(self.files) >= self.file_limit:
            _fail(self.code, self.field)
        self._add_node(value)
        self.files.add(value)

    def add_directory(self, value: str) -> None:
        parts = PurePosixPath(value).parts
        for count in range(1, len(parts) + 1):
            self._add_directory("/".join(parts[:count]))

    def preflight_path(self, value: str) -> None:
        parts = PurePosixPath(value).parts
        for count in range(1, len(parts)):
            self._add_directory("/".join(parts[:count]))
        self._add_node(value)

    def _add_directory(self, value: str) -> None:
        if value in self.directories:
            return
        if len(self.directories) >= self.directory_limit:
            _fail(self.code, self.field)
        self._add_node(value)
        self.directories.add(value)

    def _add_node(self, value: str) -> None:
        if value in self.node_paths:
            return
        if len(self.node_paths) >= self.node_limit:
            _fail(self.code, self.field)
        self.node_paths.add(value)


def _output_namespace_budget(code: str, field: str) -> _NamespaceBudget:
    return _NamespaceBudget(
        file_limit=MAX_OUTPUT_FILES + 1,
        directory_limit=min(MAX_OUTPUT_DIRECTORIES, MAX_OUTPUT_FILES),
        node_limit=min(MAX_OUTPUT_NODES, MAX_OUTPUT_FILES * 2 + 1),
        code=code,
        field=field,
    )


def _archive_namespace_budget(code: str, field: str) -> _NamespaceBudget:
    return _NamespaceBudget(
        file_limit=MAX_ARCHIVE_ENTRIES,
        directory_limit=min(MAX_ARCHIVE_DIRECTORIES, MAX_ARCHIVE_ENTRIES),
        node_limit=min(MAX_ARCHIVE_NODES, MAX_ARCHIVE_ENTRIES),
        code=code,
        field=field,
    )


class _PackageOutputBudget:
    def __init__(self) -> None:
        self.namespace = _output_namespace_budget(
            "output_limit_exceeded",
            "output",
        )
        self.namespace.add_file(PACKAGE_MANIFEST_NAME)
        self.payload_bytes = 0

    @property
    def remaining_bytes(self) -> int:
        return MAX_OUTPUT_BYTES - self.payload_bytes

    def preflight_path(self, path: str) -> None:
        self.namespace.preflight_path(_portable_path(path, "output"))

    def add_directory(self, path: str) -> None:
        self.namespace.add_directory(_portable_path(path, "output"))

    def add_file_path(self, path: str) -> None:
        self.namespace.add_file(_portable_path(path, "output"))

    def require_payload_capacity(self, size: int) -> None:
        if size < 0 or size > self.remaining_bytes:
            _fail("output_limit_exceeded", "output")

    def add_payload_bytes(self, size: int) -> None:
        self.require_payload_capacity(size)
        self.payload_bytes += size

    def add_known_file(self, path: str, size: int) -> None:
        self.add_file_path(path)
        self.add_payload_bytes(size)


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_file_pin(pin: FilePin, field: str) -> None:
    _portable_path(pin.path, f"{field}.path")
    if type(pin.size) is not int or pin.size < 0 or pin.size > MAX_ARCHIVE_MEMBER_BYTES:
        _fail("archive_inventory_invalid", f"{field}.size")
    if type(pin.sha256) is not str or SHA256_RE.fullmatch(pin.sha256) is None:
        _fail("archive_inventory_invalid", f"{field}.sha256")


def _receipt_object(value: object, keys: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        _fail("normalization_receipt_invalid", field)
    return value


def _receipt_int(value: object, field: str, *, maximum: int) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        _fail("normalization_receipt_invalid", field)
    return value


def _validate_archive_normalization(
    normalization: ArchiveNormalization,
    *,
    component: str,
    archive_sha256: str,
    payload_root: str,
    target_id: str | None = None,
) -> None:
    field = "normalization"
    if (
        normalization.component != component
        or normalization.archive_sha256 != archive_sha256
        or normalization.payload_root != payload_root
        or normalization.policy != "materialize_relative_symlinks_preserve_case_sensitive_paths_v1"
        or target_id is not None
        and normalization.target_id != target_id
    ):
        _fail("normalization_receipt_invalid", field)
    if normalization.component != "python" or normalization.target_id != "linux-x64":
        _fail("normalization_receipt_invalid", field)
    if (
        type(normalization.source_file_count) is not int
        or not 1 <= normalization.source_file_count <= MAX_ARCHIVE_ENTRIES
        or type(normalization.regular_file_count) is not int
        or not 1 <= normalization.regular_file_count <= normalization.source_file_count
        or type(normalization.relative_symlink_count) is not int
        or not 0 <= normalization.relative_symlink_count <= normalization.source_file_count
        or normalization.regular_file_count + normalization.relative_symlink_count
        != normalization.source_file_count
        or type(normalization.max_symlink_depth) is not int
        or not 0 <= normalization.max_symlink_depth <= MAX_SYMLINK_DEPTH
        or type(normalization.output_file_count) is not int
        or not 1 <= normalization.output_file_count <= MAX_OUTPUT_FILES
        or type(normalization.output_bytes) is not int
        or not 0 <= normalization.output_bytes <= MAX_OUTPUT_BYTES
    ):
        _fail("normalization_receipt_invalid", field)

    receipt_by_source: dict[str, MaterializationReceipt] = {}
    regular_count = 0
    symlink_count = 0
    output_bytes = 0
    for index, item in enumerate(normalization.files):
        item_field = f"{field}.files[{index}]"
        source = _portable_path(item.source, f"{item_field}.source")
        target = _portable_path(item.target, f"{item_field}.target")
        if (
            source in receipt_by_source
            or item.source_kind not in {"regular", "symlink"}
            or type(item.mode) is not int
            or item.mode not in {0o644, 0o755}
            or type(item.size) is not int
            or not 0 <= item.size <= MAX_ARCHIVE_MEMBER_BYTES
            or type(item.sha256) is not str
            or SHA256_RE.fullmatch(item.sha256) is None
        ):
            _fail("normalization_receipt_invalid", item_field)
        if item.source_kind == "regular":
            regular_count += 1
            if item.link is not None or target != source:
                _fail("normalization_receipt_invalid", item_field)
        else:
            symlink_count += 1
            if type(item.link) is not str or not item.link:
                _fail("normalization_receipt_invalid", item_field)
        receipt_by_source[source] = item
        output_bytes += item.size
    if normalization.files != tuple(
        sorted(normalization.files, key=lambda item: item.source.encode("utf-8"))
    ) or (
        len(normalization.files) != normalization.source_file_count
        or regular_count != normalization.regular_file_count
        or symlink_count != normalization.relative_symlink_count
        or output_bytes != normalization.output_bytes
    ):
        _fail("normalization_receipt_invalid", f"{field}.files")
    observed_max_depth = 0
    for source, item in receipt_by_source.items():
        if item.source_kind != "symlink":
            continue
        current = source
        visited: set[str] = set()
        depth = 0
        while True:
            current_item = receipt_by_source.get(current)
            if current_item is None:
                _fail("normalization_receipt_invalid", f"{field}.files")
            if current_item.source_kind == "regular":
                if (
                    current != item.target
                    or current_item.mode != item.mode
                    or current_item.size != item.size
                    or current_item.sha256 != item.sha256
                ):
                    _fail("normalization_receipt_invalid", f"{field}.files")
                break
            if current in visited or depth >= MAX_SYMLINK_DEPTH:
                _fail("normalization_receipt_invalid", f"{field}.files")
            visited.add(current)
            current = _relative_symlink_target(
                current,
                current_item.link,
                f"{field}.files",
            )
            depth += 1
        observed_max_depth = max(observed_max_depth, depth)
    if observed_max_depth != normalization.max_symlink_depth:
        _fail("normalization_receipt_invalid", f"{field}.files")

    directory_paths: set[str] = set()
    directory_keys: set[tuple[str, ...]] = set()
    for index, pair in enumerate(normalization.casefold_directories):
        first = _portable_path(
            pair.first,
            f"{field}.casefold_directories[{index}].first",
        )
        second = _portable_path(
            pair.second,
            f"{field}.casefold_directories[{index}].second",
        )
        if (
            first == second
            or _path_key(first) != _path_key(second)
            or first.encode("utf-8") >= second.encode("utf-8")
            or first in directory_paths
            or second in directory_paths
            or _path_key(first) in directory_keys
        ):
            _fail("normalization_receipt_invalid", f"{field}.casefold_directories")
        directory_paths.update((first, second))
        directory_keys.add(_path_key(first))
    if normalization.casefold_directories != tuple(
        sorted(normalization.casefold_directories, key=lambda item: item.first.encode("utf-8"))
    ):
        _fail("normalization_receipt_invalid", f"{field}.casefold_directories")
    if _observed_casefold_directories(receipt_by_source) != normalization.casefold_directories:
        _fail("normalization_receipt_invalid", f"{field}.casefold_directories")

    file_sources: set[str] = set()
    file_keys: set[tuple[str, ...]] = set()
    for index, pair in enumerate(normalization.casefold_files):
        item_field = f"{field}.casefold_files[{index}]"
        first_source = _portable_path(pair.first_source, f"{item_field}.first_source")
        second_source = _portable_path(pair.second_source, f"{item_field}.second_source")
        _portable_path(pair.first_target, f"{item_field}.first_target")
        _portable_path(pair.second_target, f"{item_field}.second_target")
        if (
            first_source == second_source
            or first_source.encode("utf-8") >= second_source.encode("utf-8")
            or _path_key(first_source) != _path_key(second_source)
            or first_source in file_sources
            or second_source in file_sources
            or _path_key(first_source) in file_keys
            or type(pair.first_size) is not int
            or not 0 <= pair.first_size <= MAX_ARCHIVE_MEMBER_BYTES
            or type(pair.second_size) is not int
            or not 0 <= pair.second_size <= MAX_ARCHIVE_MEMBER_BYTES
            or type(pair.first_mode) is not int
            or pair.first_mode not in {0o644, 0o755}
            or type(pair.second_mode) is not int
            or pair.second_mode not in {0o644, 0o755}
            or type(pair.first_sha256) is not str
            or SHA256_RE.fullmatch(pair.first_sha256) is None
            or type(pair.second_sha256) is not str
            or SHA256_RE.fullmatch(pair.second_sha256) is None
        ):
            _fail("normalization_receipt_invalid", item_field)
        first_receipt = receipt_by_source.get(first_source)
        second_receipt = receipt_by_source.get(second_source)
        if (
            first_receipt is None
            or second_receipt is None
            or pair.first_target != first_receipt.target
            or pair.second_target != second_receipt.target
            or pair.first_size != first_receipt.size
            or pair.second_size != second_receipt.size
            or pair.first_mode != first_receipt.mode
            or pair.second_mode != second_receipt.mode
            or pair.first_sha256 != first_receipt.sha256
            or pair.second_sha256 != second_receipt.sha256
        ):
            _fail("normalization_receipt_invalid", item_field)
        file_sources.update((first_source, second_source))
        file_keys.add(_path_key(first_source))
    if normalization.casefold_files != tuple(
        sorted(
            normalization.casefold_files,
            key=lambda item: item.first_source.encode("utf-8"),
        )
    ):
        _fail("normalization_receipt_invalid", f"{field}.casefold_files")
    observed_file_groups: set[frozenset[str]] = set()
    grouped_sources: dict[tuple[str, ...], set[str]] = {}
    for source in receipt_by_source:
        grouped_sources.setdefault(_path_key(source), set()).add(source)
    for group in grouped_sources.values():
        if len(group) > 1:
            if len(group) != 2:
                _fail("normalization_receipt_invalid", f"{field}.casefold_files")
            observed_file_groups.add(frozenset(group))
    expected_file_groups = {
        frozenset((pair.first_source, pair.second_source)) for pair in normalization.casefold_files
    }
    if observed_file_groups != expected_file_groups:
        _fail("normalization_receipt_invalid", f"{field}.casefold_files")
    if normalization.output_file_count != normalization.source_file_count:
        _fail("normalization_receipt_invalid", field)


def _parse_archive_normalization(raw: bytes) -> ArchiveNormalization:
    if len(raw) != NORMALIZATION_SIZE or _sha256(raw) != NORMALIZATION_SHA256:
        _fail("normalization_receipt_invalid", "normalization")
    try:
        document = load_strict_json_bytes(raw)
    except RuntimeSourcesError:
        _fail("normalization_receipt_invalid", "normalization")
    if raw != _canonical_json_bytes(document):
        _fail("normalization_receipt_invalid", "normalization")
    root = _receipt_object(
        document,
        {
            "archive_sha256",
            "casefold_directories",
            "casefold_files",
            "component",
            "files",
            "format",
            "format_version",
            "max_symlink_depth",
            "output_bytes",
            "output_file_count",
            "payload_root",
            "policy",
            "regular_file_count",
            "relative_symlink_count",
            "source_file_count",
            "target_id",
        },
        "normalization",
    )
    if (
        root["format"] != NORMALIZATION_FORMAT
        or root["format_version"] != NORMALIZATION_FORMAT_VERSION
        or root["component"] != "python"
        or root["target_id"] != "linux-x64"
        or root["payload_root"] != "python"
        or root["policy"] != "materialize_relative_symlinks_preserve_case_sensitive_paths_v1"
        or type(root["archive_sha256"]) is not str
        or SHA256_RE.fullmatch(root["archive_sha256"]) is None
        or root["archive_sha256"] != LINUX_PBS_SHA256
        or type(root["casefold_directories"]) is not list
        or type(root["casefold_files"]) is not list
        or type(root["files"]) is not list
    ):
        _fail("normalization_receipt_invalid", "normalization")

    directories: list[CasefoldDirectoryPair] = []
    for index, value in enumerate(root["casefold_directories"]):
        item = _receipt_object(
            value,
            {"first", "second"},
            f"normalization.casefold_directories[{index}]",
        )
        if type(item["first"]) is not str or type(item["second"]) is not str:
            _fail(
                "normalization_receipt_invalid",
                f"normalization.casefold_directories[{index}]",
            )
        directories.append(CasefoldDirectoryPair(first=item["first"], second=item["second"]))

    files: list[CasefoldFilePair] = []
    file_keys = {
        "first_sha256",
        "first_mode",
        "first_size",
        "first_source",
        "first_target",
        "second_sha256",
        "second_mode",
        "second_size",
        "second_source",
        "second_target",
    }
    for index, value in enumerate(root["casefold_files"]):
        item = _receipt_object(
            value,
            file_keys,
            f"normalization.casefold_files[{index}]",
        )
        string_keys = file_keys - {
            "first_mode",
            "first_size",
            "second_mode",
            "second_size",
        }
        if any(type(item[key]) is not str for key in string_keys):
            _fail(
                "normalization_receipt_invalid",
                f"normalization.casefold_files[{index}]",
            )
        files.append(
            CasefoldFilePair(
                first_sha256=item["first_sha256"],
                first_mode=_receipt_int(
                    item["first_mode"],
                    f"normalization.casefold_files[{index}].first_mode",
                    maximum=0o755,
                ),
                first_size=_receipt_int(
                    item["first_size"],
                    f"normalization.casefold_files[{index}].first_size",
                    maximum=MAX_ARCHIVE_MEMBER_BYTES,
                ),
                first_source=item["first_source"],
                first_target=item["first_target"],
                second_sha256=item["second_sha256"],
                second_mode=_receipt_int(
                    item["second_mode"],
                    f"normalization.casefold_files[{index}].second_mode",
                    maximum=0o755,
                ),
                second_size=_receipt_int(
                    item["second_size"],
                    f"normalization.casefold_files[{index}].second_size",
                    maximum=MAX_ARCHIVE_MEMBER_BYTES,
                ),
                second_source=item["second_source"],
                second_target=item["second_target"],
            )
        )
    materializations: list[MaterializationReceipt] = []
    materialization_keys = {
        "link",
        "mode",
        "sha256",
        "size",
        "source",
        "source_kind",
        "target",
    }
    for index, value in enumerate(root["files"]):
        item = _receipt_object(
            value,
            materialization_keys,
            f"normalization.files[{index}]",
        )
        if (
            item["link"] is not None
            and type(item["link"]) is not str
            or any(
                type(item[key]) is not str for key in ("sha256", "source", "source_kind", "target")
            )
        ):
            _fail("normalization_receipt_invalid", f"normalization.files[{index}]")
        materializations.append(
            MaterializationReceipt(
                link=item["link"],
                mode=_receipt_int(
                    item["mode"],
                    f"normalization.files[{index}].mode",
                    maximum=0o755,
                ),
                sha256=item["sha256"],
                size=_receipt_int(
                    item["size"],
                    f"normalization.files[{index}].size",
                    maximum=MAX_ARCHIVE_MEMBER_BYTES,
                ),
                source=item["source"],
                source_kind=item["source_kind"],
                target=item["target"],
            )
        )
    normalization = ArchiveNormalization(
        archive_sha256=root["archive_sha256"],
        casefold_directories=tuple(directories),
        casefold_files=tuple(files),
        component=root["component"],
        files=tuple(materializations),
        max_symlink_depth=_receipt_int(
            root["max_symlink_depth"],
            "normalization.max_symlink_depth",
            maximum=MAX_SYMLINK_DEPTH,
        ),
        output_bytes=_receipt_int(
            root["output_bytes"],
            "normalization.output_bytes",
            maximum=MAX_OUTPUT_BYTES,
        ),
        output_file_count=_receipt_int(
            root["output_file_count"],
            "normalization.output_file_count",
            maximum=MAX_OUTPUT_FILES,
        ),
        payload_root=root["payload_root"],
        policy=root["policy"],
        regular_file_count=_receipt_int(
            root["regular_file_count"],
            "normalization.regular_file_count",
            maximum=MAX_ARCHIVE_ENTRIES,
        ),
        relative_symlink_count=_receipt_int(
            root["relative_symlink_count"],
            "normalization.relative_symlink_count",
            maximum=MAX_ARCHIVE_ENTRIES,
        ),
        source_file_count=_receipt_int(
            root["source_file_count"],
            "normalization.source_file_count",
            maximum=MAX_ARCHIVE_ENTRIES,
        ),
        target_id=root["target_id"],
    )
    _validate_archive_normalization(
        normalization,
        component="python",
        archive_sha256=normalization.archive_sha256,
        payload_root="python",
        target_id="linux-x64",
    )
    return normalization


def _load_archive_normalization(
    path: Path = DEFAULT_ARCHIVE_NORMALIZATION,
) -> ArchiveNormalization:
    return _parse_archive_normalization(_read_archive_normalization_bytes(path))


def _read_archive_normalization_bytes(
    path: Path = DEFAULT_ARCHIVE_NORMALIZATION,
) -> bytes:
    return _read_pinned_regular(
        path,
        field="normalization",
        max_bytes=NORMALIZATION_SIZE,
        expected_size=NORMALIZATION_SIZE,
        expected_sha256=NORMALIZATION_SHA256,
    )


def _is_linux_pbs_archive_identity(
    archive: Mapping[str, object],
) -> bool:
    return archive == {
        "entrypoint": "python/bin/python3",
        "filename": LINUX_PBS_FILENAME,
        "payload_root": "python",
        "sha256": LINUX_PBS_SHA256,
        "size": LINUX_PBS_SIZE,
    }


def _normalization_identity() -> dict[str, object]:
    return {
        "archive_sha256": LINUX_PBS_SHA256,
        "format": NORMALIZATION_FORMAT,
        "format_version": NORMALIZATION_FORMAT_VERSION,
        "path": NORMALIZATION_PACKAGE_PATH,
        "sha256": NORMALIZATION_SHA256,
        "size": NORMALIZATION_SIZE,
    }


def _runtime_sources_identity() -> dict[str, object]:
    return {
        "format": RUNTIME_SOURCES_FORMAT,
        "format_version": RUNTIME_SOURCES_FORMAT_VERSION,
        "path": RUNTIME_SOURCES_PACKAGE_PATH,
        "sha256": RUNTIME_SOURCES_SHA256,
        "size": RUNTIME_SOURCES_SIZE,
    }


def _read_runtime_sources_bytes(path: Path = DEFAULT_SOURCE) -> bytes:
    return _read_pinned_regular(
        path,
        field="runtime_sources",
        max_bytes=RUNTIME_SOURCES_SIZE,
        expected_size=RUNTIME_SOURCES_SIZE,
        expected_sha256=RUNTIME_SOURCES_SHA256,
    )


def _parse_runtime_sources_control(raw: bytes) -> dict[str, Any]:
    if len(raw) != RUNTIME_SOURCES_SIZE or _sha256(raw) != RUNTIME_SOURCES_SHA256:
        _fail("runtime_sources_invalid", "runtime_sources")
    try:
        document = load_strict_json_bytes(raw)
        validate_document(document)
    except RuntimeSourcesError:
        _fail("runtime_sources_invalid", "runtime_sources")
    if (
        document.get("format") != RUNTIME_SOURCES_FORMAT
        or document.get("format_version") != RUNTIME_SOURCES_FORMAT_VERSION
    ):
        _fail("runtime_sources_invalid", "runtime_sources")
    return document


def _runtime_source_archive_identities(
    document: Mapping[str, Any],
    target_id: str,
) -> tuple[dict[str, object], dict[str, object], tuple[str, ...]]:
    codex_target = next(
        (item for item in document["codex"]["targets"] if item["target_id"] == target_id),
        None,
    )
    python_target = next(
        (item for item in document["python"]["targets"] if item["target_id"] == target_id),
        None,
    )
    if codex_target is None or python_target is None:
        _fail("runtime_sources_invalid", "runtime_sources")
    codex_archive = codex_target["archive"]
    python_archive = python_target["runtime_archive"]
    codex_identity = {
        "entrypoint": codex_target["entrypoint"],
        "filename": codex_archive["filename"],
        "payload_root": codex_target["payload_root"],
        "sha256": codex_archive["sha256"],
        "size": codex_archive["size"],
    }
    python_identity = {
        "entrypoint": python_target["entrypoint"],
        "filename": python_archive["filename"],
        "payload_root": python_target["payload_root"],
        "sha256": python_archive["sha256"],
        "size": python_archive["size"],
    }
    blocker_codes = tuple(document["redistribution"]["open_blocker_codes"])
    return codex_identity, python_identity, blocker_codes


def _validate_archive_spec(spec: ArchiveSpec, field: str) -> None:
    if spec.component not in {"codex", "python"}:
        _fail("assembly_plan_invalid", f"{field}.component")
    filename = _portable_path(spec.filename, f"{field}.filename")
    if "/" in filename:
        _fail("assembly_plan_invalid", f"{field}.filename")
    if type(spec.size) is not int or not 0 < spec.size <= MAX_ARCHIVE_BYTES:
        _fail("assembly_plan_invalid", f"{field}.size")
    if type(spec.sha256) is not str or SHA256_RE.fullmatch(spec.sha256) is None:
        _fail("assembly_plan_invalid", f"{field}.sha256")
    _portable_path(spec.payload_root, f"{field}.payload_root")
    _portable_path(spec.entrypoint, f"{field}.entrypoint")
    if spec.entrypoint == spec.payload_root or not spec.entrypoint.startswith(
        f"{spec.payload_root}/"
    ):
        _fail("assembly_plan_invalid", f"{field}.entrypoint")
    if spec.normalization is not None:
        _validate_archive_normalization(
            spec.normalization,
            component=spec.component,
            archive_sha256=spec.sha256,
            payload_root=spec.payload_root,
        )
    if spec.expected_inventory is not None:
        keys: set[tuple[str, ...]] = set()
        exact: set[str] = set()
        for index, pin in enumerate(spec.expected_inventory):
            _validate_file_pin(pin, f"{field}.expected_inventory[{index}]")
            if pin.path in exact or _path_key(pin.path) in keys:
                _fail("archive_inventory_invalid", f"{field}.expected_inventory")
            exact.add(pin.path)
            keys.add(_path_key(pin.path))
        relative_entrypoint = spec.entrypoint.removeprefix(f"{spec.payload_root}/")
        if relative_entrypoint not in exact:
            _fail("assembly_plan_invalid", f"{field}.entrypoint")


def _validate_plan(plan: AssemblyPlan) -> None:
    if plan.target_id not in TARGET_IDS:
        _fail("unsupported_target", "target", exit_code=2)
    if plan.assembly_kind not in {"synthetic_test_fixture", "verified_development_runtime"}:
        _fail("assembly_plan_invalid", "assembly_kind")
    if SHA256_RE.fullmatch(plan.runtime_sources_sha256) is None:
        _fail("assembly_plan_invalid", "runtime_sources_sha256")
    if (
        type(plan.source_date_epoch) is not int
        or not ZIP_MIN_EPOCH <= plan.source_date_epoch <= ZIP_MAX_EPOCH
    ):
        _fail("source_date_epoch_invalid", "source_date_epoch", exit_code=2)
    if (
        not plan.open_blocker_codes
        or len(plan.open_blocker_codes) > MAX_PACKAGE_BLOCKER_CODES
        or any(BLOCKER_RE.fullmatch(code) is None for code in plan.open_blocker_codes)
    ):
        _fail("assembly_plan_invalid", "open_blocker_codes")
    if len(set(plan.open_blocker_codes)) != len(plan.open_blocker_codes):
        _fail("assembly_plan_invalid", "open_blocker_codes")
    _validate_archive_spec(plan.codex, "codex")
    _validate_archive_spec(plan.python, "python")
    if plan.codex.component != "codex" or plan.python.component != "python":
        _fail("assembly_plan_invalid", "archives")
    for spec in (plan.codex, plan.python):
        if spec.normalization is not None:
            _validate_archive_normalization(
                spec.normalization,
                component=spec.component,
                archive_sha256=spec.sha256,
                payload_root=spec.payload_root,
                target_id=plan.target_id,
            )


def _tar_member_name(member: tarfile.TarInfo, index: int) -> str:
    name = member.name
    if not isinstance(name, str):
        _fail("archive_member_unsafe", f"archive.members[{index}]")
    return _portable_path(name.removesuffix("/"), f"archive.members[{index}].name")


def _selected_relative(name: str, payload_root: str) -> str | None:
    if name == payload_root:
        return None
    prefix = f"{payload_root}/"
    if not name.startswith(prefix):
        return None
    return name[len(prefix) :]


def _observed_casefold_directories(
    paths: Iterable[str],
) -> tuple[CasefoldDirectoryPair, ...]:
    prefixes: dict[tuple[str, ...], set[str]] = {}
    for path in paths:
        parts = PurePosixPath(path).parts[:-1]
        for count in range(1, len(parts) + 1):
            prefix = "/".join(parts[:count])
            prefixes.setdefault(_path_key(prefix), set()).add(prefix)
    pairs: list[CasefoldDirectoryPair] = []
    for group in prefixes.values():
        if len(group) == 1:
            continue
        if len(group) != 2:
            _fail("archive_member_collision", "archive.payload")
        first, second = sorted(group, key=lambda item: item.encode("utf-8"))
        pairs.append(CasefoldDirectoryPair(first=first, second=second))
    return tuple(sorted(pairs, key=lambda item: item.first.encode("utf-8")))


def _relative_symlink_target(source: str, link: object, field: str) -> str:
    if type(link) is not str or not link:
        _fail("archive_symlink_invalid", field)
    try:
        encoded = link.encode("utf-8", "strict")
    except UnicodeError:
        _fail("archive_symlink_invalid", field)
    if (
        len(encoded) > MAX_PATH_BYTES
        or unicodedata.normalize("NFC", link) != link
        or "\\" in link
        or "\x00" in link
        or any(ord(character) < 32 or ord(character) == 127 for character in link)
        or link.startswith("/")
        or PureWindowsPath(link).drive
    ):
        _fail(
            "archive_symlink_absolute"
            if link.startswith("/") or PureWindowsPath(link).drive
            else "archive_symlink_invalid",
            field,
        )
    components = link.split("/")
    if any(component == "" for component in components) or len(components) > MAX_PATH_DEPTH:
        _fail("archive_symlink_invalid", field)
    target = posixpath.normpath(posixpath.join(posixpath.dirname(source), link))
    if target == ".." or target.startswith("../") or target in {"", "."}:
        _fail("archive_symlink_outside_payload", field)
    return _portable_path(target, field)


def _resolve_archive_source(
    source: str,
    members: Mapping[str, tarfile.TarInfo],
    field: str,
) -> tuple[str, int]:
    current = source
    visited: set[str] = set()
    depth = 0
    while True:
        member = members.get(current)
        if member is None:
            _fail("archive_symlink_missing", field)
        if member.isreg():
            return current, depth
        if not member.issym():
            _fail("archive_symlink_target_unsafe", field)
        if current in visited:
            _fail("archive_symlink_cycle", field)
        visited.add(current)
        if depth >= MAX_SYMLINK_DEPTH:
            _fail("archive_symlink_depth_limit", field)
        current = _relative_symlink_target(current, member.linkname, field)
        depth += 1


def _check_archive_expansion(expanded: int, archive_bytes: int, field: str) -> None:
    if (
        expanded > MAX_ARCHIVE_EXPANDED_BYTES
        or expanded > max(archive_bytes, 1) * MAX_ARCHIVE_EXPANSION_RATIO
    ):
        _fail("archive_expansion_limit", field)


def _validate_casefold_files(
    materialized: list[_MaterializedArchiveFile],
    normalization: ArchiveNormalization | None,
    field: str,
) -> None:
    groups: dict[tuple[str, ...], list[_MaterializedArchiveFile]] = {}
    for item in materialized:
        groups.setdefault(_path_key(item.output_path), []).append(item)
    collisions = {
        key: sorted(group, key=lambda item: item.source_path.encode("utf-8"))
        for key, group in groups.items()
        if len(group) > 1
    }
    if normalization is None:
        if collisions:
            _fail("archive_member_collision", field)
        return

    observed: list[CasefoldFilePair] = []
    for group in collisions.values():
        if len(group) != 2:
            _fail("normalization_receipt_mismatch", field)
        first, second = group
        observed.append(
            CasefoldFilePair(
                first_sha256=_sha256(first.payload),
                first_mode=first.mode,
                first_size=len(first.payload),
                first_source=first.source_path,
                first_target=first.target_path,
                second_sha256=_sha256(second.payload),
                second_mode=second.mode,
                second_size=len(second.payload),
                second_source=second.source_path,
                second_target=second.target_path,
            )
        )
    if (
        tuple(sorted(observed, key=lambda item: item.first_source.encode("utf-8")))
        != normalization.casefold_files
    ):
        _fail("normalization_receipt_mismatch", field)


def _read_archive_payload(spec: ArchiveSpec) -> tuple[_PayloadFile, ...]:
    raw = _read_pinned_regular(
        spec.path,
        field=f"archive.{spec.component}",
        max_bytes=MAX_ARCHIVE_BYTES,
        expected_size=spec.size,
        expected_sha256=spec.sha256,
    )
    all_exact: set[str] = set()
    all_casefold: dict[tuple[str, ...], list[str]] = {}
    selected_members: dict[str, tarfile.TarInfo] = {}
    selected_full_names: set[str] = set()
    expanded = 0
    entries = 0
    namespace = _archive_namespace_budget(
        "archive_entry_limit",
        f"archive.{spec.component}",
    )
    try:
        archive = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
    except (tarfile.TarError, OSError):
        _fail("archive_invalid", f"archive.{spec.component}")
    try:
        for member in archive:
            entries += 1
            if entries > MAX_ARCHIVE_ENTRIES:
                _fail("archive_entry_limit", f"archive.{spec.component}")
            name = _tar_member_name(member, entries - 1)
            if member.isdir():
                namespace.add_directory(name)
            else:
                namespace.add_file(name)
            key = _path_key(name)
            if name in all_exact:
                _fail("archive_member_collision", f"archive.{spec.component}")
            all_exact.add(name)
            all_casefold.setdefault(key, []).append(name)
            relative = _selected_relative(name, spec.payload_root)
            if member.islnk() or member.isdev() or member.isfifo():
                _fail("archive_member_unsafe", f"archive.{spec.component}")
            if not (member.isdir() or member.isreg() or member.issym()):
                _fail("archive_member_unsafe", f"archive.{spec.component}")
            if getattr(member, "sparse", None):
                _fail("archive_member_unsafe", f"archive.{spec.component}")
            if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                _fail("archive_member_size_limit", f"archive.{spec.component}")
            if (member.isdir() or member.issym()) and member.size != 0:
                _fail("archive_member_unsafe", f"archive.{spec.component}")
            if member.isreg():
                expanded += member.size
                _check_archive_expansion(expanded, len(raw), f"archive.{spec.component}")
            if relative is None:
                if member.issym():
                    _fail("archive_member_unsafe", f"archive.{spec.component}")
                continue
            if member.isdir():
                continue
            relative = _portable_path(relative, f"archive.{spec.component}.payload")
            if relative in selected_members:
                _fail("archive_member_collision", f"archive.{spec.component}")
            if member.issym() and spec.normalization is None:
                _fail("archive_member_unsafe", f"archive.{spec.component}")
            selected_members[relative] = member
            selected_full_names.add(name)

        for group in all_casefold.values():
            if len(group) > 1 and (
                spec.normalization is None or any(name not in selected_full_names for name in group)
            ):
                _fail("archive_member_collision", f"archive.{spec.component}")
        if not selected_members:
            _fail("archive_payload_missing", f"archive.{spec.component}")
        source_paths = set(selected_members)
        for source in source_paths:
            for parent in PurePosixPath(source).parents:
                parent_text = parent.as_posix()
                if parent_text == ".":
                    break
                if parent_text in source_paths:
                    _fail("archive_member_collision", f"archive.{spec.component}")

        observed_directories = _observed_casefold_directories(source_paths)
        if spec.normalization is None:
            if observed_directories:
                _fail("archive_member_collision", f"archive.{spec.component}")
        elif observed_directories != spec.normalization.casefold_directories:
            _fail("normalization_receipt_mismatch", f"archive.{spec.component}")

        regular_payloads: dict[str, bytes] = {}
        for source in sorted(source_paths, key=lambda item: item.encode("utf-8")):
            member = selected_members[source]
            if not member.isreg():
                continue
            stream = archive.extractfile(member)
            if stream is None:
                _fail("archive_member_unsafe", f"archive.{spec.component}")
            payload = stream.read(member.size + 1)
            if len(payload) != member.size:
                _fail("archive_member_size_mismatch", f"archive.{spec.component}")
            regular_payloads[source] = payload

        materialized: list[_MaterializedArchiveFile] = []
        symlink_count = 0
        max_symlink_depth = 0
        for source in sorted(source_paths, key=lambda item: item.encode("utf-8")):
            member = selected_members[source]
            target, depth = _resolve_archive_source(
                source,
                selected_members,
                f"archive.{spec.component}.payload",
            )
            if member.issym():
                symlink_count += 1
                expanded += len(regular_payloads[target])
                _check_archive_expansion(expanded, len(raw), f"archive.{spec.component}")
            max_symlink_depth = max(max_symlink_depth, depth)
            target_member = selected_members[target]
            materialized.append(
                _MaterializedArchiveFile(
                    output_path=source,
                    payload=regular_payloads[target],
                    mode=0o755 if target_member.mode & 0o111 else 0o644,
                    source_path=source,
                    target_path=target,
                )
            )
        _validate_casefold_files(
            materialized,
            spec.normalization,
            f"archive.{spec.component}",
        )
        actual_receipt = tuple(
            MaterializationReceipt(
                link=(
                    selected_members[item.source_path].linkname
                    if selected_members[item.source_path].issym()
                    else None
                ),
                mode=item.mode,
                sha256=_sha256(item.payload),
                size=len(item.payload),
                source=item.source_path,
                source_kind=(
                    "symlink" if selected_members[item.source_path].issym() else "regular"
                ),
                target=item.target_path,
            )
            for item in materialized
        )
        if spec.normalization is not None and (
            len(selected_members) != spec.normalization.source_file_count
            or len(regular_payloads) != spec.normalization.regular_file_count
            or symlink_count != spec.normalization.relative_symlink_count
            or max_symlink_depth != spec.normalization.max_symlink_depth
            or len(materialized) != spec.normalization.output_file_count
            or sum(len(item.payload) for item in materialized) != spec.normalization.output_bytes
            or actual_receipt != spec.normalization.files
        ):
            _fail("normalization_receipt_mismatch", f"archive.{spec.component}")
    except (RuntimeAssemblyError, MemoryError):
        raise
    except (tarfile.TarError, OSError, UnicodeError):
        _fail("archive_invalid", f"archive.{spec.component}")
    finally:
        archive.close()
    selected = [
        _PayloadFile(path=item.output_path, payload=item.payload, mode=item.mode)
        for item in materialized
    ]
    selected.sort(key=lambda item: item.path.encode("utf-8"))
    relative_entrypoint = spec.entrypoint.removeprefix(f"{spec.payload_root}/")
    if relative_entrypoint not in {item.path for item in selected}:
        _fail("archive_entrypoint_missing", f"archive.{spec.component}")
    if spec.expected_inventory is not None:
        actual = tuple(
            FilePin(path=item.path, size=len(item.payload), sha256=_sha256(item.payload))
            for item in selected
        )
        expected = tuple(
            sorted(spec.expected_inventory, key=lambda item: item.path.encode("utf-8"))
        )
        if actual != expected:
            _fail("archive_inventory_mismatch", f"archive.{spec.component}")
    return tuple(selected)


def _source_files(
    source_root: Path,
    target_id: str,
    budget: _PackageOutputBudget,
) -> tuple[_PayloadFile, ...]:
    root = _lexical_absolute(source_root, "forge_source_root")
    root_chain = _directory_chain(root, "forge_source_root")
    site_packages = (
        f"runtime/python/{target_id}/Lib/site-packages"
        if target_id == "win32-x64"
        else f"runtime/python/{target_id}/lib/python3.12/site-packages"
    )
    prefix = f"runtime/python/{target_id}"
    specifications = (
        (root / "src/isoworld", f"{site_packages}/isoworld", frozenset({".py"})),
        (
            root / "src/worldforge",
            f"{site_packages}/worldforge",
            frozenset({".py", ".tmpl"}),
        ),
        (root / "schemas", f"{prefix}/share/rpg-world-forge/schemas", frozenset({".json"})),
        (
            root / "contracts",
            f"{prefix}/share/rpg-world-forge/contracts",
            frozenset({".json", ".md"}),
        ),
        (
            root / "apps/studio/protocol/codex-app-server-0.144.6",
            "protocol/codex-app-server-0.144.6",
            frozenset({".json", ".ts"}),
        ),
    )
    result: list[_PayloadFile] = []
    aliases: set[tuple[str, ...]] = set()
    for source, destination, suffixes in specifications:
        destination = _portable_path(destination, "forge_source_root")
        budget.add_directory(destination)
        try:
            info = source.lstat()
        except OSError:
            _fail("forge_material_missing", "forge_source_root")
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            _fail("forge_material_unsafe", "forge_source_root")
        stack: list[tuple[Path, PurePosixPath]] = [(source, PurePosixPath("."))]
        while stack:
            current, relative = stack.pop()
            child_directories: list[tuple[bytes, Path, PurePosixPath]] = []
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        child_relative = (
                            PurePosixPath(entry.name)
                            if relative == PurePosixPath(".")
                            else relative / entry.name
                        )
                        output_path = _portable_path(
                            f"{destination}/{child_relative.as_posix()}",
                            "forge_source_root",
                        )
                        budget.preflight_path(output_path)
                        entry_info = entry.stat(follow_symlinks=False)
                        if _is_link_or_reparse(entry_info):
                            _fail("forge_material_unsafe", "forge_source_root")
                        source_file = Path(entry.path)
                        if stat.S_ISDIR(entry_info.st_mode):
                            budget.add_directory(output_path)
                            if entry.name != "__pycache__":
                                child_directories.append(
                                    (
                                        os.fsencode(entry.name),
                                        source_file,
                                        child_relative,
                                    )
                                )
                            continue
                        if not (stat.S_ISREG(entry_info.st_mode) and entry_info.st_nlink == 1):
                            _fail("forge_material_unsafe", "forge_source_root")
                        budget.add_file_path(output_path)
                        if source_file.suffix == ".pyc" or "__pycache__" in source_file.parts:
                            _fail("forge_bytecode_forbidden", "forge_source_root")
                        if source_file.suffix not in suffixes:
                            continue
                        budget.require_payload_capacity(entry_info.st_size)
                        key = _path_key(output_path)
                        if key in aliases:
                            _fail("forge_material_collision", "forge_source_root")
                        aliases.add(key)
                        payload = _read_pinned_regular(
                            source_file,
                            field="forge_source_root",
                            max_bytes=min(
                                MAX_MANIFEST_BYTES,
                                budget.remaining_bytes,
                            ),
                        )
                        budget.add_payload_bytes(len(payload))
                        result.append(_PayloadFile(output_path, payload, 0o644))
            except RuntimeAssemblyError:
                raise
            except OSError:
                _fail("forge_material_unsafe", "forge_source_root")
            for _, child, child_relative in sorted(
                child_directories,
                key=lambda item: item[0],
                reverse=True,
            ):
                stack.append((child, child_relative))
    for source_name, destination_name in (
        ("LICENSE", "LICENSE"),
        ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
    ):
        output_path = _portable_path(
            f"{prefix}/share/rpg-world-forge/{destination_name}",
            "forge_source_root",
        )
        budget.preflight_path(output_path)
        budget.add_file_path(output_path)
        key = _path_key(output_path)
        if key in aliases:
            _fail("forge_material_collision", "forge_source_root")
        aliases.add(key)
        source_path = root / source_name
        try:
            source_info = source_path.lstat()
        except OSError:
            _fail("forge_material_missing", "forge_source_root")
        if (
            _is_link_or_reparse(source_info)
            or not stat.S_ISREG(source_info.st_mode)
            or source_info.st_nlink != 1
        ):
            _fail("forge_material_unsafe", "forge_source_root")
        budget.require_payload_capacity(source_info.st_size)
        payload = _read_pinned_regular(
            source_path,
            field="forge_source_root",
            max_bytes=min(MAX_MANIFEST_BYTES, budget.remaining_bytes),
        )
        budget.add_payload_bytes(len(payload))
        result.append(_PayloadFile(output_path, payload, 0o644))
    _require_directory_chain(root_chain, "forge_source_root")
    if not any(item.path.endswith("/isoworld/__init__.py") for item in result):
        _fail("forge_material_missing", "forge_source_root")
    if not any(item.path.endswith("/worldforge/studio/__main__.py") for item in result):
        _fail("forge_material_missing", "forge_source_root")
    if not any(item.path == PROTOCOL_RELATIVE.as_posix() for item in result):
        _fail("forge_material_missing", "forge_source_root")
    result.sort(key=lambda item: item.path.encode("utf-8"))
    return tuple(result)


def _launch_paths(target_id: str) -> tuple[str, str]:
    if target_id == "linux-x64":
        return (
            "runtime/codex/linux-x64/bin/codex",
            "runtime/python/linux-x64/bin/python3",
        )
    if target_id == "win32-x64":
        return (
            "runtime/codex/win32-x64/bin/codex.exe",
            "runtime/python/win32-x64/python.exe",
        )
    _fail("unsupported_target", "target", exit_code=2)


def _launch_manifest(target_id: str) -> dict[str, Any]:
    if target_id not in TARGET_IDS:
        _fail("unsupported_target", "target", exit_code=2)
    return {
        "codex": {
            "linux_x64": "runtime/codex/linux-x64/bin/codex",
            "version": CODEX_VERSION,
            "win32_x64": "runtime/codex/win32-x64/bin/codex.exe",
        },
        "codex_protocol": {
            "manifest": PROTOCOL_RELATIVE.as_posix(),
            "version": CODEX_VERSION,
        },
        "format": LAUNCH_FORMAT,
        "package_manifest": {
            "format_version": PACKAGE_FORMAT_VERSION,
            "path": PACKAGE_MANIFEST_NAME,
        },
        "python": {
            "linux_x64": "runtime/python/linux-x64/bin/python3",
            "mcp_module": MCP_MODULE,
            "service_module": SERVICE_MODULE,
            "win32_x64": "runtime/python/win32-x64/python.exe",
        },
        "version": LAUNCH_VERSION,
    }


def _insert_output(
    files: dict[str, _OutputFile],
    aliases: dict[tuple[str, ...], set[str]],
    allowed_casefold: Mapping[tuple[str, ...], frozenset[str]],
    path: str,
    payload: bytes,
    mode: int,
    component: str,
    namespace: _NamespaceBudget | None = None,
) -> None:
    portable = _portable_path(path, f"output.{component}")
    if portable not in files and len(files) >= MAX_OUTPUT_FILES:
        _fail("output_limit_exceeded", "output")
    if namespace is not None:
        namespace.add_file(portable)
    key = _path_key(portable)
    existing = aliases.setdefault(key, set())
    combined = frozenset((*existing, portable))
    if (
        portable in files
        or existing
        and (key not in allowed_casefold or not combined.issubset(allowed_casefold[key]))
    ):
        _fail("output_path_collision", f"output.{component}")
    existing.add(portable)
    files[portable] = _OutputFile(payload=payload, mode=mode, component=component)


def _case_sensitive_output_groups(
    normalization: ArchiveNormalization | None,
    prefix: str,
    *,
    include_directories: bool,
) -> dict[tuple[str, ...], frozenset[str]]:
    if normalization is None:
        return {}
    groups = _case_sensitive_file_groups(normalization, prefix)
    if include_directories:
        directory_groups = _case_sensitive_directory_groups(normalization, prefix)
        if set(groups).intersection(directory_groups):
            _fail("normalization_receipt_invalid", "normalization")
        groups.update(directory_groups)
    return groups


def _case_sensitive_file_groups(
    normalization: ArchiveNormalization | None,
    prefix: str,
) -> dict[tuple[str, ...], frozenset[str]]:
    if normalization is None:
        return {}
    return {
        _path_key(f"{prefix}/{pair.first_source}"): frozenset(
            (f"{prefix}/{pair.first_source}", f"{prefix}/{pair.second_source}")
        )
        for pair in normalization.casefold_files
    }


def _case_sensitive_directory_groups(
    normalization: ArchiveNormalization | None,
    prefix: str,
) -> dict[tuple[str, ...], frozenset[str]]:
    if normalization is None:
        return {}
    return {
        _path_key(f"{prefix}/{pair.first}"): frozenset(
            (f"{prefix}/{pair.first}", f"{prefix}/{pair.second}")
        )
        for pair in normalization.casefold_directories
    }


def _validate_portable_path_aliases(
    paths: Iterable[str],
    *,
    normalization: ArchiveNormalization | None,
    prefix: str,
    code: str,
    field: str,
) -> None:
    allowed_files = _case_sensitive_file_groups(normalization, prefix)
    allowed_directories = _case_sensitive_directory_groups(normalization, prefix)
    files: dict[tuple[str, ...], set[str]] = {}
    directories: dict[tuple[str, ...], set[str]] = {}
    exact_files: set[str] = set()
    for index, value in enumerate(paths):
        portable = _portable_path(value, f"{field}[{index}]")
        if portable in exact_files:
            _fail(code, field)
        exact_files.add(portable)
        files.setdefault(_path_key(portable), set()).add(portable)
        parts = PurePosixPath(portable).parts[:-1]
        for count in range(1, len(parts) + 1):
            directory = "/".join(parts[:count])
            directories.setdefault(_path_key(directory), set()).add(directory)

    if set(files).intersection(directories):
        _fail(code, field)
    for observed, allowed in (
        (files, allowed_files),
        (directories, allowed_directories),
    ):
        collisions = {key: frozenset(values) for key, values in observed.items() if len(values) > 1}
        if collisions != allowed:
            _fail(code, field)


def _normalization_control_payload(plan: AssemblyPlan) -> bytes | None:
    if plan.python.sha256 != LINUX_PBS_SHA256:
        return None
    if (
        plan.target_id != "linux-x64"
        or plan.python.normalization is None
        or not _is_linux_pbs_archive_identity(_archive_identity(plan.python))
    ):
        _fail("assembly_plan_invalid", "python.normalization")
    payload = _read_archive_normalization_bytes()
    if plan.python.normalization != _parse_archive_normalization(payload):
        _fail("normalization_receipt_invalid", "python.normalization")
    return payload


def _runtime_sources_control_payload(plan: AssemblyPlan) -> bytes | None:
    if plan.assembly_kind == "synthetic_test_fixture":
        return None
    payload = _read_runtime_sources_bytes()
    document = _parse_runtime_sources_control(payload)
    codex_identity, python_identity, blocker_codes = _runtime_source_archive_identities(
        document,
        plan.target_id,
    )
    if (
        plan.runtime_sources_sha256 != RUNTIME_SOURCES_SHA256
        or _archive_identity(plan.codex) != codex_identity
        or _archive_identity(plan.python) != python_identity
        or plan.open_blocker_codes != blocker_codes
    ):
        _fail("assembly_plan_invalid", "runtime_sources")
    return payload


def _output_files(plan: AssemblyPlan) -> dict[str, _OutputFile]:
    codex_payload = _read_archive_payload(plan.codex)
    python_payload = _read_archive_payload(plan.python)
    codex_entrypoint = plan.codex.entrypoint.removeprefix(f"{plan.codex.payload_root}/")
    codex_outputs = [
        (
            f"runtime/codex/{plan.target_id}/{item.path}",
            item,
            0o755 if item.path == codex_entrypoint else item.mode,
        )
        for item in codex_payload
    ]
    python_prefix = f"{plan.python.payload_root}/"
    python_outputs: list[tuple[str, _PayloadFile, int]] = []
    for item in python_payload:
        source_path = f"{plan.python.payload_root}/{item.path}"
        if not source_path.startswith(python_prefix):
            _fail("archive_payload_invalid", "archive.python")
        python_outputs.append(
            (
                f"runtime/python/{plan.target_id}/{item.path}",
                item,
                0o755 if source_path == plan.python.entrypoint else item.mode,
            )
        )
    control_outputs = [
        (
            LAUNCH_MANIFEST_NAME,
            _canonical_json_bytes(_launch_manifest(plan.target_id)),
        )
    ]
    runtime_sources_payload = _runtime_sources_control_payload(plan)
    if runtime_sources_payload is not None:
        control_outputs.append((RUNTIME_SOURCES_PACKAGE_PATH, runtime_sources_payload))
    normalization_payload = _normalization_control_payload(plan)
    if normalization_payload is not None:
        control_outputs.append((NORMALIZATION_PACKAGE_PATH, normalization_payload))

    budget = _PackageOutputBudget()
    for path, item, _ in (*codex_outputs, *python_outputs):
        budget.add_known_file(path, len(item.payload))
    for path, payload in control_outputs:
        budget.add_known_file(path, len(payload))
    source_payload = _source_files(
        plan.forge_source_root,
        plan.target_id,
        budget,
    )
    files: dict[str, _OutputFile] = {}
    aliases: dict[tuple[str, ...], set[str]] = {}
    namespace = budget.namespace
    allowed_casefold = _case_sensitive_output_groups(
        plan.python.normalization,
        f"runtime/python/{plan.target_id}",
        include_directories=False,
    )
    for path, item, mode in codex_outputs:
        _insert_output(
            files,
            aliases,
            allowed_casefold,
            path,
            item.payload,
            mode,
            "codex",
            namespace,
        )
    for path, item, mode in python_outputs:
        _insert_output(
            files,
            aliases,
            allowed_casefold,
            path,
            item.payload,
            mode,
            "python",
            namespace,
        )
    for item in source_payload:
        _insert_output(
            files,
            aliases,
            allowed_casefold,
            item.path,
            item.payload,
            item.mode,
            "forge",
            namespace,
        )
    for path, payload in control_outputs:
        _insert_output(
            files,
            aliases,
            allowed_casefold,
            path,
            payload,
            0o644,
            "control",
            namespace,
        )
    _validate_portable_path_aliases(
        files,
        normalization=plan.python.normalization,
        prefix=f"runtime/python/{plan.target_id}",
        code="output_path_collision",
        field="output",
    )
    if len(files) > MAX_OUTPUT_FILES or sum(len(item.payload) for item in files.values()) > (
        MAX_OUTPUT_BYTES
    ):
        _fail("output_limit_exceeded", "output")
    return files


def _archive_identity(spec: ArchiveSpec) -> dict[str, Any]:
    return {
        "entrypoint": spec.entrypoint,
        "filename": spec.filename,
        "payload_root": spec.payload_root,
        "sha256": spec.sha256,
        "size": spec.size,
    }


def _package_manifest(plan: AssemblyPlan, files: Mapping[str, _OutputFile]) -> dict[str, Any]:
    codex_launch, python_launch = _launch_paths(plan.target_id)
    inventory = [
        {
            "component": item.component,
            "mode": item.mode,
            "path": path,
            "sha256": _sha256(item.payload),
            "size": len(item.payload),
        }
        for path, item in sorted(files.items(), key=lambda item: item[0].encode("utf-8"))
    ]
    forge_inventory = [entry for entry in inventory if entry["component"] == "forge"]
    return {
        "assembly_kind": plan.assembly_kind,
        "format": PACKAGE_FORMAT,
        "format_version": PACKAGE_FORMAT_VERSION,
        "inventory": inventory,
        "launch": {
            "codex": codex_launch,
            "mcp_module": MCP_MODULE,
            "python": python_launch,
            "service_module": SERVICE_MODULE,
        },
        "open_blocker_codes": list(plan.open_blocker_codes),
        "release_ready": False,
        "redistribution_status": "blocked",
        "schema_id": PACKAGE_SCHEMA_ID,
        "source_date_epoch": plan.source_date_epoch,
        "sources": {
            "codex": {
                "archive": _archive_identity(plan.codex),
                "version": CODEX_VERSION,
            },
            "forge": {
                "inventory_sha256": _sha256(_canonical_json_bytes(forge_inventory)),
                "version": FORGE_VERSION,
            },
            "python": {
                "archive": _archive_identity(plan.python),
                "normalization": (
                    _normalization_identity() if plan.python.sha256 == LINUX_PBS_SHA256 else None
                ),
                "version": PYTHON_VERSION,
            },
            "runtime_sources": (
                _runtime_sources_identity()
                if plan.assembly_kind == "verified_development_runtime"
                else None
            ),
            "runtime_sources_sha256": plan.runtime_sources_sha256,
        },
        "target_id": plan.target_id,
    }


def _manifest_object(value: object, keys: set[str], field: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        _fail("package_manifest_invalid", field)
    return value


def _manifest_uses_linux_pbs(value: object) -> bool:
    if type(value) is not dict or value.get("target_id") != "linux-x64":
        return False
    sources = value.get("sources")
    if type(sources) is not dict:
        return False
    python_source = sources.get("python")
    if type(python_source) is not dict:
        return False
    archive = python_source.get("archive")
    return type(archive) is dict and archive.get("sha256") == LINUX_PBS_SHA256


def validate_package_manifest(
    value: object,
    *,
    normalization_receipt: bytes | None = None,
    runtime_sources_provenance: bytes | None = None,
) -> dict[str, Any]:
    """Validate the strict runtime package manifest and canonical inventory."""

    root = _manifest_object(
        value,
        {
            "assembly_kind",
            "format",
            "format_version",
            "inventory",
            "launch",
            "open_blocker_codes",
            "release_ready",
            "redistribution_status",
            "schema_id",
            "source_date_epoch",
            "sources",
            "target_id",
        },
        "$",
    )
    if (
        root["format"] != PACKAGE_FORMAT
        or root["format_version"] != PACKAGE_FORMAT_VERSION
        or root["schema_id"] != PACKAGE_SCHEMA_ID
        or root["assembly_kind"] not in {"synthetic_test_fixture", "verified_development_runtime"}
        or root["target_id"] not in TARGET_IDS
        or root["release_ready"] is not False
        or root["redistribution_status"] != "blocked"
        or type(root["source_date_epoch"]) is not int
        or not ZIP_MIN_EPOCH <= root["source_date_epoch"] <= ZIP_MAX_EPOCH
    ):
        _fail("package_manifest_invalid", "$")
    blockers = root["open_blocker_codes"]
    if (
        type(blockers) is not list
        or not blockers
        or len(blockers) > MAX_PACKAGE_BLOCKER_CODES
        or len(set(blockers)) != len(blockers)
        or any(type(code) is not str or BLOCKER_RE.fullmatch(code) is None for code in blockers)
    ):
        _fail("package_manifest_invalid", "$.open_blocker_codes")
    launch = _manifest_object(
        root["launch"],
        {"codex", "mcp_module", "python", "service_module"},
        "$.launch",
    )
    codex_launch, python_launch = _launch_paths(root["target_id"])
    if launch != {
        "codex": codex_launch,
        "mcp_module": MCP_MODULE,
        "python": python_launch,
        "service_module": SERVICE_MODULE,
    }:
        _fail("package_manifest_invalid", "$.launch")
    sources = _manifest_object(
        root["sources"],
        {
            "codex",
            "forge",
            "python",
            "runtime_sources",
            "runtime_sources_sha256",
        },
        "$.sources",
    )
    if (
        type(sources["runtime_sources_sha256"]) is not str
        or SHA256_RE.fullmatch(sources["runtime_sources_sha256"]) is None
    ):
        _fail("package_manifest_invalid", "$.sources.runtime_sources_sha256")
    archive_filename_keys: set[str] = set()
    archive_identities: dict[str, dict[str, Any]] = {}
    python_archive_sha256 = ""
    python_archive: dict[str, Any] | None = None
    python_normalization: object = None
    for component, version in (("codex", CODEX_VERSION), ("python", PYTHON_VERSION)):
        source = _manifest_object(
            sources[component],
            (
                {"archive", "normalization", "version"}
                if component == "python"
                else {"archive", "version"}
            ),
            f"$.sources.{component}",
        )
        if source["version"] != version:
            _fail("package_manifest_invalid", f"$.sources.{component}.version")
        archive = _manifest_object(
            source["archive"],
            {"entrypoint", "filename", "payload_root", "sha256", "size"},
            f"$.sources.{component}.archive",
        )
        filename = _portable_path(
            archive["filename"],
            f"$.sources.{component}.archive.filename",
        )
        filename_key = filename.casefold()
        if (
            "/" in filename
            or filename_key in archive_filename_keys
            or type(archive["size"]) is not int
            or not 0 < archive["size"] <= MAX_ARCHIVE_BYTES
            or type(archive["sha256"]) is not str
            or SHA256_RE.fullmatch(archive["sha256"]) is None
        ):
            _fail("package_manifest_invalid", f"$.sources.{component}.archive")
        archive_filename_keys.add(filename_key)
        payload_root = _portable_path(
            archive["payload_root"],
            f"$.sources.{component}.archive.payload_root",
        )
        entrypoint = _portable_path(
            archive["entrypoint"],
            f"$.sources.{component}.archive.entrypoint",
        )
        if entrypoint == payload_root or not entrypoint.startswith(f"{payload_root}/"):
            _fail(
                "package_manifest_invalid",
                f"$.sources.{component}.archive.entrypoint",
            )
        archive_identities[component] = archive
        if component == "python":
            python_archive_sha256 = archive["sha256"]
            python_archive = archive
            python_normalization = source["normalization"]
    if python_archive is None:
        _fail("package_manifest_invalid", "$.sources.python")
    if root["assembly_kind"] == "verified_development_runtime":
        if (
            sources["runtime_sources"] != _runtime_sources_identity()
            or sources["runtime_sources_sha256"] != RUNTIME_SOURCES_SHA256
        ):
            _fail("package_manifest_invalid", "$.sources.runtime_sources")
        provenance = (
            _read_runtime_sources_bytes()
            if runtime_sources_provenance is None
            else runtime_sources_provenance
        )
        if type(provenance) is not bytes:
            _fail("package_manifest_invalid", "$.sources.runtime_sources")
        runtime_sources_document = _parse_runtime_sources_control(provenance)
        expected_codex, expected_python, expected_blockers = _runtime_source_archive_identities(
            runtime_sources_document,
            root["target_id"],
        )
        if (
            archive_identities != {"codex": expected_codex, "python": expected_python}
            or tuple(blockers) != expected_blockers
        ):
            _fail("package_manifest_invalid", "$.sources")
    elif sources["runtime_sources"] is not None:
        _fail("package_manifest_invalid", "$.sources.runtime_sources")
    normalization: ArchiveNormalization | None = None
    if root["target_id"] == "win32-x64":
        if python_normalization is not None:
            _fail("package_manifest_invalid", "$.sources.python.normalization")
    elif python_archive_sha256 == LINUX_PBS_SHA256:
        if (
            not _is_linux_pbs_archive_identity(python_archive)
            or python_normalization != _normalization_identity()
        ):
            _fail("package_manifest_invalid", "$.sources.python")
        receipt = (
            _read_archive_normalization_bytes()
            if normalization_receipt is None
            else normalization_receipt
        )
        if type(receipt) is not bytes:
            _fail("package_manifest_invalid", "$.sources.python.normalization")
        normalization = _parse_archive_normalization(receipt)
    elif python_normalization is not None:
        _fail("package_manifest_invalid", "$.sources.python.normalization")
    forge = _manifest_object(
        sources["forge"],
        {"inventory_sha256", "version"},
        "$.sources.forge",
    )
    if (
        forge["version"] != FORGE_VERSION
        or type(forge["inventory_sha256"]) is not str
        or SHA256_RE.fullmatch(forge["inventory_sha256"]) is None
    ):
        _fail("package_manifest_invalid", "$.sources.forge")
    inventory = root["inventory"]
    if (
        type(inventory) is not list
        or not inventory
        or len(inventory) > MAX_PACKAGE_INVENTORY_ENTRIES
    ):
        _fail("package_manifest_invalid", "$.inventory")
    paths: list[str] = []
    total = 0
    for index, value_item in enumerate(inventory):
        item = _manifest_object(
            value_item,
            {"component", "mode", "path", "sha256", "size"},
            f"$.inventory[{index}]",
        )
        path = _portable_path(item["path"], f"$.inventory[{index}].path")
        if path == PACKAGE_MANIFEST_NAME:
            _fail("package_manifest_invalid", "$.inventory")
        paths.append(path)
        if (
            item["component"] not in {"codex", "python", "forge", "control"}
            or type(item["mode"]) is not int
            or item["mode"] not in {0o644, 0o755}
            or type(item["size"]) is not int
            or not 0 <= item["size"] <= MAX_ARCHIVE_MEMBER_BYTES
            or type(item["sha256"]) is not str
            or SHA256_RE.fullmatch(item["sha256"]) is None
        ):
            _fail("package_manifest_invalid", f"$.inventory[{index}]")
        total += item["size"]
        if total > MAX_OUTPUT_BYTES:
            _fail("package_manifest_invalid", "$.inventory")
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")):
        _fail("package_manifest_invalid", "$.inventory")
    namespace = _output_namespace_budget(
        "package_manifest_invalid",
        "$.inventory",
    )
    namespace.add_file(PACKAGE_MANIFEST_NAME)
    for path in paths:
        namespace.add_file(path)
    _validate_portable_path_aliases(
        paths,
        normalization=normalization,
        prefix=f"runtime/python/{root['target_id']}",
        code="package_manifest_invalid",
        field="$.inventory",
    )
    if normalization is not None:
        prefix = f"runtime/python/{root['target_id']}/"
        actual_python = tuple(
            (
                entry["mode"],
                entry["path"].removeprefix(prefix),
                entry["sha256"],
                entry["size"],
            )
            for entry in inventory
            if entry["component"] == "python" and entry["path"].startswith(prefix)
        )
        expected_python = tuple(
            (item.mode, item.source, item.sha256, item.size) for item in normalization.files
        )
        if actual_python != expected_python:
            _fail("package_manifest_invalid", "$.inventory")
    by_path = {entry["path"]: entry for entry in inventory}
    required_entries = {
        launch["codex"]: ("codex", 0o755),
        launch["python"]: ("python", 0o755),
        LAUNCH_MANIFEST_NAME: ("control", 0o644),
    }
    if normalization is not None:
        required_entries[NORMALIZATION_PACKAGE_PATH] = ("control", 0o644)
    if root["assembly_kind"] == "verified_development_runtime":
        required_entries[RUNTIME_SOURCES_PACKAGE_PATH] = ("control", 0o644)
    if any(
        path not in by_path
        or by_path[path]["component"] != component
        or by_path[path]["mode"] != mode
        for path, (component, mode) in required_entries.items()
    ):
        _fail("package_manifest_invalid", "$.inventory")
    target = root["target_id"]
    codex_prefix = f"runtime/codex/{target}/"
    python_prefix = f"runtime/python/{target}/"
    allowed_control = {LAUNCH_MANIFEST_NAME}
    if root["assembly_kind"] == "verified_development_runtime":
        provenance_entry = by_path[RUNTIME_SOURCES_PACKAGE_PATH]
        if (
            provenance_entry["size"] != RUNTIME_SOURCES_SIZE
            or provenance_entry["sha256"] != RUNTIME_SOURCES_SHA256
        ):
            _fail("package_manifest_invalid", "$.inventory")
        allowed_control.add(RUNTIME_SOURCES_PACKAGE_PATH)
    if normalization is not None:
        receipt_entry = by_path[NORMALIZATION_PACKAGE_PATH]
        if (
            receipt_entry["size"] != NORMALIZATION_SIZE
            or receipt_entry["sha256"] != NORMALIZATION_SHA256
        ):
            _fail("package_manifest_invalid", "$.inventory")
        allowed_control.add(NORMALIZATION_PACKAGE_PATH)
    for entry in inventory:
        path = entry["path"]
        component = entry["component"]
        if (
            (component == "codex" and not path.startswith(codex_prefix))
            or (component == "python" and not path.startswith(python_prefix))
            or (
                component == "forge"
                and not (
                    path.startswith(python_prefix)
                    or path.startswith("protocol/codex-app-server-0.144.6/")
                )
            )
            or (component == "control" and path not in allowed_control)
        ):
            _fail("package_manifest_invalid", "$.inventory")
    for component, launch_path, output_prefix in (
        ("codex", launch["codex"], codex_prefix),
        ("python", launch["python"], python_prefix),
    ):
        archive = sources[component]["archive"]
        relative_entrypoint = archive["entrypoint"].removeprefix(f"{archive['payload_root']}/")
        if launch_path != f"{output_prefix}{relative_entrypoint}":
            _fail("package_manifest_invalid", "$.launch")
    forge_inventory = [entry for entry in inventory if entry["component"] == "forge"]
    if forge["inventory_sha256"] != _sha256(_canonical_json_bytes(forge_inventory)):
        _fail("package_manifest_invalid", "$.sources.forge.inventory_sha256")
    return root


def _validate_launch_manifest_payload(payload: bytes, target_id: str) -> None:
    try:
        value = load_strict_json_bytes(payload)
    except RuntimeSourcesError:
        _fail("launch_manifest_invalid", "runtime-manifest")
    if _canonical_json_bytes(value) != payload or value != _launch_manifest(target_id):
        _fail("launch_manifest_invalid", "runtime-manifest")


def _require_secure_output_primitives(field: str) -> None:
    if (
        os.name != "posix"
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.mkdir not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
    ):
        _fail("secure_primitive_unavailable", field)


def _directory_open_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_pinned_directory(path: Path, field: str) -> tuple[int, tuple[int, int]]:
    _require_secure_output_primitives(field)
    absolute = _lexical_absolute(path, field)
    if absolute.anchor != os.path.sep:
        _fail("unsafe_parent", field)
    try:
        descriptor = os.open(os.path.sep, _directory_open_flags())
    except OSError:
        _fail("unsafe_parent", field)
    try:
        for component in absolute.parts[1:]:
            try:
                next_descriptor = os.open(
                    component,
                    _directory_open_flags(),
                    dir_fd=descriptor,
                )
            except OSError:
                _fail("unsafe_parent", field)
            try:
                info = os.fstat(next_descriptor)
                if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                    _fail("unsafe_parent", field)
            except BaseException:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            _fail("unsafe_parent", field)
        return descriptor, _identity(info)
    except BaseException:
        os.close(descriptor)
        raise


def _confirm_directory_binding(
    path: Path,
    descriptor: int,
    expected: tuple[int, int],
    field: str,
) -> None:
    try:
        opened = os.fstat(descriptor)
    except OSError:
        _fail("filesystem_identity_changed", field)
    if (
        _is_link_or_reparse(opened)
        or not stat.S_ISDIR(opened.st_mode)
        or _identity(opened) != expected
    ):
        _fail("filesystem_identity_changed", field)
    current_descriptor, current_identity = _open_pinned_directory(path, field)
    os.close(current_descriptor)
    if current_identity != expected:
        _fail("filesystem_identity_changed", field)


def _windows_unicode_name_length(name: str, field: str) -> int:
    try:
        length = len(name.encode("utf-16-le", "strict"))
    except UnicodeError:
        _fail("invalid_path", field)
    if length > MAX_UNICODE_STRING_NAME_BYTES:
        _fail("invalid_path", field)
    return length


class _WindowsOutputApi:
    FILE_LIST_DIRECTORY = 0x0001
    FILE_ADD_FILE = 0x0002
    FILE_ADD_SUBDIRECTORY = 0x0004
    FILE_READ_ATTRIBUTES = 0x0080
    FILE_WRITE_ATTRIBUTES = 0x0100
    SYNCHRONIZE = 0x00100000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_OPEN = 0x00000001
    FILE_CREATE = 0x00000002
    FILE_DIRECTORY_FILE = 0x00000001
    FILE_WRITE_THROUGH = 0x00000002
    FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    FILE_NON_DIRECTORY_FILE = 0x00000040
    FILE_OPEN_REPARSE_POINT = 0x00200000
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    OPEN_EXISTING = 3
    OBJ_CASE_INSENSITIVE = 0x00000040
    STATUS_OBJECT_NAME_COLLISION = 0xC0000035

    def __init__(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            self.ctypes = ctypes
            self.wintypes = wintypes
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.ntdll = ctypes.WinDLL("ntdll")
        except (AttributeError, ImportError, OSError):
            _fail("secure_primitive_unavailable", "output")

        class UnicodeString(self.ctypes.Structure):
            _fields_ = [
                ("Length", self.wintypes.USHORT),
                ("MaximumLength", self.wintypes.USHORT),
                ("Buffer", self.wintypes.LPWSTR),
            ]

        class ObjectAttributes(self.ctypes.Structure):
            _fields_ = [
                ("Length", self.wintypes.ULONG),
                ("RootDirectory", self.wintypes.HANDLE),
                ("ObjectName", self.ctypes.POINTER(UnicodeString)),
                ("Attributes", self.wintypes.ULONG),
                ("SecurityDescriptor", self.wintypes.LPVOID),
                ("SecurityQualityOfService", self.wintypes.LPVOID),
            ]

        class IoStatusBlock(self.ctypes.Structure):
            _fields_ = [
                ("Status", self.ctypes.c_void_p),
                ("Information", self.ctypes.c_size_t),
            ]

        class ByHandleFileInformation(self.ctypes.Structure):
            _fields_ = [
                ("dwFileAttributes", self.wintypes.DWORD),
                ("ftCreationTime", self.wintypes.FILETIME),
                ("ftLastAccessTime", self.wintypes.FILETIME),
                ("ftLastWriteTime", self.wintypes.FILETIME),
                ("dwVolumeSerialNumber", self.wintypes.DWORD),
                ("nFileSizeHigh", self.wintypes.DWORD),
                ("nFileSizeLow", self.wintypes.DWORD),
                ("nNumberOfLinks", self.wintypes.DWORD),
                ("nFileIndexHigh", self.wintypes.DWORD),
                ("nFileIndexLow", self.wintypes.DWORD),
            ]

        class FileAttributeTagInfo(self.ctypes.Structure):
            _fields_ = [
                ("FileAttributes", self.wintypes.DWORD),
                ("ReparseTag", self.wintypes.DWORD),
            ]

        self.UnicodeString = UnicodeString
        self.ObjectAttributes = ObjectAttributes
        self.IoStatusBlock = IoStatusBlock
        self.ByHandleFileInformation = ByHandleFileInformation
        self.FileAttributeTagInfo = FileAttributeTagInfo

        self.NtCreateFile = self.ntdll.NtCreateFile
        self.NtCreateFile.argtypes = [
            self.ctypes.POINTER(self.wintypes.HANDLE),
            self.wintypes.ULONG,
            self.ctypes.POINTER(ObjectAttributes),
            self.ctypes.POINTER(IoStatusBlock),
            self.wintypes.LPVOID,
            self.wintypes.ULONG,
            self.wintypes.ULONG,
            self.wintypes.ULONG,
            self.wintypes.ULONG,
            self.wintypes.LPVOID,
            self.wintypes.ULONG,
        ]
        self.NtCreateFile.restype = self.wintypes.LONG

        self.CreateFileW = self.kernel32.CreateFileW
        self.CreateFileW.argtypes = [
            self.wintypes.LPCWSTR,
            self.wintypes.DWORD,
            self.wintypes.DWORD,
            self.wintypes.LPVOID,
            self.wintypes.DWORD,
            self.wintypes.DWORD,
            self.wintypes.HANDLE,
        ]
        self.CreateFileW.restype = self.wintypes.HANDLE

        self.GetFileInformationByHandle = self.kernel32.GetFileInformationByHandle
        self.GetFileInformationByHandle.argtypes = [
            self.wintypes.HANDLE,
            self.ctypes.POINTER(ByHandleFileInformation),
        ]
        self.GetFileInformationByHandle.restype = self.wintypes.BOOL

        self.GetFileInformationByHandleEx = self.kernel32.GetFileInformationByHandleEx
        self.GetFileInformationByHandleEx.argtypes = [
            self.wintypes.HANDLE,
            self.ctypes.c_int,
            self.wintypes.LPVOID,
            self.wintypes.DWORD,
        ]
        self.GetFileInformationByHandleEx.restype = self.wintypes.BOOL

        self.WriteFile = self.kernel32.WriteFile
        self.WriteFile.argtypes = [
            self.wintypes.HANDLE,
            self.wintypes.LPCVOID,
            self.wintypes.DWORD,
            self.ctypes.POINTER(self.wintypes.DWORD),
            self.wintypes.LPVOID,
        ]
        self.WriteFile.restype = self.wintypes.BOOL

        self.FlushFileBuffers = self.kernel32.FlushFileBuffers
        self.FlushFileBuffers.argtypes = [self.wintypes.HANDLE]
        self.FlushFileBuffers.restype = self.wintypes.BOOL

        self.CloseHandle = self.kernel32.CloseHandle
        self.CloseHandle.argtypes = [self.wintypes.HANDLE]
        self.CloseHandle.restype = self.wintypes.BOOL

    def close(self, handle: int) -> None:
        if handle and not self.CloseHandle(self.wintypes.HANDLE(handle)):
            _fail("output_finalize_failed", "output")

    def state(self, handle: int, field: str) -> _WindowsHandleState:
        info = self.ByHandleFileInformation()
        tag = self.FileAttributeTagInfo()
        native = self.wintypes.HANDLE(handle)
        if not self.GetFileInformationByHandle(native, self.ctypes.byref(info)):
            _fail("filesystem_identity_changed", field)
        if not self.GetFileInformationByHandleEx(
            native,
            9,
            self.ctypes.byref(tag),
            self.ctypes.sizeof(tag),
        ):
            _fail("filesystem_identity_changed", field)
        identity = (
            int(info.dwVolumeSerialNumber),
            (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow),
        )
        if identity == (0, 0):
            _fail("filesystem_identity_unavailable", field)
        attributes = int(tag.FileAttributes)
        return _WindowsHandleState(
            identity=identity,
            size=(int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow),
            nlink=int(info.nNumberOfLinks),
            is_directory=bool(attributes & self.FILE_ATTRIBUTE_DIRECTORY),
            is_reparse=bool(attributes & self.FILE_ATTRIBUTE_REPARSE_POINT),
        )

    def _validated_handle(
        self,
        handle: int,
        *,
        directory: bool,
        field: str,
        failure_code: str,
    ) -> int:
        retained = False
        try:
            state = self.state(handle, field)
            if state.is_reparse or state.is_directory != directory:
                _fail(failure_code, field)
            retained = True
            return handle
        finally:
            if not retained:
                self.close(handle)

    def open_anchor(self, anchor: str, field: str) -> int:
        access = self.FILE_LIST_DIRECTORY | self.FILE_READ_ATTRIBUTES | self.SYNCHRONIZE
        handle = self.CreateFileW(
            anchor,
            access,
            self.FILE_SHARE_READ | self.FILE_SHARE_WRITE,
            None,
            self.OPEN_EXISTING,
            self.FILE_FLAG_BACKUP_SEMANTICS | self.FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        value = self.ctypes.cast(handle, self.ctypes.c_void_p).value
        if value in {None, self.ctypes.c_void_p(-1).value}:
            _fail("unsafe_parent", field)
        result = int(value)
        return self._validated_handle(
            result,
            directory=True,
            field=field,
            failure_code="unsafe_parent",
        )

    def relative(
        self,
        parent: int,
        name: str,
        *,
        directory: bool,
        create: bool,
        writable: bool = False,
        field: str,
    ) -> int:
        if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            _fail("invalid_path", field)
        length = _windows_unicode_name_length(name, field)
        buffer = self.ctypes.create_unicode_buffer(name)
        unicode_name = self.UnicodeString(
            length,
            length + 2,
            self.ctypes.cast(buffer, self.wintypes.LPWSTR),
        )
        attributes = self.ObjectAttributes(
            self.ctypes.sizeof(self.ObjectAttributes),
            self.wintypes.HANDLE(parent),
            self.ctypes.pointer(unicode_name),
            self.OBJ_CASE_INSENSITIVE,
            None,
            None,
        )
        io_status = self.IoStatusBlock()
        output = self.wintypes.HANDLE()
        if directory:
            access = self.FILE_LIST_DIRECTORY | self.FILE_READ_ATTRIBUTES | self.SYNCHRONIZE
            if create or writable:
                access |= (
                    self.FILE_ADD_FILE | self.FILE_ADD_SUBDIRECTORY | self.FILE_WRITE_ATTRIBUTES
                )
            share = self.FILE_SHARE_READ | self.FILE_SHARE_WRITE
            options = (
                self.FILE_DIRECTORY_FILE
                | self.FILE_OPEN_REPARSE_POINT
                | self.FILE_SYNCHRONOUS_IO_NONALERT
                | self.FILE_WRITE_THROUGH
            )
            file_attributes = self.FILE_ATTRIBUTE_DIRECTORY
        else:
            access = (
                (self.GENERIC_WRITE if create else 0)
                | self.FILE_READ_ATTRIBUTES
                | (self.FILE_WRITE_ATTRIBUTES if create else 0)
                | self.SYNCHRONIZE
            )
            share = self.FILE_SHARE_READ if create else self.FILE_SHARE_READ | self.FILE_SHARE_WRITE
            options = (
                self.FILE_NON_DIRECTORY_FILE
                | self.FILE_OPEN_REPARSE_POINT
                | self.FILE_SYNCHRONOUS_IO_NONALERT
                | self.FILE_WRITE_THROUGH
            )
            file_attributes = self.FILE_ATTRIBUTE_NORMAL
        status = int(
            self.NtCreateFile(
                self.ctypes.byref(output),
                access,
                self.ctypes.byref(attributes),
                self.ctypes.byref(io_status),
                None,
                file_attributes,
                share,
                self.FILE_CREATE if create else self.FILE_OPEN,
                options,
                None,
                0,
            )
        )
        if status < 0:
            if create and (status & 0xFFFFFFFF) == self.STATUS_OBJECT_NAME_COLLISION:
                _fail("output_exists", field, exit_code=2)
            _fail(
                "output_create_failed" if create else "filesystem_identity_changed",
                field,
            )
        value = self.ctypes.cast(output, self.ctypes.c_void_p).value
        if value is None:
            _fail("secure_primitive_unavailable", field)
        result = int(value)
        return self._validated_handle(
            result,
            directory=directory,
            field=field,
            failure_code=("output_create_failed" if create else "filesystem_identity_changed"),
        )

    def write(self, handle: int, payload: bytes, field: str) -> None:
        offset = 0
        while offset < len(payload):
            chunk = payload[offset : offset + READ_CHUNK_BYTES]
            buffer = (self.ctypes.c_ubyte * len(chunk)).from_buffer_copy(chunk)
            written = self.wintypes.DWORD()
            if not self.WriteFile(
                self.wintypes.HANDLE(handle),
                buffer,
                len(chunk),
                self.ctypes.byref(written),
                None,
            ):
                _fail("output_write_failed", field)
            if written.value != len(chunk):
                _fail("output_write_failed", field)
            offset += int(written.value)
        if not self.FlushFileBuffers(self.wintypes.HANDLE(handle)):
            _fail("output_finalize_failed", field)


_WINDOWS_OUTPUT_API: _WindowsOutputApi | None = None


def _windows_output_api() -> _WindowsOutputApi:
    global _WINDOWS_OUTPUT_API
    if _WINDOWS_OUTPUT_API is None:
        _WINDOWS_OUTPUT_API = _WindowsOutputApi()
    return _WINDOWS_OUTPUT_API


class _WindowsDirectoryChain:
    def __init__(self, path: Path, field: str) -> None:
        self.api = _windows_output_api()
        self.field = field
        absolute = _lexical_absolute(path, field)
        windows = PureWindowsPath(os.fspath(absolute))
        if not windows.is_absolute() or not windows.anchor:
            _fail("invalid_path", field, exit_code=2)
        self.handles: list[int] = []
        self.bindings: list[_WindowsBinding] = []
        self.anchor_name = windows.anchor
        anchor = self.api.open_anchor(self.anchor_name, field)
        self.anchor_identity = self.api.state(anchor, field).identity
        self.handles.append(anchor)
        parent = anchor
        try:
            components = windows.parts[1:]
            for index, component in enumerate(components):
                handle = self.api.relative(
                    parent,
                    component,
                    directory=True,
                    create=False,
                    writable=index == len(components) - 1,
                    field=field,
                )
                state = self.api.state(handle, field)
                self.handles.append(handle)
                self.bindings.append(
                    _WindowsBinding(
                        parent=parent,
                        name=component,
                        handle=handle,
                        identity=state.identity,
                        is_directory=True,
                    )
                )
                parent = handle
        except BaseException:
            self.close()
            raise

    @property
    def leaf(self) -> int:
        return self.handles[-1]

    def require_bindings(self) -> None:
        anchor = self.api.state(self.handles[0], self.field)
        if anchor.identity != self.anchor_identity or anchor.is_reparse:
            _fail("filesystem_identity_changed", self.field)
        reopened_anchor = self.api.open_anchor(self.anchor_name, self.field)
        try:
            if self.api.state(reopened_anchor, self.field).identity != self.anchor_identity:
                _fail("filesystem_identity_changed", self.field)
        finally:
            self.api.close(reopened_anchor)
        for binding in self.bindings:
            retained = self.api.state(binding.handle, self.field)
            if (
                retained.identity != binding.identity
                or retained.is_reparse
                or retained.is_directory != binding.is_directory
            ):
                _fail("filesystem_identity_changed", self.field)
            reopened = self.api.relative(
                binding.parent,
                binding.name,
                directory=binding.is_directory,
                create=False,
                field=self.field,
            )
            try:
                current = self.api.state(reopened, self.field)
                if current.identity != binding.identity:
                    _fail("filesystem_identity_changed", self.field)
            finally:
                self.api.close(reopened)

    def close(self) -> None:
        while self.handles:
            self.api.close(self.handles.pop())
        self.bindings.clear()


def _write_owned_file_at(
    directory_descriptor: int,
    name: str,
    payload: bytes,
    mode: int,
    *,
    field: str = "output",
) -> tuple[tuple[int, int], int]:
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        _fail("output_write_failed", field)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(name, flags, mode, dir_fd=directory_descriptor)
    except FileExistsError:
        _fail("output_exists", field, exit_code=2)
    except OSError:
        _fail("output_write_failed", field)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset : offset + READ_CHUNK_BYTES])
            if written <= 0:
                _fail("output_write_failed", field)
            offset += written
        if os.name == "posix":
            os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != len(payload):
            _fail("output_write_failed", field)
        os.fsync(directory_descriptor)
        return _identity(info), os.dup(descriptor)
    except RuntimeAssemblyError:
        raise
    except OSError:
        _fail("output_write_failed", field)
    finally:
        os.close(descriptor)


class _PosixOwnedOutput:
    def __init__(self, root: Path) -> None:
        _require_secure_output_primitives("output")
        self.root = _lexical_absolute(root, "output")
        if not self.root.name:
            _fail("invalid_path", "output", exit_code=2)
        self.parent_descriptor, self.parent_identity = _open_pinned_directory(
            self.root.parent,
            "output",
        )
        self.root_descriptor = -1
        self.directories: dict[tuple[str, ...], tuple[int, int]] = {}
        self.directory_descriptors: dict[tuple[str, ...], int] = {}
        self.files: dict[tuple[str, ...], tuple[tuple[int, int], int]] = {}
        try:
            _invoke_write_hook(self.root, "before_root_mkdir")
            _confirm_directory_binding(
                self.root.parent,
                self.parent_descriptor,
                self.parent_identity,
                "output",
            )
            os.mkdir(self.root.name, 0o700, dir_fd=self.parent_descriptor)
        except FileExistsError:
            os.close(self.parent_descriptor)
            _fail("output_exists", "output", exit_code=2)
        except OSError:
            os.close(self.parent_descriptor)
            _fail("output_create_failed", "output")
        try:
            created = os.stat(
                self.root.name,
                dir_fd=self.parent_descriptor,
                follow_symlinks=False,
            )
            if _is_link_or_reparse(created) or not stat.S_ISDIR(created.st_mode):
                _fail("output_create_failed", "output")
            created_identity = _identity(created)
            _invoke_write_hook(self.root, "after_root_mkdir")
            self.root_descriptor = os.open(
                self.root.name,
                _directory_open_flags(),
                dir_fd=self.parent_descriptor,
            )
            opened = os.fstat(self.root_descriptor)
            if (
                _is_link_or_reparse(opened)
                or not stat.S_ISDIR(opened.st_mode)
                or _identity(opened) != created_identity
            ):
                _fail("filesystem_identity_changed", "output")
            os.fchmod(self.root_descriptor, 0o700)
            os.fsync(self.root_descriptor)
            os.fsync(self.parent_descriptor)
            self.directories[()] = created_identity
            self._require_root()
        except BaseException:
            if self.root_descriptor >= 0:
                os.close(self.root_descriptor)
            os.close(self.parent_descriptor)
            raise

    def __enter__(self) -> _PosixOwnedOutput:
        return self

    def __exit__(self, *_error: object) -> None:
        while self.files:
            _parts, (_identity_value, descriptor) = self.files.popitem()
            os.close(descriptor)
        for parts in sorted(self.directory_descriptors, key=len, reverse=True):
            os.close(self.directory_descriptors[parts])
        self.directory_descriptors.clear()
        if self.root_descriptor >= 0:
            os.close(self.root_descriptor)
            self.root_descriptor = -1
        if self.parent_descriptor >= 0:
            os.close(self.parent_descriptor)
            self.parent_descriptor = -1

    def _require_root(self) -> None:
        try:
            parent = os.fstat(self.parent_descriptor)
            opened = os.fstat(self.root_descriptor)
            current = os.stat(
                self.root.name,
                dir_fd=self.parent_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            _fail("filesystem_identity_changed", "output")
        if (
            _is_link_or_reparse(parent)
            or not stat.S_ISDIR(parent.st_mode)
            or _identity(parent) != self.parent_identity
            or _is_link_or_reparse(opened)
            or not stat.S_ISDIR(opened.st_mode)
            or _identity(opened) != self.directories[()]
            or _is_link_or_reparse(current)
            or not stat.S_ISDIR(current.st_mode)
            or _identity(current) != self.directories[()]
        ):
            _fail("filesystem_identity_changed", "output")

    def _open_directory_parts(self, parts: tuple[str, ...]) -> int:
        descriptor = os.dup(self.root_descriptor)
        try:
            for component in parts:
                next_descriptor = os.open(
                    component,
                    _directory_open_flags(),
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except OSError:
            os.close(descriptor)
            _fail("filesystem_identity_changed", "output")
        except BaseException:
            os.close(descriptor)
            raise

    def _require_all_bindings(self) -> None:
        self._require_root()
        _confirm_directory_binding(
            self.root.parent,
            self.parent_descriptor,
            self.parent_identity,
            "output",
        )
        for parts, expected in self.directories.items():
            retained_descriptor = (
                self.root_descriptor if not parts else self.directory_descriptors[parts]
            )
            retained = os.fstat(retained_descriptor)
            if (
                _is_link_or_reparse(retained)
                or not stat.S_ISDIR(retained.st_mode)
                or _identity(retained) != expected
            ):
                _fail("filesystem_identity_changed", "output")
            reopened = self._open_directory_parts(parts)
            try:
                current = os.fstat(reopened)
                if _identity(current) != expected:
                    _fail("filesystem_identity_changed", "output")
            finally:
                os.close(reopened)
        for parts, (expected, retained_descriptor) in self.files.items():
            retained = os.fstat(retained_descriptor)
            if (
                _is_link_or_reparse(retained)
                or not stat.S_ISREG(retained.st_mode)
                or retained.st_nlink != 1
                or _identity(retained) != expected
            ):
                _fail("filesystem_identity_changed", "output")
            parent = self._open_directory_parts(parts[:-1])
            try:
                reopened = os.open(
                    parts[-1],
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent,
                )
                try:
                    current = os.fstat(reopened)
                    if (
                        _is_link_or_reparse(current)
                        or not stat.S_ISREG(current.st_mode)
                        or current.st_nlink != 1
                        or _identity(current) != expected
                    ):
                        _fail("filesystem_identity_changed", "output")
                finally:
                    os.close(reopened)
            finally:
                os.close(parent)

    def write(self, relative: str, payload: bytes, mode: int) -> None:
        self._require_all_bindings()
        portable = PurePosixPath(_portable_path(relative, "output"))
        current_descriptor = os.dup(self.root_descriptor)
        current_parts: tuple[str, ...] = ()
        logical = self.root
        try:
            for component in portable.parts[:-1]:
                logical /= component
                current_parts = (*current_parts, component)
                expected = self.directories.get(current_parts)
                if expected is None:
                    try:
                        _invoke_write_hook(logical, "before_directory_mkdir")
                        os.mkdir(component, 0o755, dir_fd=current_descriptor)
                        created = os.stat(
                            component,
                            dir_fd=current_descriptor,
                            follow_symlinks=False,
                        )
                    except FileExistsError:
                        _fail("filesystem_identity_changed", "output")
                    except OSError:
                        _fail("output_write_failed", "output")
                    if _is_link_or_reparse(created) or not stat.S_ISDIR(created.st_mode):
                        _fail("output_write_failed", "output")
                    expected = _identity(created)
                    _invoke_write_hook(logical, "after_directory_mkdir")
                try:
                    next_descriptor = os.open(
                        component,
                        _directory_open_flags(),
                        dir_fd=current_descriptor,
                    )
                except OSError:
                    _fail("filesystem_identity_changed", "output")
                try:
                    opened = os.fstat(next_descriptor)
                    if (
                        _is_link_or_reparse(opened)
                        or not stat.S_ISDIR(opened.st_mode)
                        or _identity(opened) != expected
                    ):
                        _fail("filesystem_identity_changed", "output")
                    if current_parts not in self.directories:
                        os.fchmod(next_descriptor, 0o755)
                        os.fsync(next_descriptor)
                        os.fsync(current_descriptor)
                        self.directories[current_parts] = expected
                        self.directory_descriptors[current_parts] = os.dup(next_descriptor)
                except RuntimeAssemblyError:
                    os.close(next_descriptor)
                    raise
                except OSError:
                    os.close(next_descriptor)
                    _fail("output_write_failed", "output")
                os.close(current_descriptor)
                current_descriptor = next_descriptor
            target_path = self.root.joinpath(*portable.parts)
            _invoke_write_hook(target_path, "before_file_open")
            _confirm_directory_binding(
                target_path.parent,
                current_descriptor,
                self.directories[current_parts],
                "output",
            )
            identity, retained_descriptor = _write_owned_file_at(
                current_descriptor,
                portable.name,
                payload,
                mode,
            )
            self.files[portable.parts] = (identity, retained_descriptor)
            _invoke_write_hook(target_path, "after_file_write")
            current = os.stat(
                portable.name,
                dir_fd=current_descriptor,
                follow_symlinks=False,
            )
            if (
                _is_link_or_reparse(current)
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or _identity(current) != identity
            ):
                _fail("filesystem_identity_changed", "output")
            self._require_all_bindings()
        finally:
            os.close(current_descriptor)

    def require_case_sensitive_paths(
        self,
        groups: Iterable[frozenset[str]],
    ) -> None:
        self._require_all_bindings()
        for group in groups:
            identities: list[tuple[int, int]] = []
            for path in group:
                parts = PurePosixPath(path).parts
                if parts in self.files:
                    identities.append(self.files[parts][0])
                elif parts in self.directories:
                    identities.append(self.directories[parts])
                else:
                    _fail("case_sensitive_filesystem_required", "output")
            if len(set(identities)) != len(group):
                _fail("case_sensitive_filesystem_required", "output")


class _WindowsOwnedOutput:
    def __init__(self, root: Path) -> None:
        self.api = _windows_output_api()
        self.root = _lexical_absolute(root, "output")
        if not self.root.name:
            _fail("invalid_path", "output", exit_code=2)
        self.parent_chain = _WindowsDirectoryChain(self.root.parent, "output")
        self.directories: dict[tuple[str, ...], int] = {}
        self.files: dict[tuple[str, ...], int] = {}
        self.bindings: list[_WindowsBinding] = []
        try:
            _invoke_write_hook(self.root, "before_root_mkdir")
            self.parent_chain.require_bindings()
            root_handle = self.api.relative(
                self.parent_chain.leaf,
                self.root.name,
                directory=True,
                create=True,
                field="output",
            )
            root_state = self.api.state(root_handle, "output")
            self.directories[()] = root_handle
            self.bindings.append(
                _WindowsBinding(
                    parent=self.parent_chain.leaf,
                    name=self.root.name,
                    handle=root_handle,
                    identity=root_state.identity,
                    is_directory=True,
                )
            )
            _invoke_write_hook(self.root, "after_root_mkdir")
            self._require_all_bindings()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> _WindowsOwnedOutput:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def close(self) -> None:
        while self.files:
            _parts, handle = self.files.popitem()
            self.api.close(handle)
        for parts in sorted(self.directories, key=len, reverse=True):
            self.api.close(self.directories[parts])
        self.directories.clear()
        self.bindings.clear()
        self.parent_chain.close()

    def _require_root(self) -> None:
        self._require_all_bindings()

    def require_case_sensitive_paths(
        self,
        groups: Iterable[frozenset[str]],
    ) -> None:
        if any(True for _group in groups):
            _fail("case_sensitive_filesystem_required", "output")

    def _require_all_bindings(self) -> None:
        self.parent_chain.require_bindings()
        for binding in self.bindings:
            retained = self.api.state(binding.handle, "output")
            if (
                retained.identity != binding.identity
                or retained.is_reparse
                or retained.is_directory != binding.is_directory
                or (not binding.is_directory and retained.nlink != 1)
            ):
                _fail("filesystem_identity_changed", "output")
            reopened = self.api.relative(
                binding.parent,
                binding.name,
                directory=binding.is_directory,
                create=False,
                field="output",
            )
            try:
                current = self.api.state(reopened, "output")
                if current.identity != binding.identity:
                    _fail("filesystem_identity_changed", "output")
            finally:
                self.api.close(reopened)

    def write(self, relative: str, payload: bytes, mode: int) -> None:
        del mode
        self._require_all_bindings()
        portable = PurePosixPath(_portable_path(relative, "output"))
        current_parts: tuple[str, ...] = ()
        current_handle = self.directories[()]
        logical = self.root
        for component in portable.parts[:-1]:
            logical /= component
            current_parts = (*current_parts, component)
            next_handle = self.directories.get(current_parts)
            if next_handle is None:
                _invoke_write_hook(logical, "before_directory_mkdir")
                next_handle = self.api.relative(
                    current_handle,
                    component,
                    directory=True,
                    create=True,
                    field="output",
                )
                state = self.api.state(next_handle, "output")
                self.directories[current_parts] = next_handle
                self.bindings.append(
                    _WindowsBinding(
                        parent=current_handle,
                        name=component,
                        handle=next_handle,
                        identity=state.identity,
                        is_directory=True,
                    )
                )
                _invoke_write_hook(logical, "after_directory_mkdir")
                self._require_all_bindings()
            current_handle = next_handle
        target_path = self.root.joinpath(*portable.parts)
        _invoke_write_hook(target_path, "before_file_open")
        self._require_all_bindings()
        file_handle = self.api.relative(
            current_handle,
            portable.name,
            directory=False,
            create=True,
            field="output",
        )
        try:
            self.api.write(file_handle, payload, "output")
            state = self.api.state(file_handle, "output")
            if state.size != len(payload) or state.nlink != 1:
                _fail("output_write_failed", "output")
            self.files[portable.parts] = file_handle
            self.bindings.append(
                _WindowsBinding(
                    parent=current_handle,
                    name=portable.name,
                    handle=file_handle,
                    identity=state.identity,
                    is_directory=False,
                )
            )
        except BaseException:
            self.api.close(file_handle)
            raise
        _invoke_write_hook(target_path, "after_file_write")
        self._require_all_bindings()


class _OwnedOutput:
    def __new__(cls, root: Path) -> _PosixOwnedOutput | _WindowsOwnedOutput:
        if os.name == "nt":
            return _WindowsOwnedOutput(root)
        return _PosixOwnedOutput(root)


class _PosixPublishedFile:
    def __init__(
        self,
        destination: Path,
        payload: bytes,
        mode: int,
        field: str,
    ) -> None:
        self.destination = _lexical_absolute(destination, field)
        self.field = field
        if not self.destination.name:
            _fail("invalid_path", field, exit_code=2)
        self.parent_descriptor, self.parent_identity = _open_pinned_directory(
            self.destination.parent,
            field,
        )
        self.file_descriptor = -1
        try:
            _invoke_write_hook(self.destination, "before_file_open")
            _confirm_directory_binding(
                self.destination.parent,
                self.parent_descriptor,
                self.parent_identity,
                field,
            )
            self.identity, self.file_descriptor = _write_owned_file_at(
                self.parent_descriptor,
                self.destination.name,
                payload,
                mode,
                field=field,
            )
            self.expected_size = len(payload)
            _invoke_write_hook(self.destination, "after_file_write")
            self.require_bindings()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> _PosixPublishedFile:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def close(self) -> None:
        if self.file_descriptor >= 0:
            os.close(self.file_descriptor)
            self.file_descriptor = -1
        if self.parent_descriptor >= 0:
            os.close(self.parent_descriptor)
            self.parent_descriptor = -1

    def require_bindings(self) -> None:
        _confirm_directory_binding(
            self.destination.parent,
            self.parent_descriptor,
            self.parent_identity,
            self.field,
        )
        retained = os.fstat(self.file_descriptor)
        if (
            _is_link_or_reparse(retained)
            or not stat.S_ISREG(retained.st_mode)
            or retained.st_nlink != 1
            or retained.st_size != self.expected_size
            or _identity(retained) != self.identity
        ):
            _fail("filesystem_identity_changed", self.field)
        try:
            reopened = os.open(
                self.destination.name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=self.parent_descriptor,
            )
        except OSError:
            _fail("filesystem_identity_changed", self.field)
        try:
            current = os.fstat(reopened)
            if (
                _is_link_or_reparse(current)
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or current.st_size != self.expected_size
                or _identity(current) != self.identity
            ):
                _fail("filesystem_identity_changed", self.field)
        finally:
            os.close(reopened)


class _WindowsPublishedFile:
    def __init__(
        self,
        destination: Path,
        payload: bytes,
        mode: int,
        field: str,
    ) -> None:
        del mode
        self.api = _windows_output_api()
        self.destination = _lexical_absolute(destination, field)
        self.field = field
        if not self.destination.name:
            _fail("invalid_path", field, exit_code=2)
        self.parent_chain = _WindowsDirectoryChain(self.destination.parent, field)
        self.file_handle = 0
        try:
            _invoke_write_hook(self.destination, "before_file_open")
            self.parent_chain.require_bindings()
            self.file_handle = self.api.relative(
                self.parent_chain.leaf,
                self.destination.name,
                directory=False,
                create=True,
                field=field,
            )
            self.api.write(self.file_handle, payload, field)
            state = self.api.state(self.file_handle, field)
            if state.size != len(payload) or state.nlink != 1:
                _fail("output_write_failed", field)
            self.identity = state.identity
            self.expected_size = len(payload)
            _invoke_write_hook(self.destination, "after_file_write")
            self.require_bindings()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> _WindowsPublishedFile:
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def close(self) -> None:
        if self.file_handle:
            self.api.close(self.file_handle)
            self.file_handle = 0
        self.parent_chain.close()

    def require_bindings(self) -> None:
        self.parent_chain.require_bindings()
        retained = self.api.state(self.file_handle, self.field)
        if (
            retained.identity != self.identity
            or retained.is_reparse
            or retained.is_directory
            or retained.nlink != 1
            or retained.size != self.expected_size
        ):
            _fail("filesystem_identity_changed", self.field)
        reopened = self.api.relative(
            self.parent_chain.leaf,
            self.destination.name,
            directory=False,
            create=False,
            field=self.field,
        )
        try:
            current = self.api.state(reopened, self.field)
            if current.identity != self.identity:
                _fail("filesystem_identity_changed", self.field)
        finally:
            self.api.close(reopened)


class _PublishedFile:
    def __new__(
        cls,
        destination: Path,
        payload: bytes,
        mode: int,
        field: str,
    ) -> _PosixPublishedFile | _WindowsPublishedFile:
        if os.name == "nt":
            return _WindowsPublishedFile(destination, payload, mode, field)
        return _PosixPublishedFile(destination, payload, mode, field)


def assemble_runtime_resources(plan: AssemblyPlan, output_root: Path) -> AssemblyResult:
    """Assemble one deterministic, explicitly non-publishable resources tree."""

    _validate_plan(plan)
    if plan.python.normalization is not None and os.name == "nt":
        _fail("case_sensitive_filesystem_required", "output")
    files = _output_files(plan)
    manifest = _package_manifest(plan, files)
    validate_package_manifest(manifest)
    manifest_bytes = _canonical_json_bytes(manifest)
    try:
        loaded_manifest = load_strict_json_bytes(
            manifest_bytes,
            max_bytes=MAX_PACKAGE_MANIFEST_BYTES,
            max_depth=MAX_PACKAGE_JSON_DEPTH,
            max_nodes=MAX_PACKAGE_JSON_NODES,
        )
    except RuntimeSourcesError:
        _fail("package_manifest_limit", "manifest")
    if loaded_manifest != manifest:
        _fail("package_manifest_invalid", "manifest")
    # Failure preserves the exclusive partial tree. Cross-platform path cleanup
    # cannot prove that a name still refers to an owned object after inspection.
    with _OwnedOutput(output_root) as owner:
        for path, item in sorted(files.items(), key=lambda item: item[0].encode("utf-8")):
            owner.write(path, item.payload, item.mode)
        owner.write(PACKAGE_MANIFEST_NAME, manifest_bytes, 0o644)
        owner.require_case_sensitive_paths(
            _case_sensitive_output_groups(
                plan.python.normalization,
                f"runtime/python/{plan.target_id}",
                include_directories=True,
            ).values()
        )
        owner._require_all_bindings()
        verified = verify_runtime_tree(output_root)
        owner._require_all_bindings()
        if verified != manifest:
            _fail("output_verification_failed", "output")
    return AssemblyResult(
        output_root=output_root,
        manifest=manifest,
        files=len(files),
        bytes=sum(len(item.payload) for item in files.values()),
    )


def _scan_tree(
    root: Path,
) -> tuple[dict[str, _FileState], dict[str, _FileState]]:
    root = _lexical_absolute(root, "output")
    chain = _directory_chain(root, "output")
    files: dict[str, _FileState] = {}
    directories: dict[str, _FileState] = {}
    namespace = _output_namespace_budget("output_limit_exceeded", "output")
    stack: list[tuple[Path, PurePosixPath]] = [(root, PurePosixPath("."))]
    while stack:
        current, relative = stack.pop()
        try:
            discovered: list[tuple[bytes, str, Path, PurePosixPath, _FileState, bool]] = []
            with os.scandir(current) as entries:
                for entry in entries:
                    child_relative = (
                        PurePosixPath(entry.name)
                        if relative == PurePosixPath(".")
                        else relative / entry.name
                    )
                    portable = _portable_path(child_relative.as_posix(), "output")
                    info = entry.stat(follow_symlinks=False)
                    if _is_link_or_reparse(info):
                        _fail("output_tree_unsafe", "output")
                    is_directory = stat.S_ISDIR(info.st_mode)
                    if is_directory:
                        namespace.add_directory(portable)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        namespace.add_file(portable)
                    else:
                        _fail("output_tree_unsafe", "output")
                    discovered.append(
                        (
                            os.fsencode(entry.name),
                            portable,
                            Path(entry.path),
                            child_relative,
                            _state(info),
                            is_directory,
                        )
                    )
        except RuntimeAssemblyError:
            raise
        except OSError:
            _fail("output_tree_unsafe", "output")
        for _, portable, child, child_relative, state, is_directory in sorted(
            discovered,
            key=lambda item: item[0],
        ):
            if is_directory:
                directories[portable] = state
                stack.append((child, child_relative))
            else:
                files[portable] = state
    _require_directory_chain(chain, "output")
    return files, directories


def verify_runtime_tree(output_root: Path) -> dict[str, Any]:
    """Verify an assembled tree against its canonical package manifest."""

    root = _lexical_absolute(output_root, "output")
    try:
        manifest_initial = _state((root / PACKAGE_MANIFEST_NAME).lstat())
    except OSError:
        _fail("file_missing", "manifest")
    manifest_bytes = _read_pinned_regular(
        root / PACKAGE_MANIFEST_NAME,
        field="manifest",
        max_bytes=MAX_PACKAGE_MANIFEST_BYTES,
    )
    try:
        manifest = load_strict_json_bytes(
            manifest_bytes,
            max_bytes=MAX_PACKAGE_MANIFEST_BYTES,
            max_depth=MAX_PACKAGE_JSON_DEPTH,
            max_nodes=MAX_PACKAGE_JSON_NODES,
        )
    except RuntimeSourcesError:
        _fail("package_manifest_invalid", "manifest")
    if _canonical_json_bytes(manifest) != manifest_bytes:
        _fail("package_manifest_noncanonical", "manifest")
    normalization_payload = (
        _read_pinned_regular(
            root.joinpath(*PurePosixPath(NORMALIZATION_PACKAGE_PATH).parts),
            field="normalization",
            max_bytes=NORMALIZATION_SIZE,
        )
        if _manifest_uses_linux_pbs(manifest)
        else None
    )
    runtime_sources_payload = (
        _read_pinned_regular(
            root / RUNTIME_SOURCES_PACKAGE_PATH,
            field="runtime_sources",
            max_bytes=RUNTIME_SOURCES_SIZE,
        )
        if type(manifest) is dict
        and manifest.get("assembly_kind") == "verified_development_runtime"
        else None
    )
    validate_package_manifest(
        manifest,
        normalization_receipt=normalization_payload,
        runtime_sources_provenance=runtime_sources_payload,
    )
    expected_files = {entry["path"] for entry in manifest["inventory"]}
    expected_files.add(PACKAGE_MANIFEST_NAME)
    expected_directories = {
        PurePosixPath(path).parents[index].as_posix()
        for path in expected_files
        for index in range(len(PurePosixPath(path).parents))
        if PurePosixPath(path).parents[index] != PurePosixPath(".")
    }
    normalization = (
        _parse_archive_normalization(normalization_payload)
        if normalization_payload is not None
        else None
    )
    actual_files, actual_directories = _scan_tree(root)
    _validate_portable_path_aliases(
        actual_files,
        normalization=normalization,
        prefix=f"runtime/python/{manifest['target_id']}",
        code="output_tree_collision",
        field="output",
    )
    if set(actual_files) != expected_files or set(actual_directories) != expected_directories:
        _fail("output_inventory_mismatch", "output")
    total = 0
    for entry in manifest["inventory"]:
        payload = _read_pinned_regular(
            root.joinpath(*PurePosixPath(entry["path"]).parts),
            field="output",
            max_bytes=MAX_ARCHIVE_MEMBER_BYTES,
            expected_size=entry["size"],
            expected_sha256=entry["sha256"],
        )
        total += len(payload)
        if total > MAX_OUTPUT_BYTES:
            _fail("output_limit_exceeded", "output")
        if entry["path"] == LAUNCH_MANIFEST_NAME:
            _validate_launch_manifest_payload(payload, manifest["target_id"])
        if os.name != "nt":
            mode = stat.S_IMODE(root.joinpath(*PurePosixPath(entry["path"]).parts).lstat().st_mode)
            if mode != entry["mode"]:
                _fail("output_mode_mismatch", "output")
    try:
        manifest_final = (root / PACKAGE_MANIFEST_NAME).lstat()
    except OSError:
        _fail("filesystem_identity_changed", "output")
    if _state(manifest_final) != manifest_initial:
        _fail("filesystem_identity_changed", "output")
    for records in (actual_directories, actual_files):
        for relative, expected in records.items():
            try:
                current = root.joinpath(*PurePosixPath(relative).parts).lstat()
            except OSError:
                _fail("filesystem_identity_changed", "output")
            if _is_link_or_reparse(current) or _state(current) != expected:
                _fail("filesystem_identity_changed", "output")
    return manifest


def _zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    if type(epoch) is not int or not ZIP_MIN_EPOCH <= epoch <= ZIP_MAX_EPOCH:
        _fail("source_date_epoch_invalid", "source_date_epoch", exit_code=2)
    value = time.gmtime(epoch)
    return (
        value.tm_year,
        value.tm_mon,
        value.tm_mday,
        value.tm_hour,
        value.tm_min,
        value.tm_sec - (value.tm_sec % 2),
    )


def _canonical_zip_bytes(
    manifest: dict[str, Any],
    payloads: Mapping[str, bytes],
) -> bytes:
    names = sorted(
        [PACKAGE_MANIFEST_NAME, *(entry["path"] for entry in manifest["inventory"])],
        key=lambda item: item.encode("utf-8"),
    )
    if set(payloads) != set(names):
        _fail("zip_inventory_mismatch", "zip")
    modes = {entry["path"]: entry["mode"] for entry in manifest["inventory"]}
    modes[PACKAGE_MANIFEST_NAME] = 0o644
    timestamp = _zip_datetime(manifest["source_date_epoch"])
    output = io.BytesIO()
    try:
        with zipfile.ZipFile(
            output,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            archive.comment = b""
            for name in names:
                info = zipfile.ZipInfo(filename=name, date_time=timestamp)
                info.comment = b""
                info.extra = b""
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | modes[name]) << 16
                info.internal_attr = 0
                info.volume = 0
                archive.writestr(
                    info,
                    payloads[name],
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
    except (OSError, ValueError, zipfile.BadZipFile):
        _fail("zip_write_failed", "zip")
    result = output.getvalue()
    if not 0 < len(result) <= MAX_ZIP_BYTES:
        _fail("zip_write_failed", "zip")
    return result


def build_deterministic_zip(
    output_root: Path,
    destination: Path,
) -> str:
    """Write a byte-reproducible ZIP after fully verifying the source tree."""

    manifest = verify_runtime_tree(output_root)
    destination = _lexical_absolute(destination, "zip")
    if not destination.name:
        _fail("invalid_path", "zip", exit_code=2)
    names = sorted(
        [PACKAGE_MANIFEST_NAME, *(entry["path"] for entry in manifest["inventory"])],
        key=lambda item: item.encode("utf-8"),
    )
    payloads = {
        name: _read_pinned_regular(
            output_root.joinpath(*PurePosixPath(name).parts),
            field="output",
            max_bytes=MAX_PACKAGE_MANIFEST_BYTES
            if name == PACKAGE_MANIFEST_NAME
            else MAX_ARCHIVE_MEMBER_BYTES,
        )
        for name in names
    }
    canonical = _canonical_zip_bytes(manifest, payloads)
    with _PublishedFile(destination, canonical, 0o644, "zip") as publication:
        publication.require_bindings()
        verify_runtime_zip(destination)
        publication.require_bindings()
        return _sha256(canonical)


def verify_runtime_zip(filename: Path) -> dict[str, Any]:
    """Verify deterministic ZIP structure, metadata, manifest, and payload hashes."""

    filename = _lexical_absolute(filename, "zip")
    try:
        initial = _state(filename.lstat())
    except OSError:
        _fail("file_missing", "zip")
    raw = _read_pinned_regular(filename, field="zip", max_bytes=MAX_ZIP_BYTES)
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw), mode="r")
    except (OSError, zipfile.BadZipFile):
        _fail("zip_invalid", "zip")
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_OUTPUT_FILES + 1:
            _fail("zip_invalid", "zip")
        names: list[str] = []
        exact_names: set[str] = set()
        expanded = 0
        manifest_bytes: bytes | None = None
        payloads: dict[str, bytes] = {}
        namespace = _output_namespace_budget("zip_invalid", "zip")
        for index, info in enumerate(infos):
            name = _portable_path(info.filename, f"zip.entries[{index}]")
            if name in exact_names or name.endswith("/"):
                _fail("zip_collision", "zip")
            namespace.add_file(name)
            exact_names.add(name)
            names.append(name)
            mode_type = stat.S_IFMT(info.external_attr >> 16)
            if (
                info.create_system != 3
                or mode_type != stat.S_IFREG
                or info.compress_type != zipfile.ZIP_DEFLATED
                or info.flag_bits & 0x1
                or info.file_size > MAX_ARCHIVE_MEMBER_BYTES
            ):
                _fail("zip_entry_unsafe", "zip")
            expanded += info.file_size
            if (
                expanded > MAX_OUTPUT_BYTES + MAX_PACKAGE_MANIFEST_BYTES
                or expanded > max(len(raw), 1) * MAX_ARCHIVE_EXPANSION_RATIO
            ):
                _fail("zip_expansion_limit", "zip")
            payload = archive.read(info)
            if len(payload) != info.file_size:
                _fail("zip_invalid", "zip")
            payloads[name] = payload
            if name == PACKAGE_MANIFEST_NAME:
                manifest_bytes = payload
        if names != sorted(names, key=lambda item: item.encode("utf-8")):
            _fail("zip_noncanonical", "zip")
        if manifest_bytes is None:
            _fail("zip_manifest_missing", "zip")
        try:
            manifest = load_strict_json_bytes(
                manifest_bytes,
                max_bytes=MAX_PACKAGE_MANIFEST_BYTES,
                max_depth=MAX_PACKAGE_JSON_DEPTH,
                max_nodes=MAX_PACKAGE_JSON_NODES,
            )
        except RuntimeSourcesError:
            _fail("package_manifest_invalid", "zip")
        if _canonical_json_bytes(manifest) != manifest_bytes:
            _fail("package_manifest_noncanonical", "zip")
        normalization_payload = (
            payloads.get(NORMALIZATION_PACKAGE_PATH, b"")
            if _manifest_uses_linux_pbs(manifest)
            else None
        )
        runtime_sources_payload = (
            payloads.get(RUNTIME_SOURCES_PACKAGE_PATH, b"")
            if type(manifest) is dict
            and manifest.get("assembly_kind") == "verified_development_runtime"
            else None
        )
        validate_package_manifest(
            manifest,
            normalization_receipt=normalization_payload,
            runtime_sources_provenance=runtime_sources_payload,
        )
        normalization = (
            _parse_archive_normalization(normalization_payload)
            if normalization_payload is not None
            else None
        )
        _validate_portable_path_aliases(
            names,
            normalization=normalization,
            prefix=f"runtime/python/{manifest['target_id']}",
            code="zip_collision",
            field="zip",
        )
        launch_payload = payloads.get(LAUNCH_MANIFEST_NAME)
        if launch_payload is None:
            _fail("zip_inventory_mismatch", "zip")
        _validate_launch_manifest_payload(launch_payload, manifest["target_id"])
        expected_names = sorted(
            [PACKAGE_MANIFEST_NAME, *(entry["path"] for entry in manifest["inventory"])],
            key=lambda item: item.encode("utf-8"),
        )
        if names != expected_names:
            _fail("zip_inventory_mismatch", "zip")
        timestamp = _zip_datetime(manifest["source_date_epoch"])
        by_name = {info.filename: info for info in infos}
        for entry in manifest["inventory"]:
            info = by_name[entry["path"]]
            if (
                info.date_time != timestamp
                or stat.S_IMODE(info.external_attr >> 16) != entry["mode"]
                or len(payloads[entry["path"]]) != entry["size"]
                or _sha256(payloads[entry["path"]]) != entry["sha256"]
            ):
                _fail("zip_inventory_mismatch", "zip")
        manifest_info = by_name[PACKAGE_MANIFEST_NAME]
        if (
            manifest_info.date_time != timestamp
            or stat.S_IMODE(manifest_info.external_attr >> 16) != 0o644
        ):
            _fail("zip_noncanonical", "zip")
        if raw != _canonical_zip_bytes(manifest, payloads):
            _fail("zip_noncanonical", "zip")
        try:
            final = filename.lstat()
        except OSError:
            _fail("filesystem_identity_changed", "zip")
        if _is_link_or_reparse(final) or _state(final) != initial:
            _fail("filesystem_identity_changed", "zip")
        return manifest


def archive_spec_from_bytes(
    *,
    component: str,
    path: Path,
    payload_root: str,
    entrypoint: str,
    expected_inventory: tuple[FilePin, ...] | None,
) -> ArchiveSpec:
    """Create a synthetic ArchiveSpec from test-owned bytes without publishing it."""

    payload = _read_pinned_regular(
        path,
        field=f"archive.{component}",
        max_bytes=MAX_ARCHIVE_BYTES,
    )
    return ArchiveSpec(
        component=component,
        path=path,
        filename=path.name,
        size=len(payload),
        sha256=_sha256(payload),
        payload_root=payload_root,
        entrypoint=entrypoint,
        expected_inventory=expected_inventory,
    )


def _document_target(document: dict[str, Any], collection: str, target_id: str) -> dict[str, Any]:
    try:
        return next(
            target for target in document[collection]["targets"] if target["target_id"] == target_id
        )
    except (KeyError, StopIteration, TypeError):
        _fail("runtime_sources_invalid", "source")


def _production_plan(
    document: dict[str, Any],
    source_bytes: bytes,
    *,
    target_id: str,
    cache_dir: Path,
    source_date_epoch: int,
) -> AssemblyPlan:
    codex_target = _document_target(document, "codex", target_id)
    python_target = _document_target(document, "python", target_id)
    codex_archive = codex_target["archive"]
    python_archive = python_target["runtime_archive"]
    codex_inventory = tuple(
        FilePin(path=item["path"], size=item["size"], sha256=item["sha256"])
        for item in codex_target["inventory"]
    )
    return AssemblyPlan(
        target_id=target_id,
        assembly_kind="verified_development_runtime",
        runtime_sources_sha256=_sha256(source_bytes),
        source_date_epoch=source_date_epoch,
        codex=ArchiveSpec(
            component="codex",
            path=cache_dir / target_id / "codex-package" / codex_archive["filename"],
            filename=codex_archive["filename"],
            size=codex_archive["size"],
            sha256=codex_archive["sha256"],
            payload_root=codex_target["payload_root"],
            entrypoint=codex_target["entrypoint"],
            expected_inventory=codex_inventory,
        ),
        python=ArchiveSpec(
            component="python",
            path=cache_dir / target_id / "python-runtime" / python_archive["filename"],
            filename=python_archive["filename"],
            size=python_archive["size"],
            sha256=python_archive["sha256"],
            payload_root=python_target["payload_root"],
            entrypoint=python_target["entrypoint"],
            expected_inventory=None,
            normalization=(_load_archive_normalization() if target_id == "linux-x64" else None),
        ),
        forge_source_root=ROOT,
        open_blocker_codes=("development_runtime_not_publishable",),
    )


def assemble_from_committed_sources(
    *,
    target_id: str,
    cache_dir: Path,
    output_root: Path,
    source_date_epoch: int,
    source: Path = DEFAULT_SOURCE,
) -> AssemblyResult:
    """Gate real assembly on the authoritative redistribution assertion."""

    source_bytes = _read_pinned_regular(
        source,
        field="source",
        max_bytes=MAX_MANIFEST_BYTES,
    )
    try:
        document = load_strict_json_bytes(source_bytes)
        report = validate_document(document)
    except RuntimeSourcesError:
        _fail("runtime_sources_invalid", "source")
    try:
        require_redistributable(document)
    except RuntimeSourcesError:
        _fail(
            "redistribution_blocked",
            "source",
            blockers=report.open_blocker_codes,
        )
    target_id = str(target_id)
    if target_id not in TARGET_IDS:
        _fail("unsupported_target", "target", exit_code=2)
    cache = _lexical_absolute(cache_dir, "cache")
    output = _lexical_absolute(output_root, "output")
    plan = _production_plan(
        document,
        source_bytes,
        target_id=target_id,
        cache_dir=cache,
        source_date_epoch=source_date_epoch,
    )
    return assemble_runtime_resources(plan, output)


class _ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, _message: str) -> NoReturn:
        _fail("invalid_argument", "cli", exit_code=2)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        description="Assemble or verify deterministic Studio runtime resources."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    assemble = subparsers.add_parser("assemble")
    assemble.add_argument("--target", required=True, choices=TARGET_IDS)
    assemble.add_argument("--cache-dir", required=True, type=Path)
    assemble.add_argument("--output-dir", required=True, type=Path)
    assemble.add_argument("--source-date-epoch", required=True, type=int)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--output-dir", required=True, type=Path)
    verify_zip = subparsers.add_parser("verify-zip")
    verify_zip.add_argument("--zip", required=True, type=Path)
    return parser


def _print_json(value: object, *, stream: Any = sys.stdout) -> None:
    print(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        file=stream,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "assemble":
            result = assemble_from_committed_sources(
                target_id=args.target,
                cache_dir=args.cache_dir,
                output_root=args.output_dir,
                source_date_epoch=args.source_date_epoch,
            )
            payload: object = {
                "format": "rpg-world-forge.studio_runtime_assembly",
                "format_version": 1,
                "valid": True,
                "files": result.files,
                "bytes": result.bytes,
                "release_ready": False,
            }
        elif args.command == "verify":
            manifest = verify_runtime_tree(args.output_dir)
            payload = {
                "format": "rpg-world-forge.studio_runtime_assembly_verification",
                "format_version": 1,
                "valid": True,
                "target_id": manifest["target_id"],
                "release_ready": False,
            }
        elif args.command == "verify-zip":
            manifest = verify_runtime_zip(args.zip)
            payload = {
                "format": "rpg-world-forge.studio_runtime_zip_verification",
                "format_version": 1,
                "valid": True,
                "target_id": manifest["target_id"],
                "release_ready": False,
            }
        else:
            _fail("invalid_argument", "cli", exit_code=2)
    except RuntimeAssemblyError as exc:
        _print_json(
            {
                "format": "rpg-world-forge.studio_runtime_assembly_error",
                "format_version": 1,
                "valid": False,
                "error": exc.as_dict(),
            },
            stream=sys.stderr,
        )
        return exc.exit_code
    except Exception:
        error = RuntimeAssemblyError("internal_error", "assembly")
        _print_json(
            {
                "format": "rpg-world-forge.studio_runtime_assembly_error",
                "format_version": 1,
                "valid": False,
                "error": error.as_dict(),
            },
            stream=sys.stderr,
        )
        return 1
    _print_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
