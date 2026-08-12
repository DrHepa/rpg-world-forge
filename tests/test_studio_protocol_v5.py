from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from worldforge.integrity import canonical_payload_hash
from worldforge.studio.errors import StudioError

ROOT = Path(__file__).resolve().parents[1]
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
V4_SCHEMA_SHA256 = "28d3a8fa4c65cc902a9661044bebf6625916672d7252bb8bb32e48ff875d4ea5"
V4_TYPES_SHA256 = "1901911ecab5952faa750ebfa402f11903b807c60b1d13cfc5369b4d5047053c"
OUTPUT_GRANT_V5_SCHEMA_SHA256 = "0a890ccd2454d2b743415b157408b3ccb36ba80cc6e5b45ab4ecd9796495c2eb"


def _request(operation: str, **operation_fields: object) -> dict[str, object]:
    return {
        "protocol": "rpg-world-forge.studio_protocol",
        "protocol_version": 5,
        "kind": "request",
        "request_id": f"request_{operation.replace('.', '_')}",
        "method": "creation_job.create",
        "params": {
            "workspace_id": "workspace_01",
            "operation": operation,
            "expected_root_generation": 3,
            "expected_source_revision": HASH_A,
            "expected_workflow_status_hash": HASH_B,
            "expected_artifact_snapshot_hash": HASH_C,
            **operation_fields,
        },
    }


def _operation_params() -> dict[int, tuple[str, dict[str, object]]]:
    return {
        10: (
            "asset.qa.review",
            {
                "qa_report_artifact_id": "artifact_qa_01",
                "output_role": "texture",
                "review_receipt_id": "review_receipt_01",
                "decisions": ["approved", "rejected"],
                "blockers": ["criterion_rejected"],
            },
        ),
        11: (
            "asset.release.authorize",
            {
                "review_receipt_artifact_ids": ["artifact_review_01", "artifact_review_02"],
                "manifest_id": "manifest_01",
                "assetpack_id": "assetpack_01",
                "release_authority_id": "release_authority_01",
                "blockers": [],
                "target_grant_id": "grant_assetpack_01",
                "expected_target_grant_generation": 3,
            },
        ),
        12: (
            "runtime.headless.verify",
            {
                "gamepack_artifact_id": "artifact_gamepack_01",
                "asset_inventory_artifact_id": "artifact_inventory_01",
                "assetpack_artifact_id": "artifact_assetpack_01",
                "asset_release_authority_artifact_id": "artifact_release_authority_01",
                "runtime_snapshot_artifact_id": "artifact_runtime_snapshot_01",
                "runtime_adapter_registry_artifact_id": "artifact_runtime_registry_01",
                "runtime_composition_artifact_id": "artifact_runtime_composition_01",
                "runtime_bundle_artifact_id": "artifact_runtime_bundle_01",
                "headless_script_artifact_id": "artifact_headless_script_01",
                "source_grant_id": "grant_runtime_bundle_01",
                "expected_source_grant_generation": 2,
                "target_grant_id": "grant_headless_evidence_01",
                "expected_target_grant_generation": 0,
                "platform_id": "platform:linux_x86_64",
            },
        ),
    }


def _queued_job(version: int) -> dict[str, object]:
    operation, params = _operation_params()[version]
    params = copy.deepcopy(params)
    if version == 11:
        params["target_grant_generation"] = params.pop("expected_target_grant_generation")
    record: dict[str, object] = {
        "format": "world-forge.studio_creation_job",
        "format_version": version,
        "job_id": f"job_authority_{version}",
        "workspace_id": "workspace_01",
        "operation": operation,
        "operation_params": params,
        "state": "queued",
        "generation": 0,
        "authority": {
            "root_generation": 3,
            "source_revision": HASH_A,
            "workflow_status_hash": HASH_B,
            "artifact_snapshot_hash": HASH_C,
        },
        "inputs": [],
        "progress": "queued",
        "result": None,
        "error": None,
        "created_at": "2026-08-08T00:00:00Z",
        "started_at": None,
        "finished_at": None,
        "updated_at": "2026-08-08T00:00:00Z",
        "record_hash": "0" * 64,
    }
    record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
    return record


