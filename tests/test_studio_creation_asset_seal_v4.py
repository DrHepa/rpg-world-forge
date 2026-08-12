from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_studio_creation_asset_jobs_v4 as _asset_job_fixtures
from tests.test_studio_creation_asset_jobs_v4 import (
    _acceptance_results,
    _puzzle_lineage,
    _seed_lineage_candidates,
)
from worldforge.integrity import canonical_payload_hash

_ROOT = Path(__file__).resolve().parents[1]


def _grant_record(*, state: str = "ready", generation: int = 0) -> dict[str, object]:
    return {
        "format": "world-forge.studio_creation_output_grant",
        "format_version": 1,
        "grant_id": "grant_assetpack_output",
        "workspace_id": "workspace_puzzle",
        "kind": "generic_assetpack_directory",
        "display_name": "puzzle-assets",
        "state": state,
        "generation": generation,
        "publication": None,
        "created_at": "2026-08-02T00:00:00.000000Z",
        "updated_at": "2026-08-02T00:00:00.000000Z",
    }


class StudioCreationOutputGrantContractTests(unittest.TestCase):
    def test_published_schemas_expose_closed_v4_runtime_and_pathless_grants(self) -> None:
        job_schema = json.loads(
            (_ROOT / "schemas/studio-creation-job.schema.json").read_text(encoding="utf-8")
        )
        worker_schema = json.loads(
            (_ROOT / "schemas/studio-creation-worker.schema.json").read_text(encoding="utf-8")
        )
        output_schema = json.loads(
            (_ROOT / "schemas/studio-creation-output-grant.schema.json").read_text(encoding="utf-8")
        )
        protocol_schema = json.loads(
            (_ROOT / "schemas/studio-protocol-v4.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual("World Forge Studio creation job v9", job_schema["title"])
        self.assertEqual(9, len(job_schema["oneOf"]))
        self.assertEqual(
            "asset.release.seal",
            job_schema["oneOf"][2]["properties"]["operation"]["const"],
        )
        self.assertEqual(
            "World Forge Studio isolated creation worker envelope v11",
            worker_schema["title"],
        )
        self.assertEqual(33, len(worker_schema["oneOf"]))
        self.assertEqual(
            "world-forge.studio_creation_output_grant",
            output_schema["properties"]["format"]["const"],
        )
        self.assertTrue(
            {
                "path",
                "absolute_path",
                "reserved_job_id",
                "recovery",
            }.isdisjoint(output_schema["properties"])
        )
        self.assertEqual(10, len(protocol_schema["$defs"]["jobCreateParams"]["oneOf"]))
        self.assertEqual(17, len(protocol_schema["$defs"]["method"]["enum"]))
        self.assertIn(
            "creation_output_grant.list",
            protocol_schema["$defs"]["method"]["enum"],
        )

    def test_output_grant_v1_is_closed_pathless_and_keeps_published_outputs_reusable(self) -> None:
        from worldforge.studio.contracts import validate_studio_creation_output_grant

        ready = _grant_record()
        self.assertEqual(ready, validate_studio_creation_output_grant(ready))
        published = {
            **ready,
            "state": "published",
            "generation": 2,
            "publication": {
                "format": "world-forge.assetpack",
                "format_version": 1,
                "id": "assetpack_puzzle",
                "content_hash": "a" * 64,
                "inventory_hash": "b" * 64,
            },
        }
        self.assertEqual(published, validate_studio_creation_output_grant(published))
        for forbidden in (
            "path",
            "absolute_path",
            "parent_dev",
            "parent_ino",
            "normalized_leaf",
            "reserved_job_id",
            "expected_manifest_hash",
            "expected_tree_hash",
        ):
            leaked = copy.deepcopy(ready)
            leaked[forbidden] = "secret"
            with self.subTest(forbidden=forbidden), self.assertRaisesRegex(ValueError, "fields"):
                validate_studio_creation_output_grant(leaked)

    def test_asset_release_job_v3_has_a_closed_pathless_publication_result(self) -> None:
        from worldforge.studio.contracts import validate_studio_creation_job

        record: dict[str, object] = {
            "format": "world-forge.studio_creation_job",
            "format_version": 3,
            "job_id": "job_seal_assets",
            "workspace_id": "workspace_puzzle",
            "operation": "asset.release.seal",
            "operation_params": {
                "qa_report_artifact_ids": ["artifact_qa_board"],
                "manifest_id": "puzzle_release_manifest",
                "target_grant_id": "grant_assetpack_output",
                "target_grant_generation": 1,
            },
            "state": "succeeded",
            "generation": 6,
            "authority": {
                "root_generation": 0,
                "source_revision": "c" * 64,
                "workflow_status_hash": None,
                "artifact_snapshot_hash": "d" * 64,
            },
            "inputs": [],
            "progress": "committed",
            "result": {
                "output_artifact_ids": [
                    "artifact_release_manifest",
                    "artifact_assetpack_manifest",
                ],
                "artifact_snapshot_hash": "e" * 64,
                "analysis_status": "passed",
                "reason_codes": [],
                "cleanup_pending": False,
                "publication": {
                    "grant_id": "grant_assetpack_output",
                    "grant_generation": 2,
                    "kind": "generic_assetpack_directory",
                    "state": "published",
                    "assetpack": {
                        "format": "world-forge.assetpack",
                        "format_version": 1,
                        "id": "assetpack_puzzle",
                        "content_hash": "a" * 64,
                        "inventory_hash": "b" * 64,
                    },
                },
            },
            "error": None,
            "created_at": "2026-08-02T00:00:00.000000Z",
            "started_at": "2026-08-02T00:00:01.000000Z",
            "finished_at": "2026-08-02T00:00:02.000000Z",
            "updated_at": "2026-08-02T00:00:02.000000Z",
            "record_hash": "",
        }
        record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
        self.assertEqual(record, validate_studio_creation_job(record))
        leaked = copy.deepcopy(record)
        leaked["result"]["publication"]["path"] = "/private/output"  # type: ignore[index]
        leaked["record_hash"] = canonical_payload_hash(leaked, hash_field="record_hash")
        with self.assertRaisesRegex(ValueError, "fields"):
            validate_studio_creation_job(leaked)

    def test_protocol_v4_seal_request_is_closed_and_requires_output_authority(self) -> None:
        from worldforge.studio.contracts import validate_studio_protocol_envelope

        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "seal-request",
            "method": "creation_job.create",
            "params": {
                "job_id": "job_seal_assets",
                "workspace_id": "workspace_puzzle",
                "operation": "asset.release.seal",
                "expected_root_generation": 0,
                "expected_source_revision": "a" * 64,
                "expected_workflow_status_hash": None,
                "expected_artifact_snapshot_hash": "b" * 64,
                "qa_report_artifact_ids": ["artifact_qa_board"],
                "manifest_id": "puzzle_release_manifest",
                "target_grant_id": "grant_assetpack_output",
                "expected_target_grant_generation": 0,
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        for forbidden in ("path", "kind", "target_path"):
            leaked = copy.deepcopy(request)
            leaked["params"][forbidden] = "/private/output"  # type: ignore[index]
            with self.subTest(forbidden=forbidden), self.assertRaisesRegex(ValueError, "fields"):
                validate_studio_protocol_envelope(leaked)


class StudioCreationOutputGrantServiceTests(unittest.TestCase):
    def test_protocol_v4_output_grant_crud_is_pathless_and_exposes_capability(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                initialized = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "initialize-output-grants",
                        "method": "service.initialize",
                        "params": {},
                    }
                )["result"]
                self.assertTrue(initialized["capabilities"]["creation_output_grants"])
                self.assertTrue(initialized["capabilities"]["creation_runtime_compose"])
                self.assertTrue(initialized["capabilities"]["creation_runtime_bundle"])

                output_parent = base / "outputs"
                output_parent.mkdir()
                target = output_parent / "service-assets"
                created = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "create-output-grant",
                        "method": "creation_output_grant.create",
                        "params": {
                            "grant_id": "grant_service_output",
                            "workspace_id": workspace["workspace_id"],
                            "kind": "generic_assetpack_directory",
                            "display_name": "service-assets",
                            "path": str(target),
                        },
                    }
                )
                self.assertIn("result", created, created)
                grant = created["result"]["grant"]
                self.assertNotIn(str(output_parent), json.dumps(created))
                fetched = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "get-output-grant",
                        "method": "creation_output_grant.get",
                        "params": {"grant_id": grant["grant_id"]},
                    }
                )
                self.assertEqual(grant, fetched["result"]["grant"])
                revoked = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "revoke-output-grant",
                        "method": "creation_output_grant.revoke",
                        "params": {
                            "grant_id": grant["grant_id"],
                            "expected_generation": grant["generation"],
                        },
                    }
                )
                self.assertEqual("revoked", revoked["result"]["grant"]["state"])
            finally:
                service.close()
                service.store.close()

    def test_protocol_v4_output_grant_list_is_exact_bounded_pathless_and_paginated(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                output_parent = base / "outputs"
                output_parent.mkdir()
                created = []
                for grant_id in (
                    "grant_service_output_c",
                    "grant_service_output_a",
                    "grant_service_output_b",
                ):
                    created.append(
                        service.creation_output_grants.create(
                            {
                                "grant_id": grant_id,
                                "workspace_id": workspace["workspace_id"],
                                "kind": "generic_assetpack_directory",
                                "display_name": grant_id,
                                "path": str(output_parent / grant_id),
                            }
                        )
                    )
                service.creation_output_grants.revoke(
                    created[0]["grant_id"],
                    expected_generation=created[0]["generation"],
                )
                evidence = service.creation_evidence.list(
                    {
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": None,
                        "lifecycle": None,
                        "cursor": None,
                        "limit": 1,
                    }
                )
                authority = {
                    "workspace_id": workspace["workspace_id"],
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": evidence["artifact_snapshot_hash"],
                }

                first = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "list-output-grants-1",
                        "method": "creation_output_grant.list",
                        "params": {**authority, "cursor": None, "limit": 2},
                    }
                )
                self.assertIn("result", first, first)
                self.assertEqual(
                    ["grant_service_output_a", "grant_service_output_b"],
                    [grant["grant_id"] for grant in first["result"]["grants"]],
                )
                self.assertEqual("grant_service_output_b", first["result"]["next_cursor"])
                self.assertEqual(
                    evidence["artifact_snapshot_hash"],
                    first["result"]["artifact_snapshot_hash"],
                )
                self.assertEqual(evidence["authority"], first["result"]["authority"])
                self.assertNotIn(str(output_parent), json.dumps(first))

                second = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 4,
                        "kind": "request",
                        "request_id": "list-output-grants-2",
                        "method": "creation_output_grant.list",
                        "params": {
                            **authority,
                            "cursor": first["result"]["next_cursor"],
                            "limit": 2,
                        },
                    }
                )
                self.assertEqual(
                    ["grant_service_output_c"],
                    [grant["grant_id"] for grant in second["result"]["grants"]],
                )
                self.assertEqual("revoked", second["result"]["grants"][0]["state"])
                self.assertIsNone(second["result"]["next_cursor"])

                with self.assertRaisesRegex(StudioError, "authority changed") as raised:
                    service.handle(
                        {
                            "protocol": "rpg-world-forge.studio_protocol",
                            "protocol_version": 4,
                            "kind": "request",
                            "request_id": "list-output-grants-stale",
                            "method": "creation_output_grant.list",
                            "params": {
                                **authority,
                                "expected_source_revision": "0" * 64,
                                "cursor": None,
                                "limit": 8,
                            },
                        }
                    )
                self.assertEqual("conflict", raised.exception.code)
            finally:
                service.close()
                service.store.close()


