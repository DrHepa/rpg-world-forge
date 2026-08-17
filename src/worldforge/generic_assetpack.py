from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from isoworld.content.portability import is_portable_path_component
from worldforge.asset_io import (
    AssetContractError,
    PinnedOutputParent,
    open_verified_output_parent,
)
from worldforge.creation_contracts import (
    CreationContractError,
    _decode_creation_object,
    _exact_keys,
    _identifier,
    _integer,
    _object,
    _portable_relative_path,
    _sha256,
    _validate_json_structure,
    canonical_creation_hash,
)
from worldforge.directory_publish import (
    DirectoryIdentity,
    DirectoryPublishError,
    DirectoryPublishIndeterminateError,
    DirectoryPublishRecoveryRequiredError,
    append_append_only_journal,
    create_append_only_journal,
    directory_identity,
    fsync_directory,
    publish_directory_noreplace,
    quarantine_and_remove_verified_directory,
    read_append_only_journal_history_state,
    remove_d3_append_only_journal,
    remove_verified_empty_directory,
    require_pinned_names_absent,
    retained_journal_evidence_path,
    retained_recovery_evidence,
)
from worldforge.file_stat import (
    FileStat,
    descriptor_file_stat,
    file_identity,
    is_link_or_reparse,
    path_file_stat,
    windows_handle_file_stat,
)
from worldforge.game_boundary_policy import validate_lexical_directory_root
from worldforge.generic_asset_processing import (
    ASSET_MANIFEST_FORMAT,
    ASSET_PROCESSING_RECEIPT_FORMAT,
    ASSET_PROCESSING_RECIPE_FORMAT,
    ASSET_QA_REPORT_FORMAT,
    GenericAssetProcessingError,
    validate_asset_manifest,
)
from worldforge.generic_asset_production import (
    ASSET_LICENSE_FORMAT,
    ASSET_PRODUCTION_RECEIPT_FORMAT,
    ASSET_PRODUCTION_REQUEST_FORMAT,
    ASSET_PROVENANCE_FORMAT,
    ASSET_SELECTION_FORMAT,
    GenericAssetProductionError,
    _portable_path_tree,
    _safe_artifact_bytes,
    _validate_metadata,
    inspect_runtime_asset_bytes,
    read_verified_artifact_bytes,
)
from worldforge.generic_assets import (
    _OUTPUT_MEDIA,
    ASSET_INVENTORY_FORMAT,
    ASSET_SPEC_FORMAT,
    ASSET_STYLE_FORMAT,
    ASSET_SUBJECT_FORMAT,
    ASSET_TARGET_FORMAT,
    GenericAssetError,
    _validate_spec_output,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.repository_boundary import (
    RepositoryBoundaryError,
    assert_new_repository_target,
)

GENERIC_ASSETPACK_FORMAT = "world-forge.assetpack"
GENERIC_ASSETPACK_VERSION = 1
GENERIC_ASSETPACK_MANIFEST = "assetpack.json"
GENERIC_ASSETPACK_NOTICE_DIRECTORY = "notices"
GENERIC_ASSETPACK_JOURNAL_FORMAT = "world-forge.assetpack_publication_journal"
GENERIC_ASSETPACK_JOURNAL_VERSION = 1

MAX_GENERIC_ASSETPACK_ASSETS = 1024
MAX_GENERIC_ASSETPACK_OUTPUTS = 4096
MAX_GENERIC_ASSETPACK_FILES = 8192
MAX_GENERIC_ASSETPACK_DIRECTORIES = 8192
MAX_GENERIC_ASSETPACK_TREE_NODES = (
    MAX_GENERIC_ASSETPACK_FILES + MAX_GENERIC_ASSETPACK_DIRECTORIES + 2
)
MAX_GENERIC_ASSETPACK_DEPTH = 32
MAX_GENERIC_ASSETPACK_TOTAL_BYTES = 512 * 1024 * 1024
MAX_GENERIC_ASSETPACK_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_GENERIC_ASSETPACK_JOURNAL_BYTES = 16 * MAX_GENERIC_ASSETPACK_MANIFEST_BYTES
MAX_GENERIC_ASSETPACK_NOTICE_BYTES = 4096
MAX_GENERIC_ASSETPACK_NOTICE_CHARACTERS = 4096

_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_ASSET_FIELDS = frozenset({"asset_id", "content_hash"})
_NOTICE_FIELDS = frozenset({"path", "sha256", "size_bytes"})
_FILE_FIELDS = frozenset({"path", "sha256", "size_bytes"})
_OUTPUT_FIELDS = frozenset(
    {
        "role",
        "media_type",
        "runtime_path",
        "sha256",
        "size_bytes",
        "constraints",
        "metadata",
        "license_record",
        "runtime_notice",
    }
)
_ASSET_ENTRY_FIELDS = frozenset(
    {
        "asset",
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
        "processing_recipe",
        "processing_receipt",
        "qa_report",
        "licenses",
        "outputs",
    }
)
_INVENTORY_FIELDS = frozenset({"file_count", "total_bytes", "files", "content_hash"})
_MANIFEST_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "assetpack_id",
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "asset_inventory",
        "release_ready_manifest",
        "state",
        "assets",
        "inventory",
        "content_hash",
    }
)
_DIRECTORY_IDENTITY_FIELDS = frozenset({"device", "inode"})
_JOURNAL_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "stage_name",
        "destination_name",
        "stage_identity",
        "assetpack_id",
        "content_hash",
        "inventory_hash",
        "source_manifest_hash",
        "manifest_sha256",
        "manifest_size_bytes",
    }
)


class GenericAssetpackError(ValueError):
    """Raised when a generic runtime assetpack fails its closed contract."""

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        recovery_evidence: Mapping[str, object] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.detail = detail
        self.recovery_evidence = copy.deepcopy(dict(recovery_evidence or {}))
        super().__init__(f"{reason_code}: {detail}")


def _fail(
    reason_code: str,
    detail: str,
    *,
    recovery_evidence: Mapping[str, object] | None = None,
) -> None:
    raise GenericAssetpackError(
        reason_code,
        detail,
        recovery_evidence=recovery_evidence,
    )


def _indeterminate_directory_error(
    error: BaseException,
) -> DirectoryPublishIndeterminateError | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, DirectoryPublishIndeterminateError):
            return current
        current = current.__cause__
    return None