def _succeeded_job(version: int) -> dict[str, object]:
    record = _queued_job(version)
    common: dict[str, object] = {
        "output_artifact_ids": [f"artifact_authority_{version}"],
        "artifact_snapshot_hash": HASH_A,
        "analysis_status": "passed",
        "reason_codes": [],
        "cleanup_pending": False,
    }
    if version == 10:
        result = {
            **common,
            "review_receipt": {
                "format": "world-forge.asset_qa_review_receipt",
                "format_version": 1,
                "review_receipt_id": "review_receipt_01",
                "content_hash": HASH_B,
            },
            "review_status": "approved",
        }
    elif version == 11:
        common["output_artifact_ids"] = [
            "artifact_manifest_01",
            "artifact_assetpack_01",
            "artifact_release_authority_01",
        ]
        result = {
            **common,
            "asset_manifest": {"manifest_id": "manifest_01", "content_hash": HASH_A},
            "assetpack": {"assetpack_id": "assetpack_01", "content_hash": HASH_B},
            "asset_release_authority": {
                "format": "world-forge.asset_release_authority",
                "format_version": 1,
                "release_authority_id": "release_authority_01",
                "content_hash": HASH_C,
            },
            "release_status": "authorized",
            "publication": {
                "grant_id": "grant_assetpack_01",
                "grant_generation": 4,
                "kind": "generic_assetpack_directory",
                "state": "published",
                "assetpack": {
                    "format": "world-forge.assetpack",
                    "format_version": 1,
                    "id": "assetpack_01",
                    "content_hash": HASH_B,
                    "inventory_hash": HASH_C,
                },
            },
        }
    else:
        common["output_artifact_ids"] = [
            "artifact_runtime_authority_01",
            "artifact_runtime_evidence_01",
            "artifact_runtime_support_01",
        ]
        result = {
            **common,
            "runtime_support_authority": {
                "format": "world-forge.runtime_support_authority",
                "format_version": 1,
                "id": "runtime_authority_01",
                "content_hash": HASH_A,
            },
            "runtime_evidence": {
                "format": "world-forge.runtime_evidence",
                "format_version": 1,
                "id": "runtime_evidence_01",
                "content_hash": HASH_B,
            },
            "runtime_support_report": {
                "format": "world-forge.runtime_support_report",
                "format_version": 1,
                "id": "runtime_support_01",
                "content_hash": HASH_C,
            },
            "release_status": "blocked",
            "native_status": "unavailable",
            "supported": False,
            "publication": {
                "grant_id": "grant_headless_evidence_01",
                "grant_generation": 1,
                "kind": "headless_evidence_directory",
                "state": "published",
                "headless_evidence_set": {
                    "format": "world-forge.headless_evidence_set",
                    "format_version": 1,
                    "id": "headless_evidence_set_01",
                    "content_hash": HASH_B,
                    "tree_hash": HASH_C,
                },
            },
        }
    record.update(
        {
            "state": "succeeded",
            "generation": 1,
            "progress": "committed",
            "result": result,
            "started_at": "2026-08-08T00:00:01Z",
            "finished_at": "2026-08-08T00:00:02Z",
            "updated_at": "2026-08-08T00:00:02Z",
        }
    )
    record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
    return record


def _legacy_queued_job() -> dict[str, object]:
    record: dict[str, object] = {
        "format": "world-forge.studio_creation_job",
        "format_version": 1,
        "job_id": "job_legacy_1",
        "workspace_id": "workspace_01",
        "operation": "creation.compile",
        "state": "queued",
        "generation": 0,
        "authority": {
            "root_generation": 3,
            "source_revision": HASH_A,
            "workflow_status_hash": HASH_B,
            "artifact_snapshot_hash": HASH_C,
        },
        "inputs": [],
        "progress": "queued",
        "result": None,
        "error": None,
        "created_at": "2026-08-08T00:00:00Z",
        "started_at": None,
        "finished_at": None,
        "updated_at": "2026-08-08T00:00:00Z",
        "record_hash": "0" * 64,
    }
    record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
    return record


def _output_grant(version: int) -> dict[str, object]:
    kind = {
        1: "generic_assetpack_directory",
        6: "headless_evidence_directory",
    }[version]
    return {
        "format": "world-forge.studio_creation_output_grant",
        "format_version": version,
        "grant_id": f"grant_version_{version}",
        "workspace_id": "workspace_01",
        "kind": kind,
        "display_name": f"grant-{version}",
        "state": "ready",
        "generation": 0,
        "publication": None,
        "created_at": "2026-08-09T00:00:00Z",
        "updated_at": "2026-08-09T00:00:00Z",
    }


def _qa_preview() -> dict[str, object]:
    return {
        "format": "world-forge.studio_creation_preview",
        "format_version": 2,
        "handle": "A" * 43,
        "workspace_id": "workspace_01",
        "source": {
            "kind": "qa_review_candidate",
            "qa_report_artifact_id": "artifact_qa_01",
            "asset_id": "board_ui",
            "output_role": "texture",
        },
        "media_type": "image/png",
        "byte_length": 67,
        "sha256": HASH_C,
        "chunk_bytes": 65536,
        "metadata": {"kind": "png", "width": 1, "height": 1, "mode": "rgba8"},
    }


def _published_preview() -> dict[str, object]:
    return {
        "format": "world-forge.studio_creation_preview",
        "format_version": 1,
        "handle": "B" * 43,
        "workspace_id": "workspace_01",
        "assetpack_artifact_id": "artifact_assetpack_01",
        "output_grant_id": "grant_version_1",
        "output_grant_generation": 1,
        "asset_id": "board_ui",
        "media_type": "image/png",
        "byte_length": 67,
        "sha256": HASH_C,
        "chunk_bytes": 65536,
        "metadata": {"kind": "png", "width": 1, "height": 1, "mode": "rgba8"},
    }


