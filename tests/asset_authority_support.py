from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from worldforge.creation_contracts import canonical_creation_hash
from worldforge.generic_asset_authority import (
    GenericAssetAuthorityError,
    RetainedAssetQaReviewRecord,
    RetainedAssetReleaseAuthorityRecord,
    VerifiedAssetQaReview,
    VerifiedAssetReleaseAuthority,
    build_asset_qa_review_receipt,
    build_asset_release_authority,
    serialize_asset_qa_review_receipt,
    serialize_asset_release_authority,
    verify_asset_qa_review,
    verify_asset_release_authority,
)
from worldforge.integrity import canonical_json_bytes


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identity(document: Mapping[str, object], id_field: str) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


class TestAssetAuthorityResolver:
    """Test-only retained-record resolver; production never imports this module."""

    def __init__(self) -> None:
        self.reviews: dict[tuple[str, str], RetainedAssetQaReviewRecord] = {}
        self.releases: dict[tuple[str, str], RetainedAssetReleaseAuthorityRecord] = {}

    def resolve_asset_qa_review(
        self,
        *,
        review_receipt_id: str,
        content_hash: str,
    ) -> RetainedAssetQaReviewRecord:
        try:
            return self.reviews[(review_receipt_id, content_hash)]
        except KeyError as exc:
            raise GenericAssetAuthorityError(
                "authority_resolver_failed",
                "test review authority is not retained",
            ) from exc

    def resolve_asset_release_authority(
        self,
        *,
        release_authority_id: str,
        content_hash: str,
    ) -> RetainedAssetReleaseAuthorityRecord:
        try:
            return self.releases[(release_authority_id, content_hash)]
        except KeyError as exc:
            raise GenericAssetAuthorityError(
                "authority_resolver_failed",
                "test release authority is not retained",
            ) from exc


def build_test_verified_reviews(
    chain: Mapping[str, Any],
    artifact_root: Path,
    *,
    id_prefix: str,
) -> tuple[list[dict[str, Any]], list[VerifiedAssetQaReview], TestAssetAuthorityResolver]:
    specification = chain["specification"]
    processing_receipt = chain["processing_receipt"]
    qa_report = chain["qa_report"]
    criteria = specification["acceptance_criteria"]
    resolver = TestAssetAuthorityResolver()
    documents: list[dict[str, Any]] = []
    handles: list[VerifiedAssetQaReview] = []
    for position, output in enumerate(qa_report["outputs"]):
        retained_output = (artifact_root / output["locator"]).read_bytes()
        snapshot_hash = canonical_creation_hash(
            {
                "specification": _identity(specification, "spec_id"),
                "processing_receipt": _identity(
                    processing_receipt,
                    "processing_receipt_id",
                ),
                "qa_report": _identity(qa_report, "qa_report_id"),
                "output": {
                    "role": output["role"],
                    "sha256": output["sha256"],
                    "size_bytes": output["size_bytes"],
                },
            }
        )
        binding = {
            "workspace_id": "workspace-test-assets",
            "root_generation": 1,
            "source_revision": chain["gamepack"]["content_hash"],
            "workflow_status_hash": None,
            "artifact_snapshot_hash": snapshot_hash,
            "producer_job_id": f"job-{id_prefix}-{position}",
            "producer_operation": "asset.qa.review",
            "producer_output_position": position,
        }
        document = build_asset_qa_review_receipt(
            qa_report,
            specification,
            processing_receipt,
            review_receipt_id=f"{id_prefix}_{output['role']}_review",
            output_role=output["role"],
            decisions=["approved"] * len(criteria),
            blockers=[],
            authority=binding,
            retained_output=retained_output,
        )
        payload = serialize_asset_qa_review_receipt(document)
        resolver.reviews[(document["review_receipt_id"], document["content_hash"])] = (
            RetainedAssetQaReviewRecord(
                document_bytes=payload,
                document_blob_sha256=_sha256(payload),
                document_size_bytes=len(payload),
                specification_bytes=canonical_json_bytes(specification),
                processing_receipt_bytes=canonical_json_bytes(processing_receipt),
                qa_report_bytes=canonical_json_bytes(qa_report),
                retained_output_bytes=retained_output,
                retained_output_sha256=_sha256(retained_output),
                retained_output_size_bytes=len(retained_output),
                **binding,
            )
        )
        documents.append(document)
        handles.append(verify_asset_qa_review(document, resolver=resolver))
    return documents, handles, resolver


def build_test_verified_release(
    manifest: Mapping[str, Any],
    assetpack: Mapping[str, Any],
    reviews: Sequence[VerifiedAssetQaReview],
    resolver: TestAssetAuthorityResolver,
    *,
    id_prefix: str,
) -> tuple[dict[str, Any], VerifiedAssetReleaseAuthority]:
    review_identities = [dict(review.identity) for review in reviews]
    binding = {
        "workspace_id": "workspace-test-assets",
        "root_generation": 1,
        "source_revision": manifest["gamepack"]["content_hash"],
        "workflow_status_hash": None,
        "artifact_snapshot_hash": canonical_creation_hash(
            {
                "manifest": _identity(manifest, "manifest_id"),
                "assetpack": _identity(assetpack, "assetpack_id"),
                "reviews": review_identities,
            }
        ),
        "producer_job_id": f"job-{id_prefix}-release",
        "producer_operation": "asset.release.authorize",
        "producer_output_position": 0,
    }
    document = build_asset_release_authority(
        manifest,
        assetpack,
        reviews,
        release_authority_id=f"{id_prefix}_release_authority",
        blockers=[],
        authority=binding,
    )
    payload = serialize_asset_release_authority(document)
    resolver.releases[(document["release_authority_id"], document["content_hash"])] = (
        RetainedAssetReleaseAuthorityRecord(
            document_bytes=payload,
            document_blob_sha256=_sha256(payload),
            document_size_bytes=len(payload),
            **binding,
        )
    )
    return document, verify_asset_release_authority(
        document,
        manifest=manifest,
        assetpack=assetpack,
        reviews=reviews,
        resolver=resolver,
    )
