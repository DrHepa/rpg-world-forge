from __future__ import annotations

import copy
import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.creation_contracts import (
    CreationContractError,
    _exact_keys,
    _identifier,
    _identifier_array,
    _integer,
    _object,
    _portable_relative_path,
    _sha256,
    _validate_json_structure,
    canonical_creation_hash,
    read_creation_object,
)
from worldforge.generic_asset_limits import MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
from worldforge.generic_asset_processing import (
    ASSET_MANIFEST_FORMAT,
    ASSET_PROCESSING_RECEIPT_FORMAT,
    ASSET_PROCESSING_RECIPE_FORMAT,
    ASSET_QA_REPORT_FORMAT,
    GenericAssetProcessingError,
    validate_asset_manifest_document,
    validate_asset_processing_receipt_document,
    validate_asset_qa_report_document,
)
from worldforge.generic_asset_production import (
    ASSET_PRODUCTION_RECEIPT_FORMAT,
    ASSET_PRODUCTION_REQUEST_FORMAT,
    ASSET_PROVENANCE_FORMAT,
    ASSET_SELECTION_FORMAT,
    GenericAssetProductionError,
    read_verified_artifact_bytes,
)
from worldforge.generic_assetpack import (
    GENERIC_ASSETPACK_FORMAT,
    GenericAssetpackError,
    validate_generic_assetpack_document,
)
from worldforge.generic_assets import (
    _OUTPUT_MEDIA,
    ASSET_INVENTORY_FORMAT,
    ASSET_SPEC_FORMAT,
    ASSET_STYLE_FORMAT,
    ASSET_SUBJECT_FORMAT,
    ASSET_TARGET_FORMAT,
    GenericAssetError,
    validate_asset_specification_document,
)
from worldforge.integrity import canonical_json_bytes

ASSET_QA_REVIEW_RECEIPT_FORMAT = "world-forge.asset_qa_review_receipt"
ASSET_RELEASE_AUTHORITY_FORMAT = "world-forge.asset_release_authority"
GENERIC_ASSET_AUTHORITY_VERSION = 1

MAX_ASSET_AUTHORITY_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_ASSET_AUTHORITY_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_ASSET_RELEASE_REVIEWS = 4096
MAX_ASSET_AUTHORITY_BLOCKERS = 64

_REVIEW_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "review_receipt_id",
        "asset",
        "lineage",
        "reviewed_output",
        "criteria",
        "status",
        "blockers",
        "authority",
        "content_hash",
    }
)
_RELEASE_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "release_authority_id",
        "candidate_manifest",
        "candidate_assetpack",
        "qa_reviews",
        "status",
        "blockers",
        "authority",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_ASSET_FIELDS = frozenset({"asset_id", "content_hash"})
_AUTHORITY_FIELDS = frozenset(
    {
        "workspace_id",
        "root_generation",
        "source_revision",
        "workflow_status_hash",
        "artifact_snapshot_hash",
        "producer_job_id",
        "producer_operation",
        "producer_output_position",
    }
)
_LINEAGE_FORMATS = {
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
    "recipe": ASSET_PROCESSING_RECIPE_FORMAT,
    "processing_receipt": ASSET_PROCESSING_RECEIPT_FORMAT,
    "qa_report": ASSET_QA_REPORT_FORMAT,
}
_LINEAGE_FIELDS = frozenset(_LINEAGE_FORMATS)
_OUTPUT_FIELDS = frozenset(
    {
        "candidate_artifact_id",
        "role",
        "media_type",
        "runtime_path",
        "locator",
        "sha256",
        "size_bytes",
    }
)
_CRITERION_FIELDS = frozenset({"criterion_index", "criterion_sha256", "decision"})
_REVIEW_IDENTITY_FORMAT = ASSET_QA_REVIEW_RECEIPT_FORMAT
_REVIEW_STATUSES = frozenset({"approved", "rejected"})
_RELEASE_STATUSES = frozenset({"authorized", "blocked"})
_DECISIONS = frozenset({"approved", "rejected"})
_REVIEW_OPERATION = "asset.qa.review"
_RELEASE_OPERATION = "asset.release.authorize"
_WORKSPACE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_ENTITY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


