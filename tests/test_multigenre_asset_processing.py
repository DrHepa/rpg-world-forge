from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts.generate_generic_asset_production_schemas import build_schemas
from tests.asset_authority_support import (
    build_test_verified_release,
    build_test_verified_reviews,
)
from tests.test_multigenre_asset_production import (
    _build_complete_class_chain,
    _build_media_planning,
    _media_matrix_cases,
    _reseal,
)
from worldforge import generic_asset_processing as processing_module
from worldforge.generic_asset_authority import (
    ASSET_QA_REVIEW_RECEIPT_FORMAT,
    ASSET_RELEASE_AUTHORITY_FORMAT,
)
from worldforge.generic_asset_processing import (
    ASSET_MANIFEST_FORMAT,
    ASSET_PROCESSING_RECEIPT_FORMAT,
    ASSET_PROCESSING_RECIPE_FORMAT,
    ASSET_QA_REPORT_FORMAT,
    GENERIC_ASSET_PROCESSOR_ID,
    GenericAssetProcessingError,
    build_asset_manifest,
    build_asset_processing_receipt,
    build_asset_processing_recipe,
    build_asset_qa_report,
    load_asset_manifest,
    load_asset_processing_receipt,
    load_asset_processing_recipe,
    load_asset_qa_report,
    publish_asset_manifest,
    publish_asset_processing_receipt,
    publish_asset_processing_recipe,
    publish_asset_qa_report,
    serialize_asset_processing_contract,
    validate_asset_manifest,
    validate_asset_manifest_document,
    validate_asset_processing_receipt,
    validate_asset_processing_receipt_document,
    validate_asset_processing_recipe,
    validate_asset_processing_recipe_document,
    validate_asset_qa_report,
    validate_asset_qa_report_document,
)
from worldforge.generic_assetpack import build_generic_assetpack_manifest
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]


def _d2a_kwargs(chain: dict[str, object], artifact_root: Path) -> dict[str, object]:
    return {
        "gamepack": chain["gamepack"],
        "subject": chain["subject"],
        "target": chain["target"],
        "style": chain["style"],
        "inventory": chain["inventory"],
        "specification": chain["specification"],
        "request": chain["request"],
        "receipt": chain["receipt"],
        "selection": chain["selection"],
        "provenance": chain["provenance"],
        "license_records": chain["licenses"],
        "artifact_root": artifact_root,
    }


def _acceptance_results(specification: dict[str, object]) -> list[dict[str, object]]:
    criteria = specification["acceptance_criteria"]
    assert isinstance(criteria, list)
    return [
        {
            "criterion_index": index,
            "criterion_sha256": hashlib.sha256(str(criterion).encode("utf-8")).hexdigest(),
            "status": "passed",
            "evidence_hashes": [f"{index + 11:064x}"],
        }
        for index, criterion in enumerate(criteria)
    ]


def _corrupt_media_payload(media_type: str, payload: bytes) -> bytes:
    if media_type == "application/json":
        return b"{" + (b" " * (len(payload) - 1))
    if media_type == "text/x-glsl":
        return b"\0" * len(payload)
    return b"\0" * len(payload)


def _build_processing_chain(
    media_case: dict[str, object],
    artifact_root: Path,
) -> dict[str, object]:
    planning = _build_media_planning(media_case)
    chain = _build_complete_class_chain(
        planning,
        artifact_root,
        "procedural_offline",
        media_case=media_case,
    )
    kwargs = _d2a_kwargs(chain, artifact_root)
    recipe = build_asset_processing_recipe(
        recipe_id=f"{media_case['case_id']}_recipe",
        **kwargs,
    )
    processing_receipt = build_asset_processing_receipt(
        recipe,
        processing_receipt_id=f"{media_case['case_id']}_processing_receipt",
        **kwargs,
    )
    qa_report = build_asset_qa_report(
        processing_receipt,
        recipe=recipe,
        qa_report_id=f"{media_case['case_id']}_qa",
        acceptance_results=_acceptance_results(chain["specification"]),
        **kwargs,
    )
    record = {
        "specification": chain["specification"],
        "request": chain["request"],
        "receipt": chain["receipt"],
        "selection": chain["selection"],
        "provenance": chain["provenance"],
        "license_records": chain["licenses"],
        "recipe": recipe,
        "processing_receipt": processing_receipt,
        "qa_report": qa_report,
    }
    review_documents, qa_reviews, authority_resolver = build_test_verified_reviews(
        {
            **chain,
            "processing_receipt": processing_receipt,
            "qa_report": qa_report,
        },
        artifact_root,
        id_prefix=str(media_case["case_id"]),
    )
    manifest = build_asset_manifest(
        chain["gamepack"],
        chain["subject"],
        chain["target"],
        chain["style"],
        chain["inventory"],
        manifest_id=f"{media_case['case_id']}_manifest",
        state="release_ready",
        asset_records=[record],
        artifact_root=artifact_root,
        qa_reviews=qa_reviews,
    )
    assetpack = build_generic_assetpack_manifest(
        manifest,
        gamepack=chain["gamepack"],
        subject=chain["subject"],
        target=chain["target"],
        style=chain["style"],
        inventory=chain["inventory"],
        asset_records=[record],
        artifact_root=artifact_root,
        qa_reviews=qa_reviews,
    )
    release_document, release_authority = build_test_verified_release(
        manifest,
        assetpack,
        qa_reviews,
        authority_resolver,
        id_prefix=str(media_case["case_id"]),
    )
    return {
        **chain,
        "recipe": recipe,
        "processing_receipt": processing_receipt,
        "qa_report": qa_report,
        "manifest": manifest,
        "record": record,
        "qa_review_receipts": review_documents,
        "qa_reviews": qa_reviews,
        "authority_resolver": authority_resolver,
        "assetpack": assetpack,
        "release_authority_document": release_document,
        "release_authority": release_authority,
    }


