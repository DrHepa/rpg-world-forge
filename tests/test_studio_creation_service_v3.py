from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldforge.creation_contracts import (
    CreationContractError,
    canonical_creation_hash,
    load_creation_project,
)
from worldforge.creation_route import CreationRouteError
from worldforge.creation_scaffold import CreationScaffoldError, create_creation_project
from worldforge.creation_workflow import load_creation_workflow_status
from worldforge.integrity import canonical_json_bytes
from worldforge.phase_report_v3 import (
    build_phase_output_evidence_v2,
    build_phase_report_v3,
    document_identity,
)
from worldforge.studio.contracts import (
    METHODS,
    METHODS_V2,
    METHODS_V3,
    creation_changeset_record_hash,
    validate_studio_protocol_envelope,
)
from worldforge.studio.creation_workspaces import _bounded_scaffold_failure_details
from worldforge.studio.errors import StudioError
from worldforge.studio.service import StudioService, serve
from worldforge.studio.storage import StudioStore

_NEW_METHODS = {
    "creation_changeset.create",
    "creation_changeset.get",
    "creation_changeset.list",
    "creation_changeset.diff",
    "creation_changeset.approve",
    "creation_changeset.reject",
    "creation_changeset.apply",
    "creation_changeset.recover",
    "creation_workflow.reconcile",
    "creation_phase.read",
    "creation_phase.validate",
    "creation_phase.complete",
    "creation_phase.reopen",
}


def _request(method: str, params: dict[str, object], *, request_id: str) -> dict[str, object]:
    return {
        "protocol": "rpg-world-forge.studio_protocol",
        "protocol_version": 3,
        "kind": "request",
        "request_id": request_id,
        "method": method,
        "params": params,
    }


def _contains_native_path(value: object, root: Path) -> bool:
    if isinstance(value, dict):
        return any(_contains_native_path(item, root) for item in value.values())
    if isinstance(value, list):
        return any(_contains_native_path(item, root) for item in value)
    return isinstance(value, str) and str(root) in value


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).as_posix() != ".worldforge/lifecycle.lock"
    }


def _registered_service(base: Path) -> tuple[StudioService, Path, dict[str, object]]:
    root = base / "project"
    create_creation_project(root, project_id="service_project", title="Service project")
    loaded = load_creation_project(root / "project.json")
    service = StudioService(StudioStore(base / "studio"))
    grant = service.handle(
        _request(
            "creation_root_grant.create",
            {
                "grant_id": "grant_service_project",
                "role": "existing_root",
                "display_name": "Service project",
                "path": str(root),
                "expected_project_hash": loaded.project["content_hash"],
            },
            request_id="grant",
        )
    )["result"]["grant"]
    workspace = service.handle(
        _request(
            "creation_workspace.register",
            {
                "workspace_id": "workspace_service_project",
                "grant_id": grant["grant_id"],
                "expected_grant_generation": grant["generation"],
                "expected_project_hash": loaded.project["content_hash"],
            },
            request_id="workspace",
        )
    )["result"]["workspace"]
    return service, root, workspace


def _replace_project_operation(root: Path, *, title: str) -> dict[str, object]:
    base = (root / "project.json").read_bytes()
    document = json.loads(base)
    document["title"] = title
    document["content_hash"] = ""
    document["content_hash"] = canonical_creation_hash(document)
    proposed = canonical_json_bytes(document)
    return {
        "operation": "replace",
        "path": "project.json",
        "expected_base_file_sha256": hashlib.sha256(base).hexdigest(),
        "expected_base_size": len(base),
        "proposed_file_sha256": hashlib.sha256(proposed).hexdigest(),
        "proposed_size": len(proposed),
        "document": document,
    }


def _brief_report(root: Path) -> dict[str, object]:
    loaded = load_creation_project(root / "project.json")
    reviewer_id = "lead_reviewer"
    reviewer_role = "validation_analyst"
    output = build_phase_output_evidence_v2(
        evidence_id="p00_service_output",
        phase="p00_brief",
        role="project_brief",
        subject=document_identity(loaded.project),
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        source_project=loaded,
    )
    return build_phase_report_v3(
        loaded,
        phase="p00_brief",
        status="ready",
        rationale_code="phase_ready",
        rationale_message="The project brief was reviewed inline.",
        evidence=(
            {
                "evidence_id": "reviewed_project",
                "claim": "The exact creation project was reviewed.",
                "subject": document_identity(loaded.project),
            },
        ),
        output_evidence=output,
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        invalidation_dependencies=None,
    )


def _experience_report(root: Path) -> dict[str, object]:
    loaded = load_creation_project(root / "project.json")
    reviewer_id = "lead_reviewer"
    reviewer_role = "validation_analyst"
    output = build_phase_output_evidence_v2(
        evidence_id="p01_service_output",
        phase="p01_genre_style",
        role="experience_classification",
        subject=document_identity(loaded.profile),
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        source_project=loaded,
    )
    return build_phase_report_v3(
        loaded,
        phase="p01_genre_style",
        status="ready",
        rationale_code="phase_ready",
        rationale_message="The experience classification was reviewed inline.",
        evidence=(
            {
                "evidence_id": "reviewed_profile",
                "claim": "The exact creation profile was reviewed.",
                "subject": document_identity(loaded.profile),
            },
        ),
        output_evidence=output,
        reviewer_id=reviewer_id,
        reviewer_role=reviewer_role,
        invalidation_dependencies=None,
    )