class GenericAssetAuthorityError(ValueError):
    """Raised when retained asset release authority cannot be proven."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise GenericAssetAuthorityError(reason_code, detail)


@dataclass(frozen=True, slots=True)
class RetainedAssetQaReviewRecord:
    """Trusted resolver snapshot of one retained QA-review job and its CAS inputs."""

    document_bytes: bytes
    document_blob_sha256: str
    document_size_bytes: int
    specification_bytes: bytes
    processing_receipt_bytes: bytes
    qa_report_bytes: bytes
    retained_output_bytes: bytes
    retained_output_sha256: str
    retained_output_size_bytes: int
    workspace_id: str
    root_generation: int
    source_revision: str
    workflow_status_hash: str | None
    artifact_snapshot_hash: str
    producer_job_id: str
    producer_operation: str
    producer_output_position: int


@dataclass(frozen=True, slots=True)
class RetainedAssetReleaseAuthorityRecord:
    """Trusted resolver snapshot of one retained release-authority job output."""

    document_bytes: bytes
    document_blob_sha256: str
    document_size_bytes: int
    workspace_id: str
    root_generation: int
    source_revision: str
    workflow_status_hash: str | None
    artifact_snapshot_hash: str
    producer_job_id: str
    producer_operation: str
    producer_output_position: int


@runtime_checkable
class AssetAuthorityResolver(Protocol):
    """Trusted main-process boundary for retained job and CAS resolution."""

    def resolve_asset_qa_review(
        self,
        *,
        review_receipt_id: str,
        content_hash: str,
    ) -> RetainedAssetQaReviewRecord: ...

    def resolve_asset_release_authority(
        self,
        *,
        release_authority_id: str,
        content_hash: str,
    ) -> RetainedAssetReleaseAuthorityRecord: ...


_VERIFIED_REVIEW_TOKEN = object()
_VERIFIED_RELEASE_TOKEN = object()


class VerifiedAssetQaReview:
    """Opaque result produced only after retained resolver-backed verification."""

    __slots__ = ("_approved", "_document", "_identity", "_proof")

    def __init__(
        self,
        token: object,
        document: Mapping[str, Any] | None = None,
    ) -> None:
        if token is not _VERIFIED_REVIEW_TOKEN or document is None:
            raise TypeError("VerifiedAssetQaReview is created only by verify_asset_qa_review")
        self._document = copy.deepcopy(dict(document))
        self._identity = MappingProxyType(_document_identity(self._document, "review_receipt_id"))
        self._approved = self._document["status"] == "approved"
        self._proof = token

    @property
    def approved(self) -> bool:
        return self._approved

    @property
    def document(self) -> dict[str, Any]:
        return copy.deepcopy(self._document)

    @property
    def identity(self) -> Mapping[str, object]:
        return self._identity


class VerifiedAssetReleaseAuthority:
    """Opaque retained decision for one exact manifest and assetpack candidate."""

    __slots__ = ("_authorized", "_document", "_identity", "_proof")

    def __init__(
        self,
        token: object,
        document: Mapping[str, Any] | None = None,
    ) -> None:
        if token is not _VERIFIED_RELEASE_TOKEN or document is None:
            raise TypeError(
                "VerifiedAssetReleaseAuthority is created only by verify_asset_release_authority"
            )
        self._document = copy.deepcopy(dict(document))
        self._identity = MappingProxyType(
            _document_identity(self._document, "release_authority_id")
        )
        self._authorized = self._document["status"] == "authorized"
        self._proof = token

    @property
    def authorized(self) -> bool:
        return self._authorized

    @property
    def document(self) -> dict[str, Any]:
        return copy.deepcopy(self._document)

    @property
    def identity(self) -> Mapping[str, object]:
        return self._identity


def _hash(document: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(document)
    except CreationContractError as exc:
        _fail("asset_authority_invalid", str(exc))


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
            "asset_authority_lineage_mismatch",
            f"{context}.format must be {expected_format}",
        )
    if identity.get("format_version") != 1:
        _fail(
            "asset_authority_lineage_mismatch",
            f"{context}.format_version must be 1",
        )
    _identifier(identity.get("id"), f"{context}.id")
    _sha256(identity.get("content_hash"), f"{context}.content_hash")
    return identity


def _validate_asset(value: object, context: str) -> dict[str, Any]:
    asset = _object(value, context)
    _exact_keys(asset, _ASSET_FIELDS, context)
    _identifier(asset.get("asset_id"), f"{context}.asset_id")
    _sha256(asset.get("content_hash"), f"{context}.content_hash")
    return asset


def _validate_authority(
    value: object,
    context: str,
    *,
    operation: str,
) -> dict[str, Any]:
    authority = _object(value, context)
    _exact_keys(authority, _AUTHORITY_FIELDS, context)
    workspace_id = authority.get("workspace_id")
    if not isinstance(workspace_id, str) or _WORKSPACE_ID_RE.fullmatch(workspace_id) is None:
        _fail(
            "asset_authority_binding_mismatch",
            f"{context}.workspace_id is outside the Studio workspace ID domain",
        )
    _integer(authority.get("root_generation"), f"{context}.root_generation")
    _sha256(authority.get("source_revision"), f"{context}.source_revision")
    workflow_status_hash = authority.get("workflow_status_hash")
    if workflow_status_hash is not None:
        _sha256(workflow_status_hash, f"{context}.workflow_status_hash")
    _sha256(
        authority.get("artifact_snapshot_hash"),
        f"{context}.artifact_snapshot_hash",
    )
    producer_job_id = authority.get("producer_job_id")
    if not isinstance(producer_job_id, str) or _ENTITY_ID_RE.fullmatch(producer_job_id) is None:
        _fail(
            "asset_authority_binding_mismatch",
            f"{context}.producer_job_id is outside the Studio entity ID domain",
        )
    if authority.get("producer_operation") != operation:
        _fail(
            "asset_authority_producer_mismatch",
            f"{context}.producer_operation must be {operation}",
        )
    position = _integer(
        authority.get("producer_output_position"),
        f"{context}.producer_output_position",
    )
    if position >= MAX_ASSET_RELEASE_REVIEWS:
        _fail(
            "asset_authority_producer_mismatch",
            f"{context}.producer_output_position exceeds its closed limit",
        )
    return authority


def _validate_blockers(
    value: object,
    context: str,
    *,
    allow_empty: bool,
) -> list[str]:
    blockers = _identifier_array(value, context, allow_empty=allow_empty)
    if len(blockers) > MAX_ASSET_AUTHORITY_BLOCKERS:
        _fail("asset_authority_limit", f"{context} exceeds 64 entries")
    return blockers


def _validate_review_output(value: object, context: str) -> dict[str, Any]:
    output = _object(value, context)
    _exact_keys(output, _OUTPUT_FIELDS, context)
    _identifier(
        output.get("candidate_artifact_id"),
        f"{context}.candidate_artifact_id",
    )
    role = _identifier(output.get("role"), f"{context}.role")
    media_type = output.get("media_type")
    if role not in _OUTPUT_MEDIA or media_type not in _OUTPUT_MEDIA[role]:
        _fail(
            "review_output_mismatch",
            f"{context} role/media_type combination is unsupported",
        )
    _portable_relative_path(output.get("runtime_path"), f"{context}.runtime_path")
    _portable_relative_path(output.get("locator"), f"{context}.locator")
    _sha256(output.get("sha256"), f"{context}.sha256")
    size = _integer(output.get("size_bytes"), f"{context}.size_bytes", minimum=1)
    if size > MAX_ASSET_AUTHORITY_OUTPUT_BYTES:
        _fail("asset_authority_limit", f"{context}.size_bytes exceeds its limit")
    return output


def _validate_criteria(value: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS:
        _fail(
            "review_criterion_coverage",
            f"{context} must contain 1..{MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS} entries",
        )
    criteria: list[dict[str, Any]] = []
    hashes: set[str] = set()
    for index, raw in enumerate(value):
        criterion = _object(raw, f"{context}/{index}")
        _exact_keys(criterion, _CRITERION_FIELDS, f"{context}/{index}")
        if criterion.get("criterion_index") != index:
            _fail(
                "review_criterion_coverage",
                f"{context}/{index}.criterion_index must equal its array position",
            )
        digest = _sha256(
            criterion.get("criterion_sha256"),
            f"{context}/{index}.criterion_sha256",
        )
        if digest in hashes:
            _fail(
                "review_criterion_coverage",
                f"{context} contains a duplicate criterion hash",
            )
        hashes.add(digest)
        if criterion.get("decision") not in _DECISIONS:
            _fail(
                "review_criterion_decision",
                f"{context}/{index}.decision is unsupported",
            )
        criteria.append(criterion)
    return criteria


def _validate_review_structure(value: object) -> dict[str, Any]:
    try:
        _validate_json_structure(value, context="asset QA review receipt")
        document = _object(value, "asset QA review receipt")
        _exact_keys(document, _REVIEW_FIELDS, "asset QA review receipt")
        if document.get("format") != ASSET_QA_REVIEW_RECEIPT_FORMAT:
            _fail(
                "review_format_invalid",
                f"format must be {ASSET_QA_REVIEW_RECEIPT_FORMAT}",
            )
        if document.get("format_version") != GENERIC_ASSET_AUTHORITY_VERSION:
            _fail("review_format_invalid", "format_version must be 1")
        _identifier(document.get("review_receipt_id"), "review_receipt_id")
        _validate_asset(document.get("asset"), "asset QA review receipt.asset")
        lineage = _object(document.get("lineage"), "asset QA review receipt.lineage")
        _exact_keys(lineage, _LINEAGE_FIELDS, "asset QA review receipt.lineage")
        for field, format_name in _LINEAGE_FORMATS.items():
            _validate_identity(
                lineage.get(field),
                f"asset QA review receipt.lineage.{field}",
                expected_format=format_name,
            )
        _validate_review_output(
            document.get("reviewed_output"),
            "asset QA review receipt.reviewed_output",
        )
        criteria = _validate_criteria(
            document.get("criteria"),
            "asset QA review receipt.criteria",
        )
        status = document.get("status")
        if status not in _REVIEW_STATUSES:
            _fail("review_status", "asset QA review receipt status is unsupported")
        blockers = _validate_blockers(
            document.get("blockers"),
            "asset QA review receipt.blockers",
            allow_empty=True,
        )
        all_approved = all(item["decision"] == "approved" for item in criteria)
        if status == "approved" and (not all_approved or blockers):
            _fail(
                "review_status",
                "approved review requires all decisions approved and no blockers",
            )
        if status == "rejected" and (all_approved or not blockers):
            _fail(
                "review_blockers",
                "rejected review requires a rejected decision and blockers",
            )
        _validate_authority(
            document.get("authority"),
            "asset QA review receipt.authority",
            operation=_REVIEW_OPERATION,
        )
        _sha256(document.get("content_hash"), "asset QA review receipt.content_hash")
        if document["content_hash"] != _hash(document):
            _fail("review_content_hash_mismatch", "content_hash is not canonical")
        return copy.deepcopy(document)
    except GenericAssetAuthorityError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("review_invalid", str(exc))


def _validate_review_identity_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ASSET_RELEASE_REVIEWS:
        _fail(
            "release_review_coverage",
            "qa_reviews must contain 1..4096 identities",
        )
    identities = [
        _validate_identity(
            item,
            f"asset release authority.qa_reviews/{index}",
            expected_format=_REVIEW_IDENTITY_FORMAT,
        )
        for index, item in enumerate(value)
    ]
    ids = [str(item["id"]) for item in identities]
    hashes = [str(item["content_hash"]) for item in identities]
    if ids != sorted(ids, key=lambda item: item.encode("utf-8")):
        _fail("release_review_coverage", "qa_reviews must be UTF-8 ID sorted")
    if len(set(ids)) != len(ids) or len(set(hashes)) != len(hashes):
        _fail("release_review_coverage", "qa_reviews contain duplicate identities")
    return identities


def _validate_release_structure(value: object) -> dict[str, Any]:
    try:
        _validate_json_structure(value, context="asset release authority")
        document = _object(value, "asset release authority")
        _exact_keys(document, _RELEASE_FIELDS, "asset release authority")
        if document.get("format") != ASSET_RELEASE_AUTHORITY_FORMAT:
            _fail(
                "release_format_invalid",
                f"format must be {ASSET_RELEASE_AUTHORITY_FORMAT}",
            )
        if document.get("format_version") != GENERIC_ASSET_AUTHORITY_VERSION:
            _fail("release_format_invalid", "format_version must be 1")
        _identifier(document.get("release_authority_id"), "release_authority_id")
        _validate_identity(
            document.get("candidate_manifest"),
            "asset release authority.candidate_manifest",
            expected_format=ASSET_MANIFEST_FORMAT,
        )
        _validate_identity(
            document.get("candidate_assetpack"),
            "asset release authority.candidate_assetpack",
            expected_format=GENERIC_ASSETPACK_FORMAT,
        )
        _validate_review_identity_list(document.get("qa_reviews"))
        status = document.get("status")
        if status not in _RELEASE_STATUSES:
            _fail("release_status", "asset release authority status is unsupported")
        blockers = _validate_blockers(
            document.get("blockers"),
            "asset release authority.blockers",
            allow_empty=True,
        )
        if status == "authorized" and blockers:
            _fail("release_status", "authorized release cannot contain blockers")
        if status == "blocked" and not blockers:
            _fail("release_blockers", "blocked release requires blockers")
        _validate_authority(
            document.get("authority"),
            "asset release authority.authority",
            operation=_RELEASE_OPERATION,
        )
        _sha256(document.get("content_hash"), "asset release authority.content_hash")
        if document["content_hash"] != _hash(document):
            _fail("release_content_hash_mismatch", "content_hash is not canonical")
        return copy.deepcopy(document)
    except GenericAssetAuthorityError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("release_invalid", str(exc))


def _identity_from_document(
    document: Mapping[str, object],
    *,
    id_field: str,
) -> dict[str, object]:
    return _document_identity(document, id_field)


def _review_lineage(
    qa_report: Mapping[str, object],
) -> dict[str, object]:
    return {
        **{
            field: copy.deepcopy(qa_report[field])
            for field in _LINEAGE_FORMATS
            if field != "qa_report"
        },
        "qa_report": _identity_from_document(qa_report, id_field="qa_report_id"),
    }


def _criterion_text_hashes(specification: Mapping[str, object]) -> list[str]:
    raw = specification.get("acceptance_criteria")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS:
        _fail("review_criterion_coverage", "specification criteria are invalid")
    hashes: list[str] = []
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            _fail(
                "review_criterion_coverage",
                f"specification criterion {index} is not text",
            )
        try:
            payload = value.encode("utf-8")
        except UnicodeError as exc:
            _fail("review_criterion_coverage", str(exc))
        hashes.append(hashlib.sha256(payload).hexdigest())
    return hashes


def _source_output(
    qa_report: Mapping[str, object],
    processing_receipt: Mapping[str, object],
    *,
    role: str,
) -> dict[str, object]:
    def by_role(
        document: Mapping[str, object],
        context: str,
    ) -> dict[str, Mapping[str, object]]:
        values = document.get("outputs")
        if not isinstance(values, list):
            _fail("review_output_mismatch", f"{context}.outputs must be an array")
        indexed: dict[str, Mapping[str, object]] = {}
        for value in values:
            if not isinstance(value, Mapping) or not isinstance(value.get("role"), str):
                _fail("review_output_mismatch", f"{context}.outputs is invalid")
            output_role = str(value["role"])
            if output_role in indexed:
                _fail("review_output_mismatch", f"{context}.outputs has duplicate roles")
            indexed[output_role] = value
        return indexed

    qa_outputs = by_role(qa_report, "QA report")
    processing_outputs = by_role(processing_receipt, "processing receipt")
    if set(qa_outputs) != set(processing_outputs) or role not in qa_outputs:
        _fail("review_output_mismatch", "reviewed output role coverage differs")
    qa_output = qa_outputs[role]
    processing_output = processing_outputs[role]
    for field in _OUTPUT_FIELDS:
        if qa_output.get(field) != processing_output.get(field):
            _fail(
                "review_output_mismatch",
                f"QA and processing output {field} differ",
            )
    return {field: copy.deepcopy(qa_output[field]) for field in _OUTPUT_FIELDS}


def _validate_review_sources(
    review: Mapping[str, object],
    *,
    specification: object,
    processing_receipt: object,
    qa_report: object,
    retained_output: bytes,
) -> None:
    try:
        checked_spec = validate_asset_specification_document(specification)
        checked_receipt = validate_asset_processing_receipt_document(processing_receipt)
        checked_qa = validate_asset_qa_report_document(qa_report)
    except (GenericAssetError, GenericAssetProcessingError) as exc:
        _fail("review_source_invalid", str(exc))

    expected_specification = _identity_from_document(checked_spec, id_field="spec_id")
    expected_receipt = _identity_from_document(
        checked_receipt,
        id_field="processing_receipt_id",
    )
    if (
        checked_qa["specification"] != expected_specification
        or checked_qa["processing_receipt"] != expected_receipt
        or checked_receipt["specification"] != expected_specification
    ):
        _fail("review_lineage_mismatch", "source documents cross different lineages")
    expected_lineage = _review_lineage(checked_qa)
    if review["lineage"] != expected_lineage:
        _fail("review_lineage_mismatch", "review lineage differs from retained QA")
    if review["asset"] != checked_qa["asset"] or checked_qa["asset"] != checked_spec["asset"]:
        _fail("review_lineage_mismatch", "review asset differs from retained lineage")
    for field in (
        "gamepack",
        "asset_subject",
        "target",
        "style",
        "inventory",
        "asset",
        "request",
        "receipt",
        "selection",
        "provenance",
        "recipe",
    ):
        if checked_qa[field] != checked_receipt[field]:
            _fail(
                "review_lineage_mismatch",
                f"QA and processing receipt {field} differ",
            )

    expected_hashes = _criterion_text_hashes(checked_spec)
    qa_criteria = checked_qa["acceptance_criteria"]
    review_criteria = review["criteria"]
    if len(qa_criteria) != len(expected_hashes) or len(review_criteria) != len(expected_hashes):
        _fail("review_criterion_coverage", "criterion coverage differs from specification")
    for index, expected_hash in enumerate(expected_hashes):
        qa_criterion = qa_criteria[index]
        review_criterion = review_criteria[index]
        if (
            qa_criterion["criterion_index"] != index
            or qa_criterion["criterion_sha256"] != expected_hash
            or review_criterion["criterion_index"] != index
            or review_criterion["criterion_sha256"] != expected_hash
        ):
            _fail(
                "review_criterion_mismatch",
                f"criterion {index} does not bind exact source text",
            )
        if qa_criterion["status"] == "failed" and review_criterion["decision"] == "approved":
            _fail(
                "review_criterion_mismatch",
                f"criterion {index} cannot approve failed QA",
            )

    expected_output = _source_output(
        checked_qa,
        checked_receipt,
        role=str(review["reviewed_output"]["role"]),
    )
    if review["reviewed_output"] != expected_output:
        _fail("review_output_mismatch", "reviewed output differs from retained lineage")
    if type(retained_output) is not bytes:
        _fail("review_output_mismatch", "retained output must be exact bytes")
    if (
        len(retained_output) != expected_output["size_bytes"]
        or hashlib.sha256(retained_output).hexdigest() != expected_output["sha256"]
    ):
        _fail("review_output_mismatch", "retained output digest or size differs")

    if review["status"] == "approved":
        if checked_qa["status"] != "passed":
            _fail("review_status", "approved review requires passed retained QA")
        for output in checked_qa["outputs"]:
            if any(check["status"] == "failed" for check in output["checks"]):
                _fail("review_status", "approved review contains a failed QA check")


def build_asset_qa_review_receipt(
    qa_report: object,
    specification: object,
    processing_receipt: object,
    *,
    review_receipt_id: str,
    output_role: str,
    decisions: Sequence[str],
    blockers: Sequence[str],
    authority: Mapping[str, object],
    retained_output: bytes,
) -> dict[str, Any]:
    """Build a structural review receipt; authority still requires live resolution."""

    try:
        checked_spec = validate_asset_specification_document(specification)
        checked_receipt = validate_asset_processing_receipt_document(processing_receipt)
        checked_qa = validate_asset_qa_report_document(qa_report)
        checked_id = _identifier(review_receipt_id, "review_receipt_id")
        checked_role = _identifier(output_role, "output_role")
        if isinstance(decisions, (str, bytes)):
            _fail("review_criterion_coverage", "decisions must be an array")
        decision_values = list(decisions)
        criterion_hashes = _criterion_text_hashes(checked_spec)
        if len(decision_values) != len(criterion_hashes):
            _fail(
                "review_criterion_coverage",
                "decisions must exactly cover specification criteria",
            )
        for index, decision in enumerate(decision_values):
            if decision not in _DECISIONS:
                _fail(
                    "review_criterion_decision",
                    f"decision {index} is unsupported",
                )
        blocker_values = list(blockers)
        status = (
            "approved"
            if all(decision == "approved" for decision in decision_values)
            else "rejected"
        )
        if status == "approved" and blocker_values:
            _fail("review_blockers", "approved review cannot contain blockers")
        if status == "rejected" and not blocker_values:
            _fail("review_blockers", "rejected review requires blockers")
        document: dict[str, Any] = {
            "format": ASSET_QA_REVIEW_RECEIPT_FORMAT,
            "format_version": GENERIC_ASSET_AUTHORITY_VERSION,
            "review_receipt_id": checked_id,
            "asset": copy.deepcopy(checked_qa["asset"]),
            "lineage": _review_lineage(checked_qa),
            "reviewed_output": _source_output(
                checked_qa,
                checked_receipt,
                role=checked_role,
            ),
            "criteria": [
                {
                    "criterion_index": index,
                    "criterion_sha256": digest,
                    "decision": decision_values[index],
                }
                for index, digest in enumerate(criterion_hashes)
            ],
            "status": status,
            "blockers": sorted(blocker_values, key=lambda item: item.encode("utf-8")),
            "authority": copy.deepcopy(dict(authority)),
            "content_hash": "",
        }
        _seal(document)
        checked = validate_asset_qa_review_receipt_document(document)
        _validate_review_sources(
            checked,
            specification=checked_spec,
            processing_receipt=checked_receipt,
            qa_report=checked_qa,
            retained_output=retained_output,
        )
        return checked
    except GenericAssetAuthorityError:
        raise
    except (
        CreationContractError,
        GenericAssetError,
        GenericAssetProcessingError,
        TypeError,
        ValueError,
    ) as exc:
        _fail("review_invalid", str(exc))


def validate_asset_qa_review_receipt_document(value: object) -> dict[str, Any]:
    return _validate_review_structure(value)


def serialize_asset_qa_review_receipt(value: object) -> bytes:
    return canonical_json_bytes(validate_asset_qa_review_receipt_document(value))


def _load_canonical(path: str | Path, validator: Any) -> dict[str, Any]:
    source = Path(os.path.abspath(os.fspath(path)))
    try:
        document = validator(read_creation_object(source))
        expected = canonical_json_bytes(document)
        actual = read_verified_artifact_bytes(
            source.parent,
            _portable_relative_path(source.name, "asset authority path"),
            expected_sha256=hashlib.sha256(expected).hexdigest(),
            expected_size_bytes=len(expected),
            limit=MAX_ASSET_AUTHORITY_DOCUMENT_BYTES,
        )
        if actual != expected:
            _fail("asset_authority_noncanonical", "document bytes are not canonical")
        return document
    except GenericAssetAuthorityError:
        raise
    except (
        CreationContractError,
        GenericAssetProductionError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        _fail("asset_authority_load_failed", str(exc))


def load_asset_qa_review_receipt(path: str | Path) -> dict[str, Any]:
    return _load_canonical(path, validate_asset_qa_review_receipt_document)


def _decode_retained_document(
    payload: object,
    *,
    context: str,
    validator: Any,
) -> dict[str, Any]:
    if type(payload) is not bytes or len(payload) > MAX_ASSET_AUTHORITY_DOCUMENT_BYTES:
        _fail("authority_resolver_invalid", f"{context} bytes are invalid")
    try:
        document = decode_json_object(payload, source=context)
    except RuntimeIOError as exc:
        _fail("authority_resolver_invalid", str(exc))
    if canonical_json_bytes(document) != payload:
        _fail("authority_resolver_invalid", f"{context} bytes are not canonical")
    try:
        return validator(document)
    except (GenericAssetError, GenericAssetProcessingError) as exc:
        _fail("authority_resolver_invalid", str(exc))


def _require_resolver(value: object) -> AssetAuthorityResolver:
    if not isinstance(value, AssetAuthorityResolver):
        _fail(
            "authority_resolver_invalid",
            "resolver must implement the trusted asset authority protocol",
        )
    return value


def _record_binding(record: object) -> dict[str, object]:
    return {
        "workspace_id": record.workspace_id,
        "root_generation": record.root_generation,
        "source_revision": record.source_revision,
        "workflow_status_hash": record.workflow_status_hash,
        "artifact_snapshot_hash": record.artifact_snapshot_hash,
        "producer_job_id": record.producer_job_id,
        "producer_operation": record.producer_operation,
        "producer_output_position": record.producer_output_position,
    }


def _verify_record_document(
    document: Mapping[str, object],
    record: object,
    *,
    expected_type: type,
    operation: str,
) -> None:
    if type(record) is not expected_type:
        _fail(
            "authority_resolver_invalid",
            "resolver returned a non-retained authority record",
        )
    payload = record.document_bytes
    if type(payload) is not bytes or len(payload) > MAX_ASSET_AUTHORITY_DOCUMENT_BYTES:
        _fail("authority_resolver_invalid", "retained document bytes are invalid")
    if (
        type(record.document_size_bytes) is not int
        or record.document_size_bytes != len(payload)
        or record.document_blob_sha256 != hashlib.sha256(payload).hexdigest()
        or payload != canonical_json_bytes(document)
    ):
        _fail(
            "asset_authority_cas_mismatch",
            "retained document CAS digest, size, or bytes differ",
        )
    _sha256(record.document_blob_sha256, "retained document blob SHA-256")
    checked_binding = _validate_authority(
        _record_binding(record),
        "retained authority binding",
        operation=operation,
    )
    if checked_binding != document["authority"]:
        _fail(
            "asset_authority_binding_mismatch",
            "retained workspace, source, producer, or output position differs",
        )


def verify_asset_qa_review(
    value: object,
    *,
    resolver: AssetAuthorityResolver,
) -> VerifiedAssetQaReview:
    """Resolve one raw review through trusted retained job and CAS state."""

    review = validate_asset_qa_review_receipt_document(value)
    checked_resolver = _require_resolver(resolver)
    try:
        record = checked_resolver.resolve_asset_qa_review(
            review_receipt_id=review["review_receipt_id"],
            content_hash=review["content_hash"],
        )
    except GenericAssetAuthorityError:
        raise
    except Exception as exc:
        _fail("authority_resolver_failed", str(exc))
    _verify_record_document(
        review,
        record,
        expected_type=RetainedAssetQaReviewRecord,
        operation=_REVIEW_OPERATION,
    )
    if (
        type(record.retained_output_bytes) is not bytes
        or type(record.retained_output_size_bytes) is not int
        or record.retained_output_size_bytes != len(record.retained_output_bytes)
        or record.retained_output_sha256 != hashlib.sha256(record.retained_output_bytes).hexdigest()
    ):
        _fail(
            "asset_authority_cas_mismatch",
            "retained output CAS digest, size, or bytes differ",
        )
    _sha256(record.retained_output_sha256, "retained output SHA-256")
    specification = _decode_retained_document(
        record.specification_bytes,
        context="retained asset specification",
        validator=validate_asset_specification_document,
    )
    processing_receipt = _decode_retained_document(
        record.processing_receipt_bytes,
        context="retained asset processing receipt",
        validator=validate_asset_processing_receipt_document,
    )
    qa_report = _decode_retained_document(
        record.qa_report_bytes,
        context="retained asset QA report",
        validator=validate_asset_qa_report_document,
    )
    _validate_review_sources(
        review,
        specification=specification,
        processing_receipt=processing_receipt,
        qa_report=qa_report,
        retained_output=record.retained_output_bytes,
    )
    return VerifiedAssetQaReview(_VERIFIED_REVIEW_TOKEN, review)


def _candidate_identity(
    document: Mapping[str, object],
    *,
    id_field: str,
) -> dict[str, object]:
    return _document_identity(document, id_field)


def _candidate_output_key(
    asset_id: object,
    output: Mapping[str, object],
) -> tuple[str, str]:
    return (str(asset_id), str(output["role"]))


def _verified_review_documents(
    reviews: Sequence[VerifiedAssetQaReview],
) -> list[dict[str, Any]]:
    if (
        isinstance(reviews, (str, bytes, bytearray))
        or not isinstance(reviews, Sequence)
        or not 1 <= len(reviews) <= MAX_ASSET_RELEASE_REVIEWS
    ):
        _fail(
            "release_review_coverage",
            "release reviews must contain 1..4096 verified handles",
        )
    documents: list[dict[str, Any]] = []
    for review in reviews:
        if type(review) is not VerifiedAssetQaReview or review._proof is not _VERIFIED_REVIEW_TOKEN:
            _fail(
                "release_review_coverage",
                "release reviews must be exact verified QA handles",
            )
        documents.append(review.document)
    return documents


def _validate_manifest_review_coverage(
    manifest: Mapping[str, object],
    review_documents: Sequence[Mapping[str, object]],
    *,
    require_approved: bool,
) -> None:
    review_by_key: dict[tuple[str, str], Mapping[str, object]] = {}
    for review in review_documents:
        key = _candidate_output_key(review["asset"]["asset_id"], review["reviewed_output"])
        if key in review_by_key:
            _fail("release_review_coverage", "release reviews duplicate an asset output")
        review_by_key[key] = review

    expected_keys: set[tuple[str, str]] = set()
    for manifest_asset in manifest["assets"]:
        asset_id = manifest_asset["asset"]["asset_id"]
        expected_lineage = {
            "gamepack": manifest["gamepack"],
            "asset_subject": manifest["asset_subject"],
            "target": manifest["target"],
            "style": manifest["style"],
            "inventory": manifest["inventory"],
            "specification": manifest_asset["specification"],
            "request": manifest_asset["request"],
            "receipt": manifest_asset["receipt"],
            "selection": manifest_asset["selection"],
            "provenance": manifest_asset["provenance"],
            "recipe": manifest_asset["processing_recipe"],
            "processing_receipt": manifest_asset["processing_receipt"],
            "qa_report": manifest_asset["qa_report"],
        }
        for manifest_output in manifest_asset["outputs"]:
            key = _candidate_output_key(asset_id, manifest_output)
            expected_keys.add(key)
            review = review_by_key.get(key)
            if review is None:
                _fail("release_review_coverage", f"missing review for {key!r}")
            if require_approved and review["status"] != "approved":
                _fail("release_review_coverage", f"review for {key!r} is not approved")
            if review["asset"] != manifest_asset["asset"] or review["lineage"] != expected_lineage:
                _fail("release_review_coverage", "review lineage differs from candidate")
            reviewed_output = review["reviewed_output"]
            for field in (
                "role",
                "media_type",
                "runtime_path",
                "locator",
                "sha256",
                "size_bytes",
            ):
                if reviewed_output[field] != manifest_output[field]:
                    _fail(
                        "release_review_coverage",
                        f"reviewed output {field} differs from candidate",
                    )
    if set(review_by_key) != expected_keys:
        _fail("release_review_coverage", "release reviews contain extra asset outputs")


def require_verified_asset_qa_reviews(
    manifest: object,
    reviews: Sequence[VerifiedAssetQaReview],
) -> tuple[VerifiedAssetQaReview, ...]:
    """Require complete retained QA authority for one exact v1 candidate manifest."""

    try:
        checked_manifest = validate_asset_manifest_document(manifest)
    except GenericAssetProcessingError as exc:
        _fail("release_candidate_invalid", str(exc))
    if checked_manifest["state"] != "release_ready":
        _fail("release_candidate_mismatch", "candidate manifest is not release_ready")
    review_documents = _verified_review_documents(reviews)
    checked_reviews = tuple(reviews)
    _validate_manifest_review_coverage(
        checked_manifest,
        review_documents,
        require_approved=False,
    )
    return checked_reviews


def _validate_release_candidates(
    manifest: object,
    assetpack: object,
    reviews: Sequence[VerifiedAssetQaReview],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    try:
        checked_manifest = validate_asset_manifest_document(manifest)
        checked_assetpack = validate_generic_assetpack_document(assetpack)
    except (GenericAssetProcessingError, GenericAssetpackError) as exc:
        _fail("release_candidate_invalid", str(exc))
    if checked_manifest["state"] != "release_ready":
        _fail("release_candidate_mismatch", "candidate manifest is not release_ready")
    expected_manifest_identity = _candidate_identity(
        checked_manifest,
        id_field="manifest_id",
    )
    if checked_assetpack["release_ready_manifest"] != expected_manifest_identity:
        _fail("release_candidate_mismatch", "assetpack references another manifest")
    for field in ("gamepack", "asset_subject", "target", "style"):
        if checked_assetpack[field] != checked_manifest[field]:
            _fail(
                "release_candidate_mismatch",
                f"assetpack and manifest {field} differ",
            )
    if checked_assetpack["asset_inventory"] != checked_manifest["inventory"]:
        _fail("release_candidate_mismatch", "assetpack inventory differs from manifest")

    review_documents = _verified_review_documents(reviews)
    _validate_manifest_review_coverage(
        checked_manifest,
        review_documents,
        require_approved=False,
    )

    manifest_assets = checked_manifest["assets"]
    assetpack_assets = checked_assetpack["assets"]
    if len(manifest_assets) != len(assetpack_assets):
        _fail("release_candidate_mismatch", "candidate asset counts differ")
    for manifest_asset, assetpack_asset in zip(
        manifest_assets,
        assetpack_assets,
        strict=True,
    ):
        if manifest_asset["asset"] != assetpack_asset["asset"]:
            _fail("release_candidate_mismatch", "candidate asset identities differ")
        for field in (
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
            if manifest_asset[field] != assetpack_asset[field]:
                _fail(
                    "release_candidate_mismatch",
                    f"candidate asset {field} differs",
                )
        manifest_outputs = manifest_asset["outputs"]
        assetpack_outputs = assetpack_asset["outputs"]
        if len(manifest_outputs) != len(assetpack_outputs):
            _fail("release_candidate_mismatch", "candidate output counts differ")
        for manifest_output, assetpack_output in zip(
            manifest_outputs,
            assetpack_outputs,
            strict=True,
        ):
            for field in ("role", "media_type", "runtime_path", "sha256", "size_bytes"):
                if manifest_output[field] != assetpack_output[field]:
                    _fail(
                        "release_candidate_mismatch",
                        f"candidate output {field} differs",
                    )
    return checked_manifest, checked_assetpack, review_documents


def require_verified_asset_release_authority(
    authority: object,
    *,
    manifest: object,
    assetpack: object,
    reviews: Sequence[VerifiedAssetQaReview],
) -> VerifiedAssetReleaseAuthority:
    """Require one exact authorized retained decision for the supplied candidates."""

    if (
        type(authority) is not VerifiedAssetReleaseAuthority
        or authority._proof is not _VERIFIED_RELEASE_TOKEN
    ):
        _fail(
            "release_authority_required",
            "release authority must be an exact verified handle",
        )
    checked_manifest, checked_assetpack, review_documents = _validate_release_candidates(
        manifest,
        assetpack,
        reviews,
    )
    release = authority.document
    if not authority.authorized or release["status"] != "authorized":
        _fail("release_status", "assetpack sealing requires authorized release authority")
    expected_reviews = sorted(
        [_candidate_identity(review, id_field="review_receipt_id") for review in review_documents],
        key=lambda item: str(item["id"]).encode("utf-8"),
    )
    if release["candidate_manifest"] != _candidate_identity(
        checked_manifest, id_field="manifest_id"
    ) or release["candidate_assetpack"] != _candidate_identity(
        checked_assetpack, id_field="assetpack_id"
    ):
        _fail("release_candidate_mismatch", "release candidate identities differ")
    if release["qa_reviews"] != expected_reviews:
        _fail("release_review_coverage", "release review identities are incomplete or extra")
    return authority


def build_asset_release_authority(
    manifest: object,
    assetpack: object,
    reviews: Sequence[VerifiedAssetQaReview],
    *,
    release_authority_id: str,
    blockers: Sequence[str],
    authority: Mapping[str, object],
) -> dict[str, Any]:
    """Build a companion release decision from already verified QA handles."""

    if isinstance(reviews, (str, bytes)):
        _fail("release_review_coverage", "reviews must be an array")
    checked_manifest, checked_assetpack, review_documents = _validate_release_candidates(
        manifest,
        assetpack,
        list(reviews),
    )
    blocker_values = derive_asset_release_blockers(reviews, blockers)
    all_approved = all(review["status"] == "approved" for review in review_documents)
    status = "authorized" if all_approved and not blocker_values else "blocked"
    review_identities = sorted(
        [_candidate_identity(review, id_field="review_receipt_id") for review in review_documents],
        key=lambda item: str(item["id"]).encode("utf-8"),
    )
    document: dict[str, Any] = {
        "format": ASSET_RELEASE_AUTHORITY_FORMAT,
        "format_version": GENERIC_ASSET_AUTHORITY_VERSION,
        "release_authority_id": _identifier(
            release_authority_id,
            "release_authority_id",
        ),
        "candidate_manifest": _candidate_identity(
            checked_manifest,
            id_field="manifest_id",
        ),
        "candidate_assetpack": _candidate_identity(
            checked_assetpack,
            id_field="assetpack_id",
        ),
        "qa_reviews": review_identities,
        "status": status,
        "blockers": sorted(blocker_values, key=lambda item: item.encode("utf-8")),
        "authority": copy.deepcopy(dict(authority)),
        "content_hash": "",
    }
    _seal(document)
    return validate_asset_release_authority_document(document)


def derive_asset_release_blockers(
    reviews: Sequence[VerifiedAssetQaReview],
    explicit_blockers: Sequence[str],
) -> list[str]:
    """Derive the canonical release blocker union from verified retained reviews."""

    review_documents = _verified_review_documents(reviews)
    if isinstance(explicit_blockers, (str, bytes, bytearray)):
        _fail("release_blockers", "asset release authority blockers must be an array")
    try:
        checked_explicit = _validate_blockers(
            list(explicit_blockers),
            "asset release authority blockers",
            allow_empty=True,
        )
    except (CreationContractError, TypeError) as exc:
        _fail("release_blockers", str(exc))
    rejected_review_blockers = {
        blocker
        for review in review_documents
        if review["status"] == "rejected"
        for blocker in review["blockers"]
    }
    derived = sorted(
        {*checked_explicit, *rejected_review_blockers},
        key=lambda item: item.encode("utf-8"),
    )
    _validate_blockers(
        derived,
        "asset release authority blockers",
        allow_empty=True,
    )
    return derived


def validate_asset_release_authority_document(value: object) -> dict[str, Any]:
    return _validate_release_structure(value)


def serialize_asset_release_authority(value: object) -> bytes:
    return canonical_json_bytes(validate_asset_release_authority_document(value))


def load_asset_release_authority(path: str | Path) -> dict[str, Any]:
    return _load_canonical(path, validate_asset_release_authority_document)


def verify_asset_release_authority(
    value: object,
    *,
    manifest: object,
    assetpack: object,
    reviews: Sequence[VerifiedAssetQaReview],
    resolver: AssetAuthorityResolver,
) -> VerifiedAssetReleaseAuthority:
    """Verify one exact release decision against retained authority and candidates."""

    release = validate_asset_release_authority_document(value)
    if isinstance(reviews, (str, bytes)):
        _fail("release_review_coverage", "reviews must be an array")
    checked_manifest, checked_assetpack, review_documents = _validate_release_candidates(
        manifest,
        assetpack,
        list(reviews),
    )
    if release["candidate_manifest"] != _candidate_identity(
        checked_manifest,
        id_field="manifest_id",
    ) or release["candidate_assetpack"] != _candidate_identity(
        checked_assetpack,
        id_field="assetpack_id",
    ):
        _fail("release_candidate_mismatch", "release candidate identities differ")
    expected_reviews = sorted(
        [_candidate_identity(review, id_field="review_receipt_id") for review in review_documents],
        key=lambda item: str(item["id"]).encode("utf-8"),
    )
    if release["qa_reviews"] != expected_reviews:
        _fail("release_review_coverage", "release review identities are incomplete or extra")
    all_approved = all(review["status"] == "approved" for review in review_documents)
    if release["status"] == "authorized" and not all_approved:
        _fail("release_status", "authorized release contains a rejected review")
    if release["status"] == "blocked" and all_approved and not release["blockers"]:
        _fail("release_blockers", "blocked release has no blocker")

    checked_resolver = _require_resolver(resolver)
    try:
        record = checked_resolver.resolve_asset_release_authority(
            release_authority_id=release["release_authority_id"],
            content_hash=release["content_hash"],
        )
    except GenericAssetAuthorityError:
        raise
    except Exception as exc:
        _fail("authority_resolver_failed", str(exc))
    _verify_record_document(
        release,
        record,
        expected_type=RetainedAssetReleaseAuthorityRecord,
        operation=_RELEASE_OPERATION,
    )
    return VerifiedAssetReleaseAuthority(_VERIFIED_RELEASE_TOKEN, release)


__all__ = [
    "ASSET_QA_REVIEW_RECEIPT_FORMAT",
    "ASSET_RELEASE_AUTHORITY_FORMAT",
    "AssetAuthorityResolver",
    "GENERIC_ASSET_AUTHORITY_VERSION",
    "GenericAssetAuthorityError",
    "RetainedAssetQaReviewRecord",
    "RetainedAssetReleaseAuthorityRecord",
    "VerifiedAssetQaReview",
    "VerifiedAssetReleaseAuthority",
    "build_asset_qa_review_receipt",
    "build_asset_release_authority",
    "derive_asset_release_blockers",
    "load_asset_qa_review_receipt",
    "load_asset_release_authority",
    "require_verified_asset_qa_reviews",
    "require_verified_asset_release_authority",
    "serialize_asset_qa_review_receipt",
    "serialize_asset_release_authority",
    "validate_asset_qa_review_receipt_document",
    "validate_asset_release_authority_document",
    "verify_asset_qa_review",
    "verify_asset_release_authority",
]