class StudioCreationAssetSealWorkerTests(unittest.TestCase):
    @staticmethod
    def _request(stage: Path) -> dict[str, object]:
        from worldforge.creation_contracts import load_creation_project
        from worldforge.studio.creation_job_protocol import (
            build_private_asset_release_seal_request,
            execute_private_creation_request,
        )

        process_request = _asset_job_fixtures.StudioCreationAssetJobContractTests()._request(stage)
        processed = execute_private_creation_request(
            process_request,
            artifact_root=stage / "artifact_root",
        )
        if processed.analysis_status != "passed":
            raise AssertionError(processed.reason_codes)
        processing_documents = tuple(json.loads(output.payload) for output in processed.outputs)
        production_receipt = next(
            document
            for document in _puzzle_lineage()
            if document["format"] == "world-forge.asset_production_receipt"
        )
        processing_receipt = processing_documents[1]
        staged_inputs = sorted(
            [
                {
                    "asset_id": receipt["asset"]["asset_id"],
                    "role": output["role"],
                    "source_locator": output["locator"],
                    "sha256": output["sha256"],
                    "size_bytes": output["size_bytes"],
                }
                for receipt in (production_receipt, processing_receipt)
                for output in receipt["outputs"]
            ],
            key=lambda item: (
                item["asset_id"].encode("utf-8"),
                item["role"].encode("utf-8"),
                item["source_locator"].encode("utf-8"),
            ),
        )
        return build_private_asset_release_seal_request(
            job_id="job_seal_assets",
            workspace_id="workspace_puzzle",
            authority={
                "root_generation": 0,
                "source_revision": "c" * 64,
                "workflow_status_hash": None,
                "artifact_snapshot_hash": "d" * 64,
            },
            project=load_creation_project(
                _ROOT / "examples/multigenre-contracts/abstract-puzzle/project.json"
            ),
            lineage_documents=(*_puzzle_lineage(), *processing_documents),
            manifest_id="puzzle_release_manifest",
            target_grant_id="grant_assetpack_output",
            target_grant_generation=1,
            staged_inputs=staged_inputs,
        )

    def test_v3_seal_worker_requires_retained_v10_v11_authority(self) -> None:
        from worldforge.studio.creation_job_protocol import (
            CreationWorkerProtocolError,
            execute_private_creation_request,
            validate_private_creation_request,
        )

        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            request = self._request(stage)
            self.assertEqual(3, request["format_version"])
            self.assertEqual("asset.release.seal", request["operation"])
            self.assertEqual(request, validate_private_creation_request(request))
            with self.assertRaisesRegex(
                CreationWorkerProtocolError,
                "asset_release_authority_required.*v10.*v11",
            ):
                execute_private_creation_request(
                    request,
                    artifact_root=stage / "artifact_root",
                )

    def test_v3_seal_wire_validation_remains_strict_before_execution_is_disabled(self) -> None:
        from worldforge.studio.creation_job_protocol import (
            CreationWorkerProtocolError,
            execute_private_creation_request,
            validate_private_creation_request,
        )

        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            request = self._request(stage)
            missing_qa = copy.deepcopy(request)
            missing_qa["lineage_documents"] = [
                document
                for document in missing_qa["lineage_documents"]
                if document["format"] != "world-forge.asset_qa_report"
            ]
            with self.assertRaisesRegex(ValueError, "QA|coverage|lineage|exact"):
                validate_private_creation_request(missing_qa)

            processed = request["staged_inputs"][0]
            output = stage / "artifact_root" / processed["source_locator"]
            output.write_bytes(b"tampered")
            with self.assertRaisesRegex(
                CreationWorkerProtocolError,
                "asset_release_authority_required.*v10.*v11",
            ):
                execute_private_creation_request(
                    request,
                    artifact_root=stage / "artifact_root",
                )


