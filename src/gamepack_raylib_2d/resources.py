"""Retained immutable runtime-bundle loading and bounded resource ownership."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import struct
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

import gamepack_runtime
from gamepack_raylib_2d.backend import RaylibBackend
from gamepack_raylib_2d.descriptor_policy import ADAPTER_DESCRIPTOR_HASHES
from gamepack_raylib_2d.types import FontHandle, TextureHandle
from gamepack_runtime import GameLogicError, canonical_state_hash, load_gamepack_bytes
from gamepack_runtime.file_stat import (
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
)
from gamepack_runtime.persistence_io import PersistenceIOError, decode_json_object

MAX_BUNDLE_FILES = 256
MAX_BUNDLE_DIRECTORIES = 256
MAX_BUNDLE_FILE_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
_HEX = frozenset("0123456789abcdef")
_WINDOWS_RESERVED = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


class RaylibResourceError(ValueError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise RaylibResourceError(reason_code, detail)


def _sha256(value: object, context: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        _fail("bundle_contract_invalid", f"{context} must be lowercase SHA-256")
    return value


def _portable_path(value: object, context: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 4096:
        _fail("bundle_path_invalid", f"{context} must be a bounded relative path")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise RaylibResourceError("bundle_path_invalid", f"{context} is not UTF-8") from exc
    if unicodedata.normalize("NFC", value) != value or "\\" in value:
        _fail("bundle_path_invalid", f"{context} must be NFC portable POSIX form")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        _fail("bundle_path_invalid", f"{context} is not canonical")
    for part in path.parts:
        stem = part.split(".", 1)[0].casefold()
        if (
            part in {"", ".", ".."}
            or part.endswith((" ", "."))
            or stem in _WINDOWS_RESERVED
            or any(ord(character) < 32 or character in '<>:"|?*' for character in part)
            or len(part.encode("utf-8")) > 255
        ):
            _fail("bundle_path_invalid", f"{context} is not portable")
    return value


def _canonical_hash(value: Mapping[str, object]) -> str:
    document = copy.deepcopy(dict(value))
    document.pop("content_hash", None)
    try:
        payload = json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    except (RecursionError, TypeError, UnicodeError, ValueError, OverflowError) as exc:
        raise RaylibResourceError("bundle_contract_invalid", str(exc)) from exc
    return hashlib.sha256(payload).hexdigest()


def _preflight_json(value: object, context: str) -> None:
    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            _fail("bundle_contract_invalid", f"{context} exceeds JSON limits")
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str:
                    _fail("bundle_contract_invalid", f"{context} contains a non-string key")
                stack.append((item, depth + 1))
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
        elif type(current) not in {str, int, bool, type(None)}:
            _fail("bundle_contract_invalid", f"{context} contains an unsupported JSON value")


def _decode(payload: bytes, path: str) -> dict[str, Any]:
    try:
        value = decode_json_object(
            payload,
            source=path,
            limit=MAX_BUNDLE_FILE_BYTES,
        )
    except (PersistenceIOError, GameLogicError) as exc:
        _fail("bundle_json_invalid", str(exc))
    _preflight_json(value, path)
    return value


def _state(info: os.stat_result | object) -> tuple[int, int, int, int, int, int, int]:
    return (
        int(info.st_mode),  # type: ignore[attr-defined]
        int(info.st_dev),  # type: ignore[attr-defined]
        int(info.st_ino),  # type: ignore[attr-defined]
        int(info.st_nlink),  # type: ignore[attr-defined]
        int(info.st_size),  # type: ignore[attr-defined]
        int(info.st_mtime_ns),  # type: ignore[attr-defined]
        int(info.st_ctime_ns),  # type: ignore[attr-defined]
    )


def _safe_directory(path: Path, context: str) -> tuple[int, int]:
    try:
        info = path_file_stat(path)
    except OSError as exc:
        raise RaylibResourceError("bundle_tree_unsafe", f"{context}: {exc}") from exc
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _fail("bundle_tree_unsafe", f"{context} is not a retained regular directory")
    return file_identity(info)


def _read_regular(path: Path, relative: str) -> bytes:
    try:
        before = path_file_stat(path)
        if (
            is_link_or_reparse(before)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_BUNDLE_FILE_BYTES
        ):
            _fail("bundle_tree_unsafe", f"{relative} is linked, special, or oversized")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            retained_before = descriptor_file_stat(descriptor)
            if (
                is_link_or_reparse(retained_before)
                or not stat.S_ISREG(retained_before.st_mode)
                or retained_before.st_nlink != 1
                or _state(retained_before) != _state(before)
            ):
                _fail("bundle_tree_changed", f"{relative} changed before retained read")
            chunks: list[bytes] = []
            remaining = retained_before.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    _fail("bundle_tree_changed", f"{relative} was truncated during read")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                _fail("bundle_tree_changed", f"{relative} grew during read")
            retained_after = descriptor_file_stat(descriptor)
        finally:
            os.close(descriptor)
        after = path_file_stat(path)
    except RaylibResourceError:
        raise
    except OSError as exc:
        raise RaylibResourceError("bundle_tree_unsafe", f"{relative}: {exc}") from exc
    if (
        _state(retained_before) != _state(retained_after)
        or _state(retained_after) != _state(after)
        or file_identity(after) != file_identity(before)
    ):
        _fail("bundle_tree_changed", f"{relative} changed during retained read")
    return b"".join(chunks)


def _capture_bundle(root: Path) -> Mapping[str, bytes]:
    root = Path(os.path.abspath(os.fspath(root)))
    root_identity = _safe_directory(root, "runtime bundle root")
    discovered_files: list[str] = []
    discovered_directories: list[str] = []
    try:
        for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            relative_parent = current_path.relative_to(root)
            _safe_directory(current_path, f"runtime bundle directory {relative_parent}")
            directory_names.sort(key=lambda item: item.encode("utf-8"))
            file_names.sort(key=lambda item: item.encode("utf-8"))
            for name in directory_names:
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                _portable_path(relative, "runtime bundle directory")
                _safe_directory(path, f"runtime bundle directory {relative}")
                discovered_directories.append(relative)
            for name in file_names:
                relative = (current_path / name).relative_to(root).as_posix()
                _portable_path(relative, "runtime bundle file")
                discovered_files.append(relative)
    except RaylibResourceError:
        raise
    except (OSError, ValueError) as exc:
        raise RaylibResourceError("bundle_tree_unsafe", str(exc)) from exc
    if (
        len(discovered_files) > MAX_BUNDLE_FILES + 1
        or len(discovered_directories) > MAX_BUNDLE_DIRECTORIES
    ):
        _fail("bundle_tree_limit", "runtime bundle exceeds its node limits")
    combined = discovered_files + discovered_directories
    if len({item.casefold() for item in combined}) != len(combined):
        _fail("bundle_path_collision", "runtime bundle paths collide under casefold")
    captured: dict[str, bytes] = {}
    total = 0
    for relative in sorted(discovered_files, key=lambda item: item.encode("utf-8")):
        payload = _read_regular(root / PurePosixPath(relative), relative)
        total += len(payload)
        if total > MAX_BUNDLE_BYTES:
            _fail("bundle_tree_limit", "runtime bundle exceeds its byte limit")
        captured[relative] = payload
    if _safe_directory(root, "runtime bundle root") != root_identity:
        _fail("bundle_tree_changed", "runtime bundle root identity changed")
    expected_directories: set[str] = set()
    for relative in captured:
        parent = PurePosixPath(relative).parent
        while parent.as_posix() != ".":
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    if set(discovered_directories) != expected_directories:
        _fail("bundle_tree_unsafe", "runtime bundle contains an empty or extra directory")
    return MappingProxyType(captured)


def _png_dimensions(payload: bytes) -> tuple[int, int]:
    if (
        len(payload) < 33
        or payload[:8] != b"\x89PNG\r\n\x1a\n"
        or payload[8:16] != b"\x00\x00\x00\rIHDR"
    ):
        _fail("resource_media_invalid", "sealed texture is not a canonical PNG")
    width, height = struct.unpack(">II", payload[16:24])
    if not 1 <= width <= 8192 or not 1 <= height <= 8192:
        _fail("resource_media_invalid", "sealed PNG dimensions are out of bounds")
    if payload[24:29] != bytes((8, 6, 0, 0, 0)):
        _fail("resource_media_invalid", "sealed PNG must be 8-bit non-interlaced RGBA")
    return width, height


def _validate_ttf(payload: bytes) -> None:
    if len(payload) < 12 or payload[:4] not in {b"\x00\x01\x00\x00", b"true"}:
        _fail("resource_media_invalid", "sealed font is not a TrueType SFNT")
    table_count = int.from_bytes(payload[4:6], "big")
    if not 1 <= table_count <= 128 or 12 + 16 * table_count > len(payload):
        _fail("resource_media_invalid", "sealed font table directory is invalid")
    spans: list[tuple[int, int]] = []
    for index in range(table_count):
        offset = 12 + 16 * index
        table_offset = int.from_bytes(payload[offset + 8 : offset + 12], "big")
        table_length = int.from_bytes(payload[offset + 12 : offset + 16], "big")
        if table_offset < 12 + 16 * table_count or table_offset + table_length > len(payload):
            _fail("resource_media_invalid", "sealed font table span is invalid")
        spans.append((table_offset, table_offset + table_length))
    ordered_spans = sorted(spans)
    if any(
        end > next_start
        for (_, end), (next_start, _) in zip(
            ordered_spans,
            ordered_spans[1:],
            strict=False,
        )
    ):
        _fail("resource_media_invalid", "sealed font tables overlap or are noncanonical")


@dataclass(frozen=True, slots=True)
class BoundResource:
    binding_id: str
    asset_id: str
    role: str
    media_type: str
    runtime_path: str
    bundle_path: str
    sha256: str
    size_bytes: int
    payload: bytes
    dimensions: tuple[int, int] | None


@dataclass(frozen=True, slots=True)
class LoadedRuntimeBundle:
    root: Path
    manifest: dict[str, Any]
    gamepack: dict[str, Any]
    composition: dict[str, Any]
    adapter: dict[str, Any]
    snapshot: dict[str, Any]
    registry: dict[str, Any]
    support_report: dict[str, Any]
    bindings: Mapping[str, BoundResource]
    files: Mapping[str, bytes]

    @property
    def initial_state_hash(self) -> str:
        return canonical_state_hash(self.gamepack["logic"]["initial_state"])

    def with_adapter(self, adapter: Mapping[str, object]) -> LoadedRuntimeBundle:
        return replace(self, adapter=copy.deepcopy(dict(adapter)))

    def with_snapshot(self, snapshot: Mapping[str, object]) -> LoadedRuntimeBundle:
        return replace(self, snapshot=copy.deepcopy(dict(snapshot)))


def _require_content_hash(document: dict[str, Any], context: str) -> None:
    declared = _sha256(document.get("content_hash"), f"{context}.content_hash")
    if declared != _canonical_hash(document):
        _fail("bundle_hash_mismatch", f"{context} content hash differs")


def _identity(
    document: Mapping[str, object],
    *,
    id_field: str,
) -> dict[str, object]:
    return {
        "format": document.get("format"),
        "format_version": document.get("format_version"),
        "id": document.get(id_field),
        "content_hash": document.get("content_hash"),
    }


def _manifest_identity(value: object, context: str) -> dict[str, object]:
    if type(value) is not dict:
        _fail("bundle_contract_invalid", f"{context} identity is absent")
    required = {"format", "format_version", "id", "content_hash", "path"}
    if set(value) != required:
        _fail("bundle_contract_invalid", f"{context} identity is invalid")
    return {field: value[field] for field in required - {"path"}}


def _require_derived_id(
    document: Mapping[str, object],
    *,
    id_field: str,
    prefix: str,
    seed_fields: tuple[str, ...],
    length: int = 40,
) -> None:
    expected = prefix + _canonical_hash({field: document[field] for field in seed_fields})[:length]
    if document.get(id_field) != expected:
        _fail("bundle_contract_invalid", f"{id_field} is not deterministic")


def _canonical_json_bytes(document: Mapping[str, object]) -> bytes:
    try:
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
    except (RecursionError, TypeError, UnicodeError, ValueError, OverflowError) as exc:
        raise RaylibResourceError("bundle_contract_invalid", str(exc)) from exc


def _verify_runtime_package(
    *,
    package_name: str,
    package_root: Path,
    snapshot_records: Mapping[str, Mapping[str, object]],
    bundle_files: Mapping[str, bytes],
) -> None:
    try:
        before = path_file_stat(package_root)
        if is_link_or_reparse(before) or not stat.S_ISDIR(before.st_mode):
            _fail("bundle_runtime_mismatch", f"{package_name} root is unsafe")
        names = sorted(os.listdir(package_root), key=lambda item: item.encode("utf-8"))
    except RaylibResourceError:
        raise
    except (OSError, UnicodeError) as exc:
        raise RaylibResourceError(
            "bundle_runtime_mismatch",
            f"could not inspect {package_name}: {exc}",
        ) from exc
    source_names: list[str] = []
    for name in names:
        path = package_root / name
        if name == "__pycache__":
            _safe_directory(path, f"{package_name} bytecode cache")
            continue
        _portable_path(name, f"{package_name} source")
        if not name.endswith(".py"):
            _fail("bundle_runtime_mismatch", f"{package_name} contains an extra file")
        source_names.append(name)
    expected_paths = sorted(
        (path for path in snapshot_records if path.startswith(f"{package_name}/")),
        key=lambda item: item.encode("utf-8"),
    )
    if expected_paths != [f"{package_name}/{name}" for name in source_names]:
        _fail("bundle_runtime_mismatch", f"{package_name} source closure differs")
    for snapshot_path in expected_paths:
        name = snapshot_path.removeprefix(f"{package_name}/")
        payload = _read_regular(package_root / name, snapshot_path)
        record = snapshot_records[snapshot_path]
        bundled = bundle_files.get(f"runtime/snapshot-tree/{snapshot_path}")
        if (
            bundled != payload
            or record.get("size_bytes") != len(payload)
            or record.get("sha256") != hashlib.sha256(payload).hexdigest()
        ):
            _fail("bundle_runtime_mismatch", f"{snapshot_path} differs from executing code")
    try:
        after = path_file_stat(package_root)
    except OSError as exc:
        raise RaylibResourceError(
            "bundle_runtime_mismatch",
            f"could not recheck {package_name}: {exc}",
        ) from exc
    if _state(before) != _state(after):
        _fail("bundle_runtime_mismatch", f"{package_name} changed during verification")


def _verify_runtime_lineage(
    *,
    manifest: dict[str, Any],
    gamepack: dict[str, Any],
    snapshot: dict[str, Any],
    registry: dict[str, Any],
    composition: dict[str, Any],
    support: dict[str, Any],
    adapter: dict[str, Any],
    files: Mapping[str, bytes],
) -> None:
    contracts = manifest["contracts"]
    game = gamepack.get("game")
    if type(game) is not dict:
        _fail("bundle_binding_mismatch", "gamepack identity is absent")
    expected_identities = {
        "gamepack": _identity(gamepack, id_field="missing") | {"id": game.get("id")},
        "runtime_snapshot": _identity(snapshot, id_field="snapshot_id"),
        "runtime_adapter_registry": _identity(registry, id_field="registry_id"),
        "runtime_composition": _identity(composition, id_field="composition_id"),
        "runtime_support_report": _identity(support, id_field="report_id"),
    }
    for field, expected in expected_identities.items():
        if _manifest_identity(contracts.get(field), field) != expected:
            _fail("bundle_binding_mismatch", f"{field} identity differs")
    adapter_identity = contracts.get("runtime_adapter")
    if (
        type(adapter_identity) is not dict
        or set(adapter_identity)
        != {
            "format",
            "format_version",
            "id",
            "adapter_version",
            "content_hash",
            "path",
        }
        or {
            "format": adapter_identity["format"],
            "format_version": adapter_identity["format_version"],
            "id": adapter_identity["id"],
            "content_hash": adapter_identity["content_hash"],
        }
        != _identity(adapter, id_field="adapter_id")
        or adapter_identity["adapter_version"] != adapter.get("adapter_version")
    ):
        _fail("bundle_binding_mismatch", "runtime adapter identity differs")

    _require_derived_id(
        snapshot,
        id_field="snapshot_id",
        prefix="runtime_snapshot_",
        seed_fields=("runtime_api", "adapter_descriptors", "files", "tree_hash"),
    )
    _require_derived_id(
        registry,
        id_field="registry_id",
        prefix="runtime_registry_",
        seed_fields=("runtime_snapshot", "adapters"),
    )
    _require_derived_id(
        composition,
        id_field="composition_id",
        prefix="runtime_composition_",
        seed_fields=(
            "gamepack",
            "asset_inventory",
            "assetpack",
            "adapter",
            "registry",
            "runtime_snapshot",
            "platforms",
            "bindings",
        ),
    )
    _require_derived_id(
        support,
        id_field="report_id",
        prefix="runtime_support_",
        seed_fields=(
            "gamepack",
            "composition",
            "adapter",
            "evidence",
            "dimensions",
            "compatibility_status",
            "mechanics",
            "features",
            "missing_capabilities",
            "reason_codes",
            "supported",
        ),
    )

    if composition.get("gamepack") != expected_identities["gamepack"]:
        _fail("bundle_binding_mismatch", "composition gamepack identity differs")
    if composition.get("adapter") != _identity(adapter, id_field="adapter_id"):
        _fail("bundle_binding_mismatch", "composition adapter identity differs")
    if composition.get("registry") != expected_identities["runtime_adapter_registry"]:
        _fail("bundle_binding_mismatch", "composition registry identity differs")
    if composition.get("runtime_snapshot") != expected_identities["runtime_snapshot"]:
        _fail("bundle_binding_mismatch", "composition snapshot identity differs")
    if composition.get("platforms") != adapter.get("platforms"):
        _fail("bundle_binding_mismatch", "composition platforms differ")
    if support.get("gamepack") != expected_identities["gamepack"]:
        _fail("bundle_binding_mismatch", "support gamepack identity differs")
    if support.get("composition") != expected_identities["runtime_composition"]:
        _fail("bundle_binding_mismatch", "support composition identity differs")
    if support.get("adapter") != _identity(adapter, id_field="adapter_id"):
        _fail("bundle_binding_mismatch", "support adapter identity differs")
    dimensions = support.get("dimensions")
    if (
        support.get("evidence") != []
        or support.get("supported") is not False
        or type(dimensions) is not dict
        or dimensions.get("release") != "blocked"
    ):
        _fail("bundle_binding_mismatch", "pre-execution support status overclaims")

    snapshot_identity = expected_identities["runtime_snapshot"]
    if registry.get("runtime_snapshot") != snapshot_identity:
        _fail("bundle_binding_mismatch", "registry snapshot identity differs")
    adapters = registry.get("adapters")
    if type(adapters) is not list:
        _fail("bundle_binding_mismatch", "registry adapters are absent")
    admitted: dict[str, dict[str, Any]] = {}
    for registered in adapters:
        if type(registered) is not dict:
            _fail("bundle_binding_mismatch", "registry adapter is invalid")
        _require_content_hash(registered, "registry adapter")
        adapter_id = registered.get("adapter_id")
        adapter_version = registered.get("adapter_version")
        key = f"{adapter_id}@{adapter_version}"
        if (
            type(adapter_id) is not str
            or type(adapter_version) is not str
            or key in admitted
            or ADAPTER_DESCRIPTOR_HASHES.get(key) != registered.get("content_hash")
        ):
            _fail("bundle_binding_mismatch", "registry adapter is not admitted")
        admitted[key] = registered
    if set(admitted) != set(ADAPTER_DESCRIPTOR_HASHES):
        _fail("bundle_binding_mismatch", "registry adapter closure differs")
    selected_key = f"{adapter['adapter_id']}@{adapter['adapter_version']}"
    if admitted.get(selected_key) != adapter:
        _fail("bundle_binding_mismatch", "selected adapter differs from registry")
    descriptor_identities = [
        _identity(registered, id_field="adapter_id")
        for registered in sorted(
            admitted.values(),
            key=lambda item: item["adapter_id"].encode("utf-8"),
        )
    ]
    if snapshot.get("adapter_descriptors") != descriptor_identities:
        _fail("bundle_binding_mismatch", "snapshot descriptor identities differ")

    raw_snapshot_records = snapshot.get("files")
    if type(raw_snapshot_records) is not list:
        _fail("bundle_binding_mismatch", "snapshot file inventory is absent")
    snapshot_records: dict[str, Mapping[str, object]] = {}
    for record in raw_snapshot_records:
        if type(record) is not dict or type(record.get("path")) is not str:
            _fail("bundle_binding_mismatch", "snapshot file record is invalid")
        path = record["path"]
        if path in snapshot_records:
            _fail("bundle_binding_mismatch", "snapshot file record is duplicated")
        snapshot_records[path] = record
    for key, registered in admitted.items():
        path = f"descriptors/{key}.json"
        payload = files.get(f"runtime/snapshot-tree/{path}")
        if payload != _canonical_json_bytes(registered):
            _fail("bundle_binding_mismatch", f"descriptor bytes differ: {path}")
    expected_snapshot_paths = {f"runtime/snapshot-tree/{path}" for path in snapshot_records}
    physical_snapshot_paths = {path for path in files if path.startswith("runtime/snapshot-tree/")}
    if physical_snapshot_paths != expected_snapshot_paths:
        _fail("bundle_binding_mismatch", "snapshot physical closure differs")
    runtime_tree = manifest.get("runtime_snapshot_tree")
    if (
        type(runtime_tree) is not dict
        or runtime_tree.get("runtime_api") != snapshot.get("runtime_api")
        or runtime_tree.get("file_count") != len(raw_snapshot_records)
        or runtime_tree.get("total_bytes")
        != sum(int(record["size_bytes"]) for record in raw_snapshot_records)
        or runtime_tree.get("tree_hash") != snapshot.get("tree_hash")
    ):
        _fail("bundle_binding_mismatch", "runtime snapshot tree identity differs")
    kernel_file = gamepack_runtime.__file__
    if not isinstance(kernel_file, str):
        _fail("bundle_runtime_mismatch", "gamepack_runtime has no source root")
    _verify_runtime_package(
        package_name="gamepack_runtime",
        package_root=Path(kernel_file).parent,
        snapshot_records=snapshot_records,
        bundle_files=files,
    )
    _verify_runtime_package(
        package_name="gamepack_raylib_2d",
        package_root=Path(__file__).parent,
        snapshot_records=snapshot_records,
        bundle_files=files,
    )


def _verify_assetpack_lineage(
    *,
    manifest: dict[str, Any],
    gamepack: dict[str, Any],
    composition: dict[str, Any],
    adapter: dict[str, Any],
    files: Mapping[str, bytes],
) -> None:
    assetpack_contract = manifest.get("assetpack")
    if type(assetpack_contract) is not dict:
        _fail("bundle_binding_mismatch", "assetpack contract is absent")
    manifest_identity = assetpack_contract.get("manifest")
    if (
        type(manifest_identity) is not dict
        or manifest_identity.get("path") != "assetpack/assetpack.json"
    ):
        _fail("bundle_binding_mismatch", "assetpack manifest path differs")
    assetpack = _decode(files["assetpack/assetpack.json"], "assetpack/assetpack.json")
    _require_content_hash(assetpack, "assetpack")
    if (
        assetpack.get("format") != "world-forge.assetpack"
        or assetpack.get("format_version") != 1
        or assetpack.get("state") != "sealed"
        or _identity(assetpack, id_field="assetpack_id")
        != {
            field: manifest_identity.get(field)
            for field in ("format", "format_version", "id", "content_hash")
        }
    ):
        _fail("bundle_binding_mismatch", "assetpack identity differs")
    game = gamepack["game"]
    if assetpack.get("gamepack") != _identity(gamepack, id_field="missing") | {"id": game["id"]}:
        _fail("bundle_binding_mismatch", "assetpack gamepack identity differs")
    inventory = assetpack.get("inventory")
    if type(inventory) is not dict:
        _fail("bundle_binding_mismatch", "assetpack inventory is absent")
    declared_inventory_hash = inventory.get("content_hash")
    if declared_inventory_hash != _canonical_hash(inventory):
        _fail("bundle_binding_mismatch", "assetpack inventory hash differs")
    records = inventory.get("files")
    if type(records) is not list:
        _fail("bundle_binding_mismatch", "assetpack inventory files are absent")
    expected_paths: list[str] = []
    for record in records:
        if type(record) is not dict:
            _fail("bundle_binding_mismatch", "assetpack inventory record is invalid")
        path = _portable_path(record.get("path"), "assetpack inventory path")
        payload = files.get(f"assetpack/{path}")
        if (
            payload is None
            or len(payload) != record.get("size_bytes")
            or hashlib.sha256(payload).hexdigest() != record.get("sha256")
        ):
            _fail("bundle_binding_mismatch", f"assetpack file differs: {path}")
        expected_paths.append(path)
    if expected_paths != sorted(expected_paths, key=lambda item: item.encode("utf-8")):
        _fail("bundle_binding_mismatch", "assetpack inventory is noncanonical")
    if inventory.get("file_count") != len(records) or inventory.get("total_bytes") != sum(
        int(record["size_bytes"]) for record in records
    ):
        _fail("bundle_binding_mismatch", "assetpack inventory totals differ")
    physical_paths = {
        path.removeprefix("assetpack/")
        for path in files
        if path.startswith("assetpack/") and path != "assetpack/assetpack.json"
    }
    if physical_paths != set(expected_paths):
        _fail("bundle_binding_mismatch", "assetpack physical closure differs")
    root_records = [
        {
            "path": "assetpack.json",
            "sha256": hashlib.sha256(files["assetpack/assetpack.json"]).hexdigest(),
            "size_bytes": len(files["assetpack/assetpack.json"]),
        },
        *records,
    ]
    expected_assetpack_identity = {
        **_identity(assetpack, id_field="assetpack_id"),
        "root_hash": _canonical_hash({"files": root_records}),
        "inventory_hash": declared_inventory_hash,
    }
    if (
        composition.get("assetpack") != expected_assetpack_identity
        or assetpack_contract.get("root_hash") != expected_assetpack_identity["root_hash"]
        or assetpack_contract.get("inventory_hash") != declared_inventory_hash
    ):
        _fail("bundle_binding_mismatch", "assetpack lineage differs")
    outputs: dict[tuple[str, str], Mapping[str, object]] = {}
    assets = assetpack.get("assets")
    if type(assets) is not list:
        _fail("bundle_binding_mismatch", "assetpack assets are absent")
    for asset in assets:
        if type(asset) is not dict or type(asset.get("asset")) is not dict:
            _fail("bundle_binding_mismatch", "assetpack asset entry is invalid")
        asset_id = asset["asset"].get("asset_id")
        raw_outputs = asset.get("outputs")
        if type(asset_id) is not str or type(raw_outputs) is not list:
            _fail("bundle_binding_mismatch", "assetpack output entry is invalid")
        for output in raw_outputs:
            if type(output) is not dict or type(output.get("runtime_path")) is not str:
                _fail("bundle_binding_mismatch", "assetpack output is invalid")
            outputs[(asset_id, output["runtime_path"])] = output
    manifest_bindings = manifest.get("bindings")
    composition_bindings = composition.get("bindings")
    adapter_bindings = adapter.get("asset_bindings")
    if (
        type(manifest_bindings) is not list
        or type(composition_bindings) is not list
        or type(adapter_bindings) is not list
    ):
        _fail("bundle_binding_mismatch", "runtime bindings are absent")
    normalized_manifest = [
        {field: binding[field] for field in binding if field != "bundle_path"}
        for binding in manifest_bindings
    ]
    if normalized_manifest != composition_bindings:
        _fail("bundle_binding_mismatch", "composition bindings differ")
    adapter_by_id = {
        binding["binding_id"]: binding
        for binding in adapter_bindings
        if type(binding) is dict and type(binding.get("binding_id")) is str
    }
    for binding in composition_bindings:
        if type(binding) is not dict:
            _fail("bundle_binding_mismatch", "composition binding is invalid")
        adapter_binding = adapter_by_id.get(binding.get("binding_id"))
        output = outputs.get((binding.get("asset_id"), binding.get("runtime_path")))
        if (
            adapter_binding is None
            or output is None
            or any(
                binding.get(field) != adapter_binding.get(field)
                for field in (
                    "binding_id",
                    "asset_id",
                    "role",
                    "media_type",
                    "runtime_path",
                )
            )
            or any(
                binding.get(field) != output.get(field)
                for field in (
                    "role",
                    "media_type",
                    "runtime_path",
                    "sha256",
                    "size_bytes",
                )
            )
        ):
            _fail("bundle_binding_mismatch", "runtime binding lineage differs")


def load_runtime_bundle(root: str | Path) -> LoadedRuntimeBundle:
    files = _capture_bundle(Path(root))
    manifest_payload = files.get("game-runtime-bundle.json")
    if manifest_payload is None:
        _fail("bundle_manifest_missing", "runtime bundle manifest is absent")
    manifest = _decode(manifest_payload, "game-runtime-bundle.json")
    if (
        manifest.get("format") != "world-forge.game_runtime_bundle"
        or manifest.get("format_version") != 1
        or manifest.get("state") != "pre_execution"
    ):
        _fail("bundle_contract_invalid", "runtime bundle identity/state is unsupported")
    manifest_fields = {
        "assetpack",
        "bindings",
        "bundle_id",
        "content_hash",
        "contracts",
        "files",
        "format",
        "format_version",
        "legal",
        "runtime_snapshot_tree",
        "state",
        "tree_hash",
    }
    if set(manifest) != manifest_fields:
        _fail("bundle_contract_invalid", "runtime bundle fields are not closed")
    _require_content_hash(manifest, "runtime bundle")
    bundle_seed = {
        field: manifest[field]
        for field in manifest_fields
        if field not in {"bundle_id", "content_hash"}
    }
    expected_bundle_id = "game_runtime_bundle_" + _canonical_hash(bundle_seed)[:48]
    if manifest.get("bundle_id") != expected_bundle_id:
        _fail("bundle_contract_invalid", "runtime bundle ID is not deterministic")
    records = manifest.get("files")
    if type(records) is not list or not records or len(records) > MAX_BUNDLE_FILES:
        _fail("bundle_contract_invalid", "runtime bundle file inventory is invalid")
    expected_paths: list[str] = []
    for index, record in enumerate(records):
        if type(record) is not dict or set(record) != {"path", "sha256", "size_bytes"}:
            _fail("bundle_contract_invalid", f"runtime bundle file {index} is invalid")
        path = _portable_path(record.get("path"), f"runtime bundle file {index}")
        digest = _sha256(record.get("sha256"), f"runtime bundle file {index}.sha256")
        size = record.get("size_bytes")
        if type(size) is not int or not 0 <= size <= MAX_BUNDLE_FILE_BYTES:
            _fail("bundle_contract_invalid", f"runtime bundle file {index}.size is invalid")
        payload = files.get(path)
        if payload is None or len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
            _fail("bundle_file_mismatch", f"runtime bundle file differs: {path}")
        expected_paths.append(path)
    if expected_paths != sorted(expected_paths, key=lambda item: item.encode("utf-8")):
        _fail("bundle_contract_invalid", "runtime bundle file inventory is noncanonical")
    if len({path.casefold() for path in expected_paths}) != len(expected_paths):
        _fail("bundle_path_collision", "runtime bundle file inventory collides")
    if set(files) != {*expected_paths, "game-runtime-bundle.json"}:
        _fail("bundle_file_mismatch", "runtime bundle physical file closure differs")
    if manifest.get("tree_hash") != _canonical_hash({"files": records}):
        _fail("bundle_hash_mismatch", "runtime bundle tree hash differs")

    contracts = manifest.get("contracts")
    if type(contracts) is not dict:
        _fail("bundle_contract_invalid", "runtime bundle contracts are absent")
    required_paths = {
        "gamepack": "contracts/gamepack.json",
        "runtime_snapshot": "contracts/runtime-snapshot.json",
        "runtime_adapter_registry": "contracts/runtime-adapter-registry.json",
        "runtime_composition": "contracts/runtime-composition.json",
        "runtime_support_report": "status/runtime-support-report.json",
    }
    for field, path in required_paths.items():
        identity = contracts.get(field)
        if type(identity) is not dict or identity.get("path") != path:
            _fail("bundle_contract_invalid", f"runtime bundle {field} path differs")
    try:
        gamepack = load_gamepack_bytes(files[required_paths["gamepack"]], source="bundle gamepack")
    except GameLogicError as exc:
        _fail("bundle_gamepack_invalid", str(exc))
    snapshot = _decode(
        files[required_paths["runtime_snapshot"]], required_paths["runtime_snapshot"]
    )
    registry = _decode(
        files[required_paths["runtime_adapter_registry"]],
        required_paths["runtime_adapter_registry"],
    )
    composition = _decode(
        files[required_paths["runtime_composition"]],
        required_paths["runtime_composition"],
    )
    support = _decode(
        files[required_paths["runtime_support_report"]],
        required_paths["runtime_support_report"],
    )
    for document, context in (
        (snapshot, "runtime snapshot"),
        (registry, "runtime registry"),
        (composition, "runtime composition"),
        (support, "runtime support"),
    ):
        _require_content_hash(document, context)
    adapter_identity = contracts.get("runtime_adapter")
    if type(adapter_identity) is not dict:
        _fail("bundle_contract_invalid", "runtime adapter identity is absent")
    adapter_path = _portable_path(adapter_identity.get("path"), "runtime adapter path")
    adapter = _decode(files[adapter_path], adapter_path)
    _require_content_hash(adapter, "runtime adapter")
    if (
        adapter.get("adapter_id") != adapter_identity.get("id")
        or adapter.get("adapter_version") != adapter_identity.get("adapter_version")
        or adapter.get("content_hash") != adapter_identity.get("content_hash")
    ):
        _fail("bundle_contract_invalid", "runtime adapter identity differs")

    _verify_runtime_lineage(
        manifest=manifest,
        gamepack=gamepack,
        snapshot=snapshot,
        registry=registry,
        composition=composition,
        support=support,
        adapter=adapter,
        files=files,
    )
    _verify_assetpack_lineage(
        manifest=manifest,
        gamepack=gamepack,
        composition=composition,
        adapter=adapter,
        files=files,
    )

    snapshot_records = snapshot.get("files")
    if type(snapshot_records) is not list or not snapshot_records:
        _fail("bundle_contract_invalid", "runtime snapshot file inventory is absent")
    for record in snapshot_records:
        if type(record) is not dict:
            _fail("bundle_contract_invalid", "runtime snapshot file record is invalid")
        path = _portable_path(record.get("path"), "runtime snapshot path")
        bundle_path = f"runtime/snapshot-tree/{path}"
        payload = files.get(bundle_path)
        if (
            payload is None
            or len(payload) != record.get("size_bytes")
            or hashlib.sha256(payload).hexdigest() != record.get("sha256")
        ):
            _fail("bundle_snapshot_mismatch", f"runtime snapshot bytes differ: {path}")
    descriptor_path = (
        f"runtime/snapshot-tree/descriptors/{adapter['adapter_id']}@"
        f"{adapter['adapter_version']}.json"
    )
    if files.get(descriptor_path) != files[adapter_path]:
        _fail("bundle_snapshot_mismatch", "runtime adapter descriptor bytes differ")

    raw_bindings = manifest.get("bindings")
    if type(raw_bindings) is not list or not raw_bindings:
        _fail("bundle_contract_invalid", "runtime bundle bindings are absent")
    bindings: dict[str, BoundResource] = {}
    for index, raw in enumerate(raw_bindings):
        if type(raw) is not dict:
            _fail("bundle_contract_invalid", f"runtime binding {index} is invalid")
        binding_id = raw.get("binding_id")
        if type(binding_id) is not str or binding_id in bindings:
            _fail("bundle_contract_invalid", f"runtime binding {index} ID is invalid")
        bundle_path = _portable_path(raw.get("bundle_path"), f"runtime binding {binding_id}")
        runtime_path = _portable_path(raw.get("runtime_path"), f"runtime binding {binding_id}")
        if bundle_path != f"assetpack/{runtime_path}":
            _fail("bundle_binding_mismatch", f"runtime binding {binding_id} path differs")
        payload = files.get(bundle_path)
        sha256 = _sha256(raw.get("sha256"), f"runtime binding {binding_id}.sha256")
        size = raw.get("size_bytes")
        if (
            payload is None
            or type(size) is not int
            or size != len(payload)
            or hashlib.sha256(payload).hexdigest() != sha256
        ):
            _fail("bundle_binding_mismatch", f"runtime binding {binding_id} bytes differ")
        media_type = raw.get("media_type")
        dimensions: tuple[int, int] | None
        if media_type == "image/png":
            dimensions = _png_dimensions(payload)
        elif media_type == "font/ttf":
            _validate_ttf(payload)
            dimensions = None
        else:
            _fail("resource_media_invalid", f"runtime binding {binding_id} media is unsupported")
        asset_id = raw.get("asset_id")
        role = raw.get("role")
        if type(asset_id) is not str or type(role) is not str:
            _fail("bundle_binding_mismatch", f"runtime binding {binding_id} metadata differs")
        bindings[binding_id] = BoundResource(
            binding_id,
            asset_id,
            role,
            media_type,
            runtime_path,
            bundle_path,
            sha256,
            size,
            payload,
            dimensions,
        )
    expected_binding_ids = [item.get("binding_id") for item in adapter.get("asset_bindings", [])]
    if set(bindings) != set(expected_binding_ids):
        _fail("bundle_binding_mismatch", "runtime bindings differ from the exact adapter")
    return LoadedRuntimeBundle(
        Path(os.path.abspath(os.fspath(root))),
        manifest,
        gamepack,
        composition,
        adapter,
        snapshot,
        registry,
        support,
        MappingProxyType(bindings),
        files,
    )


class ResourceManager:
    __slots__ = ("_backend", "_bundle", "_closed", "_draws", "_handles", "_unique")

    def __init__(self, bundle: LoadedRuntimeBundle, backend: RaylibBackend) -> None:
        if type(bundle) is not LoadedRuntimeBundle:
            _fail("resource_bundle_invalid", "resource manager requires a loaded bundle")
        if not isinstance(backend, RaylibBackend):
            _fail("resource_backend_invalid", "resource manager requires the backend protocol")
        self._bundle = bundle
        self._backend = backend
        self._handles: dict[str, TextureHandle | FontHandle] = {}
        self._unique: list[tuple[str, TextureHandle | FontHandle]] = []
        self._draws = {binding_id: 0 for binding_id in bundle.bindings}
        self._closed = False

    def load(self) -> None:
        if self._closed:
            _fail("resource_manager_closed", "resource manager is closed")
        if self._handles:
            return
        shared: dict[tuple[str, int], TextureHandle | FontHandle] = {}
        try:
            for binding_id in sorted(self._bundle.bindings, key=lambda item: item.encode("utf-8")):
                resource = self._bundle.bindings[binding_id]
                key = (resource.sha256, resource.size_bytes)
                handle = shared.get(key)
                if handle is None:
                    if resource.media_type == "image/png":
                        assert resource.dimensions is not None
                        handle = self._backend.load_texture_png(
                            resource.payload,
                            identity=resource.sha256,
                            width=resource.dimensions[0],
                            height=resource.dimensions[1],
                        )
                        kind = "texture"
                    else:
                        handle = self._backend.load_font_ttf(
                            resource.payload,
                            identity=resource.sha256,
                            font_size=32,
                        )
                        kind = "font"
                    shared[key] = handle
                    self._unique.append((kind, handle))
                self._handles[binding_id] = handle
        except BaseException:
            self.close()
            raise

    def handle(self, binding_id: str) -> TextureHandle | FontHandle:
        if binding_id not in self._handles:
            _fail("resource_binding_unloaded", f"runtime binding is not loaded: {binding_id}")
        return self._handles[binding_id]

    def mark_drawn(self, binding_id: str) -> None:
        if binding_id not in self._draws or binding_id not in self._handles:
            _fail("resource_binding_unloaded", f"runtime binding is not loaded: {binding_id}")
        self._draws[binding_id] += 1

    def report(self) -> dict[str, dict[str, object]]:
        return {
            binding_id: {
                "sha256": self._bundle.bindings[binding_id].sha256,
                "size_bytes": self._bundle.bindings[binding_id].size_bytes,
                "loaded": binding_id in self._handles,
                "draw_count": self._draws[binding_id],
            }
            for binding_id in sorted(self._draws, key=lambda item: item.encode("utf-8"))
        }

    def close(self) -> None:
        if self._closed:
            return
        for kind, handle in reversed(self._unique):
            if kind == "texture":
                assert isinstance(handle, TextureHandle)
                self._backend.unload_texture(handle)
            else:
                assert isinstance(handle, FontHandle)
                self._backend.unload_font(handle)
        self._handles.clear()
        self._unique.clear()
        self._closed = True


__all__ = [
    "BoundResource",
    "LoadedRuntimeBundle",
    "RaylibResourceError",
    "ResourceManager",
    "load_runtime_bundle",
]
