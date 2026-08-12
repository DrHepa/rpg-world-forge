from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from worldforge.integrity import canonical_json_bytes

_ROOT = Path(__file__).resolve().parents[1]
_PUZZLE_ROOT = _ROOT / "examples/multigenre-contracts/abstract-puzzle"
_ASSET_ROOT = _PUZZLE_ROOT / "assets"
_PRODUCTION_ROOT = _ASSET_ROOT / "production/board_ui"


def _document(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _processed_project_output(base: Path) -> Path:
    recipe = _document(_PRODUCTION_ROOT / "recipe.json")
    locator = recipe["steps"][0]["output_locator"]
    return base / "project" / Path(locator)


def _interrupt_processing_before_publication(
    recipe: object,
    *,
    processing_receipt_id: str,
    **lineage: object,
) -> object:
    from worldforge.generic_asset_processing import (
        GenericAssetProcessingError,
        build_asset_processing_receipt,
    )

    failed_receipt = build_asset_processing_receipt(
        recipe,
        processing_receipt_id=processing_receipt_id,
        status="failed",
        failure_reasons=["processor_interrupted"],
        **lineage,
    )
    raise GenericAssetProcessingError(
        "processor_interrupted",
        "simulated controlled interruption before output publication",
        recovery_receipt=failed_receipt,
    )


def _puzzle_lineage() -> tuple[dict[str, object], ...]:
    return tuple(
        _document(path)
        for path in (
            _PUZZLE_ROOT / "artifacts/abstract-puzzle.gamepack.json",
            _ASSET_ROOT / "subject.json",
            _ASSET_ROOT / "target.json",
            _ASSET_ROOT / "style.json",
            _ASSET_ROOT / "inventory.json",
            _ASSET_ROOT / "specs/board_ui.json",
            _PRODUCTION_ROOT / "request.json",
            _PRODUCTION_ROOT / "receipt.json",
            _PRODUCTION_ROOT / "selection.json",
            _PRODUCTION_ROOT / "provenance.json",
            _PRODUCTION_ROOT / "license.json",
        )
    )


def _acceptance_results(specification: dict[str, object]) -> list[dict[str, object]]:
    criteria = specification["acceptance_criteria"]
    assert isinstance(criteria, list)
    return [
        {
            "criterion_index": index,
            "criterion_sha256": hashlib.sha256(str(criterion).encode("utf-8")).hexdigest(),
            "status": "passed",
            "evidence_hashes": [hashlib.sha256(f"evidence:{index}".encode()).hexdigest()],
        }
        for index, criterion in enumerate(criteria)
    ]


def _bounded_acceptance_results(
    count: int,
    *,
    evidence_count: int = 1,
) -> tuple[list[str], list[dict[str, object]]]:
    criteria = [f"Criterion {index:03d} remains exact." for index in range(count)]
    evidence = [f"{index + 1:064x}" for index in range(evidence_count)]
    return criteria, [
        {
            "criterion_index": index,
            "criterion_sha256": hashlib.sha256(criterion.encode()).hexdigest(),
            "status": "passed",
            "evidence_hashes": list(evidence),
        }
        for index, criterion in enumerate(criteria)
    ]


def _stage_candidate(stage: Path, receipt: dict[str, object]) -> None:
    outputs = receipt["outputs"]
    assert isinstance(outputs, list)
    for output in outputs:
        assert isinstance(output, dict)
        locator = Path(str(output["locator"]))
        target = stage / "artifact_root" / locator
        target.parent.mkdir(parents=True, exist_ok=True)
        source = _PUZZLE_ROOT / locator
        target.write_bytes(source.read_bytes())


def _snapshot(service: object, workspace: dict[str, object]) -> dict[str, object]:
    return service.creation_evidence.list(
        {
            "workspace_id": workspace["workspace_id"],
            "expected_root_generation": workspace["root_generation"],
            "expected_source_revision": workspace["source_revision"],
            "expected_workflow_status_hash": workspace["workflow_status_hash"],
            "expected_artifact_snapshot_hash": None,
            "lifecycle": None,
            "cursor": None,
            "limit": 64,
        }
    )


def _seed_lineage_candidates(
    service: object,
    workspace: dict[str, object],
) -> tuple[dict[str, object], dict[tuple[object, ...], str]]:
    from worldforge.phase_report_v3 import artifact_dependency_identities, document_identity
    from worldforge.studio.creation_artifacts import artifact_id_for_identity
    from worldforge.studio.creation_executor import (
        CreationWorkerExecution,
        _verified_outputs,
    )
    from worldforge.studio.creation_worker import _execute

    def inline_worker(
        stage: Path,
        _stage_identity: tuple[int, int],
        envelope: object,
        **_kwargs: object,
    ) -> CreationWorkerExecution:
        response = _execute(envelope, stage)
        return CreationWorkerExecution(response, _verified_outputs(stage, response))

    project = service.creation_workspaces._refresh_snapshot(workspace["workspace_id"])[1]
    source_keys = {
        tuple(document_identity(document).values())
        for document in (
            project.project,
            project.profile,
            project.manifest,
            *project.world_modules,
            *project.activity_modules,
            *project.narrative_modules,
            *project.system_modules,
            *project.logic_modules,
        )
    }
    admitted: dict[tuple[object, ...], dict[str, object]] = {}
    artifact_ids: dict[tuple[object, ...], str] = {}
    current = _snapshot(service, workspace)
    for index, document in enumerate(_puzzle_lineage()):
        dependencies: dict[tuple[object, ...], dict[str, object]] = {}
        pending = list(artifact_dependency_identities(document))
        while pending:
            dependency = pending.pop()
            key = tuple(dependency.values())
            if key in source_keys or key in dependencies:
                continue
            dependency_document = admitted[key]
            dependencies[key] = dependency_document
            pending.extend(artifact_dependency_identities(dependency_document))
        dependency_ids = sorted(
            (artifact_ids[key] for key in dependencies),
            key=lambda item: item.encode("utf-8"),
        )
        with patch(
            "worldforge.studio.creation_job_protocol._reject_admission_secrets",
            return_value=None,
        ):
            queued = service.creation_jobs.create_admission(
                {
                    "job_id": f"job_admit_asset_lineage_{index:02d}",
                    "workspace_id": workspace["workspace_id"],
                    "operation": "artifact.admit",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
                    "document": document,
                    "dependency_artifact_ids": dependency_ids,
                }
            )
            with patch(
                "worldforge.studio.creation_jobs.run_isolated_creation_worker",
                side_effect=inline_worker,
            ):
                assert service.creation_job_coordinator.run_once() == queued["job_id"]
        completed = service.creation_jobs.get(queued["job_id"])
        assert completed["state"] == "succeeded"
        identity = document_identity(document)
        key = tuple(identity.values())
        admitted[key] = document
        artifact_ids[key] = artifact_id_for_identity(identity)
        current = _snapshot(service, workspace)
    return current, artifact_ids


def _asset_process_params(
    service: object,
    workspace: dict[str, object],
) -> dict[str, object]:
    from worldforge.phase_report_v3 import document_identity

    before, artifact_ids = _seed_lineage_candidates(service, workspace)
    license_document = _puzzle_lineage()[-1]
    license_id = artifact_ids[tuple(document_identity(license_document).values())]
    return {
        "workspace_id": workspace["workspace_id"],
        "operation": "asset.process",
        "expected_root_generation": workspace["root_generation"],
        "expected_source_revision": workspace["source_revision"],
        "expected_workflow_status_hash": workspace["workflow_status_hash"],
        "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
        "license_artifact_ids": [license_id],
        "recipe_id": "board_ui_studio_recipe",
        "processing_receipt_id": "board_ui_studio_processing_receipt",
        "qa_report_id": "board_ui_studio_qa",
        "acceptance_results": _acceptance_results(_puzzle_lineage()[5]),
    }


def _concurrent_submit(
    base: Path,
    submit: object,
) -> tuple[dict[str, object], dict[str, object]]:
    from worldforge.studio.service import StudioService
    from worldforge.studio.storage import StudioStore

    start = threading.Barrier(2)
    snapshots = threading.Barrier(2)
    snapshot_serial = threading.Lock()
    results: list[dict[str, object] | None] = [None, None]
    errors: list[BaseException] = []

    def run(index: int) -> None:
        store = StudioStore(base / "studio", mode="secondary")
        service = StudioService(store)
        real_snapshot = service.creation_evidence._snapshot  # noqa: SLF001

        def synchronized_snapshot(params: object):
            with snapshot_serial:
                result = real_snapshot(params)
            if not store.connection.in_transaction:
                snapshots.wait(timeout=30.0)
            return result

        try:
            with patch.object(
                service.creation_evidence,
                "_snapshot",
                side_effect=synchronized_snapshot,
            ):
                start.wait(timeout=30.0)
                results[index] = submit(service, index)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
        finally:
            service.close()
            store.close()

    threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=240.0)
    if any(thread.is_alive() for thread in threads):
        raise AssertionError("concurrent creation submission did not terminate")
    if errors:
        raise errors[0]
    if results[0] is None or results[1] is None:
        raise AssertionError("concurrent creation submission returned no result")
    return results[0], results[1]


