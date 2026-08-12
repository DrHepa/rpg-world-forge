"""Independent verification for one immutable standalone game distribution."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import stat
import tomllib
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from gamepack_runtime.distribution_names import (
    normalize_distribution_name,
    requirement_distribution_name,
)
from gamepack_runtime.file_stat import (
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
)

STANDALONE_GAME_FORMAT = "world-forge.standalone_game"
STANDALONE_GAME_LOCK_FORMAT = "world-forge.standalone_game_lock"
STANDALONE_PLATFORM_FORMAT = "world-forge.standalone_platform"
STANDALONE_CONTRACT_VERSION = 1

GAME_MANIFEST_PATH = "game-manifest.json"
GAME_LOCK_PATH = "game.lock.json"
PLATFORM_LOCK_PATH = "platform.lock.json"
RUNTIME_BUNDLE_ROOT = "game_data/runtime-bundle"
RUNTIME_BUNDLE_MANIFEST_PATH = f"{RUNTIME_BUNDLE_ROOT}/game-runtime-bundle.json"
RUNTIME_IMPLEMENTATION_PATH = "game_data/contracts/runtime-implementation.json"
RUNTIME_PLATFORM_LOCK_ROOT = "game_data/contracts/platform-locks"

MAX_STANDALONE_FILES = 768
MAX_STANDALONE_DIRECTORIES = 768
MAX_STANDALONE_FILE_BYTES = 32 * 1024 * 1024
MAX_STANDALONE_TOTAL_BYTES = 256 * 1024 * 1024
MAX_STANDALONE_JSON_BYTES = 4 * 1024 * 1024

_SAFE_INTEGER = 9_007_199_254_740_991
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ID_RE = re.compile(r"[a-z][a-z0-9_]{1,95}")
_GAME_ID_RE = re.compile(r"[a-z][a-z0-9_]{1,63}")
_RESERVED = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_MANIFEST_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "game_id",
        "state",
        "lineage",
        "materialization_bundle",
        "runtime_implementation",
        "platform_set",
        "payload_lock",
        "entry_points",
        "content_hash",
    }
)
_LINEAGE_FIELDS = frozenset(
    {
        "gamepack_hash",
        "assetpack_hash",
        "runtime_snapshot_hash",
        "runtime_composition_hash",
        "runtime_bundle_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_PAYLOAD_LOCK_IDENTITY_FIELDS = frozenset(
    {"format", "format_version", "id", "content_hash", "tree_hash"}
)
_ENTRY_POINT_FIELDS = frozenset({"game", "verifier", "offline_smoke", "native_smoke"})
_LOCK_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "lock_id",
        "files",
        "tree_hash",
        "content_hash",
    }
)
_FILE_FIELDS = frozenset({"path", "sha256", "size_bytes"})
_PLATFORM_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "platform_set_id",
        "requires_python",
        "dependency",
        "adapter",
        "runtime_implementation",
        "runtime_snapshot",
        "platform_locks",
        "content_hash",
    }
)
_DEPENDENCY_FIELDS = frozenset({"distribution", "version", "pin", "import_module", "native_api"})
_ADAPTER_FIELDS = frozenset({"adapter_id", "adapter_version", "content_hash"})
_IMPLEMENTATION_FIELDS = frozenset({"implementation_id", "content_hash"})
_SNAPSHOT_FIELDS = frozenset({"snapshot_id", "content_hash", "tree_hash"})
_PLATFORM_LOCK_FIELDS = frozenset({"lock_id", "content_hash", "os", "python_minor", "abi"})
_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "anthropic",
        "ftplib",
        "http",
        "huggingface_hub",
        "isoworld",
        "ollama",
        "openai",
        "requests",
        "smtplib",
        "socket",
        "subprocess",
        "urllib",
        "worldforge",
    }
)
_FORBIDDEN_CALL_NAMES = frozenset({"compile", "eval", "exec", "__import__"})
_FORBIDDEN_OS_CALLS = frozenset(
    {
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "popen",
        "posix_spawn",
        "posix_spawnp",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "system",
    }
)
_FORBIDDEN_PATH_COMPONENTS = frozenset(
    {
        ".agents",
        ".worldforge",
        "agents",
        "authoring",
        "models",
        "prompts",
        "providers",
        "source-project",
        "source_projects",
    }
)
_ALLOWED_ENTRY_POINTS = {
    "game": "run_game.py",
    "verifier": "scripts/verify_game.py",
    "offline_smoke": "scripts/offline_smoke.py",
    "native_smoke": "scripts/native_smoke.py",
}
_FORBIDDEN_FORGE_DISTRIBUTIONS = frozenset(
    {
        normalize_distribution_name("-".join(("rpg", "world", "forge"))),
        normalize_distribution_name("-".join(("world", "forge"))),
    }
)


class StandaloneDistributionError(ValueError):
    """Raised when distributed game bytes do not satisfy the closed boundary."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise StandaloneDistributionError(reason_code, detail)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("standalone_game_json_invalid", f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_float(_value: str) -> None:
    _fail("standalone_game_json_invalid", "JSON decimal/exponent numbers are unsupported")


