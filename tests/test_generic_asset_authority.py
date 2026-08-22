from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scripts.generate_generic_asset_production_schemas import build_schemas
from tests.test_multigenre_asset_processing import _build_processing_chain
from tests.test_multigenre_asset_production import _media_matrix_cases
from worldforge.__main__ import _resolve_generic_assetpack_cli_source
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.generic_asset_authority import (
    ASSET_QA_REVIEW_RECEIPT_FORMAT,
    ASSET_RELEASE_AUTHORITY_FORMAT,
    GenericAssetAuthorityError,
    RetainedAssetQaReviewRecord,
    RetainedAssetReleaseAuthorityRecord,
    VerifiedAssetQaReview,
    VerifiedAssetReleaseAuthority,
    build_asset_qa_review_receipt,
    build_asset_release_authority,
    derive_asset_release_blockers,
    load_asset_qa_review_receipt,
    load_asset_release_authority,
    serialize_asset_qa_review_receipt,
    serialize_asset_release_authority,
    validate_asset_qa_review_receipt_document,
    validate_asset_release_authority_document,
    verify_asset_qa_review,
    verify_asset_release_authority,
)
from worldforge.generic_asset_fixture_authority import (
    RepositoryFixtureAssetAuthorityError,
    resolve_repository_fixture_asset_authority,
)
from worldforge.generic_asset_processing import (
    GenericAssetProcessingError,
    build_asset_manifest,
    validate_asset_manifest_document,
)
from worldforge.generic_assetpack import (
    GenericAssetpackError,
    build_generic_assetpack_manifest,
    seal_generic_assetpack,
    serialize_generic_assetpack,
    validate_generic_assetpack_document,
)
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _reseal(document: dict[str, object]) -> dict[str, object]:
    document["content_hash"] = canonical_creation_hash(document)
    return document


class _Resolver:
    def __init__(
        self,
        *,
        review: object | None = None,
        release: object | None = None,
    ) -> None:
        self.review = review
        self.release = release
        self.review_requests: list[tuple[str, str]] = []
        self.release_requests: list[tuple[str, str]] = []

    def resolve_asset_qa_review(
        self,
        *,
        review_receipt_id: str,
        content_hash: str,
    ) -> object:
        self.review_requests.append((review_receipt_id, content_hash))
        return self.review

    def resolve_asset_release_authority(
        self,
        *,
        release_authority_id: str,
        content_hash: str,
    ) -> object:
        self.release_requests.append((release_authority_id, content_hash))
        return self.release


class GenericAssetAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="world-forge-asset-authority-")
        cls.root = Path(cls._temporary.name)
        media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
        cls.chain = _build_processing_chain(media_case, cls.root)
        cls.assetpack = build_generic_assetpack_manifest(
            cls.chain["manifest"],
            gamepack=cls.chain["gamepack"],
            subject=cls.chain["subject"],
            target=cls.chain["target"],
            style=cls.chain["style"],
            inventory=cls.chain["inventory"],
            asset_records=[cls.chain["record"]],
            artifact_root=cls.root,
            qa_reviews=cls.chain["qa_reviews"],
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def _binding(self, operation: str, *, output_position: int = 0) -> dict[str, object]:
        return {
            "workspace_id": "workspace-asset-authority",
            "root_generation": 7,
            "source_revision": "1" * 64,
            "workflow_status_hash": "2" * 64,
            "artifact_snapshot_hash": "3" * 64,
            "producer_job_id": "job-asset-authority",
            "producer_operation": operation,
            "producer_output_position": output_position,
        }

    def _output_payload(self) -> bytes:
        output = self.chain["qa_report"]["outputs"][0]
        return (self.root / output["locator"]).read_bytes()

    def _review(
        self,
        *,
        review_receipt_id: str = "review_png_texture",
        decisions: list[str] | None = None,
        blockers: list[str] | None = None,
    ) -> dict[str, object]:
        criteria = self.chain["specification"]["acceptance_criteria"]
        return build_asset_qa_review_receipt(
            self.chain["qa_report"],
            self.chain["specification"],
            self.chain["processing_receipt"],
            review_receipt_id=review_receipt_id,
            output_role=self.chain["qa_report"]["outputs"][0]["role"],
            decisions=decisions or ["approved"] * len(criteria),
            blockers=[] if blockers is None else blockers,
            authority=self._binding("asset.qa.review"),
            retained_output=self._output_payload(),
        )

    def _retained_review(
        self,
        review: dict[str, object],
    ) -> RetainedAssetQaReviewRecord:
        document_bytes = serialize_asset_qa_review_receipt(review)
        output = self._output_payload()
        binding = review["authority"]
        return RetainedAssetQaReviewRecord(
            document_bytes=document_bytes,
            document_blob_sha256=_sha256(document_bytes),
            document_size_bytes=len(document_bytes),
            specification_bytes=canonical_json_bytes(self.chain["specification"]),
            processing_receipt_bytes=canonical_json_bytes(self.chain["processing_receipt"]),
            qa_report_bytes=canonical_json_bytes(self.chain["qa_report"]),
            retained_output_bytes=output,
            retained_output_sha256=_sha256(output),
            retained_output_size_bytes=len(output),
            workspace_id=binding["workspace_id"],
            root_generation=binding["root_generation"],
            source_revision=binding["source_revision"],
            workflow_status_hash=binding["workflow_status_hash"],
            artifact_snapshot_hash=binding["artifact_snapshot_hash"],
            producer_job_id=binding["producer_job_id"],
            producer_operation=binding["producer_operation"],
            producer_output_position=binding["producer_output_position"],
        )

    def _verified_review(
        self,
        review: dict[str, object] | None = None,
    ) -> VerifiedAssetQaReview:
        candidate = self._review() if review is None else review
        resolver = _Resolver(review=self._retained_review(candidate))
        verified = verify_asset_qa_review(candidate, resolver=resolver)
        self.assertEqual(
            [(candidate["review_receipt_id"], candidate["content_hash"])],
            resolver.review_requests,
        )
        return verified

    def _release(
        self,
        reviews: list[VerifiedAssetQaReview],
        *,
        blockers: list[str] | None = None,
    ) -> dict[str, object]:
        return build_asset_release_authority(
            self.chain["manifest"],
            self.assetpack,
            reviews,
            release_authority_id="release_png_assetpack",
            blockers=[] if blockers is None else blockers,
            authority=self._binding("asset.release.authorize"),
        )

    def _retained_release(
        self,
        release: dict[str, object],
    ) -> RetainedAssetReleaseAuthorityRecord:
        document_bytes = serialize_asset_release_authority(release)
        binding = release["authority"]
        return RetainedAssetReleaseAuthorityRecord(
            document_bytes=document_bytes,
            document_blob_sha256=_sha256(document_bytes),
            document_size_bytes=len(document_bytes),
            workspace_id=binding["workspace_id"],
            root_generation=binding["root_generation"],
            source_revision=binding["source_revision"],
            workflow_status_hash=binding["workflow_status_hash"],
            artifact_snapshot_hash=binding["artifact_snapshot_hash"],
            producer_job_id=binding["producer_job_id"],
            producer_operation=binding["producer_operation"],
            producer_output_position=binding["producer_output_position"],
        )

    def test_raw_documents_and_scalar_resolver_claims_never_create_verified_handles(
        self,
    ) -> None:
        review = self._review()
        self.assertEqual(review, validate_asset_qa_review_receipt_document(review))
        with self.assertRaises(TypeError):
            VerifiedAssetQaReview(review)
        with self.assertRaises(TypeError):
            VerifiedAssetReleaseAuthority(review)

        for self_attested in (True, "a" * 64, {"status": "passed"}, review):
            with self.subTest(value=type(self_attested).__name__):
                with self.assertRaisesRegex(
                    GenericAssetAuthorityError,
                    "authority_resolver_invalid",
                ):
                    verify_asset_qa_review(
                        review,
                        resolver=_Resolver(review=self_attested),
                    )

        with self.assertRaisesRegex(
            GenericAssetAuthorityError,
            "authority_resolver_invalid",
        ):
            verify_asset_qa_review(review, resolver={})

    def test_review_binds_complete_ordered_criterion_text_hashes_and_decisions(
        self,
    ) -> None:
        review = self._review()
        expected = [
            {
                "criterion_index": index,
                "criterion_sha256": _sha256(criterion.encode("utf-8")),
                "decision": "approved",
            }
            for index, criterion in enumerate(self.chain["specification"]["acceptance_criteria"])
        ]
        self.assertEqual(expected, review["criteria"])

        with self.assertRaisesRegex(
            GenericAssetAuthorityError,
            "review_criterion_coverage",
        ):
            self._review(decisions=["approved"])

        mutations: list[tuple[str, object]] = [
            ("missing", lambda items: items.pop()),
            ("duplicate", lambda items: items.append(copy.deepcopy(items[0]))),
            ("reordered", lambda items: items.reverse()),
        ]
        for name, mutate in mutations:
            with self.subTest(mutation=name):
                crossed = copy.deepcopy(review)
                mutate(crossed["criteria"])
                _reseal(crossed)
                if name == "missing":
                    retained = self._retained_review(crossed)
                    with self.assertRaisesRegex(
                        GenericAssetAuthorityError,
                        "review_criterion_coverage",
                    ):
                        verify_asset_qa_review(
                            crossed,
                            resolver=_Resolver(review=retained),
                        )
                else:
                    with self.assertRaises(GenericAssetAuthorityError):
                        validate_asset_qa_review_receipt_document(crossed)

        mismatched_hash = copy.deepcopy(review)
        mismatched_hash["criteria"][0]["criterion_sha256"] = "f" * 64
        _reseal(mismatched_hash)
        with self.assertRaisesRegex(
            GenericAssetAuthorityError,
            "review_criterion_mismatch",
        ):
            verify_asset_qa_review(
                mismatched_hash,
                resolver=_Resolver(review=self._retained_review(mismatched_hash)),
            )

    def test_review_verification_binds_lineage_cas_producer_workspace_and_output(
        self,
    ) -> None:
        review = self._review()
        retained = self._retained_review(review)
        verified = verify_asset_qa_review(review, resolver=_Resolver(review=retained))
        self.assertTrue(verified.approved)
        self.assertEqual(review, verified.document)

        mismatches = {
            "workspace": replace(retained, workspace_id="workspace_crossed"),
            "generation": replace(retained, root_generation=8),
            "source": replace(retained, source_revision="4" * 64),
            "snapshot": replace(retained, artifact_snapshot_hash="5" * 64),
            "producer": replace(retained, producer_job_id="job_crossed"),
            "operation": replace(retained, producer_operation="asset.release.authorize"),
            "position": replace(retained, producer_output_position=1),
            "document CAS": replace(retained, document_blob_sha256="6" * 64),
            "document size": replace(retained, document_size_bytes=1),
            "output CAS": replace(retained, retained_output_sha256="7" * 64),
            "output size": replace(retained, retained_output_size_bytes=1),
            "output bytes": replace(retained, retained_output_bytes=b"crossed"),
            "specification bytes": replace(
                retained,
                specification_bytes=retained.qa_report_bytes,
            ),
        }
        for name, crossed in mismatches.items():
            with self.subTest(mismatch=name):
                with self.assertRaises(GenericAssetAuthorityError):
                    verify_asset_qa_review(
                        review,
                        resolver=_Resolver(review=crossed),
                    )

        crossed_lineage = copy.deepcopy(review)
        crossed_lineage["lineage"]["qa_report"]["content_hash"] = "8" * 64
        _reseal(crossed_lineage)
        crossed_retained = replace(
            retained,
            document_bytes=serialize_asset_qa_review_receipt(crossed_lineage),
        )
        crossed_retained = replace(
            crossed_retained,
            document_blob_sha256=_sha256(crossed_retained.document_bytes),
            document_size_bytes=len(crossed_retained.document_bytes),
        )
        with self.assertRaisesRegex(
            GenericAssetAuthorityError,
            "review_lineage_mismatch",
        ):
            verify_asset_qa_review(
                crossed_lineage,
                resolver=_Resolver(review=crossed_retained),
            )

    def test_release_authority_requires_exact_complete_unique_review_coverage(
        self,
    ) -> None:
        verified_review = self._verified_review()
        release = self._release([verified_review])
        retained = self._retained_release(release)
        resolver = _Resolver(release=retained)
        verified_release = verify_asset_release_authority(
            release,
            manifest=self.chain["manifest"],
            assetpack=self.assetpack,
            reviews=[verified_review],
            resolver=resolver,
        )
        self.assertTrue(verified_release.authorized)
        self.assertEqual(release, verified_release.document)
        self.assertEqual(
            [(release["release_authority_id"], release["content_hash"])],
            resolver.release_requests,
        )

        for self_attested in (True, "a" * 64, {"status": "authorized"}, release):
            with self.subTest(release_resolver=type(self_attested).__name__):
                with self.assertRaisesRegex(
                    GenericAssetAuthorityError,
                    "authority_resolver_invalid",
                ):
                    verify_asset_release_authority(
                        release,
                        manifest=self.chain["manifest"],
                        assetpack=self.assetpack,
                        reviews=[verified_review],
                        resolver=_Resolver(release=self_attested),
                    )

        for reviews in (
            [],
            [verified_review, verified_review],
            [verified_review.document],
        ):
            with self.subTest(review_count=len(reviews)):
                with self.assertRaisesRegex(
                    GenericAssetAuthorityError,
                    "release_review_coverage",
                ):
                    verify_asset_release_authority(
                        release,
                        manifest=self.chain["manifest"],
                        assetpack=self.assetpack,
                        reviews=reviews,
                        resolver=_Resolver(release=retained),
                    )

        incomplete = copy.deepcopy(release)
        incomplete["qa_reviews"] = []
        _reseal(incomplete)
        with self.assertRaisesRegex(
            GenericAssetAuthorityError,
            "release_review_coverage",
        ):
            validate_asset_release_authority_document(incomplete)

    def test_rejected_reviews_and_release_blockers_have_exact_status_semantics(self) -> None:
        criteria = self.chain["specification"]["acceptance_criteria"]
        rejected_decisions = ["rejected", *(["approved"] * (len(criteria) - 1))]
        with self.assertRaisesRegex(
            GenericAssetAuthorityError,
            "review_blockers",
        ):
            self._review(decisions=rejected_decisions)

        rejected = self._review(
            review_receipt_id="review_png_rejected",
            decisions=rejected_decisions,
            blockers=["criterion_rejected"],
        )
        verified_rejected = self._verified_review(rejected)
        self.assertFalse(verified_rejected.approved)
        self.assertEqual(
            ["criterion_rejected", "qa_review_rejected"],
            derive_asset_release_blockers(
                [verified_rejected],
                ["qa_review_rejected"],
            ),
        )
        for invalid_blockers in ("caller_blocker", ["z_blocker", "a_blocker"]):
            with (
                self.subTest(invalid_blockers=invalid_blockers),
                self.assertRaisesRegex(GenericAssetAuthorityError, "release_blockers"),
            ):
                build_asset_release_authority(
                    self.chain["manifest"],
                    self.assetpack,
                    [verified_rejected],
                    release_authority_id="release_invalid_blockers",
                    blockers=invalid_blockers,
                    authority=self._binding("asset.release.authorize"),
                )
        rejected_only = self._release([verified_rejected])
        self.assertEqual("blocked", rejected_only["status"])
        self.assertEqual(["criterion_rejected"], rejected_only["blockers"])

        blocked = self._release(
            [verified_rejected],
            blockers=["qa_review_rejected"],
        )
        self.assertEqual(
            ["criterion_rejected", "qa_review_rejected"],
            blocked["blockers"],
        )
        verified_blocked = verify_asset_release_authority(
            blocked,
            manifest=self.chain["manifest"],
            assetpack=self.assetpack,
            reviews=[verified_rejected],
            resolver=_Resolver(release=self._retained_release(blocked)),
        )
        self.assertFalse(verified_blocked.authorized)

        invalid_status = copy.deepcopy(blocked)
        invalid_status["status"] = "authorized"
        _reseal(invalid_status)
        with self.assertRaisesRegex(
            GenericAssetAuthorityError,
            "release_status",
        ):
            validate_asset_release_authority_document(invalid_status)

        missing_blocker = copy.deepcopy(blocked)
        missing_blocker["blockers"] = []
        _reseal(missing_blocker)
        with self.assertRaisesRegex(
            GenericAssetAuthorityError,
            "release_blockers",
        ):
            validate_asset_release_authority_document(missing_blocker)

    def test_hashes_serialization_and_loaders_are_deterministic_and_strict(self) -> None:
        first_review = self._review()
        second_review = self._review()
        self.assertEqual(first_review, second_review)
        review_bytes = serialize_asset_qa_review_receipt(first_review)
        self.assertEqual(canonical_json_bytes(first_review), review_bytes)

        verified = self._verified_review(first_review)
        first_release = self._release([verified])
        second_release = self._release([verified])
        self.assertEqual(first_release, second_release)
        release_bytes = serialize_asset_release_authority(first_release)
        self.assertEqual(canonical_json_bytes(first_release), release_bytes)

        with tempfile.TemporaryDirectory(prefix="world-forge-authority-load-") as temporary:
            root = Path(temporary)
            review_path = root / "review.json"
            release_path = root / "release.json"
            review_path.write_bytes(review_bytes)
            release_path.write_bytes(release_bytes)
            self.assertEqual(first_review, load_asset_qa_review_receipt(review_path))
            self.assertEqual(first_release, load_asset_release_authority(release_path))

            review_path.write_bytes(review_bytes + b" ")
            with self.assertRaises(GenericAssetAuthorityError):
                load_asset_qa_review_receipt(review_path)

    def test_runtime_manifest_and_assetpack_v1_remain_authority_field_free(self) -> None:
        for document, validator, field in (
            (
                self.chain["manifest"],
                validate_asset_manifest_document,
                "release_authority",
            ),
            (self.assetpack, validate_generic_assetpack_document, "qa_reviews"),
        ):
            with self.subTest(format=document["format"]):
                self.assertNotIn(field, document)
                crossed = copy.deepcopy(document)
                crossed[field] = {"self_attested": True}
                _reseal(crossed)
                with self.assertRaises((GenericAssetProcessingError, GenericAssetpackError)):
                    validator(crossed)

    def test_release_ready_builders_require_verified_qa_handles(self) -> None:
        manifest_arguments = {
            "gamepack": self.chain["gamepack"],
            "subject": self.chain["subject"],
            "target": self.chain["target"],
            "style": self.chain["style"],
            "inventory": self.chain["inventory"],
            "manifest_id": "authority_required_manifest",
            "state": "release_ready",
            "asset_records": [self.chain["record"]],
            "artifact_root": self.root,
        }
        with self.assertRaisesRegex(
            GenericAssetProcessingError,
            "manifest_qa_authority_required",
        ):
            build_asset_manifest(**manifest_arguments)
        for raw_reviews in (
            [self.chain["qa_report"]],
            [self._review()],
        ):
            with self.subTest(manifest_raw_type=raw_reviews[0]["format"]):
                with self.assertRaisesRegex(
                    GenericAssetProcessingError,
                    "manifest_qa_authority_invalid",
                ):
                    build_asset_manifest(
                        **manifest_arguments,
                        qa_reviews=raw_reviews,
                    )

        assetpack_arguments = {
            "manifest": self.chain["manifest"],
            "gamepack": self.chain["gamepack"],
            "subject": self.chain["subject"],
            "target": self.chain["target"],
            "style": self.chain["style"],
            "inventory": self.chain["inventory"],
            "asset_records": [self.chain["record"]],
            "artifact_root": self.root,
        }
        with self.assertRaisesRegex(
            GenericAssetpackError,
            "assetpack_qa_authority_required",
        ):
            build_generic_assetpack_manifest(**assetpack_arguments)
        for raw_reviews in (
            [self.chain["qa_report"]],
            [self._review()],
        ):
            with self.subTest(raw_type=raw_reviews[0]["format"]):
                with self.assertRaisesRegex(
                    GenericAssetpackError,
                    "assetpack_qa_authority_invalid",
                ):
                    build_generic_assetpack_manifest(
                        **assetpack_arguments,
                        qa_reviews=raw_reviews,
                    )

    def test_sealing_requires_exact_verified_release_authority_without_shipping_it(
        self,
    ) -> None:
        verified_review = self._verified_review()
        release = self._release([verified_review])
        verified_release = verify_asset_release_authority(
            release,
            manifest=self.chain["manifest"],
            assetpack=self.assetpack,
            reviews=[verified_review],
            resolver=_Resolver(release=self._retained_release(release)),
        )
        source = {
            "manifest": self.chain["manifest"],
            "gamepack": self.chain["gamepack"],
            "subject": self.chain["subject"],
            "target": self.chain["target"],
            "style": self.chain["style"],
            "inventory": self.chain["inventory"],
            "asset_records": [self.chain["record"]],
            "artifact_root": self.root,
            "qa_reviews": [verified_review],
        }
        for value, reason in (
            (None, "assetpack_release_authority_required"),
            (release, "assetpack_release_authority_invalid"),
        ):
            with self.subTest(authority_type=type(value).__name__):
                with tempfile.TemporaryDirectory(
                    prefix="world-forge-authority-seal-reject-"
                ) as temporary:
                    with self.assertRaisesRegex(GenericAssetpackError, reason):
                        seal_generic_assetpack(
                            Path(temporary) / "assetpack",
                            **source,
                            release_authority=value,
                        )

        with tempfile.TemporaryDirectory(
            prefix="world-forge-authority-seal-approved-"
        ) as temporary:
            destination = Path(temporary) / "assetpack"
            with seal_generic_assetpack(
                destination,
                **source,
                release_authority=verified_release,
            ) as sealed:
                self.assertEqual(self.assetpack, sealed.manifest)
                relative_files = sorted(
                    path.relative_to(destination).as_posix()
                    for path in destination.rglob("*")
                    if path.is_file()
                )
                self.assertNotIn("qa-review.json", relative_files)
                self.assertNotIn("release-authority.json", relative_files)
                for payload in sealed.files.values():
                    self.assertNotIn(release["content_hash"].encode("ascii"), payload)

    def test_repository_fixture_authority_is_exact_byte_bound_and_closed(self) -> None:
        source_root = ROOT / "examples" / "multigenre-contracts" / "abstract-puzzle"
        with tempfile.TemporaryDirectory(prefix="world-forge-fixture-authority-copy-") as temporary:
            copied = Path(temporary) / "abstract-puzzle"
            shutil.copytree(source_root, copied)
            manifest_path = copied / "assets" / "manifest.json"
            resolved = _resolve_generic_assetpack_cli_source(manifest_path)
            self.assertEqual(1, len(resolved["qa_reviews"]))
            self.assertTrue(resolved["release_authority"].authorized)

            review_path = copied / "assets" / "production" / "board_ui" / "qa-review-texture.json"
            duplicate = review_path.with_name("qa-review-extra.json")
            duplicate.write_bytes(review_path.read_bytes())
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "fixture_authority_companion_coverage",
            ):
                _resolve_generic_assetpack_cli_source(manifest_path)
            duplicate.unlink()

            original_review = review_path.read_bytes()
            cross_case_review = (
                ROOT
                / "examples"
                / "multigenre-contracts"
                / "action-framing"
                / "assets"
                / "production"
                / "action_hud"
                / "qa-review-texture.json"
            ).read_bytes()
            review_path.write_bytes(cross_case_review)
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "fixture_authority_bytes_mismatch",
            ):
                _resolve_generic_assetpack_cli_source(manifest_path)
            review_path.write_bytes(original_review)

            specification_path = copied / "assets" / "specs" / "board_ui.json"
            original_specification = specification_path.read_bytes()
            specification_path.write_bytes(original_specification + b" ")
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "fixture_authority_bytes_mismatch",
            ):
                _resolve_generic_assetpack_cli_source(manifest_path)
            specification_path.write_bytes(original_specification)

            extra_source = copied / "assets" / "unknown.json"
            extra_source.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "fixture_authority_source_coverage",
            ):
                _resolve_generic_assetpack_cli_source(manifest_path)
            extra_source.unlink()

            resolved = _resolve_generic_assetpack_cli_source(manifest_path)
            unknown_gamepack = copy.deepcopy(resolved["gamepack"])
            unknown_gamepack["content_hash"] = "0" * 64
            with self.assertRaisesRegex(
                RepositoryFixtureAssetAuthorityError,
                "fixture_authority_unknown",
            ):
                resolve_repository_fixture_asset_authority(
                    project_root=copied,
                    manifest=resolved["manifest"],
                    gamepack=unknown_gamepack,
                    subject=resolved["subject"],
                    target=resolved["target"],
                    style=resolved["style"],
                    inventory=resolved["inventory"],
                    asset_records=resolved["asset_records"],
                    artifact_root=resolved["artifact_root"],
                )

            retained_output = (
                copied
                / "assets"
                / "production"
                / "board_ui"
                / "processed"
                / "texture"
                / "board.png"
            )
            original_output = retained_output.read_bytes()
            retained_output.write_bytes(bytes([original_output[0] ^ 1]) + original_output[1:])
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "fixture_authority_bytes_mismatch",
            ):
                _resolve_generic_assetpack_cli_source(manifest_path)
            retained_output.write_bytes(original_output)

            review = json.loads(original_review)
            review["authority"]["producer_output_position"] = 1
            review["content_hash"] = canonical_creation_hash(review)
            review_path.write_bytes(canonical_json_bytes(review))
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "fixture_authority_bytes_mismatch",
            ):
                _resolve_generic_assetpack_cli_source(manifest_path)
            review_path.write_bytes(original_review)

            release_path = copied / "assets" / "release-authority.json"
            release_path.unlink()
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "fixture_authority_companion_coverage",
            ):
                _resolve_generic_assetpack_cli_source(manifest_path)

    def test_existing_v1_manifest_and_assetpack_golden_bytes_are_unchanged(self) -> None:
        expected = {
            "abstract-puzzle": (
                "c1a6c0408922545f211342b2828fd880c0069a27b8f3f952c261b5cc40bd4438",
                4251,
                "990cff52a2fcf28725122d4efec69ada445aad1adbe648409b19821b6bcd927f",
                5826,
            ),
            "branching-narrative": (
                "9829d73019d19ee7579c8444794d52ceb93ff9b916b1111ef1e54ba03b381dd1",
                4360,
                "6ab08b81201b108d02b06fdd221189493ba40b4904bdfc068440d7ce74fb4eb5",
                6050,
            ),
            "action-framing": (
                "5c650dcd150446e655c7dd5f7b73017c3f43c7f5d66c717c086027a583719dd4",
                4279,
                "027967ad968a462f5cb7e01626654c1acaf04793c3fe504402ab5ec474c2d971",
                5854,
            ),
            "faction-strategy": (
                "20142454315f32976c273a826d8b89b166a09ccdd0aa4bbc478763d4b9806f69",
                4307,
                "e232c9a420fbe9e01e9dec5043f547cdb7be1f361f33c3de9bbc1b959b517098",
                5882,
            ),
            "modular-roguelite": (
                "84b9351a034f280d32135c6c8091c0680b5f5d076fec7e98419b8a51588027b1",
                4333,
                "4c3e30de6752c1d77a294301cfae626556fa93b91927f4466f9d476979de59fe",
                5908,
            ),
            "sports-career": (
                "59efce7c66988c26b48e360d5e470a327640d5a90e41f743518194776a8915a2",
                4349,
                "6f2af847e4564737ee6d4c2f0c6a6a19e2184aaa928966291fdf6bab6d7b3e4d",
                5926,
            ),
        }
        fixture_root = ROOT / "examples" / "multigenre-contracts"
        for case, golden in expected.items():
            with self.subTest(case=case):
                manifest_path = fixture_root / case / "assets" / "manifest.json"
                manifest_payload = manifest_path.read_bytes()
                source = _resolve_generic_assetpack_cli_source(manifest_path)
                assetpack_payload = serialize_generic_assetpack(
                    build_generic_assetpack_manifest(**source)
                )
                actual = (
                    _sha256(manifest_payload),
                    len(manifest_payload),
                    _sha256(assetpack_payload),
                    len(assetpack_payload),
                )
                self.assertEqual(golden, actual)
                self.assertNotIn(b"asset_qa_review_receipt", assetpack_payload)
                self.assertNotIn(b"asset_release_authority", assetpack_payload)

    def test_generated_schemas_catalog_and_types_expose_only_additive_contracts(self) -> None:
        schemas = build_schemas()
        expected = {
            "generic-asset-qa-review-receipt.schema.json": (ASSET_QA_REVIEW_RECEIPT_FORMAT),
            "generic-asset-release-authority.schema.json": (ASSET_RELEASE_AUTHORITY_FORMAT),
        }
        for name, format_name in expected.items():
            with self.subTest(schema=name):
                self.assertIn(name, schemas)
                self.assertEqual(format_name, schemas[name]["properties"]["format"]["const"])
                self.assertEqual(
                    canonical_json_bytes(schemas[name]),
                    (ROOT / "schemas" / name).read_bytes(),
                )

        catalog = json.loads((ROOT / "contracts" / "catalog.json").read_text("utf-8"))
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        self.assertEqual(122, len(entries))
        required_companion_entries = {
            "generic-asset-qa-review-receipt": (
                ASSET_QA_REVIEW_RECEIPT_FORMAT,
                1,
                "schemas/generic-asset-qa-review-receipt.schema.json",
            ),
            "generic-asset-release-authority": (
                ASSET_RELEASE_AUTHORITY_FORMAT,
                1,
                "schemas/generic-asset-release-authority.schema.json",
            ),
            "runtime-support-authority": (
                "world-forge.runtime_support_authority",
                1,
                "schemas/runtime-support-authority.schema.json",
            ),
            "hosted-native-release-authority": (
                "world-forge.hosted_native_release_authority",
                1,
                "schemas/hosted-native-release-authority.schema.json",
            ),
            "hosted-native-release-attestation-receipt": (
                "world-forge.hosted_native_release_attestation_receipt",
                1,
                "schemas/hosted-native-release-attestation-receipt.schema.json",
            ),
            "studio-creation-job-v12": (
                "world-forge.studio_creation_job",
                12,
                "schemas/studio-creation-job-v12.schema.json",
            ),
            "studio-creation-output-grant-v6": (
                "world-forge.studio_creation_output_grant",
                6,
                "schemas/studio-creation-output-grant-v6.schema.json",
            ),
            "studio-creation-preview-v2": (
                "world-forge.studio_creation_preview",
                2,
                "schemas/studio-creation-preview-v2.schema.json",
            ),
            "studio-protocol-v5": (
                "rpg" + "-world-forge.studio_protocol",
                5,
                "schemas/studio-protocol-v5.schema.json",
            ),
        }
        self.assertLessEqual(required_companion_entries.keys(), entries.keys())
        for contract_id, (format_name, version, schema) in required_companion_entries.items():
            with self.subTest(contract_id=contract_id):
                self.assertEqual(format_name, entries[contract_id]["format"])
                self.assertEqual(version, entries[contract_id]["version"])
                self.assertEqual(schema, entries[contract_id]["schema"])
        expected_symbols = {
            "generic-asset-qa-review-receipt": [
                "worldforge.generic_asset_authority:build_asset_qa_review_receipt",
                "worldforge.generic_asset_authority:load_asset_qa_review_receipt",
                "worldforge.generic_asset_authority:serialize_asset_qa_review_receipt",
                "worldforge.generic_asset_authority:validate_asset_qa_review_receipt_document",
                "worldforge.generic_asset_authority:verify_asset_qa_review",
            ],
            "generic-asset-release-authority": [
                "worldforge.generic_asset_authority:build_asset_release_authority",
                "worldforge.generic_asset_authority:load_asset_release_authority",
                "worldforge.generic_asset_authority:serialize_asset_release_authority",
                "worldforge.generic_asset_authority:validate_asset_release_authority_document",
                "worldforge.generic_asset_authority:verify_asset_release_authority",
            ],
        }
        for contract_id, format_name in (
            ("generic-asset-qa-review-receipt", ASSET_QA_REVIEW_RECEIPT_FORMAT),
            ("generic-asset-release-authority", ASSET_RELEASE_AUTHORITY_FORMAT),
        ):
            self.assertEqual(format_name, entries[contract_id]["format"])
            self.assertEqual(1, entries[contract_id]["version"])
            self.assertEqual(
                ["tests/test_generic_asset_authority.py"],
                entries[contract_id]["tests"],
            )
            self.assertEqual(
                expected_symbols[contract_id],
                entries[contract_id]["python_symbols"],
            )

        self.assertEqual(
            [
                "examples/multigenre-contracts/abstract-puzzle/assets/production/board_ui/qa-review-texture.json",
                "examples/multigenre-contracts/action-framing/assets/production/action_hud/qa-review-texture.json",
                "examples/multigenre-contracts/branching-narrative/assets/production/narrative_ui_font/qa-review-font.json",
                "examples/multigenre-contracts/faction-strategy/assets/production/strategy_map/qa-review-texture.json",
                "examples/multigenre-contracts/modular-roguelite/assets/production/storylet_cards/qa-review-texture.json",
                "examples/multigenre-contracts/sports-career/assets/production/season_dashboard/qa-review-texture.json",
            ],
            entries["generic-asset-qa-review-receipt"]["fixtures"],
        )
        self.assertEqual(
            [
                f"examples/multigenre-contracts/{case}/assets/release-authority.json"
                for case in (
                    "abstract-puzzle",
                    "action-framing",
                    "branching-narrative",
                    "faction-strategy",
                    "modular-roguelite",
                    "sports-career",
                )
            ],
            entries["generic-asset-release-authority"]["fixtures"],
        )

        generated = (ROOT / "apps/studio/src/generated/world-forge-contracts.d.ts").read_text(
            "utf-8"
        )
        self.assertIn("WorldForgeRetainedAssetQAReviewReceiptV1", generated)
        self.assertIn("WorldForgeAssetReleaseAuthorityCompanionV1", generated)


if __name__ == "__main__":
    unittest.main()