class StudioCreationAssetJobGetOrCreateTests(unittest.TestCase):
    def test_same_admission_and_process_are_atomic_get_or_create_while_pending(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service

        with tempfile.TemporaryDirectory() as temporary:
            service, workspace = _prepared_creation_service(Path(temporary))
            try:
                before = _snapshot(service, workspace)
                admission = {
                    "workspace_id": workspace["workspace_id"],
                    "operation": "artifact.admit",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
                    "document": _puzzle_lineage()[0],
                    "dependency_artifact_ids": [],
                }
                with patch(
                    "worldforge.studio.creation_job_protocol._reject_admission_secrets",
                    return_value=None,
                ):
                    first_admit = service.creation_jobs.create_admission(
                        {**admission, "job_id": "job_admit_atomic_first"}
                    )
                    second_admit = service.creation_jobs.create_admission(
                        {**admission, "job_id": "job_admit_atomic_second"}
                    )
                self.assertEqual(first_admit["job_id"], second_admit["job_id"])
                self.assertEqual(
                    1,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_job_payloads WHERE job_id = ?",
                        (first_admit["job_id"],),
                    ).fetchone()[0],
                )

                service.creation_jobs.cancel(
                    first_admit["job_id"],
                    expected_generation=first_admit["generation"],
                    expected_record_hash=first_admit["record_hash"],
                )
                process = _asset_process_params(service, workspace)
                first_process = service.creation_jobs.create_asset_process(
                    {**process, "job_id": "job_process_atomic_first"}
                )
                second_process = service.creation_jobs.create_asset_process(
                    {**process, "job_id": "job_process_atomic_second"}
                )
                self.assertEqual(first_process["job_id"], second_process["job_id"])
                self.assertEqual(
                    1,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_jobs WHERE workspace_id = ? "
                        "AND operation = 'asset.process' AND state IN ('queued', 'running')",
                        (workspace["workspace_id"],),
                    ).fetchone()[0],
                )
                self.assertEqual(
                    0,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_job_attempts WHERE job_id IN (?, ?)",
                        (first_admit["job_id"], first_process["job_id"]),
                    ).fetchone()[0],
                )
            finally:
                service.close()
                service.store.close()

    def test_concurrent_same_admission_and_process_create_one_pending_job(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            primary, workspace = _prepared_creation_service(base)
            try:
                before = _snapshot(primary, workspace)
                admission = {
                    "workspace_id": workspace["workspace_id"],
                    "operation": "artifact.admit",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
                    "document": _puzzle_lineage()[0],
                    "dependency_artifact_ids": [],
                }

                def admit(service: object, index: int) -> dict[str, object]:
                    with patch(
                        "worldforge.studio.creation_job_protocol._reject_admission_secrets",
                        return_value=None,
                    ):
                        return service.creation_jobs.create_admission(
                            {**admission, "job_id": f"job_admit_concurrent_{index}"}
                        )

                first_admit, second_admit = _concurrent_submit(base, admit)
                self.assertEqual(first_admit["job_id"], second_admit["job_id"])
                retained_admit = primary.creation_jobs.get(first_admit["job_id"])
                primary.creation_jobs.cancel(
                    retained_admit["job_id"],
                    expected_generation=retained_admit["generation"],
                    expected_record_hash=retained_admit["record_hash"],
                )

                process = _asset_process_params(primary, workspace)

                def process_asset(service: object, index: int) -> dict[str, object]:
                    return service.creation_jobs.create_asset_process(
                        {**process, "job_id": f"job_process_concurrent_{index}"}
                    )

                first_process, second_process = _concurrent_submit(base, process_asset)
                self.assertEqual(first_process["job_id"], second_process["job_id"])
                self.assertEqual(
                    1,
                    primary.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_jobs WHERE workspace_id = ? "
                        "AND operation = 'asset.process' AND state IN ('queued', 'running')",
                        (workspace["workspace_id"],),
                    ).fetchone()[0],
                )
                self.assertEqual(
                    0,
                    primary.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_jobs WHERE workspace_id = ? "
                        "AND operation = 'artifact.admit' AND state IN ('queued', 'running')",
                        (workspace["workspace_id"],),
                    ).fetchone()[0],
                )
                self.assertEqual(
                    1,
                    primary.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_job_payloads WHERE job_id = ?",
                        (first_admit["job_id"],),
                    ).fetchone()[0],
                )
            finally:
                primary.close()
                primary.store.close()


