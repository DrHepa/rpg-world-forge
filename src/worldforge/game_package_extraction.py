"""Deterministic evidence for one verified game-package extraction."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from gamepack_runtime.game_package import (
    MAX_GAME_PACKAGE_ARCHIVE_BYTES,
    GamePackageError,
    validate_game_package_document,
)
from worldforge.integrity import canonical_payload_hash

GAME_PACKAGE_EXTRACTION_FORMAT = "world-forge.game_package_extraction"
GAME_PACKAGE_EXTRACTION_VERSION = 1
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PACKAGE_ID_RE = re.compile(r"^game_package_[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "extraction_id",
        "package",
        "standalone_game",
        "payload_lock",
        "lineage",
        "extracted_tree_hash",
        "verification_status",
        "content_hash",
    }
)
_PACKAGE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "content_hash",
        "archive_sha256",
        "size_bytes",
    }
)
_STANDALONE_FIELDS = frozenset({"format", "format_version", "game_id", "content_hash"})
_LOCK_FIELDS = frozenset({"format", "format_version", "id", "content_hash", "tree_hash"})
_LINEAGE_FIELDS = frozenset(
    {
        "gamepack_hash",
        "assetpack_hash",
        "runtime_snapshot_hash",
        "runtime_composition_hash",
        "runtime_bundle_hash",
    }
)


class GamePackageExtractionEvidenceError(ValueError):
    """Raised when extraction evidence is malformed or crosses package lineage."""


def _fail(detail: str) -> None:
    raise GamePackageExtractionEvidenceError(detail)


def _sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(f"{field} must be a lowercase SHA-256 digest")
    return value


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _fail(f"{field} is not a canonical identifier")
    return value


def _package_projection(
    package: Mapping[str, Any],
    *,
    archive_sha256: str,
    archive_size_bytes: int,
) -> dict[str, Any]:
    return {
        "format": package["format"],
        "format_version": package["format_version"],
        "id": package["package_id"],
        "content_hash": package["content_hash"],
        "archive_sha256": archive_sha256,
        "size_bytes": archive_size_bytes,
    }


def build_game_package_extraction_evidence(
    package_manifest: Mapping[str, Any],
    *,
    archive_sha256: str,
    archive_size_bytes: int,
) -> dict[str, Any]:
    """Build canonical pathless evidence from one exact verified package."""

    try:
        package = validate_game_package_document(dict(package_manifest))
    except (GamePackageError, TypeError, ValueError) as exc:
        raise GamePackageExtractionEvidenceError("game package manifest is invalid") from exc
    digest = _sha256(archive_sha256, field="archive_sha256")
    if (
        isinstance(archive_size_bytes, bool)
        or not isinstance(archive_size_bytes, int)
        or not 1 <= archive_size_bytes <= MAX_GAME_PACKAGE_ARCHIVE_BYTES
    ):
        _fail("archive_size_bytes is outside its fixed bound")
    extraction_id = (
        "game_package_extraction_"
        + canonical_payload_hash(
            {
                "package": _package_projection(
                    package,
                    archive_sha256=digest,
                    archive_size_bytes=archive_size_bytes,
                )
            }
        )[:40]
    )
    document: dict[str, Any] = {
        "format": GAME_PACKAGE_EXTRACTION_FORMAT,
        "format_version": GAME_PACKAGE_EXTRACTION_VERSION,
        "extraction_id": extraction_id,
        "package": _package_projection(
            package,
            archive_sha256=digest,
            archive_size_bytes=archive_size_bytes,
        ),
        "standalone_game": copy.deepcopy(package["standalone_game"]),
        "payload_lock": copy.deepcopy(package["payload_lock"]),
        "lineage": copy.deepcopy(package["lineage"]),
        "extracted_tree_hash": package["payload_lock"]["tree_hash"],
        "verification_status": "verified",
        "content_hash": "",
    }
    document["content_hash"] = canonical_payload_hash(document)
    return validate_game_package_extraction_evidence(
        document,
        package_manifest=package,
        archive_sha256=digest,
        archive_size_bytes=archive_size_bytes,
    )


def validate_game_package_extraction_evidence(
    value: object,
    *,
    package_manifest: Mapping[str, Any] | None = None,
    archive_sha256: str | None = None,
    archive_size_bytes: int | None = None,
) -> dict[str, Any]:
    """Validate one closed v1 extraction-evidence document."""

    if not isinstance(value, dict) or set(value) != _FIELDS:
        _fail("game package extraction evidence has invalid fields")
    if value["format"] != GAME_PACKAGE_EXTRACTION_FORMAT:
        _fail("game package extraction evidence format is unsupported")
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail("game package extraction evidence format_version must be 1")
    _identifier(value["extraction_id"], field="extraction_id")
    package = value["package"]
    if not isinstance(package, dict) or set(package) != _PACKAGE_FIELDS:
        _fail("game package extraction evidence package is invalid")
    if (
        package["format"] != "world-forge.game_package"
        or type(package["format_version"]) is not int
        or package["format_version"] != 1
    ):
        _fail("game package extraction evidence package format is unsupported")
    if not isinstance(package["id"], str) or _PACKAGE_ID_RE.fullmatch(package["id"]) is None:
        _fail("package/id is not a canonical game package identifier")
    _sha256(package["content_hash"], field="package/content_hash")
    _sha256(package["archive_sha256"], field="package/archive_sha256")
    size = package["size_bytes"]
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not 1 <= size <= MAX_GAME_PACKAGE_ARCHIVE_BYTES
    ):
        _fail("game package extraction evidence package size is invalid")
    standalone = value["standalone_game"]
    lock = value["payload_lock"]
    lineage = value["lineage"]
    if (
        not isinstance(standalone, dict)
        or not isinstance(lock, dict)
        or not isinstance(lineage, dict)
    ):
        _fail("game package extraction evidence lineage is invalid")
    if set(standalone) != _STANDALONE_FIELDS:
        _fail("game package extraction evidence standalone identity is invalid")
    if (
        standalone["format"] != "world-forge.standalone_game"
        or type(standalone["format_version"]) is not int
        or standalone["format_version"] != 1
    ):
        _fail("game package extraction evidence standalone format is unsupported")
    _identifier(standalone["game_id"], field="standalone_game/game_id")
    _sha256(standalone["content_hash"], field="standalone_game/content_hash")
    if set(lock) != _LOCK_FIELDS:
        _fail("game package extraction evidence payload lock identity is invalid")
    if (
        lock["format"] != "world-forge.standalone_game_lock"
        or type(lock["format_version"]) is not int
        or lock["format_version"] != 1
    ):
        _fail("game package extraction evidence payload lock format is unsupported")
    _identifier(lock["id"], field="payload_lock/id")
    _sha256(lock["content_hash"], field="payload_lock/content_hash")
    _sha256(lock["tree_hash"], field="payload_lock/tree_hash")
    if set(lineage) != _LINEAGE_FIELDS:
        _fail("game package extraction evidence lineage fields are invalid")
    for field in sorted(_LINEAGE_FIELDS):
        _sha256(lineage[field], field=f"lineage/{field}")
    _sha256(value["extracted_tree_hash"], field="extracted_tree_hash")
    if value["verification_status"] != "verified":
        _fail("game package extraction evidence verification_status must be verified")
    _sha256(value["content_hash"], field="content_hash")

    expected_package: dict[str, Any] | None = None
    if package_manifest is not None:
        try:
            expected_package = validate_game_package_document(dict(package_manifest))
        except (GamePackageError, TypeError, ValueError) as exc:
            raise GamePackageExtractionEvidenceError(
                "expected game package manifest is invalid"
            ) from exc
    if expected_package is not None:
        expected_archive_sha256 = (
            package["archive_sha256"]
            if archive_sha256 is None
            else _sha256(archive_sha256, field="archive_sha256")
        )
        expected_archive_size = (
            package["size_bytes"] if archive_size_bytes is None else archive_size_bytes
        )
        if (
            isinstance(expected_archive_size, bool)
            or not isinstance(expected_archive_size, int)
            or not 1 <= expected_archive_size <= MAX_GAME_PACKAGE_ARCHIVE_BYTES
        ):
            _fail("archive_size_bytes is outside its fixed bound")
        if package != _package_projection(
            expected_package,
            archive_sha256=expected_archive_sha256,
            archive_size_bytes=expected_archive_size,
        ):
            _fail("game package extraction evidence crosses its exact package")
        if (
            standalone != expected_package["standalone_game"]
            or lock != expected_package["payload_lock"]
            or lineage != expected_package["lineage"]
        ):
            _fail("game package extraction evidence crosses package lineage")
    if value["extracted_tree_hash"] != lock.get("tree_hash"):
        _fail("game package extraction evidence tree hash crosses its payload lock")
    expected_id = "game_package_extraction_" + canonical_payload_hash({"package": package})[:40]
    if value["extraction_id"] != expected_id:
        _fail("game package extraction evidence ID is not canonical")
    if canonical_payload_hash(value) != value["content_hash"]:
        _fail("game package extraction evidence content_hash is not canonical")
    return copy.deepcopy(value)


__all__ = [
    "GAME_PACKAGE_EXTRACTION_FORMAT",
    "GAME_PACKAGE_EXTRACTION_VERSION",
    "GamePackageExtractionEvidenceError",
    "build_game_package_extraction_evidence",
    "validate_game_package_extraction_evidence",
]