class GenericAssetProcessingTests(unittest.TestCase):
    def test_resealed_document_local_recipe_and_receipt_crossings_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-local-semantics-") as temporary:
            root = Path(temporary)
            chain = _build_processing_chain(
                next(case for case in _media_matrix_cases() if case["case_id"] == "atlas"),
                root,
            )

            crossed_licenses = copy.deepcopy(chain["recipe"])
            first_license = copy.deepcopy(crossed_licenses["steps"][0]["license_record"])
            second_license = copy.deepcopy(crossed_licenses["steps"][1]["license_record"])
            crossed_licenses["steps"][0]["license_record"] = second_license
            crossed_licenses["licenses"][0]["license_record"] = second_license
            crossed_licenses["steps"][1]["license_record"] = first_license
            crossed_licenses["licenses"][1]["license_record"] = first_license
            crossed_licenses["content_hash"] = _reseal(crossed_licenses)
            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "processing_license_coverage",
            ):
                validate_asset_processing_recipe_document(crossed_licenses)

            duplicate_role = copy.deepcopy(chain["processing_receipt"])
            duplicate_output = copy.deepcopy(duplicate_role["outputs"][0])
            duplicate_output["step_id"] = "step_duplicate_receipt_role"
            duplicate_output["candidate_artifact_id"] = "duplicate_receipt_candidate"
            duplicate_output["runtime_path"] = "assets/matrix/duplicate-atlas.json"
            duplicate_output["locator"] = (
                "assets/production/matrix_atlas/processed/clipset/duplicate-atlas.json"
            )
            duplicate_role["outputs"].insert(1, duplicate_output)
            duplicate_role["content_hash"] = _reseal(duplicate_role)
            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "processing_receipt_noncanonical",
            ):
                validate_asset_processing_receipt_document(duplicate_role)

            runtime_collision = copy.deepcopy(chain["recipe"])
            runtime_collision["steps"][1]["runtime_path"] = runtime_collision["steps"][0][
                "runtime_path"
            ].upper()
            runtime_collision["content_hash"] = _reseal(runtime_collision)
            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "collision",
            ):
                validate_asset_processing_recipe_document(runtime_collision)

            runtime_prefix = copy.deepcopy(chain["recipe"])
            runtime_prefix["steps"][1]["runtime_path"] = (
                f"{runtime_prefix['steps'][0]['runtime_path']}/child.png"
            )
            runtime_prefix["content_hash"] = _reseal(runtime_prefix)
            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "collision",
            ):
                validate_asset_processing_recipe_document(runtime_prefix)

            reserved_runtime = copy.deepcopy(chain["recipe"])
            reserved_runtime["steps"][0]["runtime_path"] = "assets/CON/atlas.json"
            reserved_runtime["content_hash"] = _reseal(reserved_runtime)
            with self.assertRaises(GenericAssetProcessingError):
                validate_asset_processing_recipe_document(reserved_runtime)

            crossed_receipt_binding = copy.deepcopy(chain["processing_receipt"])
            crossed_receipt_binding["outputs"][0]["candidate_artifact_id"] = (
                "crossed_processing_candidate"
            )
            crossed_receipt_binding["content_hash"] = _reseal(crossed_receipt_binding)
            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "qa_processing_binding_mismatch",
            ):
                build_asset_qa_report(
                    crossed_receipt_binding,
                    recipe=chain["recipe"],
                    qa_report_id="crossed_processing_binding_qa",
                    acceptance_results=_acceptance_results(chain["specification"]),
                    **_d2a_kwargs(chain, root),
                )

            crossed_qa_output = copy.deepcopy(chain["qa_report"])
            crossed_qa_output["outputs"][0]["runtime_path"] = "assets/matrix/crossed-atlas.json"
            crossed_qa_output["content_hash"] = _reseal(crossed_qa_output)
            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "qa_lineage_mismatch",
            ):
                validate_asset_qa_report(
                    crossed_qa_output,
                    recipe=chain["recipe"],
                    processing_receipt=chain["processing_receipt"],
                    **_d2a_kwargs(chain, root),
                )

            manifest_collision = copy.deepcopy(chain["manifest"])
            duplicate_asset = copy.deepcopy(manifest_collision["assets"][0])
            duplicate_asset["asset"]["asset_id"] = "matrix_atlas_duplicate"
            duplicate_asset["asset"]["content_hash"] = "f" * 64
            manifest_collision["assets"].append(duplicate_asset)
            manifest_collision["assets"].sort(
                key=lambda item: item["asset"]["asset_id"].encode("utf-8")
            )
            manifest_collision["content_hash"] = _reseal(manifest_collision)
            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "collision",
            ):
                validate_asset_manifest_document(manifest_collision)

    def test_partial_publication_raises_validated_hash_bound_recovery_receipts(self) -> None:
        media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "atlas")
        for failing_write, expected_retained, expected_reason in (
            (1, 0, "processing_partial_publication"),
            (2, 1, "processing_partial_publication"),
        ):
            with self.subTest(failing_write=failing_write):
                with tempfile.TemporaryDirectory(
                    prefix=f"world-forge-d2b-recovery-{failing_write}-"
                ) as temporary:
                    root = Path(temporary)
                    chain = _build_complete_class_chain(
                        _build_media_planning(media_case),
                        root,
                        "procedural_offline",
                        media_case=media_case,
                    )
                    lineage = _d2a_kwargs(chain, root)
                    recipe = build_asset_processing_recipe(
                        recipe_id=f"atlas_recovery_recipe_{failing_write}",
                        **lineage,
                    )
                    real_writer = processing_module.write_bytes_atomic
                    writes = 0

                    def interrupted_writer(
                        path: str | Path,
                        payload: bytes,
                        *,
                        durable_parent: bool = False,
                        _failing_write: int = failing_write,
                        _real_writer: object = real_writer,
                    ) -> None:
                        nonlocal writes
                        writes += 1
                        if writes == _failing_write:
                            raise OSError(f"simulated write {_failing_write} failure")
                        assert callable(_real_writer)
                        _real_writer(path, payload, durable_parent=durable_parent)

                    with (
                        mock.patch.object(
                            processing_module,
                            "write_bytes_atomic",
                            side_effect=interrupted_writer,
                        ),
                        self.assertRaisesRegex(
                            GenericAssetProcessingError,
                            expected_reason,
                        ) as raised,
                    ):
                        build_asset_processing_receipt(
                            recipe,
                            processing_receipt_id=f"atlas_recovery_receipt_{failing_write}",
                            **lineage,
                        )

                    recovery_receipt = raised.exception.recovery_receipt
                    self.assertIsNotNone(recovery_receipt)
                    assert recovery_receipt is not None
                    self.assertEqual(
                        validate_asset_processing_receipt_document(recovery_receipt),
                        recovery_receipt,
                    )
                    self.assertEqual(recovery_receipt["status"], "failed")
                    self.assertEqual(recovery_receipt["outputs"], [])
                    self.assertEqual(
                        recovery_receipt["failure_reasons"],
                        [expected_reason],
                    )
                    recovery = recovery_receipt["recovery"]
                    self.assertIsInstance(recovery, dict)
                    assert isinstance(recovery, dict)
                    retained = recovery["retained_artifacts"]
                    self.assertEqual(len(retained), expected_retained)
                    self.assertEqual(
                        recovery["failure_code"],
                        expected_reason,
                    )
                    self.assertEqual(recovery["recipe"], recovery_receipt["recipe"])
                    self.assertEqual(
                        validate_asset_processing_receipt(
                            recovery_receipt,
                            recipe=recipe,
                            **lineage,
                        ),
                        recovery_receipt,
                    )
                    for artifact in retained:
                        payload = (root / artifact["locator"]).read_bytes()
                        self.assertEqual(
                            artifact["sha256"],
                            hashlib.sha256(payload).hexdigest(),
                        )
                        self.assertEqual(artifact["size_bytes"], len(payload))

                    if retained:
                        tampered = copy.deepcopy(recovery_receipt)
                        tampered["recovery"]["retained_artifacts"][0]["sha256"] = "0" * 64
                        tampered["recovery"]["content_hash"] = _reseal(tampered["recovery"])
                        tampered["content_hash"] = _reseal(tampered)
                        with self.assertRaisesRegex(
                            GenericAssetProcessingError,
                            "recovery",
                        ):
                            validate_asset_processing_receipt(
                                tampered,
                                recipe=recipe,
                                **lineage,
                            )

    def test_parent_rename_after_publication_cannot_be_downgraded_to_absent(self) -> None:
        media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "atlas")
        with tempfile.TemporaryDirectory(
            prefix="world-forge-d2b-renamed-publication-"
        ) as temporary:
            base = Path(temporary)
            root = base / "artifact-root"
            root.mkdir()
            chain = _build_complete_class_chain(
                _build_media_planning(media_case),
                root,
                "procedural_offline",
                media_case=media_case,
            )
            lineage = _d2a_kwargs(chain, root)
            recipe = build_asset_processing_recipe(
                recipe_id="atlas_renamed_publication_recipe",
                **lineage,
            )
            real_writer = processing_module.write_bytes_atomic
            retained_root = base / "retained-artifact-root"

            def renamed_parent_writer(
                path: str | Path,
                payload: bytes,
                *,
                durable_parent: bool = False,
            ) -> None:
                real_writer(path, payload, durable_parent=durable_parent)
                root.rename(retained_root)
                root.mkdir()
                raise OSError("simulated parent replacement after publication")

            with (
                mock.patch.object(
                    processing_module,
                    "write_bytes_atomic",
                    side_effect=renamed_parent_writer,
                ),
                self.assertRaisesRegex(
                    GenericAssetProcessingError,
                    "processing_partial_publication",
                ) as raised,
            ):
                build_asset_processing_receipt(
                    recipe,
                    processing_receipt_id="atlas_renamed_publication_receipt",
                    **lineage,
                )

            recovery_receipt = raised.exception.recovery_receipt
            self.assertIsNotNone(recovery_receipt)
            assert recovery_receipt is not None
            self.assertEqual(
                ["processing_partial_publication"],
                recovery_receipt["failure_reasons"],
            )
            self.assertEqual([], recovery_receipt["recovery"]["retained_artifacts"])
            published = retained_root / recipe["steps"][0]["output_locator"]
            self.assertTrue(published.is_file())

    def test_later_failure_recensuses_earlier_outputs_for_truthful_recovery(self) -> None:
        media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "atlas")
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-recovery-recensus-") as temporary:
            root = Path(temporary)
            chain = _build_complete_class_chain(
                _build_media_planning(media_case),
                root,
                "procedural_offline",
                media_case=media_case,
            )
            lineage = _d2a_kwargs(chain, root)
            recipe = build_asset_processing_recipe(
                recipe_id="atlas_recovery_recensus_recipe",
                **lineage,
            )
            real_writer = processing_module.write_bytes_atomic
            writes = 0

            def mutate_then_fail(
                path: str | Path,
                payload: bytes,
                *,
                durable_parent: bool = False,
            ) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    first = root / recipe["steps"][0]["output_locator"]
                    first.write_bytes(b"foreign-earlier-output")
                    raise OSError("simulated later publication failure")
                real_writer(path, payload, durable_parent=durable_parent)

            with (
                mock.patch.object(
                    processing_module,
                    "write_bytes_atomic",
                    side_effect=mutate_then_fail,
                ),
                self.assertRaisesRegex(
                    GenericAssetProcessingError,
                    "processing_partial_publication",
                ) as raised,
            ):
                build_asset_processing_receipt(
                    recipe,
                    processing_receipt_id="atlas_recovery_recensus_receipt",
                    **lineage,
                )

            recovery_receipt = raised.exception.recovery_receipt
            self.assertIsNotNone(recovery_receipt)
            assert recovery_receipt is not None
            self.assertEqual([], recovery_receipt["recovery"]["retained_artifacts"])
            self.assertEqual(
                recovery_receipt,
                validate_asset_processing_receipt(
                    recovery_receipt,
                    recipe=recipe,
                    **lineage,
                ),
            )

    def test_later_success_recensuses_the_complete_exact_output_set(self) -> None:
        media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "atlas")
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-complete-recensus-") as temporary:
            root = Path(temporary)
            chain = _build_complete_class_chain(
                _build_media_planning(media_case),
                root,
                "procedural_offline",
                media_case=media_case,
            )
            lineage = _d2a_kwargs(chain, root)
            recipe = build_asset_processing_recipe(
                recipe_id="atlas_complete_recensus_recipe",
                **lineage,
            )
            real_writer = processing_module.write_bytes_atomic
            writes = 0

            def mutate_then_publish(
                path: str | Path,
                payload: bytes,
                *,
                durable_parent: bool = False,
            ) -> None:
                nonlocal writes
                writes += 1
                if writes == 2:
                    first = root / recipe["steps"][0]["output_locator"]
                    first.write_bytes(b"foreign-earlier-output")
                real_writer(path, payload, durable_parent=durable_parent)

            with (
                mock.patch.object(
                    processing_module,
                    "write_bytes_atomic",
                    side_effect=mutate_then_publish,
                ),
                self.assertRaisesRegex(
                    GenericAssetProcessingError,
                    "processing_partial_publication",
                ) as raised,
            ):
                build_asset_processing_receipt(
                    recipe,
                    processing_receipt_id="atlas_complete_recensus_receipt",
                    **lineage,
                )

            recovery_receipt = raised.exception.recovery_receipt
            self.assertIsNotNone(recovery_receipt)
            assert recovery_receipt is not None
            self.assertEqual([], recovery_receipt["recovery"]["retained_artifacts"])
            self.assertEqual(
                recovery_receipt,
                validate_asset_processing_receipt(
                    recovery_receipt,
                    recipe=recipe,
                    **lineage,
                ),
            )

    def test_indeterminate_durability_is_recovery_not_collision_reuse(self) -> None:
        media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "atlas")
        with tempfile.TemporaryDirectory(
            prefix="world-forge-d2b-indeterminate-durability-"
        ) as temporary:
            root = Path(temporary)
            chain = _build_complete_class_chain(
                _build_media_planning(media_case),
                root,
                "procedural_offline",
                media_case=media_case,
            )
            lineage = _d2a_kwargs(chain, root)
            recipe = build_asset_processing_recipe(
                recipe_id="atlas_indeterminate_durability_recipe",
                **lineage,
            )
            real_writer = processing_module.write_bytes_atomic
            writes = 0

            def indeterminate_writer(
                path: str | Path,
                payload: bytes,
                *,
                durable_parent: bool = False,
            ) -> None:
                nonlocal writes
                writes += 1
                real_writer(path, payload, durable_parent=durable_parent)
                if writes == 1:
                    raise processing_module.AssetContractError(
                        "Published output durability is indeterminate: simulated"
                    )

            with (
                mock.patch.object(
                    processing_module,
                    "write_bytes_atomic",
                    side_effect=indeterminate_writer,
                ),
                self.assertRaisesRegex(
                    GenericAssetProcessingError,
                    "processing_partial_publication",
                ) as raised,
            ):
                build_asset_processing_receipt(
                    recipe,
                    processing_receipt_id="atlas_indeterminate_durability_receipt",
                    **lineage,
                )

            recovery_receipt = raised.exception.recovery_receipt
            self.assertIsNotNone(recovery_receipt)
            assert recovery_receipt is not None
            recovery = recovery_receipt["recovery"]
            self.assertIsInstance(recovery, dict)
            assert isinstance(recovery, dict)
            self.assertEqual(len(recovery["retained_artifacts"]), 1)
            self.assertEqual(
                validate_asset_processing_receipt(
                    recovery_receipt,
                    recipe=recipe,
                    **lineage,
                ),
                recovery_receipt,
            )
            completed = build_asset_processing_receipt(
                recipe,
                processing_receipt_id="atlas_indeterminate_durability_retry",
                **lineage,
            )
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(len(completed["outputs"]), 2)

    def test_qa_uses_one_retained_capture_and_reports_hash_media_and_metadata_failures(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-qa-capture-") as temporary:
            root = Path(temporary)
            media_case = _media_matrix_cases()[0]
            planning = _build_media_planning(media_case)
            chain = _build_complete_class_chain(
                planning,
                root,
                "procedural_offline",
                media_case=media_case,
            )
            lineage = _d2a_kwargs(chain, root)
            recipe = build_asset_processing_recipe(recipe_id="qa_capture_recipe", **lineage)
            receipt = build_asset_processing_receipt(
                recipe,
                processing_receipt_id="qa_capture_receipt",
                **lineage,
            )
            output = receipt["outputs"][0]
            output_path = root / output["locator"]
            original = output_path.read_bytes()
            output_path.write_bytes(_corrupt_media_payload(output["media_type"], original))

            real_reader = processing_module._safe_artifact_bytes
            output_reads = 0

            def counted_reader(
                artifact_root: str | Path,
                locator: object,
                *,
                limit: int,
            ) -> bytes:
                nonlocal output_reads
                if locator == output["locator"]:
                    output_reads += 1
                return real_reader(artifact_root, locator, limit=limit)

            with mock.patch.object(
                processing_module,
                "_safe_artifact_bytes",
                side_effect=counted_reader,
            ):
                report = build_asset_qa_report(
                    receipt,
                    recipe=recipe,
                    qa_report_id="qa_capture_failed",
                    acceptance_results=_acceptance_results(chain["specification"]),
                    **lineage,
                )
            self.assertEqual(output_reads, 1)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["outputs"][0]["metadata"], None)
            checks = {
                check["check_id"]: check["status"] for check in report["outputs"][0]["checks"]
            }
            self.assertEqual(checks["hash"], "failed")
            self.assertEqual(checks["media"], "failed")
            self.assertEqual(checks["png"], "failed")
            self.assertEqual(
                report["blockers"],
                [
                    "output_texture_hash_failed",
                    "output_texture_media_failed",
                    "output_texture_png_failed",
                ],
            )
            self.assertEqual(
                validate_asset_qa_report(
                    report,
                    recipe=recipe,
                    processing_receipt=receipt,
                    **lineage,
                ),
                report,
            )

            output_path.write_bytes(original)
            stale_metadata = copy.deepcopy(receipt)
            stale_metadata["outputs"][0]["metadata"]["width"] = 2
            stale_metadata["content_hash"] = _reseal(stale_metadata)
            stale_report = build_asset_qa_report(
                stale_metadata,
                recipe=recipe,
                qa_report_id="qa_stale_metadata",
                acceptance_results=_acceptance_results(chain["specification"]),
                **lineage,
            )
            stale_checks = {
                check["check_id"]: check["status"] for check in stale_report["outputs"][0]["checks"]
            }
            self.assertEqual(stale_report["outputs"][0]["metadata"], output["metadata"])
            self.assertEqual(stale_checks["hash"], "passed")
            self.assertEqual(stale_checks["media"], "failed")
            self.assertEqual(stale_checks["png"], "passed")
            self.assertEqual(
                stale_report["blockers"],
                ["output_texture_media_failed"],
            )

    def test_cli_strictly_dispatches_all_sixteen_generic_asset_formats(self) -> None:
        from worldforge import __main__ as worldforge_cli

        asset_root = ROOT / "examples" / "multigenre-contracts" / "abstract-puzzle" / "assets"
        contracts = []
        for path in sorted(asset_root.rglob("*.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            if str(document.get("format", "")).startswith("world-forge.asset_"):
                contracts.append((path, document))
        self.assertEqual(len(contracts), 16)
        self.assertEqual(len({document["format"] for _, document in contracts}), 16)
        for path, document in contracts:
            with self.subTest(format=document["format"]):
                output = io.StringIO()
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        ["worldforge", "validate-generic-asset-contract", str(path)],
                    ),
                    redirect_stdout(output),
                ):
                    self.assertEqual(worldforge_cli.main(), 0)
                self.assertEqual(
                    output.getvalue().strip(),
                    (f"OK format={document['format']} version=1 hash={document['content_hash']}"),
                )

    def test_cli_uses_strict_creation_reader_and_safe_portable_file_boundaries(self) -> None:
        from worldforge import __main__ as worldforge_cli

        fixture_document = json.loads(
            (
                ROOT
                / "examples"
                / "multigenre-contracts"
                / "abstract-puzzle"
                / "assets"
                / "production"
                / "board_ui"
                / "recipe.json"
            ).read_bytes()
        )
        for collection in ("licenses", "steps"):
            for item in fixture_document[collection]:
                item["license_record"]["candidate_artifact_id"] = item["candidate_artifact_id"]
                item["license_record"]["role"] = item["role"]
        fixture_document["content_hash"] = _reseal(fixture_document)
        fixture = canonical_json_bytes(fixture_document)

        def run(path: Path) -> tuple[int, str]:
            output = io.StringIO()
            with (
                mock.patch.object(
                    sys,
                    "argv",
                    ["worldforge", "validate-generic-asset-contract", str(path)],
                ),
                redirect_stdout(output),
            ):
                result = worldforge_cli.main()
            return result, output.getvalue()

        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-cli-reader-") as temporary:
            root = Path(temporary)
            portable_parent = root / "portable-parent"
            portable_parent.mkdir()
            for name in ("generic-asset.contract-v1.json", "GENERIC_ASSET_01.JSON"):
                with self.subTest(valid=name):
                    path = portable_parent / name
                    path.write_bytes(fixture)
                    status, output = run(path)
                    self.assertEqual(status, 0)
                    self.assertIn("OK format=world-forge.asset_processing_recipe", output)

            duplicate = portable_parent / "duplicate.json"
            duplicate.write_bytes(b'{"format":"a","format":"b"}')
            nonfinite = portable_parent / "nonfinite.json"
            nonfinite.write_bytes(b'{"format":NaN}')
            invalid_utf8 = portable_parent / "invalid-utf8.json"
            invalid_utf8.write_bytes(b'{"format":"\xff"}')
            non_object = portable_parent / "non-object.json"
            non_object.write_bytes(b"[]")

            hardlink_source = portable_parent / "hardlink-source.json"
            hardlink_source.write_bytes(fixture)
            hardlink = portable_parent / "hardlink.json"
            os.link(hardlink_source, hardlink)

            symlink = portable_parent / "symlink.json"
            symlink.symlink_to(portable_parent / "generic-asset.contract-v1.json")

            nonregular = portable_parent / "nonregular.json"
            nonregular.mkdir()

            real_parent = root / "real-parent"
            real_parent.mkdir()
            (real_parent / "contract.json").write_bytes(fixture)
            symlinked_parent = root / "symlinked-parent"
            symlinked_parent.symlink_to(real_parent, target_is_directory=True)
            unsafe_ancestor = root / "unsafe-ancestor"
            unsafe_ancestor.symlink_to(root, target_is_directory=True)

            invalid_paths = (
                duplicate,
                nonfinite,
                invalid_utf8,
                non_object,
                hardlink,
                symlink,
                nonregular,
                symlinked_parent / "contract.json",
                unsafe_ancestor / "real-parent" / "contract.json",
            )
            for path in invalid_paths:
                with self.subTest(invalid=path.name):
                    status, output = run(path)
                    self.assertEqual(status, 1)
                    self.assertIn("ERROR", output)
                    self.assertNotIn("Traceback", output)

    def test_fixture_generator_commits_complete_d2b_chain(self) -> None:
        from scripts.generate_generic_asset_fixtures import build_fixture_documents

        expected_formats = {
            ASSET_PROCESSING_RECIPE_FORMAT,
            ASSET_PROCESSING_RECEIPT_FORMAT,
            ASSET_QA_REPORT_FORMAT,
            ASSET_MANIFEST_FORMAT,
            ASSET_QA_REVIEW_RECEIPT_FORMAT,
            ASSET_RELEASE_AUTHORITY_FORMAT,
        }
        for case in ("abstract-puzzle", "branching-narrative"):
            with self.subTest(case=case):
                documents = build_fixture_documents(case)
                self.assertEqual(len(documents), 18)
                formats = {
                    document["format"] for _, document, _ in documents if document is not None
                }
                self.assertTrue(expected_formats.issubset(formats))
                binary_paths = {
                    path.relative_to(ROOT / "examples" / "multigenre-contracts" / case).as_posix()
                    for path, document, _ in documents
                    if document is None
                }
                asset_id = "board_ui" if case == "abstract-puzzle" else "narrative_ui_font"
                self.assertTrue(
                    any(
                        path.startswith(f"assets/production/{asset_id}/processed/")
                        for path in binary_paths
                    )
                )

    def test_processing_schemas_are_canonical_generated_outputs(self) -> None:
        generated = build_schemas()
        names = {
            "generic-asset-processing-recipe.schema.json",
            "generic-asset-processing-receipt.schema.json",
            "generic-asset-qa-report.schema.json",
            "generic-asset-manifest.schema.json",
        }
        self.assertTrue(names.issubset(generated))
        for name in names:
            with self.subTest(schema=name):
                self.assertEqual(
                    canonical_json_bytes(generated[name]),
                    (ROOT / "schemas" / name).read_bytes(),
                )

    def test_processing_schema_surface_exposes_recovery_and_failed_qa_parity(self) -> None:
        generated = build_schemas()
        recipe = generated["generic-asset-processing-recipe.schema.json"]
        receipt = generated["generic-asset-processing-receipt.schema.json"]
        qa = generated["generic-asset-qa-report.schema.json"]
        manifest = generated["generic-asset-manifest.schema.json"]
        for kind, schema in (
            ("recipe", recipe),
            ("receipt", receipt),
            ("qa", qa),
            ("manifest", manifest),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(schema["x-world-forge-d2b-coherent"], kind)

        license_identity = recipe["$defs"]["licenseBindingIdentity"]
        self.assertEqual(
            set(license_identity["required"]),
            {
                "format",
                "format_version",
                "id",
                "content_hash",
                "candidate_artifact_id",
                "role",
            },
        )
        self.assertIn("recovery", receipt["required"])
        self.assertEqual(
            set(receipt["$defs"]["recovery"]["required"]),
            {
                "failure_code",
                "recipe",
                "retained_artifacts",
                "content_hash",
            },
        )
        qa_output_variants = qa["$defs"]["qaOutput"]["oneOf"]
        self.assertTrue(
            all(
                {"type": "null"} in variant["properties"]["metadata"]["oneOf"]
                for variant in qa_output_variants
            )
        )
        self.assertTrue(
            any(
                "failed"
                in variant["properties"]["checks"]["prefixItems"][0]["properties"]["status"]["enum"]
                for variant in qa_output_variants
            )
        )

    def _assert_media_cases_process_qa_and_become_release_ready(
        self,
        case_ids: tuple[str, ...],
    ) -> None:
        expected_operations = {
            "png": ["validate_copy_png"],
            "atlas": ["canonicalize_clipset_json", "validate_copy_png"],
            "wav": ["validate_copy_pcm16_wav"],
            "ttf": ["validate_copy_font"],
            "otf": ["validate_copy_font"],
            "glsl": [
                "validate_copy_fragment_glsl",
                "validate_copy_vertex_glsl",
            ],
            "json": ["canonicalize_localization_json"],
            "glb": ["validate_copy_glb"],
            "glb_pair": ["validate_copy_glb", "validate_copy_glb"],
        }
        media_cases = {
            str(media_case["case_id"]): media_case for media_case in _media_matrix_cases()
        }
        self.assertEqual(set(case_ids).difference(media_cases), set())
        for case_id in case_ids:
            media_case = media_cases[case_id]
            with self.subTest(media=case_id):
                with tempfile.TemporaryDirectory(prefix=f"world-forge-d2b-{case_id}-") as temporary:
                    root = Path(temporary)
                    chain = _build_processing_chain(media_case, root)
                    recipe = chain["recipe"]
                    receipt = chain["processing_receipt"]
                    qa_report = chain["qa_report"]
                    manifest = chain["manifest"]
                    self.assertEqual(
                        [step["role"] for step in recipe["steps"]],
                        sorted(
                            [step["role"] for step in recipe["steps"]],
                            key=lambda value: value.encode("utf-8"),
                        ),
                    )
                    self.assertEqual(
                        [step["operation"] for step in recipe["steps"]],
                        expected_operations[case_id],
                    )
                    self.assertEqual(
                        recipe["processor"],
                        {
                            "processor_id": GENERIC_ASSET_PROCESSOR_ID,
                            "version": 1,
                        },
                    )
                    self.assertEqual(receipt["status"], "completed")
                    self.assertEqual(qa_report["status"], "passed")
                    self.assertEqual(manifest["state"], "release_ready")
                    self.assertEqual(
                        len(receipt["outputs"]),
                        len(media_case["outputs"]),
                    )
                    for output in receipt["outputs"]:
                        payload = (root / output["locator"]).read_bytes()
                        self.assertEqual(
                            hashlib.sha256(payload).hexdigest(),
                            output["sha256"],
                        )
                        self.assertEqual(len(payload), output["size_bytes"])
                    if case_id in {"atlas", "json"}:
                        json_outputs = [
                            output
                            for output in receipt["outputs"]
                            if output["media_type"] == "application/json"
                        ]
                        self.assertTrue(json_outputs)
                        for output in json_outputs:
                            payload = (root / output["locator"]).read_bytes()
                            self.assertEqual(
                                payload,
                                canonical_json_bytes(json.loads(payload)),
                            )
                    originals = {
                        output["locator"]: (root / output["locator"]).read_bytes()
                        for output in receipt["outputs"]
                    }
                    for output in receipt["outputs"]:
                        payload = originals[output["locator"]]
                        (root / output["locator"]).write_bytes(
                            _corrupt_media_payload(output["media_type"], payload)
                        )
                    failed_qa = build_asset_qa_report(
                        receipt,
                        recipe=recipe,
                        qa_report_id=f"{case_id}_failed_media_qa",
                        acceptance_results=_acceptance_results(chain["specification"]),
                        **_d2a_kwargs(chain, root),
                    )
                    self.assertEqual(failed_qa["status"], "failed")
                    media_check = {
                        "application/json": "json",
                        "audio/wav": "wav",
                        "font/otf": "font",
                        "font/ttf": "font",
                        "image/png": "png",
                        "model/gltf-binary": "glb",
                        "text/x-glsl": "glsl",
                    }
                    for output in failed_qa["outputs"]:
                        checks = {check["check_id"]: check["status"] for check in output["checks"]}
                        self.assertEqual(checks["hash"], "failed")
                        self.assertEqual(checks["media"], "failed")
                        self.assertEqual(
                            checks[media_check[output["media_type"]]],
                            "failed",
                        )
                        self.assertIsNone(output["metadata"])
                    for locator, payload in originals.items():
                        (root / locator).write_bytes(payload)

    def test_raster_atlas_and_audio_process_qa_and_become_release_ready(
        self,
    ) -> None:
        self._assert_media_cases_process_qa_and_become_release_ready(
            ("png", "atlas", "wav"),
        )

    def test_fonts_and_shaders_process_qa_and_become_release_ready(self) -> None:
        self._assert_media_cases_process_qa_and_become_release_ready(
            ("ttf", "otf", "glsl"),
        )

    def test_json_and_models_process_qa_and_become_release_ready(self) -> None:
        self._assert_media_cases_process_qa_and_become_release_ready(
            ("json", "glb", "glb_pair"),
        )

    def _assert_production_class_closes_d2b_integrally(
        self,
        production_class: str,
    ) -> None:
        seen: set[tuple[str, bool]] = set()
        media_cases = {
            False: next(case for case in _media_matrix_cases() if case["case_id"] == "png"),
            True: next(case for case in _media_matrix_cases() if case["case_id"] == "atlas"),
        }
        for include_input in (False, True):
            with self.subTest(include_input=include_input):
                seen.add((production_class, include_input))
                media_case = media_cases[include_input]
                with tempfile.TemporaryDirectory(
                    prefix=(
                        f"world-forge-d2b-class-{production_class}-"
                        f"{'input' if include_input else 'no-input'}-"
                    )
                ) as temporary:
                    root = Path(temporary)
                    chain = _build_complete_class_chain(
                        _build_media_planning(media_case),
                        root,
                        production_class,
                        include_input=include_input,
                        media_case=media_case,
                    )
                    lineage = _d2a_kwargs(chain, root)
                    recipe = build_asset_processing_recipe(
                        recipe_id=f"{production_class}_matrix_recipe",
                        **lineage,
                    )
                    receipt = build_asset_processing_receipt(
                        recipe,
                        processing_receipt_id=f"{production_class}_matrix_receipt",
                        **lineage,
                    )
                    qa_report = build_asset_qa_report(
                        receipt,
                        recipe=recipe,
                        qa_report_id=f"{production_class}_matrix_qa",
                        acceptance_results=_acceptance_results(chain["specification"]),
                        **lineage,
                    )
                    record = {
                        "specification": chain["specification"],
                        "request": chain["request"],
                        "receipt": chain["receipt"],
                        "selection": chain["selection"],
                        "provenance": chain["provenance"],
                        "license_records": chain["licenses"],
                        "recipe": recipe,
                        "processing_receipt": receipt,
                        "qa_report": qa_report,
                    }
                    _review_documents, qa_reviews, _resolver = build_test_verified_reviews(
                        {
                            **chain,
                            "processing_receipt": receipt,
                            "qa_report": qa_report,
                        },
                        root,
                        id_prefix=f"{production_class}_{int(include_input)}",
                    )
                    manifest = build_asset_manifest(
                        chain["gamepack"],
                        chain["subject"],
                        chain["target"],
                        chain["style"],
                        chain["inventory"],
                        manifest_id=f"{production_class}_matrix_manifest",
                        state="release_ready",
                        asset_records=[record],
                        artifact_root=root,
                        qa_reviews=qa_reviews,
                    )
                    self.assertEqual(receipt["status"], "completed")
                    self.assertEqual(qa_report["status"], "passed")
                    self.assertEqual(manifest["state"], "release_ready")
                    self.assertEqual(
                        len(receipt["outputs"]),
                        2 if include_input else 1,
                    )
                    input_scopes = [
                        component["scope"]
                        for component in chain["licenses"][0]["component_licenses"]
                    ]
                    self.assertEqual(
                        input_scopes.count("input_license"),
                        1 if include_input else 0,
                    )
        self.assertEqual(
            seen,
            {(production_class, include_input) for include_input in (False, True)},
        )

    def test_human_production_with_and_without_inputs_closes_d2b_integrally(
        self,
    ) -> None:
        self._assert_production_class_closes_d2b_integrally("human")

    def test_procedural_offline_production_with_and_without_inputs_closes_d2b_integrally(
        self,
    ) -> None:
        self._assert_production_class_closes_d2b_integrally("procedural_offline")

    def test_external_authoring_production_with_and_without_inputs_closes_d2b_integrally(
        self,
    ) -> None:
        self._assert_production_class_closes_d2b_integrally("external_authoring")

    def test_generative_authoring_production_with_and_without_inputs_closes_d2b_integrally(
        self,
    ) -> None:
        self._assert_production_class_closes_d2b_integrally("generative_authoring")

    def test_document_validators_are_strict_but_integral_validators_bind_lineage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-integral-") as temporary:
            root = Path(temporary)
            chain = _build_processing_chain(_media_matrix_cases()[0], root)
            documents = (
                (
                    chain["recipe"],
                    validate_asset_processing_recipe_document,
                    validate_asset_processing_recipe,
                    _d2a_kwargs(chain, root),
                ),
                (
                    chain["processing_receipt"],
                    validate_asset_processing_receipt_document,
                    validate_asset_processing_receipt,
                    {
                        "recipe": chain["recipe"],
                        **_d2a_kwargs(chain, root),
                    },
                ),
                (
                    chain["qa_report"],
                    validate_asset_qa_report_document,
                    validate_asset_qa_report,
                    {
                        "recipe": chain["recipe"],
                        "processing_receipt": chain["processing_receipt"],
                        **_d2a_kwargs(chain, root),
                    },
                ),
            )
            for document, document_validator, integral_validator, dependencies in documents:
                with self.subTest(format=document["format"]):
                    self.assertEqual(document_validator(document), document)
                    self.assertEqual(integral_validator(document, **dependencies), document)
                    crossed = copy.deepcopy(document)
                    crossed["gamepack"]["content_hash"] = "f" * 64
                    crossed["content_hash"] = _reseal(crossed)
                    self.assertEqual(document_validator(crossed), crossed)
                    with self.assertRaises(GenericAssetProcessingError):
                        integral_validator(crossed, **dependencies)

    def test_recipe_requires_exact_selection_provenance_and_one_license_per_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-recipe-") as temporary:
            root = Path(temporary)
            media_case = _media_matrix_cases()[1]
            planning = _build_media_planning(media_case)
            chain = _build_complete_class_chain(
                planning,
                root,
                "procedural_offline",
                media_case=media_case,
            )
            kwargs = _d2a_kwargs(chain, root)
            recipe = build_asset_processing_recipe(recipe_id="atlas_recipe", **kwargs)
            self.assertEqual(
                [item["candidate_artifact_id"] for item in recipe["licenses"]],
                [
                    output["candidate_artifact_id"]
                    for output in chain["selection"]["selected_outputs"]
                ],
            )
            for records in (
                chain["licenses"][:-1],
                [*chain["licenses"], copy.deepcopy(chain["licenses"][0])],
            ):
                with self.subTest(count=len(records)):
                    with self.assertRaises(GenericAssetProcessingError):
                        build_asset_processing_recipe(
                            recipe_id="bad_recipe",
                            **{**kwargs, "license_records": records},
                        )
            crossed = copy.deepcopy(chain["licenses"])
            crossed[0]["candidate"]["candidate_artifact_id"] = "crossed_candidate"
            crossed[0]["content_hash"] = _reseal(crossed[0])
            with self.assertRaises(GenericAssetProcessingError):
                build_asset_processing_recipe(
                    recipe_id="crossed_recipe",
                    **{**kwargs, "license_records": crossed},
                )

    def test_json_canonicalization_requires_modification_permission(self) -> None:
        media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "json")
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-json-rights-") as temporary:
            root = Path(temporary)
            chain = _build_complete_class_chain(
                _build_media_planning(media_case),
                root,
                "procedural_offline",
                media_case=media_case,
            )
            denied = copy.deepcopy(chain["licenses"])
            denied[0]["permissions"]["modification"] = False
            denied[0]["content_hash"] = _reseal(denied[0])
            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "processing_license_permission",
            ):
                build_asset_processing_recipe(
                    recipe_id="json_recipe",
                    **{
                        **_d2a_kwargs(chain, root),
                        "license_records": denied,
                    },
                )

    def test_receipts_bind_recipe_sources_outputs_failure_state_and_retained_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-receipt-") as temporary:
            root = Path(temporary)
            media_case = _media_matrix_cases()[0]
            planning = _build_media_planning(media_case)
            chain = _build_complete_class_chain(
                planning,
                root,
                "procedural_offline",
                media_case=media_case,
            )
            kwargs = _d2a_kwargs(chain, root)
            recipe = build_asset_processing_recipe(recipe_id="png_recipe", **kwargs)
            failed = build_asset_processing_receipt(
                recipe,
                processing_receipt_id="png_failed",
                status="failed",
                failure_reasons=["processor_interrupted"],
                **kwargs,
            )
            self.assertEqual(failed["outputs"], [])
            self.assertEqual(failed["failure_reasons"], ["processor_interrupted"])
            with self.assertRaises(GenericAssetProcessingError):
                build_asset_qa_report(
                    failed,
                    recipe=recipe,
                    qa_report_id="failed_qa",
                    acceptance_results=_acceptance_results(chain["specification"]),
                    **kwargs,
                )

            completed = build_asset_processing_receipt(
                recipe,
                processing_receipt_id="png_completed",
                **kwargs,
            )
            output_path = root / completed["outputs"][0]["locator"]
            output_path.write_bytes(output_path.read_bytes() + b"stale")
            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "processed_output_mismatch",
            ):
                validate_asset_processing_receipt(
                    completed,
                    recipe=recipe,
                    **kwargs,
                )

            contradictory = copy.deepcopy(failed)
            contradictory["status"] = "completed"
            contradictory["content_hash"] = _reseal(contradictory)
            with self.assertRaises(GenericAssetProcessingError):
                validate_asset_processing_receipt_document(contradictory)

    def test_partial_publication_retains_created_and_foreign_outputs(self) -> None:
        for foreign_write, expected_retained in ((1, 0), (2, 1)):
            with (
                self.subTest(foreign_write=foreign_write),
                tempfile.TemporaryDirectory(prefix="world-forge-d2b-partial-") as temporary,
            ):
                root = Path(temporary)
                media_case = next(
                    case for case in _media_matrix_cases() if case["case_id"] == "atlas"
                )
                planning = _build_media_planning(media_case)
                chain = _build_complete_class_chain(
                    planning,
                    root,
                    "procedural_offline",
                    media_case=media_case,
                )
                lineage = _d2a_kwargs(chain, root)
                recipe = build_asset_processing_recipe(
                    recipe_id=f"atlas_partial_recipe_{foreign_write}",
                    **lineage,
                )
                real_writer = processing_module.write_bytes_atomic
                writes = 0
                foreign_payload = b"foreign-writer-owned"

                def interrupted_writer(
                    path: str | Path,
                    payload: bytes,
                    *,
                    durable_parent: bool = False,
                    _foreign_write: int = foreign_write,
                    _foreign_payload: bytes = foreign_payload,
                    _real_writer: object = real_writer,
                ) -> None:
                    nonlocal writes
                    writes += 1
                    if writes == _foreign_write:
                        destination = Path(path)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(_foreign_payload)
                        raise OSError("foreign publication won")
                    assert callable(_real_writer)
                    _real_writer(path, payload, durable_parent=durable_parent)

                with (
                    mock.patch.object(
                        processing_module,
                        "write_bytes_atomic",
                        side_effect=interrupted_writer,
                    ),
                    self.assertRaisesRegex(
                        GenericAssetProcessingError,
                        "processing_partial_publication",
                    ) as raised,
                ):
                    build_asset_processing_receipt(
                        recipe,
                        processing_receipt_id=f"atlas_partial_receipt_{foreign_write}",
                        **lineage,
                    )

                receipt = raised.exception.recovery_receipt
                self.assertIsNotNone(receipt)
                assert receipt is not None
                self.assertEqual(
                    expected_retained,
                    len(receipt["recovery"]["retained_artifacts"]),
                )
                first, second = recipe["steps"]
                foreign = first if foreign_write == 1 else second
                self.assertEqual((root / foreign["output_locator"]).read_bytes(), foreign_payload)
                if foreign_write == 2:
                    self.assertTrue((root / first["output_locator"]).is_file())

    def test_processed_outputs_reject_links_and_recipe_paths_reject_aliases(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-links-") as temporary:
            root = Path(temporary)
            chain = _build_processing_chain(_media_matrix_cases()[0], root)
            receipt = chain["processing_receipt"]
            output_path = root / receipt["outputs"][0]["locator"]
            retained = output_path.with_suffix(".retained")
            output_path.rename(retained)
            for link_kind in ("hardlink", "symlink"):
                with self.subTest(kind=link_kind):
                    if output_path.exists() or output_path.is_symlink():
                        output_path.unlink()
                    if link_kind == "hardlink":
                        os.link(retained, output_path)
                    else:
                        output_path.symlink_to(retained)
                    with self.assertRaises(GenericAssetProcessingError):
                        validate_asset_processing_receipt(
                            receipt,
                            recipe=chain["recipe"],
                            **_d2a_kwargs(chain, root),
                        )

            recipe = copy.deepcopy(chain["recipe"])
            recipe["steps"][0]["output_locator"] = "CON/asset.png"
            recipe["content_hash"] = _reseal(recipe)
            with self.assertRaises(GenericAssetProcessingError):
                validate_asset_processing_recipe_document(recipe)

    def test_qa_structure_accepts_exactly_64_criteria_and_evidence_hashes(self) -> None:
        report = json.loads(
            (
                ROOT
                / "examples"
                / "multigenre-contracts"
                / "abstract-puzzle"
                / "assets"
                / "production"
                / "board_ui"
                / "qa-report.json"
            ).read_text(encoding="utf-8")
        )
        schema = json.loads(
            (ROOT / "schemas/generic-asset-qa-report.schema.json").read_text(encoding="utf-8")
        )
        acceptance_schema = schema["properties"]["acceptance_criteria"]
        self.assertEqual(64, acceptance_schema["maxItems"])
        self.assertEqual(
            64,
            acceptance_schema["items"]["properties"]["evidence_hashes"]["maxItems"],
        )
        self.assertEqual(
            63,
            acceptance_schema["items"]["properties"]["criterion_index"]["maximum"],
        )

        maximum_criteria = copy.deepcopy(report)
        maximum_criteria["acceptance_criteria"] = [
            {
                "criterion_index": index,
                "criterion_sha256": hashlib.sha256(f"criterion:{index}".encode()).hexdigest(),
                "status": "passed",
                "evidence_hashes": [f"{index + 1:064x}"],
            }
            for index in range(64)
        ]
        maximum_criteria["content_hash"] = _reseal(maximum_criteria)
        self.assertEqual(
            maximum_criteria,
            validate_asset_qa_report_document(maximum_criteria),
        )

        oversized_criteria = copy.deepcopy(maximum_criteria)
        oversized_criteria["acceptance_criteria"].append(
            {
                "criterion_index": 64,
                "criterion_sha256": hashlib.sha256(b"criterion:64").hexdigest(),
                "status": "passed",
                "evidence_hashes": [f"{65:064x}"],
            }
        )
        oversized_criteria["content_hash"] = _reseal(oversized_criteria)
        with self.assertRaisesRegex(GenericAssetProcessingError, "qa_contract_limit"):
            validate_asset_qa_report_document(oversized_criteria)

        maximum_evidence = copy.deepcopy(report)
        maximum_evidence["acceptance_criteria"][0]["evidence_hashes"] = [
            f"{index + 1:064x}" for index in range(64)
        ]
        maximum_evidence["content_hash"] = _reseal(maximum_evidence)
        self.assertEqual(
            maximum_evidence,
            validate_asset_qa_report_document(maximum_evidence),
        )

        oversized_evidence = copy.deepcopy(maximum_evidence)
        oversized_evidence["acceptance_criteria"][0]["evidence_hashes"].append(f"{65:064x}")
        oversized_evidence["content_hash"] = _reseal(oversized_evidence)
        with self.assertRaisesRegex(GenericAssetProcessingError, "qa_contract_limit"):
            validate_asset_qa_report_document(oversized_evidence)

    def test_qa_binds_media_checks_criteria_hashes_and_exact_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-qa-") as temporary:
            root = Path(temporary)
            chain = _build_processing_chain(_media_matrix_cases()[0], root)
            report = chain["qa_report"]
            self.assertEqual(
                [check["check_id"] for check in report["outputs"][0]["checks"][:4]],
                ["hash", "media", "path", "license"],
            )
            applicable = [
                check
                for check in report["outputs"][0]["checks"]
                if check["status"] != "not_applicable"
            ]
            self.assertTrue(applicable)
            self.assertTrue(all(check["status"] == "passed" for check in applicable))
            invalids = []
            bad_criterion = copy.deepcopy(report)
            bad_criterion["acceptance_criteria"][0]["criterion_sha256"] = "0" * 64
            invalids.append(bad_criterion)
            bad_applicability = copy.deepcopy(report)
            bad_applicability["outputs"][0]["checks"][0]["status"] = "not_applicable"
            invalids.append(bad_applicability)
            bad_status = copy.deepcopy(report)
            bad_status["status"] = "failed"
            invalids.append(bad_status)
            extra_blocker = copy.deepcopy(report)
            extra_blocker["blockers"] = ["fabricated_blocker"]
            invalids.append(extra_blocker)
            for invalid in invalids:
                invalid["content_hash"] = _reseal(invalid)
                with self.subTest(mutation=invalids.index(invalid)):
                    with self.assertRaises(GenericAssetProcessingError):
                        validate_asset_qa_report(
                            invalid,
                            recipe=chain["recipe"],
                            processing_receipt=chain["processing_receipt"],
                            **_d2a_kwargs(chain, root),
                        )

    def test_manifest_states_are_exact_and_release_ready_is_not_sealed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-manifest-") as temporary:
            root = Path(temporary)
            chain = _build_processing_chain(_media_matrix_cases()[0], root)
            base = {
                "gamepack": chain["gamepack"],
                "subject": chain["subject"],
                "target": chain["target"],
                "style": chain["style"],
                "inventory": chain["inventory"],
                "asset_records": [chain["record"]],
                "artifact_root": root,
            }
            produced_record = {
                **chain["record"],
                "recipe": None,
                "processing_receipt": None,
                "qa_report": None,
            }
            produced = build_asset_manifest(
                manifest_id="produced_manifest",
                state="produced",
                **{**base, "asset_records": [produced_record]},
            )
            processed_record = {**chain["record"], "qa_report": None}
            processed = build_asset_manifest(
                manifest_id="processed_manifest",
                state="processed",
                **{**base, "asset_records": [processed_record]},
            )
            release_ready = chain["manifest"]
            self.assertEqual(
                [produced["state"], processed["state"], release_ready["state"]],
                ["produced", "processed", "release_ready"],
            )
            self.assertNotIn("sealed", json.dumps(release_ready, sort_keys=True))
            self.assertNotIn("runtime_compatible", release_ready)

            with self.assertRaisesRegex(
                GenericAssetProcessingError,
                "manifest_inventory_incomplete",
            ):
                build_asset_manifest(
                    manifest_id="missing_manifest",
                    state="produced",
                    **{**base, "asset_records": []},
                )

            premature_record = {**chain["record"], "qa_report": None}
            with self.assertRaises(GenericAssetProcessingError):
                build_asset_manifest(
                    manifest_id="premature_manifest",
                    state="release_ready",
                    **{**base, "asset_records": [premature_record]},
                )

            crossed = copy.deepcopy(release_ready)
            crossed["assets"][0]["processing_receipt"]["content_hash"] = "0" * 64
            crossed["content_hash"] = _reseal(crossed)
            self.assertEqual(validate_asset_manifest_document(crossed), crossed)
            with self.assertRaises(GenericAssetProcessingError):
                validate_asset_manifest(crossed, **base)

    def test_contract_serialization_loading_and_publication_are_symmetric(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d2b-io-") as temporary:
            root = Path(temporary)
            artifact_root = root / "artifacts"
            artifact_root.mkdir()
            chain = _build_processing_chain(_media_matrix_cases()[0], artifact_root)
            dependencies = _d2a_kwargs(chain, artifact_root)
            manifest_dependencies = {
                "gamepack": chain["gamepack"],
                "subject": chain["subject"],
                "target": chain["target"],
                "style": chain["style"],
                "inventory": chain["inventory"],
                "asset_records": [chain["record"]],
                "artifact_root": artifact_root,
            }
            cases = (
                (
                    chain["recipe"],
                    publish_asset_processing_recipe,
                    load_asset_processing_recipe,
                    dependencies,
                ),
                (
                    chain["processing_receipt"],
                    publish_asset_processing_receipt,
                    load_asset_processing_receipt,
                    {"recipe": chain["recipe"], **dependencies},
                ),
                (
                    chain["qa_report"],
                    publish_asset_qa_report,
                    load_asset_qa_report,
                    {
                        "recipe": chain["recipe"],
                        "processing_receipt": chain["processing_receipt"],
                        **dependencies,
                    },
                ),
                (
                    chain["manifest"],
                    publish_asset_manifest,
                    load_asset_manifest,
                    manifest_dependencies,
                ),
            )
            for index, (document, publisher, loader, kwargs) in enumerate(cases):
                with self.subTest(format=document["format"]):
                    destination = root / f"{index:02d}.json"
                    published = publisher(destination, document, **kwargs)
                    self.assertEqual(
                        destination.read_bytes(),
                        serialize_asset_processing_contract(document),
                    )
                    self.assertEqual(loader(destination, **kwargs), document)
                    self.assertEqual(published.content_hash, document["content_hash"])
                    with self.assertRaises(GenericAssetProcessingError):
                        publisher(destination, document, **kwargs)

    def test_format_constants_are_distinct_additive_v1_contracts(self) -> None:
        self.assertEqual(
            {
                ASSET_PROCESSING_RECIPE_FORMAT,
                ASSET_PROCESSING_RECEIPT_FORMAT,
                ASSET_QA_REPORT_FORMAT,
                ASSET_MANIFEST_FORMAT,
            },
            {
                "world-forge.asset_processing_recipe",
                "world-forge.asset_processing_receipt",
                "world-forge.asset_qa_report",
                "world-forge.asset_manifest",
            },
        )


if __name__ == "__main__":
    unittest.main()
