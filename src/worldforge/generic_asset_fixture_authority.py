from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.generic_asset_authority import (
    GenericAssetAuthorityError,
    RetainedAssetQaReviewRecord,
    RetainedAssetReleaseAuthorityRecord,
    VerifiedAssetQaReview,
    VerifiedAssetReleaseAuthority,
    serialize_asset_qa_review_receipt,
    serialize_asset_release_authority,
    validate_asset_qa_review_receipt_document,
    validate_asset_release_authority_document,
    verify_asset_qa_review,
    verify_asset_release_authority,
)
from worldforge.generic_asset_fixture_policy import (
    REPOSITORY_FIXTURE_ASSET_AUTHORITY_POLICY,
)
from worldforge.generic_asset_production import (
    GenericAssetProductionError,
    read_verified_artifact_bytes,
)
from worldforge.generic_assetpack import build_generic_assetpack_manifest
from worldforge.integrity import canonical_json_bytes


class RepositoryFixtureAssetAuthorityError(ValueError):
    """Raised when a source is not an exact code-owned repository fixture."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise RepositoryFixtureAssetAuthorityError(reason_code, detail)


def _identity(document: Mapping[str, object], id_field: str) -> dict[str, object]:
    return {
        "format": document["format"],
        "format_version": document["format_version"],
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


def _require_payload(payload: bytes, policy: Mapping[str, object], context: str) -> None:
    if (
        len(payload) != policy["size_bytes"]
        or hashlib.sha256(payload).hexdigest() != policy["sha256"]
    ):
        _fail("fixture_authority_bytes_mismatch", f"{context} differs from code-owned policy")


def _canonical_source_payload(
    document: object,
    policy: Mapping[str, object],
    context: str,
) -> bytes:
    if not isinstance(document, Mapping):
        _fail("fixture_authority_source_mismatch", f"{context} is not an object")
    payload = canonical_json_bytes(document)
    _require_payload(payload, policy, context)
    return payload


class _RepositoryFixtureResolver:
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
                "repository fixture review is not retained by exact identity",
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
                "repository fixture release is not retained by exact identity",
            ) from exc


def _match_policy(
    gamepack: Mapping[str, object],
    manifest: Mapping[str, object],
) -> tuple[str, Mapping[str, object]]:
    manifest_identity = _identity(manifest, "manifest_id")
    matches = [
        (case, policy)
        for case, policy in REPOSITORY_FIXTURE_ASSET_AUTHORITY_POLICY.items()
        if gamepack.get("content_hash") == policy.get("gamepack_content_hash")
        and manifest_identity == policy.get("manifest", {}).get("identity")
    ]
    if len(matches) != 1:
        _fail(
            "fixture_authority_unknown",
            "source is not one exact code-owned canonical multigenre fixture",
        )
    return matches[0]


def _require_companion_coverage(
    project_root: Path,
    policy: Mapping[str, object],
) -> None:
    expected = {str(review["path"]) for review in policy["reviews"] if isinstance(review, Mapping)}
    expected.add(str(policy["release"]["path"]))
    try:
        candidates = [
            *project_root.glob("assets/production/*/qa-review-*.json"),
            *project_root.glob("assets/release-authority*.json"),
        ]
        if len(candidates) > 64:
            _fail(
                "fixture_authority_companion_coverage",
                "fixture authority companion count exceeds its closed limit",
            )
        actual = {path.relative_to(project_root).as_posix() for path in candidates}
    except (OSError, ValueError) as exc:
        _fail("fixture_authority_companion_coverage", str(exc))
    if actual != expected:
        _fail(
            "fixture_authority_companion_coverage",
            "fixture authority companions are missing, duplicated, or extra",
        )


def _require_source_closure(
    project_root: Path,
    policy: Mapping[str, object],
) -> None:
    records = policy.get("source_closure")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        _fail("fixture_authority_source_coverage", "code-owned source closure is invalid")
    expected = {str(record["path"]) for record in records if isinstance(record, Mapping)}
    if len(expected) != len(records) or not 1 <= len(expected) <= 128:
        _fail("fixture_authority_source_coverage", "code-owned source closure is invalid")

    expected_asset_directories = {"assets"}
    for relative in expected:
        path = Path(relative)
        if not path.parts or path.parts[0] != "assets":
            continue
        for parent in path.parents:
            if parent == Path("."):
                continue
            expected_asset_directories.add(parent.as_posix())

    actual: set[str] = set()
    pending = [project_root / "assets"]
    visited_entries = 0
    try:
        while pending:
            directory = pending.pop()
            relative_directory = directory.relative_to(project_root).as_posix()
            if relative_directory not in expected_asset_directories:
                _fail(
                    "fixture_authority_source_coverage",
                    "fixture assets contain an unknown directory",
                )
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited_entries += 1
                    if visited_entries > 256:
                        _fail(
                            "fixture_authority_source_coverage",
                            "fixture source closure exceeds its closed limit",
                        )
                    entry_path = Path(entry.path)
                    relative = entry_path.relative_to(project_root).as_posix()
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(entry_path)
                    else:
                        actual.add(relative)
        for gamepack_path in (project_root / "artifacts").glob("*.gamepack.json"):
            actual.add(gamepack_path.relative_to(project_root).as_posix())
    except RepositoryFixtureAssetAuthorityError:
        raise
    except (OSError, ValueError) as exc:
        _fail("fixture_authority_source_coverage", str(exc))
    if actual != expected:
        _fail(
            "fixture_authority_source_coverage",
            "fixture source files are missing or extra",
        )

    for record in records:
        assert isinstance(record, Mapping)
        try:
            read_verified_artifact_bytes(
                project_root,
                str(record["path"]),
                expected_sha256=str(record["sha256"]),
                expected_size_bytes=int(record["size_bytes"]),
            )
        except (GenericAssetProductionError, OSError, TypeError, ValueError) as exc:
            _fail(
                "fixture_authority_bytes_mismatch",
                f"fixture source {record.get('path')}: {exc}",
            )


def _record_for_identity(
    asset_records: Sequence[Mapping[str, object]],
    field: str,
    identity: Mapping[str, object],
) -> Mapping[str, object]:
    id_fields = {
        "specification": "spec_id",
        "processing_receipt": "processing_receipt_id",
        "qa_report": "qa_report_id",
    }
    id_field = id_fields.get(field)
    if id_field is None:
        _fail("fixture_authority_source_mismatch", f"unknown source field {field!r}")
    matches = [
        record[field]
        for record in asset_records
        if isinstance(record.get(field), Mapping)
        and record[field].get("format") == identity["format"]
        and record[field].get("format_version") == identity["format_version"]
        and record[field].get("content_hash") == identity["content_hash"]
        and record[field].get(id_field) == identity["id"]
    ]
    if len(matches) != 1:
        _fail(
            "fixture_authority_source_mismatch",
            f"{field} did not resolve exactly once",
        )
    return matches[0]


def _read_policy_document(
    project_root: Path,
    policy: Mapping[str, object],
    *,
    validator: Any,
    serializer: Any,
    context: str,
) -> dict[str, Any]:
    try:
        payload = read_verified_artifact_bytes(
            project_root,
            str(policy["path"]),
            expected_sha256=str(policy["sha256"]),
            expected_size_bytes=int(policy["size_bytes"]),
        )
        document = validator(decode_json_object(payload, source=context))
    except (
        GenericAssetAuthorityError,
        GenericAssetProductionError,
        RuntimeIOError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        _fail("fixture_authority_load_failed", f"{context}: {exc}")
    if serializer(document) != payload:
        _fail("fixture_authority_bytes_mismatch", f"{context} is not canonical")
    _require_payload(payload, policy, context)
    return document


def resolve_repository_fixture_asset_authority(
    *,
    project_root: str | Path,
    manifest: Mapping[str, object],
    gamepack: Mapping[str, object],
    subject: Mapping[str, object],
    target: Mapping[str, object],
    style: Mapping[str, object],
    inventory: Mapping[str, object],
    asset_records: Sequence[Mapping[str, object]],
    artifact_root: str | Path,
) -> tuple[
    tuple[VerifiedAssetQaReview, ...],
    VerifiedAssetReleaseAuthority,
    dict[str, Any],
]:
    """Resolve authority only for an exact byte-identical canonical repository fixture."""

    checked_project_root = Path(project_root).absolute()
    checked_artifact_root = Path(artifact_root).absolute()
    _case, policy = _match_policy(gamepack, manifest)
    _require_companion_coverage(checked_project_root, policy)
    _require_source_closure(checked_project_root, policy)
    manifest_payload = canonical_json_bytes(manifest)
    _require_payload(manifest_payload, policy["manifest"], "fixture manifest")
    resolver = _RepositoryFixtureResolver()
    verified_reviews: list[VerifiedAssetQaReview] = []
    for review_policy in policy["reviews"]:
        review = _read_policy_document(
            checked_project_root,
            review_policy,
            validator=validate_asset_qa_review_receipt_document,
            serializer=serialize_asset_qa_review_receipt,
            context="repository fixture QA review",
        )
        if _identity(review, "review_receipt_id") != review_policy["identity"]:
            _fail("fixture_authority_source_mismatch", "fixture review identity differs")
        sources: dict[str, bytes] = {}
        for field in ("specification", "processing_receipt", "qa_report"):
            source_policy = review_policy["sources"][field]
            source = _record_for_identity(asset_records, field, source_policy["identity"])
            sources[field] = _canonical_source_payload(
                source,
                source_policy,
                f"fixture {field}",
            )
        output_policy = review_policy["retained_output"]
        try:
            retained_output = read_verified_artifact_bytes(
                checked_artifact_root,
                str(output_policy["path"]),
                expected_sha256=str(output_policy["sha256"]),
                expected_size_bytes=int(output_policy["size_bytes"]),
            )
        except (GenericAssetProductionError, OSError, TypeError, ValueError) as exc:
            _fail("fixture_authority_bytes_mismatch", f"fixture retained output: {exc}")
        payload = serialize_asset_qa_review_receipt(review)
        binding = review_policy["authority"]
        resolver.reviews[(review["review_receipt_id"], review["content_hash"])] = (
            RetainedAssetQaReviewRecord(
                document_bytes=payload,
                document_blob_sha256=hashlib.sha256(payload).hexdigest(),
                document_size_bytes=len(payload),
                specification_bytes=sources["specification"],
                processing_receipt_bytes=sources["processing_receipt"],
                qa_report_bytes=sources["qa_report"],
                retained_output_bytes=retained_output,
                retained_output_sha256=hashlib.sha256(retained_output).hexdigest(),
                retained_output_size_bytes=len(retained_output),
                **binding,
            )
        )
        verified_reviews.append(verify_asset_qa_review(review, resolver=resolver))

    assetpack = build_generic_assetpack_manifest(
        manifest,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
        asset_records=asset_records,
        artifact_root=checked_artifact_root,
        qa_reviews=verified_reviews,
    )
    if _identity(assetpack, "assetpack_id") != policy["assetpack_identity"]:
        _fail("fixture_authority_candidate_mismatch", "fixture assetpack candidate differs")
    release_policy = policy["release"]
    release = _read_policy_document(
        checked_project_root,
        release_policy,
        validator=validate_asset_release_authority_document,
        serializer=serialize_asset_release_authority,
        context="repository fixture release authority",
    )
    if _identity(release, "release_authority_id") != release_policy["identity"]:
        _fail("fixture_authority_source_mismatch", "fixture release identity differs")
    release_payload = serialize_asset_release_authority(release)
    release_binding = release_policy["authority"]
    resolver.releases[(release["release_authority_id"], release["content_hash"])] = (
        RetainedAssetReleaseAuthorityRecord(
            document_bytes=release_payload,
            document_blob_sha256=hashlib.sha256(release_payload).hexdigest(),
            document_size_bytes=len(release_payload),
            **release_binding,
        )
    )
    verified_release = verify_asset_release_authority(
        release,
        manifest=manifest,
        assetpack=assetpack,
        reviews=verified_reviews,
        resolver=resolver,
    )
    return tuple(verified_reviews), verified_release, assetpack


__all__ = [
    "RepositoryFixtureAssetAuthorityError",
    "resolve_repository_fixture_asset_authority",
]