def _reseal(document: dict[str, object]) -> dict[str, object]:
    document["content_hash"] = ""
    document["content_hash"] = canonical_creation_hash(document)
    return document


def _authority(workspace: dict[str, object]) -> dict[str, object]:
    return {
        "workspace_id": workspace["workspace_id"],
        "expected_root_generation": workspace["root_generation"],
        "expected_source_revision": workspace["source_revision"],
        "expected_workflow_status_hash": workspace["workflow_status_hash"],
    }


class StudioCreationServiceV3Tests(unittest.TestCase):
    def test_service_preserves_all_operational_creation_reason_codes(self) -> None:
        operational_reasons = (
            "creation_project_aggregate_limit",
            "creation_project_file_byte_limit",
            "creation_project_file_changed",
            "creation_project_file_limit",
            "creation_project_file_unsafe",
            "creation_project_inspection_failed",
            "creation_project_root_changed",
            "creation_project_root_linked",
            "creation_project_root_non_directory",
        )
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="operational_reason_service_project",
                title="Operational reason service project",
            )
            loaded = load_creation_project(root / "project.json")
            service = StudioService(StudioStore(base / "studio"))
            try:
                for index, reason_code in enumerate(operational_reasons):
                    with self.subTest(reason_code=reason_code):
                        detail = f"Safe creation failure {index}"

                        def routed_failure(
                            _root: Path,
                            *,
                            reason_code: str = reason_code,
                            detail: str = detail,
                        ) -> str:
                            try:
                                raise CreationContractError(
                                    detail,
                                    reason_code=reason_code,
                                )
                            except CreationContractError as exc:
                                raise CreationRouteError("wrapped creation failure") from exc

                        with (
                            patch(
                                "worldforge.studio.creation_grants.route_creation_project",
                                side_effect=routed_failure,
                            ),
                            self.assertRaises(StudioError) as caught,
                        ):
                            service.handle(
                                _request(
                                    "creation_root_grant.create",
                                    {
                                        "grant_id": f"grant_operational_reason_{index}",
                                        "role": "existing_root",
                                        "display_name": "Operational reason project",
                                        "path": str(root),
                                        "expected_project_hash": loaded.project["content_hash"],
                                    },
                                    request_id=f"operational-reason-{index}",
                                )
                            )
                        self.assertEqual("invalid_request", caught.exception.code)
                        self.assertEqual(detail, caught.exception.message)
                        self.assertEqual(
                            reason_code,
                            caught.exception.details["reason_code"],
                        )
                        self.assertNotIn(str(base), caught.exception.message)
            finally:
                service.close()
                service.store.close()

    def test_service_preserves_pathless_non_directory_creation_root_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "not-a-project-directory"
            root.write_bytes(b"not a directory")
            service = StudioService(StudioStore(base / "studio"))
            try:
                with self.assertRaises(StudioError) as caught:
                    service.handle(
                        _request(
                            "creation_root_grant.create",
                            {
                                "grant_id": "grant_non_directory_service_project",
                                "role": "existing_root",
                                "display_name": "Non-directory service project",
                                "path": str(root),
                                "expected_project_hash": "a" * 64,
                            },
                            request_id="non-directory-root",
                        )
                    )
                self.assertEqual("invalid_request", caught.exception.code)
                self.assertEqual(
                    "creation_project_root_non_directory",
                    caught.exception.details["reason_code"],
                )
                self.assertNotIn(str(base), caught.exception.message)
            finally:
                service.close()
                service.store.close()

    def test_service_preserves_pathless_creation_aggregate_limit_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            create_creation_project(
                root,
                project_id="aggregate_service_project",
                title="Aggregate service project",
            )
            loaded = load_creation_project(root / "project.json")
            service = StudioService(StudioStore(base / "studio"))
            try:
                with (
                    patch(
                        "worldforge.creation_contracts.MAX_CREATION_AGGREGATE_BYTES",
                        1,
                    ),
                    self.assertRaises(StudioError) as caught,
                ):
                    service.handle(
                        _request(
                            "creation_root_grant.create",
                            {
                                "grant_id": "grant_aggregate_service_project",
                                "role": "existing_root",
                                "display_name": "Aggregate service project",
                                "path": str(root),
                                "expected_project_hash": loaded.project["content_hash"],
                            },
                            request_id="aggregate-root",
                        )
                    )
                self.assertEqual("invalid_request", caught.exception.code)
                self.assertEqual(
                    "creation_project_aggregate_limit",
                    caught.exception.details["reason_code"],
                )
                self.assertNotIn(str(base), caught.exception.message)
            finally:
                service.close()
                service.store.close()

    def test_service_preserves_pathless_linked_creation_root_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "project"
            alias = base / "project-alias"
            create_creation_project(
                root,
                project_id="linked_service_project",
                title="Linked service project",
            )
            loaded = load_creation_project(root / "project.json")
            try:
                alias.symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symbolic links are unavailable")
            service = StudioService(StudioStore(base / "studio"))
            try:
                with self.assertRaises(StudioError) as caught:
                    service.handle(
                        _request(
                            "creation_root_grant.create",
                            {
                                "grant_id": "grant_linked_service_project",
                                "role": "existing_root",
                                "display_name": "Linked service project",
                                "path": str(alias),
                                "expected_project_hash": loaded.project["content_hash"],
                            },
                            request_id="linked-root",
                        )
                    )
                self.assertEqual("invalid_request", caught.exception.code)
                self.assertEqual(
                    "creation_project_root_linked",
                    caught.exception.details["reason_code"],
                )
                self.assertNotIn(str(base), caught.exception.message)
            finally:
                service.close()
                service.store.close()

    def test_protocol_v3_adds_authoring_methods_without_broadening_v1_or_v2(self) -> None:
        self.assertTrue(_NEW_METHODS.isdisjoint(METHODS))
        self.assertTrue(_NEW_METHODS.isdisjoint(METHODS_V2))
        self.assertEqual(_NEW_METHODS, _NEW_METHODS & set(METHODS_V3))
        self.assertEqual(27, len(METHODS_V3))

        schema = json.loads(
            (
                Path(__file__).resolve().parents[1] / "schemas" / "studio-protocol-v3.schema.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(set(METHODS_V3), set(schema["$defs"]["method"]["enum"]))
        initialize_methods = schema["$defs"]["initializeResult"]["properties"]["methods"]
        self.assertEqual(27, initialize_methods["minItems"])
        self.assertEqual(27, initialize_methods["maxItems"])

        phase_read = _request(
            "creation_phase.read",
            {
                "workspace_id": "workspace_service_project",
                "phase_id": "p00_brief",
                "expected_root_generation": 0,
                "expected_source_revision": "a" * 64,
                "expected_workflow_status_hash": "b" * 64,
            },
            request_id="phase_read_contract",
        )
        self.assertEqual(phase_read, validate_studio_protocol_envelope(phase_read))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_protocol_envelope(
                {
                    **phase_read,
                    "params": {**phase_read["params"], "path": ".worldforge/private.json"},
                }
            )

        phase_request = _request(
            "creation_phase.complete",
            {
                "workspace_id": "workspace_service_project",
                "expected_root_generation": 0,
                "expected_source_revision": "a" * 64,
                "expected_workflow_status_hash": "b" * 64,
                "report": {},
                "artifact_registry": [],
            },
            request_id="phase_contract",
        )
        self.assertEqual(phase_request, validate_studio_protocol_envelope(phase_request))
        for method, params in (
            (
                "creation_phase.complete",
                {
                    **phase_request["params"],
                    "expected_workflow_status_hash": None,
                },
            ),
            (
                "creation_phase.reopen",
                {
                    "workspace_id": "workspace_service_project",
                    "expected_root_generation": 0,
                    "expected_source_revision": "a" * 64,
                    "expected_workflow_status_hash": None,
                    "phase_id": "p00_brief",
                    "reason": "Requirements changed",
                    "approved_by": "lead_reviewer",
                },
            ),
        ):
            with (
                self.subTest(method=method),
                self.assertRaisesRegex(
                    ValueError,
                    "expected_workflow_status_hash",
                ),
            ):
                validate_studio_protocol_envelope(
                    _request(method, params, request_id=f"null_hash_{method}")
                )
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_protocol_envelope(
                {
                    **phase_request,
                    "params": {
                        **phase_request["params"],
                        "report_path": "/private/report.json",
                    },
                }
            )
        with self.assertRaisesRegex(ValueError, "not available"):
            validate_studio_protocol_envelope({**phase_request, "protocol_version": 2})

        crossed_response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "response",
            "request_id": "crossed_result",
            "method": "creation_changeset.get",
            "result": {"changesets": []},
        }
        with self.assertRaisesRegex(ValueError, "changeset"):
            validate_studio_protocol_envelope(crossed_response)

    def test_service_exposes_pathless_creation_changeset_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                created = service.handle(
                    _request(
                        "creation_changeset.create",
                        {
                            **_authority(workspace),
                            "changeset_id": "service_title_change",
                            "operations": [
                                _replace_project_operation(root, title="Updated service project")
                            ],
                        },
                        request_id="create",
                    )
                )["result"]["changeset"]
                self.assertEqual("staged", created["status"])
                self.assertNotIn("document", created["operations"][0])

                fetched = service.handle(
                    _request(
                        "creation_changeset.get",
                        {"changeset_id": created["changeset_id"]},
                        request_id="get",
                    )
                )["result"]["changeset"]
                listed = service.handle(
                    _request(
                        "creation_changeset.list",
                        {"workspace_id": workspace["workspace_id"], "limit": 10},
                        request_id="list",
                    )
                )["result"]["changesets"]
                diff = service.handle(
                    _request(
                        "creation_changeset.diff",
                        {"changeset_id": created["changeset_id"]},
                        request_id="diff",
                    )
                )["result"]["diff"]
                self.assertEqual(created, fetched)
                self.assertEqual([created], listed)
                self.assertEqual(created["review_sha256"], diff["review_sha256"])

                approved = service.handle(
                    _request(
                        "creation_changeset.approve",
                        {
                            "changeset_id": created["changeset_id"],
                            "expected_record_hash": created["record_hash"],
                            "expected_review_sha256": created["review_sha256"],
                        },
                        request_id="approve",
                    )
                )["result"]["changeset"]
                applied_result = service.handle(
                    _request(
                        "creation_changeset.apply",
                        {
                            "changeset_id": approved["changeset_id"],
                            "expected_record_hash": approved["record_hash"],
                            "expected_review_sha256": approved["review_sha256"],
                            "expected_root_generation": workspace["root_generation"],
                        },
                        request_id="apply",
                    )
                )["result"]
                self.assertEqual("applied", applied_result["changeset"]["status"])
                self.assertEqual(
                    "Updated service project",
                    json.loads((root / "project.json").read_text(encoding="utf-8"))["title"],
                )
                recovered = service.handle(
                    _request(
                        "creation_changeset.recover",
                        {
                            "changeset_id": applied_result["changeset"]["changeset_id"],
                            "mode": "resume",
                            "expected_record_hash": applied_result["changeset"]["record_hash"],
                            "expected_review_sha256": applied_result["changeset"]["review_sha256"],
                            "expected_root_generation": applied_result["workspace"][
                                "root_generation"
                            ],
                        },
                        request_id="recover",
                    )
                )["result"]
                self.assertEqual("not_needed", recovered["outcome"])
                for response in (created, fetched, listed, diff, applied_result, recovered):
                    self.assertFalse(_contains_native_path(response, root))
            finally:
                service.close()
                service.store.close()

    def test_changeset_list_rejects_an_oversized_response_inside_request_handling(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                created = service.handle(
                    _request(
                        "creation_changeset.create",
                        {
                            **_authority(workspace),
                            "changeset_id": "oversized_list_seed",
                            "operations": [
                                _replace_project_operation(root, title="Oversized list seed")
                            ],
                        },
                        request_id="oversized_seed",
                    )
                )["result"]["changeset"]
                oversized_record = json.loads(json.dumps(created))
                oversized_record["operations"][0]["path"] = "/".join(["a" * 200] * 5)
                oversized_record["record_hash"] = creation_changeset_record_hash(oversized_record)
                with (
                    patch.object(
                        service.creation_authoring,
                        "list",
                        return_value=[oversized_record] * 1000,
                    ),
                    self.assertRaises(StudioError) as raised,
                ):
                    service.handle(
                        _request(
                            "creation_changeset.list",
                            {"workspace_id": workspace["workspace_id"], "limit": 1000},
                            request_id="oversized_list",
                        )
                    )
                self.assertEqual("internal_error", raised.exception.code)
                self.assertEqual(
                    "Studio response exceeds the NDJSON line limit",
                    raised.exception.message,
                )

                requests = b"".join(
                    (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
                    for request in (
                        _request(
                            "creation_changeset.list",
                            {"limit": 1000},
                            request_id="oversized_serve_list",
                        ),
                        _request("service.initialize", {}, request_id="after_oversized_list"),
                    )
                )
                output = io.BytesIO()
                with patch(
                    "worldforge.studio.service.CreationAuthoringManager.list",
                    return_value=[oversized_record] * 1000,
                ):
                    exit_code = serve(
                        io.BytesIO(requests),
                        output,
                        data_dir=Path(temp) / "oversized-service",
                    )
                responses = [json.loads(line) for line in output.getvalue().splitlines()]
                self.assertEqual(0, exit_code)
                self.assertEqual(["error", "response"], [item["kind"] for item in responses])
                self.assertEqual("internal_error", responses[0]["error"]["code"])
                self.assertEqual("after_oversized_list", responses[1]["request_id"])
            finally:
                service.close()
                service.store.close()

    def test_creation_workspace_create_reports_bounded_scaffold_failure_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sensitive = str(base / "native" / "secret")
            service = StudioService(StudioStore(base / "studio"))
            try:
                grant = service.handle(
                    _request(
                        "creation_root_grant.create",
                        {
                            "grant_id": "grant_bounded_scaffold",
                            "role": "new_target",
                            "display_name": "Bounded scaffold",
                            "path": str(base / "created-project"),
                            "expected_project_hash": None,
                        },
                        request_id="grant",
                    )
                )["result"]["grant"]

                with (
                    patch(
                        "worldforge.studio.creation_workspaces.create_creation_project",
                        side_effect=CreationScaffoldError(
                            f"native publication failed at {sensitive}",
                            reason_code="creation_scaffold_recovery_required",
                        ),
                    ),
                    self.assertRaises(StudioError) as raised,
                ):
                    service.handle(
                        _request(
                            "creation_workspace.create",
                            {
                                "workspace_id": "workspace_bounded_scaffold",
                                "grant_id": grant["grant_id"],
                                "expected_grant_generation": grant["generation"],
                                "project_kind": "universe_library",
                                "project_id": "bounded_scaffold",
                                "title": "Bounded scaffold",
                                "default_locale": "en",
                                "project_version": "0.1.0",
                            },
                            request_id="create",
                        )
                    )

                self.assertEqual("invalid_state", raised.exception.code)
                self.assertEqual(
                    "Creation failed before workspace registration",
                    raised.exception.message,
                )
                self.assertEqual(
                    {
                        "reason_code": "creation_scaffold_recovery_required",
                        "phase": "before_publication",
                    },
                    raised.exception.details,
                )
                self.assertNotIn(sensitive, json.dumps(raised.exception.details, sort_keys=True))
                self.assertNotIn(sensitive, raised.exception.message)
            finally:
                service.close()
                service.store.close()

    def test_creation_workspace_scaffold_failure_details_reject_untrusted_reason_codes(
        self,
    ) -> None:
        sensitive = "/tmp/native/secret"
        cases: tuple[object, ...] = (
            sensitive,
            r"C:\native\secret",
            "creation_scaffold_recovery_required/../../secret",
            "creation_scaffold_recovery_required_é",
            "creation_scaffold_" + ("x" * 128),
            "creation_scaffold_unknown",
            123,
        )
        for reason_code in cases:
            with self.subTest(reason_code=reason_code):
                error = CreationScaffoldError("native publication failed")
                error.reason_code = reason_code  # type: ignore[assignment]

                details = _bounded_scaffold_failure_details(error, phase="not_a_phase")

                self.assertEqual(
                    {
                        "reason_code": "creation_scaffold_failed",
                        "phase": "before_publication",
                    },
                    details,
                )
                encoded = json.dumps(details, sort_keys=True)
                self.assertNotIn(sensitive, encoded)
                self.assertNotIn("secret", encoded)
                self.assertNotIn("é", encoded)

    def test_creation_workspace_scaffold_failure_details_preserve_canonical_reason_codes(
        self,
    ) -> None:
        for reason_code in (
            "creation_scaffold_failed",
            "creation_scaffold_inputs_invalid",
            "creation_scaffold_recovery_required",
        ):
            with self.subTest(reason_code=reason_code):
                details = _bounded_scaffold_failure_details(
                    CreationScaffoldError("safe", reason_code=reason_code),
                    phase="cleanup_authorized",
                )

                self.assertEqual(
                    {"reason_code": reason_code, "phase": "cleanup_authorized"},
                    details,
                )

    def test_changeset_filesystem_errors_do_not_leak_native_store_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                sensitive = str(service.store.blobs_dir)
                payload = b'{"pathless":"blob"}\n'
                digest = hashlib.sha256(payload).hexdigest()
                with (
                    patch(
                        "worldforge.studio.changesets.path_file_stat",
                        side_effect=OSError(f"sensitive native path: {sensitive}"),
                    ),
                    self.assertRaises(StudioError) as shared_helper_error,
                ):
                    service.creation_authoring._store_blob(payload, digest)
                self.assertEqual("conflict", shared_helper_error.exception.code)
                self.assertNotIn(sensitive, shared_helper_error.exception.message)

                with (
                    patch(
                        "worldforge.studio.changesets.os.stat",
                        side_effect=OSError(f"sensitive native path: {sensitive}"),
                    ),
                    self.assertRaises(StudioError) as entry_info_error,
                ):
                    service.creation_authoring._store_blob(payload, digest)
                self.assertNotIn(sensitive, entry_info_error.exception.message)

                with (
                    patch(
                        "worldforge.studio.changesets.os.open",
                        side_effect=OSError(f"sensitive native path: {sensitive}"),
                    ),
                    self.assertRaises(StudioError) as pinned_open_error,
                ):
                    service.creation_authoring._store_blob(payload, digest)
                self.assertNotIn(sensitive, pinned_open_error.exception.message)

                blob_root_info = service.store.blobs_dir.stat()
                with (
                    patch(
                        "worldforge.studio.changesets._platform_name",
                        return_value="nt",
                    ),
                    patch(
                        "worldforge.studio.changesets._safe_directory_info",
                        return_value=blob_root_info,
                    ),
                    patch(
                        "worldforge.studio.changesets._windows_lock_directory",
                        return_value=101,
                    ),
                    patch("worldforge.studio.changesets._windows_close_handle"),
                    patch.object(
                        Path,
                        "iterdir",
                        side_effect=OSError(f"sensitive native path: {sensitive}"),
                    ),
                    self.assertRaises(StudioError) as windows_parent_error,
                ):
                    service.creation_authoring._store_blob(payload, digest)
                self.assertNotIn(sensitive, windows_parent_error.exception.message)

                with (
                    patch(
                        "worldforge.studio.creation_authoring.os.mkdir",
                        side_effect=OSError(f"sensitive native path: {sensitive}"),
                    ),
                    self.assertRaises(StudioError) as direct_error,
                ):
                    service.creation_authoring._store_blob(payload, digest)
                self.assertEqual("internal_error", direct_error.exception.code)
                self.assertNotIn(sensitive, direct_error.exception.message)

                with (
                    patch(
                        "worldforge.studio.creation_authoring.os.mkdir",
                        side_effect=OSError(f"sensitive native path: {sensitive}"),
                    ),
                    self.assertRaises(StudioError) as raised,
                ):
                    service.handle(
                        _request(
                            "creation_changeset.create",
                            {
                                **_authority(workspace),
                                "changeset_id": "pathless_blob_error",
                                "operations": [
                                    _replace_project_operation(root, title="Pathless error")
                                ],
                            },
                            request_id="pathless_error",
                        )
                    )
                self.assertEqual("internal_error", raised.exception.code)
                self.assertNotIn(sensitive, raised.exception.message)

                if os.name == "posix":
                    with (
                        patch(
                            "worldforge.studio.workspaces._open_posix_ancestry",
                            side_effect=OSError(f"sensitive native path: {root}"),
                        ),
                        self.assertRaises(StudioError) as ancestry_error,
                    ):
                        service.handle(
                            _request(
                                "creation_workspace.open",
                                {"workspace_id": workspace["workspace_id"]},
                                request_id="pathless_ancestry_error",
                            )
                        )
                    self.assertNotIn(str(root), ancestry_error.exception.message)
            finally:
                service.close()
                service.store.close()

    def test_changeset_rejection_stale_review_and_bounded_list_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                created: list[dict[str, object]] = []
                for index in range(3):
                    created.append(
                        service.handle(
                            _request(
                                "creation_changeset.create",
                                {
                                    **_authority(workspace),
                                    "changeset_id": f"service_rejected_{index}",
                                    "operations": [
                                        _replace_project_operation(
                                            root,
                                            title=f"Rejected title {index}",
                                        )
                                    ],
                                },
                                request_id=f"create_rejected_{index}",
                            )
                        )["result"]["changeset"]
                    )

                with self.assertRaises(StudioError) as raised:
                    service.handle(
                        _request(
                            "creation_changeset.approve",
                            {
                                "changeset_id": created[0]["changeset_id"],
                                "expected_record_hash": "0" * 64,
                                "expected_review_sha256": created[0]["review_sha256"],
                            },
                            request_id="stale_approve",
                        )
                    )
                self.assertEqual("conflict", raised.exception.code)

                rejected = service.handle(
                    _request(
                        "creation_changeset.reject",
                        {
                            "changeset_id": created[0]["changeset_id"],
                            "expected_record_hash": created[0]["record_hash"],
                            "expected_review_sha256": created[0]["review_sha256"],
                        },
                        request_id="reject",
                    )
                )["result"]["changeset"]
                self.assertEqual("rejected", rejected["status"])
                self.assertNotIn("document", rejected["operations"][0])

                with self.assertRaises(StudioError) as raised:
                    service.handle(
                        _request(
                            "creation_changeset.apply",
                            {
                                "changeset_id": rejected["changeset_id"],
                                "expected_record_hash": rejected["record_hash"],
                                "expected_review_sha256": rejected["review_sha256"],
                                "expected_root_generation": workspace["root_generation"],
                            },
                            request_id="apply_rejected",
                        )
                    )
                self.assertEqual("invalid_state", raised.exception.code)

                listed = service.handle(
                    _request(
                        "creation_changeset.list",
                        {"workspace_id": workspace["workspace_id"], "limit": 2},
                        request_id="bounded_list",
                    )
                )["result"]["changesets"]
                self.assertEqual(2, len(listed))
                self.assertFalse(_contains_native_path(listed, root))
                self.assertTrue(
                    all(
                        "document" not in operation
                        for item in listed
                        for operation in item["operations"]
                    )
                )
            finally:
                service.close()
                service.store.close()

    def test_inline_phase_validate_complete_and_reopen_are_idempotent_and_pathless(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                report = _brief_report(root)
                before = _tree_bytes(root)
                validation_params = {
                    **_authority(workspace),
                    "report": report,
                    "artifact_registry": [],
                }
                first_validation = service.handle(
                    _request(
                        "creation_phase.validate",
                        validation_params,
                        request_id="validate_1",
                    )
                )["result"]
                second_validation = service.handle(
                    _request(
                        "creation_phase.validate",
                        validation_params,
                        request_id="validate_2",
                    )
                )["result"]
                self.assertEqual(first_validation, second_validation)
                self.assertEqual(report, first_validation["report"])
                self.assertEqual(before, _tree_bytes(root))

                completed = service.handle(
                    _request(
                        "creation_phase.complete",
                        validation_params,
                        request_id="complete",
                    )
                )["result"]
                self.assertEqual(
                    ["p00_brief"],
                    completed["workflow"]["status"]["completed_phases"],
                )
                self.assertFalse(_contains_native_path(completed, root))

                repeated = service.handle(
                    _request(
                        "creation_phase.complete",
                        {
                            **_authority(completed["workspace"]),
                            "report": report,
                            "artifact_registry": [],
                        },
                        request_id="complete_again",
                    )
                )["result"]
                self.assertEqual(completed, repeated)

                reopened = service.handle(
                    _request(
                        "creation_phase.reopen",
                        {
                            **_authority(completed["workspace"]),
                            "phase_id": "p00_brief",
                            "reason": "Requirements changed",
                            "approved_by": "lead_reviewer",
                        },
                        request_id="reopen",
                    )
                )["result"]
                self.assertEqual("p00_brief", reopened["workflow"]["current_phase"])
                self.assertEqual([], reopened["workflow"]["status"]["completed_phases"])
                self.assertEqual(
                    1,
                    len(reopened["workflow"]["status"]["invalidated_reports"]),
                )
                self.assertFalse(_contains_native_path(reopened, root))
            finally:
                service.close()
                service.store.close()

    def test_completed_phase_report_read_is_authority_bound_pathless_and_rejects_invalidated(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                report = _brief_report(root)
                completed = service.handle(
                    _request(
                        "creation_phase.complete",
                        {
                            **_authority(workspace),
                            "report": report,
                            "artifact_registry": [],
                        },
                        request_id="complete_for_read",
                    )
                )["result"]
                authority = _authority(completed["workspace"])
                before = _tree_bytes(root)
                read = service.handle(
                    _request(
                        "creation_phase.read",
                        {**authority, "phase_id": "p00_brief"},
                        request_id="read_completed_phase",
                    )
                )["result"]

                self.assertEqual(report, read["report"])
                self.assertEqual(
                    {
                        "phase": "p00_brief",
                        "status": "ready",
                        "content_hash": report["content_hash"],
                        "invalidation_dependencies": report["invalidation_dependencies"],
                    },
                    read["reference"],
                )
                self.assertEqual(completed["workspace"], read["workspace"])
                self.assertEqual(completed["workflow"], read["workflow"])
                self.assertFalse(_contains_native_path(read, root))
                self.assertNotIn("path", read["reference"])
                self.assertEqual(before, _tree_bytes(root))

                for name, override in {
                    "root_generation": {"expected_root_generation": 99},
                    "source_revision": {"expected_source_revision": "e" * 64},
                    "workflow_hash": {"expected_workflow_status_hash": "f" * 64},
                }.items():
                    with self.subTest(name=name), self.assertRaises(StudioError) as raised:
                        service.handle(
                            _request(
                                "creation_phase.read",
                                {**authority, **override, "phase_id": "p00_brief"},
                                request_id=f"stale_phase_read_{name}",
                            )
                        )
                    self.assertEqual("conflict", raised.exception.code)
                    self.assertNotIn(str(root), str(raised.exception))
                    self.assertEqual(before, _tree_bytes(root))

                reopened = service.handle(
                    _request(
                        "creation_phase.reopen",
                        {
                            **authority,
                            "phase_id": "p00_brief",
                            "reason": "Requirements changed",
                            "approved_by": "lead_reviewer",
                        },
                        request_id="reopen_before_read",
                    )
                )["result"]
                with self.assertRaises(StudioError) as raised:
                    service.handle(
                        _request(
                            "creation_phase.read",
                            {**_authority(reopened["workspace"]), "phase_id": "p00_brief"},
                            request_id="read_invalidated_phase",
                        )
                    )
                self.assertEqual("not_found", raised.exception.code)
                self.assertNotIn(str(root), str(raised.exception))
            finally:
                service.close()
                service.store.close()

    def test_completed_phase_report_read_rejects_missing_or_replaced_bytes_pathlessly(self) -> None:
        for mutation in ("missing", "replaced"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                service, root, workspace = _registered_service(Path(temp))
                try:
                    report = _brief_report(root)
                    completed = service.handle(
                        _request(
                            "creation_phase.complete",
                            {
                                **_authority(workspace),
                                "report": report,
                                "artifact_registry": [],
                            },
                            request_id=f"complete_for_{mutation}",
                        )
                    )["result"]
                    reference = completed["workflow"]["status"]["reports"][0]
                    report_path = root.joinpath(*reference["path"].split("/"))
                    if mutation == "missing":
                        report_path.unlink()
                    else:
                        report_path.write_bytes(b"{}\n")
                    before = _tree_bytes(root)
                    with self.assertRaises(StudioError) as raised:
                        service.handle(
                            _request(
                                "creation_phase.read",
                                {
                                    **_authority(completed["workspace"]),
                                    "phase_id": "p00_brief",
                                },
                                request_id=f"read_{mutation}_phase",
                            )
                        )
                    self.assertIn(raised.exception.code, {"not_found", "conflict"})
                    self.assertNotIn(str(root), str(raised.exception))
                    self.assertEqual(before, _tree_bytes(root))
                finally:
                    service.close()
                    service.store.close()

    def test_phase_authority_cas_rejects_before_world_tree_or_database_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                report = _brief_report(root)
                before = _tree_bytes(root)
                before_workspace = service.handle(
                    _request(
                        "creation_workspace.get",
                        {"workspace_id": workspace["workspace_id"]},
                        request_id="workspace_before_stale",
                    )
                )["result"]["workspace"]
                stale_cases = {
                    "root_generation": {
                        "expected_root_generation": workspace["root_generation"] + 1
                    },
                    "source_revision": {"expected_source_revision": "e" * 64},
                    "workflow_status_hash": {"expected_workflow_status_hash": "f" * 64},
                }
                for name, override in stale_cases.items():
                    with self.subTest(name=name):
                        stale = {
                            **_authority(workspace),
                            **override,
                            "report": report,
                            "artifact_registry": [],
                        }
                        with self.assertRaises(StudioError) as raised:
                            service.handle(
                                _request(
                                    "creation_phase.complete",
                                    stale,
                                    request_id=f"stale_complete_{name}",
                                )
                            )
                        self.assertEqual("conflict", raised.exception.code)
                        self.assertEqual(before, _tree_bytes(root))
                        current_workspace = service.handle(
                            _request(
                                "creation_workspace.get",
                                {"workspace_id": workspace["workspace_id"]},
                                request_id=f"workspace_after_{name}",
                            )
                        )["result"]["workspace"]
                        self.assertEqual(before_workspace, current_workspace)
                self.assertEqual(
                    load_creation_workflow_status(root)["content_hash"],
                    workspace["workflow_status_hash"],
                )
            finally:
                service.close()
                service.store.close()

    def test_phase_validation_rejects_crossed_semantics_and_unsafe_registry_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                brief = _brief_report(root)
                reviewer_mismatch = json.loads(json.dumps(brief))
                reviewer_mismatch["reviewer"]["id"] = "different_reviewer"
                _reseal(reviewer_mismatch)

                output_role_mismatch = json.loads(json.dumps(brief))
                output_role_mismatch["output_evidence"]["role"] = "experience_classification"
                _reseal(output_role_mismatch["output_evidence"])
                _reseal(output_role_mismatch)

                invalid_not_applicable = json.loads(json.dumps(brief))
                invalid_not_applicable["status"] = "not_applicable"
                invalid_not_applicable["output_evidence"] = None
                _reseal(invalid_not_applicable)

                cases = {
                    "wrong_current_phase": (_experience_report(root), []),
                    "reviewer_mismatch": (reviewer_mismatch, []),
                    "output_role_mismatch": (output_role_mismatch, []),
                    "invalid_not_applicable": (invalid_not_applicable, []),
                    "unsafe_artifact_registry": (brief, [{"native_path": str(root)}]),
                }
                before = _tree_bytes(root)
                before_workspace = service.handle(
                    _request(
                        "creation_workspace.get",
                        {"workspace_id": workspace["workspace_id"]},
                        request_id="workspace_before_invalid_reports",
                    )
                )["result"]["workspace"]
                for name, (report, registry) in cases.items():
                    with self.subTest(name=name):
                        with self.assertRaises(StudioError) as raised:
                            service.handle(
                                _request(
                                    "creation_phase.validate",
                                    {
                                        **_authority(workspace),
                                        "report": report,
                                        "artifact_registry": registry,
                                    },
                                    request_id=f"invalid_report_{name}",
                                )
                            )
                        self.assertEqual("invalid_state", raised.exception.code)
                        self.assertNotIn(str(root), str(raised.exception))
                        self.assertEqual(before, _tree_bytes(root))
                        self.assertEqual(
                            before_workspace,
                            service.handle(
                                _request(
                                    "creation_workspace.get",
                                    {"workspace_id": workspace["workspace_id"]},
                                    request_id=f"workspace_after_invalid_{name}",
                                )
                            )["result"]["workspace"],
                        )
            finally:
                service.close()
                service.store.close()

    def test_corrupt_workflow_is_classified_and_reconcile_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                status_path = root / ".worldforge" / "status.json"
                status_path.write_bytes(b"{}\n")
                workflow = service.handle(
                    _request(
                        "creation_workflow.get",
                        {"workspace_id": workspace["workspace_id"]},
                        request_id="corrupt_workflow_get",
                    )
                )["result"]["workflow"]
                self.assertEqual("invalid", workflow["state"])
                self.assertEqual(hashlib.sha256(b"{}\n").hexdigest(), workflow["status_hash"])
                refreshed_workspace = service.handle(
                    _request(
                        "creation_workspace.get",
                        {"workspace_id": workspace["workspace_id"]},
                        request_id="workspace_after_corruption",
                    )
                )["result"]["workspace"]
                before = _tree_bytes(root)

                with self.assertRaises(StudioError) as raised:
                    service.handle(
                        _request(
                            "creation_workflow.reconcile",
                            {
                                **_authority(refreshed_workspace),
                                "artifact_registry": [],
                            },
                            request_id="reconcile_corrupt_workflow",
                        )
                    )
                self.assertEqual("invalid_state", raised.exception.code)
                self.assertNotIn(str(root), str(raised.exception))
                self.assertEqual(before, _tree_bytes(root))
                self.assertEqual(
                    refreshed_workspace,
                    service.handle(
                        _request(
                            "creation_workspace.get",
                            {"workspace_id": workspace["workspace_id"]},
                            request_id="workspace_after_failed_reconcile",
                        )
                    )["result"]["workspace"],
                )
            finally:
                service.close()
                service.store.close()

    def test_reconcile_after_source_apply_uses_the_embedded_workflow_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            try:
                created = service.handle(
                    _request(
                        "creation_changeset.create",
                        {
                            **_authority(workspace),
                            "changeset_id": "reconcile_title_change",
                            "operations": [
                                _replace_project_operation(root, title="Reconciled project")
                            ],
                        },
                        request_id="create_reconcile",
                    )
                )["result"]["changeset"]
                approved = service.handle(
                    _request(
                        "creation_changeset.approve",
                        {
                            "changeset_id": created["changeset_id"],
                            "expected_record_hash": created["record_hash"],
                            "expected_review_sha256": created["review_sha256"],
                        },
                        request_id="approve_reconcile",
                    )
                )["result"]["changeset"]
                applied = service.handle(
                    _request(
                        "creation_changeset.apply",
                        {
                            "changeset_id": approved["changeset_id"],
                            "expected_record_hash": approved["record_hash"],
                            "expected_review_sha256": approved["review_sha256"],
                            "expected_root_generation": workspace["root_generation"],
                        },
                        request_id="apply_reconcile",
                    )
                )["result"]
                self.assertEqual("invalid", applied["workflow"]["state"])
                self.assertEqual(
                    workspace["workflow_status_hash"],
                    applied["workflow"]["status_hash"],
                )

                reconciled = service.handle(
                    _request(
                        "creation_workflow.reconcile",
                        {
                            **_authority(applied["workspace"]),
                            "artifact_registry": [],
                        },
                        request_id="reconcile",
                    )
                )["result"]
                self.assertEqual("active", reconciled["workflow"]["state"])
                self.assertEqual(
                    applied["workspace"]["source_revision"],
                    reconciled["workspace"]["source_revision"],
                )
                self.assertEqual(
                    load_creation_project(root / "project.json").project["content_hash"],
                    reconciled["workflow"]["status"]["project"]["content_hash"],
                )
                self.assertGreater(
                    reconciled["workspace"]["root_generation"],
                    applied["workspace"]["root_generation"],
                )
                self.assertFalse(_contains_native_path(reconciled, root))
            finally:
                service.close()
                service.store.close()


if __name__ == "__main__":
    unittest.main()
