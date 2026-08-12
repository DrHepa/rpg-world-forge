"""Deterministic, bounded packaging for one verified standalone game."""

from __future__ import annotations

import copy
import hashlib
import io
import os
import re
import stat
import unicodedata
import zipfile
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from gamepack_runtime.distribution import (
    GAME_LOCK_PATH,
    GAME_MANIFEST_PATH,
    MAX_STANDALONE_FILE_BYTES,
    MAX_STANDALONE_FILES,
    MAX_STANDALONE_JSON_BYTES,
    MAX_STANDALONE_TOTAL_BYTES,
    STANDALONE_GAME_FORMAT,
    STANDALONE_GAME_LOCK_FORMAT,
    StandaloneDistributionError,
    canonical_contract_bytes,
    canonical_contract_hash,
    capture_standalone_tree,
    decode_json_object,
    portable_relative_path,
    validate_standalone_game_document,
    validate_standalone_game_lock_document,
    verify_captured_standalone_distribution,
)
from gamepack_runtime.persistence_io import (
    PersistenceIOError,
    read_immutable_file_bytes,
)

GAME_PACKAGE_FORMAT = "world-forge.game_package"
GAME_PACKAGE_VERSION = 1
PACKAGE_MANIFEST_PATH = "PACKAGE-MANIFEST.json"

MAX_GAME_PACKAGE_ENTRIES = MAX_STANDALONE_FILES + 1
MAX_GAME_PACKAGE_MEMBER_BYTES = MAX_STANDALONE_FILE_BYTES
MAX_GAME_PACKAGE_EXPANDED_BYTES = MAX_STANDALONE_TOTAL_BYTES
MAX_GAME_PACKAGE_ARCHIVE_BYTES = MAX_GAME_PACKAGE_EXPANDED_BYTES + 8 * 1024 * 1024
MAX_GAME_PACKAGE_MANIFEST_BYTES = MAX_STANDALONE_JSON_BYTES

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ZIP_MODE = stat.S_IFREG | 0o644
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PACKAGE_ID_RE = re.compile(r"game_package_[0-9a-f]{40}")
_CONTRACT_ID_RE = re.compile(r"(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}")
_PACKAGE_PATH_RE = re.compile(
    r"(?!(?:[Aa][Uu][Xx]|[Cc][Oo][Nn]|[Nn][Uu][Ll]|[Pp][Rr][Nn]"
    r"|[Cc][Oo][Mm][1-9]|[Ll][Pp][Tt][1-9])(?:[.]|/|$))"
    r"[A-Za-z0-9_.@ -]*[A-Za-z0-9_@-]"
    r"(?:/(?!(?:[Aa][Uu][Xx]|[Cc][Oo][Nn]|[Nn][Uu][Ll]|[Pp][Rr][Nn]"
    r"|[Cc][Oo][Mm][1-9]|[Ll][Pp][Tt][1-9])(?:[.]|/|$))"
    r"[A-Za-z0-9_.@ -]*[A-Za-z0-9_@-])*"
)
_MANIFEST_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "package_id",
        "game_id",
        "lineage",
        "standalone_game",
        "payload_lock",
        "files",
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
_STANDALONE_FIELDS = frozenset({"format", "format_version", "game_id", "content_hash"})
_LOCK_FIELDS = frozenset({"format", "format_version", "id", "content_hash", "tree_hash"})
_FILE_FIELDS = frozenset({"path", "sha256", "size_bytes"})


