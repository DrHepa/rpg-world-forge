from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import re
import struct
import unicodedata
import wave
import zlib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from worldforge.asset_formats.gltf import GLBError, inspect_glb_bytes
from worldforge.asset_io import AssetContractError, write_json_atomic
from worldforge.creation_contracts import (
    CreationContractError,
    _exact_keys,
    _identifier,
    _integer,
    _logic_runtime_string,
    _non_empty_string,
    _object,
    _portable_relative_path,
    _sha256,
    _string_array,
    _validate_json_structure,
    canonical_creation_hash,
    read_creation_object,
)
from worldforge.gamepack import (
    GamepackError,
    PublishedGameArtifact,
    _published_artifact,
    preflight_game_artifact_output,
    validate_gamepack_document,
)
from worldforge.generic_assets import (
    _OUTPUT_MEDIA,
    ASSET_INVENTORY_FORMAT,
    ASSET_SPEC_FORMAT,
    ASSET_STYLE_FORMAT,
    ASSET_SUBJECT_FORMAT,
    ASSET_TARGET_FORMAT,
    GenericAssetError,
    _gamepack_identity,
    _identity,
    _validate_glyph_ranges,
    _validate_spec_output,
    validate_asset_inventory,
    validate_asset_specification,
    validate_asset_style,
    validate_asset_subject,
    validate_asset_target,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.studio.changesets import _open_pinned_parent, _safe_entry_snapshot
from worldforge.studio.errors import StudioError
from worldforge.studio.workspaces import _pinned_ancestor_identities
from worldforge.validation_memo import memoize_document_validation

ASSET_PRODUCTION_REQUEST_FORMAT = "world-forge.asset_production_request"
ASSET_PRODUCTION_RECEIPT_FORMAT = "world-forge.asset_production_receipt"
ASSET_RECEIPT_LINEAGE_FORMAT = "world-forge.asset_receipt_lineage"
ASSET_SELECTION_FORMAT = "world-forge.asset_selection"
ASSET_PROVENANCE_FORMAT = "world-forge.asset_provenance_record"
ASSET_LICENSE_FORMAT = "world-forge.asset_license_record"
GENERIC_ASSET_PRODUCTION_VERSION = 1

MAX_PRODUCTION_INPUTS = 64
MAX_PRODUCTION_OUTPUTS = 4
MAX_PRODUCTION_PARENTS = 64
MAX_PRODUCTION_EVIDENCE = 64
MAX_PRODUCTION_COMPONENTS = 70
MAX_PRODUCTION_DATASETS = 64
MAX_PRODUCTION_TEXT = 1024
MAX_RUNTIME_NOTICE_UTF8_BYTES = 4096
MAX_CANDIDATE_BYTES = 16 * 1024 * 1024
MAX_DECODED_IMAGE_BYTES = 64 * 1024 * 1024
MAX_GLSL_BYTES = 1024 * 1024
MAX_JSON_RECORDS = 1_000_000

_FORMATS = frozenset(
    {
        ASSET_PRODUCTION_REQUEST_FORMAT,
        ASSET_PRODUCTION_RECEIPT_FORMAT,
        ASSET_SELECTION_FORMAT,
        ASSET_PROVENANCE_FORMAT,
        ASSET_LICENSE_FORMAT,
    }
)
_PRODUCTION_CLASSES = frozenset(
    {"human", "procedural_offline", "external_authoring", "generative_authoring"}
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_ASSET_FIELDS = frozenset({"asset_id", "content_hash"})
_OPERATION_FIELDS = frozenset({"operation_id", "version"})
_INPUT_FIELDS = frozenset({"artifact_id", "role", "locator", "size_bytes", "sha256"})
_REPRODUCIBILITY_FIELDS = frozenset({"mode", "seed_policy"})
_RIGHTS_REQUIREMENT_FIELDS = frozenset(
    {
        "commercial_use_review_required",
        "evidence_required",
        "human_review_required",
        "redistribution_review_required",
    }
)
_REVIEW_REQUIREMENT_FIELDS = frozenset({"human_review_required", "qa_profile", "evidence_required"})
_REQUEST_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "request_id",
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "asset",
        "production_class",
        "operation",
        "input_artifacts",
        "expected_outputs",
        "reproducibility",
        "review_requirements",
        "rights_requirements",
        "toolchain_requirements",
        "content_hash",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "receipt_id",
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "asset",
        "request",
        "production_class",
        "status",
        "executed_toolchain",
        "input_artifacts",
        "outputs",
        "lineage_parents",
        "execution_evidence",
        "rights_attestation",
        "failure_reasons",
        "content_hash",
    }
)
_RECEIPT_OUTPUT_FIELDS = frozenset(
    {
        "candidate_artifact_id",
        "locator",
        "media_type",
        "metadata",
        "role",
        "runtime_path",
        "sha256",
        "size_bytes",
    }
)
_PARENT_FIELDS = frozenset({"receipt_id", "content_hash"})
_EXECUTION_EVIDENCE_FIELDS = frozenset(
    {"started_evidence_hash", "completed_evidence_hash", "sanitized_log_hashes"}
)
_RIGHTS_ATTESTATION_FIELDS = frozenset({"basis", "evidence_hashes"})
_SELECTION_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "selection_id",
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "asset",
        "request",
        "receipt",
        "receipt_lineage",
        "selected_outputs",
        "rejected_candidates",
        "review",
        "content_hash",
    }
)
_SELECTED_OUTPUT_FIELDS = frozenset(
    {"candidate_artifact_id", "role", "media_type", "size_bytes", "sha256"}
)
_REJECTED_FIELDS = frozenset({"candidate_artifact_id", "receipt", "reason_code"})
_REVIEW_FIELDS = frozenset({"reviewer_id", "rationale", "evidence_hashes"})
_RECEIPT_LINEAGE_FIELDS = frozenset({"format", "format_version", "closures"})
_RECEIPT_CLOSURE_FIELDS = frozenset({"root", "parents"})
_PROVENANCE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "provenance_id",
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "asset",
        "request",
        "receipt",
        "selection",
        "production_class",
        "toolchain",
        "components",
        "candidates",
        "lineage",
        "content_hash",
    }
)
_COMPONENT_FIELDS = frozenset({"scope", "component_id", "component_version", "evidence_hash"})
_PROVENANCE_CANDIDATE_FIELDS = frozenset({"candidate_artifact_id", "role", "media_type", "sha256"})
_LINEAGE_NODE_FIELDS = frozenset({"node_id", "content_hash", "parent_hashes"})
_LICENSE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "license_record_id",
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "asset",
        "request",
        "receipt",
        "selection",
        "provenance",
        "candidate",
        "license_basis",
        "copyright",
        "permissions",
        "obligations",
        "component_licenses",
        "runtime_notice",
        "evidence_hashes",
        "content_hash",
    }
)
_LICENSE_BASIS_FIELDS = frozenset({"kind", "identifier"})
_COPYRIGHT_FIELDS = frozenset({"holder", "year_policy", "year"})
_PERMISSION_FIELDS = frozenset({"commercial_use", "modification", "redistribution"})
_OBLIGATION_FIELDS = frozenset({"attribution_required", "notice_required", "source_offer_required"})
_COMPONENT_LICENSE_FIELDS = frozenset({"scope", "component_id", "identifier", "evidence_hash"})
_NOTICE_FIELDS = frozenset({"text", "sha256"})

_SEMVER_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_SPDX_RE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9.+-]*)(?:\s+(?:AND|OR)\s+[A-Za-z0-9][A-Za-z0-9.+-]*)*$"
)
_CUSTOM_LICENSE_RE = re.compile(r"^LicenseRef-[A-Za-z0-9][A-Za-z0-9.-]{1,126}$")
_APPROVED_CUSTOM_LICENSES = frozenset({"LicenseRef-WorldForge-Fixture-Public-Domain"})
_URL_RE = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?:api[_ -]?key|authorization|bearer|credential|password|private[_ -]?key|token)"
    r"\s*(?:=|:)",
    re.IGNORECASE,
)
_NOTICE_AUTHORING_RE = re.compile(
    r"\b(?:apis?|credentials?|datasets?|endpoints?|instructions?|mcps?|models?|prompts?|"
    r"providers?|seeds?|tokens?|weights?)\b",
    re.IGNORECASE,
)
_NOTICE_SECRET_RE = re.compile(
    r"(?:"
    r"^[ \t]*bearer[ \t]+[A-Za-z0-9._~+/=-]{8,}[ \t]*$"
    r"|\bsk-[A-Za-z0-9_-]{12,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{36,255}\b"
    r"|\bxox[baprs]-[A-Za-z0-9-]{10,255}\b"
    r"|\bAIza[0-9A-Za-z_-]{35}\b"
    r"|^[ \t]*(?:authorization|proxy-authorization|x-api-key|api-key)[ \t]*:[ \t]*\S+"
    r"|-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----"
    r")",
    re.IGNORECASE | re.MULTILINE,
)
_JWT_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_JWT_HEADER_SEGMENT = 512