class StudioCreationOutputGrantManagerTests(unittest.TestCase):
    def test_grant_creation_is_pathless_and_rejects_aliases_links_and_hardlinks(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                output_parent = base / "outputs"
                output_parent.mkdir()
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_assetpack_output",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "generic_assetpack_directory",
                        "display_name": "puzzle-assets",
                        "path": str(output_parent / "puzzle-assets"),
                    }
                )
                self.assertEqual("ready", grant["state"])
                self.assertNotIn(str(output_parent), json.dumps(grant))

                alias = output_parent / "PUZZLE-ASSETS"
                alias.mkdir()
                with self.assertRaisesRegex(StudioError, "collision|absent"):
                    service.creation_output_grants.create(
                        {
                            "grant_id": "grant_alias",
                            "workspace_id": workspace["workspace_id"],
                            "kind": "generic_assetpack_directory",
                            "display_name": "alias",
                            "path": str(output_parent / "puzzle-assets"),
                        }
                    )
                alias.rmdir()

                link = output_parent / "linked"
                link.symlink_to(output_parent, target_is_directory=True)
                with self.assertRaisesRegex(StudioError, "link|reparse|absent|collision"):
                    service.creation_output_grants.create(
                        {
                            "grant_id": "grant_link",
                            "workspace_id": workspace["workspace_id"],
                            "kind": "generic_assetpack_directory",
                            "display_name": "link",
                            "path": str(link),
                        }
                    )
            finally:
                service.close()
                service.store.close()


