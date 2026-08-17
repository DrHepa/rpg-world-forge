from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from isoworld.content.file_stat import descriptor_file_stat
from tests.test_m5_asset_io import _PosixBackedWindowsStageApi
from worldforge import creation_scaffold as creation_scaffold_module
from worldforge import directory_publish as directory_publish_module
from worldforge import generic_assetpack as generic_assetpack_module
from worldforge.__main__ import _resolve_generic_assetpack_cli_source, main
from worldforge.creation_contracts import (
    CreationContractError,
    canonical_creation_hash,
    load_creation_project,
)
from worldforge.creation_readiness import (
    CreationReadinessError,
    build_creation_handoff,
    build_creation_readiness,
    validate_creation_handoff,
    validate_creation_readiness,
)
from worldforge.creation_route import (
    CreationRouteError,
    route_creation_project,
)
from worldforge.creation_scaffold import (
    CreationScaffoldError,
    create_creation_project,
)
from worldforge.creation_workflow import (
    CreationWorkflowError,
    complete_creation_phase,
    initial_creation_workflow_status,
    load_creation_workflow_status,
    reconcile_creation_workflow,
    reopen_creation_phase,
)
from worldforge.game_analysis import analyze_gamepack
from worldforge.gamepack import build_gamepack
from worldforge.generic_assetpack import build_generic_assetpack_manifest
from worldforge.phase_report_v3 import (
    PhaseReportV3Error,
    build_phase_output_evidence_v2,
    build_phase_report_v3,
    document_identity,
    validate_artifact_documents,
    validate_phase_report_v3,
)
from worldforge.repository_boundary import repository_kind

ROOT = Path(__file__).resolve().parents[1]
SYSTEMIC_ROOT = ROOT / "examples/multigenre-contracts/systemic-simulation"
SYSTEMIC_PROJECT = SYSTEMIC_ROOT / "project.json"
PUZZLE_ROOT = ROOT / "examples/multigenre-contracts/abstract-puzzle"
PUZZLE_PROJECT = PUZZLE_ROOT / "project.json"