class GenericAssetProductionError(ValueError):
    """Raised when generic gamepack production lineage fails closed."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise GenericAssetProductionError(reason_code, detail)


def _hash(document: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(document)
    except CreationContractError as exc:
        _fail("production_contract_invalid", str(exc))


def _seal(document: dict[str, Any]) -> dict[str, Any]:
    document["content_hash"] = _hash(document)
    return document


def _preflight_runtime_notice_text(value: object, context: str) -> None:
    """Reject oversized or non-scalar notice text before whole-value work."""

    if not isinstance(value, str):
        return
    utf8_bytes = 0
    for index in range(MAX_PRODUCTION_TEXT + 1):
        try:
            character = value[index]
        except IndexError:
            return
        code_point = ord(character)
        if 0xD800 <= code_point <= 0xDFFF:
            _fail(
                "production_contract_unsafe_text",
                f"{context} contains a non-scalar Unicode value",
            )
        if index == MAX_PRODUCTION_TEXT:
            _fail(
                "production_contract_limit",
                f"{context} exceeds {MAX_PRODUCTION_TEXT} characters",
            )
        if code_point <= 0x7F:
            utf8_bytes += 1
        elif code_point <= 0x7FF:
            utf8_bytes += 2
        elif code_point <= 0xFFFF:
            utf8_bytes += 3
        else:
            utf8_bytes += 4
        if utf8_bytes > MAX_RUNTIME_NOTICE_UTF8_BYTES:
            _fail(
                "production_contract_limit",
                f"{context} exceeds {MAX_RUNTIME_NOTICE_UTF8_BYTES} UTF-8 bytes",
            )


def _preflight_asset_license_notice(value: object) -> None:
    if not isinstance(value, dict):
        return
    notice = dict.get(value, "runtime_notice")
    if not isinstance(notice, dict):
        return
    _preflight_runtime_notice_text(
        dict.get(notice, "text"),
        "asset license record.runtime_notice.text",
    )


def _bounded_text(value: object, context: str, *, maximum: int = MAX_PRODUCTION_TEXT) -> str:
    text = _non_empty_string(value, context)
    if len(text) > maximum:
        _fail("production_contract_limit", f"{context} exceeds {maximum} characters")
    try:
        _logic_runtime_string(text, context)
    except CreationContractError as exc:
        _fail("production_contract_unsafe_text", str(exc))
    if _URL_RE.search(text) or _SECRET_RE.search(text):
        _fail("production_contract_unsafe_text", f"{context} contains a URL or secret-like value")
    return text


def _contains_standalone_jwt(value: str) -> bool:
    for raw_line in value.splitlines() or (value,):
        candidate = raw_line.strip(" \t")
        segments = candidate.split(".")
        if len(segments) != 3 or any(
            _JWT_SEGMENT_RE.fullmatch(segment) is None for segment in segments
        ):
            continue
        if len(segments[0]) > _MAX_JWT_HEADER_SEGMENT:
            return True
        encoded_header = segments[0]
        try:
            header_bytes = base64.b64decode(
                (encoded_header + "=" * (-len(encoded_header) % 4)).encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
            header = json.loads(header_bytes.decode("utf-8"))
        except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
            continue
        if isinstance(header, dict) and isinstance(header.get("alg"), str) and bool(header["alg"]):
            return True
    return False


def _validate_hash(document: Mapping[str, object], context: str) -> None:
    _sha256(document.get("content_hash"), f"{context}.content_hash")
    if document["content_hash"] != _hash(document):
        _fail("content_hash_mismatch", f"{context}.content_hash is not canonical")


def _ensure_structure(value: object, context: str) -> None:
    try:
        _validate_json_structure(value, context=context)
    except CreationContractError as exc:
        _fail("production_contract_invalid", str(exc))


def _identity_value(
    value: object,
    context: str,
    *,
    expected_format: str | None = None,
) -> dict[str, Any]:
    identity = _object(value, context)
    _exact_keys(identity, _IDENTITY_FIELDS, context)
    format_name = _non_empty_string(identity.get("format"), f"{context}.format")
    if expected_format is not None and format_name != expected_format:
        _fail("production_lineage_mismatch", f"{context}.format must be {expected_format}")
    if identity.get("format_version") != 1:
        _fail("production_lineage_mismatch", f"{context}.format_version must be 1")
    _identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def _production_identity(document: Mapping[str, object], id_field: str) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


def _spec_identity(specification: Mapping[str, object]) -> dict[str, object]:
    return _identity(specification, id_field="spec_id")


def _common_identities(
    gamepack: Mapping[str, object],
    subject: Mapping[str, object],
    target: Mapping[str, object],
    style: Mapping[str, object],
    inventory: Mapping[str, object],
    specification: Mapping[str, object],
) -> dict[str, object]:
    return {
        "gamepack": _gamepack_identity(gamepack),
        "asset_subject": _identity(subject, id_field="subject_id"),
        "target": _identity(target, id_field="target_id"),
        "style": _identity(style, id_field="style_id"),
        "inventory": _identity(inventory, id_field="inventory_id"),
        "specification": _spec_identity(specification),
        "asset": copy.deepcopy(specification["asset"]),
    }


def _checked_chain(
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
) -> tuple[dict[str, Any], ...]:
    try:
        checked_gamepack = validate_gamepack_document(gamepack)
        checked_subject = validate_asset_subject(subject, gamepack=checked_gamepack)
        checked_target = validate_asset_target(
            target,
            gamepack=checked_gamepack,
            subject=checked_subject,
        )
        checked_style = validate_asset_style(
            style,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
        )
        checked_inventory = validate_asset_inventory(
            inventory,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
            style=checked_style,
        )
        checked_specification = validate_asset_specification(
            specification,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
            style=checked_style,
            inventory=checked_inventory,
        )
    except (CreationContractError, GenericAssetError, TypeError, ValueError) as exc:
        _fail("production_lineage_invalid", str(exc))
    return (
        checked_gamepack,
        checked_subject,
        checked_target,
        checked_style,
        checked_inventory,
        checked_specification,
    )


def _check_common(
    document: Mapping[str, object],
    checked: tuple[dict[str, Any], ...],
    context: str,
) -> None:
    expected = _common_identities(*checked)
    for name, value in expected.items():
        if document.get(name) != value:
            _fail("production_lineage_mismatch", f"{context}.{name} is not the exact D1 identity")


def _canonical_entries(
    values: list[dict[str, Any]],
    context: str,
    *,
    key: str,
    maximum: int,
    allow_empty: bool = True,
) -> list[dict[str, Any]]:
    if (not allow_empty and not values) or len(values) > maximum:
        _fail("production_contract_limit", f"{context} has invalid cardinality")
    ordered = sorted(values, key=lambda item: str(item[key]).encode("utf-8"))
    if ordered != values:
        _fail("production_contract_noncanonical", f"{context} must use canonical order")
    folded: set[str] = set()
    for entry in values:
        identifier = str(entry[key])
        normalized = unicodedata.normalize("NFC", identifier).casefold()
        if normalized in folded:
            _fail("production_contract_collision", f"{context} has an NFC/casefold collision")
        folded.add(normalized)
    return values


def _portable_path_tree(values: Sequence[str], context: str) -> None:
    paths: dict[tuple[str, ...], str] = {}
    for value in values:
        if not value.isascii():
            _fail(
                "production_artifact_path_invalid",
                f"{context} paths must use printable ASCII for cross-platform portability",
            )
        parts = tuple(
            unicodedata.normalize("NFC", part).casefold() for part in PurePosixPath(value).parts
        )
        for existing_parts, existing in paths.items():
            shared = min(len(parts), len(existing_parts))
            if parts[:shared] == existing_parts[:shared]:
                _fail(
                    "production_contract_collision",
                    f"{context} has an NFC/casefold or file-prefix collision: "
                    f"{existing!r}, {value!r}",
                )
        paths[parts] = value


def _validate_input_artifacts(value: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_PRODUCTION_INPUTS:
        _fail("production_contract_limit", f"{context} must be a bounded array")
    result: list[dict[str, Any]] = []
    locators: set[str] = set()
    for index, raw in enumerate(value):
        item_context = f"{context}/{index}"
        item = _object(raw, item_context)
        _exact_keys(item, _INPUT_FIELDS, item_context)
        _identifier(item.get("artifact_id"), f"{item_context}.artifact_id")
        _identifier(item.get("role"), f"{item_context}.role")
        try:
            locator = _portable_relative_path(item.get("locator"), f"{item_context}.locator")
        except CreationContractError as exc:
            _fail("production_artifact_path_invalid", str(exc))
        folded = unicodedata.normalize("NFC", locator).casefold()
        if folded in locators:
            _fail("production_contract_collision", f"{context} has colliding locators")
        locators.add(folded)
        _integer(item.get("size_bytes"), f"{item_context}.size_bytes", minimum=1)
        if item["size_bytes"] > MAX_CANDIDATE_BYTES:
            _fail("production_contract_limit", f"{item_context}.size_bytes exceeds limit")
        _sha256(item.get("sha256"), f"{item_context}.sha256")
        result.append(item)
    checked = _canonical_entries(
        result,
        context,
        key="artifact_id",
        maximum=MAX_PRODUCTION_INPUTS,
    )
    _portable_path_tree([item["locator"] for item in checked], context)
    _require_distinct_hashes(
        ((item["sha256"], f"{context}/{index}.sha256") for index, item in enumerate(checked)),
        context,
    )
    return checked


def _require_distinct_hashes(
    values: Iterable[tuple[object, str]],
    context: str,
) -> None:
    seen: dict[str, str] = {}
    for raw, source in values:
        digest = _sha256(raw, source)
        previous = seen.get(digest)
        if previous is not None:
            _fail(
                "production_lineage_duplicate",
                f"{context} reuses content hash across {previous} and {source}",
            )
        seen[digest] = source


def _validate_expected_outputs(value: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_PRODUCTION_OUTPUTS:
        _fail("production_contract_limit", f"{context} must be a bounded non-empty array")
    outputs = []
    for index, output in enumerate(value):
        try:
            outputs.append(copy.deepcopy(_validate_spec_output(output, f"{context}/{index}")))
        except GenericAssetError as exc:
            _fail("production_contract_invalid", str(exc))
    return _canonical_entries(
        outputs,
        context,
        key="role",
        maximum=MAX_PRODUCTION_OUTPUTS,
        allow_empty=False,
    )


def _validate_reproducibility(value: object, context: str) -> dict[str, Any]:
    result = _object(value, context)
    _exact_keys(result, _REPRODUCIBILITY_FIELDS, context)
    if result.get("mode") not in {"deterministic", "reviewed_nondeterministic"}:
        _fail("production_contract_invalid", f"{context}.mode is unsupported")
    if result.get("seed_policy") not in {"forbidden", "fixed", "recorded"}:
        _fail("production_contract_invalid", f"{context}.seed_policy is unsupported")
    return result


def _validate_rights_requirements(value: object, context: str) -> dict[str, Any]:
    result = _object(value, context)
    _exact_keys(result, _RIGHTS_REQUIREMENT_FIELDS, context)
    for field in _RIGHTS_REQUIREMENT_FIELDS:
        if result.get(field) is not True:
            _fail("production_rights_incomplete", f"{context}.{field} must be true")
    return result


def _validate_review_requirements(value: object, context: str) -> dict[str, Any]:
    result = _object(value, context)
    _exact_keys(result, _REVIEW_REQUIREMENT_FIELDS, context)
    if result.get("human_review_required") is not True:
        _fail("production_review_incomplete", f"{context}.human_review_required must be true")
    if result.get("evidence_required") is not True:
        _fail("production_review_incomplete", f"{context}.evidence_required must be true")
    _identifier(result.get("qa_profile"), f"{context}.qa_profile")
    return result


def _semver(value: object, context: str) -> str:
    text = _non_empty_string(value, context)
    if _SEMVER_RE.fullmatch(text) is None:
        _fail("production_toolchain_invalid", f"{context} must be semantic version")
    return text


def _validate_toolchain(
    value: object,
    context: str,
    *,
    production_class: str,
    operation_id: str,
    reproducibility: Mapping[str, object],
) -> dict[str, Any]:
    result = _object(value, context)
    common = {"production_class", "operation_id"}
    fields_by_class = {
        "human": common | {"creator_id", "work_attestation_hash"},
        "procedural_offline": common | {"tool_id", "tool_version", "seed"},
        "external_authoring": common | {"tool_id", "tool_version"},
        "generative_authoring": common
        | {
            "provider_id",
            "tool_id",
            "tool_version",
            "model_id",
            "model_version",
            "weights_id",
            "weights_version",
            "dataset_ids",
            "seed_policy",
            "seed",
            "instruction_artifact_hash",
        },
    }
    expected_fields = fields_by_class.get(production_class)
    if expected_fields is None:
        _fail("production_class_invalid", f"{context} production class is unsupported")
    _exact_keys(result, frozenset(expected_fields), context)
    if result.get("production_class") != production_class:
        _fail("production_toolchain_invalid", f"{context}.production_class is crossed")
    actual_operation_id = _identifier(
        result.get("operation_id"),
        f"{context}.operation_id",
    )
    if actual_operation_id != operation_id:
        _fail("production_toolchain_invalid", f"{context}.operation_id does not match request")
    if production_class == "human":
        _identifier(result.get("creator_id"), f"{context}.creator_id")
        _sha256(result.get("work_attestation_hash"), f"{context}.work_attestation_hash")
        if reproducibility["seed_policy"] != "forbidden":
            _fail("production_toolchain_invalid", "human production forbids seed policy")
    elif production_class == "procedural_offline":
        _identifier(result.get("tool_id"), f"{context}.tool_id")
        _semver(result.get("tool_version"), f"{context}.tool_version")
        seed = result.get("seed")
        if seed is not None:
            _integer(seed, f"{context}.seed", minimum=0)
        if reproducibility["seed_policy"] in {"fixed", "recorded"} and seed is None:
            _fail(
                "production_toolchain_invalid",
                f"{reproducibility['seed_policy']} procedural production requires a seed",
            )
        if reproducibility["seed_policy"] == "forbidden" and seed is not None:
            _fail("production_toolchain_invalid", "forbidden seed policy cannot record a seed")
    elif production_class == "external_authoring":
        _identifier(result.get("tool_id"), f"{context}.tool_id")
        _semver(result.get("tool_version"), f"{context}.tool_version")
        if reproducibility["seed_policy"] != "forbidden":
            _fail("production_toolchain_invalid", "external authoring forbids model seed fields")
    else:
        for field in ("provider_id", "tool_id", "model_id", "weights_id"):
            _identifier(result.get(field), f"{context}.{field}")
        for field in ("tool_version", "model_version", "weights_version"):
            _semver(result.get(field), f"{context}.{field}")
        datasets = _string_array(
            result.get("dataset_ids"),
            f"{context}.dataset_ids",
            allow_empty=False,
            canonical_order=True,
        )
        if len(datasets) > MAX_PRODUCTION_DATASETS:
            _fail("production_contract_limit", f"{context}.dataset_ids exceeds limit")
        for index, dataset_id in enumerate(datasets):
            _identifier(dataset_id, f"{context}.dataset_ids/{index}")
        if result.get("seed_policy") != reproducibility["seed_policy"]:
            _fail("production_toolchain_invalid", f"{context}.seed_policy is crossed")
        if result["seed_policy"] not in {"fixed", "recorded"}:
            _fail("production_toolchain_invalid", "generative authoring requires a recorded seed")
        _integer(result.get("seed"), f"{context}.seed", minimum=0)
        _sha256(
            result.get("instruction_artifact_hash"),
            f"{context}.instruction_artifact_hash",
        )
    return result


def _validate_request_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset production request")
    _exact_keys(document, _REQUEST_FIELDS, "asset production request")
    if document.get("format") != ASSET_PRODUCTION_REQUEST_FORMAT:
        _fail("production_request_format_invalid", "request format is unsupported")
    if document.get("format_version") != 1:
        _fail("production_request_version_invalid", "request version must be 1")
    _identifier(document.get("request_id"), "asset production request.request_id")
    for field, expected_format in (
        ("gamepack", "world-forge.gamepack"),
        ("asset_subject", ASSET_SUBJECT_FORMAT),
        ("target", ASSET_TARGET_FORMAT),
        ("style", ASSET_STYLE_FORMAT),
        ("inventory", ASSET_INVENTORY_FORMAT),
        ("specification", ASSET_SPEC_FORMAT),
    ):
        _identity_value(
            document.get(field),
            f"asset production request.{field}",
            expected_format=expected_format,
        )
    asset = _object(document.get("asset"), "asset production request.asset")
    _exact_keys(asset, _ASSET_FIELDS, "asset production request.asset")
    _identifier(asset.get("asset_id"), "asset production request.asset.asset_id")
    _sha256(asset.get("content_hash"), "asset production request.asset.content_hash")
    production_class = document.get("production_class")
    if production_class not in _PRODUCTION_CLASSES:
        _fail("production_class_invalid", "request production_class is unsupported")
    operation = _object(document.get("operation"), "asset production request.operation")
    _exact_keys(operation, _OPERATION_FIELDS, "asset production request.operation")
    operation_id = _identifier(
        operation.get("operation_id"),
        "asset production request.operation.operation_id",
    )
    operation_version = _integer(
        operation.get("version"),
        "asset production request.operation.version",
        minimum=1,
    )
    if operation_version > 65535:
        _fail(
            "production_contract_limit",
            "asset production request.operation.version exceeds 65535",
        )
    _validate_input_artifacts(
        document.get("input_artifacts"),
        "asset production request.input_artifacts",
    )
    _validate_expected_outputs(
        document.get("expected_outputs"),
        "asset production request.expected_outputs",
    )
    reproducibility = _validate_reproducibility(
        document.get("reproducibility"),
        "asset production request.reproducibility",
    )
    _validate_review_requirements(
        document.get("review_requirements"),
        "asset production request.review_requirements",
    )
    _validate_rights_requirements(
        document.get("rights_requirements"),
        "asset production request.rights_requirements",
    )
    _validate_toolchain(
        document.get("toolchain_requirements"),
        "asset production request.toolchain_requirements",
        production_class=production_class,
        operation_id=operation_id,
        reproducibility=reproducibility,
    )
    _ensure_structure(document, "asset production request")
    _validate_hash(document, "asset production request")
    return copy.deepcopy(document)


def build_asset_production_request(
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    *,
    request_id: str,
    production_class: str,
    operation: object,
    input_artifacts: object,
    reproducibility: object,
    rights_requirements: object,
    toolchain_requirements: object,
) -> dict[str, Any]:
    checked = _checked_chain(
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    common = _common_identities(*checked)
    document = {
        "format": ASSET_PRODUCTION_REQUEST_FORMAT,
        "format_version": 1,
        "request_id": request_id,
        **common,
        "production_class": production_class,
        "operation": copy.deepcopy(operation),
        "input_artifacts": copy.deepcopy(input_artifacts),
        "expected_outputs": copy.deepcopy(checked[-1]["outputs"]),
        "reproducibility": copy.deepcopy(reproducibility),
        "review_requirements": copy.deepcopy(checked[-1]["review_requirements"]),
        "rights_requirements": copy.deepcopy(rights_requirements),
        "toolchain_requirements": copy.deepcopy(toolchain_requirements),
        "content_hash": "",
    }
    request = validate_asset_production_request_document(_seal(document))
    _check_common(request, checked, "asset production request")
    if request["production_class"] != checked[-1]["production_class"]:
        _fail(
            "production_class_mismatch",
            "request production class does not match the exact specification",
        )
    return request


def validate_asset_production_request_document(value: object) -> dict[str, Any]:
    try:
        return _validate_request_structure(value)
    except GenericAssetProductionError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("production_request_invalid", str(exc))


def _validate_asset_production_request_uncached(
    value: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
) -> dict[str, Any]:
    request = validate_asset_production_request_document(value)
    checked = _checked_chain(
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    _check_common(request, checked, "asset production request")
    if request["expected_outputs"] != checked[-1]["outputs"]:
        _fail(
            "production_output_mismatch", "request outputs are not the exact specification outputs"
        )
    if request["review_requirements"] != checked[-1]["review_requirements"]:
        _fail(
            "production_review_mismatch",
            "request review requirements are not the exact specification requirements",
        )
    if request["production_class"] != checked[-1]["production_class"]:
        _fail("production_class_mismatch", "request class is not the specification class")
    return request


def validate_asset_production_request(
    value: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
) -> dict[str, Any]:
    return memoize_document_validation(
        "validate_asset_production_request",
        value,
        lambda candidate: _validate_asset_production_request_uncached(
            candidate,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
            inventory=inventory,
            specification=specification,
        ),
        dependencies=(gamepack, subject, target, style, inventory, specification),
    )


def _safe_artifact_bytes(root: str | Path, locator: object, *, limit: int) -> bytes:
    try:
        safe = _portable_relative_path(locator, "candidate locator")
    except CreationContractError as exc:
        _fail("production_artifact_path_invalid", str(exc))
    base = Path(os.path.abspath(os.fspath(root)))
    try:
        with _pinned_ancestor_identities(base, context="candidate artifact root") as identities:
            relative = PurePosixPath(safe)
            parent_path = base.joinpath(*relative.parts[:-1])
            with _pinned_ancestor_identities(
                parent_path,
                context=f"candidate artifact parent {relative.parent}",
            ) as parent_identities:
                with _open_pinned_parent(
                    base,
                    relative,
                    world_identity=identities[-1],
                    parent_identity=parent_identities[-1],
                ) as parent:
                    payload, _ = _safe_entry_snapshot(
                        parent,
                        relative.name,
                        context=f"candidate artifact {safe}",
                        require_standalone=True,
                        require_utf8=False,
                        limit=limit,
                    )
                    return payload
    except StudioError as exc:
        _fail("production_artifact_read_failed", exc.message)


def read_verified_artifact_bytes(
    root: str | Path,
    locator: object,
    *,
    expected_sha256: object,
    expected_size_bytes: object,
    limit: int = MAX_CANDIDATE_BYTES,
) -> bytes:
    """Read one link-safe artifact snapshot and verify its declared byte identity."""

    try:
        checked_hash = _sha256(expected_sha256, "expected artifact sha256")
        checked_size = _integer(
            expected_size_bytes,
            "expected artifact size_bytes",
            minimum=0,
        )
        checked_limit = _integer(limit, "artifact byte limit", minimum=1)
    except CreationContractError as exc:
        _fail("production_artifact_identity_invalid", str(exc))
    if checked_size > checked_limit:
        _fail(
            "production_artifact_size_mismatch",
            "expected artifact size exceeds the retained-byte limit",
        )
    payload = _safe_artifact_bytes(root, locator, limit=checked_limit)
    if len(payload) != checked_size:
        _fail(
            "production_artifact_size_mismatch",
            "artifact size does not match its declared byte identity",
        )
    if hashlib.sha256(payload).hexdigest() != checked_hash:
        _fail(
            "production_artifact_hash_mismatch",
            "artifact hash does not match its declared byte identity",
        )
    return payload


def _validate_input_artifact_bytes(
    inputs: Sequence[Mapping[str, object]],
    artifact_root: str | Path,
) -> None:
    for index, item in enumerate(inputs):
        payload = _safe_artifact_bytes(
            artifact_root,
            item["locator"],
            limit=MAX_CANDIDATE_BYTES,
        )
        if len(payload) != item["size_bytes"]:
            _fail(
                "production_input_mismatch",
                f"input_artifacts/{index}.size_bytes is not byte-derived",
            )
        if hashlib.sha256(payload).hexdigest() != item["sha256"]:
            _fail(
                "production_input_mismatch",
                f"input_artifacts/{index}.sha256 is not byte-derived",
            )


def _png_metadata(payload: bytes, expectations: Mapping[str, object]) -> dict[str, object]:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        _fail("candidate_media_invalid", "PNG signature is invalid")
    offset = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    seen_iend = False
    while offset < len(payload):
        if len(payload) - offset < 12:
            _fail("candidate_media_invalid", "PNG chunk is truncated")
        length = struct.unpack_from(">I", payload, offset)[0]
        chunk_type = payload[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(payload):
            _fail("candidate_media_invalid", "PNG chunk exceeds file")
        data = payload[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack_from(">I", payload, offset + 8 + length)[0]
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            _fail("candidate_media_invalid", "PNG chunk CRC is invalid")
        if chunk_type == b"IHDR":
            if offset != 8 or length != 13:
                _fail("candidate_media_invalid", "PNG IHDR is invalid")
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            if compression or filtering or interlace:
                _fail("candidate_media_invalid", "PNG uses unsupported coding")
        elif chunk_type == b"IDAT":
            idat.extend(data)
        elif chunk_type == b"IEND":
            if length or end != len(payload):
                _fail("candidate_media_invalid", "PNG IEND is invalid")
            seen_iend = True
        offset = end
    if not seen_iend or width is None or not idat:
        _fail("candidate_media_invalid", "PNG is incomplete")
    modes = {(8, 6): ("rgba8", 4), (8, 2): ("rgb8", 3), (8, 0): ("grayscale8", 1)}
    mode_info = modes.get((bit_depth, color_type))
    if mode_info is None:
        _fail("candidate_media_invalid", "PNG color mode is unsupported")
    mode, channels = mode_info
    if (
        width != expectations["width"]
        or height != expectations["height"]
        or mode != expectations["color_type"]
    ):
        _fail("candidate_media_mismatch", "PNG bytes do not match specification expectations")
    expected_bytes = height * (1 + width * channels)
    if expected_bytes > MAX_DECODED_IMAGE_BYTES:
        _fail("candidate_media_mismatch", "PNG decoded byte budget exceeds validator limit")
    try:
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(bytes(idat), expected_bytes + 1)
        if len(decoded) > expected_bytes or decompressor.unconsumed_tail:
            _fail("candidate_media_invalid", "PNG decoded data exceeds expected byte count")
        decoded += decompressor.flush(expected_bytes + 1 - len(decoded))
    except zlib.error as exc:
        _fail("candidate_media_invalid", f"PNG image data is invalid: {exc}")
    if (
        len(decoded) != expected_bytes
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        _fail("candidate_media_invalid", "PNG decoded byte count is invalid")
    row_size = 1 + width * channels
    if any(decoded[row * row_size] > 4 for row in range(height)):
        _fail("candidate_media_invalid", "PNG scanline filter is invalid")
    return {"kind": "png", "width": width, "height": height, "mode": mode}


def _wav_metadata(payload: bytes, expectations: Mapping[str, object]) -> dict[str, object]:
    try:
        with wave.open(io.BytesIO(payload), "rb") as source:
            channels = source.getnchannels()
            sample_rate = source.getframerate()
            frames = source.getnframes()
            sample_width = source.getsampwidth()
            compression = source.getcomptype()
            decoded = source.readframes(frames)
    except (EOFError, wave.Error) as exc:
        _fail("candidate_media_invalid", f"WAV is invalid: {exc}")
    if compression != "NONE" or sample_width != 2:
        _fail("candidate_media_invalid", "WAV must be uncompressed PCM16")
    if len(decoded) != frames * channels * sample_width:
        _fail("candidate_media_invalid", "WAV frame bytes are truncated")
    if (
        channels != expectations["channels"]
        or sample_rate != expectations["sample_rate"]
        or frames != expectations["frames"]
    ):
        _fail("candidate_media_mismatch", "WAV bytes do not match specification expectations")
    return {
        "kind": "wav_pcm16",
        "channels": channels,
        "sample_rate": sample_rate,
        "frames": frames,
        "sample_width": sample_width,
    }


def _font_cmap_ranges(
    payload: bytes,
    table_offset: int,
    table_length: int,
    glyph_limit: int,
) -> list[tuple[int, int]]:
    if table_length < 4:
        _fail("candidate_media_invalid", "font cmap table is truncated")
    version, count = struct.unpack_from(">HH", payload, table_offset)
    if version != 0 or not 1 <= count <= 256 or table_length < 4 + count * 8:
        _fail("candidate_media_invalid", "font cmap header is invalid")
    ranges: list[tuple[int, int]] = []
    for index in range(count):
        platform, encoding, sub_offset = struct.unpack_from(
            ">HHI", payload, table_offset + 4 + index * 8
        )
        if not (platform == 0 and encoding <= 6 or platform == 3 and encoding in {1, 10}):
            continue
        if sub_offset < 4 + count * 8:
            _fail("candidate_media_invalid", "font cmap subtable overlaps its header")
        subtable = table_offset + sub_offset
        table_end = table_offset + table_length
        if subtable + 2 > table_end:
            _fail("candidate_media_invalid", "font cmap subtable is out of bounds")
        format_number = struct.unpack_from(">H", payload, subtable)[0]
        if format_number == 12:
            if subtable + 16 > table_end:
                _fail("candidate_media_invalid", "font cmap format 12 header is truncated")
            reserved, length, _language, group_count = struct.unpack_from(
                ">HIII", payload, subtable + 2
            )
            if (
                reserved != 0
                or not 1 <= group_count <= 65536
                or length != 16 + group_count * 12
                or subtable + length > table_end
            ):
                _fail("candidate_media_invalid", "font cmap format 12 bounds are invalid")
            previous_end = -1
            for group_index in range(group_count):
                start, end, start_glyph = struct.unpack_from(
                    ">III", payload, subtable + 16 + group_index * 12
                )
                if (
                    start > end
                    or end > 0x10FFFF
                    or start <= previous_end
                    or (start <= 0xDFFF and end >= 0xD800)
                ):
                    _fail("candidate_media_invalid", "font cmap format 12 group is invalid")
                previous_end = end
                last_glyph = start_glyph + (end - start)
                mapped_start = start + 1 if start_glyph == 0 else start
                if mapped_start <= end:
                    first_glyph = start_glyph + (mapped_start - start)
                    if first_glyph == 0 or last_glyph >= glyph_limit:
                        _fail(
                            "candidate_media_invalid",
                            "font cmap format 12 references an unknown glyph",
                        )
                    ranges.append((mapped_start, end))
            continue
        if format_number != 4 or subtable + 14 > table_end:
            continue
        length = struct.unpack_from(">H", payload, subtable + 2)[0]
        if length < 16 or subtable + length > table_end:
            _fail("candidate_media_invalid", "font cmap format 4 bounds are invalid")
        seg_count_x2 = struct.unpack_from(">H", payload, subtable + 6)[0]
        if seg_count_x2 % 2 or not 2 <= seg_count_x2 <= 8192:
            _fail("candidate_media_invalid", "font cmap segment count is invalid")
        seg_count = seg_count_x2 // 2
        ends_offset = subtable + 14
        starts_offset = ends_offset + seg_count * 2 + 2
        deltas_offset = starts_offset + seg_count * 2
        range_offsets_offset = deltas_offset + seg_count * 2
        if range_offsets_offset + seg_count * 2 > subtable + length:
            _fail("candidate_media_invalid", "font cmap segments are truncated")
        ends = struct.unpack_from(f">{seg_count}H", payload, ends_offset)
        if struct.unpack_from(">H", payload, ends_offset + seg_count * 2)[0] != 0:
            _fail("candidate_media_invalid", "font cmap format 4 reserved pad is invalid")
        starts = struct.unpack_from(f">{seg_count}H", payload, starts_offset)
        deltas = struct.unpack_from(f">{seg_count}H", payload, deltas_offset)
        range_offsets = struct.unpack_from(f">{seg_count}H", payload, range_offsets_offset)
        if (
            any(left >= right for left, right in zip(ends, ends[1:], strict=False))
            or ends[-1] != 0xFFFF
        ):
            _fail("candidate_media_invalid", "font cmap format 4 end codes are not canonical")
        mapped_codepoints: list[int] = []
        for segment_index, (start, end, delta, range_offset) in enumerate(
            zip(starts, ends, deltas, range_offsets, strict=True)
        ):
            if start == end == 0xFFFF:
                continue
            if start > end or end >= 0xD800 and start <= 0xDFFF:
                _fail("candidate_media_invalid", "font cmap range is invalid")
            for codepoint in range(start, end + 1):
                if range_offset == 0:
                    glyph_id = (codepoint + delta) & 0xFFFF
                else:
                    glyph_offset = (
                        range_offsets_offset
                        + segment_index * 2
                        + range_offset
                        + (codepoint - start) * 2
                    )
                    if glyph_offset + 2 > subtable + length:
                        _fail("candidate_media_invalid", "font cmap glyph mapping is out of bounds")
                    glyph_id = struct.unpack_from(">H", payload, glyph_offset)[0]
                    if glyph_id != 0:
                        glyph_id = (glyph_id + delta) & 0xFFFF
                if glyph_id != 0:
                    if glyph_id >= glyph_limit:
                        _fail("candidate_media_invalid", "font cmap references an unknown glyph")
                    mapped_codepoints.append(codepoint)
        if mapped_codepoints:
            range_start = range_end = mapped_codepoints[0]
            for codepoint in mapped_codepoints[1:]:
                if codepoint == range_end + 1:
                    range_end = codepoint
                    continue
                ranges.append((range_start, range_end))
                range_start = range_end = codepoint
            ranges.append((range_start, range_end))
    if not ranges:
        _fail("candidate_media_invalid", "font has no supported Unicode cmap evidence")
    ranges.sort()
    merged: list[tuple[int, int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _font_metadata(
    payload: bytes,
    media_type: str,
    expectations: Mapping[str, object],
) -> dict[str, object]:
    signature = b"\x00\x01\x00\x00" if media_type == "font/ttf" else b"OTTO"
    container = "ttf" if media_type == "font/ttf" else "otf"
    if len(payload) < 12 or payload[:4] != signature:
        _fail("candidate_media_invalid", f"{container.upper()} header is invalid")
    table_count, search_range, entry_selector, range_shift = struct.unpack_from(">HHHH", payload, 4)
    if not 1 <= table_count <= 4096 or 12 + table_count * 16 > len(payload):
        _fail("candidate_media_invalid", "font table directory is invalid")
    greatest_power = 1 << (table_count.bit_length() - 1)
    if (
        search_range != greatest_power * 16
        or entry_selector != greatest_power.bit_length() - 1
        or range_shift != table_count * 16 - search_range
    ):
        _fail("candidate_media_invalid", "font search header is invalid")
    tables: dict[bytes, tuple[int, int]] = {}
    occupied: list[tuple[int, int]] = []
    for index in range(table_count):
        tag, expected_checksum, offset, length = struct.unpack_from(
            ">4sIII", payload, 12 + index * 16
        )
        if tag in tables or offset % 4 or offset < 12 + table_count * 16:
            _fail("candidate_media_invalid", "font table entry is invalid")
        if offset > len(payload) or length > len(payload) - offset:
            _fail("candidate_media_invalid", "font table is out of bounds")
        occupied.append((offset, offset + length))
        tables[tag] = (offset, length)
        table_payload = bytearray(payload[offset : offset + length])
        if tag == b"head":
            if length < 12:
                _fail("candidate_media_invalid", "font head table is truncated")
            struct.pack_into(">I", table_payload, 8, 0)
        padded = bytes(table_payload) + b"\0" * (-len(table_payload) % 4)
        checksum = sum(struct.unpack(f">{len(padded) // 4}I", padded)) & 0xFFFFFFFF
        if checksum != expected_checksum:
            _fail("candidate_media_invalid", f"font table {tag!r} checksum is invalid")
    cursor = 12 + table_count * 16
    for start, end in sorted(occupied):
        if start < cursor:
            _fail("candidate_media_invalid", "font tables overlap")
        cursor = end
    cmap = tables.get(b"cmap")
    maxp = tables.get(b"maxp")
    if cmap is None or maxp is None or maxp[1] < 6:
        _fail("candidate_media_invalid", "font has no complete cmap/maxp evidence")
    glyph_limit = struct.unpack_from(">H", payload, maxp[0] + 4)[0]
    if glyph_limit < 1:
        _fail("candidate_media_invalid", "font maxp glyph count is invalid")
    ranges = _font_cmap_ranges(payload, *cmap, glyph_limit)
    padded_font = payload + b"\0" * (-len(payload) % 4)
    if sum(struct.unpack(f">{len(padded_font) // 4}I", padded_font)) & 0xFFFFFFFF != 0xB1B0AFBA:
        _fail("candidate_media_invalid", "font checksum adjustment is invalid")
    expected_ranges = []
    for item in expectations["glyph_ranges"]:
        start_text, end_text = item.removeprefix("U+").split("-", 1)
        expected_ranges.append((int(start_text, 16), int(end_text, 16)))
    for start, end in expected_ranges:
        if not any(
            actual_start <= start and actual_end >= end for actual_start, actual_end in ranges
        ):
            _fail("candidate_media_mismatch", "font cmap does not cover required glyph range")
    glyph_count = sum(end - start + 1 for start, end in ranges)
    if glyph_count > expectations["max_glyphs"]:
        _fail("candidate_media_mismatch", "font glyph evidence exceeds specification budget")
    rendered = [f"U+{start:04X}-{end:04X}" for start, end in ranges]
    return {
        "kind": "font",
        "container": container,
        "glyph_count": glyph_count,
        "glyph_ranges": rendered,
    }


def _glsl_metadata(
    payload: bytes,
    role: str,
    expectations: Mapping[str, object],
) -> dict[str, object]:
    if not payload or len(payload) > MAX_GLSL_BYTES:
        _fail("candidate_media_invalid", "GLSL byte count is invalid")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail("candidate_media_invalid", f"GLSL is not UTF-8: {exc}")
    if text.startswith("\ufeff") or "\x00" in text or _URL_RE.search(text):
        _fail("candidate_media_invalid", "GLSL contains forbidden content")
    if re.search(r"^\s*#\s*include\b", text, re.MULTILINE):
        _fail("candidate_media_invalid", "GLSL contains an external include")
    line_count = len(text.splitlines())
    if line_count < 1 or line_count > expectations["max_lines"]:
        _fail("candidate_media_mismatch", "GLSL line count exceeds specification")
    expected_stage = "vertex" if role == "vertex_shader" else "fragment"
    if expectations["stage"] != expected_stage:
        _fail("candidate_media_mismatch", "GLSL role/stage mismatch")
    return {"kind": "glsl", "stage": expected_stage, "line_count": line_count}


def _json_metadata(payload: bytes, expectations: Mapping[str, object]) -> dict[str, object]:
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        _fail("candidate_media_invalid", f"JSON candidate is invalid: {exc}")
    if not isinstance(document, dict):
        _fail("candidate_media_invalid", "JSON candidate root must be an object")
    if set(document) != {"schema_id", "schema_version", "records"}:
        _fail("candidate_media_invalid", "JSON candidate shape is not closed")
    if (
        document["schema_id"] != expectations["schema_id"]
        or document["schema_version"] != expectations["schema_version"]
        or not isinstance(document["records"], list)
    ):
        _fail("candidate_media_mismatch", "JSON candidate does not match expected schema identity")
    if len(document["records"]) > min(expectations["max_records"], MAX_JSON_RECORDS):
        _fail("candidate_media_mismatch", "JSON candidate record count exceeds specification")
    _ensure_structure(document, "JSON candidate")
    return {
        "kind": "schema_json",
        "schema_id": document["schema_id"],
        "schema_version": document["schema_version"],
        "record_count": len(document["records"]),
    }


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_float(value: str) -> None:
    raise ValueError(f"decimal number is unsupported: {value}")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number is unsupported: {value}")


def _glb_metadata(payload: bytes, expectations: Mapping[str, object]) -> dict[str, object]:
    try:
        inspection = inspect_glb_bytes(
            payload,
            allow_external_uris=False,
            max_bytes=min(expectations["max_bytes"], MAX_CANDIDATE_BYTES),
        )
    except GLBError as exc:
        _fail("candidate_media_invalid", f"GLB is invalid: {exc}")
    production_metrics = inspection["production_metrics"]
    for field in (
        "nodes",
        "meshes",
        "primitives",
        "materials",
        "joints",
        "animations",
        "triangles",
    ):
        maximum = expectations[f"max_{field}"]
        if production_metrics[field] > maximum:
            _fail(
                "candidate_media_mismatch",
                f"GLB {field} budget exceeded: {production_metrics[field]} > {maximum}",
            )
    return {
        "kind": "glb",
        "metrics": production_metrics,
        "max_texture_dimension": inspection["max_texture_dimension"],
    }


def _inspect_candidate(
    payload: bytes,
    *,
    role: str,
    media_type: str,
    expectations: Mapping[str, object],
) -> dict[str, object]:
    if len(payload) > min(expectations["max_bytes"], MAX_CANDIDATE_BYTES):
        _fail("candidate_media_mismatch", "candidate exceeds the exact byte budget")
    if media_type == "image/png":
        return _png_metadata(payload, expectations)
    if media_type == "audio/wav":
        return _wav_metadata(payload, expectations)
    if media_type in {"font/ttf", "font/otf"}:
        return _font_metadata(payload, media_type, expectations)
    if media_type == "text/x-glsl":
        return _glsl_metadata(payload, role, expectations)
    if media_type == "application/json":
        return _json_metadata(payload, expectations)
    if media_type == "model/gltf-binary":
        return _glb_metadata(payload, expectations)
    _fail("candidate_media_invalid", f"unsupported candidate media type {media_type}")


def inspect_runtime_asset_bytes(
    payload: bytes,
    *,
    role: str,
    media_type: str,
    expectations: Mapping[str, object],
) -> dict[str, object]:
    """Inspect retained runtime bytes with the canonical generic-asset media rules."""

    if not isinstance(payload, bytes):
        _fail("candidate_media_invalid", "runtime asset payload must be bytes")
    return _inspect_candidate(
        payload,
        role=role,
        media_type=media_type,
        expectations=expectations,
    )


def _validate_metadata(value: object, media_type: str, context: str) -> dict[str, Any]:
    metadata = _object(value, context)
    fields = {
        "image/png": {"kind", "width", "height", "mode"},
        "audio/wav": {"kind", "channels", "sample_rate", "frames", "sample_width"},
        "font/ttf": {"kind", "container", "glyph_count", "glyph_ranges"},
        "font/otf": {"kind", "container", "glyph_count", "glyph_ranges"},
        "text/x-glsl": {"kind", "stage", "line_count"},
        "application/json": {"kind", "schema_id", "schema_version", "record_count"},
        "model/gltf-binary": {"kind", "metrics", "max_texture_dimension"},
    }.get(media_type)
    if fields is None:
        _fail("candidate_media_invalid", f"{context} media type is unsupported")
    _exact_keys(metadata, frozenset(fields), context)
    if media_type == "image/png":
        if metadata.get("kind") != "png":
            _fail("candidate_media_invalid", f"{context}.kind must be png")
        for field in ("width", "height"):
            metric = _integer(metadata.get(field), f"{context}.{field}", minimum=1)
            if metric > 16384:
                _fail("candidate_media_invalid", f"{context}.{field} exceeds 16384")
        if metadata.get("mode") not in {"rgba8", "rgb8", "grayscale8"}:
            _fail("candidate_media_invalid", f"{context}.mode is unsupported")
    elif media_type == "audio/wav":
        if metadata.get("kind") != "wav_pcm16":
            _fail("candidate_media_invalid", f"{context}.kind must be wav_pcm16")
        if metadata.get("channels") not in {1, 2}:
            _fail("candidate_media_invalid", f"{context}.channels is unsupported")
        sample_rate = _integer(metadata.get("sample_rate"), f"{context}.sample_rate", minimum=8000)
        if sample_rate > 192000:
            _fail("candidate_media_invalid", f"{context}.sample_rate exceeds 192000")
        frames = _integer(metadata.get("frames"), f"{context}.frames", minimum=1)
        if frames > 192000000:
            _fail("candidate_media_invalid", f"{context}.frames exceeds 192000000")
        if metadata.get("sample_width") != 2:
            _fail("candidate_media_invalid", f"{context}.sample_width must be 2")
    elif media_type in {"font/ttf", "font/otf"}:
        if metadata.get("kind") != "font":
            _fail("candidate_media_invalid", f"{context}.kind must be font")
        expected_container = "ttf" if media_type == "font/ttf" else "otf"
        if metadata.get("container") != expected_container:
            _fail(
                "candidate_media_invalid",
                f"{context}.container must be {expected_container} for {media_type}",
            )
        glyph_count = _integer(metadata.get("glyph_count"), f"{context}.glyph_count", minimum=1)
        if glyph_count > 1114112:
            _fail("candidate_media_invalid", f"{context}.glyph_count exceeds Unicode space")
        try:
            glyph_ranges = _validate_glyph_ranges(
                metadata.get("glyph_ranges"),
                f"{context}.glyph_ranges",
            )
        except GenericAssetError as exc:
            _fail("candidate_media_invalid", str(exc))
        covered = 0
        for glyph_range in glyph_ranges:
            start_text, end_text = glyph_range.removeprefix("U+").split("-", 1)
            covered += int(end_text, 16) - int(start_text, 16) + 1
        if glyph_count != covered:
            _fail(
                "candidate_media_invalid",
                f"{context}.glyph_count must equal the canonical covered codepoint count",
            )
    elif media_type == "text/x-glsl":
        if metadata.get("kind") != "glsl":
            _fail("candidate_media_invalid", f"{context}.kind must be glsl")
        if metadata.get("stage") not in {"vertex", "fragment"}:
            _fail("candidate_media_invalid", f"{context}.stage is unsupported")
        line_count = _integer(metadata.get("line_count"), f"{context}.line_count", minimum=1)
        if line_count > 65536:
            _fail("candidate_media_invalid", f"{context}.line_count exceeds 65536")
    elif media_type == "application/json":
        if metadata.get("kind") != "schema_json":
            _fail("candidate_media_invalid", f"{context}.kind must be schema_json")
        _bounded_text(metadata.get("schema_id"), f"{context}.schema_id", maximum=1024)
        schema_version = _integer(
            metadata.get("schema_version"), f"{context}.schema_version", minimum=1
        )
        if schema_version > 65535:
            _fail("candidate_media_invalid", f"{context}.schema_version exceeds 65535")
        record_count = _integer(metadata.get("record_count"), f"{context}.record_count", minimum=0)
        if record_count > 1000000:
            _fail("candidate_media_invalid", f"{context}.record_count exceeds 1000000")
    else:
        if metadata.get("kind") != "glb":
            _fail("candidate_media_invalid", f"{context}.kind must be glb")
        metrics = _object(metadata.get("metrics"), f"{context}.metrics")
        metric_fields = frozenset(
            {
                "nodes",
                "meshes",
                "primitives",
                "materials",
                "joints",
                "animations",
                "triangles",
            }
        )
        _exact_keys(metrics, metric_fields, f"{context}.metrics")
        for field in metric_fields:
            metric = _integer(metrics.get(field), f"{context}.metrics.{field}", minimum=0)
            if metric > 100000000:
                _fail("candidate_media_invalid", f"{context}.metrics.{field} exceeds limit")
        texture_dimension = _integer(
            metadata.get("max_texture_dimension"),
            f"{context}.max_texture_dimension",
            minimum=0,
        )
        if texture_dimension > 65536:
            _fail(
                "candidate_media_invalid",
                f"{context}.max_texture_dimension exceeds 65536",
            )
    return metadata


def _validate_receipt_output(value: object, context: str) -> dict[str, Any]:
    output = _object(value, context)
    _exact_keys(output, _RECEIPT_OUTPUT_FIELDS, context)
    _identifier(output.get("candidate_artifact_id"), f"{context}.candidate_artifact_id")
    role = _identifier(output.get("role"), f"{context}.role")
    media_type = _non_empty_string(output.get("media_type"), f"{context}.media_type")
    try:
        _portable_relative_path(output.get("runtime_path"), f"{context}.runtime_path")
        _portable_relative_path(output.get("locator"), f"{context}.locator")
    except CreationContractError as exc:
        _fail("production_artifact_path_invalid", str(exc))
    _integer(output.get("size_bytes"), f"{context}.size_bytes", minimum=1)
    if output["size_bytes"] > MAX_CANDIDATE_BYTES:
        _fail("production_contract_limit", f"{context}.size_bytes exceeds limit")
    _sha256(output.get("sha256"), f"{context}.sha256")
    metadata = _validate_metadata(output.get("metadata"), media_type, f"{context}.metadata")
    _validate_candidate_role_media(
        role,
        media_type,
        context=context,
        metadata=metadata,
    )
    return output


def _validate_candidate_role_media(
    role: str,
    media_type: str,
    *,
    context: str,
    metadata: Mapping[str, object] | None = None,
) -> None:
    accepted = _OUTPUT_MEDIA.get(role)
    if accepted is None or media_type not in accepted:
        _fail(
            "candidate_media_mismatch",
            f"{context} role/media type is unsupported",
        )
    if media_type == "text/x-glsl" and metadata is not None:
        expected_stage = "vertex" if role == "vertex_shader" else "fragment"
        if metadata.get("stage") != expected_stage:
            _fail(
                "candidate_media_mismatch",
                f"{context} GLSL role/stage mismatch",
            )


def _validate_parent_identities(value: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_PRODUCTION_PARENTS:
        _fail("production_contract_limit", f"{context} must be a bounded array")
    parents: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        item_context = f"{context}/{index}"
        parent = _object(raw, item_context)
        _exact_keys(parent, _PARENT_FIELDS, item_context)
        _identifier(parent.get("receipt_id"), f"{item_context}.receipt_id")
        _sha256(parent.get("content_hash"), f"{item_context}.content_hash")
        parents.append(parent)
    checked = _canonical_entries(
        parents,
        context,
        key="receipt_id",
        maximum=MAX_PRODUCTION_PARENTS,
    )
    _require_distinct_hashes(
        (
            (item["content_hash"], f"{context}/{index}.content_hash")
            for index, item in enumerate(checked)
        ),
        context,
    )
    return checked


def _validate_hash_array(value: object, context: str, *, allow_empty: bool) -> list[str]:
    values = _string_array(value, context, allow_empty=allow_empty, canonical_order=True)
    if len(values) > MAX_PRODUCTION_EVIDENCE:
        _fail("production_contract_limit", f"{context} exceeds limit")
    for index, item in enumerate(values):
        _sha256(item, f"{context}/{index}")
    return values


def _validate_receipt_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset production receipt")
    _exact_keys(document, _RECEIPT_FIELDS, "asset production receipt")
    if document.get("format") != ASSET_PRODUCTION_RECEIPT_FORMAT:
        _fail("production_receipt_format_invalid", "receipt format is unsupported")
    if document.get("format_version") != 1:
        _fail("production_receipt_version_invalid", "receipt version must be 1")
    _identifier(document.get("receipt_id"), "asset production receipt.receipt_id")
    for field, expected_format in (
        ("gamepack", "world-forge.gamepack"),
        ("asset_subject", ASSET_SUBJECT_FORMAT),
        ("target", ASSET_TARGET_FORMAT),
        ("style", ASSET_STYLE_FORMAT),
        ("inventory", ASSET_INVENTORY_FORMAT),
        ("specification", ASSET_SPEC_FORMAT),
        ("request", ASSET_PRODUCTION_REQUEST_FORMAT),
    ):
        _identity_value(
            document.get(field),
            f"asset production receipt.{field}",
            expected_format=expected_format,
        )
    asset = _object(document.get("asset"), "asset production receipt.asset")
    _exact_keys(asset, _ASSET_FIELDS, "asset production receipt.asset")
    _identifier(asset.get("asset_id"), "asset production receipt.asset.asset_id")
    _sha256(asset.get("content_hash"), "asset production receipt.asset.content_hash")
    production_class = document.get("production_class")
    if production_class not in _PRODUCTION_CLASSES:
        _fail("production_class_invalid", "receipt production_class is unsupported")
    status = document.get("status")
    if status not in {"completed", "failed"}:
        _fail("production_receipt_status_invalid", "receipt status is unsupported")
    request_identity = document["request"]
    assert isinstance(request_identity, dict)
    reproducibility = {"mode": "deterministic", "seed_policy": "forbidden"}
    toolchain = _object(document.get("executed_toolchain"), "receipt.executed_toolchain")
    seed_policy = toolchain.get("seed_policy")
    if production_class == "generative_authoring" and seed_policy in {"fixed", "recorded"}:
        reproducibility["seed_policy"] = seed_policy
    elif production_class == "procedural_offline" and toolchain.get("seed") is not None:
        reproducibility["seed_policy"] = "fixed"
    _validate_toolchain(
        toolchain,
        "receipt.executed_toolchain",
        production_class=production_class,
        operation_id=str(toolchain.get("operation_id", "")),
        reproducibility=reproducibility,
    )
    inputs = _validate_input_artifacts(document.get("input_artifacts"), "receipt.input_artifacts")
    outputs_value = document.get("outputs")
    if not isinstance(outputs_value, list) or len(outputs_value) > MAX_PRODUCTION_OUTPUTS:
        _fail("production_contract_limit", "receipt.outputs must be bounded")
    outputs = [
        _validate_receipt_output(item, f"receipt.outputs/{index}")
        for index, item in enumerate(outputs_value)
    ]
    _canonical_entries(
        outputs,
        "receipt.outputs",
        key="role",
        maximum=MAX_PRODUCTION_OUTPUTS,
    )
    candidate_ids = [item["candidate_artifact_id"] for item in outputs]
    if len({item.casefold() for item in candidate_ids}) != len(candidate_ids):
        _fail("production_contract_collision", "receipt output candidate IDs collide")
    _portable_path_tree([item["locator"] for item in outputs], "receipt.outputs")
    parents = _validate_parent_identities(
        document.get("lineage_parents"), "receipt.lineage_parents"
    )
    _require_distinct_hashes(
        [
            *[
                (item["sha256"], f"receipt.input_artifacts/{index}.sha256")
                for index, item in enumerate(inputs)
            ],
            *[
                (item["content_hash"], f"receipt.lineage_parents/{index}.content_hash")
                for index, item in enumerate(parents)
            ],
            *[
                (item["sha256"], f"receipt.outputs/{index}.sha256")
                for index, item in enumerate(outputs)
            ],
            (document.get("content_hash"), "asset production receipt.content_hash"),
        ],
        "asset production receipt lineage",
    )
    execution = _object(document.get("execution_evidence"), "receipt.execution_evidence")
    _exact_keys(execution, _EXECUTION_EVIDENCE_FIELDS, "receipt.execution_evidence")
    _sha256(execution.get("started_evidence_hash"), "receipt.execution_evidence.started")
    _sha256(execution.get("completed_evidence_hash"), "receipt.execution_evidence.completed")
    _validate_hash_array(
        execution.get("sanitized_log_hashes"),
        "receipt.execution_evidence.sanitized_log_hashes",
        allow_empty=True,
    )
    rights = _object(document.get("rights_attestation"), "receipt.rights_attestation")
    _exact_keys(rights, _RIGHTS_ATTESTATION_FIELDS, "receipt.rights_attestation")
    _identifier(rights.get("basis"), "receipt.rights_attestation.basis")
    _validate_hash_array(
        rights.get("evidence_hashes"),
        "receipt.rights_attestation.evidence_hashes",
        allow_empty=False,
    )
    failures = _string_array(
        document.get("failure_reasons"),
        "receipt.failure_reasons",
        allow_empty=status == "completed",
        canonical_order=True,
    )
    if len(failures) > MAX_PRODUCTION_EVIDENCE:
        _fail("production_contract_limit", "receipt.failure_reasons exceeds limit")
    for index, reason in enumerate(failures):
        _identifier(reason, f"receipt.failure_reasons/{index}")
    if status == "completed" and (not outputs or failures):
        _fail(
            "production_receipt_status_invalid",
            "completed receipt requires outputs and no failures",
        )
    if status == "failed" and (outputs or not failures):
        _fail(
            "production_receipt_status_invalid", "failed receipt requires no outputs and failures"
        )
    _ensure_structure(document, "asset production receipt")
    _validate_hash(document, "asset production receipt")
    return copy.deepcopy(document)


def _expected_receipt_outputs(
    request: Mapping[str, object],
    candidates: object,
    *,
    artifact_root: str | Path,
) -> list[dict[str, Any]]:
    if not isinstance(candidates, list) or len(candidates) > MAX_PRODUCTION_OUTPUTS:
        _fail("production_contract_limit", "candidate output set must be bounded")
    by_role: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(candidates):
        context = f"candidate outputs/{index}"
        candidate = _object(raw, context)
        _exact_keys(candidate, frozenset({"role", "candidate_artifact_id", "locator"}), context)
        role = _identifier(candidate.get("role"), f"{context}.role")
        _identifier(candidate.get("candidate_artifact_id"), f"{context}.candidate_artifact_id")
        if role in by_role:
            _fail("production_output_mismatch", f"duplicate candidate role {role}")
        by_role[role] = candidate
    result: list[dict[str, Any]] = []
    expected_outputs = request["expected_outputs"]
    assert isinstance(expected_outputs, list)
    for expected in expected_outputs:
        assert isinstance(expected, dict)
        role = expected["role"]
        candidate = by_role.pop(role, None)
        if candidate is None:
            _fail("production_output_mismatch", f"candidate role {role} is missing")
        payload = _safe_artifact_bytes(
            artifact_root,
            candidate["locator"],
            limit=min(expected["expectations"]["max_bytes"], MAX_CANDIDATE_BYTES),
        )
        metadata = _inspect_candidate(
            payload,
            role=role,
            media_type=expected["media_type"],
            expectations=expected["expectations"],
        )
        result.append(
            {
                "candidate_artifact_id": candidate["candidate_artifact_id"],
                "locator": candidate["locator"],
                "media_type": expected["media_type"],
                "metadata": metadata,
                "role": role,
                "runtime_path": expected["runtime_path"],
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )
    if by_role:
        _fail("production_output_mismatch", "candidate output set has extra roles")
    return sorted(result, key=lambda item: item["role"].encode("utf-8"))


def _receipt_graph(
    receipt: Mapping[str, object],
    parent_receipts: Sequence[Mapping[str, object]],
) -> None:
    if len(parent_receipts) > MAX_PRODUCTION_PARENTS:
        _fail("production_lineage_limit", "receipt parent graph exceeds limit")
    by_hash: dict[str, Mapping[str, object]] = {}
    by_id: dict[str, str] = {}
    for parent in parent_receipts:
        checked = validate_asset_production_receipt_document(parent)
        digest = checked["content_hash"]
        identifier = checked["receipt_id"]
        if digest in by_hash or identifier.casefold() in by_id:
            _fail("production_lineage_duplicate", "receipt graph contains duplicate identities")
        by_hash[digest] = checked
        by_id[identifier.casefold()] = digest
    root_hash = str(receipt["content_hash"])
    root_id = str(receipt["receipt_id"])
    if root_hash in by_hash or root_id.casefold() in by_id:
        _fail(
            "production_lineage_duplicate",
            "receipt graph repeats the root identity in its parent set",
        )
    by_hash[root_hash] = receipt
    by_id[root_id.casefold()] = root_hash
    for node in tuple(by_hash.values()):
        parents = node["lineage_parents"]
        assert isinstance(parents, list)
        for parent in parents:
            assert isinstance(parent, dict)
            known = by_hash.get(parent["content_hash"])
            if known is None or known["receipt_id"] != parent["receipt_id"]:
                _fail("production_lineage_unknown_parent", "receipt graph has an unknown parent")
    shared_inputs = _receipt_graph_inputs(tuple(by_hash.values()))
    identity_domains: list[tuple[str, str]] = []
    hash_domains: list[tuple[object, str]] = []
    for node in by_hash.values():
        receipt_id = str(node["receipt_id"])
        identity_domains.append((receipt_id, f"receipt {receipt_id}"))
        hash_domains.append((node["content_hash"], f"receipt {receipt_id}.content_hash"))
    for item in shared_inputs:
        artifact_id = str(item["artifact_id"])
        identity_domains.append((artifact_id, f"input {artifact_id}"))
        hash_domains.append((item["sha256"], f"input {artifact_id}.sha256"))
    outputs = receipt["outputs"]
    assert isinstance(outputs, list)
    for output in outputs:
        assert isinstance(output, dict)
        candidate_id = str(output["candidate_artifact_id"])
        identity_domains.append((candidate_id, f"candidate {candidate_id}"))
        hash_domains.append((output["sha256"], f"candidate {candidate_id}.sha256"))
    seen_ids: dict[str, str] = {}
    for identifier, source in identity_domains:
        folded = unicodedata.normalize("NFC", identifier).casefold()
        previous = seen_ids.get(folded)
        if previous is not None:
            _fail(
                "production_lineage_duplicate",
                f"receipt graph reuses node identity across {previous} and {source}",
            )
        seen_ids[folded] = source
    _require_distinct_hashes(hash_domains, "receipt graph provenance hashes")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(digest: str) -> None:
        if digest in visiting:
            _fail("production_lineage_cycle", "receipt graph contains a cycle")
        if digest in visited:
            return
        visiting.add(digest)
        node = by_hash[digest]
        parents = node["lineage_parents"]
        assert isinstance(parents, list)
        for parent in parents:
            assert isinstance(parent, dict)
            visit(parent["content_hash"])
        visiting.remove(digest)
        visited.add(digest)

    visit(root_hash)
    if visited != set(by_hash):
        _fail(
            "production_lineage_unreferenced_parent",
            "receipt graph contains parent receipts outside the root transitive closure",
        )


def _receipt_graph_inputs(
    receipt_nodes: Sequence[Mapping[str, object]],
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_hash: dict[str, str] = {}
    for node in receipt_nodes:
        inputs = node["input_artifacts"]
        assert isinstance(inputs, list)
        for item in inputs:
            assert isinstance(item, dict)
            artifact_id = str(item["artifact_id"])
            key = unicodedata.normalize("NFC", artifact_id).casefold()
            checked = copy.deepcopy(item)
            existing = by_id.get(key)
            if existing is not None:
                if existing != checked:
                    _fail(
                        "production_lineage_duplicate",
                        f"receipt graph input identity {artifact_id} is inconsistent",
                    )
                continue
            digest = str(item["sha256"])
            previous_id = by_hash.get(digest)
            if previous_id is not None:
                _fail(
                    "production_lineage_duplicate",
                    "receipt graph input content hash is reused by "
                    f"{previous_id} and {artifact_id}",
                )
            by_id[key] = checked
            by_hash[digest] = artifact_id
    if len(by_id) > MAX_PRODUCTION_INPUTS:
        _fail(
            "production_lineage_limit",
            "receipt graph unique input artifacts exceed the provenance bound",
        )
    result = sorted(by_id.values(), key=lambda item: item["artifact_id"].encode("utf-8"))
    _portable_path_tree([item["locator"] for item in result], "receipt graph inputs")
    return result


def build_asset_production_receipt(
    request: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    receipt_id: str,
    status: str,
    executed_toolchain: object,
    candidates: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    execution_evidence: object,
    rights_attestation: object,
    failure_reasons: object = (),
) -> dict[str, Any]:
    checked_request = validate_asset_production_request(
        request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    if status == "completed":
        outputs = _expected_receipt_outputs(
            checked_request,
            candidates,
            artifact_root=artifact_root,
        )
    else:
        if not isinstance(candidates, list) or candidates:
            _fail(
                "production_receipt_status_invalid",
                "a non-completed receipt cannot discard candidate evidence",
            )
        outputs = []
    parents = [
        {
            "receipt_id": parent["receipt_id"],
            "content_hash": parent["content_hash"],
        }
        for parent in parent_receipts
    ]
    parents.sort(key=lambda item: item["receipt_id"].encode("utf-8"))
    document = {
        "format": ASSET_PRODUCTION_RECEIPT_FORMAT,
        "format_version": 1,
        "receipt_id": receipt_id,
        **{
            key: copy.deepcopy(checked_request[key])
            for key in (
                "gamepack",
                "asset_subject",
                "target",
                "style",
                "inventory",
                "specification",
                "asset",
            )
        },
        "request": _production_identity(checked_request, "request_id"),
        "production_class": checked_request["production_class"],
        "status": status,
        "executed_toolchain": copy.deepcopy(executed_toolchain),
        "input_artifacts": copy.deepcopy(checked_request["input_artifacts"]),
        "outputs": outputs,
        "lineage_parents": parents,
        "execution_evidence": copy.deepcopy(execution_evidence),
        "rights_attestation": copy.deepcopy(rights_attestation),
        "failure_reasons": sorted(
            list(failure_reasons), key=lambda item: str(item).encode("utf-8")
        ),
        "content_hash": "",
    }
    receipt = validate_asset_production_receipt_document(_seal(document))
    return validate_asset_production_receipt(
        receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        parent_receipts=parent_receipts,
    )


def validate_asset_production_receipt_document(value: object) -> dict[str, Any]:
    try:
        return _validate_receipt_structure(value)
    except GenericAssetProductionError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("production_receipt_invalid", str(exc))


def _validate_receipt_against_request(
    receipt: dict[str, Any],
    checked_request: Mapping[str, object],
    *,
    artifact_root: str | Path,
) -> dict[str, Any]:
    for key in (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "asset",
        "production_class",
    ):
        if receipt[key] != checked_request[key]:
            _fail("production_lineage_mismatch", f"receipt.{key} does not match request")
    if receipt["request"] != _production_identity(checked_request, "request_id"):
        _fail("production_lineage_mismatch", "receipt request identity is crossed")
    if receipt["executed_toolchain"] != checked_request["toolchain_requirements"]:
        _fail("production_toolchain_mismatch", "receipt toolchain does not satisfy exact request")
    if receipt["input_artifacts"] != checked_request["input_artifacts"]:
        _fail("production_input_mismatch", "receipt inputs do not match exact request")
    _validate_input_artifact_bytes(receipt["input_artifacts"], artifact_root)
    if receipt["status"] == "completed":
        expected = _expected_receipt_outputs(
            checked_request,
            [
                {
                    "role": output["role"],
                    "candidate_artifact_id": output["candidate_artifact_id"],
                    "locator": output["locator"],
                }
                for output in receipt["outputs"]
            ],
            artifact_root=artifact_root,
        )
        if receipt["outputs"] != expected:
            _fail("production_output_mismatch", "receipt output metadata is not byte-derived")
    return receipt


def validate_asset_production_receipt(
    value: object,
    *,
    request: object,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
) -> dict[str, Any]:
    receipt = validate_asset_production_receipt_document(value)
    checked_request = validate_asset_production_request(
        request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    _validate_receipt_against_request(
        receipt,
        checked_request,
        artifact_root=artifact_root,
    )
    checked_parents = []
    for parent in parent_receipts:
        checked_parent = _validate_receipt_against_request(
            validate_asset_production_receipt_document(parent),
            checked_request,
            artifact_root=artifact_root,
        )
        if checked_parent["status"] != "completed":
            _fail(
                "production_lineage_parent_failed",
                "receipt lineage parents must be completed",
            )
        checked_parents.append(checked_parent)
    _receipt_graph(receipt, checked_parents)
    return receipt


def _normalize_receipt_parent_closures(
    root_receipts: Sequence[Mapping[str, object]],
    *,
    parent_receipts: Sequence[Mapping[str, object]],
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None,
) -> dict[str, tuple[dict[str, Any], ...]]:
    roots = [validate_asset_production_receipt_document(root) for root in root_receipts]
    root_by_id: dict[str, dict[str, Any]] = {}
    for root in roots:
        root_id = str(root["receipt_id"])
        if root_id in root_by_id:
            _fail(
                "production_lineage_duplicate",
                "receipt lineage roots contain duplicate identities",
            )
        root_by_id[root_id] = root
    if not root_by_id:
        _fail("production_lineage_invalid", "receipt lineage requires at least one root")

    if receipt_parent_closures is None:
        first_root_id = str(roots[0]["receipt_id"])
        raw_closures: dict[str, Sequence[Mapping[str, object]]] = {
            root_id: parent_receipts if root_id == first_root_id else () for root_id in root_by_id
        }
        for root_id, root in root_by_id.items():
            if root_id != first_root_id and root["lineage_parents"]:
                _fail(
                    "production_lineage_ambiguous",
                    "each additional receipt root with parents requires an explicit closure",
                )
    else:
        if parent_receipts:
            _fail(
                "production_lineage_ambiguous",
                "legacy parent_receipts cannot be combined with explicit receipt closures",
            )
        if not isinstance(receipt_parent_closures, Mapping):
            _fail("production_lineage_invalid", "receipt closures must be an object")
        if any(not isinstance(root_id, str) for root_id in receipt_parent_closures):
            _fail("production_lineage_invalid", "receipt closure keys must be receipt IDs")
        if set(receipt_parent_closures) != set(root_by_id):
            _fail(
                "production_lineage_root_mismatch",
                "receipt closures must exactly cover selected and rejected roots",
            )
        raw_closures = dict(receipt_parent_closures)

    normalized: dict[str, tuple[dict[str, Any], ...]] = {}
    global_parent_by_id: dict[str, dict[str, Any]] = {}
    for root_id in sorted(root_by_id, key=lambda item: item.encode("utf-8")):
        raw_parents = raw_closures[root_id]
        if (
            not isinstance(raw_parents, Sequence)
            or isinstance(raw_parents, (str, bytes, bytearray))
            or len(raw_parents) > MAX_PRODUCTION_PARENTS
        ):
            _fail(
                "production_lineage_limit",
                f"receipt closure {root_id} must be a bounded parent array",
            )
        checked_parents = tuple(
            validate_asset_production_receipt_document(parent) for parent in raw_parents
        )
        for parent in checked_parents:
            parent_id = str(parent["receipt_id"])
            if parent_id in root_by_id:
                _fail(
                    "production_lineage_cycle",
                    "receipt roots cannot also appear as lineage parents",
                )
            existing = global_parent_by_id.get(parent_id)
            if existing is not None and existing != parent:
                _fail(
                    "production_lineage_duplicate",
                    "shared receipt parents must be byte-identical contracts",
                )
            global_parent_by_id[parent_id] = parent
        normalized[root_id] = checked_parents
    if len(global_parent_by_id) > MAX_PRODUCTION_PARENTS:
        _fail(
            "production_lineage_limit",
            "combined receipt parent closures exceed the global limit",
        )
    return normalized


def _receipt_lineage_document(
    root_receipts: Sequence[Mapping[str, object]],
    closures: Mapping[str, Sequence[Mapping[str, object]]],
) -> dict[str, Any]:
    roots = {
        str(root["receipt_id"]): validate_asset_production_receipt_document(root)
        for root in root_receipts
    }
    return {
        "format": ASSET_RECEIPT_LINEAGE_FORMAT,
        "format_version": 1,
        "closures": [
            {
                "root": _production_identity(roots[root_id], "receipt_id"),
                "parents": [
                    _production_identity(parent, "receipt_id")
                    for parent in sorted(
                        closures[root_id],
                        key=lambda item: str(item["receipt_id"]).encode("utf-8"),
                    )
                ],
            }
            for root_id in sorted(roots, key=lambda item: item.encode("utf-8"))
        ],
    }


def _validate_receipt_lineage_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset selection.receipt_lineage")
    _exact_keys(document, _RECEIPT_LINEAGE_FIELDS, "asset selection.receipt_lineage")
    if (
        document.get("format") != ASSET_RECEIPT_LINEAGE_FORMAT
        or document.get("format_version") != 1
    ):
        _fail(
            "production_lineage_invalid",
            "asset selection receipt lineage format/version is unsupported",
        )
    closures_value = document.get("closures")
    if (
        not isinstance(closures_value, list)
        or not closures_value
        or len(closures_value) > MAX_PRODUCTION_OUTPUTS * 8 + 1
    ):
        _fail(
            "production_lineage_limit",
            "asset selection receipt lineage closures must be bounded and non-empty",
        )
    closures: list[dict[str, Any]] = []
    root_ids: set[str] = set()
    for index, raw in enumerate(closures_value):
        context = f"asset selection.receipt_lineage.closures/{index}"
        closure = _object(raw, context)
        _exact_keys(closure, _RECEIPT_CLOSURE_FIELDS, context)
        root = _identity_value(
            closure.get("root"),
            f"{context}.root",
            expected_format=ASSET_PRODUCTION_RECEIPT_FORMAT,
        )
        root_id = str(root["id"])
        if root_id in root_ids:
            _fail(
                "production_lineage_duplicate",
                "asset selection receipt lineage has duplicate roots",
            )
        root_ids.add(root_id)
        parents_value = closure.get("parents")
        if not isinstance(parents_value, list) or len(parents_value) > MAX_PRODUCTION_PARENTS:
            _fail(
                "production_lineage_limit",
                f"{context}.parents must be a bounded array",
            )
        parents = [
            _identity_value(
                parent,
                f"{context}.parents/{parent_index}",
                expected_format=ASSET_PRODUCTION_RECEIPT_FORMAT,
            )
            for parent_index, parent in enumerate(parents_value)
        ]
        expected_parents = sorted(
            parents,
            key=lambda item: str(item["id"]).encode("utf-8"),
        )
        if parents != expected_parents:
            _fail(
                "production_contract_noncanonical",
                f"{context}.parents must be canonical",
            )
        if len({str(parent["id"]).casefold() for parent in parents}) != len(parents):
            _fail(
                "production_lineage_duplicate",
                f"{context}.parents contains duplicate IDs",
            )
        _require_distinct_hashes(
            (
                (parent["content_hash"], f"{context}.parents/{parent_index}.content_hash")
                for parent_index, parent in enumerate(parents)
            ),
            f"{context}.parents",
        )
        closures.append({"root": root, "parents": parents})
    expected = sorted(
        closures,
        key=lambda item: str(item["root"]["id"]).encode("utf-8"),
    )
    if closures != expected:
        _fail(
            "production_contract_noncanonical",
            "asset selection receipt lineage closures must be canonical",
        )
    return copy.deepcopy(document)


def _validate_selection_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset selection")
    _exact_keys(document, _SELECTION_FIELDS, "asset selection")
    if document.get("format") != ASSET_SELECTION_FORMAT or document.get("format_version") != 1:
        _fail("asset_selection_format_invalid", "selection format/version is unsupported")
    _identifier(document.get("selection_id"), "asset selection.selection_id")
    for field, expected in (
        ("gamepack", "world-forge.gamepack"),
        ("asset_subject", ASSET_SUBJECT_FORMAT),
        ("target", ASSET_TARGET_FORMAT),
        ("style", ASSET_STYLE_FORMAT),
        ("inventory", ASSET_INVENTORY_FORMAT),
        ("specification", ASSET_SPEC_FORMAT),
        ("request", ASSET_PRODUCTION_REQUEST_FORMAT),
        ("receipt", ASSET_PRODUCTION_RECEIPT_FORMAT),
    ):
        _identity_value(document.get(field), f"asset selection.{field}", expected_format=expected)
    asset = _object(document.get("asset"), "asset selection.asset")
    _exact_keys(asset, _ASSET_FIELDS, "asset selection.asset")
    _identifier(asset.get("asset_id"), "asset selection.asset.asset_id")
    _sha256(asset.get("content_hash"), "asset selection.asset.content_hash")
    _validate_receipt_lineage_structure(document.get("receipt_lineage"))
    selected_value = document.get("selected_outputs")
    if not isinstance(selected_value, list) or not selected_value:
        _fail("asset_selection_invalid", "selection requires selected outputs")
    selected = []
    for index, raw in enumerate(selected_value):
        context = f"asset selection.selected_outputs/{index}"
        item = _object(raw, context)
        _exact_keys(item, _SELECTED_OUTPUT_FIELDS, context)
        _identifier(item.get("candidate_artifact_id"), f"{context}.candidate_artifact_id")
        role = _identifier(item.get("role"), f"{context}.role")
        media_type = _non_empty_string(item.get("media_type"), f"{context}.media_type")
        _validate_candidate_role_media(role, media_type, context=context)
        _integer(item.get("size_bytes"), f"{context}.size_bytes", minimum=1)
        _sha256(item.get("sha256"), f"{context}.sha256")
        selected.append(item)
    _canonical_entries(
        selected,
        "asset selection.selected_outputs",
        key="role",
        maximum=MAX_PRODUCTION_OUTPUTS,
        allow_empty=False,
    )
    selected_ids = [item["candidate_artifact_id"] for item in selected]
    if len({item.casefold() for item in selected_ids}) != len(selected_ids):
        _fail("production_contract_collision", "selected candidate IDs collide")
    _require_distinct_hashes(
        (
            (item["sha256"], f"asset selection.selected_outputs/{index}.sha256")
            for index, item in enumerate(selected)
        ),
        "asset selection selected output hashes",
    )
    rejected_value = document.get("rejected_candidates")
    if not isinstance(rejected_value, list) or len(rejected_value) > MAX_PRODUCTION_OUTPUTS * 8:
        _fail("production_contract_limit", "rejected candidates must be bounded")
    rejected = []
    for index, raw in enumerate(rejected_value):
        context = f"asset selection.rejected_candidates/{index}"
        item = _object(raw, context)
        _exact_keys(item, _REJECTED_FIELDS, context)
        for field in ("candidate_artifact_id", "reason_code"):
            _identifier(item.get(field), f"{context}.{field}")
        _identity_value(
            item.get("receipt"),
            f"{context}.receipt",
            expected_format=ASSET_PRODUCTION_RECEIPT_FORMAT,
        )
        rejected.append(item)
    _canonical_entries(
        rejected,
        "asset selection.rejected_candidates",
        key="candidate_artifact_id",
        maximum=MAX_PRODUCTION_OUTPUTS * 8,
    )
    review = _object(document.get("review"), "asset selection.review")
    _exact_keys(review, _REVIEW_FIELDS, "asset selection.review")
    _identifier(review.get("reviewer_id"), "asset selection.review.reviewer_id")
    _bounded_text(review.get("rationale"), "asset selection.review.rationale")
    _validate_hash_array(
        review.get("evidence_hashes"),
        "asset selection.review.evidence_hashes",
        allow_empty=False,
    )
    _validate_hash(document, "asset selection")
    return copy.deepcopy(document)


def build_asset_selection(
    receipt: object,
    *,
    request: object,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
    selection_id: str,
    review: object,
    rejected_candidates: object = (),
) -> dict[str, Any]:
    checked_request = validate_asset_production_request(
        request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    root_receipts = [
        validate_asset_production_receipt_document(receipt),
        *[validate_asset_production_receipt_document(candidate) for candidate in rejected_receipts],
    ]
    closures = _normalize_receipt_parent_closures(
        root_receipts,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
    )
    selected_root_id = str(root_receipts[0]["receipt_id"])
    checked_receipt = validate_asset_production_receipt(
        receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        parent_receipts=closures[selected_root_id],
    )
    if checked_receipt["status"] != "completed":
        _fail("asset_selection_failed_receipt", "failed receipt outputs cannot be selected")
    selected_outputs = [
        {
            key: output[key]
            for key in ("candidate_artifact_id", "role", "media_type", "size_bytes", "sha256")
        }
        for output in checked_receipt["outputs"]
    ]
    document = {
        "format": ASSET_SELECTION_FORMAT,
        "format_version": 1,
        "selection_id": selection_id,
        **{
            key: copy.deepcopy(checked_request[key])
            for key in (
                "gamepack",
                "asset_subject",
                "target",
                "style",
                "inventory",
                "specification",
                "asset",
            )
        },
        "request": _production_identity(checked_request, "request_id"),
        "receipt": _production_identity(checked_receipt, "receipt_id"),
        "receipt_lineage": _receipt_lineage_document(root_receipts, closures),
        "selected_outputs": selected_outputs,
        "rejected_candidates": sorted(
            copy.deepcopy(list(rejected_candidates)),
            key=lambda item: str(item.get("candidate_artifact_id", "")).encode("utf-8"),
        ),
        "review": copy.deepcopy(review),
        "content_hash": "",
    }
    selection = validate_asset_selection_document(_seal(document))
    return validate_asset_selection(
        selection,
        receipt=checked_receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        receipt_parent_closures=closures,
        rejected_receipts=rejected_receipts,
    )


def validate_asset_selection_document(value: object) -> dict[str, Any]:
    try:
        return _validate_selection_structure(value)
    except GenericAssetProductionError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_selection_invalid", str(exc))


def validate_asset_selection(
    value: object,
    *,
    receipt: object,
    request: object,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
) -> dict[str, Any]:
    selection = validate_asset_selection_document(value)
    checked_request = validate_asset_production_request(
        request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    root_receipts = [
        validate_asset_production_receipt_document(receipt),
        *[validate_asset_production_receipt_document(candidate) for candidate in rejected_receipts],
    ]
    closures = _normalize_receipt_parent_closures(
        root_receipts,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
    )
    selected_root_id = str(root_receipts[0]["receipt_id"])
    checked_receipt = validate_asset_production_receipt(
        root_receipts[0],
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        parent_receipts=closures[selected_root_id],
    )
    if checked_receipt["status"] != "completed":
        _fail("asset_selection_failed_receipt", "selection cannot bind a failed receipt")
    for key in (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "asset",
    ):
        if selection[key] != checked_request[key]:
            _fail("production_lineage_mismatch", f"selection.{key} is crossed")
    if selection["request"] != _production_identity(checked_request, "request_id"):
        _fail("production_lineage_mismatch", "selection request identity is crossed")
    if selection["receipt"] != _production_identity(checked_receipt, "receipt_id"):
        _fail("production_lineage_mismatch", "selection receipt identity is crossed")
    expected_lineage = _receipt_lineage_document(root_receipts, closures)
    if selection["receipt_lineage"] != expected_lineage:
        _fail(
            "production_lineage_mismatch",
            "selection receipt lineage does not exactly cover every root closure",
        )
    expected = [
        {
            key: output[key]
            for key in ("candidate_artifact_id", "role", "media_type", "size_bytes", "sha256")
        }
        for output in checked_receipt["outputs"]
    ]
    if selection["selected_outputs"] != expected:
        _fail("asset_selection_mismatch", "selection does not exactly cover receipt/spec outputs")
    selected_ids = {item["candidate_artifact_id"].casefold() for item in expected}
    if any(
        item["candidate_artifact_id"].casefold() in selected_ids
        for item in selection["rejected_candidates"]
    ):
        _fail("asset_selection_mismatch", "selected candidate is also rejected")
    if len(rejected_receipts) > MAX_PRODUCTION_OUTPUTS * 8:
        _fail("production_contract_limit", "rejected receipt evidence exceeds limit")
    rejected_by_id: dict[str, dict[str, Any]] = {}
    main_identity = _production_identity(checked_receipt, "receipt_id")
    for raw in root_receipts[1:]:
        rejected_root_id = str(raw["receipt_id"])
        rejected_receipt = validate_asset_production_receipt(
            raw,
            request=checked_request,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
            inventory=inventory,
            specification=specification,
            artifact_root=artifact_root,
            parent_receipts=closures[rejected_root_id],
        )
        if rejected_receipt["status"] != "completed":
            _fail(
                "asset_selection_rejected_evidence_invalid",
                "rejected candidate evidence must come from a completed receipt",
            )
        identity = _production_identity(rejected_receipt, "receipt_id")
        if identity == main_identity:
            _fail(
                "asset_selection_rejected_evidence_invalid",
                "selected receipt cannot also be rejected evidence",
            )
        key = str(identity["id"]).casefold()
        if key in rejected_by_id:
            _fail(
                "asset_selection_rejected_evidence_invalid",
                "rejected receipt evidence contains duplicate identities",
            )
        rejected_by_id[key] = rejected_receipt
    referenced_receipts: set[str] = set()
    for rejected in selection["rejected_candidates"]:
        identity = rejected["receipt"]
        key = str(identity["id"]).casefold()
        rejected_receipt = rejected_by_id.get(key)
        if rejected_receipt is None or identity != _production_identity(
            rejected_receipt, "receipt_id"
        ):
            _fail(
                "asset_selection_rejected_evidence_missing",
                "rejected candidate is not bound to its exact integral receipt",
            )
        if not any(
            output["candidate_artifact_id"] == rejected["candidate_artifact_id"]
            for output in rejected_receipt["outputs"]
        ):
            _fail(
                "asset_selection_rejected_evidence_missing",
                "rejected candidate is absent from its receipt outputs",
            )
        referenced_receipts.add(key)
    if referenced_receipts != set(rejected_by_id):
        _fail(
            "asset_selection_rejected_evidence_unreferenced",
            "rejected receipt evidence contains unreferenced receipts",
        )
    return selection


def _expected_component_keys(
    production_class: str,
    request: Mapping[str, object],
) -> list[tuple[str, str, str]]:
    toolchain = request["toolchain_requirements"]
    assert isinstance(toolchain, dict)
    if production_class == "human":
        result = [
            ("creator", toolchain["creator_id"], "not_applicable"),
            ("original_work", request["asset"]["asset_id"], "not_applicable"),
            ("source_rights", request["asset"]["asset_id"], "not_applicable"),
        ]
    elif production_class == "procedural_offline":
        result = [("generator_tool", toolchain["tool_id"], toolchain["tool_version"])]
    elif production_class == "external_authoring":
        result = [
            ("authoring_tool", toolchain["tool_id"], toolchain["tool_version"]),
            ("source_rights", request["asset"]["asset_id"], "not_applicable"),
        ]
    else:
        result = [
            ("provider", toolchain["provider_id"], "not_applicable"),
            ("authoring_tool", toolchain["tool_id"], toolchain["tool_version"]),
            ("model", toolchain["model_id"], toolchain["model_version"]),
            ("weights", toolchain["weights_id"], toolchain["weights_version"]),
            *[("dataset", dataset_id, "not_applicable") for dataset_id in toolchain["dataset_ids"]],
            ("source_rights", request["asset"]["asset_id"], "not_applicable"),
        ]
    if request["input_artifacts"]:
        result.append(("input_license", "request_inputs", "not_applicable"))
    return sorted(result, key=lambda item: (item[0].encode(), item[1].encode()))


def _validate_components(value: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_PRODUCTION_COMPONENTS:
        _fail("production_contract_limit", f"{context} must be a bounded non-empty array")
    result = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        item_context = f"{context}/{index}"
        item = _object(raw, item_context)
        _exact_keys(item, _COMPONENT_FIELDS, item_context)
        scope = _identifier(item.get("scope"), f"{item_context}.scope")
        component_id = _identifier(item.get("component_id"), f"{item_context}.component_id")
        version = item.get("component_version")
        if version != "not_applicable":
            _semver(version, f"{item_context}.component_version")
        _sha256(item.get("evidence_hash"), f"{item_context}.evidence_hash")
        key = (scope.casefold(), component_id.casefold())
        if key in seen:
            _fail("production_contract_collision", f"{context} has duplicate components")
        seen.add(key)
        result.append(item)
    expected = sorted(
        result, key=lambda item: (item["scope"].encode(), item["component_id"].encode())
    )
    if result != expected:
        _fail("production_contract_noncanonical", f"{context} must use canonical order")
    return result


def _derive_lineage(
    receipt: Mapping[str, object],
    selection: Mapping[str, object],
    parent_receipts: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    checked_parents = [
        validate_asset_production_receipt_document(parent) for parent in parent_receipts
    ]
    receipt_nodes = [*checked_parents, receipt]
    inputs = _receipt_graph_inputs(receipt_nodes)
    root_inputs = receipt["input_artifacts"]
    assert isinstance(root_inputs, list)
    receipt_parents = receipt["lineage_parents"]
    assert isinstance(receipt_parents, list)
    parent_hashes = sorted(
        [item["sha256"] for item in root_inputs]
        + [item["content_hash"] for item in receipt_parents]
    )
    candidates = [
        {
            "candidate_artifact_id": item["candidate_artifact_id"],
            "role": item["role"],
            "media_type": item["media_type"],
            "sha256": item["sha256"],
        }
        for item in selection["selected_outputs"]
    ]
    candidates.sort(key=lambda item: item["candidate_artifact_id"].encode("utf-8"))
    nodes = [
        {
            "node_id": item["artifact_id"],
            "content_hash": item["sha256"],
            "parent_hashes": [],
        }
        for item in inputs
    ]
    nodes.extend(
        {
            "node_id": node["receipt_id"],
            "content_hash": node["content_hash"],
            "parent_hashes": sorted(
                [item["sha256"] for item in node["input_artifacts"]]
                + [item["content_hash"] for item in node["lineage_parents"]]
            ),
        }
        for node in checked_parents
    )
    nodes.extend(
        {
            "node_id": item["candidate_artifact_id"],
            "content_hash": item["sha256"],
            "parent_hashes": parent_hashes,
        }
        for item in candidates
    )
    nodes.sort(key=lambda item: item["node_id"].encode("utf-8"))
    return candidates, nodes


def _validate_provenance_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset provenance record")
    _exact_keys(document, _PROVENANCE_FIELDS, "asset provenance record")
    if document.get("format") != ASSET_PROVENANCE_FORMAT or document.get("format_version") != 1:
        _fail("asset_provenance_format_invalid", "provenance format/version is unsupported")
    _identifier(document.get("provenance_id"), "asset provenance record.provenance_id")
    for field, expected in (
        ("gamepack", "world-forge.gamepack"),
        ("asset_subject", ASSET_SUBJECT_FORMAT),
        ("target", ASSET_TARGET_FORMAT),
        ("style", ASSET_STYLE_FORMAT),
        ("inventory", ASSET_INVENTORY_FORMAT),
        ("specification", ASSET_SPEC_FORMAT),
        ("request", ASSET_PRODUCTION_REQUEST_FORMAT),
        ("receipt", ASSET_PRODUCTION_RECEIPT_FORMAT),
        ("selection", ASSET_SELECTION_FORMAT),
    ):
        _identity_value(
            document.get(field), f"asset provenance record.{field}", expected_format=expected
        )
    asset = _object(document.get("asset"), "asset provenance record.asset")
    _exact_keys(asset, _ASSET_FIELDS, "asset provenance record.asset")
    _identifier(asset.get("asset_id"), "asset provenance record.asset.asset_id")
    _sha256(asset.get("content_hash"), "asset provenance record.asset.content_hash")
    production_class = document.get("production_class")
    if production_class not in _PRODUCTION_CLASSES:
        _fail("production_class_invalid", "provenance production class is unsupported")
    toolchain = _object(document.get("toolchain"), "asset provenance record.toolchain")
    reproducibility = {"mode": "deterministic", "seed_policy": "forbidden"}
    if production_class == "generative_authoring":
        reproducibility["seed_policy"] = toolchain.get("seed_policy")
    elif production_class == "procedural_offline" and toolchain.get("seed") is not None:
        reproducibility["seed_policy"] = "fixed"
    _validate_toolchain(
        toolchain,
        "asset provenance record.toolchain",
        production_class=production_class,
        operation_id=str(toolchain.get("operation_id", "")),
        reproducibility=reproducibility,
    )
    _validate_components(document.get("components"), "asset provenance record.components")
    candidates_value = document.get("candidates")
    if not isinstance(candidates_value, list) or not candidates_value:
        _fail("asset_provenance_invalid", "provenance candidates must be non-empty")
    candidates = []
    for index, raw in enumerate(candidates_value):
        context = f"asset provenance record.candidates/{index}"
        item = _object(raw, context)
        _exact_keys(item, _PROVENANCE_CANDIDATE_FIELDS, context)
        _identifier(item.get("candidate_artifact_id"), f"{context}.candidate_artifact_id")
        role = _identifier(item.get("role"), f"{context}.role")
        media_type = _non_empty_string(item.get("media_type"), f"{context}.media_type")
        _validate_candidate_role_media(role, media_type, context=context)
        _sha256(item.get("sha256"), f"{context}.sha256")
        candidates.append(item)
    _canonical_entries(
        candidates,
        "asset provenance record.candidates",
        key="candidate_artifact_id",
        maximum=MAX_PRODUCTION_OUTPUTS,
        allow_empty=False,
    )
    _require_distinct_hashes(
        (
            (item["sha256"], f"asset provenance record.candidates/{index}.sha256")
            for index, item in enumerate(candidates)
        ),
        "asset provenance candidate hashes",
    )
    lineage_value = document.get("lineage")
    if not isinstance(lineage_value, list) or len(lineage_value) > (
        MAX_PRODUCTION_INPUTS + MAX_PRODUCTION_PARENTS + MAX_PRODUCTION_OUTPUTS
    ):
        _fail("production_lineage_limit", "provenance lineage exceeds limit")
    lineage = []
    hashes: set[str] = set()
    for index, raw in enumerate(lineage_value):
        context = f"asset provenance record.lineage/{index}"
        item = _object(raw, context)
        _exact_keys(item, _LINEAGE_NODE_FIELDS, context)
        _identifier(item.get("node_id"), f"{context}.node_id")
        digest = _sha256(item.get("content_hash"), f"{context}.content_hash")
        if digest in hashes:
            _fail("production_lineage_duplicate", "provenance lineage hashes must be unique")
        hashes.add(digest)
        _validate_hash_array(
            item.get("parent_hashes"), f"{context}.parent_hashes", allow_empty=True
        )
        lineage.append(item)
    _canonical_entries(
        lineage,
        "asset provenance record.lineage",
        key="node_id",
        maximum=MAX_PRODUCTION_INPUTS + MAX_PRODUCTION_PARENTS + MAX_PRODUCTION_OUTPUTS,
    )
    for item in lineage:
        for parent_hash in item["parent_hashes"]:
            if parent_hash not in hashes:
                _fail("production_lineage_unknown_parent", "provenance has unknown parent hash")
    graph = {item["content_hash"]: item["parent_hashes"] for item in lineage}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(digest: str) -> None:
        if digest in visiting:
            _fail("production_lineage_cycle", "provenance lineage contains a cycle")
        if digest in visited:
            return
        visiting.add(digest)
        for parent in graph[digest]:
            visit(parent)
        visiting.remove(digest)
        visited.add(digest)

    for digest in graph:
        visit(digest)
    _validate_hash(document, "asset provenance record")
    return copy.deepcopy(document)


def build_asset_provenance_record(
    selection: object,
    *,
    receipt: object,
    request: object,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
    provenance_id: str,
    component_evidence: object,
) -> dict[str, Any]:
    checked_request = validate_asset_production_request(
        request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    root_receipts = [
        validate_asset_production_receipt_document(receipt),
        *[validate_asset_production_receipt_document(candidate) for candidate in rejected_receipts],
    ]
    closures = _normalize_receipt_parent_closures(
        root_receipts,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
    )
    selected_root_id = str(root_receipts[0]["receipt_id"])
    checked_receipt = validate_asset_production_receipt(
        root_receipts[0],
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        parent_receipts=closures[selected_root_id],
    )
    checked_selection = validate_asset_selection(
        selection,
        receipt=checked_receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        receipt_parent_closures=closures,
        rejected_receipts=rejected_receipts,
    )
    components = copy.deepcopy(component_evidence)
    if not isinstance(components, list):
        _fail("asset_provenance_invalid", "component evidence must be an array")
    components.sort(
        key=lambda item: (
            str(item.get("scope", "")).encode(),
            str(item.get("component_id", "")).encode(),
        )
    )
    expected_components = _expected_component_keys(
        checked_request["production_class"],
        checked_request,
    )
    actual_components = [
        (item.get("scope"), item.get("component_id"), item.get("component_version"))
        for item in components
    ]
    if actual_components != expected_components:
        _fail(
            "asset_provenance_component_mismatch",
            "provenance components are not the mechanically required toolchain scopes",
        )
    candidates, lineage = _derive_lineage(
        checked_receipt,
        checked_selection,
        closures[selected_root_id],
    )
    document = {
        "format": ASSET_PROVENANCE_FORMAT,
        "format_version": 1,
        "provenance_id": provenance_id,
        **{
            key: copy.deepcopy(checked_request[key])
            for key in (
                "gamepack",
                "asset_subject",
                "target",
                "style",
                "inventory",
                "specification",
                "asset",
            )
        },
        "request": _production_identity(checked_request, "request_id"),
        "receipt": _production_identity(checked_receipt, "receipt_id"),
        "selection": _production_identity(checked_selection, "selection_id"),
        "production_class": checked_request["production_class"],
        "toolchain": copy.deepcopy(checked_receipt["executed_toolchain"]),
        "components": components,
        "candidates": candidates,
        "lineage": lineage,
        "content_hash": "",
    }
    provenance = validate_asset_provenance_record_document(_seal(document))
    return validate_asset_provenance_record(
        provenance,
        selection=checked_selection,
        receipt=checked_receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        receipt_parent_closures=closures,
        rejected_receipts=rejected_receipts,
    )


def validate_asset_provenance_record_document(value: object) -> dict[str, Any]:
    try:
        return _validate_provenance_structure(value)
    except GenericAssetProductionError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_provenance_invalid", str(exc))


def validate_asset_provenance_record(
    value: object,
    *,
    selection: object,
    receipt: object,
    request: object,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
) -> dict[str, Any]:
    provenance = validate_asset_provenance_record_document(value)
    checked_request = validate_asset_production_request(
        request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    root_receipts = [
        validate_asset_production_receipt_document(receipt),
        *[validate_asset_production_receipt_document(candidate) for candidate in rejected_receipts],
    ]
    closures = _normalize_receipt_parent_closures(
        root_receipts,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
    )
    selected_root_id = str(root_receipts[0]["receipt_id"])
    checked_receipt = validate_asset_production_receipt(
        root_receipts[0],
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        parent_receipts=closures[selected_root_id],
    )
    checked_selection = validate_asset_selection(
        selection,
        receipt=checked_receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        receipt_parent_closures=closures,
        rejected_receipts=rejected_receipts,
    )
    for key in (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "asset",
        "production_class",
    ):
        if provenance[key] != checked_request[key]:
            _fail("production_lineage_mismatch", f"provenance.{key} is crossed")
    for field, document, id_field in (
        ("request", checked_request, "request_id"),
        ("receipt", checked_receipt, "receipt_id"),
        ("selection", checked_selection, "selection_id"),
    ):
        if provenance[field] != _production_identity(document, id_field):
            _fail("production_lineage_mismatch", f"provenance.{field} is crossed")
    if provenance["toolchain"] != checked_receipt["executed_toolchain"]:
        _fail("asset_provenance_toolchain_mismatch", "provenance toolchain was caller-invented")
    expected_keys = _expected_component_keys(provenance["production_class"], checked_request)
    actual_keys = [
        (item["scope"], item["component_id"], item["component_version"])
        for item in provenance["components"]
    ]
    if actual_keys != expected_keys:
        _fail("asset_provenance_component_mismatch", "provenance component scopes are crossed")
    candidates, lineage = _derive_lineage(
        checked_receipt,
        checked_selection,
        closures[selected_root_id],
    )
    if provenance["candidates"] != candidates or provenance["lineage"] != lineage:
        _fail("production_lineage_mismatch", "provenance lineage is not derived from selection")
    return provenance


def _license_identifier(value: object, context: str) -> str:
    identifier = _non_empty_string(value, context)
    if _SPDX_RE.fullmatch(identifier) is None and _CUSTOM_LICENSE_RE.fullmatch(identifier) is None:
        _fail("asset_license_identifier_invalid", f"{context} is not SPDX/custom")
    return identifier


def _validate_license_structure(value: object) -> dict[str, Any]:
    document = _object(value, "asset license record")
    _exact_keys(document, _LICENSE_FIELDS, "asset license record")
    if document.get("format") != ASSET_LICENSE_FORMAT or document.get("format_version") != 1:
        _fail("asset_license_format_invalid", "license format/version is unsupported")
    _identifier(document.get("license_record_id"), "asset license record.license_record_id")
    for field, expected in (
        ("gamepack", "world-forge.gamepack"),
        ("asset_subject", ASSET_SUBJECT_FORMAT),
        ("target", ASSET_TARGET_FORMAT),
        ("style", ASSET_STYLE_FORMAT),
        ("inventory", ASSET_INVENTORY_FORMAT),
        ("specification", ASSET_SPEC_FORMAT),
        ("request", ASSET_PRODUCTION_REQUEST_FORMAT),
        ("receipt", ASSET_PRODUCTION_RECEIPT_FORMAT),
        ("selection", ASSET_SELECTION_FORMAT),
        ("provenance", ASSET_PROVENANCE_FORMAT),
    ):
        _identity_value(
            document.get(field), f"asset license record.{field}", expected_format=expected
        )
    asset = _object(document.get("asset"), "asset license record.asset")
    _exact_keys(asset, _ASSET_FIELDS, "asset license record.asset")
    _identifier(asset.get("asset_id"), "asset license record.asset.asset_id")
    _sha256(asset.get("content_hash"), "asset license record.asset.content_hash")
    candidate = _object(document.get("candidate"), "asset license record.candidate")
    _exact_keys(candidate, _PROVENANCE_CANDIDATE_FIELDS, "asset license record.candidate")
    _identifier(candidate.get("candidate_artifact_id"), "asset license record.candidate.id")
    candidate_role = _identifier(candidate.get("role"), "asset license record.candidate.role")
    candidate_media_type = _non_empty_string(
        candidate.get("media_type"),
        "asset license record.candidate.media_type",
    )
    _validate_candidate_role_media(
        candidate_role,
        candidate_media_type,
        context="asset license record.candidate",
    )
    _sha256(candidate.get("sha256"), "asset license record.candidate.sha256")
    basis = _object(document.get("license_basis"), "asset license record.license_basis")
    _exact_keys(basis, _LICENSE_BASIS_FIELDS, "asset license record.license_basis")
    if basis.get("kind") not in {"spdx", "custom"}:
        _fail("asset_license_identifier_invalid", "license basis kind is unsupported")
    identifier = _license_identifier(
        basis.get("identifier"),
        "asset license record.license_basis.identifier",
    )
    if basis["kind"] == "custom" and not identifier.startswith("LicenseRef-"):
        _fail("asset_license_identifier_invalid", "custom license must use LicenseRef")
    if basis["kind"] == "custom" and identifier not in _APPROVED_CUSTOM_LICENSES:
        _fail("asset_license_identifier_invalid", "custom license is not approved")
    if basis["kind"] == "spdx" and identifier.startswith("LicenseRef-"):
        _fail("asset_license_identifier_invalid", "SPDX basis cannot use LicenseRef")
    copyright_value = _object(document.get("copyright"), "asset license record.copyright")
    _exact_keys(copyright_value, _COPYRIGHT_FIELDS, "asset license record.copyright")
    _bounded_text(copyright_value.get("holder"), "asset license record.copyright.holder")
    if copyright_value.get("year_policy") not in {"fixed", "not_applicable"}:
        _fail("asset_license_invalid", "copyright year policy is unsupported")
    year = copyright_value.get("year")
    if copyright_value["year_policy"] == "fixed":
        _integer(year, "asset license record.copyright.year", minimum=1900)
        if year > 9999:
            _fail("asset_license_invalid", "copyright year exceeds 9999")
    elif year is not None:
        _fail("asset_license_invalid", "not-applicable copyright year must be null")
    permissions = _object(document.get("permissions"), "asset license record.permissions")
    _exact_keys(permissions, _PERMISSION_FIELDS, "asset license record.permissions")
    obligations = _object(document.get("obligations"), "asset license record.obligations")
    _exact_keys(obligations, _OBLIGATION_FIELDS, "asset license record.obligations")
    for container, fields in (
        (permissions, _PERMISSION_FIELDS),
        (obligations, _OBLIGATION_FIELDS),
    ):
        if any(not isinstance(container.get(field), bool) for field in fields):
            _fail("asset_license_invalid", "license permission/obligation values must be boolean")
    component_value = document.get("component_licenses")
    if (
        not isinstance(component_value, list)
        or not component_value
        or len(component_value) > MAX_PRODUCTION_COMPONENTS
    ):
        _fail("asset_license_invalid", "component licenses must be bounded and non-empty")
    component_licenses = []
    component_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(component_value):
        context = f"asset license record.component_licenses/{index}"
        item = _object(raw, context)
        _exact_keys(item, _COMPONENT_LICENSE_FIELDS, context)
        scope = _identifier(item.get("scope"), f"{context}.scope")
        component_id = _identifier(item.get("component_id"), f"{context}.component_id")
        identifier = _license_identifier(item.get("identifier"), f"{context}.identifier")
        if "LicenseRef-" in identifier and identifier not in _APPROVED_CUSTOM_LICENSES:
            _fail(
                "asset_license_identifier_invalid",
                f"{context}.identifier custom license is not approved",
            )
        _sha256(item.get("evidence_hash"), f"{context}.evidence_hash")
        key = (scope.casefold(), component_id.casefold())
        if key in component_keys:
            _fail("production_contract_collision", "component licenses contain duplicates")
        component_keys.add(key)
        component_licenses.append(item)
    expected_order = sorted(
        component_licenses,
        key=lambda item: (item["scope"].encode(), item["component_id"].encode()),
    )
    if component_licenses != expected_order:
        _fail("production_contract_noncanonical", "component licenses must be canonical")
    notice = _object(document.get("runtime_notice"), "asset license record.runtime_notice")
    _exact_keys(notice, _NOTICE_FIELDS, "asset license record.runtime_notice")
    text = _bounded_text(notice.get("text"), "asset license record.runtime_notice.text")
    _sha256(notice.get("sha256"), "asset license record.runtime_notice.sha256")
    if notice["sha256"] != hashlib.sha256(text.encode("utf-8")).hexdigest():
        _fail("asset_license_notice_mismatch", "runtime notice hash is not exact")
    if _NOTICE_AUTHORING_RE.search(text):
        _fail("asset_license_notice_unsafe", "runtime notice leaks authoring-only detail")
    if _NOTICE_SECRET_RE.search(text) or _contains_standalone_jwt(text):
        _fail("asset_license_notice_unsafe", "runtime notice contains credential-like data")
    _validate_hash_array(
        document.get("evidence_hashes"),
        "asset license record.evidence_hashes",
        allow_empty=False,
    )
    _validate_hash(document, "asset license record")
    return copy.deepcopy(document)


def build_asset_license_record(
    provenance: object,
    *,
    selection: object,
    receipt: object,
    request: object,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
    license_record_id: str,
    candidate_artifact_id: str,
    license_basis: object,
    copyright: object,
    permissions: object,
    obligations: object,
    component_licenses: object,
    runtime_notice_text: str,
    evidence_hashes: object,
) -> dict[str, Any]:
    _preflight_runtime_notice_text(
        runtime_notice_text,
        "asset license record.runtime_notice.text",
    )
    checked_request = validate_asset_production_request(
        request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    root_receipts = [
        validate_asset_production_receipt_document(receipt),
        *[validate_asset_production_receipt_document(candidate) for candidate in rejected_receipts],
    ]
    closures = _normalize_receipt_parent_closures(
        root_receipts,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
    )
    selected_root_id = str(root_receipts[0]["receipt_id"])
    checked_receipt = validate_asset_production_receipt(
        root_receipts[0],
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        parent_receipts=closures[selected_root_id],
    )
    checked_selection = validate_asset_selection(
        selection,
        receipt=checked_receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        receipt_parent_closures=closures,
        rejected_receipts=rejected_receipts,
    )
    checked_provenance = validate_asset_provenance_record(
        provenance,
        selection=checked_selection,
        receipt=checked_receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        receipt_parent_closures=closures,
        rejected_receipts=rejected_receipts,
    )
    candidate = next(
        (
            item
            for item in checked_provenance["candidates"]
            if item["candidate_artifact_id"] == candidate_artifact_id
        ),
        None,
    )
    if candidate is None:
        _fail("asset_license_candidate_mismatch", "candidate is not selected provenance output")
    licenses = copy.deepcopy(component_licenses)
    if not isinstance(licenses, list):
        _fail("asset_license_invalid", "component_licenses must be an array")
    licenses.sort(
        key=lambda item: (
            str(item.get("scope", "")).encode(),
            str(item.get("component_id", "")).encode(),
        )
    )
    document = {
        "format": ASSET_LICENSE_FORMAT,
        "format_version": 1,
        "license_record_id": license_record_id,
        **{
            key: copy.deepcopy(checked_request[key])
            for key in (
                "gamepack",
                "asset_subject",
                "target",
                "style",
                "inventory",
                "specification",
                "asset",
            )
        },
        "request": _production_identity(checked_request, "request_id"),
        "receipt": _production_identity(checked_receipt, "receipt_id"),
        "selection": _production_identity(checked_selection, "selection_id"),
        "provenance": _production_identity(checked_provenance, "provenance_id"),
        "candidate": copy.deepcopy(candidate),
        "license_basis": copy.deepcopy(license_basis),
        "copyright": copy.deepcopy(copyright),
        "permissions": copy.deepcopy(permissions),
        "obligations": copy.deepcopy(obligations),
        "component_licenses": licenses,
        "runtime_notice": {
            "text": runtime_notice_text,
            "sha256": hashlib.sha256(runtime_notice_text.encode("utf-8")).hexdigest(),
        },
        "evidence_hashes": sorted(list(evidence_hashes)),
        "content_hash": "",
    }
    license_record = validate_asset_license_record_document(_seal(document))
    return validate_asset_license_record(
        license_record,
        provenance=checked_provenance,
        selection=checked_selection,
        receipt=checked_receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        receipt_parent_closures=closures,
        rejected_receipts=rejected_receipts,
    )


def validate_asset_license_record_document(value: object) -> dict[str, Any]:
    try:
        _preflight_asset_license_notice(value)
        return _validate_license_structure(value)
    except GenericAssetProductionError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("asset_license_invalid", str(exc))


def validate_asset_license_record(
    value: object,
    *,
    provenance: object,
    selection: object,
    receipt: object,
    request: object,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
) -> dict[str, Any]:
    license_record = validate_asset_license_record_document(value)
    checked_request = validate_asset_production_request(
        request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
    )
    root_receipts = [
        validate_asset_production_receipt_document(receipt),
        *[validate_asset_production_receipt_document(candidate) for candidate in rejected_receipts],
    ]
    closures = _normalize_receipt_parent_closures(
        root_receipts,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
    )
    selected_root_id = str(root_receipts[0]["receipt_id"])
    checked_receipt = validate_asset_production_receipt(
        root_receipts[0],
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        parent_receipts=closures[selected_root_id],
    )
    checked_selection = validate_asset_selection(
        selection,
        receipt=checked_receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        receipt_parent_closures=closures,
        rejected_receipts=rejected_receipts,
    )
    checked_provenance = validate_asset_provenance_record(
        provenance,
        selection=checked_selection,
        receipt=checked_receipt,
        request=checked_request,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        artifact_root=artifact_root,
        receipt_parent_closures=closures,
        rejected_receipts=rejected_receipts,
    )
    for key in (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "specification",
        "asset",
    ):
        if license_record[key] != checked_request[key]:
            _fail("production_lineage_mismatch", f"license.{key} is crossed")
    for field, document, id_field in (
        ("request", checked_request, "request_id"),
        ("receipt", checked_receipt, "receipt_id"),
        ("selection", checked_selection, "selection_id"),
        ("provenance", checked_provenance, "provenance_id"),
    ):
        if license_record[field] != _production_identity(document, id_field):
            _fail("production_lineage_mismatch", f"license.{field} is crossed")
    expected_components = [
        (item["scope"], item["component_id"]) for item in checked_provenance["components"]
    ]
    actual_components = [
        (item["scope"], item["component_id"]) for item in license_record["component_licenses"]
    ]
    if actual_components != expected_components:
        _fail(
            "asset_license_component_mismatch",
            "component license scopes are missing, extra, or inapplicable",
        )
    if license_record["candidate"] not in checked_provenance["candidates"]:
        _fail("asset_license_candidate_mismatch", "license candidate is not selected")
    return license_record


def serialize_production_contract(value: object) -> bytes:
    if not isinstance(value, Mapping):
        _fail("production_contract_invalid", "production contract must be an object")
    validators = {
        ASSET_PRODUCTION_REQUEST_FORMAT: validate_asset_production_request_document,
        ASSET_PRODUCTION_RECEIPT_FORMAT: validate_asset_production_receipt_document,
        ASSET_SELECTION_FORMAT: validate_asset_selection_document,
        ASSET_PROVENANCE_FORMAT: validate_asset_provenance_record_document,
        ASSET_LICENSE_FORMAT: validate_asset_license_record_document,
    }
    validator = validators.get(value.get("format"))
    if validator is None:
        _fail("production_contract_format_invalid", "production contract format is unsupported")
    return canonical_json_bytes(validator(value))


def _read_contract(
    path: str | Path,
    validator: Any,
    *,
    preflight: Any = None,
) -> dict[str, Any]:
    try:
        return validator(read_creation_object(path, preflight=preflight))
    except GenericAssetProductionError:
        raise
    except (CreationContractError, OSError, TypeError, ValueError) as exc:
        _fail("production_contract_read_failed", str(exc))


def load_asset_production_request(path: str | Path, **chain: object) -> dict[str, Any]:
    return validate_asset_production_request(
        _read_contract(path, validate_asset_production_request_document),
        **chain,
    )


def load_asset_production_receipt(
    path: str | Path,
    *,
    request: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    **chain: object,
) -> dict[str, Any]:
    return validate_asset_production_receipt(
        _read_contract(path, validate_asset_production_receipt_document),
        request=request,
        artifact_root=artifact_root,
        parent_receipts=parent_receipts,
        **chain,
    )


def load_asset_selection(
    path: str | Path,
    *,
    receipt: object,
    request: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
    **chain: object,
) -> dict[str, Any]:
    return validate_asset_selection(
        _read_contract(path, validate_asset_selection_document),
        receipt=receipt,
        request=request,
        artifact_root=artifact_root,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
        rejected_receipts=rejected_receipts,
        **chain,
    )


def load_asset_provenance_record(
    path: str | Path,
    *,
    selection: object,
    receipt: object,
    request: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
    **chain: object,
) -> dict[str, Any]:
    return validate_asset_provenance_record(
        _read_contract(path, validate_asset_provenance_record_document),
        selection=selection,
        receipt=receipt,
        request=request,
        artifact_root=artifact_root,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
        rejected_receipts=rejected_receipts,
        **chain,
    )


def load_asset_license_record(
    path: str | Path,
    *,
    provenance: object,
    selection: object,
    receipt: object,
    request: object,
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
    **chain: object,
) -> dict[str, Any]:
    return validate_asset_license_record(
        _read_contract(
            path,
            validate_asset_license_record_document,
            preflight=_preflight_asset_license_notice,
        ),
        provenance=provenance,
        selection=selection,
        receipt=receipt,
        request=request,
        artifact_root=artifact_root,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
        rejected_receipts=rejected_receipts,
        **chain,
    )


def _publish(path: str | Path, document: Mapping[str, Any]) -> PublishedGameArtifact:
    try:
        destination = preflight_game_artifact_output(path)
        write_json_atomic(destination, document, durable_parent=True)
        return _published_artifact(destination, document)
    except (AssetContractError, GamepackError, OSError) as exc:
        reason = "output_exists" if "overwrite" in str(exc).casefold() else "output_publish_failed"
        _fail(reason, str(exc))


def publish_asset_production_request(
    path: str | Path,
    value: object,
    **chain: object,
) -> PublishedGameArtifact:
    return _publish(path, validate_asset_production_request(value, **chain))


def publish_asset_production_receipt(
    path: str | Path,
    value: object,
    **lineage: object,
) -> PublishedGameArtifact:
    return _publish(path, validate_asset_production_receipt(value, **lineage))


def publish_asset_selection(
    path: str | Path,
    value: object,
    **lineage: object,
) -> PublishedGameArtifact:
    return _publish(path, validate_asset_selection(value, **lineage))


def publish_asset_provenance_record(
    path: str | Path,
    value: object,
    **lineage: object,
) -> PublishedGameArtifact:
    return _publish(path, validate_asset_provenance_record(value, **lineage))


def publish_asset_license_record(
    path: str | Path,
    value: object,
    **lineage: object,
) -> PublishedGameArtifact:
    return _publish(path, validate_asset_license_record(value, **lineage))
