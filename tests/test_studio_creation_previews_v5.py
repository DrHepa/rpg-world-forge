from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_FORMAT = "rpg-world-forge.studio_protocol"


def _qa_preview_params() -> dict[str, object]:
    return {
        "source_kind": "qa_review_candidate",
        "workspace_id": "workspace_puzzle",
        "expected_root_generation": 0,
        "expected_source_revision": "a" * 64,
        "expected_workflow_status_hash": None,
        "expected_artifact_snapshot_hash": "b" * 64,
        "qa_report_artifact_id": "artifact_qa_board",
        "asset_id": "board_ui",
        "output_role": "texture",
    }


def _qa_preview_request(*, protocol_version: int = 5) -> dict[str, object]:
    return {
        "protocol": PROTOCOL_FORMAT,
        "protocol_version": protocol_version,
        "kind": "request",
        "request_id": "preview-qa-board",
        "method": "creation_preview.open",
        "params": _qa_preview_params(),
    }


class CreationPreviewV5ContractTests(unittest.TestCase):
    def test_v5_adds_one_closed_pathless_qa_review_candidate_request(self) -> None:
        from worldforge.studio.contracts import (
            StudioContractError,
            validate_studio_protocol_envelope,
        )

        request = _qa_preview_request()
        self.assertEqual(request, validate_studio_protocol_envelope(request))

        with self.assertRaises(StudioContractError):
            validate_studio_protocol_envelope({**request, "protocol_version": 4})

        for forbidden in (
            "path",
            "runtime_path",
            "sha256",
            "content_hash",
            "status",
            "evidence",
            "decision",
            "command",
            "provider",
            "env",
            "output_grant_id",
            "expected_output_grant_generation",
        ):
            with self.subTest(forbidden=forbidden):
                injected = copy.deepcopy(request)
                injected["params"][forbidden] = "renderer-controlled"
                with self.assertRaises(StudioContractError):
                    validate_studio_protocol_envelope(injected)

    def test_v2_response_is_pathless_and_v1_reader_remains_published_only(self) -> None:
        from worldforge.studio.contracts import (
            StudioContractError,
            validate_studio_creation_preview,
            validate_studio_creation_preview_v2,
        )

        preview = {
            "format": "world-forge.studio_creation_preview",
            "format_version": 2,
            "handle": "A" * 43,
            "workspace_id": "workspace_puzzle",
            "source": {
                "kind": "qa_review_candidate",
                "qa_report_artifact_id": "artifact_qa_board",
                "asset_id": "board_ui",
                "output_role": "texture",
            },
            "media_type": "image/png",
            "byte_length": 67,
            "sha256": "c" * 64,
            "chunk_bytes": 65536,
            "metadata": {
                "kind": "png",
                "width": 1,
                "height": 1,
                "mode": "rgba8",
            },
        }
        self.assertEqual(preview, validate_studio_creation_preview_v2(preview))
        self.assertNotIn("path", json.dumps(preview).casefold())
        self.assertEqual("qa_review_candidate", preview["source"]["kind"])
        with self.assertRaises(StudioContractError):
            validate_studio_creation_preview(preview)
        response = {
            "protocol": PROTOCOL_FORMAT,
            "protocol_version": 5,
            "kind": "response",
            "request_id": "preview-qa-board",
            "method": "creation_preview.open",
            "result": {"preview": preview},
        }
        from worldforge.studio.contracts import validate_studio_protocol_envelope

        self.assertEqual(response, validate_studio_protocol_envelope(response))
        with self.assertRaises(StudioContractError):
            validate_studio_protocol_envelope({**response, "protocol_version": 4})

        v1_schema = json.loads(
            (ROOT / "schemas/studio-creation-preview.schema.json").read_text("utf-8")
        )
        v2_schema = json.loads(
            (ROOT / "schemas/studio-creation-preview-v2.schema.json").read_text("utf-8")
        )
        protocol_v4 = json.loads(
            (ROOT / "schemas/studio-protocol-v4.schema.json").read_text("utf-8")
        )
        protocol_v5 = json.loads(
            (ROOT / "schemas/studio-protocol-v5.schema.json").read_text("utf-8")
        )
        self.assertEqual(1, v1_schema["properties"]["format_version"]["const"])
        self.assertEqual(2, v2_schema["properties"]["format_version"]["const"])
        self.assertEqual(
            {"$ref": "studio-creation-preview.schema.json"},
            protocol_v4["$defs"]["creationPreviewOpenResult"]["properties"]["preview"],
        )
        self.assertEqual(
            [
                {"$ref": "studio-creation-preview.schema.json"},
                {"$ref": "studio-creation-preview-v2.schema.json"},
            ],
            protocol_v5["$defs"]["creationPreviewOpenResult"]["properties"]["preview"]["oneOf"],
        )

        catalog = json.loads((ROOT / "contracts/catalog.json").read_text("utf-8"))
        entry = next(
            item for item in catalog["contracts"] if item["id"] == "studio-creation-preview-v2"
        )
        self.assertEqual("world-forge.studio_creation_preview", entry["format"])
        self.assertEqual(2, entry["version"])
        self.assertEqual(
            "schemas/studio-creation-preview-v2.schema.json",
            entry["schema"],
        )
        self.assertIn("tests/test_studio_creation_previews_v5.py", entry["tests"])