def _reject_constant(_value: str) -> None:
    _fail("standalone_game_json_invalid", "non-finite JSON values are unsupported")


def decode_json_object(payload: bytes, context: str) -> dict[str, Any]:
    if type(payload) is not bytes or len(payload) > MAX_STANDALONE_JSON_BYTES:
        _fail("standalone_game_json_invalid", f"{context} exceeds its byte limit")
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except StandaloneDistributionError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        _fail("standalone_game_json_invalid", f"{context}: {exc}")
    if type(value) is not dict:
        _fail("standalone_game_json_invalid", f"{context} must contain one object")
    _validate_json(value, context)
    return value


def _validate_json(value: object, context: str) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if depth > 64 or nodes > 100_000:
            _fail("standalone_game_json_invalid", f"{context} exceeds structural limits")
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str or unicodedata.normalize("NFC", key) != key:
                    _fail("standalone_game_json_invalid", f"{context} has a non-NFC key")
                stack.append((item, depth + 1))
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
        elif current is None or type(current) is bool:
            continue
        elif type(current) is int:
            if not -_SAFE_INTEGER <= current <= _SAFE_INTEGER:
                _fail("standalone_game_json_invalid", f"{context} has an unsafe integer")
        elif type(current) is str:
            if unicodedata.normalize("NFC", current) != current:
                _fail("standalone_game_json_invalid", f"{context} has non-NFC text")
            try:
                current.encode("utf-8", errors="strict")
            except UnicodeError:
                _fail("standalone_game_json_invalid", f"{context} has invalid Unicode")
        else:
            _fail("standalone_game_json_invalid", f"{context} has an unsupported value")


def canonical_contract_hash(value: Mapping[str, object]) -> str:
    document = copy.deepcopy(dict(value))
    document.pop("content_hash", None)
    _validate_json(document, "canonical standalone contract")
    payload = json.dumps(
        document,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="strict")
    return hashlib.sha256(payload).hexdigest()


