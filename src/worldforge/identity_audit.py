from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from isoworld.content.portability import portable_path_key, portable_relative_path
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.asset_io import AssetContractError, write_json_cooperative_replace
from worldforge.integrity import canonical_json_bytes
from worldforge.retained_tree import (
    RetainedTreeError,
    RetainedTreeHook,
    RetainedTreeSnapshot,
    capture_retained_tree,
)

ALLOWLIST_FORMAT = "world-forge.legacy_identity_allowlist"
ALLOWLIST_VERSION = 1
DEFAULT_ALLOWLIST_PATH = PurePosixPath("contracts/legacy-identity-allowlist.json")
LEGACY_PATTERNS = (
    "-".join(("rpg", "world", "forge")),
    " ".join(("RPG", "World", "Forge")),
)
ALLOWED_CATEGORIES = frozenset(
    {
        "compatibility_reader",
        "historical_provenance",
        "legacy_contract",
        "license_third_party_notice",
        "migration",
        "regression_fixture",
    }
)
_ENTRY_KEYS = frozenset(
    {
        "category",
        "file_sha256",
        "justification",
        "offsets",
        "path",
        "pattern",
    }
)
_LEGACY_COUNT_ENTRY_KEYS = frozenset({"category", "count", "justification", "path", "pattern"})
_TOP_LEVEL_KEYS = frozenset({"entries", "format", "format_version"})
_EXCLUDED_ROOT_RELATIVES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "release",
        "venv",
        "apps/studio/coverage",
        "apps/studio/dist-electron",
        "apps/studio/dist-renderer",
        "apps/studio/node_modules",
        "apps/studio/scripts/__pycache__",
        "scripts/__pycache__",
        "src/gamepack_raylib_2d/__pycache__",
        "src/gamepack_runtime/__pycache__",
        "src/isoworld/__pycache__",
        "src/isoworld/content/__pycache__",
        "src/isoworld/core/__pycache__",
        "src/isoworld/render/__pycache__",
        "src/isoworld/world/__pycache__",
        "src/worldforge/__pycache__",
        "src/worldforge/asset_formats/__pycache__",
        "src/worldforge/studio/__pycache__",
        "tests/__pycache__",
    }
)
_MAX_ALLOWLIST_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _CategoryPathPolicy:
    exact_paths: frozenset[str] = frozenset()
    directory_paths: frozenset[str] = frozenset()
    segment_prefixes: tuple[str, ...] = ()


_CATEGORY_PATH_POLICIES = {
    "compatibility_reader": _CategoryPathPolicy(
        exact_paths=frozenset({"README.md", "pyproject.toml"}),
        directory_paths=frozenset({"apps", "docs", "scripts", "src"}),
    ),
    "historical_provenance": _CategoryPathPolicy(
        directory_paths=frozenset({"docs/audits", "docs/decisions"}),
        segment_prefixes=("docs/M5_", "docs/M6_"),
    ),
    "legacy_contract": _CategoryPathPolicy(
        exact_paths=frozenset({"contracts/catalog.json"}),
        directory_paths=frozenset({"apps", "authoring", "docs", "schemas", "scripts", "src"}),
    ),
    "license_third_party_notice": _CategoryPathPolicy(
        exact_paths=frozenset({"LICENSE"}),
        directory_paths=frozenset({"apps/studio/packaging/notices", "docs/licenses"}),
        segment_prefixes=("THIRD_PARTY",),
    ),
    "migration": _CategoryPathPolicy(
        exact_paths=frozenset({"MANIFEST.in", "README.md", "pyproject.toml"}),
        directory_paths=frozenset({".github", "docs", "scripts", "src"}),
    ),
    "regression_fixture": _CategoryPathPolicy(
        directory_paths=frozenset({"apps/studio/tests", "examples", "tests"}),
    ),
}


class IdentityAuditError(ValueError):
    """Raised when legacy public identity references are not exactly justified."""


@dataclass(frozen=True, slots=True)
class IdentityAuditResult:
    allowlist_path: Path
    entries: int
    occurrences: int


@dataclass(frozen=True, slots=True)
class ReviewedIdentityPolicy:
    """Human-reviewed classification required before a generator may add a row."""

    category: str
    justification: str


@dataclass(frozen=True, slots=True)
class _AllowlistEntry:
    category: str
    file_sha256: str
    justification: str
    offsets: tuple[int, ...]
    path: str
    pattern: str

    @property
    def count(self) -> int:
        return len(self.offsets)