class StudioCreationAssetJobContractTests(unittest.TestCase):
    def test_asset_lineage_validation_reuses_one_exact_gamepack_per_request(self) -> None:
        import worldforge.gamepack as gamepack_module
        from worldforge.creation_contracts import load_creation_project
        from worldforge.phase_report_v3 import validate_artifact_documents

        project = load_creation_project(_PUZZLE_ROOT / "project.json")
        with patch.object(
            gamepack_module,
            "_validate_gamepack_document_uncached",
            wraps=gamepack_module._validate_gamepack_document_uncached,
        ) as validate:
            checked = validate_artifact_documents(project, _puzzle_lineage())

        self.assertEqual(len(_puzzle_lineage()), len(checked))
        self.assertEqual(1, validate.call_count)

    def test_asset_process_execution_shares_one_exact_validation_scope(self) -> None:
        import worldforge.gamepack as gamepack_module
        from worldforge.studio.creation_job_protocol import (
            execute_private_creation_request,
        )

        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            request = self._request(stage)
            with patch.object(
                gamepack_module,
                "_validate_gamepack_document_uncached",
                wraps=gamepack_module._validate_gamepack_document_uncached,
            ) as validate:
                result = execute_private_creation_request(
                    request,
                    artifact_root=stage / "artifact_root",
                )

        self.assertEqual("passed", result.analysis_status)
        self.assertEqual(1, validate.call_count)

    def test_validation_memo_keys_exact_dependency_bytes(self) -> None:
        import worldforge.generic_assets as assets_module
        from worldforge.generic_assets import GenericAssetError, validate_asset_subject
        from worldforge.validation_memo import validation_memo_scope

        gamepack, subject, *_rest = _puzzle_lineage()
        tampered_gamepack = copy.deepcopy(gamepack)
        tampered_gamepack["game"]["title"] = "Tampered dependency without resealing"
        with (
            patch.object(
                assets_module,
                "_validate_asset_subject_uncached",
                wraps=assets_module._validate_asset_subject_uncached,
            ) as validate,
            validation_memo_scope(),
        ):
            self.assertEqual(
                subject,
                validate_asset_subject(subject, gamepack=gamepack),
            )
            self.assertEqual(
                subject,
                validate_asset_subject(subject, gamepack=gamepack),
            )
            with self.assertRaises(GenericAssetError):
                validate_asset_subject(subject, gamepack=tampered_gamepack)

        self.assertEqual(2, validate.call_count)

    def test_request_validation_memo_never_caches_staged_artifact_authority(self) -> None:
        from worldforge.studio.creation_job_protocol import (
            CreationWorkerProtocolError,
            execute_private_creation_request,
        )

        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            request = self._request(stage)
            staged = request["staged_inputs"][0]
            source = stage / "artifact_root" / staged["source_locator"]
            source.write_bytes(b"tampered after request construction")

            with self.assertRaises(CreationWorkerProtocolError) as raised:
                execute_private_creation_request(
                    request,
                    artifact_root=stage / "artifact_root",
                )

        self.assertEqual("private asset processing is not integral", str(raised.exception))
        self.assertNotIn(temporary, str(raised.exception))

    def _request(self, stage: Path) -> dict[str, object]:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_job_protocol import (
            build_private_asset_process_request,
        )

        lineage = _puzzle_lineage()
        receipt = lineage[7]
        specification = lineage[5]
        _stage_candidate(stage, receipt)
        outputs = receipt["outputs"]
        assert isinstance(outputs, list)
        staged_inputs = [
            {
                "candidate_artifact_id": output["candidate_artifact_id"],
                "role": output["role"],
                "source_locator": output["locator"],
                "sha256": output["sha256"],
                "size_bytes": output["size_bytes"],
            }
            for output in outputs
            if isinstance(output, dict)
        ]
        return build_private_asset_process_request(
            job_id="job_process_board",
            workspace_id="workspace_board",
            authority={
                "root_generation": 0,
                "source_revision": "a" * 64,
                "workflow_status_hash": None,
                "artifact_snapshot_hash": "b" * 64,
            },
            project=load_creation_project(_PUZZLE_ROOT / "project.json"),
            lineage_documents=lineage,
            recipe_id="board_ui_studio_recipe",
            processing_receipt_id="board_ui_studio_processing_receipt",
            qa_report_id="board_ui_studio_qa",
            acceptance_results=_acceptance_results(specification),
            staged_inputs=staged_inputs,
        )

    def test_v2_asset_process_is_deterministic_and_keeps_v1_closed(self) -> None:
        from worldforge.studio.creation_job_protocol import (
            execute_private_creation_request,
            validate_private_creation_request,
        )

        with (
            tempfile.TemporaryDirectory() as first_temp,
            tempfile.TemporaryDirectory() as second_temp,
        ):
            first_stage = Path(first_temp)
            second_stage = Path(second_temp)
            first_request = self._request(first_stage)
            second_request = self._request(second_stage)

            self.assertEqual(2, first_request["format_version"])
            self.assertEqual("asset.process", first_request["operation"])
            self.assertEqual(first_request, validate_private_creation_request(first_request))
            first_result = execute_private_creation_request(
                first_request,
                artifact_root=first_stage / "artifact_root",
            )
            second_result = execute_private_creation_request(
                second_request,
                artifact_root=second_stage / "artifact_root",
            )
            self.assertEqual(
                [
                    "world-forge.asset_processing_recipe",
                    "world-forge.asset_processing_receipt",
                    "world-forge.asset_qa_report",
                ],
                [output.subject["format"] for output in first_result.outputs],
            )
            self.assertEqual("passed", first_result.analysis_status)
            self.assertEqual((), first_result.reason_codes)
            self.assertEqual(
                [output.payload for output in first_result.outputs],
                [output.payload for output in second_result.outputs],
            )

            legacy = copy.deepcopy(first_request)
            legacy["format_version"] = 1
            with self.assertRaisesRegex(ValueError, "invalid fields|version|operation"):
                validate_private_creation_request(legacy)

    def test_controlled_processing_failure_preserves_recipe_and_failed_receipt(self) -> None:
        from worldforge.studio.creation_job_protocol import execute_private_creation_request

        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            request = self._request(stage)
            with patch(
                "worldforge.studio.creation_job_protocol.build_asset_processing_receipt",
                side_effect=_interrupt_processing_before_publication,
            ):
                result = execute_private_creation_request(
                    request,
                    artifact_root=stage / "artifact_root",
                )
            self.assertEqual("failed", result.analysis_status)
            self.assertEqual(("processor_interrupted",), result.reason_codes)
            self.assertEqual(2, len(result.outputs))
            documents = [json.loads(output.payload) for output in result.outputs]
            self.assertEqual("world-forge.asset_processing_recipe", documents[0]["format"])
            self.assertEqual("failed", documents[1]["status"])
            self.assertEqual(
                ["processor_interrupted"],
                documents[1]["failure_reasons"],
            )
            self.assertEqual([], documents[1]["recovery"]["retained_artifacts"])

    def test_asset_process_rejects_mixed_or_extra_lineage_and_forbidden_worker_capabilities(
        self,
    ) -> None:
        from worldforge.studio.creation_job_protocol import validate_private_creation_request
        from worldforge.studio.creation_worker import _worker_audit_hook

        with tempfile.TemporaryDirectory() as temporary:
            request = self._request(Path(temporary))
            mixed = copy.deepcopy(request)
            mixed["lineage_documents"].append(copy.deepcopy(mixed["lineage_documents"][0]))
            with self.assertRaisesRegex(ValueError, "exact|duplicate|lineage"):
                validate_private_creation_request(mixed)

        for event in (
            "socket.connect",
            "socket.getaddrinfo",
            "subprocess.Popen",
            "os.system",
            "os.posix_spawn",
        ):
            with self.subTest(event=event), self.assertRaisesRegex(PermissionError, "denied"):
                _worker_audit_hook(event, ())
        _worker_audit_hook("open", ())

    def test_v2_worker_executes_only_the_fixed_staged_asset_operation(self) -> None:
        from worldforge.studio.creation_executor import (
            _directory_proof,
            _verified_binary_outputs,
            _verified_outputs,
            verify_creation_stage_outputs,
            write_private_request,
        )
        from worldforge.studio.creation_worker import _execute

        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            request = self._request(stage)
            locator, digest = write_private_request(stage, request, locator="request_" + "a" * 32)
            response = _execute(
                {
                    "format": "world-forge.studio_creation_worker",
                    "format_version": 2,
                    "kind": "request",
                    "job_id": "job_process_board",
                    "operation": "asset.process",
                    "request_locator": locator,
                    "request_sha256": digest,
                },
                stage,
            )
            self.assertTrue(response["ok"])
            self.assertEqual(2, response["format_version"])
            self.assertEqual("passed", response["metadata"]["analysis_status"])
            self.assertEqual(3, len(response["outputs"]))
            receipt = json.loads((stage / "output_0002.json").read_bytes())
            processed = receipt["outputs"]
            self.assertEqual(1, len(processed))
            output_path = stage / "artifact_root" / processed[0]["locator"]
            self.assertEqual(
                processed[0]["sha256"], hashlib.sha256(output_path.read_bytes()).hexdigest()
            )

        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            request = self._request(stage)
            staged = request["staged_inputs"][0]
            source = stage / "artifact_root" / staged["source_locator"]
            replacement = source.with_suffix(".hardlink")
            source.rename(replacement)
            source.hardlink_to(replacement)
            locator, digest = write_private_request(stage, request, locator="request_" + "b" * 32)
            with self.assertRaisesRegex(ValueError, "integral|link|standalone|safe"):
                _execute(
                    {
                        "format": "world-forge.studio_creation_worker",
                        "format_version": 2,
                        "kind": "request",
                        "job_id": "job_process_board",
                        "operation": "asset.process",
                        "request_locator": locator,
                        "request_sha256": digest,
                    },
                    stage,
                )

        for extra_locator in ("unexpected.bin", "artifact_root/nested/unexpected.bin"):
            with (
                self.subTest(extra_locator=extra_locator),
                tempfile.TemporaryDirectory() as temporary,
            ):
                stage = Path(temporary)
                request = self._request(stage)
                locator, digest = write_private_request(
                    stage,
                    request,
                    locator="request_" + "c" * 32,
                )
                response = _execute(
                    {
                        "format": "world-forge.studio_creation_worker",
                        "format_version": 2,
                        "kind": "request",
                        "job_id": "job_process_board",
                        "operation": "asset.process",
                        "request_locator": locator,
                        "request_sha256": digest,
                    },
                    stage,
                )
                outputs = _verified_outputs(stage, response)
                binary_outputs = _verified_binary_outputs(stage, response, outputs, request)
                extra = stage.joinpath(*Path(extra_locator).parts)
                extra.parent.mkdir(parents=True, exist_ok=True)
                extra.write_bytes(b"unexpected")
                with self.assertRaisesRegex(ValueError, "stage.*exact|unexpected"):
                    verify_creation_stage_outputs(
                        stage,
                        _directory_proof(stage),
                        locator,
                        digest,
                        outputs,
                        binary_outputs,
                    )

    def test_public_asset_process_accepts_exactly_64_criteria_and_evidence_hashes(
        self,
    ) -> None:
        from worldforge.studio.contracts import (
            PROTOCOL_FORMAT,
            validate_studio_protocol_envelope,
        )

        def envelope(acceptance_results: list[dict[str, object]]) -> dict[str, object]:
            return {
                "protocol": PROTOCOL_FORMAT,
                "protocol_version": 4,
                "kind": "request",
                "request_id": "request_asset_bound",
                "method": "creation_job.create",
                "params": {
                    "workspace_id": "workspace_board",
                    "operation": "asset.process",
                    "expected_root_generation": 0,
                    "expected_source_revision": "a" * 64,
                    "expected_workflow_status_hash": None,
                    "expected_artifact_snapshot_hash": "b" * 64,
                    "license_artifact_ids": ["artifact_license_board"],
                    "recipe_id": "board_ui_recipe",
                    "processing_receipt_id": "board_ui_processing_receipt",
                    "qa_report_id": "board_ui_qa",
                    "acceptance_results": acceptance_results,
                },
            }

        _criteria, maximum = _bounded_acceptance_results(64)
        self.assertEqual(
            maximum,
            validate_studio_protocol_envelope(envelope(maximum))["params"]["acceptance_results"],
        )
        _criteria, oversized = _bounded_acceptance_results(65)
        with self.assertRaisesRegex(ValueError, "acceptance_results"):
            validate_studio_protocol_envelope(envelope(oversized))

        _criteria, maximum_evidence = _bounded_acceptance_results(
            1,
            evidence_count=64,
        )
        self.assertEqual(
            maximum_evidence,
            validate_studio_protocol_envelope(envelope(maximum_evidence))["params"][
                "acceptance_results"
            ],
        )
        _criteria, oversized_evidence = _bounded_acceptance_results(
            1,
            evidence_count=65,
        )
        with self.assertRaisesRegex(ValueError, "evidence_hashes"):
            validate_studio_protocol_envelope(envelope(oversized_evidence))

    def test_private_worker_accepts_exactly_64_criteria_and_evidence_hashes(
        self,
    ) -> None:
        from worldforge.studio.creation_job_protocol import (
            CreationWorkerProtocolError,
            _validate_acceptance_results,
        )

        maximum_criteria, maximum = _bounded_acceptance_results(64)
        self.assertEqual(
            maximum,
            _validate_acceptance_results(
                maximum,
                {"acceptance_criteria": maximum_criteria},
            ),
        )
        oversized_criteria, oversized = _bounded_acceptance_results(65)
        with self.assertRaisesRegex(CreationWorkerProtocolError, "not exact"):
            _validate_acceptance_results(
                oversized,
                {"acceptance_criteria": oversized_criteria},
            )

        single_criterion, maximum_evidence = _bounded_acceptance_results(
            1,
            evidence_count=64,
        )
        self.assertEqual(
            maximum_evidence,
            _validate_acceptance_results(
                maximum_evidence,
                {"acceptance_criteria": single_criterion},
            ),
        )
        _single_criterion, oversized_evidence = _bounded_acceptance_results(
            1,
            evidence_count=65,
        )
        with self.assertRaisesRegex(CreationWorkerProtocolError, "invalid"):
            _validate_acceptance_results(
                oversized_evidence,
                {"acceptance_criteria": single_criterion},
            )

    def test_published_schemas_and_catalog_expose_closed_asset_process_v2(self) -> None:
        job_schema = _document(_ROOT / "schemas/studio-creation-job.schema.json")
        worker_schema = _document(_ROOT / "schemas/studio-creation-worker.schema.json")
        protocol_schema = _document(_ROOT / "schemas/studio-protocol-v4.schema.json")
        catalog = _document(_ROOT / "contracts/catalog.json")

        self.assertEqual("World Forge Studio creation job v9", job_schema["title"])
        self.assertEqual(9, len(job_schema["oneOf"]))
        self.assertEqual(
            "World Forge Studio isolated creation worker envelope v11", worker_schema["title"]
        )
        self.assertEqual(33, len(worker_schema["oneOf"]))
        create_variants = protocol_schema["$defs"]["jobCreateParams"]["oneOf"]
        self.assertEqual(10, len(create_variants))
        self.assertEqual(
            "asset.process",
            create_variants[2]["properties"]["operation"]["const"],
        )
        self.assertEqual(
            64,
            create_variants[2]["properties"]["acceptance_results"]["maxItems"],
        )
        self.assertEqual(
            64,
            job_schema["$defs"]["assetProcessOperationParams"]["properties"]["acceptance_results"][
                "maxItems"
            ],
        )
        self.assertEqual(
            63,
            job_schema["$defs"]["acceptanceResult"]["properties"]["criterion_index"]["maximum"],
        )
        self.assertEqual(
            64,
            protocol_schema["$defs"]["assetProcessOperationParams"]["properties"][
                "acceptance_results"
            ]["maxItems"],
        )
        self.assertEqual(
            63,
            protocol_schema["$defs"]["assetProcessAcceptanceResult"]["properties"][
                "criterion_index"
            ]["maximum"],
        )
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        self.assertEqual(9, entries["studio-creation-job"]["version"])
        self.assertEqual(11, entries["studio-creation-worker"]["version"])
        for contract_id in (
            "studio-creation-job",
            "studio-creation-worker",
            "studio-protocol-v4",
        ):
            self.assertIn(
                "tests/test_studio_creation_asset_jobs_v4.py",
                entries[contract_id]["tests"],
            )

    def test_public_v4_and_worker_v2_versions_are_discriminated(self) -> None:
        from worldforge.studio.contracts import (
            validate_studio_creation_worker_envelope,
            validate_studio_protocol_envelope,
        )

        params = {
            "workspace_id": "workspace_board",
            "operation": "asset.process",
            "expected_root_generation": 0,
            "expected_source_revision": "a" * 64,
            "expected_workflow_status_hash": None,
            "expected_artifact_snapshot_hash": "b" * 64,
            "license_artifact_ids": ["artifact_license_board"],
            "recipe_id": "board_ui_recipe",
            "processing_receipt_id": "board_ui_processing_receipt",
            "qa_report_id": "board_ui_qa",
            "acceptance_results": _acceptance_results(_puzzle_lineage()[5]),
        }
        envelope = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "request_process_board",
            "method": "creation_job.create",
            "params": params,
        }
        self.assertEqual(
            "asset.process",
            validate_studio_protocol_envelope(envelope)["params"]["operation"],
        )
        leaked = copy.deepcopy(envelope)
        leaked["params"]["provider"] = "remote"
        with self.assertRaisesRegex(ValueError, "fields"):
            validate_studio_protocol_envelope(leaked)

        worker = {
            "format": "world-forge.studio_creation_worker",
            "format_version": 2,
            "kind": "request",
            "job_id": "job_process_board",
            "operation": "asset.process",
            "request_locator": "request_" + "c" * 32,
            "request_sha256": "d" * 64,
        }
        self.assertEqual(2, validate_studio_creation_worker_envelope(worker)["format_version"])
        legacy_asset = {**worker, "format_version": 1}
        with self.assertRaisesRegex(ValueError, "operation|version"):
            validate_studio_creation_worker_envelope(legacy_asset)
        v2_compile = {**worker, "operation": "creation.compile"}
        with self.assertRaisesRegex(ValueError, "operation|version"):
            validate_studio_creation_worker_envelope(v2_compile)


