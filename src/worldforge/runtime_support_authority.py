"""Trusted companion authority for release-gating generic runtime support.

The published runtime evidence and support-report v1 documents remain structural
claims.  This module is the code-owned authority boundary that may derive those
documents after exact retained artifacts have been reverified.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

from gamepack_runtime.game_package import (
    GamePackageError,
    VerifiedGamePackage,
    verify_game_package_bytes,
)
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.creation_contracts import (
    CreationContractError,
    _exact_keys,
    _identifier,
    _object,
    _sha256,
    _validate_json_structure,
    canonical_creation_hash,
    read_creation_object,
)
from worldforge.game_package_extraction import (
    GAME_PACKAGE_EXTRACTION_FORMAT,
    GamePackageExtractionEvidenceError,
    validate_game_package_extraction_evidence,
)
from worldforge.gamepack import GamepackError, validate_gamepack_document
from worldforge.generic_asset_authority import (
    ASSET_RELEASE_AUTHORITY_FORMAT,
    GenericAssetAuthorityError,
    VerifiedAssetReleaseAuthority,
    validate_asset_release_authority_document,
)
from worldforge.generic_assetpack import (
    GENERIC_ASSETPACK_FORMAT,
    GenericAssetpackError,
    VerifiedGenericAssetpack,
    verify_generic_assetpack,
)
from worldforge.generic_assets import GenericAssetError, validate_asset_inventory_document
from worldforge.generic_headless import (
    HEADLESS_EVIDENCE_SET_FORMAT,
    GenericHeadlessError,
    VerifiedHeadlessEvidenceSet,
    verify_headless_evidence_set,
)
from worldforge.generic_runtime import (
    RUNTIME_ADAPTER_FORMAT,
    RUNTIME_ADAPTER_REGISTRY_FORMAT,
    RUNTIME_COMPOSITION_FORMAT,
    RUNTIME_EVIDENCE_FORMAT,
    RUNTIME_SNAPSHOT_FORMAT,
    RUNTIME_SUPPORT_REPORT_FORMAT,
    RuntimeContractError,
    build_runtime_evidence,
    build_runtime_support_report,
    resolve_runtime_adapter,
    serialize_runtime_evidence,
    serialize_runtime_support_report,
    validate_game_runtime_composition,
    validate_runtime_adapter_registry_document,
    validate_runtime_evidence_document,
    validate_runtime_snapshot_document,
    validate_runtime_support_report_document,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.standalone_game import (
    STANDALONE_GAME_FORMAT,
    STANDALONE_GAME_LOCK_FORMAT,
    StandaloneGameError,
    VerifiedStandaloneGame,
    verify_standalone_game,
)

RUNTIME_SUPPORT_AUTHORITY_FORMAT = "world-forge.runtime_support_authority"
RUNTIME_SUPPORT_AUTHORITY_VERSION = 1
MAX_RUNTIME_SUPPORT_AUTHORITY_BYTES = 16 * 1024 * 1024
RUNTIME_SUPPORT_AUTHORITY_NATIVE_UNAVAILABLE = "runtime_support_authority_native_unavailable"

_AUTHORITY_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "authority_id",
        "gamepack",
        "asset_inventory",
        "composition",
        "assetpack",
        "asset_release_authority",
        "adapter",
        "registry",
        "runtime_snapshot",
        "headless_evidence",
        "package_evidence",
        "runtime_evidence",
        "runtime_support_report",
        "native_status",
        "release_status",
        "supported",
        "reason_codes",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_ASSETPACK_FIELDS = frozenset(
    {"format", "format_version", "id", "content_hash", "root_hash", "inventory_hash"}
)
_PLATFORM_FIELDS = frozenset(
    {"platform_id", "platform_family", "architecture", "backend", "renderer"}
)
_HEADLESS_FIELDS = frozenset(
    {
        "platform",
        "evidence_set",
        "runtime_bundle",
        "execution_script",
        "headless_receipt",
        "runtime_evidence",
    }
)
_RUNTIME_EVIDENCE_REFERENCE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "content_hash",
        "platform",
        "execution_status",
        "packaging_status",
    }
)
_PACKAGE_FIELDS = frozenset(
    {
        "package",
        "extraction",
        "standalone_game",
        "payload_lock",
        "runtime_bundle_hash",
    }
)
_PACKAGE_IDENTITY_FIELDS = frozenset(
    {"format", "format_version", "id", "content_hash", "archive_sha256", "size_bytes"}
)
_STANDALONE_IDENTITY_FIELDS = frozenset({"format", "format_version", "game_id", "content_hash"})
_PAYLOAD_LOCK_FIELDS = frozenset({"format", "format_version", "id", "content_hash", "tree_hash"})
_SUPPORT_REFERENCE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "content_hash",
        "compatibility_status",
        "packaging_status",
        "release_status",
        "supported",
    }
)
_VERIFIED_AUTHORITY_TOKEN = object()
_AUTHORITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,127}$")


class RuntimeSupportAuthorityError(ValueError):
    """Raised when release-gating runtime authority cannot be proven exactly."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise RuntimeSupportAuthorityError(reason_code, detail)


def _hash(value: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(value)
    except CreationContractError as exc:
        _fail("runtime_support_authority_invalid", str(exc))


def _seal(document: dict[str, Any]) -> dict[str, Any]:
    document["content_hash"] = _hash(document)
    return document


def _identity(
    document: Mapping[str, object],
    *,
    id_field: str,
) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


def _authority_identifier(value: object, context: str) -> str:
    if not isinstance(value, str) or _AUTHORITY_ID_RE.fullmatch(value) is None:
        _fail(
            "runtime_support_authority_binding_mismatch",
            f"{context} must be a lowercase authority identifier",
        )
    return value


def _gamepack_identity(document: Mapping[str, Any]) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document["game"]["id"],
        "content_hash": document["content_hash"],
    }