def _reseal(document: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(document)
    result["content_hash"] = canonical_creation_hash(result)
    return result


def _identity(document: dict[str, object]) -> dict[str, object]:
    return document_identity(document)


def _puzzle_asset_graph() -> tuple[dict[str, object], ...]:
    source = _resolve_generic_assetpack_cli_source(PUZZLE_ROOT / "assets/manifest.json")
    assetpack = build_generic_assetpack_manifest(**source)
    records = []
    for record in source["asset_records"]:
        records.extend(
            (
                record["specification"],
                record["request"],
                record["receipt"],
                record["selection"],
                record["provenance"],
                *record["license_records"],
                record["recipe"],
                record["processing_receipt"],
                record["qa_report"],
            )
        )
    return (
        source["gamepack"],
        source["subject"],
        source["target"],
        source["style"],
        source["inventory"],
        *records,
        source["manifest"],
        assetpack,
    )


def _clone_contract(
    document: dict[str, object],
    *,
    id_field: str,
    suffix: str = "_alternate",
) -> dict[str, object]:
    cloned = copy.deepcopy(document)
    cloned[id_field] = f"{cloned[id_field]}{suffix}"
    return _reseal(cloned)


def _copy_project(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    return destination


def _write_profile(root: Path, profile: dict[str, object]) -> None:
    profile = _reseal(profile)
    (root / "profile.json").write_text(
        json.dumps(profile, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads((root / "source/manifest.json").read_text(encoding="utf-8"))
    manifest["profile"]["content_hash"] = profile["content_hash"]
    manifest = _reseal(manifest)
    (root / "source/manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    project = json.loads((root / "project.json").read_text(encoding="utf-8"))
    project["profile"]["content_hash"] = profile["content_hash"]
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project = _reseal(project)
    (root / "project.json").write_text(
        json.dumps(project, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _authoring_only_runtime_absence_project(
    root: Path,
    *,
    asset_content_mode: str = "not_applicable",
) -> object:
    create_creation_project(
        root,
        project_id="authoring_only_runtime_absence",
        title="Authoring-only runtime absence",
        project_kind="game",
        gameplay_family="puzzle",
        initial_core_verb="solve",
        initial_core_loop="inspect, act, and review deterministic feedback",
        world_presence="none",
        narrative_requirement="none",
        narrative_authorship="none",
        narrative_topology="none",
        presentation_mode="2d",
        runtime_support_intent="authoring_only",
        asset_content_mode=asset_content_mode,
    )
    return load_creation_project(root / "project.json")


def _non_game_creation_project(root: Path, *, project_kind: str) -> object:
    create_creation_project(
        root,
        project_id=f"{project_kind}_runtime_absence",
        title=f"{project_kind} runtime absence",
        project_kind=project_kind,
    )
    return load_creation_project(root / "project.json")


def _runtime_support_report_artifact() -> dict[str, object]:
    return json.loads((PUZZLE_ROOT / "runtime/support-report.json").read_text(encoding="utf-8"))


def _generic_report(
    loaded: object,
    *,
    phase: str,
    role: str | None,
    subject: dict[str, object] | None,
    status: str = "ready",
    rationale_code: str = "phase_ready",
    artifact_registry: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    reviewer = {"id": "lead_reviewer", "role": "validation_analyst"}
    output = None
    if status == "ready":
        assert role is not None and subject is not None
        output = build_phase_output_evidence_v2(
            evidence_id=f"{phase}_output",
            phase=phase,
            role=role,
            subject=_identity(subject),
            reviewer_id=reviewer["id"],
            reviewer_role=reviewer["role"],
            artifact_registry=artifact_registry,
            source_project=loaded,
        )
    evidence_subject = loaded.profile if subject is None else subject
    return build_phase_report_v3(
        loaded,
        phase=phase,
        status=status,
        rationale_code=rationale_code,
        rationale_message=f"Reviewed {phase}.",
        evidence=(
            {
                "evidence_id": "reviewed_subject",
                "claim": "The exact subject was reviewed.",
                "subject": _identity(evidence_subject),
            },
        ),
        output_evidence=output,
        reviewer_id=reviewer["id"],
        reviewer_role=reviewer["role"],
        invalidation_dependencies=None,
        artifact_registry=artifact_registry,
    )


class CreationScaffoldAndRoutingTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "requires POSIX descriptor-backed Windows seam")
    def test_windows_retained_stage_omits_delete_while_named_verification_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stage = root / ".creation-stage"
            parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            info = root.stat()
            parent_identity = (info.st_dev, info.st_ino)
            api = _PosixBackedWindowsStageApi()
            parent = SimpleNamespace(
                parent_fd=None,
                windows_api=api,
                windows_parent_handle=parent_fd,
                identities=(parent_identity,),
                assert_current=lambda: None,
            )

            @contextlib.contextmanager
            def open_parent(_path: Path):
                yield parent

            files = {"source/manifest.json": b"{}\n"}
            try:
                with (
                    mock.patch.object(
                        directory_publish_module,
                        "open_verified_output_parent",
                        side_effect=open_parent,
                    ),
                    mock.patch.object(
                        directory_publish_module,
                        "windows_handle_file_stat",
                        side_effect=descriptor_file_stat,
                    ),
                    directory_publish_module.create_retained_stage(
                        stage,
                        expected_parent_identity=parent_identity,
                    ) as writer,
                ):
                    writer.write_file("source/manifest.json", files["source/manifest.json"])
                    writer.fsync()
                    creation_scaffold_module._verify_exact_tree(stage, files)  # noqa: SLF001
                    writer.require_binding()
                    self.assertEqual(
                        files["source/manifest.json"],
                        (stage / "source/manifest.json").read_bytes(),
                    )
            finally:
                os.close(parent_fd)

            self.assertEqual(
                [
                    ("directory", stage.name, False),
                    ("directory", "source", False),
                    ("file", "manifest.json", False),
                ],
                api.creations,
            )

    def test_scaffold_stage_write_failure_has_a_bounded_reason_and_no_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "bounded-stage-write"
            cleanup = mock.Mock()
            with (
                mock.patch.object(
                    directory_publish_module.RetainedStageWriter,
                    "write_file",
                    side_effect=directory_publish_module.DirectoryPublishError(
                        r"private path C:\Users\runner"
                    ),
                ),
                mock.patch.object(
                    creation_scaffold_module,
                    "quarantine_and_remove_verified_directory",
                    cleanup,
                ),
                self.assertRaises(CreationScaffoldError) as raised,
            ):
                create_creation_project(
                    target,
                    project_id="bounded_stage_write",
                    title="Bounded stage write",
                )

            self.assertEqual(
                "creation_scaffold_stage_write_failed",
                raised.exception.reason_code,
            )
            self.assertEqual("creation scaffold stage write failed", raised.exception.detail)
            self.assertFalse(target.exists())
            self.assertEqual(1, cleanup.call_count)
            self.assertEqual(target.parent, cleanup.call_args.args[0].parent)
            self.assertIsInstance(cleanup.call_args.args[1], tuple)
            self.assertIn("verify", cleanup.call_args.kwargs)

    def test_scaffold_publish_failure_cleans_exact_stage_transactionally(self) -> None:
        @contextlib.contextmanager
        def fail_publish(*_args: object, **_kwargs: object):
            raise directory_publish_module.DirectoryPublishError(r"private path C:\Users\runner")
            yield

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "bounded-publish"
            cleanup = mock.Mock()
            with (
                mock.patch.object(
                    creation_scaffold_module,
                    "publish_directory_noreplace",
                    side_effect=fail_publish,
                ),
                mock.patch.object(
                    creation_scaffold_module,
                    "quarantine_and_remove_verified_directory",
                    cleanup,
                ),
                self.assertRaises(CreationScaffoldError) as raised,
            ):
                create_creation_project(
                    target,
                    project_id="bounded_publish",
                    title="Bounded publish",
                )

            self.assertEqual(
                "creation_scaffold_publish_failed",
                raised.exception.reason_code,
            )
            self.assertEqual(
                "creation project target already exists or publication failed",
                raised.exception.detail,
            )
            self.assertFalse(target.exists())
            self.assertEqual(1, cleanup.call_count)
            self.assertEqual(target.parent, cleanup.call_args.args[0].parent)
            self.assertIsInstance(cleanup.call_args.args[1], tuple)
            self.assertIn("verify", cleanup.call_args.kwargs)

    def test_scaffold_operation_failures_classify_every_bounded_boundary(self) -> None:
        cases = {
            "creation_scaffold_stage_create_failed": "creation scaffold stage creation failed",
            "creation_scaffold_stage_write_failed": "creation scaffold stage write failed",
            "creation_scaffold_stage_flush_failed": "creation scaffold stage flush failed",
            "creation_scaffold_stage_verify_failed": (
                "creation scaffold stage verification failed"
            ),
            "creation_scaffold_publish_failed": (
                "creation project target already exists or publication failed"
            ),
            "creation_scaffold_published_verify_failed": (
                "published creation scaffold verification failed"
            ),
            "creation_scaffold_parent_flush_failed": ("creation scaffold parent flush failed"),
            "creation_scaffold_finalize_failed": "creation scaffold finalization failed",
        }
        real_verify = creation_scaffold_module._verify_exact_tree  # noqa: SLF001
        real_directory_identity = creation_scaffold_module.directory_identity

        @contextlib.contextmanager
        def fail_context(*_args: object, **_kwargs: object):
            raise directory_publish_module.DirectoryPublishError(r"private path C:\Users\runner")
            yield

        for expected_reason, expected_detail in cases.items():
            with self.subTest(reason_code=expected_reason):
                with tempfile.TemporaryDirectory() as temp:
                    target = Path(temp) / expected_reason

                    def fail_published_verify(
                        root: Path,
                        files: dict[str, bytes],
                        target: Path = target,
                    ) -> None:
                        if root == target:
                            raise directory_publish_module.DirectoryPublishError(
                                r"private path C:\Users\runner"
                            )
                        real_verify(root, files)

                    def fail_final_identity(path: Path, *, context: str) -> tuple[int, int]:
                        if context == "published creation root":
                            raise directory_publish_module.DirectoryPublishError(
                                r"private path C:\Users\runner"
                            )
                        return real_directory_identity(path, context=context)

                    with contextlib.ExitStack() as stack:
                        stack.enter_context(
                            mock.patch.object(
                                creation_scaffold_module,
                                "quarantine_and_remove_verified_directory",
                            )
                        )
                        if expected_reason == "creation_scaffold_stage_create_failed":
                            stack.enter_context(
                                mock.patch.object(
                                    creation_scaffold_module,
                                    "create_retained_stage",
                                    side_effect=fail_context,
                                )
                            )
                        elif expected_reason == "creation_scaffold_stage_write_failed":
                            stack.enter_context(
                                mock.patch.object(
                                    directory_publish_module.RetainedStageWriter,
                                    "write_file",
                                    side_effect=directory_publish_module.DirectoryPublishError(
                                        r"private path C:\Users\runner"
                                    ),
                                )
                            )
                        elif expected_reason == "creation_scaffold_stage_flush_failed":
                            stack.enter_context(
                                mock.patch.object(
                                    directory_publish_module.RetainedStageWriter,
                                    "fsync",
                                    side_effect=directory_publish_module.DirectoryPublishError(
                                        r"private path C:\Users\runner"
                                    ),
                                )
                            )
                        elif expected_reason == "creation_scaffold_stage_verify_failed":
                            stack.enter_context(
                                mock.patch.object(
                                    creation_scaffold_module,
                                    "_verify_exact_tree",
                                    side_effect=directory_publish_module.DirectoryPublishError(
                                        r"private path C:\Users\runner"
                                    ),
                                )
                            )
                        elif expected_reason == "creation_scaffold_publish_failed":
                            stack.enter_context(
                                mock.patch.object(
                                    creation_scaffold_module,
                                    "publish_directory_noreplace",
                                    side_effect=fail_context,
                                )
                            )
                        elif expected_reason == "creation_scaffold_published_verify_failed":
                            stack.enter_context(
                                mock.patch.object(
                                    creation_scaffold_module,
                                    "_verify_exact_tree",
                                    side_effect=fail_published_verify,
                                )
                            )
                        elif expected_reason == "creation_scaffold_parent_flush_failed":
                            stack.enter_context(
                                mock.patch.object(
                                    creation_scaffold_module,
                                    "fsync_directory",
                                    side_effect=directory_publish_module.DirectoryPublishError(
                                        r"private path C:\Users\runner"
                                    ),
                                )
                            )
                        else:
                            stack.enter_context(
                                mock.patch.object(
                                    creation_scaffold_module,
                                    "directory_identity",
                                    side_effect=fail_final_identity,
                                )
                            )
                        with self.assertRaises(CreationScaffoldError) as raised:
                            create_creation_project(
                                target,
                                project_id="bounded_boundary",
                                title="Bounded boundary",
                            )

                    self.assertEqual(expected_reason, raised.exception.reason_code)
                    self.assertEqual(expected_detail, raised.exception.detail)
                    self.assertNotIn("Users", raised.exception.detail)

    def test_scaffold_is_transactional_valid_and_exclusively_published(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "neutral-creation"

            project_path = create_creation_project(
                target,
                project_id="neutral_creation",
                title="Neutral creation",
                default_locale="en",
            )
            loaded = load_creation_project(project_path)

            self.assertEqual(target / "project.json", project_path)
            self.assertEqual("neutral_creation", loaded.project["project_id"])
            self.assertEqual("generic", route_creation_project(target))
            self.assertEqual("creation", repository_kind(target))
            self.assertEqual(
                "p00_brief",
                load_creation_workflow_status(target)["current_phase"],
            )

            with self.assertRaisesRegex(CreationScaffoldError, "already exists"):
                create_creation_project(
                    target,
                    project_id="other_creation",
                    title="Other",
                    default_locale="en",
                )

    def test_scaffold_accepts_a_maximum_length_project_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "maximum-id"
            project_id = "p" + ("a" * 63)

            create_creation_project(
                target,
                project_id=project_id,
                title="Maximum identifier",
                default_locale="en",
            )

            loaded = load_creation_project(target / "project.json")
            self.assertEqual(project_id, loaded.project["project_id"])
            self.assertEqual(project_id, loaded.profile["profile_id"])

    def test_concurrent_scaffold_has_one_winner_and_no_partial_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "race"

            def create() -> tuple[str, CreationScaffoldError | None]:
                try:
                    create_creation_project(
                        target,
                        project_id="creation_race",
                        title="Creation race",
                        default_locale="en",
                    )
                except CreationScaffoldError as exc:
                    return "lost", exc
                return "won", None

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = sorted(
                    executor.map(lambda _index: create(), range(2)),
                    key=lambda item: item[0],
                )

            self.assertEqual(["lost", "won"], [status for status, _notes in outcomes])
            self.assertEqual(
                "creation_race",
                load_creation_project(target / "project.json").project["project_id"],
            )
            retained_stages = tuple(target.parent.glob(".race.creation-stage-*"))
            if sys.platform.startswith("linux") and os.name == "posix":
                self.assertEqual(1, len(retained_stages))
                loser = next(error for status, error in outcomes if status == "lost")
                self.assertIsNotNone(loser)
                assert loser is not None
                self.assertEqual(
                    "creation_scaffold_recovery_required",
                    loser.reason_code,
                )
                self.assertEqual(
                    retained_stages[0].name,
                    loser.recovery_evidence["stage"]["locator"],
                )
            else:
                self.assertFalse(retained_stages)

    def test_routing_uses_exact_project_format_and_ignores_fiction_genre(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = _copy_project(PUZZLE_ROOT, Path(temp) / "generic")
            self.assertEqual("generic", route_creation_project(root))

            profile_path = root / "profile.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["fiction"]["genres"] = ["historical"]
            profile = _reseal(profile)
            profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
            manifest_path = root / "source/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["profile"]["content_hash"] = profile["content_hash"]
            manifest = _reseal(manifest)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            project_path = root / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["profile"]["content_hash"] = profile["content_hash"]
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _reseal(project)
            project_path.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")

            self.assertEqual("generic", route_creation_project(root))
            self.assertEqual(
                "historical",
                load_creation_project(root / "project.json").profile["fiction"]["genres"][0],
            )

            malformed = Path(temp) / "malformed"
            malformed.mkdir()
            (malformed / "project.json").write_text(
                '{"format":"rpg-world-forge.project","format_version":1}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CreationRouteError, "invalid generic creation project"):
                route_creation_project(malformed)


class PhaseReportV3Tests(unittest.TestCase):
    def test_asset_evidence_rejects_a_foreign_target_without_its_closed_lineage(self) -> None:
        systemic = load_creation_project(SYSTEMIC_PROJECT)
        target = json.loads((PUZZLE_ROOT / "assets/target.json").read_text(encoding="utf-8"))

        with self.assertRaisesRegex(
            PhaseReportV3Error,
            "resolve|lineage|project|gamepack|subject",
        ):
            _generic_report(
                systemic,
                phase="p11_art_audio",
                role="presentation_direction",
                subject=target,
                artifact_registry=(target,),
            )

    def test_crossed_asset_predecessor_matrix_fails_closed(self) -> None:
        loaded = load_creation_project(PUZZLE_PROJECT)
        graph = _puzzle_asset_graph()
        by_format = {str(document["format"]): document for document in graph}
        ordered_formats = (
            "world-forge.gamepack",
            "world-forge.asset_subject",
            "world-forge.asset_target",
            "world-forge.asset_style",
            "world-forge.asset_inventory",
            "world-forge.asset_spec",
            "world-forge.asset_production_request",
            "world-forge.asset_production_receipt",
            "world-forge.asset_selection",
            "world-forge.asset_provenance_record",
            "world-forge.asset_license_record",
            "world-forge.asset_processing_recipe",
            "world-forge.asset_processing_receipt",
            "world-forge.asset_qa_report",
            "world-forge.asset_manifest",
            "world-forge.assetpack",
        )

        def through(format_name: str) -> tuple[dict[str, object], ...]:
            end = ordered_formats.index(format_name) + 1
            return tuple(by_format[item] for item in ordered_formats[:end])

        request = by_format["world-forge.asset_production_request"]
        receipt = by_format["world-forge.asset_production_receipt"]
        selection = by_format["world-forge.asset_selection"]
        provenance = by_format["world-forge.asset_provenance_record"]
        license_record = by_format["world-forge.asset_license_record"]
        recipe = by_format["world-forge.asset_processing_recipe"]
        processing = by_format["world-forge.asset_processing_receipt"]
        qa_report = by_format["world-forge.asset_qa_report"]
        manifest = by_format["world-forge.asset_manifest"]
        assetpack = by_format["world-forge.assetpack"]

        request_b = _clone_contract(request, id_field="request_id")

        receipt_with_crossed_parent = _clone_contract(receipt, id_field="receipt_id")
        receipt_with_crossed_parent["request"] = _identity(request_b)
        receipt_with_crossed_parent["lineage_parents"] = [
            {
                "receipt_id": receipt["receipt_id"],
                "content_hash": receipt["content_hash"],
            }
        ]
        receipt_with_crossed_parent = _reseal(receipt_with_crossed_parent)

        selection_crossed = copy.deepcopy(selection)
        selection_crossed["request"] = _identity(request_b)
        selection_crossed = _reseal(selection_crossed)

        receipt_b = _clone_contract(receipt, id_field="receipt_id", suffix="_rejected")
        receipt_b["request"] = _identity(request_b)
        receipt_b = _reseal(receipt_b)
        rejected_selection = copy.deepcopy(selection)
        rejected_selection["rejected_candidates"] = [
            {
                "candidate_artifact_id": "rejected_candidate",
                "reason_code": "not_selected",
                "receipt": _identity(receipt_b),
            }
        ]
        rejected_selection["receipt_lineage"]["closures"].append(
            {"root": _identity(receipt_b), "parents": []}
        )
        rejected_selection["receipt_lineage"]["closures"].sort(
            key=lambda item: item["root"]["id"].encode("utf-8")
        )
        rejected_selection = _reseal(rejected_selection)

        selection_b = _clone_contract(selection, id_field="selection_id")
        provenance_crossed = copy.deepcopy(provenance)
        provenance_crossed["request"] = _identity(request_b)
        provenance_crossed["selection"] = _identity(selection_b)
        provenance_crossed = _reseal(provenance_crossed)

        provenance_b = _clone_contract(provenance, id_field="provenance_id")
        license_crossed = copy.deepcopy(license_record)
        license_crossed["selection"] = _identity(selection_b)
        license_crossed["provenance"] = _identity(provenance_b)
        license_crossed = _reseal(license_crossed)

        license_b = _clone_contract(license_record, id_field="license_record_id")
        recipe_crossed = copy.deepcopy(recipe)
        recipe_crossed["provenance"] = _identity(provenance_b)
        recipe_crossed["licenses"][0]["license_record"].update(_identity(license_b))
        recipe_crossed["steps"][0]["license_record"].update(_identity(license_b))
        recipe_crossed = _reseal(recipe_crossed)

        recipe_b = _clone_contract(recipe, id_field="recipe_id")
        processing_crossed = copy.deepcopy(processing)
        processing_crossed["provenance"] = _identity(provenance_b)
        processing_crossed["recipe"] = _identity(recipe_b)
        processing_crossed = _reseal(processing_crossed)

        processing_b = _clone_contract(
            processing,
            id_field="processing_receipt_id",
        )
        qa_crossed = copy.deepcopy(qa_report)
        qa_crossed["recipe"] = _identity(recipe_b)
        qa_crossed["processing_receipt"] = _identity(processing_b)
        qa_crossed = _reseal(qa_crossed)

        qa_b = _clone_contract(qa_report, id_field="qa_report_id")
        manifest_crossed = copy.deepcopy(manifest)
        manifest_crossed["assets"][0]["processing_receipt"] = _identity(processing_b)
        manifest_crossed["assets"][0]["qa_report"] = _identity(qa_b)
        manifest_crossed = _reseal(manifest_crossed)

        manifest_b = _clone_contract(manifest, id_field="manifest_id")
        manifest_b["assets"][0]["qa_report"] = _identity(qa_b)
        manifest_b = _reseal(manifest_b)
        assetpack_crossed = copy.deepcopy(assetpack)
        assetpack_crossed["release_ready_manifest"] = _identity(manifest_b)
        assetpack_crossed["assetpack_id"] = generic_assetpack_module._derived_assetpack_id(
            assetpack_crossed
        )
        assetpack_crossed = _reseal(assetpack_crossed)

        cases = {
            "receipt_parent": (
                *through("world-forge.asset_production_receipt"),
                request_b,
                receipt_with_crossed_parent,
            ),
            "selection_request_receipt": (
                *through("world-forge.asset_production_receipt"),
                request_b,
                selection_crossed,
            ),
            "selection_rejected_receipt": (
                *through("world-forge.asset_production_receipt"),
                request_b,
                receipt_b,
                rejected_selection,
            ),
            "provenance_selection": (
                *through("world-forge.asset_production_receipt"),
                request_b,
                selection_b,
                provenance_crossed,
            ),
            "license_provenance": (
                *through("world-forge.asset_provenance_record"),
                selection_b,
                provenance_b,
                license_crossed,
            ),
            "recipe_license": (
                *through("world-forge.asset_provenance_record"),
                provenance_b,
                license_b,
                recipe_crossed,
            ),
            "processing_recipe": (
                *through("world-forge.asset_license_record"),
                provenance_b,
                recipe_b,
                processing_crossed,
            ),
            "qa_processing": (
                *through("world-forge.asset_processing_recipe"),
                recipe_b,
                processing_b,
                qa_crossed,
            ),
            "manifest_nested": (
                *through("world-forge.asset_processing_receipt"),
                processing_b,
                qa_b,
                manifest_crossed,
            ),
            "assetpack_manifest": (
                *through("world-forge.asset_manifest"),
                qa_b,
                manifest_b,
                assetpack_crossed,
            ),
        }
        for name, registry in cases.items():
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(
                    PhaseReportV3Error,
                    "lineage|predecessor|parent|manifest",
                ),
            ):
                validate_artifact_documents(loaded, registry)

    def test_asset_semantic_continuity_mutation_matrix_fails_closed(self) -> None:
        loaded = load_creation_project(PUZZLE_PROJECT)
        graph = _puzzle_asset_graph()
        by_format = {str(document["format"]): document for document in graph}
        ordered_formats = tuple(str(document["format"]) for document in graph)
        recipe = by_format["world-forge.asset_processing_recipe"]
        processing = by_format["world-forge.asset_processing_receipt"]
        qa_report = by_format["world-forge.asset_qa_report"]
        specification = by_format["world-forge.asset_spec"]
        assetpack = by_format["world-forge.assetpack"]
        license_record = by_format["world-forge.asset_license_record"]

        self.assertEqual(len(graph), len(validate_artifact_documents(loaded, graph)))

        def through(
            format_name: str,
            replacement: dict[str, object],
            *extra: dict[str, object],
        ) -> tuple[dict[str, object], ...]:
            end = ordered_formats.index(format_name)
            return (*graph[:end], *extra, replacement)

        def audio_processing_output() -> dict[str, object]:
            return {
                "step_id": "step_alternate_audio",
                "candidate_artifact_id": "alternate_audio_candidate",
                "source_sha256": "1" * 64,
                "role": "audio",
                "media_type": "audio/wav",
                "runtime_path": "assets/audio/alternate.wav",
                "locator": "assets/production/alternate/processed/audio.wav",
                "sha256": "2" * 64,
                "size_bytes": 64,
                "metadata": {
                    "kind": "wav_pcm16",
                    "channels": 1,
                    "sample_rate": 8000,
                    "frames": 10,
                    "sample_width": 2,
                },
            }

        processing_mutations: dict[str, dict[str, object]] = {}
        for field, value in (
            ("step_id", "step_crossed"),
            ("candidate_artifact_id", "candidate_crossed"),
            ("source_sha256", "3" * 64),
            ("runtime_path", "assets/ui/crossed.png"),
            ("locator", "assets/production/board_ui/processed/texture/crossed.png"),
        ):
            mutated = copy.deepcopy(processing)
            mutated["outputs"][0][field] = value
            processing_mutations[field] = _reseal(mutated)
        role_media = copy.deepcopy(processing)
        role_media["outputs"] = [audio_processing_output()]
        processing_mutations["role_media_type"] = _reseal(role_media)
        extra_output = copy.deepcopy(processing)
        extra_output["outputs"] = [
            audio_processing_output(),
            copy.deepcopy(processing["outputs"][0]),
        ]
        processing_mutations["count_order"] = _reseal(extra_output)

        failed_processing = copy.deepcopy(processing)
        failed_processing["status"] = "failed"
        failed_processing["outputs"] = []
        failed_processing["failure_reasons"] = ["processing_interrupted"]
        failed_processing["recovery"] = _reseal(
            {
                "failure_code": "processing_interrupted",
                "recipe": _identity(recipe),
                "retained_artifacts": [],
            }
        )
        failed_processing = _reseal(failed_processing)
        self.assertEqual(
            failed_processing,
            validate_artifact_documents(
                loaded,
                through(
                    "world-forge.asset_processing_receipt",
                    failed_processing,
                ),
            )[-1],
        )
        crossed_recovery = copy.deepcopy(failed_processing)
        retained = copy.deepcopy(processing["outputs"][0])
        retained["runtime_path"] = "assets/ui/recovery-crossed.png"
        crossed_recovery["recovery"]["retained_artifacts"] = [retained]
        crossed_recovery["recovery"] = _reseal(crossed_recovery["recovery"])
        processing_mutations["recovery"] = _reseal(crossed_recovery)

        for name, mutated in processing_mutations.items():
            with (
                self.subTest(stage="processing", field=name),
                self.assertRaisesRegex(
                    PhaseReportV3Error,
                    "processing|recipe|output|recovery|predecessor",
                ),
            ):
                validate_artifact_documents(
                    loaded,
                    through("world-forge.asset_processing_receipt", mutated),
                )

        def qa_checks(media_check: str) -> list[dict[str, str]]:
            return [
                {
                    "check_id": check_id,
                    "status": (
                        "passed"
                        if check_id in {"hash", "media", "path", "license", media_check}
                        else "not_applicable"
                    ),
                }
                for check_id in (
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
            ]

        def audio_qa_output() -> dict[str, object]:
            output = audio_processing_output()
            return {
                key: output[key]
                for key in (
                    "candidate_artifact_id",
                    "role",
                    "media_type",
                    "runtime_path",
                    "locator",
                    "sha256",
                    "size_bytes",
                    "metadata",
                )
            } | {"checks": qa_checks("wav")}

        qa_mutations: dict[str, dict[str, object]] = {}
        for field, value in (
            ("candidate_artifact_id", "candidate_crossed"),
            ("runtime_path", "assets/ui/crossed.png"),
            ("locator", "assets/production/board_ui/processed/texture/crossed.png"),
            ("sha256", "4" * 64),
            ("size_bytes", 1375),
        ):
            mutated = copy.deepcopy(qa_report)
            mutated["outputs"][0][field] = value
            qa_mutations[field] = _reseal(mutated)
        qa_role_media = copy.deepcopy(qa_report)
        qa_role_media["outputs"] = [audio_qa_output()]
        qa_role_media["multi_output_check"]["roles"] = ["audio"]
        qa_mutations["role_media_type"] = _reseal(qa_role_media)
        qa_metadata = copy.deepcopy(qa_report)
        qa_metadata["outputs"][0]["metadata"]["width"] = 255
        qa_mutations["metadata"] = _reseal(qa_metadata)
        qa_extra_output = copy.deepcopy(qa_report)
        qa_extra_output["outputs"] = [
            audio_qa_output(),
            copy.deepcopy(qa_report["outputs"][0]),
        ]
        qa_extra_output["multi_output_check"] = {
            "status": "passed",
            "roles": ["audio", "texture"],
        }
        qa_mutations["count_order"] = _reseal(qa_extra_output)
        qa_missing_criterion = copy.deepcopy(qa_report)
        qa_missing_criterion["acceptance_criteria"] = qa_missing_criterion["acceptance_criteria"][
            :1
        ]
        qa_mutations["criterion_missing"] = _reseal(qa_missing_criterion)
        qa_extra_criterion = copy.deepcopy(qa_report)
        qa_extra_criterion["acceptance_criteria"].append(
            {
                "criterion_index": 2,
                "criterion_sha256": "5" * 64,
                "status": "passed",
                "evidence_hashes": ["6" * 64],
            }
        )
        qa_mutations["criterion_extra"] = _reseal(qa_extra_criterion)
        qa_criterion_hash = copy.deepcopy(qa_report)
        qa_criterion_hash["acceptance_criteria"][0]["criterion_sha256"] = "7" * 64
        qa_mutations["criterion_hash"] = _reseal(qa_criterion_hash)

        for name, mutated in qa_mutations.items():
            with (
                self.subTest(stage="qa", field=name),
                self.assertRaisesRegex(
                    PhaseReportV3Error,
                    "QA|qa|processing|criterion|output|specification|predecessor",
                ),
            ):
                validate_artifact_documents(
                    loaded,
                    through("world-forge.asset_qa_report", mutated),
                )

        def rebuild_assetpack(mutated: dict[str, object]) -> dict[str, object]:
            files: dict[str, dict[str, object]] = {}
            for asset in mutated["assets"]:
                for output in asset["outputs"]:
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
                        files[path] = {
                            "path": path,
                            "sha256": sha256,
                            "size_bytes": size_bytes,
                        }
            entries = sorted(files.values(), key=lambda item: item["path"].encode())
            mutated["inventory"] = _reseal(
                {
                    "file_count": len(entries),
                    "total_bytes": sum(int(item["size_bytes"]) for item in entries),
                    "files": entries,
                }
            )
            mutated["assetpack_id"] = generic_assetpack_module._derived_assetpack_id(mutated)
            return _reseal(mutated)

        def audio_assetpack_output() -> dict[str, object]:
            output = copy.deepcopy(assetpack["assets"][0]["outputs"][0])
            output.update(
                {
                    "role": "audio",
                    "media_type": "audio/wav",
                    "runtime_path": "assets/audio/alternate.wav",
                    "constraints": {
                        "kind": "wav_pcm16",
                        "channels": 1,
                        "sample_rate": 8000,
                        "frames": 10,
                        "max_bytes": output["size_bytes"],
                    },
                    "metadata": {
                        "kind": "wav_pcm16",
                        "channels": 1,
                        "sample_rate": 8000,
                        "frames": 10,
                        "sample_width": 2,
                    },
                }
            )
            return output

        assetpack_mutations: dict[
            str,
            tuple[dict[str, object], tuple[dict[str, object], ...]],
        ] = {}
        for field, value in (
            ("runtime_path", "assets/ui/crossed.png"),
            ("sha256", "8" * 64),
        ):
            mutated = copy.deepcopy(assetpack)
            mutated["assets"][0]["outputs"][0][field] = value
            assetpack_mutations[field] = (rebuild_assetpack(mutated), ())
        pack_size = copy.deepcopy(assetpack)
        pack_size["assets"][0]["outputs"][0]["size_bytes"] += 1
        pack_size["assets"][0]["outputs"][0]["constraints"]["max_bytes"] += 1
        assetpack_mutations["size_bytes"] = (rebuild_assetpack(pack_size), ())
        pack_role_media = copy.deepcopy(assetpack)
        pack_role_media["assets"][0]["outputs"] = [audio_assetpack_output()]
        assetpack_mutations["role_media_type"] = (
            rebuild_assetpack(pack_role_media),
            (),
        )
        pack_extra = copy.deepcopy(assetpack)
        pack_extra["assets"][0]["outputs"] = [
            audio_assetpack_output(),
            copy.deepcopy(assetpack["assets"][0]["outputs"][0]),
        ]
        assetpack_mutations["role_coverage"] = (rebuild_assetpack(pack_extra), ())
        pack_constraints = copy.deepcopy(assetpack)
        pack_constraints["assets"][0]["outputs"][0]["constraints"]["width"] = 255
        assetpack_mutations["constraints"] = (
            rebuild_assetpack(pack_constraints),
            (),
        )
        pack_metadata = copy.deepcopy(assetpack)
        pack_metadata["assets"][0]["outputs"][0]["metadata"]["width"] = 255
        assetpack_mutations["metadata"] = (rebuild_assetpack(pack_metadata), ())
        pack_notice = copy.deepcopy(assetpack)
        pack_notice["assets"][0]["outputs"][0]["runtime_notice"] = {
            "path": f"notices/{'9' * 64}.txt",
            "sha256": "9" * 64,
            "size_bytes": 70,
        }
        assetpack_mutations["runtime_notice"] = (rebuild_assetpack(pack_notice), ())
        license_b = _clone_contract(
            license_record,
            id_field="license_record_id",
            suffix="_assetpack_crossed",
        )
        pack_license = copy.deepcopy(assetpack)
        pack_license["assets"][0]["licenses"] = [_identity(license_b)]
        pack_license["assets"][0]["outputs"][0]["license_record"] = _identity(license_b)
        assetpack_mutations["license"] = (
            rebuild_assetpack(pack_license),
            (license_b,),
        )

        self.assertEqual(
            specification["outputs"][0]["role"],
            assetpack["assets"][0]["outputs"][0]["role"],
        )
        for name, (mutated, extra) in assetpack_mutations.items():
            with (
                self.subTest(stage="assetpack", field=name),
                self.assertRaisesRegex(
                    PhaseReportV3Error,
                    "assetpack|manifest|output|inventory|license|notice|semantic|lineage",
                ),
            ):
                validate_artifact_documents(
                    loaded,
                    through("world-forge.assetpack", mutated, *extra),
                )

    def test_asset_graph_is_closed_cycle_safe_and_fully_invalidating(self) -> None:
        loaded = load_creation_project(PUZZLE_PROJECT)
        graph = _puzzle_asset_graph()
        by_format = {str(document["format"]): document for document in graph}
        assetpack = by_format["world-forge.assetpack"]
        manifest = by_format["world-forge.asset_manifest"]
        reviewer = {"id": "lead_reviewer", "role": "validation_analyst"}
        output = build_phase_output_evidence_v2(
            evidence_id="asset_plan_output",
            phase="p12_asset_specs",
            role="asset_plan",
            subject=_identity(manifest),
            reviewer_id=reviewer["id"],
            reviewer_role=reviewer["role"],
            source_project=loaded,
            artifact_registry=graph,
        )
        report = build_phase_report_v3(
            loaded,
            phase="p12_asset_specs",
            status="ready",
            rationale_code="phase_ready",
            rationale_message="The complete sealed asset lineage was reviewed.",
            evidence=(
                {
                    "evidence_id": "sealed_assetpack",
                    "claim": "The exact sealed asset graph was reviewed.",
                    "subject": _identity(assetpack),
                },
            ),
            output_evidence=output,
            reviewer_id=reviewer["id"],
            reviewer_role=reviewer["role"],
            invalidation_dependencies=None,
            artifact_registry=graph,
        )
        dependencies = {
            (
                identity["format"],
                identity["format_version"],
                identity["id"],
                identity["content_hash"],
            )
            for identity in report["invalidation_dependencies"]
        }
        self.assertLessEqual(
            {
                (
                    identity["format"],
                    identity["format_version"],
                    identity["id"],
                    identity["content_hash"],
                )
                for identity in (_identity(document) for document in graph)
            },
            dependencies,
        )
        readiness = build_creation_readiness(loaded, artifacts=graph)
        self.assertEqual("sealed", readiness["dimensions"]["assets"])
        self.assertIn(_identity(assetpack), readiness["evidence"])

        without_license = tuple(
            document
            for document in graph
            if document["format"] != "world-forge.asset_license_record"
        )
        with self.assertRaisesRegex(PhaseReportV3Error, "resolve|unknown|license"):
            build_phase_output_evidence_v2(
                evidence_id="incomplete_asset_plan",
                phase="p12_asset_specs",
                role="asset_plan",
                subject=_identity(manifest),
                reviewer_id=reviewer["id"],
                reviewer_role=reviewer["role"],
                source_project=loaded,
                artifact_registry=without_license,
            )

        cyclic_graph = list(copy.deepcopy(graph))
        receipt_index = next(
            index
            for index, document in enumerate(cyclic_graph)
            if document["format"] == "world-forge.asset_production_receipt"
        )
        receipt = cyclic_graph[receipt_index]
        receipt["lineage_parents"] = [_identity(receipt)]
        with self.assertRaisesRegex(PhaseReportV3Error, "cycle"):
            build_phase_output_evidence_v2(
                evidence_id="cyclic_asset_plan",
                phase="p12_asset_specs",
                role="asset_plan",
                subject=_identity(manifest),
                reviewer_id=reviewer["id"],
                reviewer_role=reviewer["role"],
                source_project=loaded,
                artifact_registry=tuple(cyclic_graph),
            )

    def test_external_handoff_requires_integral_validation_not_only_a_matching_hash(self) -> None:
        loaded = load_creation_project(SYSTEMIC_PROJECT)
        readiness = build_creation_readiness(loaded)
        status = initial_creation_workflow_status(loaded)
        handoff = build_creation_handoff(loaded, status=status, readiness=readiness)
        malformed = copy.deepcopy(handoff)
        del malformed["release_blockers"]
        malformed = _reseal(malformed)

        with self.assertRaisesRegex(PhaseReportV3Error, "handoff|artifact"):
            _generic_report(
                loaded,
                phase="p14_handoff",
                role="implementation_handoff",
                subject=malformed,
                artifact_registry=(malformed,),
            )

    def test_output_evidence_requires_real_registered_subject_and_exact_role(self) -> None:
        loaded = load_creation_project(SYSTEMIC_PROJECT)
        system = loaded.system_modules[0]
        evidence = build_phase_output_evidence_v2(
            evidence_id="systems_output",
            phase="p07_systems",
            role="systems_design",
            subject=_identity(system),
            reviewer_id="lead_reviewer",
            reviewer_role="validation_analyst",
            source_project=loaded,
        )
        self.assertEqual(2, evidence["format_version"])
        self.assertEqual(_identity(system), evidence["subject"])

        with self.assertRaisesRegex(PhaseReportV3Error, "role"):
            build_phase_output_evidence_v2(
                evidence_id="wrong_role",
                phase="p07_systems",
                role="asset_release",
                subject=_identity(system),
                reviewer_id="lead_reviewer",
                reviewer_role="validation_analyst",
                source_project=loaded,
            )
        forged = {**_identity(system), "content_hash": "0" * 64}
        with self.assertRaisesRegex(PhaseReportV3Error, "registered|unknown|mismatched"):
            build_phase_output_evidence_v2(
                evidence_id="forged",
                phase="p07_systems",
                role="systems_design",
                subject=forged,
                reviewer_id="lead_reviewer",
                reviewer_role="validation_analyst",
                source_project=loaded,
            )

    def test_not_applicable_is_profile_proven_and_p14_is_never_optional(self) -> None:
        loaded = load_creation_project(SYSTEMIC_PROJECT)
        report = _generic_report(
            loaded,
            phase="p12_asset_specs",
            role=None,
            subject=None,
            status="not_applicable",
            rationale_code="assets_not_applicable",
        )
        self.assertEqual("not_applicable", validate_phase_report_v3(report, loaded)["status"])

        changed = copy.deepcopy(report)
        changed["phase"] = "p14_handoff"
        changed = _reseal(changed)
        with self.assertRaisesRegex(PhaseReportV3Error, "p14_handoff cannot be not_applicable"):
            validate_phase_report_v3(changed, loaded)

        puzzle = load_creation_project(PUZZLE_PROJECT)
        with self.assertRaisesRegex(PhaseReportV3Error, "does not prove"):
            _generic_report(
                puzzle,
                phase="p12_asset_specs",
                role=None,
                subject=None,
                status="not_applicable",
                rationale_code="assets_not_applicable",
            )

    def test_p13_runtime_not_applicable_requires_exact_authoring_only_absence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            authoring_only_cases = (
                ("authored-assets", "authored"),
                ("not-applicable-assets", "not_applicable"),
            )
            loaded_cases = []
            for dirname, asset_content_mode in authoring_only_cases:
                root = Path(temp) / dirname
                loaded = _authoring_only_runtime_absence_project(
                    root,
                    asset_content_mode=asset_content_mode,
                )
                loaded_cases.append(loaded)
                with self.subTest(asset_content_mode=asset_content_mode):
                    self.assertEqual(1, len(loaded.activity_modules))
                    self.assertEqual(1, len(loaded.logic_modules))
                    report = _generic_report(
                        loaded,
                        phase="p13_asset_production",
                        role=None,
                        subject=None,
                        status="not_applicable",
                        rationale_code="runtime_not_applicable",
                    )
                    self.assertEqual(
                        "not_applicable", validate_phase_report_v3(report, loaded)["status"]
                    )

            self.assertEqual(
                loaded_cases[0].profile["runtime_target"],
                loaded_cases[1].profile["runtime_target"],
            )

            default_root = Path(temp) / "default-runtime"
            create_creation_project(
                default_root,
                project_id="default_runtime_game",
                title="Default runtime game",
                project_kind="game",
                gameplay_family="puzzle",
                initial_core_verb="solve",
                initial_core_loop="inspect, act, and review deterministic feedback",
                world_presence="none",
                narrative_requirement="none",
                narrative_authorship="none",
                narrative_topology="none",
                presentation_mode="2d",
                runtime_support_intent="authoring_only",
            )
            default_loaded = load_creation_project(default_root / "project.json")
            self.assertEqual(1, len(default_loaded.logic_modules))
            self.assertEqual(
                "not_applicable",
                validate_phase_report_v3(
                    _generic_report(
                        default_loaded,
                        phase="p13_asset_production",
                        role=None,
                        subject=None,
                        status="not_applicable",
                        rationale_code="runtime_not_applicable",
                    ),
                    default_loaded,
                )["status"],
            )

            compatibility_root = Path(temp) / "compatibility-runtime"
            create_creation_project(
                compatibility_root,
                project_id="compatibility_runtime_game",
                title="Compatibility runtime game",
                project_kind="game",
                gameplay_family="puzzle",
                initial_core_verb="solve",
                initial_core_loop="inspect, act, and review deterministic feedback",
                world_presence="none",
                narrative_requirement="none",
                narrative_authorship="none",
                narrative_topology="none",
                presentation_mode="2d",
                runtime_support_intent="compatibility_assessment",
                asset_content_mode="not_applicable",
            )
            self.assertEqual(
                1,
                len(load_creation_project(compatibility_root / "project.json").logic_modules),
            )
            with self.assertRaisesRegex(PhaseReportV3Error, "does not prove"):
                _generic_report(
                    load_creation_project(compatibility_root / "project.json"),
                    phase="p13_asset_production",
                    role=None,
                    subject=None,
                    status="not_applicable",
                    rationale_code="runtime_not_applicable",
                )

    def test_p13_runtime_not_applicable_fails_closed_for_each_runtime_request_dimension(
        self,
    ) -> None:
        mutations = {
            "requested_adapter": lambda target: target.__setitem__("requested_adapter", "raylib"),
            "accepted_logic_formats": lambda target: target.__setitem__(
                "accepted_logic_formats", [{"format": "world-forge.gamepack", "versions": [1]}]
            ),
            "required_features": lambda target: target.__setitem__(
                "required_features", ["logic:deterministic_actions"]
            ),
            "optional_features": lambda target: target.__setitem__(
                "optional_features", ["runtime:save"]
            ),
            "platforms": lambda target: target.__setitem__("platforms", ["platform:linux_x86_64"]),
            "renderer": lambda target: target.__setitem__("renderer", "raylib"),
            "input_capabilities": lambda target: target.__setitem__(
                "input_capabilities", ["input:keyboard"]
            ),
            "asset_formats": lambda target: target.__setitem__("asset_formats", ["image/png"]),
            "save_expected": lambda target: target.__setitem__("save_expected", True),
            "replay_expected": lambda target: target.__setitem__("replay_expected", True),
            "packaging_target": lambda target: target.__setitem__(
                "packaging_target", "standalone desktop directory"
            ),
        }
        with tempfile.TemporaryDirectory() as temp:
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    root = Path(temp) / name
                    _authoring_only_runtime_absence_project(root)
                    profile = json.loads((root / "profile.json").read_text(encoding="utf-8"))
                    mutate(profile["runtime_target"])
                    _write_profile(root, profile)
                    try:
                        loaded = load_creation_project(root / "project.json")
                    except CreationContractError:
                        continue
                    with self.assertRaises(PhaseReportV3Error):
                        _generic_report(
                            loaded,
                            phase="p13_asset_production",
                            role=None,
                            subject=None,
                            status="not_applicable",
                            rationale_code="runtime_not_applicable",
                        )

    def test_p13_runtime_not_applicable_uses_same_absence_proof_for_non_games(self) -> None:
        mutations = {
            "requested_adapter": lambda target: target.__setitem__("requested_adapter", "raylib"),
            "accepted_logic_formats": lambda target: target.__setitem__(
                "accepted_logic_formats", [{"format": "world-forge.gamepack", "versions": [1]}]
            ),
            "required_features": lambda target: target.__setitem__(
                "required_features", ["logic:deterministic_actions"]
            ),
            "optional_features": lambda target: target.__setitem__(
                "optional_features", ["runtime:save"]
            ),
            "platforms": lambda target: target.__setitem__("platforms", ["platform:linux_x86_64"]),
            "renderer": lambda target: target.__setitem__("renderer", "raylib"),
            "input_capabilities": lambda target: target.__setitem__(
                "input_capabilities", ["input:keyboard"]
            ),
            "asset_formats": lambda target: target.__setitem__(
                "asset_formats", ["asset:image_png"]
            ),
            "save_expected": lambda target: target.__setitem__("save_expected", True),
            "replay_expected": lambda target: target.__setitem__("replay_expected", True),
            "packaging_target": lambda target: target.__setitem__(
                "packaging_target", "standalone desktop directory"
            ),
        }
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for project_kind in ("universe_library", "asset_library"):
                root = base / project_kind
                loaded = _non_game_creation_project(root, project_kind=project_kind)
                with self.subTest(project_kind=project_kind, case="default"):
                    self.assertEqual(
                        {
                            "accepted_logic_formats": [],
                            "asset_formats": [],
                            "input_capabilities": [],
                            "optional_features": [],
                            "packaging_target": "none",
                            "platforms": [],
                            "presentation_mode": loaded.profile["presentation"]["mode"],
                            "renderer": "none",
                            "replay_expected": False,
                            "requested_adapter": None,
                            "required_features": [],
                            "save_expected": False,
                        },
                        loaded.profile["runtime_target"],
                    )
                    report = _generic_report(
                        loaded,
                        phase="p13_asset_production",
                        role=None,
                        subject=None,
                        status="not_applicable",
                        rationale_code="runtime_not_applicable",
                    )
                    self.assertEqual(
                        "not_applicable", validate_phase_report_v3(report, loaded)["status"]
                    )
                    with self.assertRaises(PhaseReportV3Error):
                        _generic_report(
                            loaded,
                            phase="p13_asset_production",
                            role=None,
                            subject=None,
                            status="not_applicable",
                            rationale_code="runtime_not_applicable",
                            artifact_registry=(_runtime_support_report_artifact(),),
                        )

                for name, mutate in mutations.items():
                    mutated_root = base / f"{project_kind}-{name}"
                    _non_game_creation_project(mutated_root, project_kind=project_kind)
                    profile = json.loads(
                        (mutated_root / "profile.json").read_text(encoding="utf-8")
                    )
                    mutate(profile["runtime_target"])
                    _write_profile(mutated_root, profile)
                    loaded = load_creation_project(mutated_root / "project.json")
                    with self.subTest(project_kind=project_kind, mutation=name):
                        with self.assertRaisesRegex(PhaseReportV3Error, "does not prove"):
                            _generic_report(
                                loaded,
                                phase="p13_asset_production",
                                role=None,
                                subject=None,
                                status="not_applicable",
                                rationale_code="runtime_not_applicable",
                            )

    def test_p11_to_p14_role_subject_matrix_is_closed(self) -> None:
        loaded = load_creation_project(SYSTEMIC_PROJECT)
        readiness = build_creation_readiness(loaded)
        status = initial_creation_workflow_status(loaded)
        handoff = build_creation_handoff(loaded, status=status, readiness=readiness)

        cases = (
            ("p11_art_audio", "presentation_direction", loaded.profile, ()),
            (
                "p14_handoff",
                "implementation_handoff",
                handoff,
                (status, readiness, handoff),
            ),
        )
        for phase, role, subject, registry in cases:
            with self.subTest(phase=phase):
                report = _generic_report(
                    loaded,
                    phase=phase,
                    role=role,
                    subject=subject,
                    artifact_registry=registry,
                )
                self.assertEqual(
                    phase,
                    validate_phase_report_v3(
                        report,
                        loaded,
                        artifact_registry=registry,
                    )["phase"],
                )


class CreationWorkflowAndReadinessTests(unittest.TestCase):
    def test_archived_asset_report_replay_uses_recorded_creation_snapshot_once(
        self,
    ) -> None:
        from worldforge.creation_workflow import _validate_report_reference

        loaded = load_creation_project(PUZZLE_PROJECT)
        graph = _puzzle_asset_graph()
        by_format = {str(document["format"]): document for document in graph}
        reviewer = {"id": "lead_reviewer", "role": "validation_analyst"}
        output = build_phase_output_evidence_v2(
            evidence_id="asset_plan_output",
            phase="p12_asset_specs",
            role="asset_plan",
            subject=_identity(by_format["world-forge.asset_manifest"]),
            reviewer_id=reviewer["id"],
            reviewer_role=reviewer["role"],
            source_project=loaded,
            artifact_registry=graph,
        )
        report = build_phase_report_v3(
            loaded,
            phase="p12_asset_specs",
            status="ready",
            rationale_code="phase_ready",
            rationale_message="The archived asset graph was reviewed.",
            evidence=(
                {
                    "evidence_id": "sealed_assetpack",
                    "claim": "The exact sealed asset graph was reviewed.",
                    "subject": _identity(by_format["world-forge.assetpack"]),
                },
            ),
            output_evidence=output,
            reviewer_id=reviewer["id"],
            reviewer_role=reviewer["role"],
            invalidation_dependencies=None,
            artifact_registry=graph,
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "archived-project"
            history = root / ".worldforge/artifact_history"
            reports = root / ".worldforge/phase_reports"
            history.mkdir(parents=True)
            reports.mkdir()
            creation_documents = (
                loaded.project,
                loaded.profile,
                loaded.manifest,
                *loaded.world_modules,
                *loaded.activity_modules,
                *loaded.narrative_modules,
                *loaded.system_modules,
                *loaded.logic_modules,
            )
            for document in (*creation_documents, *graph):
                identity = document_identity(document)
                (history / f"{identity['content_hash']}.json").write_text(
                    json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            relative = f".worldforge/phase_reports/p12_asset_specs-{report['content_hash']}.json"
            (root / relative).write_text(
                json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            reference = {
                "phase": report["phase"],
                "status": report["status"],
                "path": relative,
                "content_hash": report["content_hash"],
                "invalidation_dependencies": report["invalidation_dependencies"],
            }

            self.assertIsNone(
                _validate_report_reference(root, reference, loaded),
            )

    def test_reconciliation_accepts_source_change_before_p00_from_initial_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "creation"
            create_creation_project(
                root,
                project_id="revision_zero",
                title="Revision zero",
                default_locale="en",
            )
            loaded = load_creation_project(root / "project.json")
            initial_status_hash = load_creation_workflow_status(root)["content_hash"]
            for document in (loaded.project, loaded.profile, loaded.manifest):
                identity = document_identity(document)
                archived = (
                    root / ".worldforge/artifact_history" / (f"{identity['content_hash']}.json")
                )
                self.assertEqual(document, json.loads(archived.read_text(encoding="utf-8")))

            profile_path = root / "profile.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["fiction"]["genres"] = ["historical"]
            profile = _reseal(profile)
            profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
            manifest_path = root / "source/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["profile"]["content_hash"] = profile["content_hash"]
            manifest = _reseal(manifest)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            project_path = root / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["profile"]["content_hash"] = profile["content_hash"]
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _reseal(project)
            project_path.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")

            stale_status = (root / ".worldforge/status.json").read_bytes()
            with self.assertRaisesRegex(CreationWorkflowError, "does not match"):
                load_creation_workflow_status(root)
            self.assertEqual(stale_status, (root / ".worldforge/status.json").read_bytes())

            reconciled = reconcile_creation_workflow(
                root,
                expected_status_hash=initial_status_hash,
            )
            reconciled_bytes = (root / ".worldforge/status.json").read_bytes()
            repeated = reconcile_creation_workflow(
                root,
                expected_status_hash=reconciled["content_hash"],
            )
            self.assertEqual(1, reconciled["revision"])
            self.assertEqual([], reconciled["reports"])
            self.assertEqual([], reconciled["invalidated_reports"])
            self.assertEqual("p00_brief", reconciled["current_phase"])
            self.assertEqual(reconciled, repeated)
            self.assertEqual(
                reconciled_bytes,
                (root / ".worldforge/status.json").read_bytes(),
            )

            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["fiction"]["genres"] = ["science_fiction"]
            profile = _reseal(profile)
            profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["profile"]["content_hash"] = profile["content_hash"]
            manifest = _reseal(manifest)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["profile"]["content_hash"] = profile["content_hash"]
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _reseal(project)
            project_path.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")

            second_reconciliation = reconcile_creation_workflow(
                root,
                expected_status_hash=reconciled["content_hash"],
            )
            self.assertEqual(2, second_reconciliation["revision"])
            self.assertEqual([], second_reconciliation["reports"])
            self.assertEqual([], second_reconciliation["invalidated_reports"])

    def test_readiness_rejects_resealed_positive_dimensions_without_exact_inputs(self) -> None:
        loaded = load_creation_project(SYSTEMIC_PROJECT)
        forged = build_creation_readiness(loaded)
        forged["dimensions"] = {
            "authoring": "valid",
            "compilation": "compiled",
            "assets": "sealed",
            "adapter": "verified",
            "execution": [
                {
                    "platform": platform,
                    "status": "native_verified",
                    "evidence_ids": [f"forged_{index}"],
                }
                for index, platform in enumerate(
                    loaded.profile["runtime_target"]["platforms"],
                    start=1,
                )
            ],
            "packaging": "verified",
            "release": "ready",
        }
        forged["blocker_reason_codes"] = []
        forged["release_ready"] = True
        forged = _reseal(forged)

        with self.assertRaisesRegex(CreationReadinessError, "canonical|dimensions|artifacts"):
            validate_creation_readiness(forged, loaded)

    def test_systemic_fixture_compiles_without_asset_requirements_and_analysis_is_unsupported(
        self,
    ) -> None:
        loaded = load_creation_project(SYSTEMIC_PROJECT)
        gamepack = build_gamepack(loaded)
        analysis = analyze_gamepack(gamepack)

        self.assertEqual([], gamepack["asset_requirements"])
        self.assertEqual("unsupported", analysis["status"])
        self.assertEqual(
            "unsupported",
            gamepack["analysis_requirements"]["profile"],
        )

    def test_workflow_is_hash_bound_deterministic_and_reopen_invalidates_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = _copy_project(SYSTEMIC_ROOT, Path(temp) / "project")
            loaded = load_creation_project(root / "project.json")
            reports = (
                _generic_report(
                    loaded,
                    phase="p00_brief",
                    role="project_brief",
                    subject=loaded.project,
                ),
                _generic_report(
                    loaded,
                    phase="p01_genre_style",
                    role="experience_classification",
                    subject=loaded.profile,
                ),
            )
            report_paths: list[Path] = []
            for report in reports:
                path = root / f"{report['phase']}.json"
                path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                report_paths.append(path)

            first = complete_creation_phase(
                root,
                report_paths[0],
                expected_status_hash=load_creation_workflow_status(root)["content_hash"],
            )
            second = complete_creation_phase(
                root,
                report_paths[1],
                expected_status_hash=first["content_hash"],
            )
            loaded_status = load_creation_workflow_status(root)

            self.assertEqual(first["content_hash"], first["content_hash"])
            self.assertEqual(second, loaded_status)
            self.assertEqual(
                ["p00_brief", "p01_genre_style"],
                second["completed_phases"],
            )
            self.assertEqual("p02_world_laws", second["current_phase"])
            self.assertEqual(2, len(second["reports"]))

            reopened = reopen_creation_phase(
                root,
                "p01_genre_style",
                reason="Profile changed",
                approved_by="lead_reviewer",
                expected_status_hash=second["content_hash"],
            )
            self.assertEqual("p01_genre_style", reopened["current_phase"])
            self.assertEqual(["p00_brief"], reopened["completed_phases"])
            self.assertEqual(1, len(reopened["reports"]))
            self.assertEqual(1, len(reopened["invalidated_reports"]))

    def test_reconciliation_reopens_stale_dependency_without_accepting_forged_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = _copy_project(SYSTEMIC_ROOT, Path(temp) / "project")
            loaded = load_creation_project(root / "project.json")
            report = _generic_report(
                loaded,
                phase="p00_brief",
                role="project_brief",
                subject=loaded.project,
            )
            report_path = root / "p00.json"
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            completed = complete_creation_phase(
                root,
                report_path,
                expected_status_hash=load_creation_workflow_status(root)["content_hash"],
            )

            stored_report = root / completed["reports"][0]["path"]
            changed = json.loads(stored_report.read_text(encoding="utf-8"))
            changed["evidence"][0]["claim"] = "tampered"
            stored_report.write_text(json.dumps(changed, indent=2) + "\n", encoding="utf-8")

            reconciled = reconcile_creation_workflow(
                root,
                expected_status_hash=completed["content_hash"],
            )
            self.assertEqual("p00_brief", reconciled["current_phase"])
            self.assertEqual([], reconciled["completed_phases"])
            self.assertEqual("report_hash_mismatch", reconciled["invalidated_reports"][0]["reason"])

    def test_phase_advance_and_reopen_fail_closed_when_prior_report_is_missing_or_tampered(
        self,
    ) -> None:
        for mutation in ("delete", "tamper"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = _copy_project(SYSTEMIC_ROOT, Path(temp) / "project")
                loaded = load_creation_project(root / "project.json")
                first_report = _generic_report(
                    loaded,
                    phase="p00_brief",
                    role="project_brief",
                    subject=loaded.project,
                )
                first_path = root / "p00.json"
                first_path.write_text(
                    json.dumps(first_report, indent=2) + "\n",
                    encoding="utf-8",
                )
                completed = complete_creation_phase(
                    root,
                    first_path,
                    expected_status_hash=load_creation_workflow_status(root)["content_hash"],
                )
                stored = root / completed["reports"][0]["path"]
                if mutation == "delete":
                    stored.unlink()
                else:
                    changed = json.loads(stored.read_text(encoding="utf-8"))
                    changed["evidence"][0]["claim"] = "tampered"
                    stored.write_text(
                        json.dumps(changed, indent=2) + "\n",
                        encoding="utf-8",
                    )

                second_report = _generic_report(
                    loaded,
                    phase="p01_genre_style",
                    role="experience_classification",
                    subject=loaded.profile,
                )
                second_path = root / "p01.json"
                second_path.write_text(
                    json.dumps(second_report, indent=2) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(CreationWorkflowError, "prior|report"):
                    complete_creation_phase(
                        root,
                        second_path,
                        expected_status_hash=completed["content_hash"],
                    )
                with self.assertRaisesRegex(CreationWorkflowError, "prior|report"):
                    reopen_creation_phase(
                        root,
                        "p00_brief",
                        reason="Review changed",
                        approved_by="lead_reviewer",
                        expected_status_hash=completed["content_hash"],
                    )

    def test_reconciliation_accepts_changed_source_snapshot_and_is_byte_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = _copy_project(SYSTEMIC_ROOT, Path(temp) / "project")
            loaded = load_creation_project(root / "project.json")
            report = _generic_report(
                loaded,
                phase="p00_brief",
                role="project_brief",
                subject=loaded.project,
            )
            report_path = root / "p00.json"
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            completed = complete_creation_phase(
                root,
                report_path,
                expected_status_hash=load_creation_workflow_status(root)["content_hash"],
            )

            profile_path = root / "profile.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["fiction"]["genres"] = ["historical"]
            profile = _reseal(profile)
            profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
            manifest_path = root / "source/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["profile"]["content_hash"] = profile["content_hash"]
            manifest = _reseal(manifest)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            project_path = root / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["profile"]["content_hash"] = profile["content_hash"]
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _reseal(project)
            project_path.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")

            stale_status_bytes = (root / ".worldforge/status.json").read_bytes()
            with self.assertRaisesRegex(CreationWorkflowError, "does not match"):
                load_creation_workflow_status(root)
            self.assertEqual(
                stale_status_bytes,
                (root / ".worldforge/status.json").read_bytes(),
            )
            reconciled = reconcile_creation_workflow(
                root,
                expected_status_hash=completed["content_hash"],
            )
            status_bytes = (root / ".worldforge/status.json").read_bytes()
            repeated = reconcile_creation_workflow(
                root,
                expected_status_hash=reconciled["content_hash"],
            )

            self.assertEqual([], reconciled["completed_phases"])
            self.assertEqual("p00_brief", reconciled["current_phase"])
            self.assertEqual(
                "report_dependency_stale",
                reconciled["invalidated_reports"][0]["reason"],
            )
            self.assertEqual(
                _identity(load_creation_project(project_path).project),
                reconciled["project"],
            )
            self.assertEqual(reconciled, repeated)
            self.assertEqual(status_bytes, (root / ".worldforge/status.json").read_bytes())

    def test_authoring_ready_is_independent_from_release_and_native_evidence(self) -> None:
        loaded = load_creation_project(SYSTEMIC_PROJECT)
        readiness = build_creation_readiness(loaded)
        validated = validate_creation_readiness(readiness, loaded)

        self.assertEqual("valid", validated["dimensions"]["authoring"])
        self.assertEqual("not_requested", validated["dimensions"]["compilation"])
        self.assertEqual("unplanned", validated["dimensions"]["assets"])
        self.assertEqual("absent", validated["dimensions"]["adapter"])
        self.assertEqual("blocked", validated["dimensions"]["release"])
        self.assertIn("runtime_adapter_not_requested", validated["blocker_reason_codes"])
        self.assertIn("native_evidence_missing", validated["blocker_reason_codes"])
        self.assertFalse(validated["release_ready"])

        status = load_creation_workflow_status(SYSTEMIC_ROOT)
        handoff = build_creation_handoff(loaded, status=status, readiness=validated)
        self.assertEqual("authoring_ready", handoff["handoff_status"])
        self.assertEqual(
            handoff,
            validate_creation_handoff(handoff, loaded, status=status, readiness=validated),
        )

    def test_generic_cli_machine_output_and_legacy_dispatch_remain_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "creation"
            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "new-creation",
                        str(target),
                        "--id",
                        "cli_creation",
                        "--title",
                        "CLI creation",
                        "--language",
                        "en",
                        "--json",
                    ],
                ),
            ):
                self.assertEqual(0, main())
            output = json.loads(stdout.getvalue())
            self.assertEqual("generic", output["route"])
            self.assertEqual("world-forge.project", output["project"]["format"])

            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                mock.patch(
                    "sys.argv",
                    ["worldforge", "phase-status", str(target), "--json"],
                ),
            ):
                self.assertEqual(0, main())
            status = json.loads(stdout.getvalue())
            self.assertEqual("world-forge.creation_workflow_status", status["format"])

            loaded = load_creation_project(target / "project.json")
            report = _generic_report(
                loaded,
                phase="p00_brief",
                role="project_brief",
                subject=loaded.project,
            )
            report_path = root / "p00.json"
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            initial_status_hash = load_creation_workflow_status(target)["content_hash"]
            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "complete-phase",
                        str(target),
                        "--report",
                        str(report_path),
                        "--expected-status-hash",
                        initial_status_hash,
                        "--json",
                    ],
                ),
            ):
                self.assertEqual(0, main())
            completed = json.loads(stdout.getvalue())
            self.assertEqual(["p00_brief"], completed["completed_phases"])
            self.assertEqual("p01_genre_style", completed["current_phase"])

            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "reopen-phase",
                        str(target),
                        "--phase",
                        "p00_brief",
                        "--reason",
                        "Review changed",
                        "--approved-by",
                        "lead_reviewer",
                        "--expected-status-hash",
                        completed["content_hash"],
                        "--json",
                    ],
                ),
            ):
                self.assertEqual(0, main())
            reopened = json.loads(stdout.getvalue())
            self.assertEqual([], reopened["completed_phases"])
            self.assertEqual("p00_brief", reopened["current_phase"])

            legacy = root / "legacy"
            legacy.mkdir()
            control = legacy / ".worldforge"
            control.mkdir()
            (control / "project.json").write_text(
                '{"format":"rpg-world-forge.project","format_version":2}\n',
                encoding="utf-8",
            )
            self.assertEqual("legacy", route_creation_project(legacy))

    def test_reconcile_creation_cli_is_cas_bound_append_only_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "creation"
            create_creation_project(
                target,
                project_id="cli_reconciliation",
                title="CLI reconciliation",
                default_locale="en",
            )
            loaded = load_creation_project(target / "project.json")
            report = _generic_report(
                loaded,
                phase="p00_brief",
                role="project_brief",
                subject=loaded.project,
            )
            report_path = root / "p00.json"
            report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            completed = complete_creation_phase(
                target,
                report_path,
                expected_status_hash=load_creation_workflow_status(target)["content_hash"],
            )
            expected_status_hash = str(completed["content_hash"])
            invalidated_report_hash = str(completed["reports"][0]["content_hash"])

            profile_path = target / "profile.json"
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["fiction"]["genres"] = ["historical"]
            profile = _reseal(profile)
            profile_path.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
            manifest_path = target / "source/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["profile"]["content_hash"] = profile["content_hash"]
            manifest = _reseal(manifest)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            project_path = target / "project.json"
            project = json.loads(project_path.read_text(encoding="utf-8"))
            project["profile"]["content_hash"] = profile["content_hash"]
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _reseal(project)
            project_path.write_text(json.dumps(project, indent=2) + "\n", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "reconcile-creation",
                        str(target),
                        "--expected-status-hash",
                        expected_status_hash,
                    ],
                ),
            ):
                self.assertEqual(0, main())
            self.assertEqual("", stderr.getvalue())
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["changed"])
            self.assertEqual("generic", result["route"])
            reconciled = result["workflow_status"]
            self.assertEqual("p00_brief", reconciled["current_phase"])
            self.assertEqual([], reconciled["completed_phases"])
            self.assertEqual(
                {
                    "phase": "p00_brief",
                    "reason": "report_dependency_stale",
                    "report_content_hash": invalidated_report_hash,
                    "revision": completed["revision"] + 1,
                },
                reconciled["invalidated_reports"][-1],
            )
            current = load_creation_project(project_path)
            for document in (current.project, current.profile, current.manifest):
                identity = document_identity(document)
                archived = (
                    target / ".worldforge/artifact_history" / f"{identity['content_hash']}.json"
                )
                self.assertEqual(document, json.loads(archived.read_text(encoding="utf-8")))

            reconciled_bytes = (target / ".worldforge/status.json").read_bytes()
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "reconcile-creation",
                        str(target),
                        "--expected-status-hash",
                        expected_status_hash,
                    ],
                ),
            ):
                self.assertEqual(1, main())
            self.assertEqual("", stdout.getvalue())
            error = json.loads(stderr.getvalue())
            self.assertEqual("error", error["status"])
            self.assertEqual(
                "creation_workflow_expected_status_hash_mismatch",
                error["reason_code"],
            )
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertEqual(
                reconciled_bytes,
                (target / ".worldforge/status.json").read_bytes(),
            )

            invalid_artifact = root / "invalid-artifact.json"
            invalid_artifact.write_text('{"format":', encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "reconcile-creation",
                        str(target),
                        "--expected-status-hash",
                        reconciled["content_hash"],
                        "--artifact",
                        str(invalid_artifact),
                    ],
                ),
            ):
                self.assertEqual(1, main())
            artifact_error = json.loads(stderr.getvalue())
            self.assertEqual(
                "creation_workflow_artifact_invalid",
                artifact_error["reason_code"],
            )
            self.assertEqual("", stdout.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertEqual(
                reconciled_bytes,
                (target / ".worldforge/status.json").read_bytes(),
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "reconcile-creation",
                        str(target),
                        "--expected-status-hash",
                        reconciled["content_hash"],
                    ],
                ),
            ):
                self.assertEqual(0, main())
            repeated = json.loads(stdout.getvalue())
            self.assertFalse(repeated["changed"])
            self.assertEqual(reconciled, repeated["workflow_status"])
            self.assertEqual("", stderr.getvalue())
            self.assertEqual(
                reconciled_bytes,
                (target / ".worldforge/status.json").read_bytes(),
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "reconcile-creation",
                        str(target),
                        "--expected-status-hash",
                        "not-a-content-hash",
                    ],
                ),
            ):
                self.assertEqual(1, main())
            invalid_error = json.loads(stderr.getvalue())
            self.assertEqual(
                "creation_workflow_expected_status_hash_invalid",
                invalid_error["reason_code"],
            )
            self.assertEqual("", stdout.getvalue())
            self.assertEqual(
                reconciled_bytes,
                (target / ".worldforge/status.json").read_bytes(),
            )