@dataclass(frozen=True, slots=True)
class _LoadedAllowlist:
    entries: tuple[_AllowlistEntry, ...]
    raw_bytes: bytes


@dataclass(frozen=True, slots=True)
class _ScannedEvidence:
    file_hashes: dict[str, str]
    occurrences: dict[tuple[str, str], tuple[int, ...]]


def _portable_relative_path(value: object, *, context: str) -> str:
    if isinstance(value, str) and "*" in value:
        raise IdentityAuditError(f"{context}: path must not contain wildcards")
    try:
        path = portable_relative_path(value)
    except UnicodeError as exc:
        raise IdentityAuditError(f"{context}: path must be portable UTF-8 text") from exc
    if path is None:
        raise IdentityAuditError(f"{context}: path must be a portable relative path")
    return path.as_posix()


def _category_accepts_path(category: str, relative: str) -> bool:
    policy = _CATEGORY_PATH_POLICIES.get(category)
    path = portable_relative_path(relative)
    if policy is None or path is None:
        return False
    canonical = path.as_posix()
    if canonical in policy.exact_paths:
        return True
    if any(
        canonical == directory or canonical.startswith(f"{directory}/")
        for directory in policy.directory_paths
    ):
        return True
    return any(
        path.parent == PurePosixPath(prefix).parent
        and path.name.startswith(PurePosixPath(prefix).name)
        for prefix in policy.segment_prefixes
    )