def canonical_contract_bytes(value: Mapping[str, object]) -> bytes:
    document = copy.deepcopy(dict(value))
    _validate_json(document, "standalone contract")
    return (
        json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8", errors="strict")


def _object(value: object, context: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("standalone_game_contract_invalid", f"{context} must be an object")
    return value


def _exact(value: Mapping[str, object], fields: frozenset[str], context: str) -> None:
    if set(value) != fields:
        _fail("standalone_game_contract_invalid", f"{context} fields are not closed")


def _sha256(value: object, context: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail("standalone_game_contract_invalid", f"{context} must be a SHA-256")
    return value


def _identifier(value: object, context: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        _fail("standalone_game_contract_invalid", f"{context} is invalid")
    return value


def portable_relative_path(value: object, context: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 1024:
        _fail("standalone_game_path_invalid", f"{context} is invalid")
    if unicodedata.normalize("NFC", value) != value or "\\" in value:
        _fail("standalone_game_path_invalid", f"{context} must be NFC POSIX text")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        _fail("standalone_game_path_invalid", f"{context} is not canonical")
    for part in path.parts:
        folded = part.casefold()
        stem = folded.split(".", 1)[0]
        if part in {"", ".", ".."} or part[-1:] in {" ", "."} or ":" in part or stem in _RESERVED:
            _fail("standalone_game_path_invalid", f"{context} is not portable")
    return value


def _identity(
    value: object,
    context: str,
    *,
    format_name: str,
    fields: frozenset[str] = _IDENTITY_FIELDS,
) -> dict[str, Any]:
    item = _object(value, context)
    _exact(item, fields, context)
    if item.get("format") != format_name or item.get("format_version") != 1:
        _fail("standalone_game_contract_invalid", f"{context} format is invalid")
    _identifier(item.get("id"), f"{context}.id")
    _sha256(item.get("content_hash"), f"{context}.content_hash")
    if "tree_hash" in fields:
        _sha256(item.get("tree_hash"), f"{context}.tree_hash")
    return copy.deepcopy(item)


def _require_content_hash(document: Mapping[str, object], context: str) -> None:
    declared = _sha256(document.get("content_hash"), f"{context}.content_hash")
    if declared != canonical_contract_hash(document):
        _fail("standalone_game_hash_mismatch", f"{context} content hash differs")


def validate_standalone_game_document(value: object) -> dict[str, Any]:
    _validate_json(value, "standalone game")
    document = _object(value, "standalone game")
    _exact(document, _MANIFEST_FIELDS, "standalone game")
    if (
        document.get("format") != STANDALONE_GAME_FORMAT
        or document.get("format_version") != 1
        or document.get("state") != "materialized"
    ):
        _fail("standalone_game_contract_invalid", "standalone game identity/state is invalid")
    game_id = document.get("game_id")
    if type(game_id) is not str or _GAME_ID_RE.fullmatch(game_id) is None:
        _fail("standalone_game_contract_invalid", "standalone game game_id is invalid")
    lineage = _object(document.get("lineage"), "standalone game.lineage")
    _exact(lineage, _LINEAGE_FIELDS, "standalone game.lineage")
    for field in _LINEAGE_FIELDS:
        _sha256(lineage.get(field), f"standalone game.lineage.{field}")
    materialization = _identity(
        document.get("materialization_bundle"),
        "standalone game.materialization_bundle",
        format_name="world-forge.game_materialization_bundle",
    )
    implementation = _identity(
        document.get("runtime_implementation"),
        "standalone game.runtime_implementation",
        format_name="world-forge.runtime_implementation",
    )
    platform = _identity(
        document.get("platform_set"),
        "standalone game.platform_set",
        format_name=STANDALONE_PLATFORM_FORMAT,
    )
    payload = _identity(
        document.get("payload_lock"),
        "standalone game.payload_lock",
        format_name=STANDALONE_GAME_LOCK_FORMAT,
        fields=_PAYLOAD_LOCK_IDENTITY_FIELDS,
    )
    entry_points = _object(document.get("entry_points"), "standalone game.entry_points")
    _exact(entry_points, _ENTRY_POINT_FIELDS, "standalone game.entry_points")
    if entry_points != _ALLOWED_ENTRY_POINTS:
        _fail("standalone_game_contract_invalid", "standalone entry points are not fixed")
    _require_content_hash(document, "standalone game")
    return {
        **copy.deepcopy(document),
        "materialization_bundle": materialization,
        "runtime_implementation": implementation,
        "platform_set": platform,
        "payload_lock": payload,
    }


def _file_records(value: object) -> list[dict[str, Any]]:
    if type(value) is not list or not value or len(value) > MAX_STANDALONE_FILES:
        _fail("standalone_game_contract_invalid", "standalone lock files are invalid")
    result: list[dict[str, Any]] = []
    folded: set[str] = set()
    for index, raw in enumerate(value):
        item = _object(raw, f"standalone lock.files/{index}")
        _exact(item, _FILE_FIELDS, f"standalone lock.files/{index}")
        path = portable_relative_path(item.get("path"), f"standalone lock.files/{index}.path")
        if path in {GAME_MANIFEST_PATH, GAME_LOCK_PATH}:
            _fail(
                "standalone_game_contract_invalid",
                "manifest and lock cannot inventory themselves",
            )
        key = path.casefold()
        if key in folded:
            _fail("standalone_game_path_collision", "standalone paths collide by NFC/casefold")
        folded.add(key)
        _sha256(item.get("sha256"), f"standalone lock.files/{index}.sha256")
        size = item.get("size_bytes")
        if type(size) is not int or not 0 <= size <= MAX_STANDALONE_FILE_BYTES:
            _fail("standalone_game_contract_invalid", "standalone file size is invalid")
        result.append(copy.deepcopy(item))
    expected = sorted(result, key=lambda item: item["path"].encode("utf-8"))
    if result != expected:
        _fail("standalone_game_contract_invalid", "standalone files are not ordered")
    return result


def validate_standalone_game_lock_document(value: object) -> dict[str, Any]:
    _validate_json(value, "standalone game lock")
    document = _object(value, "standalone game lock")
    _exact(document, _LOCK_FIELDS, "standalone game lock")
    if document.get("format") != STANDALONE_GAME_LOCK_FORMAT or document.get("format_version") != 1:
        _fail("standalone_game_contract_invalid", "standalone lock format is invalid")
    lock_id = _identifier(document.get("lock_id"), "standalone lock.lock_id")
    files = _file_records(document.get("files"))
    tree_hash = _sha256(document.get("tree_hash"), "standalone lock.tree_hash")
    expected_tree = canonical_contract_hash({"files": files})
    if tree_hash != expected_tree:
        _fail("standalone_game_hash_mismatch", "standalone lock tree hash differs")
    expected_id = "standalone_game_lock_" + expected_tree[:40]
    if lock_id != expected_id:
        _fail("standalone_game_hash_mismatch", "standalone lock ID differs")
    _require_content_hash(document, "standalone game lock")
    return copy.deepcopy(document)


def validate_standalone_platform_document(value: object) -> dict[str, Any]:
    _validate_json(value, "standalone platform")
    document = _object(value, "standalone platform")
    _exact(document, _PLATFORM_FIELDS, "standalone platform")
    if (
        document.get("format") != STANDALONE_PLATFORM_FORMAT
        or document.get("format_version") != 1
        or document.get("requires_python") != ">=3.11,<3.13"
    ):
        _fail("standalone_game_contract_invalid", "standalone platform header is invalid")
    dependency = _object(document.get("dependency"), "standalone platform.dependency")
    _exact(dependency, _DEPENDENCY_FIELDS, "standalone platform.dependency")
    if dependency != {
        "distribution": "raylib",
        "version": "6.0.1.0",
        "pin": "raylib==6.0.1.0",
        "import_module": "pyray",
        "native_api": "raylib-5.5",
    }:
        _fail("standalone_game_contract_invalid", "standalone raylib dependency differs")
    adapter = _object(document.get("adapter"), "standalone platform.adapter")
    _exact(adapter, _ADAPTER_FIELDS, "standalone platform.adapter")
    _identifier(adapter.get("adapter_id"), "standalone platform.adapter.adapter_id")
    version = adapter.get("adapter_version")
    if (
        type(version) is not str
        or re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
            version,
        )
        is None
    ):
        _fail("standalone_game_contract_invalid", "adapter version is invalid")
    _sha256(adapter.get("content_hash"), "standalone platform.adapter.content_hash")
    implementation = _object(
        document.get("runtime_implementation"),
        "standalone platform.runtime_implementation",
    )
    _exact(
        implementation,
        _IMPLEMENTATION_FIELDS,
        "standalone platform.runtime_implementation",
    )
    _identifier(
        implementation.get("implementation_id"),
        "standalone platform.runtime_implementation.implementation_id",
    )
    _sha256(
        implementation.get("content_hash"),
        "standalone platform.runtime_implementation.content_hash",
    )
    snapshot = _object(document.get("runtime_snapshot"), "standalone platform.runtime_snapshot")
    _exact(snapshot, _SNAPSHOT_FIELDS, "standalone platform.runtime_snapshot")
    _identifier(snapshot.get("snapshot_id"), "standalone platform.runtime_snapshot.snapshot_id")
    _sha256(snapshot.get("content_hash"), "standalone platform.runtime_snapshot.content_hash")
    _sha256(snapshot.get("tree_hash"), "standalone platform.runtime_snapshot.tree_hash")
    raw_locks = document.get("platform_locks")
    if type(raw_locks) is not list or len(raw_locks) != 4:
        _fail("standalone_game_contract_invalid", "exactly four platform locks are required")
    locks: list[dict[str, Any]] = []
    combinations: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(raw_locks):
        item = _object(raw, f"standalone platform.platform_locks/{index}")
        _exact(item, _PLATFORM_LOCK_FIELDS, f"standalone platform.platform_locks/{index}")
        _identifier(item.get("lock_id"), f"standalone platform.platform_locks/{index}.lock_id")
        _sha256(
            item.get("content_hash"),
            f"standalone platform.platform_locks/{index}.content_hash",
        )
        combination = (item.get("os"), item.get("python_minor"), item.get("abi"))
        if (
            combination
            not in {
                ("linux", "3.11", "cp311"),
                ("linux", "3.12", "cp312"),
                ("windows", "3.11", "cp311"),
                ("windows", "3.12", "cp312"),
            }
            or combination in combinations
        ):
            _fail("standalone_game_contract_invalid", "platform lock matrix is invalid")
        combinations.add(combination)
        locks.append(copy.deepcopy(item))
    if locks != sorted(locks, key=lambda item: item["lock_id"].encode("utf-8")):
        _fail("standalone_game_contract_invalid", "platform locks are not ordered")
    seed = {
        key: document[key]
        for key in (
            "requires_python",
            "dependency",
            "adapter",
            "runtime_implementation",
            "runtime_snapshot",
            "platform_locks",
        )
    }
    expected_id = "standalone_platform_" + canonical_contract_hash(seed)[:40]
    if document.get("platform_set_id") != expected_id:
        _fail("standalone_game_hash_mismatch", "standalone platform ID differs")
    _require_content_hash(document, "standalone platform")
    return copy.deepcopy(document)


def _state(info: os.stat_result | object) -> tuple[int, int, int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_nlink),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _safe_root(root: Path) -> tuple[int, int]:
    try:
        info = path_file_stat(root)
    except OSError as exc:
        _fail("standalone_game_root_invalid", str(exc))
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _fail("standalone_game_root_invalid", "standalone root must be a real directory")
    return file_identity(info)


def _read_regular(path: Path, relative: str) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        before = path_file_stat(path)
        if (
            is_link_or_reparse(before)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_STANDALONE_FILE_BYTES
        ):
            _fail("standalone_game_file_unsafe", f"{relative} is not one regular file")
        descriptor = os.open(path, flags)
        opened = descriptor_file_stat(descriptor)
        if (
            is_link_or_reparse(opened)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or file_identity(opened) != file_identity(before)
        ):
            _fail("standalone_game_file_unsafe", f"{relative} changed before read")
        chunks: list[bytes] = []
        remaining = int(opened.st_size)
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                _fail("standalone_game_file_unsafe", f"{relative} ended early")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail("standalone_game_file_unsafe", f"{relative} grew during read")
        after = descriptor_file_stat(descriptor)
        named = path_file_stat(path)
        if _state(after) != _state(opened) or _state(named) != _state(before):
            _fail("standalone_game_file_unsafe", f"{relative} changed during read")
        return b"".join(chunks)
    except StandaloneDistributionError:
        raise
    except OSError as exc:
        _fail("standalone_game_file_unsafe", f"{relative}: {exc}")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _directory_closure(paths: Iterable[str]) -> frozenset[str]:
    directories: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        directories.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
    return frozenset(directories)


def capture_standalone_tree_with_directories(
    root: str | Path,
) -> tuple[Mapping[str, bytes], frozenset[str]]:
    root_path = Path(os.path.abspath(os.fspath(root)))
    root_identity = _safe_root(root_path)
    files: dict[str, bytes] = {}
    directories: set[str] = set()
    path_keys: set[str] = set()
    total = 0
    stack: list[tuple[Path, str]] = [(root_path, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            before = path_file_stat(directory)
            names = sorted(os.listdir(directory), key=lambda item: item.encode("utf-8"))
        except (OSError, UnicodeError) as exc:
            _fail("standalone_game_tree_unsafe", str(exc))
        if is_link_or_reparse(before) or not stat.S_ISDIR(before.st_mode):
            _fail("standalone_game_tree_unsafe", f"{prefix or '.'} is not a real directory")
        children: list[tuple[Path, str]] = []
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            portable_relative_path(relative, "standalone tree path")
            folded = relative.casefold()
            if folded in path_keys:
                _fail("standalone_game_path_collision", "standalone paths collide")
            path_keys.add(folded)
            path = directory / name
            try:
                info = path_file_stat(path)
            except OSError as exc:
                _fail("standalone_game_tree_unsafe", f"{relative}: {exc}")
            if is_link_or_reparse(info):
                _fail("standalone_game_tree_unsafe", f"{relative} is a link/reparse point")
            if stat.S_ISDIR(info.st_mode):
                if len(directories) >= MAX_STANDALONE_DIRECTORIES:
                    _fail(
                        "standalone_game_tree_limit",
                        "standalone tree exceeds its directory limit",
                    )
                directories.add(relative)
                children.append((path, relative))
            elif stat.S_ISREG(info.st_mode):
                payload = _read_regular(path, relative)
                total += len(payload)
                if len(files) >= MAX_STANDALONE_FILES or total > MAX_STANDALONE_TOTAL_BYTES:
                    _fail("standalone_game_tree_limit", "standalone tree exceeds its limits")
                files[relative] = payload
            else:
                _fail("standalone_game_tree_unsafe", f"{relative} has an unsupported type")
        try:
            after = path_file_stat(directory)
        except OSError as exc:
            _fail("standalone_game_tree_unsafe", str(exc))
        if _state(before) != _state(after):
            _fail("standalone_game_tree_unsafe", f"{prefix or '.'} changed during capture")
        stack.extend(reversed(children))
    if _safe_root(root_path) != root_identity:
        _fail("standalone_game_tree_unsafe", "standalone root changed during capture")
    return MappingProxyType(files), frozenset(directories)


def capture_standalone_tree(root: str | Path) -> Mapping[str, bytes]:
    files, directories = capture_standalone_tree_with_directories(root)
    if directories != _directory_closure(files):
        _fail(
            "standalone_game_directory_closure_invalid",
            "standalone tree contains an unexpected empty directory",
        )
    return files


def _file_inventory(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    return [
        {
            "path": path,
            "sha256": hashlib.sha256(files[path]).hexdigest(),
            "size_bytes": len(files[path]),
        }
        for path in sorted(files, key=lambda item: item.encode("utf-8"))
    ]


def _requirements_contain_forge_distribution(payload: bytes) -> bool:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeError:
        return False
    for raw_line in text.splitlines():
        requirement = raw_line.split("#", 1)[0].strip()
        if (
            requirement
            and not requirement.startswith("-")
            and requirement_distribution_name(requirement) in _FORBIDDEN_FORGE_DISTRIBUTIONS
        ):
            return True
    return False


def _pyproject_requirement_strings(document: object) -> Iterable[str]:
    if type(document) is not dict:
        return ()
    requirements: list[str] = []
    project = document.get("project")
    if type(project) is dict:
        dependencies = project.get("dependencies")
        if type(dependencies) is list:
            requirements.extend(item for item in dependencies if type(item) is str)
        optional = project.get("optional-dependencies")
        if type(optional) is dict:
            for values in optional.values():
                if type(values) is list:
                    requirements.extend(item for item in values if type(item) is str)
    build_system = document.get("build-system")
    if type(build_system) is dict:
        requires = build_system.get("requires")
        if type(requires) is list:
            requirements.extend(item for item in requires if type(item) is str)
    groups = document.get("dependency-groups")
    if type(groups) is dict:
        for values in groups.values():
            if type(values) is list:
                requirements.extend(item for item in values if type(item) is str)
    return tuple(requirements)


def _pyproject_contains_forge_distribution(payload: bytes) -> bool:
    try:
        document = tomllib.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeError, tomllib.TOMLDecodeError):
        return False
    return any(
        requirement_distribution_name(requirement) in _FORBIDDEN_FORGE_DISTRIBUTIONS
        for requirement in _pyproject_requirement_strings(document)
    )


def _python_boundary_findings(files: Mapping[str, bytes]) -> list[str]:
    findings: list[str] = []
    for path in sorted(files, key=lambda item: item.encode("utf-8")):
        parts = PurePosixPath(path).parts
        if any(part.casefold() in _FORBIDDEN_PATH_COMPONENTS for part in parts):
            findings.append(f"{path}: forbidden authoring/runtime path")
            continue
        payload = files[path]
        if (path == "requirements.txt" and _requirements_contain_forge_distribution(payload)) or (
            path == "pyproject.toml" and _pyproject_contains_forge_distribution(payload)
        ):
            findings.append(f"{path}: Forge distribution dependency")
        if not path.endswith(".py"):
            continue
        try:
            source = payload.decode("utf-8", errors="strict")
            tree = ast.parse(source, filename=path)
        except (UnicodeError, SyntaxError) as exc:
            findings.append(f"{path}: invalid Python source: {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
                for name in names:
                    if name in _FORBIDDEN_IMPORT_ROOTS and not (
                        name == "subprocess" and path.startswith("tests/")
                    ):
                        findings.append(f"{path}:{node.lineno}: forbidden import {name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if root in _FORBIDDEN_IMPORT_ROOTS and not (
                    root == "subprocess" and path.startswith("tests/")
                ):
                    findings.append(f"{path}:{node.lineno}: forbidden import {root}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALL_NAMES:
                    findings.append(f"{path}:{node.lineno}: dynamic code call {node.func.id}")
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr in _FORBIDDEN_OS_CALLS
                ):
                    findings.append(f"{path}:{node.lineno}: process capability os.{node.func.attr}")
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "importlib"
                    and node.func.attr == "import_module"
                ):
                    allowed = (
                        path
                        == (
                            "game_data/runtime-bundle/runtime/snapshot-tree/"
                            "gamepack_raylib_2d/backend.py"
                        )
                        and len(node.args) == 1
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "pyray"
                    )
                    if not allowed:
                        findings.append(f"{path}:{node.lineno}: dynamic import capability")
    return findings


def _canonical_json_file(
    files: Mapping[str, bytes],
    path: str,
    *,
    format_name: str,
) -> dict[str, Any]:
    try:
        payload = files[path]
    except KeyError:
        _fail("standalone_game_lineage_mismatch", f"missing lineage contract {path}")
    document = decode_json_object(payload, path)
    if canonical_contract_bytes(document) != payload:
        _fail("standalone_game_noncanonical_json", f"{path} is not canonical")
    if document.get("format") != format_name or document.get("format_version") != 1:
        _fail("standalone_game_lineage_mismatch", f"{path} format differs")
    _require_content_hash(document, path)
    return document


def _verify_runtime_lineage(
    files: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    platform: Mapping[str, Any],
) -> None:
    runtime = _canonical_json_file(
        files,
        RUNTIME_BUNDLE_MANIFEST_PATH,
        format_name="world-forge.game_runtime_bundle",
    )
    implementation = _canonical_json_file(
        files,
        RUNTIME_IMPLEMENTATION_PATH,
        format_name="world-forge.runtime_implementation",
    )
    try:
        contracts = _object(runtime["contracts"], "nested runtime contracts")
        gamepack = _object(contracts["gamepack"], "nested gamepack identity")
        snapshot = _object(contracts["runtime_snapshot"], "nested snapshot identity")
        adapter = _object(contracts["runtime_adapter"], "nested adapter identity")
        composition = _object(
            contracts["runtime_composition"],
            "nested composition identity",
        )
        assetpack = _object(runtime["assetpack"], "nested assetpack")
        assetpack_manifest = _object(
            assetpack["manifest"],
            "nested assetpack identity",
        )
        runtime_tree = _object(
            runtime["runtime_snapshot_tree"],
            "nested runtime snapshot tree",
        )
    except KeyError as exc:
        _fail(
            "standalone_game_lineage_mismatch",
            f"nested runtime lineage lacks {exc.args[0]}",
        )
    lineage = manifest["lineage"]
    if (
        runtime.get("content_hash") != lineage["runtime_bundle_hash"]
        or gamepack.get("content_hash") != lineage["gamepack_hash"]
        or assetpack_manifest.get("content_hash") != lineage["assetpack_hash"]
        or snapshot.get("content_hash") != lineage["runtime_snapshot_hash"]
        or composition.get("content_hash") != lineage["runtime_composition_hash"]
        or gamepack.get("id") != manifest["game_id"]
    ):
        _fail(
            "standalone_game_lineage_mismatch",
            "standalone lineage differs from the nested runtime contracts",
        )
    if platform["adapter"] != {
        "adapter_id": adapter.get("id"),
        "adapter_version": adapter.get("adapter_version"),
        "content_hash": adapter.get("content_hash"),
    }:
        _fail(
            "standalone_game_lineage_mismatch",
            "standalone platform adapter differs from the nested runtime",
        )
    if platform["runtime_snapshot"] != {
        "snapshot_id": snapshot.get("id"),
        "content_hash": snapshot.get("content_hash"),
        "tree_hash": runtime_tree.get("tree_hash"),
    }:
        _fail(
            "standalone_game_lineage_mismatch",
            "standalone platform snapshot differs from the nested runtime",
        )
    if manifest["runtime_implementation"] != {
        "format": "world-forge.runtime_implementation",
        "format_version": 1,
        "id": implementation.get("implementation_id"),
        "content_hash": implementation.get("content_hash"),
    }:
        _fail(
            "standalone_game_lineage_mismatch",
            "standalone implementation identity differs from its exact contract",
        )
    if (
        implementation.get("adapter") != platform["adapter"]
        or implementation.get("snapshot") != platform["runtime_snapshot"]
        or implementation.get("platform_locks") != platform["platform_locks"]
    ):
        _fail(
            "standalone_game_lineage_mismatch",
            "runtime implementation differs from the standalone platform set",
        )

    nested_prefix = RUNTIME_BUNDLE_ROOT + "/"
    nested_files = {
        path.removeprefix(nested_prefix): payload
        for path, payload in files.items()
        if path.startswith(nested_prefix) and path != RUNTIME_BUNDLE_MANIFEST_PATH
    }
    if _file_inventory(nested_files) != runtime.get("files"):
        _fail(
            "standalone_game_lineage_mismatch",
            "nested runtime bytes differ from its internal inventory",
        )
    snapshot_prefix = nested_prefix + "runtime/snapshot-tree/"
    snapshot_files = {
        path.removeprefix(snapshot_prefix): payload
        for path, payload in files.items()
        if path.startswith(snapshot_prefix)
    }
    snapshot_records = _file_inventory(snapshot_files)
    if (
        runtime_tree.get("tree_hash") != canonical_contract_hash({"files": snapshot_records})
        or runtime_tree.get("file_count") != len(snapshot_records)
        or runtime_tree.get("total_bytes")
        != sum(record["size_bytes"] for record in snapshot_records)
    ):
        _fail(
            "standalone_game_lineage_mismatch",
            "nested runtime snapshot tree differs from its captured bytes",
        )

    checked_locks: list[dict[str, object]] = []
    for reference in platform["platform_locks"]:
        lock_id = reference["lock_id"]
        lock = _canonical_json_file(
            files,
            f"{RUNTIME_PLATFORM_LOCK_ROOT}/{lock_id}.json",
            format_name="world-forge.runtime_platform_lock",
        )
        try:
            lock_reference = {
                "lock_id": lock["lock_id"],
                "content_hash": lock["content_hash"],
                "os": lock["platform"]["os"],
                "python_minor": lock["python"]["minor"],
                "abi": lock["python"]["abi"],
            }
        except (KeyError, TypeError):
            _fail(
                "standalone_game_lineage_mismatch",
                f"platform lock {lock_id} has incomplete identity fields",
            )
        if lock_reference != reference:
            _fail(
                "standalone_game_lineage_mismatch",
                f"platform lock {lock_id} differs from its exact reference",
            )
        checked_locks.append(lock_reference)
    if checked_locks != platform["platform_locks"]:
        _fail(
            "standalone_game_lineage_mismatch",
            "standalone platform lock order differs",
        )


def verify_captured_standalone_distribution(
    files: Mapping[str, bytes],
    *,
    root: str | Path = ".",
) -> dict[str, object]:
    root_path = Path(os.path.abspath(os.fspath(root)))
    try:
        manifest_payload = files[GAME_MANIFEST_PATH]
        lock_payload = files[GAME_LOCK_PATH]
        platform_payload = files[PLATFORM_LOCK_PATH]
    except KeyError as exc:
        _fail("standalone_game_manifest_missing", f"missing {exc.args[0]}")
    manifest = validate_standalone_game_document(
        decode_json_object(manifest_payload, GAME_MANIFEST_PATH)
    )
    lock = validate_standalone_game_lock_document(decode_json_object(lock_payload, GAME_LOCK_PATH))
    platform = validate_standalone_platform_document(
        decode_json_object(platform_payload, PLATFORM_LOCK_PATH)
    )
    if canonical_contract_bytes(manifest) != manifest_payload:
        _fail("standalone_game_noncanonical_json", f"{GAME_MANIFEST_PATH} is not canonical")
    if canonical_contract_bytes(lock) != lock_payload:
        _fail("standalone_game_noncanonical_json", f"{GAME_LOCK_PATH} is not canonical")
    if canonical_contract_bytes(platform) != platform_payload:
        _fail("standalone_game_noncanonical_json", f"{PLATFORM_LOCK_PATH} is not canonical")
    payload_files = {
        path: payload
        for path, payload in files.items()
        if path not in {GAME_MANIFEST_PATH, GAME_LOCK_PATH}
    }
    if _file_inventory(payload_files) != lock["files"]:
        _fail(
            "standalone_game_file_closure_invalid",
            "standalone bytes differ from the exact payload lock",
        )
    if manifest["payload_lock"] != {
        "format": STANDALONE_GAME_LOCK_FORMAT,
        "format_version": 1,
        "id": lock["lock_id"],
        "content_hash": lock["content_hash"],
        "tree_hash": lock["tree_hash"],
    }:
        _fail("standalone_game_lineage_mismatch", "payload lock identity differs")
    if manifest["platform_set"] != {
        "format": STANDALONE_PLATFORM_FORMAT,
        "format_version": 1,
        "id": platform["platform_set_id"],
        "content_hash": platform["content_hash"],
    }:
        _fail("standalone_game_lineage_mismatch", "platform identity differs")
    if manifest["runtime_implementation"] != {
        "format": "world-forge.runtime_implementation",
        "format_version": 1,
        "id": platform["runtime_implementation"]["implementation_id"],
        "content_hash": platform["runtime_implementation"]["content_hash"],
    }:
        _fail("standalone_game_lineage_mismatch", "implementation identity differs")
    _verify_runtime_lineage(files, manifest, platform)
    findings = _python_boundary_findings(files)
    if findings:
        _fail("standalone_game_boundary_violation", "; ".join(findings[:16]))
    return {
        "status": "verified",
        "root": str(root_path),
        "game_id": manifest["game_id"],
        "manifest_hash": manifest["content_hash"],
        "payload_lock_hash": lock["content_hash"],
        "payload_tree_hash": lock["tree_hash"],
        "runtime_bundle_hash": manifest["lineage"]["runtime_bundle_hash"],
        "authoring_dependencies": 0,
        "runtime_ai_capabilities": 0,
        "files": len(files),
    }


def verify_standalone_distribution(root: str | Path) -> dict[str, object]:
    root_path = Path(os.path.abspath(os.fspath(root)))
    return verify_captured_standalone_distribution(
        capture_standalone_tree(root_path),
        root=root_path,
    )


__all__ = [
    "GAME_LOCK_PATH",
    "GAME_MANIFEST_PATH",
    "PLATFORM_LOCK_PATH",
    "RUNTIME_BUNDLE_ROOT",
    "STANDALONE_CONTRACT_VERSION",
    "STANDALONE_GAME_FORMAT",
    "STANDALONE_GAME_LOCK_FORMAT",
    "STANDALONE_PLATFORM_FORMAT",
    "StandaloneDistributionError",
    "canonical_contract_bytes",
    "canonical_contract_hash",
    "capture_standalone_tree",
    "capture_standalone_tree_with_directories",
    "decode_json_object",
    "portable_relative_path",
    "validate_standalone_game_document",
    "validate_standalone_game_lock_document",
    "validate_standalone_platform_document",
    "verify_captured_standalone_distribution",
    "verify_standalone_distribution",
]
