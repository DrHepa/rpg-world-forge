from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from worldforge.asset_io import (
    AssetContractError,
    write_bytes_atomic,
    write_json_atomic,
)
from worldforge.creation_contracts import (
    CreationContractError,
    _exact_keys,
    _identifier,
    _integer,
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
from worldforge.generic_asset_limits import MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
from worldforge.generic_asset_production import (
    ASSET_LICENSE_FORMAT,
    ASSET_PRODUCTION_RECEIPT_FORMAT,
    ASSET_PRODUCTION_REQUEST_FORMAT,
    ASSET_PROVENANCE_FORMAT,
    ASSET_SELECTION_FORMAT,
    GenericAssetProductionError,
    _inspect_candidate,
    _portable_path_tree,
    _safe_artifact_bytes,
    _validate_metadata,
    validate_asset_license_record,
    validate_asset_production_receipt,
    validate_asset_production_request,
    validate_asset_provenance_record,
    validate_asset_selection,
)
from worldforge.generic_assets import (
    ASSET_INVENTORY_FORMAT,
    ASSET_SPEC_FORMAT,
    ASSET_STYLE_FORMAT,
    ASSET_SUBJECT_FORMAT,
    ASSET_TARGET_FORMAT,
    GenericAssetError,
    _gamepack_identity,
    _identity,
    _validate_spec_output,
    validate_asset_inventory,
    validate_asset_specification,
    validate_asset_style,
    validate_asset_subject,
    validate_asset_target,
)
from worldforge.integrity import canonical_json_bytes

ASSET_PROCESSING_RECIPE_FORMAT = "world-forge.asset_processing_recipe"
ASSET_PROCESSING_RECEIPT_FORMAT = "world-forge.asset_processing_receipt"
ASSET_QA_REPORT_FORMAT = "world-forge.asset_qa_report"
ASSET_MANIFEST_FORMAT = "world-forge.asset_manifest"
GENERIC_ASSET_PROCESSING_VERSION = 1
GENERIC_ASSET_PROCESSOR_ID = "world_forge_generic_asset_processor"

MAX_PROCESSING_OUTPUTS = 4
MAX_PROCESSING_FAILURES = 64
MAX_QA_CHECKS = 10
MAX_MANIFEST_ASSETS = 1024

_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_ASSET_FIELDS = frozenset({"asset_id", "content_hash"})
_PROCESSOR_FIELDS = frozenset({"processor_id", "version"})
_LICENSE_IDENTITY_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "content_hash",
        "candidate_artifact_id",
        "role",
    }
)
_LICENSE_BINDING_FIELDS = frozenset({"candidate_artifact_id", "role", "license_record"})
_STEP_FIELDS = frozenset(
    {
        "step_id",
        "candidate_artifact_id",
        "source_locator",
        "source_sha256",
        "source_size_bytes",
        "role",
        "media_type",
        "runtime_path",
        "operation",
        "output_locator",
        "expectations",
        "license_record",
    }
)
_RECIPE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "recipe_id",
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
        "licenses",
        "processor",
        "steps",
        "content_hash",
    }
)
_PROCESSED_OUTPUT_FIELDS = frozenset(
    {
        "step_id",
        "candidate_artifact_id",
        "source_sha256",
        "role",
        "media_type",
        "runtime_path",
        "locator",
        "sha256",
        "size_bytes",
        "metadata",
    }
)
_PROCESSING_RECEIPT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "processing_receipt_id",
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
        "recipe",
        "processor",
        "status",
        "outputs",
        "failure_reasons",
        "recovery",
        "content_hash",
    }
)
_RECOVERY_FIELDS = frozenset(
    {
        "failure_code",
        "recipe",
        "retained_artifacts",
        "content_hash",
    }
)
_QA_CHECK_FIELDS = frozenset({"check_id", "status"})
_QA_OUTPUT_FIELDS = frozenset(
    {
        "candidate_artifact_id",
        "role",
        "media_type",
        "runtime_path",
        "locator",
        "sha256",
        "size_bytes",
        "metadata",
        "checks",
    }
)
_QA_CRITERION_FIELDS = frozenset(
    {
        "criterion_index",
        "criterion_sha256",
        "status",
        "evidence_hashes",
    }
)
_MULTI_OUTPUT_FIELDS = frozenset({"status", "roles"})
_QA_REPORT_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "qa_report_id",
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
        "recipe",
        "processing_receipt",
        "status",
        "outputs",
        "acceptance_criteria",
        "multi_output_check",
        "blockers",
        "content_hash",
    }
)
_MANIFEST_OUTPUT_FIELDS = frozenset(
    {"role", "media_type", "runtime_path", "locator", "sha256", "size_bytes"}
)
_MANIFEST_ASSET_FIELDS = frozenset(
    {
        "asset",
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
        "licenses",
        "processing_recipe",
        "processing_receipt",
        "qa_report",
        "state",
        "outputs",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "manifest_id",
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "state",
        "assets",
        "content_hash",
    }
)

_OPERATIONS = {
    ("texture", "image/png"): "validate_copy_png",
    ("audio", "audio/wav"): "validate_copy_pcm16_wav",
    ("font", "font/ttf"): "validate_copy_font",
    ("font", "font/otf"): "validate_copy_font",
    ("vertex_shader", "text/x-glsl"): "validate_copy_vertex_glsl",
    ("fragment_shader", "text/x-glsl"): "validate_copy_fragment_glsl",
    ("clipset", "application/json"): "canonicalize_clipset_json",
    ("localized_text", "application/json"): "canonicalize_localization_json",
    ("model", "model/gltf-binary"): "validate_copy_glb",
    ("collision", "model/gltf-binary"): "validate_copy_glb",
    ("skeleton", "model/gltf-binary"): "validate_copy_glb",
    ("animation", "model/gltf-binary"): "validate_copy_glb",
}
_MEDIA_CHECKS = (
    ("png", "image/png"),
    ("wav", "audio/wav"),
    ("font", "font/ttf"),
    ("font", "font/otf"),
    ("glsl", "text/x-glsl"),
    ("json", "application/json"),
    ("glb", "model/gltf-binary"),
)
_QA_CHECK_ORDER = (
    "hash",
    "media",
    "path",
    "license",
    "png",
    "wav",
    "font",
    "glsl",
    "json",
    "glb",
)
_COMMON_IDENTITY_FORMATS = {
    "gamepack": "world-forge.gamepack",
    "asset_subject": ASSET_SUBJECT_FORMAT,
    "target": ASSET_TARGET_FORMAT,
    "style": ASSET_STYLE_FORMAT,
    "inventory": ASSET_INVENTORY_FORMAT,
    "specification": ASSET_SPEC_FORMAT,
    "request": ASSET_PRODUCTION_REQUEST_FORMAT,
    "receipt": ASSET_PRODUCTION_RECEIPT_FORMAT,
    "selection": ASSET_SELECTION_FORMAT,
    "provenance": ASSET_PROVENANCE_FORMAT,
}


class GenericAssetProcessingError(ValueError):
    """Raised when generic processing, QA, or manifest evidence fails closed."""

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        recovery_receipt: dict[str, Any] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.detail = detail
        self.recovery_receipt = recovery_receipt
        super().__init__(f"{reason_code}: {detail}")


class _ProcessingPublicationFailure(RuntimeError):
    def __init__(
        self,
        detail: str,
        retained_artifacts: Sequence[Mapping[str, object]],
    ) -> None:
        self.detail = detail
        self.retained_artifacts = copy.deepcopy(list(retained_artifacts))
        super().__init__(detail)


def _fail(reason_code: str, detail: str) -> None:
    raise GenericAssetProcessingError(reason_code, detail)