class CreationWorkflowGenerationTests(unittest.TestCase):
    def test_catalog_types_and_generators_cover_creation_workflow_contracts(self) -> None:
        catalog = json.loads((ROOT / "contracts/catalog.json").read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        expected = {
            "creation-workflow-status": "world-forge.creation_workflow_status",
            "creation-readiness": "world-forge.creation_readiness",
            "creation-handoff": "world-forge.creation_handoff",
            "phase-report-v3": "world-forge.phase_report",
        }
        for contract_id, format_name in expected.items():
            with self.subTest(contract=contract_id):
                self.assertEqual(format_name, entries[contract_id]["format"])

        generated = (ROOT / "apps/studio/src/generated/world-forge-contracts.d.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("WorldForgePhaseReportV3", generated)
        self.assertIn("WorldForgeCreationWorkflowStatusV1", generated)
        self.assertIn("WorldForgeCreationReadinessV1", generated)
        self.assertIn("WorldForgeCreationHandoffV1", generated)

        from scripts.generate_creation_workflow_contracts import main as generate_contracts
        from scripts.generate_creation_workflow_fixtures import main as generate_fixtures

        self.assertEqual(0, generate_contracts(["--check"]))
        self.assertEqual(0, generate_fixtures(["--check"]))

        loaded = load_creation_project(SYSTEMIC_PROJECT)
        history_root = SYSTEMIC_ROOT / ".worldforge/artifact_history"
        for document in (
            loaded.project,
            loaded.profile,
            loaded.manifest,
            *loaded.system_modules,
        ):
            identity = document_identity(document)
            self.assertEqual(
                document,
                json.loads(
                    (history_root / f"{identity['content_hash']}.json").read_text(encoding="utf-8")
                ),
            )


if __name__ == "__main__":
    unittest.main()