def _validate_identity(
    value: object,
    context: str,
    *,
    expected_format: str,
) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    if identity.get("format") != expected_format or identity.get("format_version") != 1:
        _fail(
            "runtime_support_authority_binding_mismatch",
            f"{context} is not an exact {expected_format} v1 identity",
        )
    _authority_identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return copy.deepcopy(identity)


def _validate_assetpack_identity(value: object, context: str) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _ASSETPACK_FIELDS, context)
    if identity.get("format") != GENERIC_ASSETPACK_FORMAT or identity.get("format_version") != 1:
        _fail(
            "runtime_support_authority_binding_mismatch",
            f"{context} is not an exact generic assetpack v1 identity",
        )
    _authority_identifier(identity.get("id"), f"{context}.id")
    for field in ("content_hash", "root_hash", "inventory_hash"):
        _sha256(identity.get(field), f"{context}.{field}")
    return copy.deepcopy(identity)


def _validate_platform(value: object, context: str) -> dict[str, Any]:
    platform = _object(value, context)
    _exact_keys(platform, _PLATFORM_FIELDS, context)
    expected = {
        "platform:linux_x86_64": (
            "platform:linux",
            "architecture:x86_64",
            "backend:raylib",
            "raylib",
        ),
        "platform:windows_x86_64": (
            "platform:windows",
            "architecture:x86_64",
            "backend:raylib",
            "raylib",
        ),
    }
    platform_id = platform.get("platform_id")
    if (
        platform_id not in expected
        or tuple(
            platform.get(field)
            for field in ("platform_family", "architecture", "backend", "renderer")
        )
        != expected[platform_id]
    ):
        _fail(
            "runtime_support_authority_platform_mismatch",
            f"{context} is outside the closed runtime platform set",
        )
    return copy.deepcopy(platform)


def _authority_seed(document: Mapping[str, object]) -> dict[str, object]:
    return {
        key: document[key]
        for key in sorted(document, key=lambda item: item.encode("utf-8"))
        if key not in {"authority_id", "content_hash"}
    }


def _derived_authority_id(document: Mapping[str, object]) -> str:
    return "runtime_authority_" + _hash(_authority_seed(document))[:40]


def _runtime_evidence_reference(evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": evidence["format"],
        "format_version": evidence["format_version"],
        "id": evidence["evidence_id"],
        "content_hash": evidence["content_hash"],
        "platform": copy.deepcopy(evidence["platform"]),
        "execution_status": evidence["execution_status"],
        "packaging_status": evidence["packaging_status"],
    }


def _support_reference(support: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "format": support["format"],
        "format_version": support["format_version"],
        "id": support["report_id"],
        "content_hash": support["content_hash"],
        "compatibility_status": support["compatibility_status"],
        "packaging_status": support["dimensions"]["packaging"],
        "release_status": support["dimensions"]["release"],
        "supported": support["supported"],
    }


def _headless_reference(record: Mapping[str, Any]) -> dict[str, Any]:
    manifest = record["manifest"]
    runtime_evidence = record["runtime_evidence"]
    return {
        "platform": copy.deepcopy(runtime_evidence["platform"]),
        "evidence_set": _identity(manifest, id_field="evidence_set_id"),
        "runtime_bundle": copy.deepcopy(manifest["runtime_bundle"]),
        "execution_script": copy.deepcopy(manifest["execution_script"]),
        "headless_receipt": copy.deepcopy(manifest["headless_receipt"]),
        "runtime_evidence": _runtime_evidence_reference(runtime_evidence),
    }


def _package_reference(record: Mapping[str, Any]) -> dict[str, Any]:
    package = record["package"]
    extraction = record["extraction"]
    return {
        "package": {
            "format": package["format"],
            "format_version": package["format_version"],
            "id": package["package_id"],
            "content_hash": package["content_hash"],
            "archive_sha256": record["archive_sha256"],
            "size_bytes": record["archive_size_bytes"],
        },
        "extraction": _identity(extraction, id_field="extraction_id"),
        "standalone_game": copy.deepcopy(extraction["standalone_game"]),
        "payload_lock": copy.deepcopy(extraction["payload_lock"]),
        "runtime_bundle_hash": extraction["lineage"]["runtime_bundle_hash"],
    }