def _prepare_processed_creation_service(base: Path):
    from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
    from worldforge.phase_report_v3 import document_identity

    service, workspace = _prepared_creation_service(base)
    current, artifact_ids = _seed_lineage_candidates(service, workspace)
    license_document = _puzzle_lineage()[-1]
    license_id = artifact_ids[tuple(document_identity(license_document).values())]
    queued = service.creation_jobs.create_asset_process(
        {
            "job_id": "job_process_asset_for_seal",
            "workspace_id": workspace["workspace_id"],
            "operation": "asset.process",
            "expected_root_generation": workspace["root_generation"],
            "expected_source_revision": workspace["source_revision"],
            "expected_workflow_status_hash": workspace["workflow_status_hash"],
            "expected_artifact_snapshot_hash": current["artifact_snapshot_hash"],
            "license_artifact_ids": [license_id],
            "recipe_id": "board_ui_studio_recipe",
            "processing_receipt_id": "board_ui_studio_processing_receipt",
            "qa_report_id": "board_ui_studio_qa",
            "acceptance_results": _acceptance_results(_puzzle_lineage()[5]),
        }
    )
    if service.creation_job_coordinator.run_once() != queued["job_id"]:
        raise AssertionError("asset processing job was not claimed")
    completed = service.creation_jobs.get(queued["job_id"])
    if completed["state"] != "succeeded":
        raise AssertionError(completed)
    snapshot = service.creation_evidence.list(
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
    qa_ids = [
        record["artifact_id"]
        for record in snapshot["artifacts"]
        if record["subject"]["format"] == "world-forge.asset_qa_report"
    ]
    if len(qa_ids) != 1:
        raise AssertionError(qa_ids)
    return service, workspace, snapshot, qa_ids


class StudioCreationAssetSealCoordinatorTests(unittest.TestCase):
    def test_new_v3_seal_creation_requires_v10_v11_authority_without_grant_mutation(
        self,
    ) -> None:
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace, before, qa_ids = _prepare_processed_creation_service(base)
            try:
                output_parent = base / "outputs"
                output_parent.mkdir()
                target = output_parent / "legacy-assets"
                grant = service.creation_output_grants.create(
                    {
                        "grant_id": "grant_legacy_assetpack",
                        "workspace_id": workspace["workspace_id"],
                        "kind": "generic_assetpack_directory",
                        "display_name": "legacy-assets",
                        "path": str(target),
                    }
                )
                retained_before = service.creation_output_grants.get(grant["grant_id"])
                params = {
                    "job_id": "job_legacy_seal_rejected",
                    "workspace_id": workspace["workspace_id"],
                    "operation": "asset.release.seal",
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": before["artifact_snapshot_hash"],
                    "qa_report_artifact_ids": qa_ids,
                    "manifest_id": "legacy_release_manifest",
                    "target_grant_id": grant["grant_id"],
                    "expected_target_grant_generation": grant["generation"],
                }
                with self.assertRaisesRegex(
                    StudioError,
                    "asset_release_authority_required.*v10.*v11",
                ):
                    service.creation_jobs.create_asset_release_seal(params)

                self.assertEqual(
                    retained_before,
                    service.creation_output_grants.get(grant["grant_id"]),
                )
                self.assertFalse(target.exists())
                self.assertEqual(
                    0,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_jobs WHERE job_id = ?",
                        (params["job_id"],),
                    ).fetchone()[0],
                )
            finally:
                service.close()
                service.store.close()

    @unittest.skipUnless(
        (os.name == "posix") or os.name == "nt",
        "exact assetpack rollback requires Linux or Windows primitives",
    )
    def test_rollback_parent_rename_away_is_not_recreated(self) -> None:
        from worldforge.__main__ import _resolve_generic_assetpack_cli_source
        from worldforge.generic_assetpack import seal_generic_assetpack
        from worldforge.studio.creation_jobs import CreationJobCoordinator
        from worldforge.studio.errors import StudioError

        source = _resolve_generic_assetpack_cli_source(
            _ROOT
            / "examples"
            / "multigenre-contracts"
            / "abstract-puzzle"
            / "assets"
            / "manifest.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output_parent = base / "outputs"
            displaced_parent = base / "outputs-displaced"
            output_parent.mkdir()
            destination = output_parent / "sealed"
            parent_info = output_parent.stat()
            parent_identity = (parent_info.st_dev, parent_info.st_ino)
            with seal_generic_assetpack(destination, **source) as verified:
                manifest = verified.manifest
                published_identity = verified.root_identity
            binding = {
                "path": str(destination),
                "parent_identity": parent_identity,
                "published_identity": published_identity,
                "expected_tree_hash": manifest["content_hash"],
                "expected_manifest_hash": manifest["release_ready_manifest"]["content_hash"],
            }
            inspect_identity = CreationJobCoordinator._asset_release_publication_identity

            def rename_after_identity_inspection(value: dict[str, object]):
                identity = inspect_identity(value)
                output_parent.rename(displaced_parent)
                return identity

            with (
                patch.object(
                    CreationJobCoordinator,
                    "_asset_release_publication_identity",
                    side_effect=rename_after_identity_inspection,
                ),
                self.assertRaises(StudioError),
            ):
                CreationJobCoordinator._rollback_asset_release_publication(binding)
            self.assertFalse(
                output_parent.exists(),
                "rollback must not recreate a selected parent that was renamed away",
            )
            self.assertTrue((displaced_parent / destination.name).is_dir())

    @unittest.skipUnless(
        (os.name == "posix") or os.name == "nt",
        "exact assetpack rollback requires Linux or Windows primitives",
    )
    def test_exact_output_rollback_verifies_the_claimed_assetpack(self) -> None:
        from worldforge.__main__ import _resolve_generic_assetpack_cli_source
        from worldforge.generic_assetpack import seal_generic_assetpack
        from worldforge.studio.creation_jobs import CreationJobCoordinator
        from worldforge.studio.errors import StudioError

        source = _resolve_generic_assetpack_cli_source(
            _ROOT
            / "examples"
            / "multigenre-contracts"
            / "abstract-puzzle"
            / "assets"
            / "manifest.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output_parent = base / "outputs"
            output_parent.mkdir()
            destination = output_parent / "sealed"
            parent_info = output_parent.stat()
            parent_identity = (parent_info.st_dev, parent_info.st_ino)
            with seal_generic_assetpack(destination, **source) as verified:
                manifest = verified.manifest
                published_identity = verified.root_identity
            binding = {
                "path": str(destination),
                "parent_identity": parent_identity,
                "published_identity": published_identity,
                "expected_tree_hash": manifest["content_hash"],
                "expected_manifest_hash": manifest["release_ready_manifest"]["content_hash"],
            }

            if sys.platform.startswith("linux") and os.name == "posix":
                with self.assertRaisesRegex(
                    StudioError,
                    "requires retained-output recovery",
                ) as raised:
                    CreationJobCoordinator._rollback_asset_release_publication(binding)
                self.assertIn("reason_code", raised.exception.details)
                self.assertEqual(
                    destination.name,
                    raised.exception.details["recovery_evidence"]["stage"]["locator"],
                )
                self.assertTrue(destination.is_dir())
            else:
                CreationJobCoordinator._rollback_asset_release_publication(binding)
                self.assertFalse(destination.exists())

    @unittest.skipUnless(os.name == "posix", "hardlink fixture requires POSIX")
    def test_grant_parent_rejects_hardlinked_non_directory_target(self) -> None:
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.errors import StudioError

        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            service, workspace = _prepared_creation_service(base)
            try:
                parent = base / "outputs"
                parent.mkdir()
                source = parent / "source"
                source.write_bytes(b"foreign")
                target = parent / "sealed-assets"
                os.link(source, target)
                with self.assertRaisesRegex(StudioError, "absent|exists|directory|collision"):
                    service.creation_output_grants.create(
                        {
                            "workspace_id": workspace["workspace_id"],
                            "kind": "generic_assetpack_directory",
                            "display_name": "sealed-assets",
                            "path": str(target),
                        }
                    )
            finally:
                service.close()
                service.store.close()


if __name__ == "__main__":
    unittest.main()