class GamePackageError(ValueError):
    """Raised when package bytes fail the closed generic game boundary."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise GamePackageError(reason_code, detail)


class VerifiedGamePackage:
    """Immutable verified package snapshot with copy-on-read documents."""

    __slots__ = ("_archive_bytes", "_files", "_manifest_bytes")

    def __init__(
        self,
        *,
        archive_bytes: bytes,
        manifest: Mapping[str, object],
        files: Mapping[str, bytes],
    ) -> None:
        object.__setattr__(self, "_archive_bytes", bytes(archive_bytes))
        object.__setattr__(self, "_manifest_bytes", canonical_contract_bytes(manifest))
        object.__setattr__(
            self,
            "_files",
            tuple(
                (path, bytes(payload))
                for path, payload in sorted(
                    files.items(),
                    key=lambda item: item[0].encode("utf-8"),
                )
            ),
        )

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("verified game packages are immutable")

    @property
    def archive_bytes(self) -> bytes:
        return self._archive_bytes

    @property
    def archive_sha256(self) -> str:
        return hashlib.sha256(self._archive_bytes).hexdigest()

    @property
    def manifest(self) -> dict[str, Any]:
        return decode_json_object(self._manifest_bytes, PACKAGE_MANIFEST_PATH)

    @property
    def files(self) -> Mapping[str, bytes]:
        return MappingProxyType(dict(self._files))

    def read_bytes(self, relative: str) -> bytes:
        try:
            return dict(self._files)[relative]
        except KeyError:
            _fail("game_package_file_missing", f"{relative} is absent")

    def close(self) -> None:
        """Keep API symmetry with descriptor-backed verified artifacts."""


def _exact(value: object, fields: frozenset[str], context: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail("game_package_contract_invalid", f"{context} fields are not closed")
    return value


def _sha256(value: object, context: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail("game_package_contract_invalid", f"{context} must be a SHA-256")
    return value


def _portable_path(value: object, context: str) -> str:
    try:
        path = portable_relative_path(value, context)
    except StandaloneDistributionError as exc:
        _fail("game_package_path_invalid", str(exc))
    if _PACKAGE_PATH_RE.fullmatch(path) is None:
        _fail(
            "game_package_path_invalid",
            f"{context} is outside the closed ASCII package path policy",
        )
    return path


def _identifier(value: object, context: str) -> str:
    if type(value) is not str or _CONTRACT_ID_RE.fullmatch(value) is None:
        _fail("game_package_contract_invalid", f"{context} is invalid")
    return value


def _path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _validate_path_set(paths: list[str], context: str) -> None:
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")):
        _fail("game_package_file_order_invalid", f"{context} is not UTF-8 byte sorted")
    for path in paths:
        _portable_path(path, context)
    folded = {_path_key(path) for path in paths}
    if len(folded) != len(paths):
        _fail(
            "game_package_path_collision",
            f"{context} collides by NFC/casefold",
        )
    for path in paths:
        parts = path.split("/")
        for depth in range(1, len(parts)):
            prefix = "/".join(parts[:depth])
            if _path_key(prefix) in folded:
                _fail(
                    "game_package_path_collision",
                    f"{context} has a file/directory prefix collision: {path}",
                )


def _file_inventory(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    return [
        {
            "path": path,
            "sha256": hashlib.sha256(files[path]).hexdigest(),
            "size_bytes": len(files[path]),
        }
        for path in sorted(files, key=lambda item: item.encode("utf-8"))
    ]


def _package_id_seed(
    *,
    game_id: str,
    lineage: Mapping[str, object],
    standalone_game: Mapping[str, object],
    payload_lock: Mapping[str, object],
    files: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "game_id": game_id,
        "lineage": copy.deepcopy(dict(lineage)),
        "standalone_game": copy.deepcopy(dict(standalone_game)),
        "payload_lock": copy.deepcopy(dict(payload_lock)),
        "files": copy.deepcopy(files),
    }


def validate_game_package_document(value: object) -> dict[str, Any]:
    document = _exact(value, _MANIFEST_FIELDS, "game package")
    if document.get("format") != GAME_PACKAGE_FORMAT:
        _fail(
            "game_package_format_mismatch",
            f"format must be {GAME_PACKAGE_FORMAT}",
        )
    if (
        type(document.get("format_version")) is not int
        or document.get("format_version") != GAME_PACKAGE_VERSION
    ):
        _fail("game_package_version_mismatch", "format_version must be 1")
    package_id = document.get("package_id")
    if type(package_id) is not str or _PACKAGE_ID_RE.fullmatch(package_id) is None:
        _fail("game_package_contract_invalid", "package_id is invalid")
    game_id = _identifier(document.get("game_id"), "game package.game_id")

    lineage = _exact(document.get("lineage"), _LINEAGE_FIELDS, "game package.lineage")
    for field in _LINEAGE_FIELDS:
        _sha256(lineage.get(field), f"game package.lineage.{field}")

    standalone = _exact(
        document.get("standalone_game"),
        _STANDALONE_FIELDS,
        "game package.standalone_game",
    )
    if (
        standalone.get("format") != STANDALONE_GAME_FORMAT
        or type(standalone.get("format_version")) is not int
        or standalone.get("format_version") != 1
        or _identifier(
            standalone.get("game_id"),
            "game package.standalone_game.game_id",
        )
        != game_id
    ):
        _fail(
            "game_package_standalone_identity_invalid",
            "standalone identity differs from the package game",
        )
    _sha256(
        standalone.get("content_hash"),
        "game package.standalone_game.content_hash",
    )

    lock = _exact(document.get("payload_lock"), _LOCK_FIELDS, "game package.payload_lock")
    if (
        lock.get("format") != STANDALONE_GAME_LOCK_FORMAT
        or type(lock.get("format_version")) is not int
        or lock.get("format_version") != 1
    ):
        _fail("game_package_lock_identity_invalid", "payload lock identity is invalid")
    _identifier(lock.get("id"), "game package.payload_lock.id")
    _sha256(lock.get("content_hash"), "game package.payload_lock.content_hash")
    _sha256(lock.get("tree_hash"), "game package.payload_lock.tree_hash")

    raw_files = document.get("files")
    if type(raw_files) is not list or not raw_files or len(raw_files) > MAX_STANDALONE_FILES:
        _fail("game_package_limit_exceeded", "package file inventory exceeds its limit")
    files: list[dict[str, object]] = []
    paths: list[str] = []
    total = 0
    for index, raw in enumerate(raw_files):
        record = _exact(raw, _FILE_FIELDS, f"game package.files/{index}")
        path = _portable_path(record.get("path"), f"game package.files/{index}.path")
        if path == PACKAGE_MANIFEST_PATH:
            _fail(
                "game_package_inventory_invalid",
                "the internal package manifest is not a payload file",
            )
        digest = _sha256(record.get("sha256"), f"game package.files/{index}.sha256")
        size = record.get("size_bytes")
        if type(size) is not int or not 0 <= size <= MAX_GAME_PACKAGE_MEMBER_BYTES:
            _fail("game_package_limit_exceeded", f"{path} exceeds its member limit")
        total += size
        if total > MAX_GAME_PACKAGE_EXPANDED_BYTES:
            _fail("game_package_limit_exceeded", "package payload exceeds its byte limit")
        paths.append(path)
        files.append({"path": path, "sha256": digest, "size_bytes": size})
    _validate_path_set(paths, "game package file inventory")
    if {GAME_MANIFEST_PATH, GAME_LOCK_PATH}.difference(paths):
        _fail(
            "game_package_inventory_invalid",
            "standalone manifest and lock are required",
        )

    expected_id = (
        "game_package_"
        + canonical_contract_hash(
            _package_id_seed(
                game_id=game_id,
                lineage=lineage,
                standalone_game=standalone,
                payload_lock=lock,
                files=files,
            )
        )[:40]
    )
    if package_id != expected_id:
        _fail("game_package_id_mismatch", "package_id is not canonical")
    content_hash = _sha256(document.get("content_hash"), "game package.content_hash")
    if content_hash != canonical_contract_hash(document):
        _fail("game_package_content_hash_mismatch", "content_hash is not canonical")
    return copy.deepcopy(document)


def _build_manifest(files: Mapping[str, bytes]) -> dict[str, Any]:
    try:
        standalone = validate_standalone_game_document(
            decode_json_object(files[GAME_MANIFEST_PATH], GAME_MANIFEST_PATH)
        )
        lock = validate_standalone_game_lock_document(
            decode_json_object(files[GAME_LOCK_PATH], GAME_LOCK_PATH)
        )
    except (KeyError, StandaloneDistributionError) as exc:
        _fail("game_package_standalone_invalid", str(exc))
    records = _file_inventory(files)
    standalone_identity = {
        "format": STANDALONE_GAME_FORMAT,
        "format_version": 1,
        "game_id": standalone["game_id"],
        "content_hash": standalone["content_hash"],
    }
    lock_identity = {
        "format": STANDALONE_GAME_LOCK_FORMAT,
        "format_version": 1,
        "id": lock["lock_id"],
        "content_hash": lock["content_hash"],
        "tree_hash": lock["tree_hash"],
    }
    seed = _package_id_seed(
        game_id=standalone["game_id"],
        lineage=standalone["lineage"],
        standalone_game=standalone_identity,
        payload_lock=lock_identity,
        files=records,
    )
    document: dict[str, Any] = {
        "format": GAME_PACKAGE_FORMAT,
        "format_version": GAME_PACKAGE_VERSION,
        "package_id": "game_package_" + canonical_contract_hash(seed)[:40],
        **seed,
        "content_hash": "",
    }
    document["content_hash"] = canonical_contract_hash(document)
    return validate_game_package_document(document)


def _canonical_zip_bytes(entries: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_STORED,
        allowZip64=False,
        strict_timestamps=True,
    ) as archive:
        archive.comment = b""
        for path in sorted(entries, key=lambda item: item.encode("utf-8")):
            info = zipfile.ZipInfo(path, _ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = _ZIP_MODE << 16
            info.internal_attr = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, entries[path])
    payload = output.getvalue()
    if len(payload) > MAX_GAME_PACKAGE_ARCHIVE_BYTES:
        _fail("game_package_limit_exceeded", "archive exceeds its byte limit")
    return payload


def _verify_standalone(files: Mapping[str, bytes]) -> dict[str, object]:
    try:
        return verify_captured_standalone_distribution(files)
    except StandaloneDistributionError as exc:
        _fail("game_package_standalone_invalid", str(exc))


def build_game_package_from_files(files: Mapping[str, bytes]) -> VerifiedGamePackage:
    if not isinstance(files, Mapping):
        _fail("game_package_standalone_invalid", "standalone files must be a mapping")
    captured: dict[str, bytes] = {}
    total = 0
    for raw_path, raw_payload in files.items():
        path = _portable_path(raw_path, "standalone package input path")
        if type(raw_payload) is not bytes:
            _fail("game_package_standalone_invalid", f"{path} is not exact bytes")
        if len(raw_payload) > MAX_GAME_PACKAGE_MEMBER_BYTES:
            _fail("game_package_limit_exceeded", f"{path} exceeds its member limit")
        total += len(raw_payload)
        if total > MAX_GAME_PACKAGE_EXPANDED_BYTES:
            _fail("game_package_limit_exceeded", "standalone payload exceeds its byte limit")
        if path in captured:
            _fail("game_package_path_collision", f"duplicate path: {path}")
        captured[path] = bytes(raw_payload)
    if len(captured) > MAX_STANDALONE_FILES:
        _fail("game_package_limit_exceeded", "standalone file count exceeds its limit")
    _validate_path_set(
        sorted(captured, key=lambda item: item.encode("utf-8")),
        "standalone package input",
    )
    _verify_standalone(captured)
    manifest = _build_manifest(captured)
    manifest_payload = canonical_contract_bytes(manifest)
    if len(manifest_payload) > MAX_GAME_PACKAGE_MANIFEST_BYTES:
        _fail("game_package_limit_exceeded", "package manifest exceeds its byte limit")
    entries = {PACKAGE_MANIFEST_PATH: manifest_payload, **captured}
    if sum(len(payload) for payload in entries.values()) > MAX_GAME_PACKAGE_EXPANDED_BYTES:
        _fail("game_package_limit_exceeded", "expanded package exceeds its byte limit")
    archive = _canonical_zip_bytes(entries)
    return verify_game_package_bytes(archive)


def build_game_package_from_standalone(root: str | Path) -> VerifiedGamePackage:
    try:
        files = capture_standalone_tree(root)
    except StandaloneDistributionError as exc:
        _fail("game_package_standalone_invalid", str(exc))
    return build_game_package_from_files(files)


def _read_archive_entries(payload: bytes) -> dict[str, bytes]:
    if type(payload) is not bytes or len(payload) > MAX_GAME_PACKAGE_ARCHIVE_BYTES:
        _fail("game_package_limit_exceeded", "archive exceeds its byte limit")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload), mode="r")
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        _fail("game_package_archive_invalid", str(exc))
    with archive:
        if archive.comment:
            _fail("game_package_archive_metadata_invalid", "archive comment is forbidden")
        infos = archive.infolist()
        if not infos or len(infos) > MAX_GAME_PACKAGE_ENTRIES:
            _fail("game_package_limit_exceeded", "archive entry count exceeds its limit")
        names = [info.filename for info in infos]
        _validate_path_set(names, "game package archive")
        if PACKAGE_MANIFEST_PATH not in names:
            _fail(
                "game_package_manifest_missing",
                f"{PACKAGE_MANIFEST_PATH} is absent",
            )
        total = 0
        entries: dict[str, bytes] = {}
        for info in infos:
            if (
                info.is_dir()
                or info.filename.endswith("/")
                or info.compress_type != zipfile.ZIP_STORED
                or info.flag_bits & 0x1
                or info.create_system != 3
                or info.date_time != _ZIP_TIMESTAMP
                or stat.S_IFMT(info.external_attr >> 16) != stat.S_IFREG
                or stat.S_IMODE(info.external_attr >> 16) != 0o644
                or info.extra
                or info.comment
            ):
                _fail(
                    "game_package_archive_metadata_invalid",
                    f"{info.filename} is not a canonical regular stored member",
                )
            if (
                info.file_size < 0
                or info.compress_size != info.file_size
                or info.file_size > MAX_GAME_PACKAGE_MEMBER_BYTES
            ):
                _fail(
                    "game_package_limit_exceeded",
                    f"{info.filename} exceeds its member limit",
                )
            total += info.file_size
            if total > MAX_GAME_PACKAGE_EXPANDED_BYTES:
                _fail("game_package_limit_exceeded", "expanded archive exceeds its limit")
        for info in infos:
            try:
                with archive.open(info, mode="r") as member:
                    data = member.read(MAX_GAME_PACKAGE_MEMBER_BYTES + 1)
                    if member.read(1):
                        _fail(
                            "game_package_limit_exceeded",
                            f"{info.filename} exceeds its member limit",
                        )
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                _fail("game_package_archive_invalid", str(exc))
            if len(data) != info.file_size:
                _fail(
                    "game_package_archive_invalid",
                    f"{info.filename} size differs from its ZIP metadata",
                )
            entries[info.filename] = data
    return entries


def verify_game_package_bytes(payload: bytes) -> VerifiedGamePackage:
    entries = _read_archive_entries(payload)
    try:
        manifest_payload = entries[PACKAGE_MANIFEST_PATH]
    except KeyError:
        _fail(
            "game_package_manifest_missing",
            f"{PACKAGE_MANIFEST_PATH} is absent",
        )
    if len(manifest_payload) > MAX_GAME_PACKAGE_MANIFEST_BYTES:
        _fail("game_package_limit_exceeded", "package manifest exceeds its byte limit")
    try:
        manifest = validate_game_package_document(
            decode_json_object(manifest_payload, PACKAGE_MANIFEST_PATH)
        )
    except StandaloneDistributionError as exc:
        _fail("game_package_contract_invalid", str(exc))
    if canonical_contract_bytes(manifest) != manifest_payload:
        _fail(
            "game_package_canonical_manifest_invalid",
            "package manifest JSON is not canonical",
        )
    files = {path: data for path, data in entries.items() if path != PACKAGE_MANIFEST_PATH}
    if _file_inventory(files) != manifest["files"]:
        _fail(
            "game_package_file_inventory_mismatch",
            "archive payload differs from the package inventory",
        )
    report = _verify_standalone(files)
    standalone = validate_standalone_game_document(
        decode_json_object(files[GAME_MANIFEST_PATH], GAME_MANIFEST_PATH)
    )
    lock = validate_standalone_game_lock_document(
        decode_json_object(files[GAME_LOCK_PATH], GAME_LOCK_PATH)
    )
    if manifest["lineage"] != standalone["lineage"]:
        _fail("game_package_lineage_mismatch", "standalone lineage differs")
    if manifest["standalone_game"] != {
        "format": STANDALONE_GAME_FORMAT,
        "format_version": 1,
        "game_id": standalone["game_id"],
        "content_hash": standalone["content_hash"],
    }:
        _fail("game_package_lineage_mismatch", "standalone identity differs")
    if manifest["payload_lock"] != {
        "format": STANDALONE_GAME_LOCK_FORMAT,
        "format_version": 1,
        "id": lock["lock_id"],
        "content_hash": lock["content_hash"],
        "tree_hash": lock["tree_hash"],
    }:
        _fail("game_package_lineage_mismatch", "payload lock identity differs")
    if report["manifest_hash"] != manifest["standalone_game"]["content_hash"]:
        _fail("game_package_lineage_mismatch", "verified standalone hash differs")
    rebuilt = _canonical_zip_bytes(entries)
    if rebuilt != payload:
        _fail(
            "game_package_canonical_archive_invalid",
            "archive bytes are not the canonical package encoding",
        )
    return VerifiedGamePackage(
        archive_bytes=payload,
        manifest=manifest,
        files=files,
    )


def verify_game_package_file(path: str | Path) -> VerifiedGamePackage:
    source = Path(os.path.abspath(os.fspath(path)))
    try:
        payload = read_immutable_file_bytes(
            source,
            limit=MAX_GAME_PACKAGE_ARCHIVE_BYTES,
        )
    except (OSError, PersistenceIOError) as exc:
        _fail("game_package_file_invalid", str(exc))
    return verify_game_package_bytes(payload)


__all__ = [
    "GAME_PACKAGE_FORMAT",
    "GAME_PACKAGE_VERSION",
    "MAX_GAME_PACKAGE_ARCHIVE_BYTES",
    "MAX_GAME_PACKAGE_ENTRIES",
    "MAX_GAME_PACKAGE_EXPANDED_BYTES",
    "MAX_GAME_PACKAGE_MANIFEST_BYTES",
    "MAX_GAME_PACKAGE_MEMBER_BYTES",
    "PACKAGE_MANIFEST_PATH",
    "GamePackageError",
    "VerifiedGamePackage",
    "build_game_package_from_files",
    "build_game_package_from_standalone",
    "validate_game_package_document",
    "verify_game_package_bytes",
    "verify_game_package_file",
]