class CreationPreviewV5AuthorityTests(unittest.TestCase):
    @staticmethod
    def _artifact_snapshot(service: object, workspace: dict[str, object]) -> dict[str, object]:
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

    def _assert_open_rejected(self, service: object, params: dict[str, object], root: Path) -> None:
        from worldforge.studio.errors import StudioError

        with self.assertRaises(StudioError) as raised:
            service.creation_previews.open(params)
        self.assertNotIn(str(root), str(raised.exception))

    def test_exact_retained_process_bytes_are_previewed_without_a_grant(self) -> None:
        from tests.test_studio_creation_asset_jobs_v4 import (
            StudioCreationAssetJobCoordinatorTests,
        )
        from tests.test_studio_creation_jobs_v4 import _prepared_creation_service
        from worldforge.studio.errors import StudioError
        from worldforge.studio.service import StudioService
        from worldforge.studio.storage import StudioStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service, workspace = _prepared_creation_service(root)
            current_service = service
            current_store = service.store
            try:
                _before, process = StudioCreationAssetJobCoordinatorTests._queued_asset_process(
                    service,
                    workspace,
                )
                self.assertEqual(process["job_id"], service.creation_job_coordinator.run_once())
                completed = service.creation_jobs.get(process["job_id"])
                self.assertEqual("succeeded", completed["state"])
                recipe_artifact_id, receipt_artifact_id, qa_artifact_id = completed["result"][
                    "output_artifact_ids"
                ]
                qa_report = service.creation_artifacts.get_document(
                    workspace["workspace_id"], qa_artifact_id
                )
                snapshot = self._artifact_snapshot(service, workspace)
                selected = qa_report["outputs"][0]
                params = {
                    "source_kind": "qa_review_candidate",
                    "workspace_id": workspace["workspace_id"],
                    "expected_root_generation": workspace["root_generation"],
                    "expected_source_revision": workspace["source_revision"],
                    "expected_workflow_status_hash": workspace["workflow_status_hash"],
                    "expected_artifact_snapshot_hash": snapshot["artifact_snapshot_hash"],
                    "qa_report_artifact_id": qa_artifact_id,
                    "asset_id": qa_report["asset"]["asset_id"],
                    "output_role": selected["role"],
                }
                retention = service.creation_artifacts.load_asset_process_retention(
                    workspace_id=workspace["workspace_id"],
                    producer_job_id=process["job_id"],
                )
                retained = next(
                    item for item in retention["outputs"] if item["role"] == selected["role"]
                )
                expected_bytes = service.creation_artifacts.read_retained_asset_output(
                    retention,
                    role=selected["role"],
                )
                blob = service.store.blob_path(retained["sha256"])
                grant_count = service.store.connection.execute(
                    "SELECT COUNT(*) FROM creation_output_grants"
                ).fetchone()[0]

                opened = service.creation_previews.open(params)
                self.assertEqual(2, opened["format_version"])
                self.assertEqual(
                    {
                        "kind": "qa_review_candidate",
                        "qa_report_artifact_id": qa_artifact_id,
                        "asset_id": qa_report["asset"]["asset_id"],
                        "output_role": selected["role"],
                    },
                    opened["source"],
                )
                self.assertEqual(selected["sha256"], opened["sha256"])
                self.assertEqual(selected["size_bytes"], opened["byte_length"])
                self.assertEqual(selected["metadata"], opened["metadata"])
                self.assertNotIn("path", json.dumps(opened).casefold())
                self.assertNotIn("grant", json.dumps(opened).casefold())
                self.assertEqual(
                    grant_count,
                    service.store.connection.execute(
                        "SELECT COUNT(*) FROM creation_output_grants"
                    ).fetchone()[0],
                )
                with self.assertRaisesRegex(StudioError, "sequence"):
                    service.creation_previews.read(opened["handle"], 1024)
                first = service.creation_previews.read(opened["handle"], 0)
                self.assertEqual(expected_bytes, first["payload"])
                self.assertTrue(first["eof"])
                self.assertEqual(first, service.creation_previews.read(opened["handle"], 0))
                self.assertTrue(service.creation_previews.close(opened["handle"]))
                self.assertTrue(service.creation_previews.close(opened["handle"]))
                with self.assertRaisesRegex(StudioError, "unavailable"):
                    service.creation_previews.read(opened["handle"], 0)

                for changed in (
                    {**params, "workspace_id": "workspace_other"},
                    {**params, "qa_report_artifact_id": "artifact_missing_qa"},
                    {**params, "asset_id": "different_asset"},
                    {**params, "output_role": "different_role"},
                ):
                    with self.subTest(selector=changed):
                        self._assert_open_rejected(service, changed, root)

                artifact_tampers = (
                    (qa_artifact_id, "producer_operation", "creation.compile"),
                    (qa_artifact_id, "producer_output_position", 9),
                    (qa_artifact_id, "subject_version", 2),
                    (receipt_artifact_id, "producer_output_position", 9),
                    (recipe_artifact_id, "producer_output_position", 9),
                )
                for artifact_id, column, value in artifact_tampers:
                    with self.subTest(artifact_id=artifact_id, column=column):
                        service.store.connection.execute("SAVEPOINT preview_tamper")
                        try:
                            service.store.connection.execute(
                                f"UPDATE creation_artifacts SET {column} = ? "
                                "WHERE workspace_id = ? AND artifact_id = ?",
                                (value, workspace["workspace_id"], artifact_id),
                            )
                            self._assert_open_rejected(service, params, root)
                        finally:
                            service.store.connection.execute("ROLLBACK TO preview_tamper")
                            service.store.connection.execute("RELEASE preview_tamper")

                service.store.connection.execute("SAVEPOINT preview_retention_tamper")
                try:
                    service.store.connection.execute(
                        "UPDATE creation_job_payloads SET content_hash = ? WHERE job_id = ?",
                        ("0" * 64, process["job_id"]),
                    )
                    self._assert_open_rejected(service, params, root)
                finally:
                    service.store.connection.execute("ROLLBACK TO preview_retention_tamper")
                    service.store.connection.execute("RELEASE preview_retention_tamper")

                retention_drift = service.creation_previews.open(params)
                service.store.connection.execute("SAVEPOINT preview_retention_drift")
                try:
                    service.store.connection.execute(
                        "UPDATE creation_job_payloads SET content_hash = ? WHERE job_id = ?",
                        ("0" * 64, process["job_id"]),
                    )
                    with self.assertRaises(StudioError):
                        service.creation_previews.read(retention_drift["handle"], 0)
                finally:
                    service.store.connection.execute("ROLLBACK TO preview_retention_drift")
                    service.store.connection.execute("RELEASE preview_retention_drift")
                with self.assertRaisesRegex(StudioError, "unavailable"):
                    service.creation_previews.read(retention_drift["handle"], 0)

                original = blob.read_bytes()
                held = blob.with_name(blob.name + ".held")
                blob.rename(held)
                try:
                    self._assert_open_rejected(service, params, root)
                finally:
                    held.rename(blob)

                blob.rename(held)
                blob.write_bytes(original)
                try:
                    self._assert_open_rejected(service, params, root)
                finally:
                    blob.unlink()
                    held.rename(blob)

                hardlink = blob.with_name(blob.name + ".hardlink")
                os.link(blob, hardlink)
                try:
                    self._assert_open_rejected(service, params, root)
                finally:
                    hardlink.unlink()

                blob.rename(held)
                os.symlink(held.name, blob)
                try:
                    self._assert_open_rejected(service, params, root)
                finally:
                    blob.unlink()
                    held.rename(blob)

                mutated = service.creation_previews.open(params)
                blob.write_bytes(b"x" * len(original))
                try:
                    with self.assertRaises(StudioError):
                        service.creation_previews.read(mutated["handle"], 0)
                finally:
                    blob.write_bytes(original)
                with self.assertRaisesRegex(StudioError, "unavailable"):
                    service.creation_previews.read(mutated["handle"], 0)

                replaced = service.creation_previews.open(params)
                blob.rename(held)
                blob.write_bytes(original)
                try:
                    with self.assertRaises(StudioError):
                        service.creation_previews.read(replaced["handle"], 0)
                finally:
                    blob.unlink()
                    held.rename(blob)
                with self.assertRaisesRegex(StudioError, "unavailable"):
                    service.creation_previews.read(replaced["handle"], 0)

                linked = service.creation_previews.open(params)
                os.link(blob, hardlink)
                try:
                    with self.assertRaises(StudioError):
                        service.creation_previews.read(linked["handle"], 0)
                finally:
                    hardlink.unlink()
                with self.assertRaisesRegex(StudioError, "unavailable"):
                    service.creation_previews.read(linked["handle"], 0)

                missing = service.creation_previews.open(params)
                blob.rename(held)
                try:
                    with self.assertRaises(StudioError):
                        service.creation_previews.read(missing["handle"], 0)
                finally:
                    held.rename(blob)
                with self.assertRaisesRegex(StudioError, "unavailable"):
                    service.creation_previews.read(missing["handle"], 0)

                symlinked = service.creation_previews.open(params)
                blob.rename(held)
                os.symlink(held.name, blob)
                try:
                    with self.assertRaises(StudioError):
                        service.creation_previews.read(symlinked["handle"], 0)
                finally:
                    blob.unlink()
                    held.rename(blob)
                with self.assertRaisesRegex(StudioError, "unavailable"):
                    service.creation_previews.read(symlinked["handle"], 0)

                restart = service.creation_previews.open(params)
                data_dir = service.store.data_dir
                service.close()
                service.store.close()
                current_store = StudioStore(data_dir)
                current_service = StudioService(current_store)
                service = current_service
                with self.assertRaisesRegex(StudioError, "unavailable"):
                    service.creation_previews.read(restart["handle"], 0)

                params["expected_artifact_snapshot_hash"] = self._artifact_snapshot(
                    service, workspace
                )["artifact_snapshot_hash"]
                stale = service.creation_previews.open(params)
                review_snapshot = self._artifact_snapshot(service, workspace)
                reviewed = service.creation_jobs.create_asset_qa_review(
                    {
                        "job_id": "job_preview_stale_review",
                        "workspace_id": workspace["workspace_id"],
                        "operation": "asset.qa.review",
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "expected_artifact_snapshot_hash": review_snapshot[
                            "artifact_snapshot_hash"
                        ],
                        "qa_report_artifact_id": qa_artifact_id,
                        "output_role": selected["role"],
                        "review_receipt_id": "preview_stale_review_receipt",
                        "decisions": [
                            "approved" for _criterion in qa_report["acceptance_criteria"]
                        ],
                        "blockers": [],
                    }
                )
                self.assertEqual(reviewed["job_id"], service.creation_job_coordinator.run_once())
                with self.assertRaises(StudioError):
                    service.creation_previews.read(stale["handle"], 0)
                with self.assertRaisesRegex(StudioError, "unavailable"):
                    service.creation_previews.read(stale["handle"], 0)
            finally:
                current_service.close()
                current_store.close()


if __name__ == "__main__":
    unittest.main()