class VerifiedRuntimeSupportAuthority:
    """Opaque immutable authority derived only from exact verified handles."""

    __slots__ = (
        "_document",
        "_proof",
        "_runtime_evidence",
        "_state",
        "_support_report",
    )

    def __init__(
        self,
        token: object,
        *,
        document: Mapping[str, Any] | None = None,
        runtime_evidence: Sequence[Mapping[str, Any]] | None = None,
        support_report: Mapping[str, Any] | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> None:
        if (
            token is not _VERIFIED_AUTHORITY_TOKEN
            or document is None
            or runtime_evidence is None
            or support_report is None
            or state is None
        ):
            raise TypeError(
                "VerifiedRuntimeSupportAuthority is created only by trusted authority functions"
            )
        self._document = copy.deepcopy(dict(document))
        self._runtime_evidence = tuple(copy.deepcopy(dict(item)) for item in runtime_evidence)
        self._support_report = copy.deepcopy(dict(support_report))
        self._state = copy.deepcopy(dict(state))
        self._proof = token

    @property
    def document(self) -> dict[str, Any]:
        return copy.deepcopy(self._document)

    @property
    def runtime_evidence(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(MappingProxyType(copy.deepcopy(item)) for item in self._runtime_evidence)

    @property
    def support_report(self) -> Mapping[str, Any]:
        return MappingProxyType(copy.deepcopy(self._support_report))


def _require_authority(value: object) -> VerifiedRuntimeSupportAuthority:
    if (
        type(value) is not VerifiedRuntimeSupportAuthority
        or value._proof is not _VERIFIED_AUTHORITY_TOKEN
    ):
        _fail(
            "runtime_support_authority_required",
            "an exact verified runtime support authority handle is required",
        )
    return value


def _verify_assetpack_handle(value: object) -> tuple[dict[str, Any], dict[str, bytes], Path]:
    if type(value) is not VerifiedGenericAssetpack:
        _fail(
            "runtime_support_authority_assetpack_required",
            "an exact verified generic assetpack handle is required",
        )
    try:
        expected_manifest = value.manifest
        expected_files = dict(value.files)
        expected_root = value.root
        expected_identity = value.root_identity
        fresh = verify_generic_assetpack(
            expected_root,
            expected_content_hash=expected_manifest["content_hash"],
        )
        try:
            if (
                fresh.root != expected_root
                or fresh.root_identity != expected_identity
                or fresh.manifest != expected_manifest
                or dict(fresh.files) != expected_files
            ):
                _fail(
                    "runtime_support_authority_assetpack_mismatch",
                    "verified assetpack differs from its exact retained tree",
                )
        finally:
            fresh.close()
    except RuntimeSupportAuthorityError:
        raise
    except (GenericAssetpackError, OSError, TypeError, ValueError) as exc:
        _fail("runtime_support_authority_assetpack_mismatch", str(exc))
    return expected_manifest, expected_files, expected_root


def _verify_release_authority_handle(
    value: object,
    *,
    assetpack: Mapping[str, Any],
) -> dict[str, Any]:
    if type(value) is not VerifiedAssetReleaseAuthority:
        _fail(
            "runtime_support_authority_asset_release_required",
            "an exact verified asset release-authority handle is required",
        )
    try:
        release = validate_asset_release_authority_document(value.document)
    except (GenericAssetAuthorityError, TypeError, ValueError) as exc:
        _fail("runtime_support_authority_asset_release_mismatch", str(exc))
    if not value.authorized or release["status"] != "authorized":
        _fail(
            "runtime_support_authority_asset_release_mismatch",
            "runtime authority requires an authorized asset release",
        )
    if release["candidate_assetpack"] != _identity(assetpack, id_field="assetpack_id"):
        _fail(
            "runtime_support_authority_asset_release_mismatch",
            "asset release authority binds another assetpack candidate",
        )
    return release


def _build_handle(state: Mapping[str, Any]) -> VerifiedRuntimeSupportAuthority:
    owned = copy.deepcopy(dict(state))
    composition = owned["composition"]
    package = owned.get("package")
    runtime_evidence: list[dict[str, Any]] = []
    for record in sorted(
        owned["headless"],
        key=lambda item: item["runtime_evidence"]["platform"]["platform_id"].encode("utf-8"),
    ):
        source = record["runtime_evidence"]
        checks = copy.deepcopy(source["checks"])
        packaging_status = "unverified"
        if package is not None:
            platform_id = source["platform"]["platform_id"]
            check_seed = {
                "extraction": package["extraction"]["content_hash"],
                "platform_id": platform_id,
            }
            checks.append(
                {
                    "check_id": "check:package_verification",
                    "kind": "packaging",
                    "status": "passed",
                    "evidence_id": "package_check_" + _hash(check_seed)[:40],
                    "content_hash": package["extraction"]["content_hash"],
                }
            )
            packaging_status = "verified"
        runtime_evidence.append(
            build_runtime_evidence(
                composition,
                platform_id=source["platform"]["platform_id"],
                execution_status="headless_verified",
                packaging_status=packaging_status,
                checks=checks,
            )
        )

    support = build_runtime_support_report(
        composition,
        gamepack=owned["gamepack"],
        registry=owned["registry"],
        snapshot=owned["snapshot"],
        evidence=runtime_evidence,
    )
    if support["supported"] or support["dimensions"]["release"] != "blocked":
        _fail(
            "runtime_support_authority_overclaim",
            "runtime support authority v1 cannot satisfy native release evidence",
        )
    reason_codes = sorted(
        {RUNTIME_SUPPORT_AUTHORITY_NATIVE_UNAVAILABLE, *support["reason_codes"]},
        key=lambda item: item.encode("utf-8"),
    )
    document: dict[str, Any] = {
        "format": RUNTIME_SUPPORT_AUTHORITY_FORMAT,
        "format_version": RUNTIME_SUPPORT_AUTHORITY_VERSION,
        "authority_id": "",
        "gamepack": _gamepack_identity(owned["gamepack"]),
        "asset_inventory": _identity(owned["inventory"], id_field="inventory_id"),
        "composition": _identity(composition, id_field="composition_id"),
        "assetpack": copy.deepcopy(composition["assetpack"]),
        "asset_release_authority": _identity(
            owned["asset_release_authority"],
            id_field="release_authority_id",
        ),
        "adapter": copy.deepcopy(composition["adapter"]),
        "registry": copy.deepcopy(composition["registry"]),
        "runtime_snapshot": copy.deepcopy(composition["runtime_snapshot"]),
        "headless_evidence": [_headless_reference(item) for item in owned["headless"]],
        "package_evidence": None if package is None else _package_reference(package),
        "runtime_evidence": [_runtime_evidence_reference(item) for item in runtime_evidence],
        "runtime_support_report": _support_reference(support),
        "native_status": "unavailable",
        "release_status": "blocked",
        "supported": False,
        "reason_codes": reason_codes,
        "content_hash": "",
    }
    document["headless_evidence"].sort(
        key=lambda item: item["platform"]["platform_id"].encode("utf-8")
    )
    document["authority_id"] = _derived_authority_id(document)
    checked = validate_runtime_support_authority_document(_seal(document))
    return VerifiedRuntimeSupportAuthority(
        _VERIFIED_AUTHORITY_TOKEN,
        document=checked,
        runtime_evidence=runtime_evidence,
        support_report=support,
        state=owned,
    )


def initialize_runtime_support_authority(
    *,
    gamepack: object,
    inventory: object,
    composition: object,
    registry: object,
    snapshot: object,
    verified_assetpack: VerifiedGenericAssetpack,
    asset_release_authority: VerifiedAssetReleaseAuthority,
) -> VerifiedRuntimeSupportAuthority:
    """Create initial blocked authority from exact logic/runtime/asset handles."""

    try:
        checked_gamepack = validate_gamepack_document(copy.deepcopy(gamepack))
        checked_inventory = validate_asset_inventory_document(copy.deepcopy(inventory))
        checked_snapshot = validate_runtime_snapshot_document(copy.deepcopy(snapshot))
        checked_registry = validate_runtime_adapter_registry_document(
            copy.deepcopy(registry),
            snapshot=checked_snapshot,
        )
        assetpack, _files, assetpack_root = _verify_assetpack_handle(verified_assetpack)
        checked_composition = validate_game_runtime_composition(
            copy.deepcopy(composition),
            gamepack=checked_gamepack,
            inventory=checked_inventory,
            assetpack_root=assetpack_root,
            registry=checked_registry,
            snapshot=checked_snapshot,
        )
        adapter = resolve_runtime_adapter(
            checked_gamepack,
            registry=checked_registry,
            snapshot=checked_snapshot,
        )
        if checked_composition["adapter"] != _identity(adapter, id_field="adapter_id"):
            _fail(
                "runtime_support_authority_binding_mismatch",
                "composition does not bind the exact resolved adapter",
            )
        if checked_composition["assetpack"]["content_hash"] != assetpack["content_hash"]:
            _fail(
                "runtime_support_authority_binding_mismatch",
                "composition does not bind the exact verified assetpack",
            )
        release = _verify_release_authority_handle(
            asset_release_authority,
            assetpack=assetpack,
        )
    except RuntimeSupportAuthorityError:
        raise
    except (
        GamepackError,
        GenericAssetError,
        GenericAssetpackError,
        RuntimeContractError,
        CreationContractError,
        TypeError,
        ValueError,
    ) as exc:
        _fail("runtime_support_authority_binding_mismatch", str(exc))
    return _build_handle(
        {
            "gamepack": checked_gamepack,
            "inventory": checked_inventory,
            "composition": checked_composition,
            "registry": checked_registry,
            "snapshot": checked_snapshot,
            "assetpack": assetpack,
            "asset_release_authority": release,
            "headless": [],
            "package": None,
        }
    )


def _decode_verified_file(files: Mapping[str, bytes], relative: str) -> dict[str, Any]:
    try:
        return decode_json_object(files[relative], source=relative)
    except (KeyError, RuntimeIOError) as exc:
        _fail("runtime_support_authority_headless_mismatch", f"{relative}: {exc}")


def attach_verified_headless_evidence(
    authority: VerifiedRuntimeSupportAuthority,
    verified: VerifiedHeadlessEvidenceSet,
    *,
    bundle_root: str | Path,
) -> VerifiedRuntimeSupportAuthority:
    """Attach one exact re-executed headless/save/replay evidence set."""

    checked_authority = _require_authority(authority)
    if type(verified) is not VerifiedHeadlessEvidenceSet:
        _fail(
            "runtime_support_authority_headless_required",
            "headless evidence must be an exact verified evidence-set handle",
        )
    try:
        expected_manifest = verified.manifest
        expected_files = dict(verified.files)
        expected_root = verified.root
        expected_identity = verified.root_identity
        fresh = verify_headless_evidence_set(
            expected_root,
            bundle_root=bundle_root,
            expected_content_hash=expected_manifest["content_hash"],
        )
        try:
            if (
                fresh.root != expected_root
                or fresh.root_identity != expected_identity
                or fresh.manifest != expected_manifest
                or dict(fresh.files) != expected_files
            ):
                _fail(
                    "runtime_support_authority_headless_mismatch",
                    "headless handle differs from exact integral re-verification",
                )
            manifest = fresh.manifest
            files = dict(fresh.files)
        finally:
            fresh.close()
        state = copy.deepcopy(checked_authority._state)
        runtime_evidence = validate_runtime_evidence_document(
            _decode_verified_file(files, "runtime/evidence.json"),
            composition=state["composition"],
        )
        embedded_support = validate_runtime_support_report_document(
            _decode_verified_file(files, "runtime/support-report.json")
        )
        expected_support = build_runtime_support_report(
            state["composition"],
            gamepack=state["gamepack"],
            registry=state["registry"],
            snapshot=state["snapshot"],
            evidence=[runtime_evidence],
        )
        if embedded_support != expected_support:
            _fail(
                "runtime_support_authority_headless_mismatch",
                "headless support report differs from exact re-executed evidence",
            )
        passed = {
            check["kind"] for check in runtime_evidence["checks"] if check["status"] == "passed"
        }
        if (
            runtime_evidence["execution_status"] != "headless_verified"
            or runtime_evidence["packaging_status"] != "unverified"
            or passed != {"headless", "save_replay"}
            or any(check["kind"] in {"native", "packaging"} for check in runtime_evidence["checks"])
            or embedded_support["supported"]
            or embedded_support["dimensions"]["release"] != "blocked"
        ):
            _fail(
                "runtime_support_authority_headless_overclaim",
                "headless evidence contains an unverified positive claim",
            )
        if runtime_evidence["composition"] != checked_authority.document["composition"]:
            _fail(
                "runtime_support_authority_headless_mismatch",
                "headless evidence crosses the authority composition",
            )
        if runtime_evidence["adapter"] != checked_authority.document["adapter"]:
            _fail(
                "runtime_support_authority_headless_mismatch",
                "headless evidence crosses the authority adapter",
            )
        if state["headless"] and any(
            item["manifest"]["runtime_bundle"] != manifest["runtime_bundle"]
            for item in state["headless"]
        ):
            _fail(
                "runtime_support_authority_headless_mismatch",
                "headless evidence crosses the retained runtime bundle",
            )
        platform_id = runtime_evidence["platform"]["platform_id"]
        if any(
            item["runtime_evidence"]["platform"]["platform_id"] == platform_id
            for item in state["headless"]
        ):
            _fail(
                "runtime_support_authority_platform_collision",
                f"headless evidence already exists for {platform_id}",
            )
        state["headless"].append(
            {
                "manifest": manifest,
                "files": files,
                "runtime_evidence": runtime_evidence,
            }
        )
        return _build_handle(state)
    except RuntimeSupportAuthorityError:
        raise
    except (GenericHeadlessError, RuntimeContractError, OSError, TypeError, ValueError) as exc:
        _fail("runtime_support_authority_headless_mismatch", str(exc))


def _verify_package_handle(value: object) -> dict[str, Any]:
    if type(value) is not VerifiedGamePackage:
        _fail(
            "runtime_support_authority_package_required",
            "an exact verified game-package handle is required",
        )
    try:
        archive = value.archive_bytes
        expected_manifest = value.manifest
        expected_files = dict(value.files)
        fresh = verify_game_package_bytes(archive)
        if (
            fresh.archive_bytes != archive
            or fresh.archive_sha256 != value.archive_sha256
            or fresh.manifest != expected_manifest
            or dict(fresh.files) != expected_files
        ):
            _fail(
                "runtime_support_authority_package_mismatch",
                "game package differs from exact byte verification",
            )
    except RuntimeSupportAuthorityError:
        raise
    except (GamePackageError, TypeError, ValueError) as exc:
        _fail("runtime_support_authority_package_mismatch", str(exc))
    return {
        "manifest": expected_manifest,
        "files": expected_files,
        "archive_bytes": archive,
        "archive_sha256": value.archive_sha256,
    }


def _verify_standalone_handle(value: object) -> dict[str, Any]:
    if type(value) is not VerifiedStandaloneGame:
        _fail(
            "runtime_support_authority_standalone_required",
            "an exact verified extracted-standalone handle is required",
        )
    try:
        manifest = value.manifest
        lock = value.lock
        files = dict(value.files)
        root = value.root
        root_identity = value.root_identity
        fresh = verify_standalone_game(
            root,
            expected_content_hash=manifest["content_hash"],
            expected_root_identity=root_identity,
        )
        try:
            if (
                fresh.root != root
                or fresh.root_identity != root_identity
                or fresh.manifest != manifest
                or fresh.lock != lock
                or dict(fresh.files) != files
            ):
                _fail(
                    "runtime_support_authority_package_lineage_mismatch",
                    "standalone handle differs from exact tree re-verification",
                )
        finally:
            fresh.close()
    except RuntimeSupportAuthorityError:
        raise
    except (StandaloneGameError, OSError, TypeError, ValueError) as exc:
        _fail("runtime_support_authority_package_lineage_mismatch", str(exc))
    return {"manifest": manifest, "lock": lock, "files": files}


def attach_verified_game_package(
    authority: VerifiedRuntimeSupportAuthority,
    package: VerifiedGamePackage,
    *,
    extracted_standalone: VerifiedStandaloneGame,
    extraction_evidence: object,
) -> VerifiedRuntimeSupportAuthority:
    """Attach exact package and extracted-tree evidence without granting native status."""

    checked_authority = _require_authority(authority)
    state = copy.deepcopy(checked_authority._state)
    if not state["headless"]:
        _fail(
            "runtime_support_authority_headless_required",
            "packaging cannot be release evidence before exact headless/save/replay evidence",
        )
    if state.get("package") is not None:
        _fail(
            "runtime_support_authority_package_collision",
            "runtime support authority already contains package evidence",
        )
    checked_package = _verify_package_handle(package)
    checked_standalone = _verify_standalone_handle(extracted_standalone)
    try:
        extraction = validate_game_package_extraction_evidence(
            copy.deepcopy(extraction_evidence),
            package_manifest=checked_package["manifest"],
            archive_sha256=checked_package["archive_sha256"],
            archive_size_bytes=len(checked_package["archive_bytes"]),
        )
    except (GamePackageExtractionEvidenceError, TypeError, ValueError) as exc:
        _fail("runtime_support_authority_package_lineage_mismatch", str(exc))

    package_manifest = checked_package["manifest"]
    standalone_manifest = checked_standalone["manifest"]
    standalone_lock = checked_standalone["lock"]
    if checked_package["files"] != checked_standalone["files"]:
        _fail(
            "runtime_support_authority_package_lineage_mismatch",
            "package payload differs from the exact extracted standalone tree",
        )
    if (
        extraction["standalone_game"] != package_manifest["standalone_game"]
        or extraction["payload_lock"] != package_manifest["payload_lock"]
        or package_manifest["standalone_game"]
        != {
            "format": standalone_manifest["format"],
            "format_version": standalone_manifest["format_version"],
            "game_id": standalone_manifest["game_id"],
            "content_hash": standalone_manifest["content_hash"],
        }
        or package_manifest["payload_lock"]
        != {
            "format": standalone_lock["format"],
            "format_version": standalone_lock["format_version"],
            "id": standalone_lock["lock_id"],
            "content_hash": standalone_lock["content_hash"],
            "tree_hash": standalone_lock["tree_hash"],
        }
    ):
        _fail(
            "runtime_support_authority_package_lineage_mismatch",
            "extraction, package, and standalone identities differ",
        )
    expected_lineage = {
        "gamepack_hash": state["gamepack"]["content_hash"],
        "assetpack_hash": state["assetpack"]["content_hash"],
        "runtime_snapshot_hash": state["snapshot"]["content_hash"],
        "runtime_composition_hash": state["composition"]["content_hash"],
    }
    for field, expected in expected_lineage.items():
        if package_manifest["lineage"].get(field) != expected:
            _fail(
                "runtime_support_authority_package_lineage_mismatch",
                f"package {field} crosses the retained authority",
            )
    runtime_bundle_hashes = {
        item["manifest"]["runtime_bundle"]["content_hash"] for item in state["headless"]
    }
    if runtime_bundle_hashes != {package_manifest["lineage"]["runtime_bundle_hash"]}:
        _fail(
            "runtime_support_authority_package_lineage_mismatch",
            "package runtime bundle crosses exact headless evidence",
        )
    if extraction["lineage"] != package_manifest["lineage"]:
        _fail(
            "runtime_support_authority_package_lineage_mismatch",
            "extraction evidence crosses package lineage",
        )
    state["package"] = {
        "package": package_manifest,
        "archive_sha256": checked_package["archive_sha256"],
        "archive_size_bytes": len(checked_package["archive_bytes"]),
        "extraction": extraction,
        "standalone": standalone_manifest,
        "payload_lock": standalone_lock,
    }
    return _build_handle(state)


def attach_native_evidence(*_args: object, **_kwargs: object) -> VerifiedRuntimeSupportAuthority:
    """Fail closed: runtime support authority v1 has no native receipt authority."""

    _fail(
        RUNTIME_SUPPORT_AUTHORITY_NATIVE_UNAVAILABLE,
        "runtime support authority v1 cannot verify or attach native execution evidence",
    )


def derive_runtime_evidence(
    authority: VerifiedRuntimeSupportAuthority,
) -> list[dict[str, Any]]:
    """Return release-gating v1 evidence only from an opaque verified authority."""

    checked = _require_authority(authority)
    evidence = [copy.deepcopy(item) for item in checked._runtime_evidence]
    for item in evidence:
        validate_runtime_evidence_document(item, composition=checked._state["composition"])
        if serialize_runtime_evidence(item) != canonical_json_bytes(item):
            _fail(
                "runtime_support_authority_evidence_mismatch",
                "derived runtime evidence bytes are noncanonical",
            )
    return evidence


def derive_runtime_support_report(
    authority: VerifiedRuntimeSupportAuthority,
) -> dict[str, Any]:
    """Return a release-gating v1 support report only from verified authority."""

    checked = _require_authority(authority)
    report = copy.deepcopy(checked._support_report)
    if report["supported"] or report["dimensions"]["release"] != "blocked":
        _fail(
            "runtime_support_authority_overclaim",
            "runtime support authority v1 cannot derive a release-ready report",
        )
    validate_runtime_support_report_document(report)
    if serialize_runtime_support_report(report) != canonical_json_bytes(report):
        _fail(
            "runtime_support_authority_support_mismatch",
            "derived runtime support report bytes are noncanonical",
        )
    return report


def validate_runtime_support_authority_document(value: object) -> dict[str, Any]:
    """Validate structural v1 audit state without granting executable authority."""

    try:
        owned = copy.deepcopy(value)
        _validate_json_structure(owned, context="runtime support authority")
        document = _object(owned, "runtime support authority")
        _exact_keys(document, _AUTHORITY_FIELDS, "runtime support authority")
        if document.get("format") != RUNTIME_SUPPORT_AUTHORITY_FORMAT:
            _fail(
                "runtime_support_authority_format_invalid",
                f"format must be {RUNTIME_SUPPORT_AUTHORITY_FORMAT}",
            )
        if document.get("format_version") != RUNTIME_SUPPORT_AUTHORITY_VERSION:
            _fail("runtime_support_authority_version_invalid", "format_version must be 1")
        _sha256(document.get("content_hash"), "runtime support authority.content_hash")
        if document["content_hash"] != _hash(document):
            _fail(
                "runtime_support_authority_hash_mismatch",
                "content_hash is not canonical",
            )
        _identifier(document.get("authority_id"), "runtime support authority.authority_id")
        _validate_identity(
            document.get("gamepack"),
            "runtime support authority.gamepack",
            expected_format="world-forge.gamepack",
        )
        _validate_identity(
            document.get("asset_inventory"),
            "runtime support authority.asset_inventory",
            expected_format="world-forge.asset_inventory",
        )
        _validate_identity(
            document.get("composition"),
            "runtime support authority.composition",
            expected_format=RUNTIME_COMPOSITION_FORMAT,
        )
        _validate_assetpack_identity(
            document.get("assetpack"),
            "runtime support authority.assetpack",
        )
        _validate_identity(
            document.get("asset_release_authority"),
            "runtime support authority.asset_release_authority",
            expected_format=ASSET_RELEASE_AUTHORITY_FORMAT,
        )
        _validate_identity(
            document.get("adapter"),
            "runtime support authority.adapter",
            expected_format=RUNTIME_ADAPTER_FORMAT,
        )
        _validate_identity(
            document.get("registry"),
            "runtime support authority.registry",
            expected_format=RUNTIME_ADAPTER_REGISTRY_FORMAT,
        )
        _validate_identity(
            document.get("runtime_snapshot"),
            "runtime support authority.runtime_snapshot",
            expected_format=RUNTIME_SNAPSHOT_FORMAT,
        )

        raw_headless = document.get("headless_evidence")
        if not isinstance(raw_headless, list) or len(raw_headless) > 32:
            _fail(
                "runtime_support_authority_limit",
                "headless_evidence must be a bounded array",
            )
        headless_platforms: list[str] = []
        headless_evidence_ids: dict[str, str] = {}
        for index, raw in enumerate(raw_headless):
            context = f"runtime support authority.headless_evidence/{index}"
            record = _object(raw, context)
            _exact_keys(record, _HEADLESS_FIELDS, context)
            platform = _validate_platform(record.get("platform"), f"{context}.platform")
            platform_id = platform["platform_id"]
            headless_platforms.append(platform_id)
            _validate_identity(
                record.get("evidence_set"),
                f"{context}.evidence_set",
                expected_format=HEADLESS_EVIDENCE_SET_FORMAT,
            )
            _validate_identity(
                record.get("runtime_bundle"),
                f"{context}.runtime_bundle",
                expected_format="world-forge.game_runtime_bundle",
            )
            _validate_identity(
                record.get("execution_script"),
                f"{context}.execution_script",
                expected_format="world-forge.game_execution_script",
            )
            _validate_identity(
                record.get("headless_receipt"),
                f"{context}.headless_receipt",
                expected_format="world-forge.headless_execution_receipt",
            )
            evidence = _validate_runtime_evidence_reference(
                record.get("runtime_evidence"),
                f"{context}.runtime_evidence",
            )
            if (
                evidence["platform"] != platform
                or evidence["execution_status"] != "headless_verified"
            ):
                _fail(
                    "runtime_support_authority_headless_mismatch",
                    f"{context} crosses platform or overclaims execution",
                )
            headless_evidence_ids[platform_id] = evidence["id"]
        if headless_platforms != sorted(headless_platforms, key=lambda item: item.encode("utf-8")):
            _fail(
                "runtime_support_authority_noncanonical",
                "headless evidence is not sorted by platform",
            )
        if len(set(headless_platforms)) != len(headless_platforms):
            _fail(
                "runtime_support_authority_platform_collision",
                "headless evidence platforms collide",
            )

        raw_evidence = document.get("runtime_evidence")
        if not isinstance(raw_evidence, list) or len(raw_evidence) > 32:
            _fail(
                "runtime_support_authority_limit",
                "runtime_evidence must be a bounded array",
            )
        runtime_platforms: list[str] = []
        package_present = document.get("package_evidence") is not None
        for index, raw in enumerate(raw_evidence):
            evidence = _validate_runtime_evidence_reference(
                raw,
                f"runtime support authority.runtime_evidence/{index}",
            )
            platform_id = evidence["platform"]["platform_id"]
            runtime_platforms.append(platform_id)
            if headless_evidence_ids.get(platform_id) != evidence["id"] and not package_present:
                _fail(
                    "runtime_support_authority_evidence_mismatch",
                    "derived runtime evidence crosses exact headless evidence",
                )
            expected_packaging = "verified" if package_present else "unverified"
            if evidence["packaging_status"] != expected_packaging:
                _fail(
                    "runtime_support_authority_package_lineage_mismatch",
                    "runtime evidence packaging state contradicts package authority",
                )
        if runtime_platforms != headless_platforms:
            _fail(
                "runtime_support_authority_evidence_mismatch",
                "runtime evidence platforms do not equal exact headless platforms",
            )

        package_value = document.get("package_evidence")
        if package_value is not None:
            package_record = _object(package_value, "runtime support authority.package_evidence")
            _exact_keys(
                package_record,
                _PACKAGE_FIELDS,
                "runtime support authority.package_evidence",
            )
            package_identity = _object(
                package_record.get("package"),
                "runtime support authority.package_evidence.package",
            )
            _exact_keys(
                package_identity,
                _PACKAGE_IDENTITY_FIELDS,
                "runtime support authority.package_evidence.package",
            )
            if (
                package_identity.get("format") != "world-forge.game_package"
                or package_identity.get("format_version") != 1
            ):
                _fail(
                    "runtime_support_authority_package_lineage_mismatch",
                    "package evidence format is unsupported",
                )
            _authority_identifier(package_identity.get("id"), "package evidence.package.id")
            for field in ("content_hash", "archive_sha256"):
                _sha256(package_identity.get(field), f"package evidence.package.{field}")
            size = package_identity.get("size_bytes")
            if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                _fail(
                    "runtime_support_authority_package_lineage_mismatch",
                    "package evidence size is invalid",
                )
            _validate_identity(
                package_record.get("extraction"),
                "runtime support authority.package_evidence.extraction",
                expected_format=GAME_PACKAGE_EXTRACTION_FORMAT,
            )
            standalone = _object(
                package_record.get("standalone_game"),
                "runtime support authority.package_evidence.standalone_game",
            )
            _exact_keys(
                standalone,
                _STANDALONE_IDENTITY_FIELDS,
                "runtime support authority.package_evidence.standalone_game",
            )
            if (
                standalone.get("format") != STANDALONE_GAME_FORMAT
                or standalone.get("format_version") != 1
            ):
                _fail(
                    "runtime_support_authority_package_lineage_mismatch",
                    "standalone package identity is invalid",
                )
            _identifier(standalone.get("game_id"), "package evidence.standalone_game.game_id")
            _sha256(
                standalone.get("content_hash"),
                "package evidence.standalone_game.content_hash",
            )
            lock = _object(
                package_record.get("payload_lock"),
                "runtime support authority.package_evidence.payload_lock",
            )
            _exact_keys(
                lock,
                _PAYLOAD_LOCK_FIELDS,
                "runtime support authority.package_evidence.payload_lock",
            )
            if lock.get("format") != STANDALONE_GAME_LOCK_FORMAT or lock.get("format_version") != 1:
                _fail(
                    "runtime_support_authority_package_lineage_mismatch",
                    "payload-lock package identity is invalid",
                )
            _identifier(lock.get("id"), "package evidence.payload_lock.id")
            for field in ("content_hash", "tree_hash"):
                _sha256(lock.get(field), f"package evidence.payload_lock.{field}")
            _sha256(
                package_record.get("runtime_bundle_hash"),
                "package evidence.runtime_bundle_hash",
            )
            bundle_hashes = {record["runtime_bundle"]["content_hash"] for record in raw_headless}
            if bundle_hashes != {package_record["runtime_bundle_hash"]}:
                _fail(
                    "runtime_support_authority_package_lineage_mismatch",
                    "package runtime bundle differs from headless evidence",
                )

        support = _object(
            document.get("runtime_support_report"),
            "runtime support authority.runtime_support_report",
        )
        _exact_keys(
            support,
            _SUPPORT_REFERENCE_FIELDS,
            "runtime support authority.runtime_support_report",
        )
        if (
            support.get("format") != RUNTIME_SUPPORT_REPORT_FORMAT
            or support.get("format_version") != 1
        ):
            _fail(
                "runtime_support_authority_support_mismatch",
                "support reference format is unsupported",
            )
        _identifier(support.get("id"), "runtime support authority.runtime_support_report.id")
        _sha256(
            support.get("content_hash"),
            "runtime support authority.runtime_support_report.content_hash",
        )
        if support.get("compatibility_status") not in {
            "supported",
            "partially_supported",
            "unsupported",
        } or support.get("packaging_status") not in {"unverified", "verified", "failed"}:
            _fail(
                "runtime_support_authority_support_mismatch",
                "support reference status is invalid",
            )
        if (
            support.get("release_status") != "blocked"
            or support.get("supported") is not False
            or document.get("native_status") != "unavailable"
            or document.get("release_status") != "blocked"
            or document.get("supported") is not False
        ):
            _fail(
                "runtime_support_authority_overclaim",
                "runtime support authority v1 must remain native-unavailable and release-blocked",
            )
        reasons = document.get("reason_codes")
        if (
            not isinstance(reasons, list)
            or not reasons
            or len(reasons) > 64
            or reasons != sorted(reasons, key=lambda item: str(item).encode("utf-8"))
            or len(set(reasons)) != len(reasons)
        ):
            _fail(
                "runtime_support_authority_noncanonical",
                "reason_codes must be a sorted unique bounded array",
            )
        for index, reason in enumerate(reasons):
            _identifier(reason, f"runtime support authority.reason_codes/{index}")
        if RUNTIME_SUPPORT_AUTHORITY_NATIVE_UNAVAILABLE not in reasons:
            _fail(
                "runtime_support_authority_overclaim",
                "native-unavailable reason is mandatory",
            )
        if document["authority_id"] != _derived_authority_id(document):
            _fail(
                "runtime_support_authority_id_mismatch",
                "authority_id is not canonical",
            )
        return copy.deepcopy(document)
    except RuntimeSupportAuthorityError:
        raise
    except (CreationContractError, TypeError, ValueError, RecursionError) as exc:
        _fail("runtime_support_authority_invalid", str(exc))


def _validate_runtime_evidence_reference(value: object, context: str) -> dict[str, Any]:
    reference = _object(value, context)
    _exact_keys(reference, _RUNTIME_EVIDENCE_REFERENCE_FIELDS, context)
    if reference.get("format") != RUNTIME_EVIDENCE_FORMAT or reference.get("format_version") != 1:
        _fail(
            "runtime_support_authority_evidence_mismatch",
            f"{context} is not runtime evidence v1",
        )
    _authority_identifier(reference.get("id"), f"{context}.id")
    _sha256(reference.get("content_hash"), f"{context}.content_hash")
    platform = _validate_platform(reference.get("platform"), f"{context}.platform")
    if reference.get("execution_status") != "headless_verified":
        _fail(
            "runtime_support_authority_headless_overclaim",
            f"{context} must be headless_verified",
        )
    if reference.get("packaging_status") not in {"unverified", "verified"}:
        _fail(
            "runtime_support_authority_evidence_mismatch",
            f"{context}.packaging_status is unsupported",
        )
    return {**copy.deepcopy(reference), "platform": platform}


def serialize_runtime_support_authority(value: object) -> bytes:
    return canonical_json_bytes(validate_runtime_support_authority_document(value))


def load_runtime_support_authority(path: str | Path) -> dict[str, Any]:
    try:
        return validate_runtime_support_authority_document(
            read_creation_object(path, limit=MAX_RUNTIME_SUPPORT_AUTHORITY_BYTES)
        )
    except RuntimeSupportAuthorityError:
        raise
    except (CreationContractError, OSError, TypeError, ValueError) as exc:
        _fail("runtime_support_authority_read_failed", str(exc))


__all__ = [
    "MAX_RUNTIME_SUPPORT_AUTHORITY_BYTES",
    "RUNTIME_SUPPORT_AUTHORITY_FORMAT",
    "RUNTIME_SUPPORT_AUTHORITY_NATIVE_UNAVAILABLE",
    "RUNTIME_SUPPORT_AUTHORITY_VERSION",
    "RuntimeSupportAuthorityError",
    "VerifiedRuntimeSupportAuthority",
    "attach_native_evidence",
    "attach_verified_game_package",
    "attach_verified_headless_evidence",
    "derive_runtime_evidence",
    "derive_runtime_support_report",
    "initialize_runtime_support_authority",
    "load_runtime_support_authority",
    "serialize_runtime_support_authority",
    "validate_runtime_support_authority_document",
]