class StudioProtocolV5Tests(unittest.TestCase):
    def test_v5_is_additive_and_preserves_exact_v4_contract_bytes(self) -> None:
        from worldforge.studio.contracts import METHODS_V4, METHODS_V5

        v4_schema = ROOT / "schemas/studio-protocol-v4.schema.json"
        v4_types = ROOT / "apps/studio/src/generated/studio-protocol-v4.d.ts"
        output_grant_v5_schema = ROOT / "schemas/studio-creation-output-grant.schema.json"
        self.assertEqual(V4_SCHEMA_SHA256, hashlib.sha256(v4_schema.read_bytes()).hexdigest())
        self.assertEqual(V4_TYPES_SHA256, hashlib.sha256(v4_types.read_bytes()).hexdigest())
        self.assertEqual(
            OUTPUT_GRANT_V5_SCHEMA_SHA256,
            hashlib.sha256(output_grant_v5_schema.read_bytes()).hexdigest(),
        )

        schema = json.loads((ROOT / "schemas/studio-protocol-v5.schema.json").read_text())
        self.assertEqual(METHODS_V4 | {"creation_workspace.create"}, METHODS_V5)
        self.assertEqual(18, len(METHODS_V5))
        self.assertEqual(sorted(METHODS_V5), sorted(schema["$defs"]["method"]["enum"]))
        self.assertEqual(
            "studio-creation-job-v12.schema.json",
            schema["$defs"]["jobResult"]["properties"]["job"]["$ref"],
        )
        self.assertNotIn("studio-creation-job.schema.json", json.dumps(schema))

        capabilities = schema["$defs"]["initializeResult"]["properties"]["capabilities"]
        for name in (
            "asset_authority_reviews",
            "asset_release_authority",
            "runtime_headless_authority",
            "creation_preview_pre_release",
        ):
            self.assertIs(True, capabilities["properties"][name]["const"])

        catalog = json.loads((ROOT / "contracts/catalog.json").read_text())
        by_id = {entry["id"]: entry for entry in catalog["contracts"]}
        self.assertEqual(5, by_id["studio-protocol-v5"]["version"])
        self.assertEqual(12, by_id["studio-creation-job-v12"]["version"])
        self.assertEqual(6, by_id["studio-creation-output-grant-v6"]["version"])
        generated = (ROOT / "apps/studio/src/generated/studio-protocol-v5.d.ts").read_text()
        self.assertIn("WorldForgeStudioAuthorityProtocolV5", generated)
        self.assertIn("WorldForgeStudioCreationJobV12", generated)
        self.assertIn("WorldForgeStudioCreationOutputGrantV6", generated)

    def test_v5_accepts_only_closed_authority_job_requests(self) -> None:
        from worldforge.studio.contracts import validate_studio_protocol_envelope

        operation_fields = _operation_params()
        requests = [
            _request(operation, **params) for operation, params in operation_fields.values()
        ]
        for request in requests:
            self.assertEqual(request, validate_studio_protocol_envelope(request))

        headless_request = requests[-1]
        windows_request = copy.deepcopy(headless_request)
        windows_request["params"]["platform_id"] = "platform:windows_x86_64"
        self.assertEqual(
            windows_request,
            validate_studio_protocol_envelope(windows_request),
        )
        noncanonical_platform = copy.deepcopy(headless_request)
        noncanonical_platform["params"]["platform_id"] = "linux-x86_64"
        with self.assertRaisesRegex(ValueError, "platform_id is unsupported"):
            validate_studio_protocol_envelope(noncanonical_platform)
        for missing in (
            "headless_script_artifact_id",
            "source_grant_id",
            "expected_source_grant_generation",
            "target_grant_id",
            "expected_target_grant_generation",
            "platform_id",
        ):
            incomplete = copy.deepcopy(headless_request)
            del incomplete["params"][missing]
            with (
                self.subTest(missing=missing),
                self.assertRaisesRegex(
                    ValueError,
                    "invalid fields",
                ),
            ):
                validate_studio_protocol_envelope(incomplete)

        for forbidden in (
            "status",
            "content_hash",
            "evidence_hash",
            "path",
            "runtime_path",
            "command",
            "provider",
            "env",
            "script",
            "script_bytes",
            "headless_evidence_set_id",
        ):
            leaked = copy.deepcopy(headless_request)
            leaked["params"][forbidden] = "renderer-controlled"
            with (
                self.subTest(forbidden=forbidden),
                self.assertRaisesRegex(
                    ValueError,
                    "invalid fields",
                ),
            ):
                validate_studio_protocol_envelope(leaked)

        duplicate_lineage = copy.deepcopy(headless_request)
        duplicate_lineage["params"]["headless_script_artifact_id"] = duplicate_lineage["params"][
            "runtime_bundle_artifact_id"
        ]
        with self.assertRaisesRegex(ValueError, "artifact IDs must be distinct"):
            validate_studio_protocol_envelope(duplicate_lineage)

        same_grant = copy.deepcopy(headless_request)
        same_grant["params"]["target_grant_id"] = same_grant["params"]["source_grant_id"]
        with self.assertRaisesRegex(ValueError, "source and target grants must be distinct"):
            validate_studio_protocol_envelope(same_grant)

    def test_v4_python_validator_rejects_future_jobs_with_only_common_fields(self) -> None:
        from worldforge.studio.contracts import validate_studio_protocol_envelope

        for version, (operation, _params) in _operation_params().items():
            with self.subTest(version=version, operation=operation):
                request = _request(operation)
                request["protocol_version"] = 4
                with self.assertRaisesRegex(ValueError, "operation is unknown"):
                    validate_studio_protocol_envelope(request)

    def test_v10_v12_jobs_are_closed_and_operation_version_discriminated(self) -> None:
        from worldforge.studio.contracts import validate_studio_creation_job

        for version in (10, 11, 12):
            record = _queued_job(version)
            self.assertEqual(record, validate_studio_creation_job(record))

            mismatch = copy.deepcopy(record)
            mismatch["format_version"] = 10 if version != 10 else 11
            mismatch["record_hash"] = canonical_payload_hash(mismatch, hash_field="record_hash")
            with self.assertRaisesRegex(ValueError, "operation is unknown for its version"):
                validate_studio_creation_job(mismatch)

            leaked = copy.deepcopy(record)
            leaked["operation_params"]["path"] = "/private/output"
            leaked["record_hash"] = canonical_payload_hash(leaked, hash_field="record_hash")
            with self.assertRaisesRegex(ValueError, "(?:invalid|unknown) fields"):
                validate_studio_creation_job(leaked)

    def test_v10_v12_results_are_versioned_and_runtime_stays_fail_closed(self) -> None:
        from worldforge.studio.contracts import validate_studio_creation_job

        for version in (10, 11, 12):
            record = _succeeded_job(version)
            self.assertEqual(record, validate_studio_creation_job(record))

        for field, value in (
            ("supported", True),
            ("native_status", "native_verified"),
            ("release_status", "ready"),
        ):
            overclaim = _succeeded_job(12)
            overclaim["result"][field] = value
            overclaim["record_hash"] = canonical_payload_hash(
                overclaim,
                hash_field="record_hash",
            )
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(
                    ValueError,
                    "overclaims runtime authority",
                ),
            ):
                validate_studio_creation_job(overclaim)

        redundant = _succeeded_job(12)
        redundant["result"]["headless_evidence_set"] = {
            "headless_evidence_set_id": "headless_evidence_set_01",
            "content_hash": HASH_B,
        }
        redundant["record_hash"] = canonical_payload_hash(redundant, hash_field="record_hash")
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_creation_job(redundant)

        wrong_publication = _succeeded_job(12)
        wrong_publication["result"]["publication"]["kind"] = "game_runtime_bundle_directory"
        wrong_publication["record_hash"] = canonical_payload_hash(
            wrong_publication,
            hash_field="record_hash",
        )
        with self.assertRaisesRegex(ValueError, "kind is unknown"):
            validate_studio_creation_job(wrong_publication)

        wrong_result = _succeeded_job(10)
        wrong_result["format_version"] = 11
        wrong_result["operation"] = "asset.release.authorize"
        wrong_result["operation_params"] = _operation_params()[11][1]
        wrong_result["record_hash"] = canonical_payload_hash(
            wrong_result,
            hash_field="record_hash",
        )
        with self.assertRaisesRegex(ValueError, "(?:invalid|missing) fields"):
            validate_studio_creation_job(wrong_result)

    def test_v5_job_results_do_not_broaden_the_v4_protocol_reader(self) -> None:
        from worldforge.studio.contracts import validate_studio_protocol_envelope

        response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 5,
            "kind": "response",
            "request_id": "job_get_v5",
            "method": "creation_job.get",
            "result": {"job": _queued_job(10)},
        }
        self.assertEqual(response, validate_studio_protocol_envelope(response))
        legacy = {**response, "protocol_version": 4}
        with self.assertRaisesRegex(ValueError, "unavailable in this protocol"):
            validate_studio_protocol_envelope(legacy)

    def test_v5_handshake_activates_only_reviewed_authority_capabilities(self) -> None:
        from worldforge.studio.contracts import METHODS_V5, validate_studio_protocol_envelope
        from worldforge.studio.service import StudioService

        response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 5,
            "kind": "response",
            "request_id": "initialize_v5",
            "method": "service.initialize",
            "result": {
                "service": "world-forge.studio",
                "service_version": 5,
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": 5,
                "methods": sorted(METHODS_V5),
                "capabilities": {
                    "creation_evidence_projection": True,
                    "creation_jobs": True,
                    "creation_output_grants": True,
                    "creation_runtime_compose": True,
                    "creation_runtime_bundle": True,
                    "creation_materialization_bundle": True,
                    "creation_asset_previews": True,
                    "game_packaging": True,
                    "game_package_extraction": True,
                    "asset_previews": False,
                    "materialization_execution": True,
                    "asset_authority_reviews": True,
                    "asset_release_authority": True,
                    "runtime_headless_authority": True,
                    "creation_preview_pre_release": True,
                },
            },
        }
        self.assertEqual(response, validate_studio_protocol_envelope(response))
        underclaim = copy.deepcopy(response)
        underclaim["result"]["capabilities"]["asset_authority_reviews"] = False
        with self.assertRaisesRegex(ValueError, "does not describe Studio v5"):
            validate_studio_protocol_envelope(underclaim)

        self.assertEqual(response["result"], StudioService._initialize({}, protocol_version=5))

        routed = False

        def route(_params: object) -> dict[str, object]:
            nonlocal routed
            routed = True
            return {"job": _queued_job(10)}

        service = object.__new__(StudioService)
        service._closed = False
        service._methods = {"creation_job.create": route}
        request = _request("asset.qa.review", **_operation_params()[10][1])
        handled = service.handle(request)
        self.assertEqual(5, handled["protocol_version"])
        self.assertEqual(10, handled["result"]["job"]["format_version"])
        self.assertIs(True, routed)

    def test_service_protocol_version_closes_job_projection_and_mutations(self) -> None:
        from worldforge.studio.service import StudioService

        jobs = [_legacy_queued_job(), *(_queued_job(version) for version in (10, 11, 12))]

        class Backend:
            mutations = 0

            @staticmethod
            def get(job_id: object) -> dict[str, object]:
                return next(record for record in jobs if record["job_id"] == job_id)

            @staticmethod
            def list(**_params: object) -> tuple[list[dict[str, object]], int]:
                return jobs, 44

            def cancel(self, job_id: object, **_params: object) -> dict[str, object]:
                self.mutations += 1
                return self.get(job_id)

            def recover(self, job_id: object, **_params: object) -> dict[str, object]:
                self.mutations += 1
                return self.get(job_id)

        backend = Backend()
        service = object.__new__(StudioService)
        service._closed = False
        service.creation_scheduler = None
        service.creation_jobs = backend
        service._methods = {
            "creation_job.get": lambda params: {"job": backend.get(params["job_id"])},
            "creation_job.list": lambda _params: {"jobs": jobs, "next_sequence": 44},
            "creation_job.cancel": lambda params: {"job": backend.cancel(params["job_id"])},
            "creation_job.recover": lambda params: {"job": backend.recover(params["job_id"])},
        }

        def request(
            method: str,
            params: dict[str, object],
            *,
            version: int,
        ) -> dict[str, object]:
            return {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": version,
                "kind": "request",
                "request_id": f"{method}-{version}",
                "method": method,
                "params": params,
            }

        page_params = {
            "workspace_id": "workspace_01",
            "state": None,
            "after_sequence": 0,
            "limit": 8,
        }
        v4_page = service.handle(request("creation_job.list", page_params, version=4))["result"]
        self.assertEqual([1], [record["format_version"] for record in v4_page["jobs"]])
        self.assertEqual(44, v4_page["next_sequence"])
        v5_page = service.handle(request("creation_job.list", page_params, version=5))["result"]
        self.assertEqual(
            [1, 10, 11, 12],
            [record["format_version"] for record in v5_page["jobs"]],
        )

        for version in (10, 11, 12):
            job = jobs[version - 9]
            get_params = {"job_id": job["job_id"]}
            with self.subTest(version=version, protocol=4):
                with self.assertRaises(StudioError) as hidden:
                    service.handle(request("creation_job.get", get_params, version=4))
                self.assertEqual("not_found", hidden.exception.code)
            with self.subTest(version=version, protocol=5):
                response = service.handle(request("creation_job.get", get_params, version=5))
                self.assertEqual(version, response["result"]["job"]["format_version"])

        high = jobs[1]
        mutation = {
            "job_id": high["job_id"],
            "expected_generation": high["generation"],
            "expected_record_hash": high["record_hash"],
        }
        with self.assertRaises(StudioError) as hidden_cancel:
            service.handle(request("creation_job.cancel", mutation, version=4))
        self.assertEqual("not_found", hidden_cancel.exception.code)
        with self.assertRaises(StudioError) as hidden_recover:
            service.handle(
                request("creation_job.recover", {**mutation, "mode": "resume"}, version=4)
            )
        self.assertEqual("not_found", hidden_recover.exception.code)
        self.assertEqual(0, backend.mutations)
        service.handle(request("creation_job.cancel", mutation, version=5))
        service.handle(request("creation_job.recover", {**mutation, "mode": "resume"}, version=5))
        self.assertEqual(2, backend.mutations)

    def test_service_v5_projects_v6_grants_and_qa_candidate_previews_only(self) -> None:
        from worldforge.studio.service import StudioService

        grants = [_output_grant(1), _output_grant(6)]

        class GrantBackend:
            revocations = 0
            page = grants
            next_cursor: str | None = None

            @staticmethod
            def create(_params: object) -> dict[str, object]:
                return grants[1]

            @staticmethod
            def get(grant_id: object) -> dict[str, object]:
                return next(record for record in grants if record["grant_id"] == grant_id)

            def list(
                self,
                **_params: object,
            ) -> tuple[list[dict[str, object]], str | None]:
                return self.page, self.next_cursor

            def revoke(self, grant_id: object, **_params: object) -> dict[str, object]:
                self.revocations += 1
                return self.get(grant_id)

        grant_backend = GrantBackend()
        service = object.__new__(StudioService)
        service._closed = False
        service.creation_output_grants = grant_backend
        service.creation_evidence = type(
            "Evidence",
            (),
            {
                "list": staticmethod(
                    lambda _params: {
                        "authority": {
                            "workspace_id": "workspace_01",
                            "root_generation": 3,
                            "source_revision": HASH_A,
                            "workflow_status_hash": HASH_B,
                        },
                        "artifact_snapshot_hash": HASH_C,
                    }
                )
            },
        )()
        service.creation_previews = type(
            "Previews",
            (),
            {
                "open": staticmethod(
                    lambda params: (
                        _qa_preview()
                        if params.get("source_kind") == "qa_review_candidate"
                        else _published_preview()
                    )
                )
            },
        )()
        service._methods = {
            "creation_output_grant.get": lambda params: {
                "grant": grant_backend.get(params["grant_id"])
            },
            "creation_output_grant.list": lambda _params: {
                "authority": {
                    "workspace_id": "workspace_01",
                    "root_generation": 3,
                    "source_revision": HASH_A,
                    "workflow_status_hash": HASH_B,
                },
                "artifact_snapshot_hash": HASH_C,
                "grants": grants,
                "next_cursor": None,
            },
            "creation_output_grant.revoke": lambda params: {
                "grant": grant_backend.revoke(params["grant_id"])
            },
            "creation_preview.open": lambda params: {
                "preview": service.creation_previews.open(params)
            },
        }

        def request(
            method: str,
            params: dict[str, object],
            *,
            version: int,
        ) -> dict[str, object]:
            return {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": version,
                "kind": "request",
                "request_id": f"{method}-{version}",
                "method": method,
                "params": params,
            }

        grant_id = grants[1]["grant_id"]
        with self.assertRaises(StudioError) as hidden:
            service.handle(request("creation_output_grant.get", {"grant_id": grant_id}, version=4))
        self.assertEqual("not_found", hidden.exception.code)
        v5_grant = service.handle(
            request("creation_output_grant.get", {"grant_id": grant_id}, version=5)
        )
        self.assertEqual(6, v5_grant["result"]["grant"]["format_version"])

        page_params = {
            "workspace_id": "workspace_01",
            "expected_root_generation": 3,
            "expected_source_revision": HASH_A,
            "expected_workflow_status_hash": HASH_B,
            "expected_artifact_snapshot_hash": HASH_C,
            "cursor": None,
            "limit": 8,
        }
        v4_page = service.handle(request("creation_output_grant.list", page_params, version=4))[
            "result"
        ]
        self.assertEqual([1], [grant["format_version"] for grant in v4_page["grants"]])
        v5_page = service.handle(request("creation_output_grant.list", page_params, version=5))[
            "result"
        ]
        self.assertEqual([1, 6], [grant["format_version"] for grant in v5_page["grants"]])
        grant_backend.filtered_page = None

        mutation = {"grant_id": grant_id, "expected_generation": 0}
        with self.assertRaises(StudioError) as hidden_revoke:
            service.handle(request("creation_output_grant.revoke", mutation, version=4))
        self.assertEqual("not_found", hidden_revoke.exception.code)
        self.assertEqual(0, grant_backend.revocations)
        service.handle(request("creation_output_grant.revoke", mutation, version=5))
        self.assertEqual(1, grant_backend.revocations)

        preview_params = {
            "source_kind": "qa_review_candidate",
            "workspace_id": "workspace_01",
            "expected_root_generation": 3,
            "expected_source_revision": HASH_A,
            "expected_workflow_status_hash": HASH_B,
            "expected_artifact_snapshot_hash": HASH_C,
            "qa_report_artifact_id": "artifact_qa_01",
            "asset_id": "board_ui",
            "output_role": "texture",
        }
        with self.assertRaises(StudioError) as unavailable:
            service.handle(request("creation_preview.open", preview_params, version=4))
        self.assertEqual("invalid_request", unavailable.exception.code)
        preview = service.handle(request("creation_preview.open", preview_params, version=5))
        self.assertEqual(2, preview["result"]["preview"]["format_version"])

        published_params = {
            "workspace_id": "workspace_01",
            "expected_root_generation": 3,
            "expected_source_revision": HASH_A,
            "expected_workflow_status_hash": HASH_B,
            "expected_artifact_snapshot_hash": HASH_C,
            "assetpack_artifact_id": "artifact_assetpack_01",
            "output_grant_id": "grant_version_1",
            "expected_output_grant_generation": 1,
            "asset_id": "board_ui",
        }
        for version in (4, 5):
            published = service.handle(
                request("creation_preview.open", published_params, version=version)
            )
            self.assertEqual(1, published["result"]["preview"]["format_version"])

    def test_v5_service_rejects_malformed_backend_authority_projection(self) -> None:
        from worldforge.studio.service import StudioService

        malformed = _queued_job(12)
        malformed["operation_params"]["path"] = "/renderer-controlled"
        malformed["record_hash"] = canonical_payload_hash(
            malformed,
            hash_field="record_hash",
        )
        service = object.__new__(StudioService)
        service._closed = False
        service._methods = {
            "creation_job.get": lambda _params: {"job": malformed},
        }
        service.creation_jobs = type(
            "Jobs",
            (),
            {"get": staticmethod(lambda _job_id: malformed)},
        )()
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 5,
            "kind": "request",
            "request_id": "malformed-v12",
            "method": "creation_job.get",
            "params": {"job_id": malformed["job_id"]},
        }
        with self.assertRaises(StudioError) as rejected:
            service.handle(request)
        self.assertEqual("internal_error", rejected.exception.code)
        self.assertEqual("Studio method produced an invalid response", rejected.exception.message)

        service._methods["creation_job.list"] = lambda _params: {
            "jobs": [malformed],
            "next_sequence": None,
        }
        service.creation_jobs = type(
            "Jobs",
            (),
            {
                "get": staticmethod(lambda _job_id: malformed),
                "list": staticmethod(lambda **_params: ([malformed], None)),
            },
        )()
        list_params = {
            "workspace_id": "workspace_01",
            "state": None,
            "after_sequence": 0,
            "limit": 8,
        }
        v4_list = {**request, "protocol_version": 4, "method": "creation_job.list"}
        v4_list["params"] = list_params
        self.assertEqual([], service.handle(v4_list)["result"]["jobs"])
        v5_list = {**v4_list, "protocol_version": 5}
        with self.assertRaises(StudioError) as rejected_list:
            service.handle(v5_list)
        self.assertEqual("internal_error", rejected_list.exception.code)

    def test_v6_output_grant_is_additive_and_only_protocol_v5_admits_it(self) -> None:
        from worldforge.studio.contracts import (
            validate_studio_creation_output_grant,
            validate_studio_creation_output_grant_v6,
            validate_studio_protocol_envelope,
        )

        legacy_kinds = {
            1: "generic_assetpack_directory",
            2: "game_runtime_bundle_directory",
            3: "game_materialization_bundle_directory",
            4: "standalone_game_directory",
            5: "game_package_file",
        }
        for version, kind in legacy_kinds.items():
            legacy = {
                "format": "world-forge.studio_creation_output_grant",
                "format_version": version,
                "grant_id": f"grant_legacy_{version}",
                "workspace_id": "workspace_01",
                "kind": kind,
                "display_name": f"legacy-{version}",
                "state": "ready",
                "generation": 0,
                "publication": None,
                "created_at": "2026-08-09T00:00:00Z",
                "updated_at": "2026-08-09T00:00:00Z",
            }
            self.assertEqual(
                legacy,
                validate_studio_creation_output_grant(copy.deepcopy(legacy)),
            )
            self.assertEqual(
                legacy,
                validate_studio_creation_output_grant_v6(copy.deepcopy(legacy)),
            )

        ready = {
            "format": "world-forge.studio_creation_output_grant",
            "format_version": 6,
            "grant_id": "grant_headless_evidence_01",
            "workspace_id": "workspace_01",
            "kind": "headless_evidence_directory",
            "display_name": "headless-evidence",
            "state": "ready",
            "generation": 0,
            "publication": None,
            "created_at": "2026-08-09T00:00:00Z",
            "updated_at": "2026-08-09T00:00:00Z",
        }
        published = {
            **ready,
            "state": "published",
            "generation": 1,
            "publication": {
                "format": "world-forge.headless_evidence_set",
                "format_version": 1,
                "id": "headless_evidence_set_01",
                "content_hash": HASH_B,
                "tree_hash": HASH_C,
            },
            "updated_at": "2026-08-09T00:00:01Z",
        }
        self.assertEqual(ready, validate_studio_creation_output_grant_v6(copy.deepcopy(ready)))
        self.assertEqual(
            published,
            validate_studio_creation_output_grant_v6(copy.deepcopy(published)),
        )
        wrong_projection = copy.deepcopy(published)
        wrong_projection["publication"]["format"] = "world-forge.game_runtime_bundle"
        with self.assertRaisesRegex(ValueError, "format must be"):
            validate_studio_creation_output_grant_v6(wrong_projection)
        leaked_projection = copy.deepcopy(published)
        leaked_projection["publication"]["native_path"] = "/private/headless-evidence"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_creation_output_grant_v6(leaked_projection)
        for value in (ready, published):
            with self.assertRaisesRegex(ValueError, "format_version must be"):
                validate_studio_creation_output_grant(copy.deepcopy(value))

        response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 5,
            "kind": "response",
            "request_id": "grant_v6",
            "method": "creation_output_grant.get",
            "result": {"grant": published},
        }
        self.assertEqual(response, validate_studio_protocol_envelope(response))
        with self.assertRaisesRegex(ValueError, "format_version must be"):
            validate_studio_protocol_envelope({**response, "protocol_version": 4})

        create = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 5,
            "kind": "request",
            "request_id": "grant_v6_create",
            "method": "creation_output_grant.create",
            "params": {
                "workspace_id": "workspace_01",
                "kind": "headless_evidence_directory",
                "display_name": "headless-evidence",
                "path": "/private/headless-evidence",
            },
        }
        self.assertEqual(create, validate_studio_protocol_envelope(create))
        with self.assertRaisesRegex(ValueError, "kind is unknown"):
            validate_studio_protocol_envelope({**create, "protocol_version": 4})

    def test_v6_contract_does_not_require_a_storage_migration(self) -> None:
        from worldforge.studio.storage import SCHEMA_VERSION, StudioStore

        self.assertEqual(5, SCHEMA_VERSION)
        with tempfile.TemporaryDirectory(prefix="wf-studio-v6-contract-") as temporary:
            with StudioStore(Path(temporary) / "studio") as store:
                columns = {
                    row["name"]
                    for row in store.connection.execute("PRAGMA table_info(creation_output_grants)")
                }
        self.assertEqual(
            {
                "grant_id",
                "workspace_id",
                "kind",
                "state",
                "record_json",
                "absolute_path",
                "parent_dev",
                "parent_ino",
                "normalized_leaf",
                "reserved_job_id",
                "generation",
                "expected_manifest_hash",
                "expected_tree_hash",
                "expected_archive_sha256",
                "expected_size_bytes",
                "published_dev",
                "published_ino",
                "recovery_json",
            },
            columns,
        )

    def test_v4_output_grant_pagination_crosses_hidden_pages_without_changing_v5(self) -> None:
        from worldforge.studio.service import StudioService

        visible_a = {**_output_grant(1), "grant_id": "grant_a_visible"}
        hidden_b = {**_output_grant(6), "grant_id": "grant_b_hidden"}
        hidden_c = {**_output_grant(6), "grant_id": "grant_c_hidden"}
        visible_z = {**_output_grant(1), "grant_id": "grant_z_visible"}

        class Backend:
            pages: dict[str | None, tuple[list[dict[str, object]], str | None]] = {}
            calls: list[str | None] = []

            def list(self, **params: object) -> tuple[list[dict[str, object]], str | None]:
                cursor = params["cursor"]
                assert cursor is None or isinstance(cursor, str)
                self.calls.append(cursor)
                return self.pages[cursor]

        backend = Backend()
        service = object.__new__(StudioService)
        service._closed = False
        service._methods = {}
        service.creation_output_grants = backend
        service.creation_evidence = type(
            "Evidence",
            (),
            {
                "list": staticmethod(
                    lambda _params: {
                        "authority": {
                            "workspace_id": "workspace_01",
                            "root_generation": 3,
                            "source_revision": HASH_A,
                            "workflow_status_hash": HASH_B,
                        },
                        "artifact_snapshot_hash": HASH_C,
                    }
                )
            },
        )()

        def request(version: int, *, limit: int) -> dict[str, object]:
            return {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": version,
                "kind": "request",
                "request_id": f"grant-page-{version}-{limit}",
                "method": "creation_output_grant.list",
                "params": {
                    "workspace_id": "workspace_01",
                    "expected_root_generation": 3,
                    "expected_source_revision": HASH_A,
                    "expected_workflow_status_hash": HASH_B,
                    "expected_artifact_snapshot_hash": HASH_C,
                    "cursor": None,
                    "limit": limit,
                },
            }

        backend.pages = {
            None: ([visible_a, hidden_b], "grant_b_hidden"),
            "grant_b_hidden": ([hidden_c, visible_z], None),
        }
        backend.calls = []
        v4 = service.handle(request(4, limit=2))["result"]
        self.assertEqual([visible_a, visible_z], v4["grants"])
        self.assertIsNone(v4["next_cursor"])
        self.assertEqual([None, "grant_b_hidden"], backend.calls)

        backend.calls = []
        v5 = service.handle(request(5, limit=2))["result"]
        self.assertEqual([visible_a, hidden_b], v5["grants"])
        self.assertEqual("grant_b_hidden", v5["next_cursor"])
        self.assertEqual([None], backend.calls)

        backend.pages = {
            None: ([hidden_b], "grant_b_hidden"),
            "grant_b_hidden": ([hidden_c], "grant_c_hidden"),
            "grant_c_hidden": ([visible_z], None),
        }
        backend.calls = []
        hidden_prefix = service.handle(request(4, limit=1))["result"]
        self.assertEqual([visible_z], hidden_prefix["grants"])
        self.assertIsNone(hidden_prefix["next_cursor"])
        self.assertEqual([None, "grant_b_hidden", "grant_c_hidden"], backend.calls)

        backend.pages = {None: ([visible_a], "grant_a_visible")}
        backend.calls = []
        filled = service.handle(request(4, limit=1))["result"]
        self.assertEqual([visible_a], filled["grants"])
        self.assertEqual("grant_a_visible", filled["next_cursor"])
        self.assertEqual([None], backend.calls)


if __name__ == "__main__":
    unittest.main()