class StudioCreationAssetJobCoordinatorTests(unittest.TestCase):
    @staticmethod
    def _queued_asset_process(
        service: object,
        workspace: dict[str, object],
    ) -> tuple[dict[str, object], dict[str, object]]:
        from worldforge.phase_report_v3 import document_identity

        before, artifact_ids = _seed_lineage_candidates(service, workspace)
        license_document = _puzzle_lineage()[-1]
        license_id = artifact_ids[tuple(document_identity(license_document).values())]
        queued = service.creation_jobs.create_asset_process(
            {
                "job_id": "job_process_asset_board",
                "workspace_id": workspace["workspace_id"],
                "operation": "asset.process",
                "expected_root_generation": workspace["root_generation"],
                "expected_source_revision": workspace["source_revision"],
                "expected_workflow_status_hash": workspace["workflow_status_hash"],
                "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
                "license_artifact_ids": [license_id],
                "recipe_id": "board_ui_studio_recipe",
                "processing_receipt_id": "board_ui_studio_processing_receipt",
                "qa_report_id": "board_ui_studio_qa",
                "acceptance_results": _acceptance_results(_puzzle_lineage()[5]),
            }
        )
        return before, queued

    def test_asset_process_commits_exact_candidate_lineage_and_processed_bytes(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                processed = _processed_project_output(base)
                processed.unlink()
                before, queued = self._queued_asset_process(service, workspace)
                self.assertEqual(2, queued["format_version"])
                self.assertEqual("asset.process", queued["operation"])
                self.assertEqual("queued", queued["state"])
                self.assertNotIn("native_path", canonical_json_bytes(queued).decode("utf-8"))
                self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())

                completed = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("succeeded", completed["state"])
                self.assertEqual("committed", completed["progress"])
                self.assertEqual("passed", completed["result"]["analysis_status"])
                self.assertEqual(3, len(completed["result"]["output_artifact_ids"]))
                formats = [
                    service.creation_artifacts.get_document(workspace["workspace_id"], artifact_id)[
                        "format"
                    ]
                    for artifact_id in completed["result"]["output_artifact_ids"]
                ]
                self.assertEqual(
                    [
                        "world-forge.asset_processing_recipe",
                        "world-forge.asset_processing_receipt",
                        "world-forge.asset_qa_report",
                    ],
                    formats,
                )
                receipt = service.creation_artifacts.get_document(
                    workspace["workspace_id"], completed["result"]["output_artifact_ids"][1]
                )
                output = receipt["outputs"][0]
                retained = base / "project" / output["locator"]
                self.assertEqual(processed, retained)
                self.assertTrue(retained.is_file())
                self.assertEqual(output["size_bytes"], retained.stat().st_size)
                self.assertEqual(
                    output["sha256"],
                    hashlib.sha256(retained.read_bytes()).hexdigest(),
                )
                after = _snapshot(service, workspace)
                self.assertEqual(
                    completed["result"]["artifact_snapshot_hash"],
                    after["artifact_snapshot_hash"],
                )
                self.assertEqual(before["counts"]["active"], after["counts"]["active"])
                self.assertEqual(
                    before["counts"]["candidate"] + 3,
                    after["counts"]["candidate"],
                )
            finally:
                service.close()
                service.store.close()

    def test_controlled_processing_failure_commits_recipe_and_failed_receipt_only(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.creation_executor import (
            CreationWorkerExecution,
            _verified_binary_outputs,
            _verified_outputs,
        )
        from worldforge.studio.creation_worker import _execute

        worker_invoked = False

        def failed_worker(
            stage: Path,
            _stage_identity: tuple[int, int],
            envelope: object,
            **_kwargs: object,
        ) -> CreationWorkerExecution:
            nonlocal worker_invoked
            worker_invoked = True
            with patch(
                "worldforge.studio.creation_job_protocol.build_asset_processing_receipt",
                side_effect=_interrupt_processing_before_publication,
            ):
                response = _execute(envelope, stage)
            outputs = _verified_outputs(stage, response)
            request_document = json.loads(
                (stage / f"{envelope['request_locator']}.json").read_bytes()
            )
            return CreationWorkerExecution(
                response,
                outputs,
                _verified_binary_outputs(stage, response, outputs, request_document),
            )

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                processed = _processed_project_output(base)
                processed.unlink()
                before, queued = self._queued_asset_process(service, workspace)
                with patch(
                    "worldforge.studio.creation_jobs.run_isolated_creation_worker",
                    side_effect=failed_worker,
                ):
                    self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(queued["job_id"])
                self.assertTrue(worker_invoked)
                self.assertEqual("succeeded", completed["state"])
                self.assertEqual("committed", completed["progress"])
                self.assertEqual("failed", completed["result"]["analysis_status"])
                self.assertEqual(
                    ["processor_interrupted"],
                    completed["result"]["reason_codes"],
                )
                self.assertEqual(2, len(completed["result"]["output_artifact_ids"]))
                documents = [
                    service.creation_artifacts.get_document(workspace["workspace_id"], artifact_id)
                    for artifact_id in completed["result"]["output_artifact_ids"]
                ]
                self.assertEqual(
                    [
                        "world-forge.asset_processing_recipe",
                        "world-forge.asset_processing_receipt",
                    ],
                    [document["format"] for document in documents],
                )
                self.assertEqual("failed", documents[1]["status"])
                self.assertEqual([], documents[1]["recovery"]["retained_artifacts"])
                self.assertFalse(processed.exists())
                after = _snapshot(service, workspace)
                self.assertEqual(
                    before["counts"]["candidate"] + 2,
                    after["counts"]["candidate"],
                )
            finally:
                service.close()
                service.store.close()

    def test_partial_processing_publication_requires_recovery_without_project_output(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge import generic_asset_processing as processing_module
        from worldforge.studio.creation_executor import (
            CreationWorkerExecution,
            _verified_binary_outputs,
            _verified_outputs,
        )
        from worldforge.studio.creation_worker import _execute

        real_writer = processing_module.write_bytes_atomic
        worker_invoked = False

        def indeterminate_writer(
            path: str | Path,
            payload: bytes,
            *,
            durable_parent: bool = False,
        ) -> None:
            real_writer(path, payload, durable_parent=durable_parent)
            raise processing_module.AssetContractError(
                "Published output durability is indeterminate: simulated"
            )

        def partial_worker(
            stage: Path,
            _stage_identity: tuple[int, int],
            envelope: object,
            **_kwargs: object,
        ) -> CreationWorkerExecution:
            nonlocal worker_invoked
            worker_invoked = True
            with patch.object(
                processing_module,
                "write_bytes_atomic",
                side_effect=indeterminate_writer,
            ):
                response = _execute(envelope, stage)
            outputs = _verified_outputs(stage, response)
            request_document = json.loads(
                (stage / f"{envelope['request_locator']}.json").read_bytes()
            )
            return CreationWorkerExecution(
                response,
                outputs,
                _verified_binary_outputs(stage, response, outputs, request_document),
            )

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                processed = _processed_project_output(base)
                processed.unlink()
                before, queued = self._queued_asset_process(service, workspace)
                with patch(
                    "worldforge.studio.creation_jobs.run_isolated_creation_worker",
                    side_effect=partial_worker,
                ):
                    self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                orphaned = service.creation_jobs.get(queued["job_id"])
                self.assertTrue(worker_invoked)
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                self.assertEqual(before["counts"], _snapshot(service, workspace)["counts"])
                self.assertFalse(processed.exists())
                attempt = service.store.connection.execute(
                    "SELECT phase FROM creation_job_attempts WHERE job_id = ?",
                    (queued["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                self.assertEqual("worker_started", attempt["phase"])
            finally:
                service.close()
                service.store.close()

    def test_unexpected_private_worker_file_never_reaches_publication_or_commit(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.creation_executor import (
            CreationWorkerExecution,
            _verified_binary_outputs,
            _verified_outputs,
        )
        from worldforge.studio.creation_worker import _execute

        def worker_with_extra_file(
            stage: Path,
            _stage_identity: tuple[int, int],
            envelope: object,
            **_kwargs: object,
        ) -> CreationWorkerExecution:
            response = _execute(envelope, stage)
            outputs = _verified_outputs(stage, response)
            request_document = json.loads(
                (stage / f"{envelope['request_locator']}.json").read_bytes()
            )
            binary_outputs = _verified_binary_outputs(
                stage,
                response,
                outputs,
                request_document,
            )
            (stage / "unexpected.bin").write_bytes(b"unexpected")
            return CreationWorkerExecution(response, outputs, binary_outputs)

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                processed = _processed_project_output(base)
                processed.unlink()
                before, queued = self._queued_asset_process(service, workspace)
                with patch(
                    "worldforge.studio.creation_jobs.run_isolated_creation_worker",
                    side_effect=worker_with_extra_file,
                ):
                    self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                rejected = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("orphaned", rejected["state"])
                self.assertEqual("recovery_required", rejected["error"]["code"])
                after = _snapshot(service, workspace)
                self.assertEqual(before["counts"], after["counts"])
                self.assertFalse(processed.exists())
            finally:
                service.close()
                service.store.close()

    def test_post_worker_publication_error_requires_explicit_recovery(self) -> None:
        from tests.test_studio_creation_jobs_v4 import (
            _assert_linux_recovery_required,
            _prepared_creation_service,
        )
        from worldforge.studio import creation_jobs as creation_jobs_module
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                processed = _processed_project_output(base)
                processed.unlink()
                _before, queued = self._queued_asset_process(service, workspace)
                observed_codes: list[str] = []
                finish_after_error = service.creation_job_coordinator._finish_after_error

                def capture_finish(job_id: str, code: str, **kwargs: object) -> None:
                    observed_codes.append(code)
                    finish_after_error(job_id, code, **kwargs)

                with (
                    patch.object(
                        creation_jobs_module,
                        "write_bytes_atomic",
                        side_effect=OSError("simulated pre-visible project publication failure"),
                    ),
                    patch.object(
                        service.creation_job_coordinator,
                        "_finish_after_error",
                        side_effect=capture_finish,
                    ),
                ):
                    self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                self.assertEqual(["recovery_required"], observed_codes)
                orphaned = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                self.assertFalse(processed.exists())
                attempt = service.store.connection.execute(
                    "SELECT phase FROM creation_job_attempts WHERE job_id = ?",
                    (queued["job_id"],),
                ).fetchone()
                self.assertEqual("output_published", attempt["phase"])

                with self.assertRaisesRegex(StudioError, "recovery_required|retained"):
                    service.creation_jobs.recover(
                        queued["job_id"],
                        mode="resume",
                        expected_generation=orphaned["generation"],
                        expected_record_hash=orphaned["record_hash"],
                    )
                if sys.platform.startswith("linux") and os.name == "posix":
                    _assert_linux_recovery_required(self, service, queued["job_id"])
                else:
                    preserved = service.creation_jobs.get(queued["job_id"])
                    self.assertEqual("orphaned", preserved["state"])
                    self.assertIsNotNone(
                        service.store.connection.execute(
                            "SELECT 1 FROM creation_job_attempts WHERE job_id = ?",
                            (queued["job_id"],),
                        ).fetchone()
                    )
            finally:
                service.close()
                service.store.close()

    def test_visible_project_publication_is_recovery_bound_and_never_committed(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio import creation_jobs as creation_jobs_module

        real_writer = creation_jobs_module.write_bytes_atomic

        def indeterminate_writer(
            path: str | Path,
            payload: bytes,
            *,
            durable_parent: bool = False,
        ) -> None:
            real_writer(path, payload, durable_parent=durable_parent)
            raise creation_jobs_module.AssetContractError(
                "Published output durability is indeterminate: simulated"
            )

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                retained = _processed_project_output(base)
                retained.unlink()
                before, queued = self._queued_asset_process(service, workspace)
                observed_codes: list[str] = []
                finish_after_error = service.creation_job_coordinator._finish_after_error

                def capture_finish(job_id: str, code: str, **kwargs: object) -> None:
                    observed_codes.append(code)
                    finish_after_error(job_id, code, **kwargs)

                with (
                    patch.object(
                        creation_jobs_module,
                        "write_bytes_atomic",
                        side_effect=indeterminate_writer,
                    ),
                    patch.object(
                        service.creation_job_coordinator,
                        "_finish_after_error",
                        side_effect=capture_finish,
                    ),
                ):
                    self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                self.assertEqual(["recovery_required"], observed_codes)
                orphaned = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                self.assertEqual(before["counts"], _snapshot(service, workspace)["counts"])
                self.assertTrue(retained.is_file())
                self.assertEqual(
                    _PUZZLE_ROOT.joinpath(retained.relative_to(base / "project")).read_bytes(),
                    retained.read_bytes(),
                )
            finally:
                service.close()
                service.store.close()

    def test_foreign_project_publication_is_recovery_bound_and_preserved(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                foreign = _processed_project_output(base)
                foreign.unlink()
                before, queued = self._queued_asset_process(service, workspace)
                foreign_payload = b"foreign-project-output"
                foreign.write_bytes(foreign_payload)
                observed_codes: list[str] = []
                finish_after_error = service.creation_job_coordinator._finish_after_error

                def capture_finish(job_id: str, code: str, **kwargs: object) -> None:
                    observed_codes.append(code)
                    finish_after_error(job_id, code, **kwargs)

                with patch.object(
                    service.creation_job_coordinator,
                    "_finish_after_error",
                    side_effect=capture_finish,
                ):
                    self.assertEqual(queued["job_id"], service.creation_job_coordinator.run_once())
                self.assertEqual(["recovery_required"], observed_codes)
                orphaned = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                self.assertEqual(before["counts"], _snapshot(service, workspace)["counts"])
                self.assertEqual(foreign_payload, foreign.read_bytes())
            finally:
                service.close()
                service.store.close()

    def test_registry_failure_after_project_publication_cannot_downgrade_to_failed(
        self,
    ) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                retained = _processed_project_output(base)
                retained.unlink()
                before, queued = self._queued_asset_process(service, workspace)
                with (
                    patch.object(
                        service.creation_job_coordinator,
                        "_commit_registry",
                        side_effect=RuntimeError("simulated registry failure"),
                    ),
                    patch.object(
                        service.creation_job_coordinator,
                        "_recover_cleanup_with_evidence",
                    ) as cleanup,
                ):
                    self.assertEqual(
                        queued["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                cleanup.assert_not_called()
                orphaned = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                self.assertEqual(before["counts"], _snapshot(service, workspace)["counts"])
                self.assertEqual(
                    _PUZZLE_ROOT.joinpath(retained.relative_to(base / "project")).read_bytes(),
                    retained.read_bytes(),
                )
                attempt = service.store.connection.execute(
                    "SELECT phase FROM creation_job_attempts WHERE job_id = ?",
                    (queued["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                self.assertEqual("registry_committing", attempt["phase"])
            finally:
                service.close()
                service.store.close()

    def test_project_publication_before_registry_phase_is_recovery_bound(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                retained = _processed_project_output(base)
                retained.unlink()
                before, queued = self._queued_asset_process(service, workspace)
                real_progress = service.creation_jobs.progress

                def interrupted_progress(job_id: str, progress: str) -> object:
                    if progress == "registry_committing":
                        raise RuntimeError("simulated phase persistence failure")
                    return real_progress(job_id, progress)

                with (
                    patch.object(
                        service.creation_jobs,
                        "progress",
                        side_effect=interrupted_progress,
                    ),
                    patch.object(
                        service.creation_job_coordinator,
                        "_recover_cleanup_with_evidence",
                    ) as cleanup,
                ):
                    self.assertEqual(
                        queued["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                cleanup.assert_not_called()
                orphaned = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                self.assertEqual(before["counts"], _snapshot(service, workspace)["counts"])
                self.assertEqual(
                    _PUZZLE_ROOT.joinpath(retained.relative_to(base / "project")).read_bytes(),
                    retained.read_bytes(),
                )
                attempt = service.store.connection.execute(
                    "SELECT phase FROM creation_job_attempts WHERE job_id = ?",
                    (queued["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                self.assertEqual("output_published", attempt["phase"])
            finally:
                service.close()
                service.store.close()

    def test_project_output_mutation_before_registry_commit_is_rejected(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                retained = _processed_project_output(base)
                retained.unlink()
                before, queued = self._queued_asset_process(service, workspace)
                real_progress = service.creation_jobs.progress
                foreign_payload = b"foreign-output-after-project-publication"

                def mutate_after_transition(job_id: str, progress: str) -> object:
                    result = real_progress(job_id, progress)
                    if progress == "registry_committing":
                        retained.write_bytes(foreign_payload)
                    return result

                with patch.object(
                    service.creation_jobs,
                    "progress",
                    side_effect=mutate_after_transition,
                ):
                    self.assertEqual(
                        queued["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                orphaned = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                self.assertEqual(before["counts"], _snapshot(service, workspace)["counts"])
                self.assertEqual(foreign_payload, retained.read_bytes())
                attempt = service.store.connection.execute(
                    "SELECT phase FROM creation_job_attempts WHERE job_id = ?",
                    (queued["job_id"],),
                ).fetchone()
                self.assertIsNotNone(attempt)
                self.assertEqual("registry_committing", attempt["phase"])
            finally:
                service.close()
                service.store.close()

    def test_restart_keeps_registry_committing_asset_publication_recovery_bound(
        self,
    ) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.creation_jobs import CreationJobCoordinator
        from worldforge.studio.errors import StudioError
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            retained = _processed_project_output(base)
            retained.unlink()
            before, queued = self._queued_asset_process(service, workspace)

            def interrupt_registry(*_args: object, **_kwargs: object) -> object:
                service.creation_job_coordinator.shutdown_requested = lambda: True
                raise RuntimeError("simulated shutdown during registry commit")

            try:
                with patch.object(
                    service.creation_job_coordinator,
                    "_commit_registry",
                    side_effect=interrupt_registry,
                ):
                    self.assertEqual(
                        queued["job_id"],
                        service.creation_job_coordinator.run_once(),
                    )
                interrupted = service.creation_jobs.get(queued["job_id"])
                self.assertEqual("running", interrupted["state"])
                self.assertEqual("registry_committing", interrupted["progress"])
                self.assertEqual(before["counts"], _snapshot(service, workspace)["counts"])
                self.assertTrue(retained.is_file())
            finally:
                service.close()
                service.store.close()

            restarted = StudioService(StudioStore(base / "studio"))
            try:
                orphaned = restarted.creation_jobs.get(queued["job_id"])
                self.assertEqual("orphaned", orphaned["state"])
                self.assertEqual("recovery_required", orphaned["error"]["code"])
                with (
                    patch.object(
                        CreationJobCoordinator,
                        "_recover_cleanup_with_evidence",
                    ) as cleanup,
                    self.assertRaisesRegex(
                        StudioError,
                        "publication.*recovery|recovery.*publication",
                    ),
                ):
                    restarted.creation_jobs.recover(
                        queued["job_id"],
                        mode="rollback",
                        expected_generation=orphaned["generation"],
                        expected_record_hash=orphaned["record_hash"],
                    )
                cleanup.assert_not_called()
                preserved = restarted.creation_jobs.get(queued["job_id"])
                self.assertEqual("orphaned", preserved["state"])
                self.assertEqual(
                    _PUZZLE_ROOT.joinpath(retained.relative_to(base / "project")).read_bytes(),
                    retained.read_bytes(),
                )
                self.assertIsNotNone(
                    restarted.store.connection.execute(
                        "SELECT 1 FROM creation_job_attempts WHERE job_id = ?",
                        (queued["job_id"],),
                    ).fetchone()
                )
            finally:
                restarted.close()
                restarted.store.close()


if __name__ == "__main__":
    unittest.main()