def _hash(document: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(document)
    except CreationContractError as exc:
        _fail("assetpack_contract_invalid", str(exc))


def _seal(document: dict[str, Any]) -> dict[str, Any]:
    document["content_hash"] = _hash(document)
    return document


def _document_identity(
    document: Mapping[str, object],
    id_field: str,
) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
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
    if identity.get("format") != expected_format:
        _fail(
            "assetpack_lineage_mismatch",
            f"{context}.format must be {expected_format}",
        )
    if identity.get("format_version") != 1:
        _fail(
            "assetpack_lineage_mismatch",
            f"{context}.format_version must be 1",
        )
    _identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def _assetpack_id_seed(document: Mapping[str, object]) -> dict[str, object]:
    return {
        "gamepack": document["gamepack"],
        "asset_subject": document["asset_subject"],
        "target": document["target"],
        "style": document["style"],
        "asset_inventory": document["asset_inventory"],
        "release_ready_manifest": document["release_ready_manifest"],
        "assets": document["assets"],
        "inventory": document["inventory"],
    }


def _derived_assetpack_id(document: Mapping[str, object]) -> str:
    return f"assetpack_{_hash(_assetpack_id_seed(document))[:48]}"


def _record_by_asset_id(
    asset_records: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    records: dict[str, Mapping[str, object]] = {}
    for raw_record in asset_records:
        specification = raw_record.get("specification")
        if not isinstance(specification, Mapping):
            _fail(
                "assetpack_lineage_mismatch",
                "asset record specification is missing",
            )
        asset = specification.get("asset")
        if not isinstance(asset, Mapping) or not isinstance(asset.get("asset_id"), str):
            _fail("assetpack_lineage_mismatch", "asset record identity is missing")
        asset_id = asset["asset_id"]
        if asset_id in records:
            _fail("assetpack_asset_collision", "asset records contain duplicate IDs")
        records[asset_id] = raw_record
    return records


def _runtime_notice(
    license_record: Mapping[str, object],
) -> tuple[dict[str, object], bytes]:
    notice = license_record.get("runtime_notice")
    if not isinstance(notice, Mapping):
        _fail("assetpack_notice_invalid", "license runtime notice is missing")
    text = notice.get("text")
    declared_hash = notice.get("sha256")
    if not isinstance(text, str):
        _fail("assetpack_notice_invalid", "license runtime notice must be text")
    try:
        payload = text.encode("utf-8")
    except UnicodeError as exc:
        _fail("assetpack_notice_invalid", str(exc))
    if (
        len(text) > MAX_GENERIC_ASSETPACK_NOTICE_CHARACTERS
        or len(payload) > MAX_GENERIC_ASSETPACK_NOTICE_BYTES
    ):
        _fail(
            "assetpack_notice_invalid",
            "license runtime notice exceeds the 4096-character/byte limit",
        )
    if not isinstance(declared_hash, str) or hashlib.sha256(payload).hexdigest() != declared_hash:
        _fail(
            "assetpack_notice_hash_mismatch",
            "license runtime notice hash is not byte-derived",
        )
    return (
        {
            "path": f"{GENERIC_ASSETPACK_NOTICE_DIRECTORY}/{declared_hash}.txt",
            "sha256": declared_hash,
            "size_bytes": len(payload),
        },
        payload,
    )


def _semantic_assetpack_outputs(
    manifest_entry: Mapping[str, object],
    *,
    specification: Mapping[str, object],
    recipe: Mapping[str, object],
    processing_receipt: Mapping[str, object],
    qa_report: Mapping[str, object],
    license_records: Sequence[Mapping[str, object]],
) -> list[tuple[dict[str, object], bytes]]:
    if (
        manifest_entry.get("state") != "release_ready"
        or processing_receipt.get("status") != "completed"
        or qa_report.get("status") != "passed"
    ):
        _fail(
            "assetpack_lineage_mismatch",
            "D3 outputs require release-ready manifest, processing, and QA evidence",
        )

    def indexed_outputs(
        document: Mapping[str, object],
        field: str,
        context: str,
    ) -> tuple[list[str], dict[str, Mapping[str, object]]]:
        values = document.get(field)
        if not isinstance(values, list):
            _fail("assetpack_lineage_mismatch", f"{context} must be an array")
        roles: list[str] = []
        indexed: dict[str, Mapping[str, object]] = {}
        for value in values:
            if not isinstance(value, Mapping) or not isinstance(value.get("role"), str):
                _fail(
                    "assetpack_lineage_mismatch",
                    f"{context} contains an invalid role",
                )
            role = str(value["role"])
            if role in indexed:
                _fail(
                    "assetpack_lineage_mismatch",
                    f"{context} contains duplicate roles",
                )
            roles.append(role)
            indexed[role] = value
        return roles, indexed

    manifest_roles, manifest_outputs = indexed_outputs(
        manifest_entry,
        "outputs",
        "release manifest outputs",
    )
    specification_roles, specification_outputs = indexed_outputs(
        specification,
        "outputs",
        "asset specification outputs",
    )
    processing_roles, processing_outputs = indexed_outputs(
        processing_receipt,
        "outputs",
        "processing receipt outputs",
    )
    qa_roles, qa_outputs = indexed_outputs(
        qa_report,
        "outputs",
        "QA report outputs",
    )
    recipe_roles, recipe_steps = indexed_outputs(
        recipe,
        "steps",
        "processing recipe steps",
    )
    if not (manifest_roles == specification_roles == processing_roles == qa_roles == recipe_roles):
        _fail(
            "assetpack_output_coverage",
            "D3 outputs do not exactly cover specification, processing, and QA roles",
        )

    recipe_bindings: dict[tuple[str, str], Mapping[str, object]] = {}
    raw_bindings = recipe.get("licenses")
    if not isinstance(raw_bindings, list):
        _fail("assetpack_license_coverage", "recipe licenses must be an array")
    for binding in raw_bindings:
        if not isinstance(binding, Mapping):
            _fail("assetpack_license_coverage", "recipe license binding is invalid")
        key = (
            str(binding.get("candidate_artifact_id")),
            str(binding.get("role")),
        )
        if key in recipe_bindings:
            _fail("assetpack_license_coverage", "recipe license binding is duplicated")
        recipe_bindings[key] = binding

    licenses_by_identity: dict[
        tuple[str, int, str, str],
        Mapping[str, object],
    ] = {}
    for license_record in license_records:
        identity = _document_identity(license_record, "license_record_id")
        key = (
            str(identity["format"]),
            int(identity["format_version"]),
            str(identity["id"]),
            str(identity["content_hash"]),
        )
        if key in licenses_by_identity:
            _fail("assetpack_license_coverage", "license record identity is duplicated")
        licenses_by_identity[key] = license_record

    results: list[tuple[dict[str, object], bytes]] = []
    for role in manifest_roles:
        manifest_output = manifest_outputs[role]
        specification_output = specification_outputs[role]
        processing_output = processing_outputs[role]
        qa_output = qa_outputs[role]
        recipe_step = recipe_steps[role]
        if any(
            manifest_output.get(field) != processing_output.get(field)
            or manifest_output.get(field) != qa_output.get(field)
            for field in (
                "role",
                "media_type",
                "runtime_path",
                "sha256",
                "size_bytes",
            )
        ):
            _fail(
                "assetpack_lineage_mismatch",
                f"D3 output {role} contradicts processing or QA evidence",
            )
        if any(
            specification_output.get(field) != manifest_output.get(field)
            for field in ("role", "media_type", "runtime_path")
        ):
            _fail(
                "assetpack_lineage_mismatch",
                f"D3 output {role} contradicts its specification",
            )
        if any(
            processing_output.get(field) != recipe_step.get(step_field)
            for field, step_field in (
                ("candidate_artifact_id", "candidate_artifact_id"),
                ("role", "role"),
                ("media_type", "media_type"),
                ("runtime_path", "runtime_path"),
            )
        ):
            _fail(
                "assetpack_lineage_mismatch",
                f"D3 output {role} contradicts its processing recipe",
            )
        if any(
            qa_output.get(field) != processing_output.get(field)
            for field in (
                "candidate_artifact_id",
                "role",
                "media_type",
                "runtime_path",
                "locator",
                "sha256",
                "size_bytes",
                "metadata",
            )
        ):
            _fail(
                "assetpack_lineage_mismatch",
                f"D3 output {role} contradicts passed QA evidence",
            )

        candidate_key = (
            str(processing_output.get("candidate_artifact_id")),
            role,
        )
        binding = recipe_bindings.get(candidate_key)
        if binding is None or not isinstance(binding.get("license_record"), Mapping):
            _fail(
                "assetpack_license_coverage",
                f"D3 output {role} has no exact recipe license",
            )
        license_identity = {
            key: binding["license_record"][key]
            for key in ("format", "format_version", "id", "content_hash")
        }
        identity_key = (
            str(license_identity["format"]),
            int(license_identity["format_version"]),
            str(license_identity["id"]),
            str(license_identity["content_hash"]),
        )
        license_record = licenses_by_identity.get(identity_key)
        if license_record is None:
            _fail(
                "assetpack_license_coverage",
                f"D3 output {role} license does not resolve exactly",
            )
        candidate = license_record.get("candidate")
        if (
            not isinstance(candidate, Mapping)
            or (
                candidate.get("candidate_artifact_id"),
                candidate.get("role"),
            )
            != candidate_key
        ):
            _fail(
                "assetpack_license_coverage",
                f"D3 output {role} license candidate is crossed",
            )
        notice_identity, notice_payload = _runtime_notice(license_record)
        constraints = copy.deepcopy(specification_output["expectations"])
        assert isinstance(constraints, dict)
        constraints["max_bytes"] = manifest_output["size_bytes"]
        results.append(
            (
                {
                    "role": role,
                    "media_type": manifest_output["media_type"],
                    "runtime_path": manifest_output["runtime_path"],
                    "sha256": manifest_output["sha256"],
                    "size_bytes": manifest_output["size_bytes"],
                    "constraints": constraints,
                    "metadata": copy.deepcopy(processing_output["metadata"]),
                    "license_record": license_identity,
                    "runtime_notice": notice_identity,
                },
                notice_payload,
            )
        )
    if len(recipe_bindings) != len(results) or len(licenses_by_identity) != len(results):
        _fail(
            "assetpack_license_coverage",
            "D3 licenses do not exactly cover runtime outputs",
        )
    return results


def validate_generic_assetpack_asset_semantics(
    value: object,
    *,
    manifest_entry: Mapping[str, object],
    specification: Mapping[str, object],
    recipe: Mapping[str, object],
    processing_receipt: Mapping[str, object],
    qa_report: Mapping[str, object],
    license_records: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    """Validate one D3 asset entry against its complete pure D2 projection."""

    checked = _validate_asset_entry(value, "generic assetpack asset")
    for field in (
        "asset",
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
        "processing_recipe",
        "processing_receipt",
        "qa_report",
        "licenses",
    ):
        if checked[field] != manifest_entry[field]:
            _fail(
                "assetpack_lineage_mismatch",
                f"generic assetpack asset {field} contradicts release manifest",
            )
    expected_outputs = [
        output
        for output, _notice_payload in _semantic_assetpack_outputs(
            manifest_entry,
            specification=specification,
            recipe=recipe,
            processing_receipt=processing_receipt,
            qa_report=qa_report,
            license_records=license_records,
        )
    ]
    if checked["outputs"] != expected_outputs:
        _fail(
            "assetpack_lineage_mismatch",
            "generic assetpack outputs are not the exact D2 semantic projection",
        )
    return copy.deepcopy(checked)


def _build_asset_entry(
    manifest_entry: Mapping[str, object],
    record: Mapping[str, object],
    *,
    artifact_root: str | Path,
    files: dict[str, bytes],
) -> dict[str, object]:
    specification = record["specification"]
    processing_receipt = record["processing_receipt"]
    qa_report = record["qa_report"]
    recipe = record["recipe"]
    license_records = record["license_records"]
    assert isinstance(specification, Mapping)
    assert isinstance(processing_receipt, Mapping)
    assert isinstance(qa_report, Mapping)
    assert isinstance(recipe, Mapping)
    assert isinstance(license_records, Sequence)

    semantic_outputs = _semantic_assetpack_outputs(
        manifest_entry,
        specification=specification,
        recipe=recipe,
        processing_receipt=processing_receipt,
        qa_report=qa_report,
        license_records=license_records,
    )
    outputs: list[dict[str, object]] = []
    for manifest_output, (semantic_output, notice_payload) in zip(
        manifest_entry["outputs"],
        semantic_outputs,
        strict=True,
    ):
        payload = read_verified_artifact_bytes(
            artifact_root,
            manifest_output["locator"],
            expected_sha256=manifest_output["sha256"],
            expected_size_bytes=manifest_output["size_bytes"],
        )
        metadata = inspect_runtime_asset_bytes(
            payload,
            role=str(semantic_output["role"]),
            media_type=manifest_output["media_type"],
            expectations=semantic_output["constraints"],
        )
        if metadata != semantic_output["metadata"]:
            _fail(
                "assetpack_media_mismatch",
                f"{manifest_output['runtime_path']} metadata differs from D2 evidence",
            )
        runtime_path = manifest_output["runtime_path"]
        if runtime_path in files and files[runtime_path] != payload:
            _fail("assetpack_file_collision", "runtime path has conflicting bytes")
        files[runtime_path] = payload

        notice = semantic_output["runtime_notice"]
        assert isinstance(notice, Mapping)
        notice_path = str(notice["path"])
        if notice_path in files and files[notice_path] != notice_payload:
            _fail("assetpack_file_collision", "notice path has conflicting bytes")
        files[notice_path] = notice_payload
        outputs.append(copy.deepcopy(semantic_output))

    return {
        "asset": copy.deepcopy(manifest_entry["asset"]),
        "specification": copy.deepcopy(manifest_entry["specification"]),
        "request": copy.deepcopy(manifest_entry["request"]),
        "receipt": copy.deepcopy(manifest_entry["receipt"]),
        "selection": copy.deepcopy(manifest_entry["selection"]),
        "provenance": copy.deepcopy(manifest_entry["provenance"]),
        "processing_recipe": copy.deepcopy(manifest_entry["processing_recipe"]),
        "processing_receipt": copy.deepcopy(manifest_entry["processing_receipt"]),
        "qa_report": copy.deepcopy(manifest_entry["qa_report"]),
        "licenses": copy.deepcopy(manifest_entry["licenses"]),
        "outputs": outputs,
    }


def _build_file_inventory(files: Mapping[str, bytes]) -> dict[str, object]:
    ordered_paths = sorted(files, key=lambda item: item.encode("utf-8"))
    if len(ordered_paths) > MAX_GENERIC_ASSETPACK_FILES:
        _fail("assetpack_contract_limit", "assetpack file count exceeds its limit")
    _portable_path_tree(ordered_paths, "generic assetpack files")
    entries = [
        {
            "path": path,
            "sha256": hashlib.sha256(files[path]).hexdigest(),
            "size_bytes": len(files[path]),
        }
        for path in ordered_paths
    ]
    total_bytes = sum(entry["size_bytes"] for entry in entries)
    if total_bytes > MAX_GENERIC_ASSETPACK_TOTAL_BYTES:
        _fail("assetpack_contract_limit", "assetpack byte count exceeds its limit")
    inventory = {
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "files": entries,
    }
    inventory["content_hash"] = _hash(inventory)
    return inventory


def _prepare_generic_assetpack(
    manifest: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    asset_records: Sequence[Mapping[str, object]],
    artifact_root: str | Path,
    qa_reviews: Sequence[object] | None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    if (
        isinstance(asset_records, (str, bytes, bytearray))
        or not isinstance(asset_records, Sequence)
        or any(not isinstance(record, Mapping) for record in asset_records)
    ):
        _fail(
            "assetpack_lineage_mismatch",
            "asset_records must contain objects",
        )
    try:
        checked_manifest = validate_asset_manifest(
            manifest,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
            inventory=inventory,
            asset_records=asset_records,
            artifact_root=artifact_root,
        )
    except (
        GenericAssetProcessingError,
        GenericAssetProductionError,
        GenericAssetError,
    ) as exc:
        _fail("assetpack_source_invalid", str(exc))
    if checked_manifest["state"] != "release_ready":
        _fail(
            "assetpack_source_not_release_ready",
            "only a release-ready D2 manifest may be sealed",
        )
    if qa_reviews is None:
        _fail(
            "assetpack_qa_authority_required",
            "candidate assetpacks require exact verified QA review handles",
        )
    try:
        from worldforge.generic_asset_authority import (
            GenericAssetAuthorityError,
            require_verified_asset_qa_reviews,
        )

        require_verified_asset_qa_reviews(checked_manifest, qa_reviews)
    except GenericAssetAuthorityError as exc:
        _fail(
            "assetpack_qa_authority_invalid",
            f"{exc.reason_code}: {exc.detail}",
        )

    records = _record_by_asset_id(asset_records)
    files: dict[str, bytes] = {}
    assets = [
        _build_asset_entry(
            manifest_entry,
            records[manifest_entry["asset"]["asset_id"]],
            artifact_root=artifact_root,
            files=files,
        )
        for manifest_entry in checked_manifest["assets"]
    ]
    for path in files:
        folded = path.casefold()
        if folded == GENERIC_ASSETPACK_MANIFEST or (
            folded.startswith(f"{GENERIC_ASSETPACK_NOTICE_DIRECTORY}/")
            and not (
                path.startswith(f"{GENERIC_ASSETPACK_NOTICE_DIRECTORY}/") and path.endswith(".txt")
            )
        ):
            _fail("assetpack_reserved_path", f"{path} uses a reserved path")
    document: dict[str, Any] = {
        "format": GENERIC_ASSETPACK_FORMAT,
        "format_version": GENERIC_ASSETPACK_VERSION,
        "gamepack": copy.deepcopy(checked_manifest["gamepack"]),
        "asset_subject": copy.deepcopy(checked_manifest["asset_subject"]),
        "target": copy.deepcopy(checked_manifest["target"]),
        "style": copy.deepcopy(checked_manifest["style"]),
        "asset_inventory": copy.deepcopy(checked_manifest["inventory"]),
        "release_ready_manifest": _document_identity(
            checked_manifest,
            "manifest_id",
        ),
        "state": "sealed",
        "assets": assets,
        "inventory": _build_file_inventory(files),
    }
    document["assetpack_id"] = _derived_assetpack_id(document)
    return validate_generic_assetpack_document(_seal(document)), files


def build_generic_assetpack_manifest(
    manifest: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    asset_records: Sequence[Mapping[str, object]],
    artifact_root: str | Path,
    qa_reviews: Sequence[object] | None = None,
    release_authority: object | None = None,
) -> dict[str, Any]:
    """Build a deterministic runtime-only D3 manifest from exact release-ready D2 lineage."""

    document, _files = _prepare_generic_assetpack(
        manifest,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        asset_records=asset_records,
        artifact_root=artifact_root,
        qa_reviews=qa_reviews,
    )
    if release_authority is not None:
        try:
            from worldforge.generic_asset_authority import (
                GenericAssetAuthorityError,
                require_verified_asset_release_authority,
            )

            require_verified_asset_release_authority(
                release_authority,
                manifest=manifest,
                assetpack=document,
                reviews=() if qa_reviews is None else qa_reviews,
            )
        except GenericAssetAuthorityError as exc:
            _fail(
                "assetpack_release_authority_invalid",
                f"{exc.reason_code}: {exc.detail}",
            )
    return document


def _validate_file_inventory(value: object) -> dict[str, Any]:
    inventory = _object(value, "generic assetpack.inventory")
    _exact_keys(inventory, _INVENTORY_FIELDS, "generic assetpack.inventory")
    files = inventory.get("files")
    if not isinstance(files, list) or len(files) > MAX_GENERIC_ASSETPACK_FILES:
        _fail("assetpack_contract_limit", "inventory files must be a bounded array")
    checked_files: list[dict[str, Any]] = []
    paths: list[str] = []
    total_bytes = 0
    for index, value in enumerate(files):
        context = f"generic assetpack.inventory.files/{index}"
        entry = _object(value, context)
        _exact_keys(entry, _FILE_FIELDS, context)
        path = _portable_relative_path(entry.get("path"), f"{context}.path")
        _sha256(entry.get("sha256"), f"{context}.sha256")
        size = _integer(entry.get("size_bytes"), f"{context}.size_bytes", minimum=0)
        paths.append(path)
        total_bytes += size
        checked_files.append(entry)
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")):
        _fail("assetpack_noncanonical", "inventory files must be UTF-8 path sorted")
    _portable_path_tree(paths, "generic assetpack inventory")
    if inventory.get("file_count") != len(checked_files):
        _fail("assetpack_inventory_mismatch", "inventory file_count is not exact")
    if inventory.get("total_bytes") != total_bytes:
        _fail("assetpack_inventory_mismatch", "inventory total_bytes is not exact")
    if total_bytes > MAX_GENERIC_ASSETPACK_TOTAL_BYTES:
        _fail("assetpack_contract_limit", "assetpack byte count exceeds its limit")
    _sha256(inventory.get("content_hash"), "generic assetpack.inventory.content_hash")
    if inventory["content_hash"] != _hash(inventory):
        _fail("assetpack_inventory_hash_mismatch", "inventory hash is not canonical")
    return inventory


def _validate_output(
    value: object,
    context: str,
) -> dict[str, Any]:
    output = _object(value, context)
    _exact_keys(output, _OUTPUT_FIELDS, context)
    role = _identifier(output.get("role"), f"{context}.role")
    media_type = output.get("media_type")
    if not isinstance(media_type, str) or media_type not in _OUTPUT_MEDIA.get(role, ()):
        _fail("assetpack_media_invalid", f"{context} role/media type is unsupported")
    runtime_path = _portable_relative_path(
        output.get("runtime_path"),
        f"{context}.runtime_path",
    )
    if runtime_path.casefold() == GENERIC_ASSETPACK_MANIFEST or runtime_path.casefold().startswith(
        f"{GENERIC_ASSETPACK_NOTICE_DIRECTORY}/"
    ):
        _fail("assetpack_reserved_path", f"{runtime_path} uses a reserved path")
    _sha256(output.get("sha256"), f"{context}.sha256")
    size_bytes = _integer(
        output.get("size_bytes"),
        f"{context}.size_bytes",
        minimum=1,
    )
    constraints = output.get("constraints")
    if not isinstance(constraints, Mapping) or constraints.get("max_bytes") != size_bytes:
        _fail(
            "assetpack_media_invalid",
            f"{context}.constraints.max_bytes must equal size_bytes",
        )
    try:
        _validate_spec_output(
            {
                "role": role,
                "media_type": media_type,
                "runtime_path": runtime_path,
                "expectations": constraints,
            },
            f"{context}.contract",
        )
        _validate_metadata(output.get("metadata"), media_type, f"{context}.metadata")
    except (GenericAssetError, GenericAssetProductionError) as exc:
        _fail("assetpack_media_invalid", str(exc))
    _validate_identity(
        output.get("license_record"),
        f"{context}.license_record",
        expected_format=ASSET_LICENSE_FORMAT,
    )
    notice = _object(output.get("runtime_notice"), f"{context}.runtime_notice")
    _exact_keys(notice, _NOTICE_FIELDS, f"{context}.runtime_notice")
    notice_hash = _sha256(
        notice.get("sha256"),
        f"{context}.runtime_notice.sha256",
    )
    expected_path = f"{GENERIC_ASSETPACK_NOTICE_DIRECTORY}/{notice_hash}.txt"
    if notice.get("path") != expected_path:
        _fail(
            "assetpack_notice_invalid",
            f"{context}.runtime_notice.path must be content-addressed",
        )
    _integer(
        notice.get("size_bytes"),
        f"{context}.runtime_notice.size_bytes",
        minimum=0,
    )
    if notice["size_bytes"] > MAX_GENERIC_ASSETPACK_NOTICE_BYTES:
        _fail(
            "assetpack_notice_invalid",
            f"{context}.runtime_notice.size_bytes exceeds 4096 bytes",
        )
    return output


def _validate_asset_entry(
    value: object,
    context: str,
) -> dict[str, Any]:
    entry = _object(value, context)
    _exact_keys(entry, _ASSET_ENTRY_FIELDS, context)
    asset = _object(entry.get("asset"), f"{context}.asset")
    _exact_keys(asset, _ASSET_FIELDS, f"{context}.asset")
    _identifier(asset.get("asset_id"), f"{context}.asset.asset_id")
    _sha256(asset.get("content_hash"), f"{context}.asset.content_hash")
    for field, expected_format in (
        ("specification", ASSET_SPEC_FORMAT),
        ("request", ASSET_PRODUCTION_REQUEST_FORMAT),
        ("receipt", ASSET_PRODUCTION_RECEIPT_FORMAT),
        ("selection", ASSET_SELECTION_FORMAT),
        ("provenance", ASSET_PROVENANCE_FORMAT),
        ("processing_recipe", ASSET_PROCESSING_RECIPE_FORMAT),
        ("processing_receipt", ASSET_PROCESSING_RECEIPT_FORMAT),
        ("qa_report", ASSET_QA_REPORT_FORMAT),
    ):
        _validate_identity(
            entry.get(field),
            f"{context}.{field}",
            expected_format=expected_format,
        )
    licenses = entry.get("licenses")
    if not isinstance(licenses, list) or not licenses or len(licenses) > 4:
        _fail("assetpack_license_coverage", f"{context}.licenses is invalid")
    for index, license_identity in enumerate(licenses):
        _validate_identity(
            license_identity,
            f"{context}.licenses/{index}",
            expected_format=ASSET_LICENSE_FORMAT,
        )
    license_ids = [identity["id"] for identity in licenses]
    if license_ids != sorted(license_ids, key=lambda item: item.encode("utf-8")):
        _fail("assetpack_noncanonical", f"{context}.licenses is not canonical")
    if len(set(license_ids)) != len(license_ids):
        _fail(
            "assetpack_license_coverage",
            f"{context}.licenses contains duplicate IDs",
        )
    outputs = entry.get("outputs")
    if not isinstance(outputs, list) or not outputs or len(outputs) > 4:
        _fail("assetpack_output_coverage", f"{context}.outputs is invalid")
    checked_outputs = [
        _validate_output(output, f"{context}.outputs/{index}")
        for index, output in enumerate(outputs)
    ]
    roles = [output["role"] for output in checked_outputs]
    if roles != sorted(roles, key=lambda item: item.encode("utf-8")):
        _fail("assetpack_noncanonical", f"{context}.outputs is not canonical")
    license_identities = {
        (
            identity["format"],
            identity["format_version"],
            identity["id"],
            identity["content_hash"],
        )
        for identity in licenses
    }
    output_license_identities = {
        (
            output["license_record"]["format"],
            output["license_record"]["format_version"],
            output["license_record"]["id"],
            output["license_record"]["content_hash"],
        )
        for output in checked_outputs
    }
    if output_license_identities != license_identities:
        _fail(
            "assetpack_license_coverage",
            f"{context}.outputs do not bind every exact license",
        )
    return entry


def validate_generic_assetpack_document(value: object) -> dict[str, Any]:
    """Validate D3 document structure without claiming directory-level sealing."""

    try:
        _validate_json_structure(value, context="generic assetpack")
        document = _object(value, "generic assetpack")
        _exact_keys(document, _MANIFEST_FIELDS, "generic assetpack")
        if document.get("format") != GENERIC_ASSETPACK_FORMAT:
            _fail(
                "assetpack_format_invalid",
                f"format must be {GENERIC_ASSETPACK_FORMAT}",
            )
        if document.get("format_version") != GENERIC_ASSETPACK_VERSION:
            _fail("assetpack_version_invalid", "format_version must be 1")
        _identifier(document.get("assetpack_id"), "generic assetpack.assetpack_id")
        for field, expected_format in (
            ("gamepack", "world-forge.gamepack"),
            ("asset_subject", ASSET_SUBJECT_FORMAT),
            ("target", ASSET_TARGET_FORMAT),
            ("style", ASSET_STYLE_FORMAT),
            ("asset_inventory", ASSET_INVENTORY_FORMAT),
            ("release_ready_manifest", ASSET_MANIFEST_FORMAT),
        ):
            _validate_identity(
                document.get(field),
                f"generic assetpack.{field}",
                expected_format=expected_format,
            )
        if document.get("state") != "sealed":
            _fail("assetpack_state_invalid", "state must be sealed")
        assets = document.get("assets")
        if not isinstance(assets, list) or not assets or len(assets) > MAX_GENERIC_ASSETPACK_ASSETS:
            _fail("assetpack_contract_limit", "assets must be bounded and non-empty")
        checked_assets = [
            _validate_asset_entry(asset, f"generic assetpack.assets/{index}")
            for index, asset in enumerate(assets)
        ]
        asset_ids = [entry["asset"]["asset_id"] for entry in checked_assets]
        if asset_ids != sorted(asset_ids, key=lambda item: item.encode("utf-8")):
            _fail("assetpack_noncanonical", "assets must be UTF-8 ID sorted")
        if len({item.casefold() for item in asset_ids}) != len(asset_ids):
            _fail("assetpack_asset_collision", "asset IDs collide")
        outputs = [output for entry in checked_assets for output in entry["outputs"]]
        if len(outputs) > MAX_GENERIC_ASSETPACK_OUTPUTS:
            _fail("assetpack_contract_limit", "output count exceeds its limit")
        file_inventory = _validate_file_inventory(document.get("inventory"))
        inventory_files = {entry["path"]: entry for entry in file_inventory["files"]}
        expected_files: dict[str, tuple[str, int]] = {}
        for output in outputs:
            for path, sha256, size_bytes in (
                (
                    output["runtime_path"],
                    output["sha256"],
                    output["size_bytes"],
                ),
                (
                    output["runtime_notice"]["path"],
                    output["runtime_notice"]["sha256"],
                    output["runtime_notice"]["size_bytes"],
                ),
            ):
                identity = (sha256, size_bytes)
                if path in expected_files and expected_files[path] != identity:
                    _fail(
                        "assetpack_file_collision",
                        f"{path} has conflicting identities",
                    )
                expected_files[path] = identity
        if set(inventory_files) != set(expected_files):
            _fail(
                "assetpack_inventory_mismatch",
                "inventory is not the exact runtime payload and notice set",
            )
        for path, (sha256, size_bytes) in expected_files.items():
            if (
                inventory_files[path]["sha256"] != sha256
                or inventory_files[path]["size_bytes"] != size_bytes
            ):
                _fail(
                    "assetpack_inventory_mismatch",
                    f"{path} inventory identity is inconsistent",
                )
        if document["assetpack_id"] != _derived_assetpack_id(document):
            _fail(
                "assetpack_id_mismatch",
                "assetpack_id is not deterministically derived",
            )
        _sha256(document.get("content_hash"), "generic assetpack.content_hash")
        if document["content_hash"] != _hash(document):
            _fail(
                "assetpack_content_hash_mismatch",
                "content_hash is not canonical",
            )
        return copy.deepcopy(document)
    except GenericAssetpackError:
        raise
    except (
        CreationContractError,
        GenericAssetError,
        GenericAssetProductionError,
        TypeError,
        ValueError,
    ) as exc:
        _fail("assetpack_contract_invalid", str(exc))


def serialize_generic_assetpack(value: object) -> bytes:
    """Serialize one structurally valid D3 manifest as canonical UTF-8 JSON."""

    return canonical_json_bytes(validate_generic_assetpack_document(value))


class VerifiedGenericAssetpack:
    """Context-managed immutable byte snapshot from one integral D3 verification."""

    __slots__ = (
        "_closed",
        "_evidence",
        "_files",
        "_manifest",
        "root",
        "root_identity",
    )

    def __init__(
        self,
        root: Path,
        manifest: dict[str, Any],
        files: Mapping[str, bytes],
        root_identity: DirectoryIdentity,
    ) -> None:
        self.root = root
        self.root_identity = root_identity
        self._manifest = copy.deepcopy(manifest)
        self._files = dict(files)
        self._evidence = MappingProxyType(
            {
                "status": "sealed",
                "assetpack_id": manifest["assetpack_id"],
                "content_hash": manifest["content_hash"],
                "inventory_hash": manifest["inventory"]["content_hash"],
                "file_count": manifest["inventory"]["file_count"],
                "total_bytes": manifest["inventory"]["total_bytes"],
            }
        )
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            _fail(
                "assetpack_snapshot_closed",
                "verified generic assetpack snapshot is already closed",
            )

    @property
    def status(self) -> str:
        self._require_open()
        return "sealed"

    @property
    def manifest(self) -> dict[str, Any]:
        self._require_open()
        return copy.deepcopy(self._manifest)

    @property
    def evidence(self) -> Mapping[str, object]:
        self._require_open()
        return self._evidence

    @property
    def files(self) -> Mapping[str, bytes]:
        self._require_open()
        return MappingProxyType(dict(self._files))

    def read_bytes(self, relative_path: str) -> bytes:
        self._require_open()
        try:
            return self._files[relative_path]
        except KeyError:
            _fail(
                "assetpack_file_missing",
                f"verified snapshot has no file {relative_path!r}",
            )

    def close(self) -> None:
        self._files.clear()
        self._closed = True

    def __enter__(self) -> VerifiedGenericAssetpack:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        self.close()


_TreeEntryState = tuple[int, int, int, int, int, int, int]
_VerificationHook = Callable[[str, str | None], None]


@dataclass(frozen=True, slots=True)
class _ExactTreeSnapshot:
    root: _TreeEntryState
    files: Mapping[str, _TreeEntryState]
    directories: Mapping[str, _TreeEntryState]


def _tree_entry_state(info: FileStat) -> _TreeEntryState:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


_RETAINED_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_RETAINED_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_BINARY", 0)
)


def _require_retained_root_fd(root_fd: int) -> FileStat:
    if os.name != "posix" or isinstance(root_fd, bool) or not isinstance(root_fd, int):
        _fail(
            "assetpack_directory_unsafe",
            "retained assetpack root descriptor is unavailable",
        )
    try:
        info = descriptor_file_stat(root_fd)
    except OSError as exc:
        _fail(
            "assetpack_directory_unsafe",
            f"could not inspect retained assetpack root: {exc}",
        )
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _fail(
            "assetpack_directory_unsafe",
            "retained assetpack root is not a real directory",
        )
    return info


@contextmanager
def _open_retained_relative_parent(
    root_fd: int,
    relative: str,
) -> Iterator[tuple[int, str]]:
    current: int | None = None
    try:
        safe = _portable_relative_path(relative, "retained assetpack path")
        parts = PurePosixPath(safe).parts
        current = os.dup(root_fd)
    except (CreationContractError, OSError) as exc:
        _fail("assetpack_file_read_failed", str(exc))
    try:
        for component in parts[:-1]:
            if current is None:
                _fail(
                    "assetpack_file_read_failed",
                    "retained assetpack parent descriptor is unavailable",
                )
            child = os.open(
                component,
                _RETAINED_DIRECTORY_FLAGS,
                dir_fd=current,
            )
            try:
                child_info = descriptor_file_stat(child)
                named_info = os.stat(
                    component,
                    dir_fd=current,
                    follow_symlinks=False,
                )
                if (
                    is_link_or_reparse(child_info)
                    or is_link_or_reparse(named_info)
                    or not stat.S_ISDIR(child_info.st_mode)
                    or not stat.S_ISDIR(named_info.st_mode)
                    or _tree_entry_state(child_info) != _tree_entry_state(named_info)
                ):
                    _fail(
                        "assetpack_directory_changed",
                        f"retained assetpack directory changed: {component}",
                    )
            except BaseException:
                primary = sys.exception()
                try:
                    os.close(child)
                except OSError as cleanup_error:
                    if primary is not None:
                        primary.add_note(
                            f"retained child descriptor cleanup failed: {cleanup_error}"
                        )
                raise
            previous = current
            try:
                os.close(previous)
            except OSError:
                primary = sys.exception()
                current = None
                try:
                    os.close(child)
                except OSError as cleanup_error:
                    if primary is not None:
                        primary.add_note(
                            "retained child descriptor cleanup after parent-close failure "
                            f"failed: {cleanup_error}"
                        )
                raise
            current = child
        if current is None:
            _fail(
                "assetpack_file_read_failed",
                "retained assetpack parent descriptor is unavailable",
            )
        yield current, parts[-1]
    except GenericAssetpackError:
        raise
    except OSError as exc:
        _fail("assetpack_file_read_failed", str(exc))
    finally:
        if current is not None:
            os.close(current)


def _retained_relative_state(
    root_fd: int,
    relative: str | None,
) -> FileStat:
    if relative is None:
        return _require_retained_root_fd(root_fd)
    with _open_retained_relative_parent(root_fd, relative) as (parent_fd, name):
        try:
            return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            _fail(
                "assetpack_directory_changed",
                f"retained assetpack entry changed: {relative}: {exc}",
            )


def _retained_file_bytes(
    root_fd: int,
    relative: str,
    *,
    limit: int,
    reason_code: str,
) -> bytes:
    descriptor: int | None = None
    try:
        with _open_retained_relative_parent(root_fd, relative) as (parent_fd, name):
            named_before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            descriptor = os.open(name, _RETAINED_FILE_FLAGS, dir_fd=parent_fd)
            opened_before = descriptor_file_stat(descriptor)
            if (
                is_link_or_reparse(named_before)
                or is_link_or_reparse(opened_before)
                or not stat.S_ISREG(named_before.st_mode)
                or not stat.S_ISREG(opened_before.st_mode)
                or named_before.st_nlink != 1
                or opened_before.st_nlink != 1
                or _tree_entry_state(named_before) != _tree_entry_state(opened_before)
                or opened_before.st_size > limit
            ):
                _fail(reason_code, f"retained assetpack file is unsafe: {relative}")
            payload = bytearray()
            while len(payload) <= limit:
                chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            opened_after = descriptor_file_stat(descriptor)
            named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                len(payload) > limit
                or len(payload) != opened_after.st_size
                or _tree_entry_state(opened_before) != _tree_entry_state(opened_after)
                or _tree_entry_state(opened_before) != _tree_entry_state(named_after)
            ):
                _fail(reason_code, f"retained assetpack file changed: {relative}")
            return bytes(payload)
    except GenericAssetpackError:
        raise
    except OSError as exc:
        _fail(reason_code, f"could not read retained assetpack file {relative}: {exc}")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _snapshot_exact_tree_from_fd(root_fd: int) -> _ExactTreeSnapshot:
    root_info = _require_retained_root_fd(root_fd)
    files: dict[str, _TreeEntryState] = {}
    directories: dict[str, _TreeEntryState] = {}
    pending: list[tuple[Path, int]] = [(Path(), os.dup(root_fd))]
    try:
        while pending:
            relative_directory, current_fd = pending.pop()
            try:
                with os.scandir(current_fd) as iterator:
                    names = sorted((entry.name for entry in iterator), key=os.fsencode)
                for name in names:
                    info = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
                    relative_path = relative_directory / name
                    relative = relative_path.as_posix()
                    _portable_relative_path(relative, "assetpack tree entry")
                    depth = len(PurePosixPath(relative).parts)
                    if depth > MAX_GENERIC_ASSETPACK_DEPTH:
                        _fail(
                            "assetpack_contract_limit",
                            "assetpack on-disk tree depth exceeds its limit",
                        )
                    if is_link_or_reparse(info):
                        _fail(
                            "assetpack_directory_unsafe",
                            f"assetpack tree entry is linked or special: {relative}",
                        )
                    if stat.S_ISDIR(info.st_mode):
                        child_fd = os.open(
                            name,
                            _RETAINED_DIRECTORY_FLAGS,
                            dir_fd=current_fd,
                        )
                        try:
                            opened = descriptor_file_stat(child_fd)
                            if _tree_entry_state(opened) != _tree_entry_state(info):
                                _fail(
                                    "assetpack_directory_changed",
                                    f"assetpack directory changed while opening: {relative}",
                                )
                        except BaseException:
                            primary = sys.exception()
                            try:
                                os.close(child_fd)
                            except OSError as cleanup_error:
                                if primary is not None:
                                    primary.add_note(
                                        "retained tree child descriptor cleanup failed: "
                                        f"{cleanup_error}"
                                    )
                            raise
                        directories[relative] = _tree_entry_state(opened)
                        if len(directories) > MAX_GENERIC_ASSETPACK_DIRECTORIES:
                            os.close(child_fd)
                            _fail(
                                "assetpack_contract_limit",
                                "assetpack on-disk directory count exceeds its limit",
                            )
                        pending.append((relative_path, child_fd))
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        files[relative] = _tree_entry_state(info)
                        if len(files) > MAX_GENERIC_ASSETPACK_FILES + 1:
                            _fail(
                                "assetpack_contract_limit",
                                "assetpack on-disk file count exceeds its limit",
                            )
                    else:
                        _fail(
                            "assetpack_file_unsafe",
                            f"assetpack file is linked or special: {relative}",
                        )
                    if len(files) + len(directories) + 1 > MAX_GENERIC_ASSETPACK_TREE_NODES:
                        _fail(
                            "assetpack_contract_limit",
                            "assetpack on-disk total node count exceeds its limit",
                        )
            finally:
                os.close(current_fd)
    except BaseException:
        for _relative, descriptor in pending:
            os.close(descriptor)
        raise
    return _ExactTreeSnapshot(
        root=_tree_entry_state(root_info),
        files=MappingProxyType(files),
        directories=MappingProxyType(directories),
    )


def _invoke_verification_hook(
    hook: _VerificationHook | None,
    event: str,
    relative: str | None = None,
) -> None:
    if hook is not None:
        hook(event, relative)


def _snapshot_exact_tree(
    root: Path,
    *,
    retained_root_fd: int | None = None,
) -> _ExactTreeSnapshot:
    if retained_root_fd is not None:
        return _snapshot_exact_tree_from_fd(retained_root_fd)
    try:
        root_info = path_file_stat(root)
    except OSError as exc:
        _fail("assetpack_directory_invalid", f"could not inspect {root}: {exc}")
    if is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        _fail("assetpack_directory_invalid", f"{root} must be a real directory")
    files: dict[str, _TreeEntryState] = {}
    directories: dict[str, _TreeEntryState] = {}
    pending = [Path()]

    try:
        while pending:
            relative_directory = pending.pop()
            current_path = root / relative_directory
            with os.scandir(current_path) as iterator:
                entries = sorted(iterator, key=lambda item: os.fsencode(item.name))
            for entry in entries:
                path = current_path / entry.name
                info = path_file_stat(path)
                relative_path = relative_directory / entry.name
                relative = relative_path.as_posix()
                _portable_relative_path(relative, "assetpack tree entry")
                depth = len(PurePosixPath(relative).parts)
                if depth > MAX_GENERIC_ASSETPACK_DEPTH:
                    _fail(
                        "assetpack_contract_limit",
                        "assetpack on-disk tree depth exceeds its limit",
                    )
                if is_link_or_reparse(info):
                    _fail(
                        "assetpack_directory_unsafe",
                        f"assetpack tree entry is linked or special: {path}",
                    )
                if stat.S_ISDIR(info.st_mode):
                    directories[relative] = _tree_entry_state(info)
                    if len(directories) > MAX_GENERIC_ASSETPACK_DIRECTORIES:
                        _fail(
                            "assetpack_contract_limit",
                            "assetpack on-disk directory count exceeds its limit",
                        )
                    pending.append(relative_path)
                elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                    files[relative] = _tree_entry_state(info)
                    if len(files) > MAX_GENERIC_ASSETPACK_FILES + 1:
                        _fail(
                            "assetpack_contract_limit",
                            "assetpack on-disk file count exceeds its limit",
                        )
                else:
                    _fail(
                        "assetpack_file_unsafe",
                        f"assetpack file is linked or special: {path}",
                    )
                if len(files) + len(directories) + 1 > MAX_GENERIC_ASSETPACK_TREE_NODES:
                    _fail(
                        "assetpack_contract_limit",
                        "assetpack on-disk total node count exceeds its limit",
                    )
    except GenericAssetpackError:
        raise
    except (CreationContractError, OSError) as exc:
        _fail("assetpack_directory_invalid", str(exc))
    return _ExactTreeSnapshot(
        root=_tree_entry_state(root_info),
        files=MappingProxyType(files),
        directories=MappingProxyType(directories),
    )


def _walk_exact_tree(
    root: Path,
    *,
    retained_root_fd: int | None = None,
) -> tuple[set[str], set[str]]:
    snapshot = _snapshot_exact_tree(root, retained_root_fd=retained_root_fd)
    return set(snapshot.files), set(snapshot.directories)


def _require_entry_state(
    root: Path,
    relative: str | None,
    expected: _TreeEntryState,
    *,
    directory: bool,
    retained_root_fd: int | None = None,
) -> None:
    path = root if relative is None else root / PurePosixPath(relative)
    try:
        info = (
            path_file_stat(path)
            if retained_root_fd is None
            else _retained_relative_state(retained_root_fd, relative)
        )
    except OSError as exc:
        _fail(
            "assetpack_directory_changed",
            f"assetpack tree entry changed during verification: {path}: {exc}",
        )
    expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        is_link_or_reparse(info)
        or not expected_type
        or (not directory and info.st_nlink != 1)
        or _tree_entry_state(info) != expected
    ):
        _fail(
            "assetpack_directory_changed",
            f"assetpack tree entry changed during verification: {path}",
        )


def _require_snapshot_path(
    root: Path,
    snapshot: _ExactTreeSnapshot,
    relative: str,
    *,
    retained_root_fd: int | None = None,
) -> None:
    _require_entry_state(
        root,
        None,
        snapshot.root,
        directory=True,
        retained_root_fd=retained_root_fd,
    )
    path = PurePosixPath(relative)
    for parent in reversed(path.parents):
        parent_text = parent.as_posix()
        if parent_text == ".":
            continue
        expected = snapshot.directories.get(parent_text)
        if expected is None:
            _fail(
                "assetpack_directory_changed",
                f"assetpack ancestry was not in the retained tree: {parent_text}",
            )
        _require_entry_state(
            root,
            parent_text,
            expected,
            directory=True,
            retained_root_fd=retained_root_fd,
        )
    expected_file = snapshot.files.get(relative)
    if expected_file is None:
        _fail(
            "assetpack_directory_changed",
            f"assetpack file was not in the retained tree: {relative}",
        )
    _require_entry_state(
        root,
        relative,
        expected_file,
        directory=False,
        retained_root_fd=retained_root_fd,
    )


def _require_tree_snapshot(
    root: Path,
    snapshot: _ExactTreeSnapshot,
    *,
    retained_root_fd: int | None = None,
) -> None:
    current = _snapshot_exact_tree(root, retained_root_fd=retained_root_fd)
    if (
        current.root != snapshot.root
        or dict(current.files) != dict(snapshot.files)
        or dict(current.directories) != dict(snapshot.directories)
    ):
        _fail(
            "assetpack_directory_changed",
            "assetpack tree changed during integral verification",
        )


def _expected_directories(paths: Sequence[str]) -> set[str]:
    return {
        parent.as_posix()
        for path in paths
        for parent in PurePosixPath(path).parents
        if parent.as_posix() != "."
    }


def verify_generic_assetpack(
    root: str | Path,
    *,
    expected_content_hash: str | None = None,
    expected_parent_identity: DirectoryIdentity | None = None,
    _verification_hook: _VerificationHook | None = None,
    _retained_root_fd: int | None = None,
) -> VerifiedGenericAssetpack:
    """Integrally verify one exact D3 tree and retain its verified byte snapshot."""

    root_path = Path(root).absolute()
    try:
        if _retained_root_fd is None:
            _require_expected_parent_identity(root_path.parent, expected_parent_identity)
        elif expected_parent_identity is not None:
            _fail(
                "assetpack_parent_identity_invalid",
                "retained-root verification does not accept a lexical parent identity",
            )
        tree_snapshot = _snapshot_exact_tree(
            root_path,
            retained_root_fd=_retained_root_fd,
        )
        retained_identity = tree_snapshot.root[0], tree_snapshot.root[1]
        files_before = set(tree_snapshot.files)
        directories_before = set(tree_snapshot.directories)
        _invoke_verification_hook(
            _verification_hook,
            "after_tree_snapshot",
        )
        _require_tree_snapshot(
            root_path,
            tree_snapshot,
            retained_root_fd=_retained_root_fd,
        )
        if GENERIC_ASSETPACK_MANIFEST not in files_before:
            _fail(
                "assetpack_manifest_missing",
                f"assetpack is missing {GENERIC_ASSETPACK_MANIFEST}",
            )
        _require_snapshot_path(
            root_path,
            tree_snapshot,
            GENERIC_ASSETPACK_MANIFEST,
            retained_root_fd=_retained_root_fd,
        )
        manifest_payload = (
            _safe_artifact_bytes(
                root_path,
                GENERIC_ASSETPACK_MANIFEST,
                limit=MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
            )
            if _retained_root_fd is None
            else _retained_file_bytes(
                _retained_root_fd,
                GENERIC_ASSETPACK_MANIFEST,
                limit=MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
                reason_code="assetpack_file_read_failed",
            )
        )
        _invoke_verification_hook(
            _verification_hook,
            "after_manifest_read",
            GENERIC_ASSETPACK_MANIFEST,
        )
        _require_snapshot_path(
            root_path,
            tree_snapshot,
            GENERIC_ASSETPACK_MANIFEST,
            retained_root_fd=_retained_root_fd,
        )
        manifest = validate_generic_assetpack_document(
            _decode_creation_object(
                manifest_payload,
                root_path / GENERIC_ASSETPACK_MANIFEST,
            )
        )
        if manifest_payload != serialize_generic_assetpack(manifest):
            _fail(
                "assetpack_manifest_noncanonical",
                "assetpack.json is not the exact canonical serialization",
            )
        if expected_content_hash is not None:
            try:
                checked_expected_hash = _sha256(
                    expected_content_hash,
                    "expected generic assetpack content hash",
                )
            except CreationContractError as exc:
                _fail("assetpack_expected_hash_invalid", str(exc))
            if manifest["content_hash"] != checked_expected_hash:
                _fail(
                    "assetpack_expected_hash_mismatch",
                    "assetpack does not match the requested immutable hash",
                )

        inventory_files = {entry["path"]: entry for entry in manifest["inventory"]["files"]}
        expected_files = {GENERIC_ASSETPACK_MANIFEST, *inventory_files}
        if files_before != expected_files:
            _fail(
                "assetpack_tree_mismatch",
                "assetpack tree has missing or extra files",
            )
        expected_directories = _expected_directories(tuple(expected_files))
        if directories_before != expected_directories:
            _fail(
                "assetpack_tree_mismatch",
                "assetpack tree has missing or extra directories",
            )

        runtime_outputs = {
            output["runtime_path"]: output
            for asset in manifest["assets"]
            for output in asset["outputs"]
        }
        notice_paths = {
            output["runtime_notice"]["path"]
            for asset in manifest["assets"]
            for output in asset["outputs"]
        }
        retained_files = {GENERIC_ASSETPACK_MANIFEST: manifest_payload}
        for path, record in inventory_files.items():
            _require_snapshot_path(
                root_path,
                tree_snapshot,
                path,
                retained_root_fd=_retained_root_fd,
            )
            if _retained_root_fd is None:
                try:
                    payload = read_verified_artifact_bytes(
                        root_path,
                        path,
                        expected_sha256=record["sha256"],
                        expected_size_bytes=record["size_bytes"],
                    )
                except GenericAssetProductionError as exc:
                    reason = {
                        "production_artifact_hash_mismatch": "assetpack_file_hash_mismatch",
                        "production_artifact_size_mismatch": "assetpack_file_size_mismatch",
                    }.get(exc.reason_code, "assetpack_file_read_failed")
                    _fail(reason, f"{path}: {exc.detail}")
            else:
                payload = _retained_file_bytes(
                    _retained_root_fd,
                    path,
                    limit=max(record["size_bytes"], 1),
                    reason_code="assetpack_file_read_failed",
                )
                if len(payload) != record["size_bytes"]:
                    _fail(
                        "assetpack_file_size_mismatch",
                        f"{path}: retained file size differs from its inventory",
                    )
                if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                    _fail(
                        "assetpack_file_hash_mismatch",
                        f"{path}: retained file hash differs from its inventory",
                    )
            retained_files[path] = payload
            _invoke_verification_hook(
                _verification_hook,
                "after_file_read",
                path,
            )
            _require_snapshot_path(
                root_path,
                tree_snapshot,
                path,
                retained_root_fd=_retained_root_fd,
            )
            output = runtime_outputs.get(path)
            if output is not None:
                metadata = inspect_runtime_asset_bytes(
                    payload,
                    role=output["role"],
                    media_type=output["media_type"],
                    expectations=output["constraints"],
                )
                if metadata != output["metadata"]:
                    _fail(
                        "assetpack_media_mismatch",
                        f"{path} metadata differs from the sealed manifest",
                    )
            elif path in notice_paths:
                try:
                    notice_text = payload.decode("utf-8")
                except UnicodeDecodeError as exc:
                    _fail(
                        "assetpack_notice_invalid",
                        f"{path} is not valid UTF-8: {exc}",
                    )
                if (
                    len(payload) > MAX_GENERIC_ASSETPACK_NOTICE_BYTES
                    or len(notice_text) > MAX_GENERIC_ASSETPACK_NOTICE_CHARACTERS
                ):
                    _fail(
                        "assetpack_notice_invalid",
                        f"{path} exceeds the 4096-character/byte limit",
                    )
            else:
                _fail(
                    "assetpack_inventory_mismatch",
                    f"{path} is not bound to an output or notice",
                )
            _invoke_verification_hook(
                _verification_hook,
                "after_file_semantics",
                path,
            )
            _require_snapshot_path(
                root_path,
                tree_snapshot,
                path,
                retained_root_fd=_retained_root_fd,
            )

        _require_tree_snapshot(
            root_path,
            tree_snapshot,
            retained_root_fd=_retained_root_fd,
        )
        if _retained_root_fd is None:
            _require_expected_parent_identity(root_path.parent, expected_parent_identity)
        return VerifiedGenericAssetpack(
            root_path,
            manifest,
            retained_files,
            retained_identity,
        )
    except GenericAssetpackError:
        raise
    except (
        CreationContractError,
        DirectoryPublishError,
        GenericAssetProductionError,
        OSError,
    ) as exc:
        _fail("assetpack_verification_failed", str(exc))


def _verify_owned_stage_subset(
    root: Path,
    journal: Mapping[str, object],
    *,
    retained_root_fd: int | None = None,
) -> None:
    retained_identity = (
        directory_identity(root, context="owned generic assetpack stage")
        if retained_root_fd is None
        else file_identity(_require_retained_root_fd(retained_root_fd))
    )
    files_before, directories_before = _walk_exact_tree(
        root,
        retained_root_fd=retained_root_fd,
    )
    if GENERIC_ASSETPACK_MANIFEST not in files_before:
        _fail(
            "assetpack_rollback_ambiguous",
            "owned stage has no manifest binding for its retained files",
        )
    manifest_payload = (
        _safe_artifact_bytes(
            root,
            GENERIC_ASSETPACK_MANIFEST,
            limit=MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
        )
        if retained_root_fd is None
        else _retained_file_bytes(
            retained_root_fd,
            GENERIC_ASSETPACK_MANIFEST,
            limit=MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
            reason_code="assetpack_rollback_ambiguous",
        )
    )
    if (
        len(manifest_payload) != journal["manifest_size_bytes"]
        or hashlib.sha256(manifest_payload).hexdigest() != journal["manifest_sha256"]
    ):
        _fail(
            "assetpack_rollback_ambiguous",
            "owned stage manifest differs from its journal",
        )
    manifest = validate_generic_assetpack_document(
        _decode_creation_object(
            manifest_payload,
            root / GENERIC_ASSETPACK_MANIFEST,
        )
    )
    if manifest_payload != serialize_generic_assetpack(manifest):
        _fail(
            "assetpack_rollback_ambiguous",
            "owned stage manifest is not canonical",
        )
    if (
        manifest["assetpack_id"] != journal["assetpack_id"]
        or manifest["content_hash"] != journal["content_hash"]
        or manifest["inventory"]["content_hash"] != journal["inventory_hash"]
        or manifest["release_ready_manifest"]["content_hash"] != journal["source_manifest_hash"]
    ):
        _fail(
            "assetpack_rollback_ambiguous",
            "owned stage manifest lineage differs from its journal",
        )
    inventory_files = {entry["path"]: entry for entry in manifest["inventory"]["files"]}
    allowed_files = {GENERIC_ASSETPACK_MANIFEST, *inventory_files}
    if not files_before.issubset(allowed_files):
        _fail(
            "assetpack_rollback_ambiguous",
            "owned stage contains a foreign file",
        )
    if not directories_before.issubset(_expected_directories(tuple(allowed_files))):
        _fail(
            "assetpack_rollback_ambiguous",
            "owned stage contains a foreign directory",
        )
    runtime_outputs = {
        output["runtime_path"]: output
        for asset in manifest["assets"]
        for output in asset["outputs"]
    }
    notice_paths = {
        output["runtime_notice"]["path"]
        for asset in manifest["assets"]
        for output in asset["outputs"]
    }
    for path in sorted(files_before - {GENERIC_ASSETPACK_MANIFEST}):
        record = inventory_files[path]
        if retained_root_fd is None:
            try:
                payload = read_verified_artifact_bytes(
                    root,
                    path,
                    expected_sha256=record["sha256"],
                    expected_size_bytes=record["size_bytes"],
                )
            except GenericAssetProductionError as exc:
                _fail(
                    "assetpack_rollback_ambiguous",
                    f"owned stage file {path} differs from its manifest: {exc.detail}",
                )
        else:
            payload = _retained_file_bytes(
                retained_root_fd,
                path,
                limit=max(record["size_bytes"], 1),
                reason_code="assetpack_rollback_ambiguous",
            )
            if (
                len(payload) != record["size_bytes"]
                or hashlib.sha256(payload).hexdigest() != record["sha256"]
            ):
                _fail(
                    "assetpack_rollback_ambiguous",
                    f"owned stage file {path} differs from its manifest",
                )
        output = runtime_outputs.get(path)
        if output is not None:
            if (
                inspect_runtime_asset_bytes(
                    payload,
                    role=output["role"],
                    media_type=output["media_type"],
                    expectations=output["constraints"],
                )
                != output["metadata"]
            ):
                _fail(
                    "assetpack_rollback_ambiguous",
                    f"owned stage media differs from its manifest: {path}",
                )
        elif path in notice_paths:
            try:
                payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                _fail(
                    "assetpack_rollback_ambiguous",
                    f"owned stage notice is invalid UTF-8: {path}: {exc}",
                )
        else:
            _fail(
                "assetpack_rollback_ambiguous",
                f"owned stage file is not bound: {path}",
            )
    files_after, directories_after = _walk_exact_tree(
        root,
        retained_root_fd=retained_root_fd,
    )
    if (
        files_after != files_before
        or directories_after != directories_before
        or (
            directory_identity(root, context="verified owned assetpack stage")
            if retained_root_fd is None
            else file_identity(_require_retained_root_fd(retained_root_fd))
        )
        != retained_identity
    ):
        _fail(
            "assetpack_rollback_ambiguous",
            "owned stage changed during subset verification",
        )


def _journal_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.assetpack.journal.json"


def _lock_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.assetpack.lock"


@dataclass(frozen=True, slots=True)
class _DestinationLockGuard:
    path: Path
    descriptor: int
    identity: DirectoryIdentity
    state: _TreeEntryState
    parent: PinnedOutputParent

    def require_binding(self) -> None:
        named_handle: int | None = None
        try:
            self.parent.assert_current()
            opened = descriptor_file_stat(self.descriptor)
            if self.parent.parent_fd is not None:
                named = os.stat(
                    self.path.name,
                    dir_fd=self.parent.parent_fd,
                    follow_symlinks=False,
                )
            else:
                api = self.parent.windows_api
                parent_handle = self.parent.windows_parent_handle
                if api is None or parent_handle is None:
                    _fail(
                        "assetpack_lock_changed",
                        "assetpack retained Windows lock parent is unavailable",
                    )
                named_handle = api.open_existing_file_strict(parent_handle, self.path.name)
                named = api.strict_entry_info(
                    named_handle,
                    context=f"retained assetpack lock {self.path.name}",
                )
        except (AssetContractError, OSError) as exc:
            _fail(
                "assetpack_lock_changed",
                f"assetpack publication lock binding changed: {exc}",
            )
        finally:
            if named_handle is not None and self.parent.windows_api is not None:
                self.parent.windows_api.close(named_handle)
        if (
            is_link_or_reparse(opened)
            or is_link_or_reparse(named)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or file_identity(opened) != self.identity
            or file_identity(named) != self.identity
            or _tree_entry_state(opened) != self.state
            or _tree_entry_state(named) != self.state
        ):
            _fail(
                "assetpack_lock_changed",
                "assetpack publication lock path binding changed",
            )


def _flush_retained_assetpack_parent(
    parent: PinnedOutputParent,
    *,
    context: str,
) -> None:
    try:
        parent.flush_durable(context=context)
    except (AssetContractError, OSError) as exc:
        _fail("assetpack_parent_unsafe", f"could not durably flush {context}: {exc}")


@contextmanager
def _destination_lock(
    destination: Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> Iterator[_DestinationLockGuard]:
    path = _lock_path(destination)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    native_handle: int | None = None
    retained_parent: PinnedOutputParent | None = None
    try:
        with open_verified_output_parent(destination.parent, create=False) as parent:
            retained_parent = parent
            checked_parent = _checked_parent_identity(expected_parent_identity)
            if checked_parent is not None and parent.identities[-1] != checked_parent:
                _fail(
                    "assetpack_parent_identity_mismatch",
                    "assetpack lock parent differs from its retained authority",
                )
            if parent.parent_fd is not None:
                descriptor = os.open(
                    path.name,
                    flags,
                    0o600,
                    dir_fd=parent.parent_fd,
                )
            else:
                api = parent.windows_api
                parent_handle = parent.windows_parent_handle
                if api is None or parent_handle is None:
                    _fail(
                        "assetpack_lock_unsafe",
                        "assetpack retained Windows lock parent is unavailable",
                    )
                native_handle = api.open_lock(parent_handle, path.name)
                descriptor = api.duplicate_to_descriptor(native_handle, writable=True)
            parent.assert_current()
            opened = descriptor_file_stat(descriptor)
            guard = _DestinationLockGuard(
                path=path,
                descriptor=descriptor,
                identity=file_identity(opened),
                state=_tree_entry_state(opened),
                parent=parent,
            )
            guard.require_binding()
            if opened.st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
                _flush_retained_assetpack_parent(parent, context="assetpack lock parent")
            elif opened.st_size != 1:
                _fail(
                    "assetpack_lock_unsafe",
                    "assetpack publication lock has invalid contents",
                )
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.read(descriptor, 1) != b"\0":
                _fail(
                    "assetpack_lock_unsafe",
                    "assetpack publication lock has invalid contents",
                )
            try:
                if os.name == "nt":  # pragma: no cover - native Windows CI
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                _fail(
                    "assetpack_publication_busy",
                    f"another assetpack publication is in progress: {exc}",
                )
            retained = descriptor_file_stat(descriptor)
            if (
                is_link_or_reparse(retained)
                or not stat.S_ISREG(retained.st_mode)
                or retained.st_nlink != 1
                or file_identity(retained) != guard.identity
            ):
                _fail("assetpack_lock_unsafe", "assetpack publication lock is unsafe")
            guard = _DestinationLockGuard(
                path=path,
                descriptor=descriptor,
                identity=file_identity(retained),
                state=_tree_entry_state(retained),
                parent=parent,
            )
            guard.require_binding()
            yield guard
            guard.require_binding()
    except GenericAssetpackError:
        raise
    except (AssetContractError, DirectoryPublishError, OSError) as exc:
        _fail("assetpack_lock_failed", str(exc))
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if native_handle is not None and retained_parent is not None:
            api = retained_parent.windows_api
            if api is not None:
                api.close(native_handle)


def _identity_document(identity: DirectoryIdentity) -> dict[str, int]:
    return {"device": identity[0], "inode": identity[1]}


def _checked_parent_identity(
    value: DirectoryIdentity | None,
) -> DirectoryIdentity | None:
    if value is None:
        return None
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
    ):
        _fail(
            "assetpack_parent_identity_invalid",
            "expected assetpack parent identity is invalid",
        )
    return value


def _require_expected_parent_identity(
    parent: Path,
    expected: DirectoryIdentity | None,
) -> None:
    checked = _checked_parent_identity(expected)
    if checked is None:
        return
    if directory_identity(parent, context="generic assetpack publication parent") != checked:
        _fail(
            "assetpack_parent_identity_mismatch",
            "assetpack parent differs from its retained authority",
        )


def _identity_from_document(
    value: object,
    context: str,
) -> DirectoryIdentity:
    identity = _object(value, context)
    _exact_keys(identity, _DIRECTORY_IDENTITY_FIELDS, context)
    return (
        _integer(identity.get("device"), f"{context}.device", minimum=0),
        _integer(identity.get("inode"), f"{context}.inode", minimum=0),
    )


def _journal_document(
    *,
    operation_id: str,
    state: str,
    stage: Path,
    destination: Path,
    stage_identity: DirectoryIdentity | None,
    manifest: Mapping[str, object],
    manifest_payload: bytes,
) -> dict[str, object]:
    return {
        "format": GENERIC_ASSETPACK_JOURNAL_FORMAT,
        "format_version": GENERIC_ASSETPACK_JOURNAL_VERSION,
        "operation_id": operation_id,
        "state": state,
        "stage_name": stage.name,
        "destination_name": destination.name,
        "stage_identity": (None if stage_identity is None else _identity_document(stage_identity)),
        "assetpack_id": manifest["assetpack_id"],
        "content_hash": manifest["content_hash"],
        "inventory_hash": manifest["inventory"]["content_hash"],
        "source_manifest_hash": manifest["release_ready_manifest"]["content_hash"],
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "manifest_size_bytes": len(manifest_payload),
    }


def _validate_journal(
    value: object,
    destination: Path,
) -> dict[str, Any]:
    try:
        journal = _object(value, "generic assetpack publication journal")
        _exact_keys(
            journal,
            _JOURNAL_FIELDS,
            "generic assetpack publication journal",
        )
        if (
            journal.get("format") != GENERIC_ASSETPACK_JOURNAL_FORMAT
            or journal.get("format_version") != GENERIC_ASSETPACK_JOURNAL_VERSION
            or isinstance(journal.get("format_version"), bool)
        ):
            _fail("assetpack_journal_invalid", "unknown journal format")
        operation_id = journal.get("operation_id")
        if (
            not isinstance(operation_id, str)
            or re.fullmatch(
                r"[0-9a-f]{32}",
                operation_id,
            )
            is None
        ):
            _fail("assetpack_journal_invalid", "operation_id is invalid")
        state = journal.get("state")
        if state not in {"intent", "copying", "ready"}:
            _fail("assetpack_journal_invalid", "journal state is invalid")
        if journal.get("destination_name") != destination.name:
            _fail(
                "assetpack_journal_invalid",
                "journal destination identity is invalid",
            )
        stage_name = journal.get("stage_name")
        if (
            not isinstance(stage_name, str)
            or "/" in stage_name
            or "\\" in stage_name
            or (
                stage_name != destination.name
                and not stage_name.startswith(f".{destination.name}.assetpack-")
            )
            or not is_portable_path_component(stage_name)
        ):
            _fail("assetpack_journal_invalid", "journal stage name is invalid")
        if state == "intent":
            if journal.get("stage_identity") is not None:
                _fail(
                    "assetpack_journal_invalid",
                    "intent journal must not claim a stage identity",
                )
        else:
            _identity_from_document(
                journal.get("stage_identity"),
                "generic assetpack publication journal.stage_identity",
            )
        _identifier(
            journal.get("assetpack_id"),
            "generic assetpack publication journal.assetpack_id",
        )
        for field in (
            "content_hash",
            "inventory_hash",
            "source_manifest_hash",
            "manifest_sha256",
        ):
            _sha256(
                journal.get(field),
                f"generic assetpack publication journal.{field}",
            )
        size = _integer(
            journal.get("manifest_size_bytes"),
            "generic assetpack publication journal.manifest_size_bytes",
            minimum=1,
        )
        if size > MAX_GENERIC_ASSETPACK_MANIFEST_BYTES:
            _fail(
                "assetpack_journal_invalid",
                "journal manifest size exceeds its limit",
            )
        return journal
    except GenericAssetpackError:
        raise
    except CreationContractError as exc:
        _fail("assetpack_journal_invalid", str(exc))


def _expected_journal_history(
    terminal: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    state = terminal["state"]
    intent = {
        **terminal,
        "state": "intent",
        "stage_identity": None,
    }
    if state == "intent":
        return (intent,)
    copying = {
        **terminal,
        "state": "copying",
    }
    if state == "copying":
        return intent, copying
    ready = {
        **terminal,
        "state": "ready",
    }
    return intent, copying, ready


def _journal_history_payloads(
    terminal: Mapping[str, Any],
) -> tuple[bytes, ...]:
    return tuple(canonical_json_bytes(record) for record in _expected_journal_history(terminal))


def _read_journal_record_state(
    path: Path,
    destination: Path | None = None,
    *,
    retained_parent: PinnedOutputParent | None = None,
) -> tuple[dict[str, Any], DirectoryIdentity, bytes, bool] | None:
    try:
        loaded = read_append_only_journal_history_state(
            path,
            max_record_bytes=MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
            max_file_bytes=MAX_GENERIC_ASSETPACK_JOURNAL_BYTES,
            retained_parent=retained_parent,
        )
    except DirectoryPublishError as exc:
        _fail("assetpack_journal_invalid", str(exc))
    if loaded is None:
        return None
    payloads, identity, partial_tail = loaded
    decoded_records: list[dict[str, Any]] = []
    for payload in payloads:
        try:
            decoded_records.append(_decode_creation_object(payload, path))
        except CreationContractError as exc:
            _fail("assetpack_journal_invalid", str(exc))
    decoded = decoded_records[-1]
    if destination is None:
        name = decoded.get("destination_name")
        if not isinstance(name, str):
            _fail("assetpack_journal_invalid", "journal destination is invalid")
        destination = path.parent / name
    documents = tuple(_validate_journal(record, destination) for record in decoded_records)
    for document, payload in zip(documents, payloads, strict=True):
        if canonical_json_bytes(document) != payload:
            _fail("assetpack_journal_invalid", "journal record is not canonical")
    if documents != _expected_journal_history(documents[-1]):
        _fail(
            "assetpack_journal_invalid",
            "journal complete history is not the exact canonical state prefix",
        )
    return documents[-1], identity, payloads[-1], partial_tail


def _read_journal_record(
    path: Path,
    destination: Path | None = None,
    *,
    retained_parent: PinnedOutputParent | None = None,
) -> tuple[dict[str, Any], DirectoryIdentity, bytes] | None:
    loaded = _read_journal_record_state(
        path,
        destination,
        retained_parent=retained_parent,
    )
    if loaded is None:
        return None
    return loaded[0], loaded[1], loaded[2]


def _write_journal(
    path: Path,
    document: dict[str, Any],
    *,
    lock: _DestinationLockGuard,
    create: bool,
    expected_document: dict[str, Any] | None = None,
    expected_identity: DirectoryIdentity | None = None,
) -> DirectoryIdentity:
    payload = canonical_json_bytes(document)
    try:
        lock.require_binding()
        if create:
            try:
                identity = create_append_only_journal(
                    path,
                    payload,
                    max_record_bytes=MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
                    retained_parent=lock.parent,
                )
            except FileExistsError:
                _fail(
                    "assetpack_recovery_required",
                    "an incomplete assetpack publication journal already exists",
                    recovery_evidence=retained_recovery_evidence(journal_path=path),
                )
            lock.require_binding()
            return identity
        if expected_document is None or expected_identity is None:
            _fail(
                "assetpack_journal_invalid",
                "journal transition lacks exact prior identity",
            )
        loaded = _read_journal_record_state(
            path,
            retained_parent=lock.parent,
        )
        expected_payload = canonical_json_bytes(expected_document)
        if (
            loaded is None
            or loaded[0] != expected_document
            or loaded[1] != expected_identity
            or loaded[2] != expected_payload
        ):
            _fail(
                "assetpack_journal_changed",
                "journal changed before its append-only transition",
            )
        lock.require_binding()
        identity = append_append_only_journal(
            path,
            expected_identity=expected_identity,
            expected_payload=expected_payload,
            expected_history=_journal_history_payloads(expected_document),
            updated_payload=payload,
            max_record_bytes=MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
            max_file_bytes=MAX_GENERIC_ASSETPACK_JOURNAL_BYTES,
            repair_partial_tail=True,
            retained_parent=lock.parent,
        )
        lock.require_binding()
        return identity
    except GenericAssetpackError:
        raise
    except DirectoryPublishError as exc:
        _fail("assetpack_journal_failed", str(exc))


def _remove_journal(
    path: Path,
    document: dict[str, Any],
    identity: DirectoryIdentity,
    *,
    lock: _DestinationLockGuard,
    absent_paths: Sequence[Path] = (),
) -> None:
    lock.require_binding()
    loaded = _read_journal_record_state(
        path,
        retained_parent=lock.parent,
    )
    expected_payload = canonical_json_bytes(document)
    expected_history = _journal_history_payloads(document)
    if (
        loaded is None
        or loaded[0] != document
        or loaded[1] != identity
        or loaded[2] != expected_payload
        or loaded[3]
    ):
        _fail(
            "assetpack_journal_changed",
            "journal changed before its identity-bound removal",
        )
    try:
        parent = lock.parent
        lock.require_binding()
        parent.assert_current()
        names: list[str] = []
        for absent in absent_paths:
            if Path(os.path.abspath(absent.parent)) != parent.path:
                _fail(
                    "assetpack_publication_indeterminate",
                    "cleanup name is outside the retained journal parent",
                )
            names.append(absent.name)
        require_pinned_names_absent(
            parent,
            tuple(names),
            context="D3 pre-journal cleanup",
        )
        retained_journal = remove_d3_append_only_journal(
            path,
            expected_identity=identity,
            expected_history=expected_history,
            max_record_bytes=MAX_GENERIC_ASSETPACK_MANIFEST_BYTES,
            max_file_bytes=MAX_GENERIC_ASSETPACK_JOURNAL_BYTES,
            retained_parent=parent,
        )
        if sys.platform.startswith("linux") and os.name == "posix":
            expected_retained = retained_journal_evidence_path(path, identity)
            if retained_journal != expected_retained:
                _fail(
                    "assetpack_publication_indeterminate",
                    "terminal journal evidence locator changed",
                )
        elif retained_journal is not None:
            _fail(
                "assetpack_publication_indeterminate",
                "unexpected terminal journal evidence was returned",
            )
        require_pinned_names_absent(
            parent,
            tuple((*names, path.name)),
            context="D3 final cleanup",
        )
        parent.assert_current()
        lock.require_binding()
    except DirectoryPublishIndeterminateError as exc:
        _fail("assetpack_publication_indeterminate", str(exc))
    except (AssetContractError, DirectoryPublishError) as exc:
        indeterminate = _indeterminate_directory_error(exc)
        if indeterminate is not None:
            _fail("assetpack_publication_indeterminate", str(indeterminate))
        _fail("assetpack_journal_failed", str(exc))
    lock.require_binding()


def _optional_directory_identity(
    path: Path,
    *,
    context: str,
) -> DirectoryIdentity | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("assetpack_directory_invalid", f"could not inspect {context}: {exc}")
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _fail("assetpack_directory_invalid", f"{context} must be a real directory")
    return info.st_dev, info.st_ino


def _require_unbound_path_absent(
    path: Path,
    *,
    reason_code: str,
    context: str,
) -> None:
    try:
        path_file_stat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        _fail(
            reason_code,
            f"{context} cannot be proven absent and must be preserved: {exc}",
        )
    _fail(
        reason_code,
        f"{context} exists without a journal-bound identity and must be preserved",
    )


_PublicationHook = Callable[[str, str | None], None]


class _AnchoredStageWriter:
    """Create one private stage only through retained parent/directory anchors."""

    def __init__(
        self,
        stage: Path,
        parent: PinnedOutputParent,
        lock: _DestinationLockGuard,
        *,
        root_native: int,
        root_identity: DirectoryIdentity,
        publication_hook: _PublicationHook | None,
    ) -> None:
        self.stage = stage
        self.parent = parent
        self.lock = lock
        self.identity = root_identity
        self.publication_hook = publication_hook
        self._directory_identities: dict[str, DirectoryIdentity] = {
            "": root_identity,
        }
        self._posix_directories: dict[str, int] = {}
        self._windows_directories: dict[str, int] = {}
        if parent.parent_fd is not None:
            self._posix_directories[""] = root_native
        else:
            self._windows_directories[""] = root_native
        self._posix_files: dict[str, tuple[int, _TreeEntryState]] = {}
        self._windows_files: dict[str, tuple[int, _TreeEntryState]] = {}
        self._closed = False

    def _hook(self, event: str, relative: str | None = None) -> None:
        if self.publication_hook is not None:
            self.publication_hook(event, relative)

    def _directory_path(self, relative: str) -> Path:
        return self.stage if not relative else self.stage / PurePosixPath(relative)

    def _directory_native(self, relative: str) -> int:
        if self.parent.parent_fd is not None:
            return self._posix_directories[relative]
        return self._windows_directories[relative]

    def require_binding(self) -> None:
        self.lock.require_binding()
        self.parent.assert_current()
        for relative, expected_identity in self._directory_identities.items():
            native = self._directory_native(relative)
            opened = (
                descriptor_file_stat(native)
                if self.parent.parent_fd is not None
                else windows_handle_file_stat(native)
            )
            named = path_file_stat(self._directory_path(relative))
            if (
                is_link_or_reparse(opened)
                or is_link_or_reparse(named)
                or not stat.S_ISDIR(opened.st_mode)
                or not stat.S_ISDIR(named.st_mode)
                or file_identity(opened) != expected_identity
                or file_identity(named) != expected_identity
            ):
                _fail(
                    "assetpack_stage_changed",
                    f"retained stage directory changed: {self._directory_path(relative)}",
                )
        file_records = (
            self._posix_files if self.parent.parent_fd is not None else self._windows_files
        )
        for relative, (native, expected_state) in file_records.items():
            opened = (
                descriptor_file_stat(native)
                if self.parent.parent_fd is not None
                else windows_handle_file_stat(native)
            )
            named = path_file_stat(self.stage / PurePosixPath(relative))
            if (
                is_link_or_reparse(opened)
                or is_link_or_reparse(named)
                or not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or opened.st_nlink != 1
                or named.st_nlink != 1
                or _tree_entry_state(opened) != expected_state
                or _tree_entry_state(named) != expected_state
            ):
                _fail(
                    "assetpack_stage_changed",
                    f"retained staged file changed: {relative}",
                )

    def _ensure_parent(self, relative: PurePosixPath) -> str:
        current = ""
        for part in relative.parts[:-1]:
            child = part if not current else f"{current}/{part}"
            if child in self._directory_identities:
                current = child
                continue
            self.require_binding()
            parent_native = self._directory_native(current)
            try:
                if self.parent.parent_fd is not None:
                    os.mkdir(part, mode=0o700, dir_fd=parent_native)
                    native = os.open(
                        part,
                        os.O_RDONLY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_native,
                    )
                    os.fchmod(native, 0o700)
                    opened = descriptor_file_stat(native)
                    self._posix_directories[child] = native
                else:
                    api = self.parent.windows_api
                    if api is None:
                        _fail(
                            "assetpack_stage_unsafe",
                            "Windows retained stage API is unavailable",
                        )
                    native = api.create_directory(
                        parent_native,
                        part,
                        request_delete=False,
                    )
                    opened = windows_handle_file_stat(native)
                    self._windows_directories[child] = native
            except (AssetContractError, OSError) as exc:
                _fail(
                    "assetpack_stage_write_failed",
                    f"could not create retained stage directory {child}: {exc}",
                )
            if is_link_or_reparse(opened) or not stat.S_ISDIR(opened.st_mode):
                _fail(
                    "assetpack_stage_unsafe",
                    f"new staged directory is unsafe: {child}",
                )
            self._directory_identities[child] = file_identity(opened)
            self._hook("after_stage_directory_created", child)
            self.require_binding()
            current = child
        return current

    def write_file(self, relative: str, payload: bytes) -> None:
        relative_path = PurePosixPath(relative)
        parent_relative = self._ensure_parent(relative_path)
        self.require_binding()
        self._hook("before_stage_file_write", relative)
        self.require_binding()
        parent_native = self._directory_native(parent_relative)
        native: int | None = None
        descriptor: int | None = None
        try:
            if self.parent.parent_fd is not None:
                native = os.open(
                    relative_path.name,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_native,
                )
                descriptor = native
            else:
                api = self.parent.windows_api
                if api is None:
                    _fail(
                        "assetpack_stage_unsafe",
                        "Windows retained stage API is unavailable",
                    )
                native = api.create_file(
                    parent_native,
                    relative_path.name,
                    request_delete=False,
                )
                descriptor = api.duplicate_to_descriptor(native, writable=True)
            opened = descriptor_file_stat(descriptor)
            if (
                is_link_or_reparse(opened)
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size != 0
            ):
                _fail(
                    "assetpack_stage_unsafe",
                    f"staged file is unsafe: {relative}",
                )
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short staged assetpack write")
                view = view[written:]
            os.fsync(descriptor)
            sealed = descriptor_file_stat(descriptor)
            if (
                not stat.S_ISREG(sealed.st_mode)
                or sealed.st_nlink != 1
                or sealed.st_size != len(payload)
            ):
                _fail(
                    "assetpack_stage_changed",
                    f"staged file changed while writing: {relative}",
                )
            state = _tree_entry_state(sealed)
            if self.parent.parent_fd is not None:
                assert native is not None
                self._posix_files[relative] = (native, state)
                descriptor = None
            else:
                assert native is not None
                self._windows_files[relative] = (native, state)
                native = None
            self._hook("after_stage_file_write", relative)
            self.require_binding()
        except GenericAssetpackError:
            raise
        except (AssetContractError, OSError) as exc:
            _fail(
                "assetpack_stage_write_failed",
                f"could not write retained stage file {relative}: {exc}",
            )
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if native is not None and self.parent.parent_fd is None:
                api = self.parent.windows_api
                if api is not None:
                    api.close(native)

    def fsync(self) -> None:
        self.require_binding()
        if self.parent.parent_fd is not None:
            for relative in sorted(
                self._posix_directories,
                key=lambda item: (
                    -len(PurePosixPath(item).parts),
                    item.encode("utf-8"),
                ),
            ):
                os.fsync(self._posix_directories[relative])
            os.fsync(self.parent.parent_fd)
        else:
            api = self.parent.windows_api
            if api is None:
                _fail(
                    "assetpack_stage_unsafe",
                    "Windows retained stage API is unavailable",
                )
            for relative in sorted(
                self._windows_directories,
                key=lambda item: (
                    -len(PurePosixPath(item).parts),
                    item.encode("utf-8"),
                ),
            ):
                api.flush_handle(
                    self._windows_directories[relative],
                    context=f"assetpack stage directory {relative or '.'}",
                )
            fsync_directory(
                self.stage.parent,
                context="assetpack stage parent",
            )
        self.require_binding()

    def close(self) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        if self.parent.parent_fd is not None:
            descriptors = [native for native, _state in self._posix_files.values()] + [
                self._posix_directories[relative]
                for relative in sorted(
                    self._posix_directories,
                    key=lambda item: len(PurePosixPath(item).parts),
                    reverse=True,
                )
            ]
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    errors.append(exc)
        else:
            api = self.parent.windows_api
            if api is not None:
                handles = [native for native, _state in self._windows_files.values()] + [
                    self._windows_directories[relative]
                    for relative in sorted(
                        self._windows_directories,
                        key=lambda item: len(PurePosixPath(item).parts),
                        reverse=True,
                    )
                ]
                for handle in handles:
                    try:
                        api.close(handle)
                    except AssetContractError as exc:
                        errors.append(exc)
        self._closed = True
        if errors:
            _fail(
                "assetpack_stage_cleanup_failed",
                f"could not release retained stage handles: {errors[0]}",
            )


@contextmanager
def _create_anchored_stage(
    stage: Path,
    lock: _DestinationLockGuard,
    *,
    expected_parent_identity: DirectoryIdentity | None,
    publication_hook: _PublicationHook | None,
) -> Iterator[_AnchoredStageWriter]:
    writer: _AnchoredStageWriter | None = None
    orphan_native: int | None = None
    orphan_close: Callable[[int], None] | None = None
    try:
        parent = lock.parent
        lock.require_binding()
        parent.assert_current()
        if (
            expected_parent_identity is not None
            and parent.identities[-1] != expected_parent_identity
        ):
            _fail(
                "assetpack_parent_identity_mismatch",
                "assetpack stage parent differs from its retained authority",
            )
        if parent.parent_fd is not None:
            os.mkdir(stage.name, mode=0o700, dir_fd=parent.parent_fd)
            root_native = os.open(
                stage.name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent.parent_fd,
            )
            orphan_native = root_native
            os.fchmod(root_native, 0o700)
            opened = descriptor_file_stat(root_native)
        else:
            api = parent.windows_api
            parent_handle = parent.windows_parent_handle
            if api is None or parent_handle is None:
                _fail(
                    "assetpack_stage_unsafe",
                    "Windows retained stage API is unavailable",
                )
            root_native = api.create_directory(
                parent_handle,
                stage.name,
                request_delete=False,
            )
            orphan_native = root_native
            orphan_close = api.close
            opened = windows_handle_file_stat(root_native)
        root_identity = file_identity(opened)
        named = path_file_stat(stage)
        if (
            is_link_or_reparse(opened)
            or is_link_or_reparse(named)
            or not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or file_identity(named) != root_identity
        ):
            _fail(
                "assetpack_stage_unsafe",
                "new retained stage identity is unsafe",
            )
        writer = _AnchoredStageWriter(
            stage,
            parent,
            lock,
            root_native=root_native,
            root_identity=root_identity,
            publication_hook=publication_hook,
        )
        orphan_native = None
        writer._hook("after_stage_created")  # noqa: SLF001
        writer.require_binding()
        try:
            yield writer
            writer.require_binding()
        finally:
            primary = sys.exception()
            try:
                writer.close()
            except GenericAssetpackError as cleanup_error:
                if primary is not None:
                    primary.add_note(str(cleanup_error))
                else:
                    raise
            writer = None
    except GenericAssetpackError:
        raise
    except (AssetContractError, FileExistsError, OSError) as exc:
        _fail(
            "assetpack_stage_write_failed",
            f"could not retain generic assetpack stage: {exc}",
        )
    finally:
        if writer is not None:
            writer.close()
        if orphan_native is not None:
            primary = sys.exception()
            try:
                if orphan_close is None:
                    os.close(orphan_native)
                else:
                    orphan_close(orphan_native)
            except (AssetContractError, OSError) as cleanup_error:
                if primary is not None:
                    primary.add_note(str(cleanup_error))
                else:
                    _fail(
                        "assetpack_stage_cleanup_failed",
                        f"could not release retained stage root: {cleanup_error}",
                    )


def _journal_matches_verified(
    journal: Mapping[str, object],
    verified: VerifiedGenericAssetpack,
) -> None:
    manifest = verified.manifest
    manifest_payload = verified.read_bytes(GENERIC_ASSETPACK_MANIFEST)
    expected = {
        "assetpack_id": manifest["assetpack_id"],
        "content_hash": manifest["content_hash"],
        "inventory_hash": manifest["inventory"]["content_hash"],
        "source_manifest_hash": manifest["release_ready_manifest"]["content_hash"],
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "manifest_size_bytes": len(manifest_payload),
    }
    for field, value in expected.items():
        if journal[field] != value:
            _fail(
                "assetpack_recovery_mismatch",
                f"journal {field} does not match the exact pack",
            )


def _recover_journal_locked(
    destination: Path,
    lock: _DestinationLockGuard,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> VerifiedGenericAssetpack | None:
    effective_parent_identity = lock.parent.identities[-1]
    if (
        expected_parent_identity is not None
        and expected_parent_identity != effective_parent_identity
    ):
        _fail(
            "assetpack_parent_identity_mismatch",
            "assetpack recovery parent differs from its retained authority",
        )
    _require_expected_parent_identity(destination.parent, effective_parent_identity)
    lock.require_binding()
    path = _journal_path(destination)
    loaded = _read_journal_record_state(
        path,
        destination,
        retained_parent=lock.parent,
    )
    if loaded is None:
        if (
            _optional_directory_identity(
                destination,
                context="existing assetpack destination",
            )
            is None
        ):
            return None
        verified = verify_generic_assetpack(
            destination,
            expected_parent_identity=effective_parent_identity,
        )
        lock.require_binding()
        return verified
    journal, journal_identity, _payload, partial_tail = loaded
    if partial_tail and journal["state"] != "copying":
        _fail(
            "assetpack_journal_invalid",
            "journal has a partial tail without an allowed next transition",
        )
    expected_hash = journal["content_hash"]
    assert isinstance(expected_hash, str)

    stage = destination.parent / journal["stage_name"]
    if journal["state"] == "intent":
        _require_unbound_path_absent(
            destination,
            reason_code="assetpack_recovery_ambiguous",
            context="intent destination path",
        )
        _require_unbound_path_absent(
            stage,
            reason_code="assetpack_recovery_ambiguous",
            context="intent stage path",
        )
        _remove_journal(
            path,
            journal,
            journal_identity,
            lock=lock,
            absent_paths=(stage, destination),
        )
        return None

    stage_identity = _optional_directory_identity(stage, context="assetpack recovery stage")
    destination_identity = _optional_directory_identity(
        destination,
        context="assetpack recovery destination",
    )
    expected_identity = _identity_from_document(
        journal["stage_identity"],
        "generic assetpack publication journal.stage_identity",
    )
    source: Path
    if stage_identity == expected_identity and destination_identity is None:
        source = stage
    elif destination_identity == expected_identity and stage_identity is None:
        source = destination
    else:
        _fail(
            "assetpack_recovery_ambiguous",
            "journal stage/destination identities are missing, changed, or conflicting",
        )
    if journal["state"] == "copying" and source == stage:
        stage_snapshot = _snapshot_exact_tree(stage)
        if not stage_snapshot.files and not stage_snapshot.directories:
            if partial_tail:
                _fail(
                    "assetpack_recovery_ambiguous",
                    "copying journal tail cannot be repaired without a complete stage",
                )
            lock.require_binding()
            try:
                parent = lock.parent
                parent.assert_current()
                lock.require_binding()
                remove_verified_empty_directory(
                    stage,
                    expected_identity,
                    retained_parent=parent,
                )
                require_pinned_names_absent(
                    parent,
                    (stage.name,),
                    context="D3 recovered empty stage cleanup",
                )
                parent.assert_current()
                lock.require_binding()
            except DirectoryPublishRecoveryRequiredError as exc:
                _fail(
                    "assetpack_recovery_required",
                    "automatic cleanup is unavailable; the exact owned stage and "
                    f"publication journal were retained for explicit recovery: {exc}",
                    recovery_evidence=retained_recovery_evidence(
                        stage_path=stage,
                        stage_identity=expected_identity,
                        journal_path=path,
                        journal_identity=journal_identity,
                    ),
                )
            except DirectoryPublishIndeterminateError as exc:
                _fail("assetpack_recovery_indeterminate", str(exc))
            except (AssetContractError, DirectoryPublishError) as exc:
                indeterminate = _indeterminate_directory_error(exc)
                if indeterminate is not None:
                    _fail("assetpack_recovery_indeterminate", str(indeterminate))
                _fail("assetpack_recovery_failed", str(exc))
            lock.require_binding()
            _remove_journal(
                path,
                journal,
                journal_identity,
                lock=lock,
                absent_paths=(stage, destination),
            )
            return None
        if GENERIC_ASSETPACK_MANIFEST not in stage_snapshot.files:
            _fail(
                "assetpack_recovery_ambiguous",
                "copying stage contains unbound entries without an assetpack manifest",
            )
    verified = verify_generic_assetpack(
        source,
        expected_content_hash=expected_hash,
        expected_parent_identity=effective_parent_identity,
    )
    try:
        _journal_matches_verified(journal, verified)
    finally:
        verified.close()

    if journal["state"] == "copying":
        ready = {**journal, "state": "ready"}
        journal_identity = _write_journal(
            path,
            ready,
            lock=lock,
            create=False,
            expected_document=journal,
            expected_identity=journal_identity,
        )
        journal = ready

    if source == stage:
        lock.require_binding()
        try:
            with publish_directory_noreplace(
                stage,
                destination,
                expected_source_identity=expected_identity,
                expected_parent_identity=effective_parent_identity,
            ) as published_identity:
                if published_identity != expected_identity:
                    _fail(
                        "assetpack_publication_identity_mismatch",
                        "recovered publication identity changed",
                    )
                verified_destination = verify_generic_assetpack(
                    destination,
                    expected_content_hash=expected_hash,
                    expected_parent_identity=effective_parent_identity,
                )
                try:
                    _journal_matches_verified(journal, verified_destination)
                    lock.require_binding()
                except BaseException:
                    verified_destination.close()
                    raise
        except DirectoryPublishIndeterminateError as exc:
            _fail("assetpack_publication_indeterminate", str(exc))
        except (DirectoryPublishError, FileExistsError) as exc:
            _fail("assetpack_recovery_failed", str(exc))
    else:
        verified_destination = verify_generic_assetpack(
            destination,
            expected_content_hash=expected_hash,
            expected_parent_identity=effective_parent_identity,
        )
        try:
            _journal_matches_verified(journal, verified_destination)
            lock.require_binding()
        except BaseException:
            verified_destination.close()
            raise
    try:
        _remove_journal(
            path,
            journal,
            journal_identity,
            lock=lock,
            absent_paths=(stage,),
        )
    except BaseException:
        verified_destination.close()
        raise
    return verified_destination


def recover_generic_assetpack(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> VerifiedGenericAssetpack | None:
    """Recover only the exact hash/identity-bound D3 publication journal."""

    destination_path = Path(destination).absolute()
    checked_parent = _checked_parent_identity(expected_parent_identity)
    with _destination_lock(
        destination_path,
        expected_parent_identity=checked_parent,
    ) as lock:
        effective_parent_identity = lock.parent.identities[-1]
        return _recover_journal_locked(
            destination_path,
            lock,
            expected_parent_identity=effective_parent_identity,
        )


def rollback_generic_assetpack(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> dict[str, object]:
    """Roll back only a verified uncommitted private D3 stage."""

    destination_path = Path(destination).absolute()
    checked_parent = _checked_parent_identity(expected_parent_identity)
    with _destination_lock(
        destination_path,
        expected_parent_identity=checked_parent,
    ) as lock:
        effective_parent_identity = lock.parent.identities[-1]
        _require_expected_parent_identity(
            destination_path.parent,
            effective_parent_identity,
        )
        lock.require_binding()
        path = _journal_path(destination_path)
        loaded = _read_journal_record_state(
            path,
            destination_path,
            retained_parent=lock.parent,
        )
        if loaded is None:
            _require_expected_parent_identity(
                destination_path.parent,
                effective_parent_identity,
            )
            return {"status": "no_operation"}
        journal, journal_identity, _payload, partial_tail = loaded
        if partial_tail:
            _fail(
                "assetpack_rollback_ambiguous",
                "rollback preserves a journal with an incomplete transition",
            )
        stage = destination_path.parent / journal["stage_name"]
        if journal["state"] == "intent":
            _require_unbound_path_absent(
                destination_path,
                reason_code="assetpack_rollback_committed",
                context="intent destination path",
            )
            _require_unbound_path_absent(
                stage,
                reason_code="assetpack_rollback_ambiguous",
                context="intent stage path",
            )
        else:
            stage_identity = _optional_directory_identity(
                stage,
                context="assetpack rollback stage",
            )
            destination_identity = _optional_directory_identity(
                destination_path,
                context="assetpack rollback destination",
            )
            if destination_identity is not None:
                _fail(
                    "assetpack_rollback_committed",
                    "rollback never removes a visible destination",
                )
            expected_identity = _identity_from_document(
                journal["stage_identity"],
                "generic assetpack publication journal.stage_identity",
            )
            if stage_identity != expected_identity:
                _fail(
                    "assetpack_rollback_ambiguous",
                    "rollback stage identity changed",
                )

            stage_snapshot = _snapshot_exact_tree(stage)
            if (
                journal["state"] == "copying"
                and not stage_snapshot.files
                and not stage_snapshot.directories
            ):
                lock.require_binding()
                try:
                    parent = lock.parent
                    parent.assert_current()
                    if parent.identities[-1] != effective_parent_identity:
                        _fail(
                            "assetpack_parent_identity_mismatch",
                            "assetpack rollback parent differs from its retained authority",
                        )
                    remove_verified_empty_directory(
                        stage,
                        expected_identity,
                        retained_parent=parent,
                    )
                    require_pinned_names_absent(
                        parent,
                        (stage.name,),
                        context="D3 rollback empty stage cleanup",
                    )
                    parent.assert_current()
                    lock.require_binding()
                except DirectoryPublishRecoveryRequiredError as exc:
                    _fail(
                        "assetpack_rollback_recovery_required",
                        "automatic rollback cleanup is unavailable; the exact owned "
                        f"stage and publication journal were retained: {exc}",
                        recovery_evidence=retained_recovery_evidence(
                            stage_path=stage,
                            stage_identity=expected_identity,
                            journal_path=path,
                            journal_identity=journal_identity,
                        ),
                    )
                except DirectoryPublishIndeterminateError as exc:
                    _fail("assetpack_rollback_indeterminate", str(exc))
                except (AssetContractError, DirectoryPublishError) as exc:
                    indeterminate = _indeterminate_directory_error(exc)
                    if indeterminate is not None:
                        _fail("assetpack_rollback_indeterminate", str(indeterminate))
                    _fail("assetpack_rollback_failed", str(exc))
                lock.require_binding()
            else:
                if (
                    journal["state"] == "copying"
                    and GENERIC_ASSETPACK_MANIFEST not in stage_snapshot.files
                ):
                    _fail(
                        "assetpack_rollback_ambiguous",
                        "copying stage contains unbound entries without an assetpack manifest",
                    )

                def verify_owned_stage(
                    stage_path: Path,
                    retained_root_fd: int | None,
                ) -> None:
                    _verify_owned_stage_subset(
                        stage_path,
                        journal,
                        retained_root_fd=retained_root_fd,
                    )

                try:
                    lock.require_binding()
                    parent = lock.parent
                    parent.assert_current()
                    if parent.identities[-1] != effective_parent_identity:
                        _fail(
                            "assetpack_parent_identity_mismatch",
                            "assetpack rollback parent differs from its retained authority",
                        )
                    quarantine_and_remove_verified_directory(
                        stage,
                        expected_identity,
                        verify_retained=verify_owned_stage,
                        retained_parent=parent,
                    )
                    require_pinned_names_absent(
                        parent,
                        (stage.name,),
                        context="D3 rollback stage cleanup",
                    )
                    parent.assert_current()
                    lock.require_binding()
                except DirectoryPublishRecoveryRequiredError as exc:
                    _fail(
                        "assetpack_rollback_recovery_required",
                        "automatic rollback cleanup is unavailable; the exact owned "
                        f"stage and publication journal were retained: {exc}",
                        recovery_evidence=retained_recovery_evidence(
                            stage_path=stage,
                            stage_identity=expected_identity,
                            journal_path=path,
                            journal_identity=journal_identity,
                        ),
                    )
                except DirectoryPublishIndeterminateError as exc:
                    _fail("assetpack_rollback_indeterminate", str(exc))
                except (AssetContractError, DirectoryPublishError) as exc:
                    indeterminate = _indeterminate_directory_error(exc)
                    if indeterminate is not None:
                        _fail("assetpack_rollback_indeterminate", str(indeterminate))
                    _fail("assetpack_rollback_failed", str(exc))
        _remove_journal(
            path,
            journal,
            journal_identity,
            lock=lock,
            absent_paths=(stage, destination_path),
        )
        _require_expected_parent_identity(
            destination_path.parent,
            effective_parent_identity,
        )
        return {
            "status": "rolled_back",
            "operation_id": journal["operation_id"],
            "content_hash": journal["content_hash"],
        }


def seal_generic_assetpack(
    destination: str | Path,
    manifest: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    asset_records: Sequence[Mapping[str, object]],
    artifact_root: str | Path,
    qa_reviews: Sequence[object] | None = None,
    release_authority: object | None = None,
    expected_parent_identity: DirectoryIdentity | None = None,
    _publication_hook: _PublicationHook | None = None,
) -> VerifiedGenericAssetpack:
    """Capture, durably stage, integrally verify, and exclusively publish one D3 pack."""

    if release_authority is None:
        _fail(
            "assetpack_release_authority_required",
            "assetpack sealing requires exact verified release authority",
        )

    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        _fail(
            "assetpack_platform_unsupported",
            "generic assetpack publication supports only Linux and Windows",
        )
    document, files = _prepare_generic_assetpack(
        manifest,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        asset_records=asset_records,
        artifact_root=artifact_root,
        qa_reviews=qa_reviews,
    )
    try:
        from worldforge.generic_asset_authority import (
            GenericAssetAuthorityError,
            require_verified_asset_release_authority,
        )

        require_verified_asset_release_authority(
            release_authority,
            manifest=manifest,
            assetpack=document,
            reviews=() if qa_reviews is None else qa_reviews,
        )
    except GenericAssetAuthorityError as exc:
        _fail(
            "assetpack_release_authority_invalid",
            f"{exc.reason_code}: {exc.detail}",
        )
    manifest_payload = serialize_generic_assetpack(document)
    destination_input = Path(destination)
    destination_path = (
        destination_input if destination_input.is_absolute() else Path.cwd() / destination_input
    )
    checked_parent = _checked_parent_identity(expected_parent_identity)
    if (
        not is_portable_path_component(destination_path.name)
        or destination_path.name.startswith(".")
        or len(destination_path.name.encode("utf-8")) > 160
    ):
        _fail("assetpack_destination_invalid", "destination name is not portable")
    if not destination_path.parent.exists():
        _fail("assetpack_destination_invalid", "destination parent must already exist")
    lexical_issues = validate_lexical_directory_root(destination_path.parent)
    if lexical_issues:
        _fail(
            "assetpack_destination_invalid",
            f"destination parent is unsafe: {', '.join(lexical_issues)}",
        )
    _require_expected_parent_identity(destination_path.parent, checked_parent)

    with _destination_lock(
        destination_path,
        expected_parent_identity=checked_parent,
    ) as lock:
        effective_parent_identity = lock.parent.identities[-1]
        if _publication_hook is not None:
            _publication_hook("after_lock_acquired", None)
        lock.require_binding()
        recovered = _recover_journal_locked(
            destination_path,
            lock,
            expected_parent_identity=effective_parent_identity,
        )
        if recovered is not None:
            if recovered.manifest["content_hash"] == document["content_hash"]:
                return recovered
            recovered.close()
            _fail(
                "assetpack_destination_exists",
                "destination contains a different immutable generic assetpack",
            )
        try:
            destination_path = assert_new_repository_target(
                destination_path,
                repository_type="generic assetpack",
            )
        except RepositoryBoundaryError as exc:
            if destination_path.exists() or destination_path.is_symlink():
                _fail("assetpack_destination_exists", str(exc))
            _fail("assetpack_destination_invalid", str(exc))

        operation_id = uuid.uuid4().hex
        stage = destination_path.parent / (f".{destination_path.name}.assetpack-{operation_id}")
        journal_path = _journal_path(destination_path)
        journal_identity: DirectoryIdentity | None = None
        journal: dict[str, Any] | None = None
        stage_identity: DirectoryIdentity | None = None
        verified_stage: VerifiedGenericAssetpack | None = None
        published: VerifiedGenericAssetpack | None = None
        try:
            intent = _journal_document(
                operation_id=operation_id,
                state="intent",
                stage=stage,
                destination=destination_path,
                stage_identity=None,
                manifest=document,
                manifest_payload=manifest_payload,
            )
            journal_identity = _write_journal(
                journal_path,
                intent,
                lock=lock,
                create=True,
            )
            journal = intent
            with _create_anchored_stage(
                stage,
                lock,
                expected_parent_identity=effective_parent_identity,
                publication_hook=_publication_hook,
            ) as stage_writer:
                stage_identity = stage_writer.identity
                copying = _journal_document(
                    operation_id=operation_id,
                    state="copying",
                    stage=stage,
                    destination=destination_path,
                    stage_identity=stage_identity,
                    manifest=document,
                    manifest_payload=manifest_payload,
                )
                journal_identity = _write_journal(
                    journal_path,
                    copying,
                    lock=lock,
                    create=False,
                    expected_document=journal,
                    expected_identity=journal_identity,
                )
                journal = copying
                stage_writer.write_file(
                    GENERIC_ASSETPACK_MANIFEST,
                    manifest_payload,
                )
                for relative in sorted(
                    files,
                    key=lambda item: item.encode("utf-8"),
                ):
                    stage_writer.write_file(relative, files[relative])
                stage_writer.fsync()
                verified_stage = verify_generic_assetpack(
                    stage,
                    expected_content_hash=document["content_hash"],
                    expected_parent_identity=effective_parent_identity,
                )
                _journal_matches_verified(journal, verified_stage)
                stage_writer.require_binding()
                verified_stage.close()
                verified_stage = None
                ready = {**journal, "state": "ready"}
                journal_identity = _write_journal(
                    journal_path,
                    ready,
                    lock=lock,
                    create=False,
                    expected_document=journal,
                    expected_identity=journal_identity,
                )
                journal = ready
                stage_writer.require_binding()
            lock.require_binding()
            if _publication_hook is not None:
                _publication_hook("before_destination_publish", None)
            lock.require_binding()
            try:
                with publish_directory_noreplace(
                    stage,
                    destination_path,
                    expected_source_identity=stage_identity,
                    expected_parent_identity=effective_parent_identity,
                ) as published_identity:
                    if published_identity != stage_identity:
                        _fail(
                            "assetpack_publication_identity_mismatch",
                            "published directory identity changed",
                        )
                    fsync_directory(
                        destination_path.parent,
                        context="published assetpack parent",
                    )
                    published = verify_generic_assetpack(
                        destination_path,
                        expected_content_hash=document["content_hash"],
                        expected_parent_identity=effective_parent_identity,
                    )
                    _journal_matches_verified(journal, published)
                    lock.require_binding()
                    if (
                        directory_identity(
                            destination_path,
                            context="verified published generic assetpack",
                        )
                        != published_identity
                        or _optional_directory_identity(
                            stage,
                            context="published absent assetpack stage",
                        )
                        is not None
                    ):
                        _fail(
                            "assetpack_publication_identity_mismatch",
                            "published directory changed during verification",
                        )
            except DirectoryPublishIndeterminateError as exc:
                _fail("assetpack_publication_indeterminate", str(exc))
            except FileExistsError as exc:
                _fail("assetpack_destination_exists", str(exc))
            except DirectoryPublishError as exc:
                _fail("assetpack_publication_failed", str(exc))
            assert journal_identity is not None
            if _publication_hook is not None:
                _publication_hook("before_journal_removal", None)
            lock.require_binding()
            _remove_journal(
                journal_path,
                journal,
                journal_identity,
                lock=lock,
                absent_paths=(stage,),
            )
            result = published
            published = None
            assert result is not None
            return result
        except BaseException as original:
            for snapshot in (verified_stage, published):
                if snapshot is not None:
                    snapshot.close()
            if stage_identity is not None and (stage.exists() or stage.is_symlink()):
                original.add_note(
                    f"Private generic assetpack stage retained for explicit recovery: {stage}"
                )
            if isinstance(original, GenericAssetpackError):
                raise
            if isinstance(original, (DirectoryPublishError, OSError)):
                _fail("assetpack_publication_failed", str(original))
            raise