def _hash(document: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(document)
    except CreationContractError as exc:
        _fail("processing_contract_invalid", str(exc))


def _seal(document: dict[str, Any]) -> dict[str, Any]:
    document["content_hash"] = _hash(document)
    return document


def _ensure_structure(value: object, context: str) -> None:
    try:
        _validate_json_structure(value, context=context)
    except CreationContractError as exc:
        _fail("processing_contract_invalid", str(exc))


def _validate_hash(document: Mapping[str, object], context: str) -> None:
    try:
        _sha256(document.get("content_hash"), f"{context}.content_hash")
    except CreationContractError as exc:
        _fail("processing_contract_invalid", str(exc))
    if document["content_hash"] != _hash(document):
        _fail("content_hash_mismatch", f"{context}.content_hash is not canonical")


def _identity_value(
    value: object,
    context: str,
    *,
    expected_format: str,
) -> dict[str, Any]:
    try:
        identity = _object(value, context)
        _exact_keys(identity, _IDENTITY_FIELDS, context)
        if identity.get("format") != expected_format:
            _fail(
                "processing_lineage_mismatch",
                f"{context}.format must be {expected_format}",
            )
        if identity.get("format_version") != 1:
            _fail(
                "processing_lineage_mismatch",
                f"{context}.format_version must be 1",
            )
        _identifier(identity.get("id"), f"{context}.id")
        _sha256(identity.get("content_hash"), f"{context}.content_hash")
        return identity
    except CreationContractError as exc:
        _fail("processing_contract_invalid", str(exc))


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


def _license_binding_identity(
    license_record: Mapping[str, object],
) -> dict[str, object]:
    candidate = license_record["candidate"]
    assert isinstance(candidate, Mapping)
    return {
        **_document_identity(license_record, "license_record_id"),
        "candidate_artifact_id": candidate["candidate_artifact_id"],
        "role": candidate["role"],
    }


def _validate_license_binding_identity(
    value: object,
    context: str,
    *,
    candidate_artifact_id: str,
    role: str,
) -> dict[str, Any]:
    try:
        identity = _object(value, context)
        _exact_keys(identity, _LICENSE_IDENTITY_FIELDS, context)
        if identity.get("format") != ASSET_LICENSE_FORMAT:
            _fail(
                "processing_license_coverage",
                f"{context}.format must be {ASSET_LICENSE_FORMAT}",
            )
        if identity.get("format_version") != 1:
            _fail(
                "processing_license_coverage",
                f"{context}.format_version must be 1",
            )
        _identifier(identity.get("id"), f"{context}.id")
        _sha256(identity.get("content_hash"), f"{context}.content_hash")
        if (
            identity.get("candidate_artifact_id") != candidate_artifact_id
            or identity.get("role") != role
        ):
            _fail(
                "processing_license_coverage",
                f"{context} is not bound to {candidate_artifact_id}/{role}",
            )
        return identity
    except GenericAssetProcessingError:
        raise
    except CreationContractError as exc:
        _fail("processing_license_coverage", str(exc))


def _common_identities(
    gamepack: Mapping[str, object],
    subject: Mapping[str, object],
    target: Mapping[str, object],
    style: Mapping[str, object],
    inventory: Mapping[str, object],
    specification: Mapping[str, object],
    request: Mapping[str, object],
    receipt: Mapping[str, object],
    selection: Mapping[str, object],
    provenance: Mapping[str, object],
) -> dict[str, object]:
    return {
        "gamepack": _gamepack_identity(gamepack),
        "asset_subject": _identity(subject, id_field="subject_id"),
        "target": _identity(target, id_field="target_id"),
        "style": _identity(style, id_field="style_id"),
        "inventory": _identity(inventory, id_field="inventory_id"),
        "specification": _identity(specification, id_field="spec_id"),
        "asset": copy.deepcopy(specification["asset"]),
        "request": _document_identity(request, "request_id"),
        "receipt": _document_identity(receipt, "receipt_id"),
        "selection": _document_identity(selection, "selection_id"),
        "provenance": _document_identity(provenance, "provenance_id"),
    }


def _checked_d2a_chain(
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    request: object,
    receipt: object,
    selection: object,
    provenance: object,
    license_records: Sequence[object],
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
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
        checked_request = validate_asset_production_request(
            request,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
            style=checked_style,
            inventory=checked_inventory,
            specification=checked_specification,
        )
        checked_receipt = validate_asset_production_receipt(
            receipt,
            request=checked_request,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
            style=checked_style,
            inventory=checked_inventory,
            specification=checked_specification,
            artifact_root=artifact_root,
            parent_receipts=parent_receipts,
        )
        if checked_receipt["status"] != "completed":
            _fail(
                "processing_production_incomplete",
                "processing requires a completed production receipt",
            )
        checked_selection = validate_asset_selection(
            selection,
            receipt=checked_receipt,
            request=checked_request,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
            style=checked_style,
            inventory=checked_inventory,
            specification=checked_specification,
            artifact_root=artifact_root,
            parent_receipts=parent_receipts,
            receipt_parent_closures=receipt_parent_closures,
            rejected_receipts=rejected_receipts,
        )
        checked_provenance = validate_asset_provenance_record(
            provenance,
            selection=checked_selection,
            receipt=checked_receipt,
            request=checked_request,
            gamepack=checked_gamepack,
            subject=checked_subject,
            target=checked_target,
            style=checked_style,
            inventory=checked_inventory,
            specification=checked_specification,
            artifact_root=artifact_root,
            parent_receipts=parent_receipts,
            receipt_parent_closures=receipt_parent_closures,
            rejected_receipts=rejected_receipts,
        )
        if (
            not isinstance(license_records, Sequence)
            or isinstance(license_records, (str, bytes, bytearray))
            or len(license_records) > MAX_PROCESSING_OUTPUTS
        ):
            _fail(
                "processing_license_coverage",
                "license_records must be a bounded sequence",
            )
        checked_licenses = [
            validate_asset_license_record(
                license_record,
                provenance=checked_provenance,
                selection=checked_selection,
                receipt=checked_receipt,
                request=checked_request,
                gamepack=checked_gamepack,
                subject=checked_subject,
                target=checked_target,
                style=checked_style,
                inventory=checked_inventory,
                specification=checked_specification,
                artifact_root=artifact_root,
                parent_receipts=parent_receipts,
                receipt_parent_closures=receipt_parent_closures,
                rejected_receipts=rejected_receipts,
            )
            for license_record in license_records
        ]
    except GenericAssetProcessingError:
        raise
    except (
        CreationContractError,
        GenericAssetError,
        GenericAssetProductionError,
        GamepackError,
        TypeError,
        ValueError,
    ) as exc:
        _fail("processing_lineage_invalid", str(exc))

    selected = {
        output["candidate_artifact_id"]: output for output in checked_selection["selected_outputs"]
    }
    licenses_by_candidate: dict[str, dict[str, Any]] = {}
    for license_record in checked_licenses:
        candidate_id = license_record["candidate"]["candidate_artifact_id"]
        if candidate_id in licenses_by_candidate:
            _fail(
                "processing_license_coverage",
                f"candidate {candidate_id} has more than one license record",
            )
        licenses_by_candidate[candidate_id] = license_record
    if set(licenses_by_candidate) != set(selected):
        _fail(
            "processing_license_coverage",
            "license records must exactly cover selected candidates",
        )
    ordered_licenses = [
        licenses_by_candidate[output["candidate_artifact_id"]]
        for output in sorted(
            checked_selection["selected_outputs"],
            key=lambda item: item["role"].encode("utf-8"),
        )
    ]
    return (
        checked_gamepack,
        checked_subject,
        checked_target,
        checked_style,
        checked_inventory,
        checked_specification,
        checked_request,
        checked_receipt,
        checked_selection,
        checked_provenance,
        ordered_licenses,
    )


def _operation(role: str, media_type: str) -> str:
    operation = _OPERATIONS.get((role, media_type))
    if operation is None:
        _fail(
            "processing_operation_unsupported",
            f"no closed processing operation for {role}/{media_type}",
        )
    return operation


def _bounded_asset_size(value: object, context: str) -> int:
    try:
        size = _integer(value, context, minimum=1)
    except CreationContractError as exc:
        _fail("processing_contract_invalid", str(exc))
    if size > 16 * 1024 * 1024:
        _fail("processing_contract_limit", f"{context} exceeds 16777216")
    return size


def _output_locator(asset_id: str, role: str, runtime_path: str) -> str:
    name = PurePosixPath(runtime_path).name
    locator = f"assets/production/{asset_id}/processed/{role}/{name}"
    try:
        return _portable_relative_path(locator, "processing output locator")
    except CreationContractError as exc:
        _fail("processing_path_invalid", str(exc))


def _step_id(candidate_id: str, role: str) -> str:
    digest = hashlib.sha256(f"{candidate_id}\0{role}".encode()).hexdigest()
    return f"step_{digest[:48]}"


def _recipe_document(checked: tuple[dict[str, Any], ...], recipe_id: str) -> dict[str, Any]:
    (
        gamepack,
        subject,
        target,
        style,
        inventory,
        specification,
        request,
        receipt,
        selection,
        provenance,
        licenses,
    ) = checked
    try:
        _identifier(recipe_id, "asset processing recipe.recipe_id")
    except CreationContractError as exc:
        _fail("processing_recipe_invalid", str(exc))
    receipt_outputs = {output["candidate_artifact_id"]: output for output in receipt["outputs"]}
    specification_outputs = {output["role"]: output for output in specification["outputs"]}
    license_by_candidate = {item["candidate"]["candidate_artifact_id"]: item for item in licenses}
    selected = sorted(
        selection["selected_outputs"],
        key=lambda item: item["role"].encode("utf-8"),
    )
    license_bindings = []
    steps = []
    for selected_output in selected:
        candidate_id = selected_output["candidate_artifact_id"]
        role = selected_output["role"]
        media_type = selected_output["media_type"]
        produced = receipt_outputs[candidate_id]
        expected = specification_outputs[role]
        license_record = license_by_candidate[candidate_id]
        operation = _operation(role, media_type)
        if (
            operation.startswith("canonicalize_")
            and not license_record["permissions"]["modification"]
        ):
            _fail(
                "processing_license_permission",
                f"{candidate_id} does not permit deterministic modification",
            )
        license_identity = _license_binding_identity(license_record)
        license_bindings.append(
            {
                "candidate_artifact_id": candidate_id,
                "role": role,
                "license_record": license_identity,
            }
        )
        steps.append(
            {
                "step_id": _step_id(candidate_id, role),
                "candidate_artifact_id": candidate_id,
                "source_locator": produced["locator"],
                "source_sha256": produced["sha256"],
                "source_size_bytes": produced["size_bytes"],
                "role": role,
                "media_type": media_type,
                "runtime_path": expected["runtime_path"],
                "operation": operation,
                "output_locator": _output_locator(
                    specification["asset"]["asset_id"],
                    role,
                    expected["runtime_path"],
                ),
                "expectations": copy.deepcopy(expected["expectations"]),
                "license_record": license_identity,
            }
        )
    _portable_path_tree(
        [
            *[step["source_locator"] for step in steps],
            *[step["output_locator"] for step in steps],
            *[step["runtime_path"] for step in steps],
        ],
        "asset processing recipe paths",
    )
    document = {
        "format": ASSET_PROCESSING_RECIPE_FORMAT,
        "format_version": GENERIC_ASSET_PROCESSING_VERSION,
        "recipe_id": recipe_id,
        **_common_identities(
            gamepack,
            subject,
            target,
            style,
            inventory,
            specification,
            request,
            receipt,
            selection,
            provenance,
        ),
        "licenses": license_bindings,
        "processor": {
            "processor_id": GENERIC_ASSET_PROCESSOR_ID,
            "version": GENERIC_ASSET_PROCESSING_VERSION,
        },
        "steps": steps,
    }
    return _seal(document)


def build_asset_processing_recipe(
    *,
    recipe_id: str,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    specification: object,
    request: object,
    receipt: object,
    selection: object,
    provenance: object,
    license_records: Sequence[object],
    artifact_root: str | Path,
    parent_receipts: Sequence[Mapping[str, object]] = (),
    receipt_parent_closures: Mapping[str, Sequence[Mapping[str, object]]] | None = None,
    rejected_receipts: Sequence[Mapping[str, object]] = (),
) -> dict[str, Any]:
    checked = _checked_d2a_chain(
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        specification=specification,
        request=request,
        receipt=receipt,
        selection=selection,
        provenance=provenance,
        license_records=license_records,
        artifact_root=artifact_root,
        parent_receipts=parent_receipts,
        receipt_parent_closures=receipt_parent_closures,
        rejected_receipts=rejected_receipts,
    )
    return validate_asset_processing_recipe_document(_recipe_document(checked, recipe_id))


def _validate_recipe_structure(value: object) -> dict[str, Any]:
    _ensure_structure(value, "asset processing recipe")
    try:
        document = _object(value, "asset processing recipe")
        _exact_keys(document, _RECIPE_FIELDS, "asset processing recipe")
        if document.get("format") != ASSET_PROCESSING_RECIPE_FORMAT:
            _fail(
                "processing_recipe_format_invalid",
                f"format must be {ASSET_PROCESSING_RECIPE_FORMAT}",
            )
        if document.get("format_version") != 1:
            _fail("processing_recipe_version_invalid", "format_version must be 1")
        _identifier(document.get("recipe_id"), "asset processing recipe.recipe_id")
        for field, expected_format in _COMMON_IDENTITY_FORMATS.items():
            _identity_value(
                document.get(field),
                f"asset processing recipe.{field}",
                expected_format=expected_format,
            )
        asset = _object(document.get("asset"), "asset processing recipe.asset")
        _exact_keys(asset, _ASSET_FIELDS, "asset processing recipe.asset")
        _identifier(asset.get("asset_id"), "asset processing recipe.asset.asset_id")
        _sha256(
            asset.get("content_hash"),
            "asset processing recipe.asset.content_hash",
        )
        processor = _object(
            document.get("processor"),
            "asset processing recipe.processor",
        )
        _exact_keys(
            processor,
            _PROCESSOR_FIELDS,
            "asset processing recipe.processor",
        )
        if processor != {
            "processor_id": GENERIC_ASSET_PROCESSOR_ID,
            "version": 1,
        }:
            _fail(
                "processing_processor_invalid",
                "recipe processor identity is not the fixed v1 processor",
            )
        raw_steps = document.get("steps")
        raw_licenses = document.get("licenses")
        if (
            not isinstance(raw_steps, list)
            or not 1 <= len(raw_steps) <= MAX_PROCESSING_OUTPUTS
            or not isinstance(raw_licenses, list)
            or len(raw_licenses) != len(raw_steps)
        ):
            _fail(
                "processing_recipe_invalid",
                "steps and licenses must be equally bounded non-empty arrays",
            )
        roles = []
        candidate_ids = []
        paths = []
        license_candidates = []
        for index, raw_license in enumerate(raw_licenses):
            context = f"asset processing recipe.licenses/{index}"
            binding = _object(raw_license, context)
            _exact_keys(binding, _LICENSE_BINDING_FIELDS, context)
            candidate_id = _identifier(
                binding.get("candidate_artifact_id"),
                f"{context}.candidate_artifact_id",
            )
            role = _identifier(binding.get("role"), f"{context}.role")
            _validate_license_binding_identity(
                binding.get("license_record"),
                f"{context}.license_record",
                candidate_artifact_id=candidate_id,
                role=role,
            )
            license_candidates.append((role, candidate_id, binding["license_record"]))
        for index, raw_step in enumerate(raw_steps):
            context = f"asset processing recipe.steps/{index}"
            step = _object(raw_step, context)
            _exact_keys(step, _STEP_FIELDS, context)
            _identifier(step.get("step_id"), f"{context}.step_id")
            candidate_id = _identifier(
                step.get("candidate_artifact_id"),
                f"{context}.candidate_artifact_id",
            )
            role = _identifier(step.get("role"), f"{context}.role")
            media_type = _non_empty_string(
                step.get("media_type"),
                f"{context}.media_type",
            )
            source_locator = _portable_relative_path(
                step.get("source_locator"),
                f"{context}.source_locator",
            )
            runtime_path = _portable_relative_path(
                step.get("runtime_path"),
                f"{context}.runtime_path",
            )
            output_locator = _portable_relative_path(
                step.get("output_locator"),
                f"{context}.output_locator",
            )
            _sha256(step.get("source_sha256"), f"{context}.source_sha256")
            _bounded_asset_size(
                step.get("source_size_bytes"),
                f"{context}.source_size_bytes",
            )
            if step.get("operation") != _operation(role, media_type):
                _fail(
                    "processing_operation_mismatch",
                    f"{context}.operation does not match role/media",
                )
            _validate_spec_output(
                {
                    "role": role,
                    "media_type": media_type,
                    "runtime_path": runtime_path,
                    "expectations": step.get("expectations"),
                },
                context,
            )
            _validate_license_binding_identity(
                step.get("license_record"),
                f"{context}.license_record",
                candidate_artifact_id=candidate_id,
                role=role,
            )
            roles.append(role)
            candidate_ids.append(candidate_id)
            paths.extend((source_locator, runtime_path, output_locator))
        canonical_roles = sorted(roles, key=lambda item: item.encode("utf-8"))
        if roles != canonical_roles or len(set(role.casefold() for role in roles)) != len(roles):
            _fail(
                "processing_recipe_noncanonical",
                "recipe steps must have unique UTF-8-sorted roles",
            )
        if len(set(candidate.casefold() for candidate in candidate_ids)) != len(candidate_ids):
            _fail(
                "processing_recipe_noncanonical",
                "recipe candidate IDs must be unique",
            )
        expected_licenses = [
            (
                step["role"],
                step["candidate_artifact_id"],
                step["license_record"],
            )
            for step in raw_steps
        ]
        if license_candidates != expected_licenses:
            _fail(
                "processing_license_coverage",
                "recipe licenses must exactly match its canonical steps",
            )
        _portable_path_tree(paths, "asset processing recipe paths")
        _validate_hash(document, "asset processing recipe")
        return copy.deepcopy(document)
    except GenericAssetProcessingError:
        raise
    except (CreationContractError, GenericAssetError, TypeError, ValueError) as exc:
        _fail("processing_recipe_invalid", str(exc))


def validate_asset_processing_recipe_document(value: object) -> dict[str, Any]:
    return _validate_recipe_structure(value)


def validate_asset_processing_recipe(
    value: object,
    **lineage: object,
) -> dict[str, Any]:
    document = validate_asset_processing_recipe_document(value)
    checked = _checked_d2a_chain(**lineage)
    expected = _recipe_document(checked, document["recipe_id"])
    if document != expected:
        _fail(
            "processing_lineage_mismatch",
            "recipe is not the exact rebuild of the selected D2a lineage",
        )
    return document


def _canonicalize_schema_json(payload: bytes) -> bytes:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        _fail("processing_media_invalid", f"schema JSON is invalid: {exc}")
    if not isinstance(value, dict):
        _fail("processing_media_invalid", "schema JSON root must be an object")
    return canonical_json_bytes(value)


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_number(value: str) -> None:
    raise ValueError(f"unsupported non-integer number {value}")


def _processed_payload(
    step: Mapping[str, object],
    payload: bytes,
) -> tuple[bytes, dict[str, object]]:
    try:
        source_metadata = _inspect_candidate(
            payload,
            role=str(step["role"]),
            media_type=str(step["media_type"]),
            expectations=step["expectations"],
        )
        output = (
            _canonicalize_schema_json(payload)
            if str(step["operation"]).startswith("canonicalize_")
            else payload
        )
        output_expectations = copy.deepcopy(step["expectations"])
        if str(step["operation"]).startswith("canonicalize_"):
            output_expectations["max_bytes"] = max(
                int(output_expectations["max_bytes"]),
                len(output),
            )
        metadata = _inspect_candidate(
            output,
            role=str(step["role"]),
            media_type=str(step["media_type"]),
            expectations=output_expectations,
        )
    except GenericAssetProductionError as exc:
        _fail("processing_media_invalid", str(exc))
    if source_metadata != metadata:
        _fail(
            "processing_media_mismatch",
            "deterministic processing changed declared media metadata",
        )
    return output, metadata


def _expected_processed_outputs(
    recipe: Mapping[str, object],
    artifact_root: str | Path,
    *,
    publish: bool,
) -> list[dict[str, Any]]:
    prepared: list[tuple[Mapping[str, object], bytes, dict[str, object]]] = []
    for step in recipe["steps"]:
        assert isinstance(step, Mapping)
        try:
            payload = _safe_artifact_bytes(
                artifact_root,
                step["source_locator"],
                limit=int(step["source_size_bytes"]),
            )
        except GenericAssetProductionError as exc:
            _fail("processing_source_mismatch", str(exc))
        if (
            len(payload) != step["source_size_bytes"]
            or hashlib.sha256(payload).hexdigest() != step["source_sha256"]
        ):
            _fail(
                "processing_source_mismatch",
                f"source bytes changed for {step['candidate_artifact_id']}",
            )
        output, metadata = _processed_payload(step, payload)
        prepared.append((step, output, metadata))
    for step, expected_payload, _metadata in prepared:
        if publish:
            destination = Path(artifact_root).joinpath(
                *PurePosixPath(str(step["output_locator"])).parts
            )
            try:
                write_bytes_atomic(destination, expected_payload, durable_parent=True)
            except (AssetContractError, OSError) as exc:
                collision = isinstance(exc, AssetContractError) and str(exc).startswith(
                    "Refusing to overwrite "
                )
                try:
                    existing = _safe_artifact_bytes(
                        artifact_root,
                        step["output_locator"],
                        limit=len(expected_payload),
                    )
                except GenericAssetProductionError:
                    existing = None
                if collision and existing == expected_payload:
                    continue
                retained_outputs = _exact_processed_output_prefix(prepared, artifact_root)
                retained = ", ".join(str(item["locator"]) for item in retained_outputs) or "none"
                raise _ProcessingPublicationFailure(
                    "processed output publication failed; exact outputs currently "
                    f"retained ({retained}): {exc}",
                    retained_outputs,
                ) from exc

    outputs = _exact_processed_output_prefix(prepared, artifact_root)
    if len(outputs) != len(prepared):
        retained = ", ".join(str(item["locator"]) for item in outputs) or "none"
        if publish:
            raise _ProcessingPublicationFailure(
                "processed output final exact-set census failed; exact outputs currently "
                f"retained ({retained})",
                outputs,
            )
        _fail(
            "processed_output_mismatch",
            "processed output exact set changed",
        )
    return outputs


def _exact_processed_output_prefix(
    prepared: Sequence[tuple[Mapping[str, object], bytes, Mapping[str, object]]],
    artifact_root: str | Path,
) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for step, expected_payload, metadata in prepared:
        try:
            retained = _safe_artifact_bytes(
                artifact_root,
                step["output_locator"],
                limit=len(expected_payload),
            )
        except GenericAssetProductionError:
            break
        if retained != expected_payload:
            break
        outputs.append(_processed_output_record(step, retained, metadata))
    return outputs


def _processed_output_record(
    step: Mapping[str, object],
    payload: bytes,
    metadata: Mapping[str, object],
) -> dict[str, Any]:
    return {
        "step_id": step["step_id"],
        "candidate_artifact_id": step["candidate_artifact_id"],
        "source_sha256": step["source_sha256"],
        "role": step["role"],
        "media_type": step["media_type"],
        "runtime_path": step["runtime_path"],
        "locator": step["output_locator"],
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "metadata": copy.deepcopy(metadata),
    }


def _recovery_document(
    recipe: Mapping[str, object],
    *,
    failure_code: str,
    retained_artifacts: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    return _seal(
        {
            "failure_code": failure_code,
            "recipe": _document_identity(recipe, "recipe_id"),
            "retained_artifacts": copy.deepcopy(list(retained_artifacts)),
        }
    )


def _processing_receipt_document(
    recipe: Mapping[str, object],
    *,
    processing_receipt_id: str,
    status: str,
    outputs: Sequence[Mapping[str, object]],
    failure_reasons: Sequence[str],
    recovery: Mapping[str, object] | None,
) -> dict[str, Any]:
    try:
        _identifier(
            processing_receipt_id,
            "asset processing receipt.processing_receipt_id",
        )
    except CreationContractError as exc:
        _fail("processing_receipt_invalid", str(exc))
    document = {
        "format": ASSET_PROCESSING_RECEIPT_FORMAT,
        "format_version": 1,
        "processing_receipt_id": processing_receipt_id,
        **{
            key: copy.deepcopy(recipe[key])
            for key in (
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
            )
        },
        "recipe": _document_identity(recipe, "recipe_id"),
        "processor": copy.deepcopy(recipe["processor"]),
        "status": status,
        "outputs": copy.deepcopy(list(outputs)),
        "failure_reasons": list(failure_reasons),
        "recovery": None if recovery is None else copy.deepcopy(recovery),
    }
    return _seal(document)


def build_asset_processing_receipt(
    recipe: object,
    *,
    processing_receipt_id: str,
    status: str = "completed",
    failure_reasons: Sequence[str] = (),
    **lineage: object,
) -> dict[str, Any]:
    checked_recipe = validate_asset_processing_recipe(recipe, **lineage)
    if status == "failed":
        checked_reasons = _canonical_reason_codes(
            failure_reasons,
            "asset processing receipt.failure_reasons",
            allow_empty=False,
        )
        if len(checked_reasons) != 1:
            _fail(
                "processing_status_contradiction",
                "failed processing requires one canonical failure code",
            )
        outputs: list[dict[str, Any]] = []
        recovery: dict[str, Any] | None = _recovery_document(
            checked_recipe,
            failure_code=checked_reasons[0],
            retained_artifacts=(),
        )
    elif status == "completed":
        if failure_reasons:
            _fail(
                "processing_status_contradiction",
                "completed processing cannot carry failure reasons",
            )
        checked_reasons = []
        recovery = None
        try:
            outputs = _expected_processed_outputs(
                checked_recipe,
                lineage["artifact_root"],
                publish=True,
            )
        except _ProcessingPublicationFailure as exc:
            # Once the exclusive writer is invoked, an exception cannot prove
            # absence: the retained parent may have been renamed or replaced
            # before any pathname-based inspection.  Fail closed even when no
            # currently reachable retained artifact can be enumerated.
            failure_code = "processing_partial_publication"
            recovery = _recovery_document(
                checked_recipe,
                failure_code=failure_code,
                retained_artifacts=exc.retained_artifacts,
            )
            failed_receipt = validate_asset_processing_receipt_document(
                _processing_receipt_document(
                    checked_recipe,
                    processing_receipt_id=processing_receipt_id,
                    status="failed",
                    outputs=(),
                    failure_reasons=(failure_code,),
                    recovery=recovery,
                )
            )
            raise GenericAssetProcessingError(
                failure_code,
                exc.detail,
                recovery_receipt=failed_receipt,
            ) from exc
    else:
        _fail(
            "processing_status_invalid",
            "processing receipt status must be completed or failed",
        )
    return validate_asset_processing_receipt_document(
        _processing_receipt_document(
            checked_recipe,
            processing_receipt_id=processing_receipt_id,
            status=status,
            outputs=outputs,
            failure_reasons=checked_reasons,
            recovery=recovery,
        )
    )


def _canonical_reason_codes(
    value: object,
    context: str,
    *,
    allow_empty: bool,
) -> list[str]:
    try:
        items = _string_array(value, context, allow_empty=allow_empty)
        if len(items) > MAX_PROCESSING_FAILURES:
            _fail("processing_contract_limit", f"{context} exceeds its bound")
        for index, item in enumerate(items):
            _identifier(item, f"{context}/{index}")
        canonical = sorted(items, key=lambda item: item.encode("utf-8"))
        if items != canonical or len({item.casefold() for item in items}) != len(items):
            _fail(
                "processing_contract_noncanonical",
                f"{context} must be unique and UTF-8 sorted",
            )
        return items
    except CreationContractError as exc:
        _fail("processing_contract_invalid", str(exc))


def _validate_processed_output(value: object, context: str) -> dict[str, Any]:
    try:
        output = _object(value, context)
        _exact_keys(output, _PROCESSED_OUTPUT_FIELDS, context)
        _identifier(output.get("step_id"), f"{context}.step_id")
        _identifier(
            output.get("candidate_artifact_id"),
            f"{context}.candidate_artifact_id",
        )
        _sha256(output.get("source_sha256"), f"{context}.source_sha256")
        role = _identifier(output.get("role"), f"{context}.role")
        media_type = _non_empty_string(
            output.get("media_type"),
            f"{context}.media_type",
        )
        _portable_relative_path(
            output.get("runtime_path"),
            f"{context}.runtime_path",
        )
        _portable_relative_path(output.get("locator"), f"{context}.locator")
        _sha256(output.get("sha256"), f"{context}.sha256")
        _bounded_asset_size(
            output.get("size_bytes"),
            f"{context}.size_bytes",
        )
        _operation(role, media_type)
        _validate_metadata(output.get("metadata"), media_type, f"{context}.metadata")
        return output
    except GenericAssetProcessingError:
        raise
    except (CreationContractError, GenericAssetProductionError, TypeError, ValueError) as exc:
        _fail("processing_receipt_invalid", str(exc))


def _validate_recovery_structure(
    value: object,
    *,
    recipe_identity: Mapping[str, object],
    failure_reasons: Sequence[str],
) -> dict[str, Any]:
    context = "asset processing receipt.recovery"
    try:
        recovery = _object(value, context)
        _exact_keys(recovery, _RECOVERY_FIELDS, context)
        failure_code = _identifier(
            recovery.get("failure_code"),
            f"{context}.failure_code",
        )
        if list(failure_reasons) != [failure_code]:
            _fail(
                "processing_recovery_invalid",
                "recovery failure code must exactly match the receipt failure reason",
            )
        checked_recipe = _identity_value(
            recovery.get("recipe"),
            f"{context}.recipe",
            expected_format=ASSET_PROCESSING_RECIPE_FORMAT,
        )
        if checked_recipe != recipe_identity:
            _fail(
                "processing_recovery_invalid",
                "recovery recipe identity must exactly match the receipt recipe",
            )
        retained = recovery.get("retained_artifacts")
        if not isinstance(retained, list) or len(retained) > MAX_PROCESSING_OUTPUTS:
            _fail(
                "processing_recovery_invalid",
                "retained_artifacts must be a bounded array",
            )
        checked = [
            _validate_processed_output(
                artifact,
                f"{context}.retained_artifacts/{index}",
            )
            for index, artifact in enumerate(retained)
        ]
        roles = [artifact["role"] for artifact in checked]
        if roles != sorted(roles, key=lambda item: item.encode("utf-8")) or len(
            set(role.casefold() for role in roles)
        ) != len(roles):
            _fail(
                "processing_recovery_invalid",
                "retained artifacts must use unique UTF-8-sorted roles",
            )
        _portable_path_tree(
            [artifact["locator"] for artifact in checked],
            "asset processing receipt recovery paths",
        )
        _validate_hash(recovery, context)
        return recovery
    except GenericAssetProcessingError:
        raise
    except (CreationContractError, GenericAssetProductionError, TypeError, ValueError) as exc:
        _fail("processing_recovery_invalid", str(exc))


def _validate_processing_receipt_structure(value: object) -> dict[str, Any]:
    _ensure_structure(value, "asset processing receipt")
    try:
        document = _object(value, "asset processing receipt")
        _exact_keys(
            document,
            _PROCESSING_RECEIPT_FIELDS,
            "asset processing receipt",
        )
        if document.get("format") != ASSET_PROCESSING_RECEIPT_FORMAT:
            _fail(
                "processing_receipt_format_invalid",
                f"format must be {ASSET_PROCESSING_RECEIPT_FORMAT}",
            )
        if document.get("format_version") != 1:
            _fail("processing_receipt_version_invalid", "format_version must be 1")
        _identifier(
            document.get("processing_receipt_id"),
            "asset processing receipt.processing_receipt_id",
        )
        for field, expected_format in _COMMON_IDENTITY_FORMATS.items():
            _identity_value(
                document.get(field),
                f"asset processing receipt.{field}",
                expected_format=expected_format,
            )
        asset = _object(document.get("asset"), "asset processing receipt.asset")
        _exact_keys(asset, _ASSET_FIELDS, "asset processing receipt.asset")
        _identifier(asset.get("asset_id"), "asset processing receipt.asset.asset_id")
        _sha256(
            asset.get("content_hash"),
            "asset processing receipt.asset.content_hash",
        )
        _identity_value(
            document.get("recipe"),
            "asset processing receipt.recipe",
            expected_format=ASSET_PROCESSING_RECIPE_FORMAT,
        )
        processor = _object(
            document.get("processor"),
            "asset processing receipt.processor",
        )
        _exact_keys(
            processor,
            _PROCESSOR_FIELDS,
            "asset processing receipt.processor",
        )
        if processor != {
            "processor_id": GENERIC_ASSET_PROCESSOR_ID,
            "version": 1,
        }:
            _fail(
                "processing_processor_invalid",
                "receipt processor identity is not fixed v1",
            )
        outputs = document.get("outputs")
        if not isinstance(outputs, list) or len(outputs) > MAX_PROCESSING_OUTPUTS:
            _fail(
                "processing_receipt_invalid",
                "outputs must be a bounded array",
            )
        checked_outputs = [
            _validate_processed_output(
                output,
                f"asset processing receipt.outputs/{index}",
            )
            for index, output in enumerate(outputs)
        ]
        roles = [output["role"] for output in checked_outputs]
        if roles != sorted(roles, key=lambda item: item.encode("utf-8")) or len(
            set(role.casefold() for role in roles)
        ) != len(roles):
            _fail(
                "processing_receipt_noncanonical",
                "outputs must use unique UTF-8-sorted roles",
            )
        _portable_path_tree(
            [output["locator"] for output in checked_outputs],
            "asset processing receipt output paths",
        )
        status = document.get("status")
        reasons = _canonical_reason_codes(
            document.get("failure_reasons"),
            "asset processing receipt.failure_reasons",
            allow_empty=status == "completed",
        )
        if status == "completed":
            if not checked_outputs or reasons or document.get("recovery") is not None:
                _fail(
                    "processing_status_contradiction",
                    "completed receipt requires outputs, no failures, and null recovery",
                )
        elif status == "failed":
            if checked_outputs or len(reasons) != 1:
                _fail(
                    "processing_status_contradiction",
                    "failed receipt requires no outputs and one failure",
                )
            _validate_recovery_structure(
                document.get("recovery"),
                recipe_identity=document["recipe"],
                failure_reasons=reasons,
            )
        else:
            _fail(
                "processing_status_invalid",
                "processing receipt status must be completed or failed",
            )
        _validate_hash(document, "asset processing receipt")
        return copy.deepcopy(document)
    except GenericAssetProcessingError:
        raise
    except (CreationContractError, GenericAssetProductionError, TypeError, ValueError) as exc:
        _fail("processing_receipt_invalid", str(exc))


def validate_asset_processing_receipt_document(value: object) -> dict[str, Any]:
    return _validate_processing_receipt_structure(value)


def _expected_recovery(
    recovery: Mapping[str, object],
    recipe: Mapping[str, object],
    artifact_root: str | Path,
) -> dict[str, Any]:
    retained = recovery["retained_artifacts"]
    assert isinstance(retained, list)
    steps = recipe["steps"]
    assert isinstance(steps, list)
    if [artifact["step_id"] for artifact in retained] != [
        step["step_id"] for step in steps[: len(retained)]
    ]:
        _fail(
            "processing_recovery_mismatch",
            "retained artifacts must be the exact canonical prefix of recipe steps",
        )
    expected_artifacts: list[dict[str, Any]] = []
    for index, artifact in enumerate(retained):
        step = steps[index]
        assert isinstance(step, Mapping)
        try:
            source = _safe_artifact_bytes(
                artifact_root,
                step["source_locator"],
                limit=int(step["source_size_bytes"]),
            )
            payload = _safe_artifact_bytes(
                artifact_root,
                artifact["locator"],
                limit=16 * 1024 * 1024,
            )
        except GenericAssetProductionError as exc:
            _fail("processing_recovery_mismatch", str(exc))
        if (
            len(source) != step["source_size_bytes"]
            or hashlib.sha256(source).hexdigest() != step["source_sha256"]
        ):
            _fail(
                "processing_recovery_mismatch",
                f"source bytes changed for retained step {step['step_id']}",
            )
        expected_payload, metadata = _processed_payload(step, source)
        if payload != expected_payload:
            _fail(
                "processing_recovery_mismatch",
                f"retained bytes are not the deterministic output for {step['step_id']}",
            )
        expected_artifacts.append(_processed_output_record(step, payload, metadata))
    return _recovery_document(
        recipe,
        failure_code=str(recovery["failure_code"]),
        retained_artifacts=expected_artifacts,
    )


def validate_asset_processing_receipt(
    value: object,
    *,
    recipe: object,
    **lineage: object,
) -> dict[str, Any]:
    document = validate_asset_processing_receipt_document(value)
    checked_recipe = validate_asset_processing_recipe(recipe, **lineage)
    if document["status"] == "completed":
        expected_outputs = _expected_processed_outputs(
            checked_recipe,
            lineage["artifact_root"],
            publish=False,
        )
        expected_recovery = None
    else:
        expected_outputs = []
        recovery = document["recovery"]
        assert isinstance(recovery, Mapping)
        expected_recovery = _expected_recovery(
            recovery,
            checked_recipe,
            lineage["artifact_root"],
        )
    expected = _processing_receipt_document(
        checked_recipe,
        processing_receipt_id=document["processing_receipt_id"],
        status=document["status"],
        outputs=expected_outputs,
        failure_reasons=document["failure_reasons"],
        recovery=expected_recovery,
    )
    if document != expected:
        if document["status"] == "failed":
            _fail(
                "processing_recovery_mismatch",
                "failed processing receipt is not the exact retained-artifact recovery result",
            )
        _fail(
            "processing_lineage_mismatch",
            "processing receipt is not the exact byte-derived recipe result",
        )
    return document


def _criterion_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _qa_checks(
    media_type: str,
    *,
    hash_matches: bool,
    media_inspected: bool,
    metadata_matches: bool,
    path_matches: bool,
    license_matches: bool,
) -> list[dict[str, str]]:
    media_check = next(
        name for name, candidate_media in _MEDIA_CHECKS if candidate_media == media_type
    )
    statuses = {
        "hash": "passed" if hash_matches else "failed",
        "media": "passed" if media_inspected and metadata_matches else "failed",
        "path": "passed" if path_matches else "failed",
        "license": "passed" if license_matches else "failed",
        media_check: "passed" if media_inspected else "failed",
    }
    return [
        {
            "check_id": check_id,
            "status": statuses.get(check_id, "not_applicable"),
        }
        for check_id in _QA_CHECK_ORDER
    ]


def _bind_completed_receipt_to_recipe(
    receipt: Mapping[str, object],
    recipe: Mapping[str, object],
) -> None:
    if receipt["status"] != "completed":
        _fail("qa_processing_incomplete", "QA requires a completed processing receipt")
    _bind_processing_receipt_header(receipt, recipe)
    if receipt["recovery"] is not None:
        _fail(
            "qa_processing_binding_mismatch",
            "completed processing receipt cannot carry recovery evidence",
        )
    outputs = receipt["outputs"]
    steps = recipe["steps"]
    assert isinstance(outputs, list)
    assert isinstance(steps, list)
    if len(outputs) != len(steps):
        _fail(
            "qa_processing_binding_mismatch",
            "processing receipt outputs do not exactly cover recipe steps",
        )
    _bind_processed_outputs_to_steps(
        outputs,
        steps,
        context="processing receipt outputs",
    )


def _bind_processing_receipt_header(
    receipt: Mapping[str, object],
    recipe: Mapping[str, object],
) -> None:
    for field in (
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
    ):
        if receipt[field] != recipe[field]:
            _fail(
                "qa_processing_binding_mismatch",
                f"processing receipt {field} does not match recipe",
            )
    if (
        receipt["recipe"] != _document_identity(recipe, "recipe_id")
        or receipt["processor"] != recipe["processor"]
    ):
        _fail(
            "qa_processing_binding_mismatch",
            "processing receipt identity or processor is inconsistent",
        )


_PROCESSED_OUTPUT_STEP_FIELDS = (
    ("step_id", "step_id"),
    ("candidate_artifact_id", "candidate_artifact_id"),
    ("source_sha256", "source_sha256"),
    ("role", "role"),
    ("media_type", "media_type"),
    ("runtime_path", "runtime_path"),
    ("locator", "output_locator"),
)


def _bind_processed_outputs_to_steps(
    outputs: Sequence[Mapping[str, object]],
    steps: Sequence[Mapping[str, object]],
    *,
    context: str,
) -> None:
    for index, (output, step) in enumerate(zip(outputs, steps, strict=True)):
        if any(
            output[output_field] != step[step_field]
            for output_field, step_field in _PROCESSED_OUTPUT_STEP_FIELDS
        ):
            _fail(
                "qa_processing_binding_mismatch",
                f"{context}/{index} is not the exact recipe step binding",
            )


def validate_processing_receipt_recipe_coherence(
    receipt: object,
    recipe: object,
) -> dict[str, Any]:
    """Validate the pure semantic binding between one D2 receipt and recipe."""

    checked_receipt = validate_asset_processing_receipt_document(receipt)
    checked_recipe = validate_asset_processing_recipe_document(recipe)
    if checked_receipt["status"] == "completed":
        _bind_completed_receipt_to_recipe(checked_receipt, checked_recipe)
        return checked_receipt

    _bind_processing_receipt_header(checked_receipt, checked_recipe)
    recovery = checked_receipt["recovery"]
    assert isinstance(recovery, Mapping)
    retained = recovery["retained_artifacts"]
    steps = checked_recipe["steps"]
    assert isinstance(retained, list)
    assert isinstance(steps, list)
    if len(retained) > len(steps):
        _fail(
            "processing_recovery_mismatch",
            "retained recovery outputs exceed the exact recipe prefix",
        )
    _bind_processed_outputs_to_steps(
        retained,
        steps[: len(retained)],
        context="processing recovery retained_artifacts",
    )
    return checked_receipt


def _qa_retained_outputs(
    processing_receipt: Mapping[str, object],
    recipe: Mapping[str, object],
    artifact_root: str | Path,
) -> list[dict[str, Any]]:
    _bind_completed_receipt_to_recipe(processing_receipt, recipe)
    outputs = processing_receipt["outputs"]
    steps = recipe["steps"]
    assert isinstance(outputs, list)
    assert isinstance(steps, list)
    captured_outputs: list[dict[str, Any]] = []
    for output, step in zip(outputs, steps, strict=True):
        assert isinstance(output, Mapping)
        assert isinstance(step, Mapping)
        try:
            retained = _safe_artifact_bytes(
                artifact_root,
                output["locator"],
                limit=16 * 1024 * 1024,
            )
        except GenericAssetProductionError as exc:
            _fail("qa_output_unavailable", str(exc))
        digest = hashlib.sha256(retained).hexdigest()
        hash_matches = digest == output["sha256"] and len(retained) == output["size_bytes"]
        expectations = copy.deepcopy(step["expectations"])
        if str(step["operation"]).startswith("canonicalize_"):
            expectations["max_bytes"] = max(
                int(expectations["max_bytes"]),
                len(retained),
            )
        metadata: dict[str, object] | None
        try:
            metadata = _inspect_candidate(
                retained,
                role=str(output["role"]),
                media_type=str(output["media_type"]),
                expectations=expectations,
            )
            media_inspected = True
        except GenericAssetProductionError:
            metadata = None
            media_inspected = False
        metadata_matches = media_inspected and metadata == output["metadata"]
        captured_outputs.append(
            {
                "candidate_artifact_id": output["candidate_artifact_id"],
                "role": output["role"],
                "media_type": output["media_type"],
                "runtime_path": output["runtime_path"],
                "locator": output["locator"],
                "sha256": digest,
                "size_bytes": len(retained),
                "metadata": copy.deepcopy(metadata),
                "checks": _qa_checks(
                    str(output["media_type"]),
                    hash_matches=hash_matches,
                    media_inspected=media_inspected,
                    metadata_matches=metadata_matches,
                    path_matches=True,
                    license_matches=True,
                ),
            }
        )
    return captured_outputs


def _validate_acceptance_results(
    value: object,
    specification: Mapping[str, object],
) -> list[dict[str, Any]]:
    criteria = specification["acceptance_criteria"]
    if (
        not isinstance(value, list)
        or len(value) != len(criteria)
        or len(value) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
    ):
        _fail(
            "qa_acceptance_coverage",
            "acceptance results must exactly cover specification criteria",
        )
    checked = []
    for index, raw in enumerate(value):
        context = f"asset QA report.acceptance_criteria/{index}"
        try:
            result = _object(raw, context)
            _exact_keys(result, _QA_CRITERION_FIELDS, context)
            if result.get("criterion_index") != index:
                _fail(
                    "qa_acceptance_coverage",
                    f"{context}.criterion_index is not exact",
                )
            expected_hash = _criterion_hash(str(criteria[index]))
            if result.get("criterion_sha256") != expected_hash:
                _fail(
                    "qa_acceptance_hash_mismatch",
                    f"{context}.criterion_sha256 is not exact",
                )
            if result.get("status") not in {"passed", "failed"}:
                _fail(
                    "qa_status_invalid",
                    f"{context}.status must be passed or failed",
                )
            evidence = _string_array(
                result.get("evidence_hashes"),
                f"{context}.evidence_hashes",
                allow_empty=False,
            )
            if len(evidence) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS:
                _fail("qa_contract_limit", f"{context}.evidence_hashes exceeds its bound")
            for evidence_index, evidence_hash in enumerate(evidence):
                _sha256(
                    evidence_hash,
                    f"{context}.evidence_hashes/{evidence_index}",
                )
            if evidence != sorted(evidence) or len(set(evidence)) != len(evidence):
                _fail(
                    "qa_evidence_noncanonical",
                    f"{context}.evidence_hashes must be sorted and unique",
                )
            checked.append(copy.deepcopy(result))
        except GenericAssetProcessingError:
            raise
        except CreationContractError as exc:
            _fail("qa_report_invalid", str(exc))
    return checked


_QA_PROCESSING_OUTPUT_FIELDS = (
    "candidate_artifact_id",
    "role",
    "media_type",
    "runtime_path",
    "locator",
    "sha256",
    "size_bytes",
    "metadata",
)


def validate_asset_qa_semantic_coherence(
    value: object,
    *,
    processing_receipt: object,
    recipe: object,
    specification: Mapping[str, object],
) -> dict[str, Any]:
    """Validate pure passed-QA continuity without rereading retained bytes."""

    report = validate_asset_qa_report_document(value)
    checked_recipe = validate_asset_processing_recipe_document(recipe)
    checked_receipt = validate_processing_receipt_recipe_coherence(
        processing_receipt,
        checked_recipe,
    )
    if checked_receipt["status"] != "completed":
        _fail(
            "qa_processing_incomplete",
            "QA cannot be based on a failed processing receipt",
        )
    if report["recipe"] != _document_identity(checked_recipe, "recipe_id") or report[
        "processing_receipt"
    ] != _document_identity(checked_receipt, "processing_receipt_id"):
        _fail(
            "qa_processing_binding_mismatch",
            "QA references do not match the exact recipe and processing receipt",
        )
    _validate_acceptance_results(
        report["acceptance_criteria"],
        specification,
    )
    if report["status"] != "passed":
        return report

    qa_outputs = report["outputs"]
    processing_outputs = checked_receipt["outputs"]
    assert isinstance(qa_outputs, list)
    assert isinstance(processing_outputs, list)
    if len(qa_outputs) != len(processing_outputs):
        _fail(
            "qa_processing_binding_mismatch",
            "passed QA outputs do not exactly cover processing outputs",
        )
    for index, (qa_output, processing_output) in enumerate(
        zip(qa_outputs, processing_outputs, strict=True)
    ):
        assert isinstance(qa_output, Mapping)
        assert isinstance(processing_output, Mapping)
        if any(
            qa_output[field] != processing_output[field] for field in _QA_PROCESSING_OUTPUT_FIELDS
        ):
            _fail(
                "qa_processing_binding_mismatch",
                f"passed QA output {index} contradicts processing evidence",
            )
    return report


def _qa_report_document(
    processing_receipt: Mapping[str, object],
    recipe: Mapping[str, object],
    *,
    qa_report_id: str,
    retained_outputs: Sequence[Mapping[str, object]],
    acceptance_results: Sequence[Mapping[str, object]],
) -> dict[str, Any]:
    try:
        _identifier(qa_report_id, "asset QA report.qa_report_id")
    except CreationContractError as exc:
        _fail("qa_report_invalid", str(exc))
    outputs = copy.deepcopy(list(retained_outputs))
    blockers = [
        f"acceptance_criterion_{result['criterion_index']}_failed"
        for result in acceptance_results
        if result["status"] == "failed"
    ]
    blockers.extend(
        f"output_{output['role']}_{check['check_id']}_failed"
        for output in outputs
        for check in output["checks"]
        if check["status"] == "failed"
    )
    blockers.sort(key=lambda item: item.encode("utf-8"))
    roles = [output["role"] for output in outputs]
    document = {
        "format": ASSET_QA_REPORT_FORMAT,
        "format_version": 1,
        "qa_report_id": qa_report_id,
        **{
            key: copy.deepcopy(recipe[key])
            for key in (
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
            )
        },
        "recipe": _document_identity(recipe, "recipe_id"),
        "processing_receipt": _document_identity(
            processing_receipt,
            "processing_receipt_id",
        ),
        "status": "failed" if blockers else "passed",
        "outputs": outputs,
        "acceptance_criteria": copy.deepcopy(list(acceptance_results)),
        "multi_output_check": {
            "status": "passed" if len(outputs) > 1 else "not_applicable",
            "roles": roles,
        },
        "blockers": blockers,
    }
    return _seal(document)


def build_asset_qa_report(
    processing_receipt: object,
    *,
    recipe: object,
    qa_report_id: str,
    acceptance_results: Sequence[Mapping[str, object]],
    **lineage: object,
) -> dict[str, Any]:
    checked_recipe = validate_asset_processing_recipe(recipe, **lineage)
    checked_receipt = validate_asset_processing_receipt_document(processing_receipt)
    _bind_completed_receipt_to_recipe(checked_receipt, checked_recipe)
    checked_acceptance = _validate_acceptance_results(
        acceptance_results,
        lineage["specification"],
    )
    retained_outputs = _qa_retained_outputs(
        checked_receipt,
        checked_recipe,
        lineage["artifact_root"],
    )
    return validate_asset_qa_report_document(
        _qa_report_document(
            checked_receipt,
            checked_recipe,
            qa_report_id=qa_report_id,
            retained_outputs=retained_outputs,
            acceptance_results=checked_acceptance,
        )
    )


def _validate_qa_output(value: object, context: str) -> dict[str, Any]:
    try:
        output = _object(value, context)
        _exact_keys(output, _QA_OUTPUT_FIELDS, context)
        _identifier(
            output.get("candidate_artifact_id"),
            f"{context}.candidate_artifact_id",
        )
        role = _identifier(output.get("role"), f"{context}.role")
        media_type = _non_empty_string(
            output.get("media_type"),
            f"{context}.media_type",
        )
        _operation(role, media_type)
        _portable_relative_path(
            output.get("runtime_path"),
            f"{context}.runtime_path",
        )
        _portable_relative_path(output.get("locator"), f"{context}.locator")
        _sha256(output.get("sha256"), f"{context}.sha256")
        _bounded_asset_size(
            output.get("size_bytes"),
            f"{context}.size_bytes",
        )
        metadata = output.get("metadata")
        if metadata is not None:
            _validate_metadata(metadata, media_type, f"{context}.metadata")
        checks = output.get("checks")
        if not isinstance(checks, list) or len(checks) != MAX_QA_CHECKS:
            _fail("qa_check_coverage", f"{context}.checks must contain ten checks")
        media_check = next(
            name for name, candidate_media in _MEDIA_CHECKS if candidate_media == media_type
        )
        for check_index, raw_check in enumerate(checks):
            check_context = f"{context}.checks/{check_index}"
            check = _object(raw_check, check_context)
            _exact_keys(check, _QA_CHECK_FIELDS, check_context)
            if check.get("check_id") != _QA_CHECK_ORDER[check_index]:
                _fail(
                    "qa_check_coverage",
                    f"{check_context}.check_id is not canonical",
                )
            status = check.get("status")
            if check["check_id"] in {"hash", "media", media_check}:
                if status not in {"passed", "failed"}:
                    _fail(
                        "qa_check_coverage",
                        f"{check_context}.status must be passed or failed",
                    )
            elif check["check_id"] in {"path", "license"}:
                if status != "passed":
                    _fail(
                        "qa_check_coverage",
                        f"{check_context}.status must be passed",
                    )
            elif status != "not_applicable":
                _fail(
                    "qa_check_coverage",
                    f"{check_context}.status must be not_applicable",
                )
        statuses = {check["check_id"]: check["status"] for check in checks}
        if metadata is None and (
            statuses["media"] != "failed" or statuses[media_check] != "failed"
        ):
            _fail(
                "qa_check_coverage",
                f"{context}.metadata null requires failed media checks",
            )
        if metadata is not None and statuses[media_check] != "passed":
            _fail(
                "qa_check_coverage",
                f"{context}.captured metadata requires a passed format check",
            )
        return output
    except GenericAssetProcessingError:
        raise
    except (CreationContractError, GenericAssetProductionError, TypeError, ValueError) as exc:
        _fail("qa_report_invalid", str(exc))


def _validate_qa_report_structure(value: object) -> dict[str, Any]:
    _ensure_structure(value, "asset QA report")
    try:
        document = _object(value, "asset QA report")
        _exact_keys(document, _QA_REPORT_FIELDS, "asset QA report")
        if document.get("format") != ASSET_QA_REPORT_FORMAT:
            _fail(
                "qa_report_format_invalid",
                f"format must be {ASSET_QA_REPORT_FORMAT}",
            )
        if document.get("format_version") != 1:
            _fail("qa_report_version_invalid", "format_version must be 1")
        _identifier(document.get("qa_report_id"), "asset QA report.qa_report_id")
        for field, expected_format in _COMMON_IDENTITY_FORMATS.items():
            _identity_value(
                document.get(field),
                f"asset QA report.{field}",
                expected_format=expected_format,
            )
        asset = _object(document.get("asset"), "asset QA report.asset")
        _exact_keys(asset, _ASSET_FIELDS, "asset QA report.asset")
        _identifier(asset.get("asset_id"), "asset QA report.asset.asset_id")
        _sha256(asset.get("content_hash"), "asset QA report.asset.content_hash")
        _identity_value(
            document.get("recipe"),
            "asset QA report.recipe",
            expected_format=ASSET_PROCESSING_RECIPE_FORMAT,
        )
        _identity_value(
            document.get("processing_receipt"),
            "asset QA report.processing_receipt",
            expected_format=ASSET_PROCESSING_RECEIPT_FORMAT,
        )
        outputs = document.get("outputs")
        if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_PROCESSING_OUTPUTS:
            _fail("qa_report_invalid", "outputs must be a bounded non-empty array")
        checked_outputs = [
            _validate_qa_output(output, f"asset QA report.outputs/{index}")
            for index, output in enumerate(outputs)
        ]
        roles = [output["role"] for output in checked_outputs]
        candidate_ids = [output["candidate_artifact_id"] for output in checked_outputs]
        if (
            roles != sorted(roles, key=lambda item: item.encode("utf-8"))
            or len(set(role.casefold() for role in roles)) != len(roles)
            or len(set(item.casefold() for item in candidate_ids)) != len(candidate_ids)
        ):
            _fail(
                "qa_report_noncanonical",
                "outputs must use unique UTF-8-sorted roles and candidate IDs",
            )
        _portable_path_tree(
            [
                *[output["runtime_path"] for output in checked_outputs],
                *[output["locator"] for output in checked_outputs],
            ],
            "asset QA report runtime and retained paths",
        )
        criteria = document.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria:
            _fail(
                "qa_acceptance_coverage",
                "acceptance_criteria must be a non-empty array",
            )
        if len(criteria) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS:
            _fail(
                "qa_contract_limit",
                "asset QA report acceptance_criteria exceeds its bound",
            )
        checked_criteria = []
        for index, raw_criterion in enumerate(criteria):
            context = f"asset QA report.acceptance_criteria/{index}"
            criterion = _object(raw_criterion, context)
            _exact_keys(criterion, _QA_CRITERION_FIELDS, context)
            if criterion.get("criterion_index") != index:
                _fail(
                    "qa_acceptance_coverage",
                    f"{context}.criterion_index is not canonical",
                )
            _sha256(
                criterion.get("criterion_sha256"),
                f"{context}.criterion_sha256",
            )
            if criterion.get("status") not in {"passed", "failed"}:
                _fail("qa_status_invalid", f"{context}.status is invalid")
            evidence = criterion.get("evidence_hashes")
            if not isinstance(evidence, list) or not evidence:
                _fail(
                    "qa_evidence_invalid",
                    f"{context}.evidence_hashes must be non-empty",
                )
            if len(evidence) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS:
                _fail(
                    "qa_contract_limit",
                    f"{context}.evidence_hashes exceeds its bound",
                )
            for evidence_index, evidence_hash in enumerate(evidence):
                _sha256(
                    evidence_hash,
                    f"{context}.evidence_hashes/{evidence_index}",
                )
            if evidence != sorted(evidence) or len(set(evidence)) != len(evidence):
                _fail(
                    "qa_evidence_noncanonical",
                    f"{context}.evidence_hashes must be sorted and unique",
                )
            checked_criteria.append(criterion)
        criterion_hashes = [criterion["criterion_sha256"] for criterion in checked_criteria]
        if len(set(criterion_hashes)) != len(criterion_hashes):
            _fail(
                "qa_acceptance_coverage",
                "acceptance criterion hashes must be unique",
            )
        multi = _object(
            document.get("multi_output_check"),
            "asset QA report.multi_output_check",
        )
        _exact_keys(
            multi,
            _MULTI_OUTPUT_FIELDS,
            "asset QA report.multi_output_check",
        )
        checked_multi_roles = _string_array(
            multi.get("roles"),
            "asset QA report.multi_output_check.roles",
            allow_empty=False,
        )
        expected_multi_status = "passed" if len(roles) > 1 else "not_applicable"
        if multi.get("status") != expected_multi_status or checked_multi_roles != roles:
            _fail(
                "qa_multi_output_mismatch",
                "multi-output applicability and exact roles are inconsistent",
            )
        expected_blockers = [
            f"acceptance_criterion_{criterion['criterion_index']}_failed"
            for criterion in checked_criteria
            if criterion["status"] == "failed"
        ]
        expected_blockers.extend(
            f"output_{output['role']}_{check['check_id']}_failed"
            for output in checked_outputs
            for check in output["checks"]
            if check["status"] == "failed"
        )
        expected_blockers.sort(key=lambda item: item.encode("utf-8"))
        blockers = document.get("blockers")
        if blockers != expected_blockers:
            _fail(
                "qa_status_contradiction",
                "QA blockers are not the exact failed criteria and output checks",
            )
        expected_status = "failed" if expected_blockers else "passed"
        if document.get("status") != expected_status:
            _fail(
                "qa_status_contradiction",
                "QA report status does not match its exact blockers",
            )
        _validate_hash(document, "asset QA report")
        return copy.deepcopy(document)
    except GenericAssetProcessingError:
        raise
    except (
        CreationContractError,
        GenericAssetProductionError,
        TypeError,
        ValueError,
    ) as exc:
        _fail("qa_report_invalid", str(exc))


def validate_asset_qa_report_document(value: object) -> dict[str, Any]:
    return _validate_qa_report_structure(value)


def validate_asset_qa_report(
    value: object,
    *,
    recipe: object,
    processing_receipt: object,
    **lineage: object,
) -> dict[str, Any]:
    document = validate_asset_qa_report_document(value)
    checked_recipe = validate_asset_processing_recipe(recipe, **lineage)
    checked_receipt = validate_asset_processing_receipt_document(processing_receipt)
    _bind_completed_receipt_to_recipe(checked_receipt, checked_recipe)
    checked_acceptance = _validate_acceptance_results(
        document["acceptance_criteria"],
        lineage["specification"],
    )
    retained_outputs = _qa_retained_outputs(
        checked_receipt,
        checked_recipe,
        lineage["artifact_root"],
    )
    expected = _qa_report_document(
        checked_receipt,
        checked_recipe,
        qa_report_id=document["qa_report_id"],
        retained_outputs=retained_outputs,
        acceptance_results=checked_acceptance,
    )
    if document != expected:
        _fail(
            "qa_lineage_mismatch",
            "QA report is not the exact retained-byte processing result",
        )
    return document


def _manifest_output(output: Mapping[str, object]) -> dict[str, object]:
    return {
        "role": output["role"],
        "media_type": output["media_type"],
        "runtime_path": output["runtime_path"],
        "locator": output["locator"],
        "sha256": output["sha256"],
        "size_bytes": output["size_bytes"],
    }


def _manifest_asset_entry(
    record: Mapping[str, object],
    *,
    state: str,
) -> dict[str, Any]:
    required = {
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
        "license_records",
        "recipe",
        "processing_receipt",
        "qa_report",
    }
    if set(record) != required:
        _fail(
            "manifest_asset_record_invalid",
            "asset record fields are not exact",
        )
    specification = record["specification"]
    request = record["request"]
    receipt = record["receipt"]
    selection = record["selection"]
    provenance = record["provenance"]
    licenses = record["license_records"]
    recipe = record["recipe"]
    processing_receipt = record["processing_receipt"]
    qa_report = record["qa_report"]
    if not isinstance(specification, Mapping):
        _fail("manifest_asset_record_invalid", "specification must be an object")
    if state == "produced":
        if recipe is not None or processing_receipt is not None or qa_report is not None:
            _fail(
                "manifest_state_contradiction",
                "produced assets cannot cite processing or QA",
            )
        output_source = receipt["outputs"]
    elif state == "processed":
        if recipe is None or processing_receipt is None or qa_report is not None:
            _fail(
                "manifest_state_contradiction",
                "processed assets require recipe/receipt and no QA identity",
            )
        output_source = processing_receipt["outputs"]
    elif state == "release_ready":
        if recipe is None or processing_receipt is None or qa_report is None:
            _fail(
                "manifest_state_contradiction",
                "release_ready assets require processing and QA",
            )
        if qa_report["status"] != "passed":
            _fail(
                "manifest_qa_blocked",
                "release_ready assets require a passed QA report",
            )
        for license_record in licenses:
            permissions = license_record["permissions"]
            if not permissions["commercial_use"] or not permissions["redistribution"]:
                _fail(
                    "manifest_license_blocked",
                    "release_ready requires commercial use and redistribution",
                )
        output_source = processing_receipt["outputs"]
    else:
        _fail("manifest_state_invalid", f"unsupported manifest state {state}")
    license_identities = [
        _document_identity(license_record, "license_record_id") for license_record in licenses
    ]
    license_identities.sort(key=lambda item: item["id"].encode("utf-8"))
    return {
        "asset": copy.deepcopy(specification["asset"]),
        "specification": _identity(specification, id_field="spec_id"),
        "request": _document_identity(request, "request_id"),
        "receipt": _document_identity(receipt, "receipt_id"),
        "selection": _document_identity(selection, "selection_id"),
        "provenance": _document_identity(provenance, "provenance_id"),
        "licenses": license_identities,
        "processing_recipe": (None if recipe is None else _document_identity(recipe, "recipe_id")),
        "processing_receipt": (
            None
            if processing_receipt is None
            else _document_identity(processing_receipt, "processing_receipt_id")
        ),
        "qa_report": (None if qa_report is None else _document_identity(qa_report, "qa_report_id")),
        "state": state,
        "outputs": [_manifest_output(output) for output in output_source],
    }


def _checked_manifest_root(
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
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
        return (
            checked_gamepack,
            checked_subject,
            checked_target,
            checked_style,
            checked_inventory,
        )
    except (
        CreationContractError,
        GenericAssetError,
        GamepackError,
        TypeError,
        ValueError,
    ) as exc:
        _fail("manifest_lineage_invalid", str(exc))


def _validate_manifest_record(
    record: Mapping[str, object],
    *,
    state: str,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    artifact_root: str | Path,
) -> dict[str, Any]:
    expected_fields = {
        "specification",
        "request",
        "receipt",
        "selection",
        "provenance",
        "license_records",
        "recipe",
        "processing_receipt",
        "qa_report",
    }
    if set(record) != expected_fields:
        _fail(
            "manifest_asset_record_invalid",
            "asset record fields are not exact",
        )
    lineage = {
        "gamepack": gamepack,
        "subject": subject,
        "target": target,
        "style": style,
        "inventory": inventory,
        "specification": record["specification"],
        "request": record["request"],
        "receipt": record["receipt"],
        "selection": record["selection"],
        "provenance": record["provenance"],
        "license_records": record["license_records"],
        "artifact_root": artifact_root,
    }
    _checked_d2a_chain(**lineage)
    recipe = record["recipe"]
    processing_receipt = record["processing_receipt"]
    qa_report = record["qa_report"]
    if recipe is not None:
        recipe = validate_asset_processing_recipe(recipe, **lineage)
    if processing_receipt is not None:
        if recipe is None:
            _fail(
                "manifest_state_contradiction",
                "processing receipt requires recipe",
            )
        processing_receipt = validate_asset_processing_receipt(
            processing_receipt,
            recipe=recipe,
            **lineage,
        )
    if qa_report is not None:
        if recipe is None or processing_receipt is None:
            _fail(
                "manifest_state_contradiction",
                "QA requires complete processing",
            )
        qa_report = validate_asset_qa_report(
            qa_report,
            recipe=recipe,
            processing_receipt=processing_receipt,
            **lineage,
        )
    checked_record = {
        **record,
        "recipe": recipe,
        "processing_receipt": processing_receipt,
        "qa_report": qa_report,
    }
    return _manifest_asset_entry(checked_record, state=state)


def _manifest_document(
    roots: tuple[dict[str, Any], ...],
    *,
    manifest_id: str,
    state: str,
    asset_records: Sequence[Mapping[str, object]],
    artifact_root: str | Path,
) -> dict[str, Any]:
    gamepack, subject, target, style, inventory = roots
    try:
        _identifier(manifest_id, "generic asset manifest.manifest_id")
    except CreationContractError as exc:
        _fail("manifest_invalid", str(exc))
    if state not in {"produced", "processed", "release_ready"}:
        _fail("manifest_state_invalid", f"unsupported manifest state {state}")
    if (
        not isinstance(asset_records, Sequence)
        or isinstance(asset_records, (str, bytes, bytearray))
        or len(asset_records) > MAX_MANIFEST_ASSETS
    ):
        _fail("manifest_contract_limit", "asset_records exceeds its bound")
    entries = [
        _validate_manifest_record(
            record,
            state=state,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
            inventory=inventory,
            artifact_root=artifact_root,
        )
        for record in asset_records
    ]
    entries.sort(key=lambda item: item["asset"]["asset_id"].encode("utf-8"))
    asset_ids = [entry["asset"]["asset_id"] for entry in entries]
    if len(set(asset_id.casefold() for asset_id in asset_ids)) != len(asset_ids):
        _fail("manifest_asset_collision", "manifest asset IDs collide")
    inventory_assets = {item["asset_id"]: item for item in inventory["assets"]}
    if not set(asset_ids).issubset(inventory_assets):
        _fail("manifest_inventory_extra", "manifest contains an unknown inventory asset")
    required = {asset_id for asset_id, item in inventory_assets.items() if item["required"]}
    if not required.issubset(asset_ids):
        _fail(
            "manifest_inventory_incomplete",
            "manifest omits a required inventory asset",
        )
    runtime_paths = [output["runtime_path"] for entry in entries for output in entry["outputs"]]
    locators = [output["locator"] for entry in entries for output in entry["outputs"]]
    _portable_path_tree(runtime_paths, "generic asset manifest runtime paths")
    _portable_path_tree(locators, "generic asset manifest output locators")
    document = {
        "format": ASSET_MANIFEST_FORMAT,
        "format_version": 1,
        "manifest_id": manifest_id,
        "gamepack": _gamepack_identity(gamepack),
        "asset_subject": _identity(subject, id_field="subject_id"),
        "target": _identity(target, id_field="target_id"),
        "style": _identity(style, id_field="style_id"),
        "inventory": _identity(inventory, id_field="inventory_id"),
        "state": state,
        "assets": entries,
    }
    return _seal(document)


def build_asset_manifest(
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    *,
    manifest_id: str,
    state: str,
    asset_records: Sequence[Mapping[str, object]],
    artifact_root: str | Path,
    qa_reviews: Sequence[object] | None = None,
) -> dict[str, Any]:
    roots = _checked_manifest_root(gamepack, subject, target, style, inventory)
    document = validate_asset_manifest_document(
        _manifest_document(
            roots,
            manifest_id=manifest_id,
            state=state,
            asset_records=asset_records,
            artifact_root=artifact_root,
        )
    )
    if state != "release_ready":
        if qa_reviews is not None:
            _fail(
                "manifest_qa_authority_not_applicable",
                "verified QA authority is accepted only for release_ready manifests",
            )
        return document
    if qa_reviews is None:
        _fail(
            "manifest_qa_authority_required",
            "release_ready manifests require exact verified QA review handles",
        )
    try:
        from worldforge.generic_asset_authority import (
            GenericAssetAuthorityError,
            require_verified_asset_qa_reviews,
        )

        require_verified_asset_qa_reviews(document, qa_reviews)
    except GenericAssetAuthorityError as exc:
        _fail(
            "manifest_qa_authority_invalid",
            f"{exc.reason_code}: {exc.detail}",
        )
    return document


def _validate_manifest_output(value: object, context: str) -> dict[str, Any]:
    try:
        output = _object(value, context)
        _exact_keys(output, _MANIFEST_OUTPUT_FIELDS, context)
        role = _identifier(output.get("role"), f"{context}.role")
        media_type = _non_empty_string(
            output.get("media_type"),
            f"{context}.media_type",
        )
        _operation(role, media_type)
        _portable_relative_path(
            output.get("runtime_path"),
            f"{context}.runtime_path",
        )
        _portable_relative_path(output.get("locator"), f"{context}.locator")
        _sha256(output.get("sha256"), f"{context}.sha256")
        _bounded_asset_size(
            output.get("size_bytes"),
            f"{context}.size_bytes",
        )
        return output
    except GenericAssetProcessingError:
        raise
    except CreationContractError as exc:
        _fail("manifest_invalid", str(exc))


def _validate_manifest_structure(value: object) -> dict[str, Any]:
    _ensure_structure(value, "generic asset manifest")
    try:
        document = _object(value, "generic asset manifest")
        _exact_keys(document, _MANIFEST_FIELDS, "generic asset manifest")
        if document.get("format") != ASSET_MANIFEST_FORMAT:
            _fail(
                "manifest_format_invalid",
                f"format must be {ASSET_MANIFEST_FORMAT}",
            )
        if document.get("format_version") != 1:
            _fail("manifest_version_invalid", "format_version must be 1")
        _identifier(document.get("manifest_id"), "generic asset manifest.manifest_id")
        for field, expected_format in {
            "gamepack": "world-forge.gamepack",
            "asset_subject": ASSET_SUBJECT_FORMAT,
            "target": ASSET_TARGET_FORMAT,
            "style": ASSET_STYLE_FORMAT,
            "inventory": ASSET_INVENTORY_FORMAT,
        }.items():
            _identity_value(
                document.get(field),
                f"generic asset manifest.{field}",
                expected_format=expected_format,
            )
        state = document.get("state")
        if state not in {"produced", "processed", "release_ready"}:
            _fail("manifest_state_invalid", "manifest state is unsupported")
        assets = document.get("assets")
        if not isinstance(assets, list) or len(assets) > MAX_MANIFEST_ASSETS:
            _fail("manifest_contract_limit", "assets must be a bounded array")
        asset_ids = []
        runtime_paths = []
        locators = []
        for index, raw_asset in enumerate(assets):
            context = f"generic asset manifest.assets/{index}"
            asset = _object(raw_asset, context)
            _exact_keys(asset, _MANIFEST_ASSET_FIELDS, context)
            asset_identity = _object(asset.get("asset"), f"{context}.asset")
            _exact_keys(asset_identity, _ASSET_FIELDS, f"{context}.asset")
            asset_id = _identifier(
                asset_identity.get("asset_id"),
                f"{context}.asset.asset_id",
            )
            _sha256(
                asset_identity.get("content_hash"),
                f"{context}.asset.content_hash",
            )
            for field, expected_format in (
                ("specification", ASSET_SPEC_FORMAT),
                ("request", ASSET_PRODUCTION_REQUEST_FORMAT),
                ("receipt", ASSET_PRODUCTION_RECEIPT_FORMAT),
                ("selection", ASSET_SELECTION_FORMAT),
                ("provenance", ASSET_PROVENANCE_FORMAT),
            ):
                _identity_value(
                    asset.get(field),
                    f"{context}.{field}",
                    expected_format=expected_format,
                )
            licenses = asset.get("licenses")
            if not isinstance(licenses, list) or not 1 <= len(licenses) <= MAX_PROCESSING_OUTPUTS:
                _fail(
                    "manifest_license_coverage",
                    f"{context}.licenses must be bounded and non-empty",
                )
            for license_index, license_identity in enumerate(licenses):
                _identity_value(
                    license_identity,
                    f"{context}.licenses/{license_index}",
                    expected_format=ASSET_LICENSE_FORMAT,
                )
            license_ids = [license_identity["id"] for license_identity in licenses]
            license_hashes = [license_identity["content_hash"] for license_identity in licenses]
            if license_ids != sorted(
                license_ids,
                key=lambda item: item.encode("utf-8"),
            ):
                _fail(
                    "manifest_noncanonical",
                    f"{context}.licenses must be UTF-8 ID sorted",
                )
            if len(set(item.casefold() for item in license_ids)) != len(license_ids) or len(
                set(license_hashes)
            ) != len(license_hashes):
                _fail(
                    "manifest_license_coverage",
                    f"{context}.licenses must be unique",
                )
            expected_presence = {
                "produced": (False, False, False),
                "processed": (True, True, False),
                "release_ready": (True, True, True),
            }[state]
            for field, expected_format, present in zip(
                ("processing_recipe", "processing_receipt", "qa_report"),
                (
                    ASSET_PROCESSING_RECIPE_FORMAT,
                    ASSET_PROCESSING_RECEIPT_FORMAT,
                    ASSET_QA_REPORT_FORMAT,
                ),
                expected_presence,
                strict=True,
            ):
                field_value = asset.get(field)
                if present:
                    _identity_value(
                        field_value,
                        f"{context}.{field}",
                        expected_format=expected_format,
                    )
                elif field_value is not None:
                    _fail(
                        "manifest_state_contradiction",
                        f"{context}.{field} must be null in {state}",
                    )
            if asset.get("state") != state:
                _fail(
                    "manifest_state_contradiction",
                    f"{context}.state must match manifest state",
                )
            outputs = asset.get("outputs")
            if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_PROCESSING_OUTPUTS:
                _fail("manifest_output_coverage", f"{context}.outputs is invalid")
            checked_outputs = [
                _validate_manifest_output(
                    output,
                    f"{context}.outputs/{output_index}",
                )
                for output_index, output in enumerate(outputs)
            ]
            roles = [output["role"] for output in checked_outputs]
            if roles != sorted(roles, key=lambda item: item.encode("utf-8")):
                _fail(
                    "manifest_noncanonical",
                    f"{context}.outputs must be UTF-8 role sorted",
                )
            runtime_paths.extend(output["runtime_path"] for output in checked_outputs)
            locators.extend(output["locator"] for output in checked_outputs)
            asset_ids.append(asset_id)
        if asset_ids != sorted(asset_ids, key=lambda item: item.encode("utf-8")):
            _fail(
                "manifest_noncanonical",
                "assets must be UTF-8 asset-ID sorted",
            )
        if len(set(asset_id.casefold() for asset_id in asset_ids)) != len(asset_ids):
            _fail("manifest_asset_collision", "asset IDs collide")
        _portable_path_tree(runtime_paths, "generic asset manifest runtime paths")
        _portable_path_tree(locators, "generic asset manifest output locators")
        _validate_hash(document, "generic asset manifest")
        return copy.deepcopy(document)
    except GenericAssetProcessingError:
        raise
    except (CreationContractError, GenericAssetError, TypeError, ValueError) as exc:
        _fail("manifest_invalid", str(exc))


def validate_asset_manifest_document(value: object) -> dict[str, Any]:
    return _validate_manifest_structure(value)


def validate_asset_manifest(
    value: object,
    *,
    gamepack: object,
    subject: object,
    target: object,
    style: object,
    inventory: object,
    asset_records: Sequence[Mapping[str, object]],
    artifact_root: str | Path,
) -> dict[str, Any]:
    document = validate_asset_manifest_document(value)
    roots = _checked_manifest_root(gamepack, subject, target, style, inventory)
    expected = _manifest_document(
        roots,
        manifest_id=document["manifest_id"],
        state=document["state"],
        asset_records=asset_records,
        artifact_root=artifact_root,
    )
    if document != expected:
        _fail(
            "manifest_lineage_mismatch",
            "manifest is not the exact target-scoped lineage rebuild",
        )
    return document


_DOCUMENT_VALIDATORS = {
    ASSET_PROCESSING_RECIPE_FORMAT: validate_asset_processing_recipe_document,
    ASSET_PROCESSING_RECEIPT_FORMAT: validate_asset_processing_receipt_document,
    ASSET_QA_REPORT_FORMAT: validate_asset_qa_report_document,
    ASSET_MANIFEST_FORMAT: validate_asset_manifest_document,
}


def validate_asset_processing_contract_document(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("processing_contract_invalid", "contract root must be an object")
    validator = _DOCUMENT_VALIDATORS.get(value.get("format"))
    if validator is None:
        _fail("processing_contract_format_invalid", "unsupported processing format")
    return validator(value)


def serialize_asset_processing_contract(value: object) -> bytes:
    return canonical_json_bytes(validate_asset_processing_contract_document(value))


def _read_contract(
    path: str | Path,
    validator: Any,
    **dependencies: object,
) -> dict[str, Any]:
    try:
        document = read_creation_object(path)
        return validator(document, **dependencies)
    except GenericAssetProcessingError:
        raise
    except (CreationContractError, OSError, TypeError, ValueError) as exc:
        _fail("processing_contract_read_failed", str(exc))


def load_asset_processing_recipe(
    path: str | Path,
    **lineage: object,
) -> dict[str, Any]:
    return _read_contract(path, validate_asset_processing_recipe, **lineage)


def load_asset_processing_receipt(
    path: str | Path,
    *,
    recipe: object,
    **lineage: object,
) -> dict[str, Any]:
    return _read_contract(
        path,
        validate_asset_processing_receipt,
        recipe=recipe,
        **lineage,
    )


def load_asset_qa_report(
    path: str | Path,
    *,
    recipe: object,
    processing_receipt: object,
    **lineage: object,
) -> dict[str, Any]:
    return _read_contract(
        path,
        validate_asset_qa_report,
        recipe=recipe,
        processing_receipt=processing_receipt,
        **lineage,
    )


def load_asset_manifest(
    path: str | Path,
    **dependencies: object,
) -> dict[str, Any]:
    return _read_contract(path, validate_asset_manifest, **dependencies)


def _publish(
    path: str | Path,
    document: Mapping[str, Any],
) -> PublishedGameArtifact:
    try:
        destination = preflight_game_artifact_output(path)
        write_json_atomic(destination, document, durable_parent=True)
        return _published_artifact(destination, document)
    except GenericAssetProcessingError:
        raise
    except (AssetContractError, GamepackError, OSError) as exc:
        reason = (
            "output_exists"
            if "exist" in str(exc).casefold() or "overwrite" in str(exc).casefold()
            else "output_publish_failed"
        )
        _fail(reason, str(exc))


def publish_asset_processing_recipe(
    path: str | Path,
    value: object,
    **lineage: object,
) -> PublishedGameArtifact:
    return _publish(path, validate_asset_processing_recipe(value, **lineage))


def publish_asset_processing_receipt(
    path: str | Path,
    value: object,
    *,
    recipe: object,
    **lineage: object,
) -> PublishedGameArtifact:
    return _publish(
        path,
        validate_asset_processing_receipt(
            value,
            recipe=recipe,
            **lineage,
        ),
    )


def publish_asset_qa_report(
    path: str | Path,
    value: object,
    *,
    recipe: object,
    processing_receipt: object,
    **lineage: object,
) -> PublishedGameArtifact:
    return _publish(
        path,
        validate_asset_qa_report(
            value,
            recipe=recipe,
            processing_receipt=processing_receipt,
            **lineage,
        ),
    )


def publish_asset_manifest(
    path: str | Path,
    value: object,
    **dependencies: object,
) -> PublishedGameArtifact:
    return _publish(path, validate_asset_manifest(value, **dependencies))