def _file_state(info: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_standalone_bytes(path: Path, *, limit: int, context: str) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise IdentityAuditError(f"{context}: not a standalone regular file")
        if opened.st_size > limit:
            raise IdentityAuditError(f"{context}: exceeds the {limit}-byte limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise IdentityAuditError(f"{context}: exceeds the {limit}-byte limit")
        final = os.fstat(descriptor)
        named = path.lstat()
        if _file_state(opened) != _file_state(final) or _file_state(opened) != _file_state(named):
            raise IdentityAuditError(f"{context}: file identity changed while reading")
        return b"".join(chunks)
    except IdentityAuditError:
        raise
    except OSError as exc:
        raise IdentityAuditError(f"{context}: could not be read safely: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_allowlist(path: Path) -> _LoadedAllowlist:
    try:
        raw_bytes = _read_standalone_bytes(
            path,
            limit=_MAX_ALLOWLIST_BYTES,
            context="allowlist",
        )
        document = decode_json_object(raw_bytes, source=path)
    except RuntimeIOError as exc:
        raise IdentityAuditError(f"allowlist could not be read safely: {exc}") from exc
    if raw_bytes != canonical_json_bytes(document):
        raise IdentityAuditError("allowlist must use canonical sorted JSON bytes")
    if set(document) != _TOP_LEVEL_KEYS:
        raise IdentityAuditError("allowlist top-level fields are not closed")
    if (
        document.get("format") != ALLOWLIST_FORMAT
        or document.get("format_version") != ALLOWLIST_VERSION
    ):
        raise IdentityAuditError("allowlist format or format_version is unsupported")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list):
        raise IdentityAuditError("allowlist entries must be an array")
    entries: list[_AllowlistEntry] = []
    keys: set[tuple[str, str]] = set()
    portable_paths: dict[tuple[str, ...], str] = {}
    file_hashes: dict[str, str] = {}
    spans: dict[str, list[tuple[int, int, str]]] = {}
    for index, raw in enumerate(raw_entries):
        context = f"entries/{index}"
        if not isinstance(raw, dict) or set(raw) != _ENTRY_KEYS:
            raise IdentityAuditError(f"{context}: fields are not closed")
        category = raw.get("category")
        if not isinstance(category, str) or category not in ALLOWED_CATEGORIES:
            raise IdentityAuditError(f"{context}: category is unsupported")
        relative = _portable_relative_path(raw.get("path"), context=f"{context}/path")
        if not _category_accepts_path(category, relative):
            raise IdentityAuditError(f"{context}: category is not valid for path {relative}")
        path_key = portable_path_key(PurePosixPath(relative))
        previous_path = portable_paths.setdefault(path_key, relative)
        if previous_path != relative:
            raise IdentityAuditError(f"{context}: allowlist path collision with {previous_path}")
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or pattern not in LEGACY_PATTERNS:
            raise IdentityAuditError(f"{context}: pattern is unsupported")
        file_sha256 = raw.get("file_sha256")
        if (
            not isinstance(file_sha256, str)
            or len(file_sha256) != 64
            or any(character not in "0123456789abcdef" for character in file_sha256)
        ):
            raise IdentityAuditError(f"{context}: file_sha256 must be a lowercase SHA-256")
        previous_hash = file_hashes.setdefault(relative, file_sha256)
        if previous_hash != file_sha256:
            raise IdentityAuditError(
                f"{context}: file_sha256 conflicts with another row for {relative}"
            )
        raw_offsets = raw.get("offsets")
        if (
            not isinstance(raw_offsets, list)
            or not raw_offsets
            or any(
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or offset < 0
                or offset > (2**63 - 1)
                for offset in raw_offsets
            )
        ):
            raise IdentityAuditError(
                f"{context}: offsets must be a non-empty array of non-negative integers"
            )
        offsets = tuple(raw_offsets)
        if tuple(sorted(set(offsets))) != offsets:
            raise IdentityAuditError(f"{context}: offsets must be strictly increasing")
        justification = raw.get("justification")
        if not isinstance(justification, str) or not justification.strip():
            raise IdentityAuditError(f"{context}: justification must be non-empty")
        key = (relative, pattern)
        if key in keys:
            raise IdentityAuditError(f"{context}: duplicate path/pattern entry")
        keys.add(key)
        entries.append(
            _AllowlistEntry(
                category=category,
                file_sha256=file_sha256,
                justification=justification,
                offsets=offsets,
                path=relative,
                pattern=pattern,
            )
        )
        needle_bytes = len(pattern.encode("ascii"))
        spans.setdefault(relative, []).extend(
            (offset, offset + needle_bytes, pattern) for offset in offsets
        )
    for relative, file_spans in spans.items():
        previous_end = -1
        for start, end, _pattern in sorted(file_spans):
            if start < previous_end:
                raise IdentityAuditError(f"allowlist offsets overlap in reviewed file {relative}")
            previous_end = end
    return _LoadedAllowlist(entries=tuple(entries), raw_bytes=raw_bytes)


def _load_refreshable_allowlist(path: Path) -> _LoadedAllowlist:
    raw_bytes = _read_standalone_bytes(
        path,
        limit=_MAX_ALLOWLIST_BYTES,
        context="allowlist",
    )
    try:
        document = decode_json_object(raw_bytes, source=path)
    except RuntimeIOError as exc:
        raise IdentityAuditError(f"allowlist could not be read safely: {exc}") from exc
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list) or all(
        isinstance(raw, dict) and set(raw) == _ENTRY_KEYS for raw in raw_entries
    ):
        return _load_allowlist(path)
    if (
        raw_bytes != canonical_json_bytes(document)
        or set(document) != _TOP_LEVEL_KEYS
        or document.get("format") != ALLOWLIST_FORMAT
        or document.get("format_version") != ALLOWLIST_VERSION
        or any(
            not isinstance(raw, dict) or set(raw) != _LEGACY_COUNT_ENTRY_KEYS for raw in raw_entries
        )
    ):
        return _load_allowlist(path)

    entries: list[_AllowlistEntry] = []
    keys: set[tuple[str, str]] = set()
    portable_paths: dict[tuple[str, ...], str] = {}
    for index, raw in enumerate(raw_entries):
        context = f"entries/{index}"
        category = raw.get("category")
        if not isinstance(category, str) or category not in ALLOWED_CATEGORIES:
            raise IdentityAuditError(f"{context}: category is unsupported")
        relative = _portable_relative_path(raw.get("path"), context=f"{context}/path")
        if not _category_accepts_path(category, relative):
            raise IdentityAuditError(f"{context}: category is not valid for path {relative}")
        path_key = portable_path_key(PurePosixPath(relative))
        previous_path = portable_paths.setdefault(path_key, relative)
        if previous_path != relative:
            raise IdentityAuditError(f"{context}: allowlist path collision with {previous_path}")
        pattern = raw.get("pattern")
        if not isinstance(pattern, str) or pattern not in LEGACY_PATTERNS:
            raise IdentityAuditError(f"{context}: pattern is unsupported")
        count = raw.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise IdentityAuditError(f"{context}: count must be a positive integer")
        justification = raw.get("justification")
        if not isinstance(justification, str) or not justification.strip():
            raise IdentityAuditError(f"{context}: justification must be non-empty")
        key = (relative, pattern)
        if key in keys:
            raise IdentityAuditError(f"{context}: duplicate path/pattern entry")
        keys.add(key)
        stride = len(pattern.encode("ascii")) + 1
        entries.append(
            _AllowlistEntry(
                category=category,
                file_sha256="0" * 64,
                justification=justification,
                offsets=tuple(index * stride for index in range(count)),
                path=relative,
                pattern=pattern,
            )
        )
    return _LoadedAllowlist(entries=tuple(entries), raw_bytes=raw_bytes)


def _is_excluded_root_relative(relative: str) -> bool:
    return relative in _EXCLUDED_ROOT_RELATIVES or "__pycache__" in PurePosixPath(relative).parts


def _is_python_bytecode(relative: str) -> bool:
    return PurePosixPath(relative).suffix.casefold() in {".pyc", ".pyo"}


def _pattern_offsets(payload: bytes, pattern: str) -> tuple[int, ...]:
    needle = pattern.encode("ascii")
    offsets: list[int] = []
    position = 0
    while True:
        found = payload.find(needle, position)
        if found < 0:
            return tuple(offsets)
        offsets.append(found)
        position = found + len(needle)


def _scanned_evidence(
    snapshot: RetainedTreeSnapshot,
    allowlist_relative: str,
) -> _ScannedEvidence:
    occurrences: dict[tuple[str, str], tuple[int, ...]] = {}
    file_hashes: dict[str, str] = {}
    for relative, payload in snapshot.files.items():
        if _is_python_bytecode(relative):
            continue
        file_hashes[relative] = hashlib.sha256(payload).hexdigest()
        if relative == allowlist_relative:
            continue
        for pattern in LEGACY_PATTERNS:
            offsets = _pattern_offsets(payload, pattern)
            if offsets:
                occurrences[(relative, pattern)] = offsets
    return _ScannedEvidence(file_hashes=file_hashes, occurrences=occurrences)


def _capture_source_tree(
    root: Path,
    *,
    verification_hook: RetainedTreeHook | None,
) -> RetainedTreeSnapshot:
    try:
        return capture_retained_tree(
            root,
            exclude_directory=_is_excluded_root_relative,
            verification_hook=verification_hook,
        )
    except RetainedTreeError as exc:
        detail = str(exc)
        if "multiple hard links" in detail:
            raise IdentityAuditError(
                f"hard-linked source entry cannot be scanned: {detail}"
            ) from exc
        if "linked or reparse-backed" in detail:
            raise IdentityAuditError(f"unsafe source entry cannot be scanned: {detail}") from exc
        raise IdentityAuditError(f"source tree changed or is unsafe: {exc}") from exc


def _validate_reviewed_rows(
    entries: tuple[_AllowlistEntry, ...],
    evidence: _ScannedEvidence,
) -> None:
    reviewed = {(entry.path, entry.pattern): entry for entry in entries}
    for (relative, pattern), offsets in sorted(evidence.occurrences.items()):
        if (relative, pattern) not in reviewed:
            raise IdentityAuditError(
                f"unallowlisted legacy identity {pattern!r} in {relative} "
                f"(offsets={','.join(str(offset) for offset in offsets)})"
            )
    for entry in entries:
        actual_hash = evidence.file_hashes.get(entry.path)
        if actual_hash is None:
            raise IdentityAuditError(
                f"stale allowlist entry for missing reviewed file {entry.path}"
            )
        if actual_hash != entry.file_sha256:
            raise IdentityAuditError(
                f"stale allowlist file hash for {entry.path} {entry.pattern!r}: "
                f"expected {entry.file_sha256}, observed {actual_hash}"
            )
        actual_offsets = evidence.occurrences.get((entry.path, entry.pattern), ())
        if actual_offsets != entry.offsets:
            raise IdentityAuditError(
                f"stale allowlist entry offsets for {entry.path} {entry.pattern!r}: "
                f"expected {','.join(str(offset) for offset in entry.offsets)}, "
                f"observed {','.join(str(offset) for offset in actual_offsets)}"
            )


def _resolve_audit_paths(
    source_root: str | Path,
    allowlist_path: str | Path | None,
) -> tuple[Path, Path, str]:
    root = Path(os.path.abspath(os.fspath(source_root)))
    allowlist_input = (
        Path(allowlist_path) if allowlist_path is not None else root / DEFAULT_ALLOWLIST_PATH
    )
    allowlist = Path(os.path.abspath(allowlist_input))
    try:
        allowlist_relative = allowlist.relative_to(root).as_posix()
    except ValueError as exc:
        raise IdentityAuditError("allowlist must be contained by source root") from exc
    return root, allowlist, allowlist_relative


def audit_identities(
    source_root: str | Path,
    *,
    allowlist_path: str | Path | None = None,
    _verification_hook: RetainedTreeHook | None = None,
) -> IdentityAuditResult:
    root, allowlist, allowlist_relative = _resolve_audit_paths(
        source_root,
        allowlist_path,
    )
    loaded = _load_allowlist(allowlist)
    snapshot = _capture_source_tree(root, verification_hook=_verification_hook)
    if snapshot.files.get(allowlist_relative) != loaded.raw_bytes:
        raise IdentityAuditError("allowlist identity or bytes changed during source capture")
    evidence = _scanned_evidence(snapshot, allowlist_relative)
    _validate_reviewed_rows(loaded.entries, evidence)
    return IdentityAuditResult(
        allowlist_path=allowlist,
        entries=len(loaded.entries),
        occurrences=sum(len(offsets) for offsets in evidence.occurrences.values()),
    )


def refresh_identity_allowlist_evidence(
    source_root: str | Path,
    *,
    allowlist_path: str | Path | None = None,
    reviewed_policy: Mapping[tuple[str, str], ReviewedIdentityPolicy] | None = None,
    _verification_hook: RetainedTreeHook | None = None,
) -> IdentityAuditResult:
    """Rebind reviewed semantic rows to exact retained file bytes and offsets."""

    root, allowlist, allowlist_relative = _resolve_audit_paths(
        source_root,
        allowlist_path,
    )
    loaded = _load_refreshable_allowlist(allowlist)
    snapshot = _capture_source_tree(root, verification_hook=_verification_hook)
    if snapshot.files.get(allowlist_relative) != loaded.raw_bytes:
        raise IdentityAuditError("allowlist identity or bytes changed during source capture")
    evidence = _scanned_evidence(snapshot, allowlist_relative)
    reviewed_entries = list(loaded.entries)
    reviewed_keys = {(entry.path, entry.pattern) for entry in reviewed_entries}
    policy = {} if reviewed_policy is None else dict(reviewed_policy)
    for (relative, pattern), offsets in sorted(evidence.occurrences.items()):
        key = (relative, pattern)
        if key in reviewed_keys:
            continue
        classification = policy.get(key)
        if classification is None:
            raise IdentityAuditError(
                f"unallowlisted legacy identity {pattern!r} in {relative} "
                f"(offsets={','.join(str(offset) for offset in offsets)})"
            )
        if (
            not isinstance(classification, ReviewedIdentityPolicy)
            or classification.category not in ALLOWED_CATEGORIES
        ):
            raise IdentityAuditError(f"reviewed policy category is invalid for {relative}")
        if not _category_accepts_path(classification.category, relative):
            raise IdentityAuditError(f"reviewed policy category is not valid for path {relative}")
        if not classification.justification.strip():
            raise IdentityAuditError(
                f"reviewed policy justification must be non-empty for {relative}"
            )
        actual_hash = evidence.file_hashes.get(relative)
        if actual_hash is None:
            raise IdentityAuditError(f"reviewed policy source is missing for {relative}")
        reviewed_entries.append(
            _AllowlistEntry(
                category=classification.category,
                file_sha256=actual_hash,
                justification=classification.justification,
                offsets=offsets,
                path=relative,
                pattern=pattern,
            )
        )
        reviewed_keys.add(key)
    rebound_entries: list[dict[str, object]] = []
    for entry in reviewed_entries:
        actual_hash = evidence.file_hashes.get(entry.path)
        actual_offsets = evidence.occurrences.get((entry.path, entry.pattern), ())
        if actual_hash is None or not actual_offsets:
            raise IdentityAuditError(f"stale allowlist entry for {entry.path} {entry.pattern!r}")
        rebound_entries.append(
            {
                "category": entry.category,
                "file_sha256": actual_hash,
                "justification": entry.justification,
                "offsets": list(actual_offsets),
                "path": entry.path,
                "pattern": entry.pattern,
            }
        )
    document = {
        "entries": rebound_entries,
        "format": ALLOWLIST_FORMAT,
        "format_version": ALLOWLIST_VERSION,
    }
    try:
        write_json_cooperative_replace(
            allowlist,
            document,
            durable_parent=True,
        )
    except AssetContractError as exc:
        raise IdentityAuditError(f"allowlist could not be refreshed safely: {exc}") from exc
    return audit_identities(root, allowlist_path=allowlist)
