from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worldforge.__main__ import _resolve_generic_assetpack_cli_source
from worldforge.creation_contracts import canonical_creation_hash, load_creation_project
from worldforge.creation_scaffold import create_creation_project
from worldforge.creation_workflow import (
    complete_creation_phase_inline,
    initial_creation_workflow_status,
)
from worldforge.gamepack import build_authoring_capability_ledger, build_gamepack
from worldforge.generic_assetpack import build_generic_assetpack_manifest
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.phase_report_v3 import (
    build_phase_output_evidence_v2,
    build_phase_report_v3,
    document_identity,
    validate_artifact_documents,
)
from worldforge.retained_tree import RetainedDirectoryFileCensus, RetainedTreeCapacityError
from worldforge.studio.contracts import (
    METHODS,
    METHODS_V2,
    METHODS_V3,
    METHODS_V4,
    validate_studio_creation_artifact,
    validate_studio_creation_evidence,
    validate_studio_protocol_envelope,
)
from worldforge.studio.creation_evidence import (
    _ignored_history_count,
    _projection,
    _qa_criterion_hashes,
)
from worldforge.studio.errors import StudioError
from worldforge.studio.service import StudioService
from worldforge.studio.storage import StudioStore

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_ROOT = Path(__file__).resolve().parents[1]
_PUZZLE_ROOT = _ROOT / "examples/multigenre-contracts/abstract-puzzle"


def _authority() -> dict[str, object]:
    return {
        "workspace_id": "workspace_01",
        "root_generation": 3,
        "source_revision": _HASH_A,
        "workflow_status_hash": _HASH_B,
    }


def _artifact() -> dict[str, object]:
    record: dict[str, object] = {
        "format": "world-forge.studio_creation_artifact",
        "format_version": 1,
        "artifact_id": f"artifact_{_HASH_C}",
        "subject": {
            "format": "world-forge.gamepack",
            "format_version": 1,
            "id": "neutral_game",
            "content_hash": _HASH_C,
        },
        "lifecycle": "active",
        "roles": ["compiled_logic"],
        "producer": {
            "kind": "active_phase_report",
            "phase_id": "p10_canon_lock",
            "reference_id": "p10_report",
        },
        "references": {"dependency_count": 4, "dependent_count": 2},
        "authority": _authority(),
        "record_hash": "",
    }
    record["record_hash"] = canonical_payload_hash(record, hash_field="record_hash")
    return record


def _evidence() -> dict[str, object]:
    value: dict[str, object] = {
        "format": "world-forge.studio_creation_evidence",
        "format_version": 1,
        "evidence_id": "evidence_workspace_01",
        "authority": _authority(),
        "artifact_snapshot_hash": _HASH_C,
        "artifact_counts": {
            "active": 1,
            "invalidated": 0,
            "historical": 0,
            "candidate": 0,
            "ignored": 0,
        },
        "dimensions": {
            "authoring": "valid",
            "compilation": "compiled",
            "assets": "unplanned",
            "adapter": "declared",
            "execution": [
                {
                    "platform": "platform:linux_x86_64",
                    "status": "untested",
                    "evidence_ids": [],
                }
            ],
            "packaging": "unverified",
            "release": "blocked",
        },
        "blocker_reason_codes": ["assets_not_sealed", "native_evidence_missing"],
        "mechanics": {
            "artifact_id": None,
            "total": 0,
            "status_counts": {
                "supported_current": 0,
                "game_extension_verified": 0,
                "authoring_only": 0,
                "blocked": 0,
            },
            "required_features": [],
            "missing_features": [],
        },
        "runtime": {
            "requested_adapter": "gamepack_raylib_2d_puzzle",
            "resolved_adapter": None,
            "required_features": ["logic:finite_state"],
            "missing_features": ["logic:finite_state"],
            "platforms": [
                {
                    "platform": "platform:linux_x86_64",
                    "status": "untested",
                    "evidence_ids": [],
                }
            ],
        },
        "assets": {
            "subject_artifact_id": None,
            "target_artifact_id": None,
            "style_artifact_id": None,
            "inventory_artifact_id": None,
            "assetpack_artifact_id": None,
            "inventory_assets": 0,
            "lineage_complete": 0,
            "lineage_partial": 0,
            "qa_passed": 0,
            "qa_failed": 0,
            "licensed": 0,
        },
        "materialization": {
            "enabled": False,
            "state": "blocked",
            "prerequisites": [
                {"code": "release_blocked", "satisfied": False, "message": "Release is blocked."}
            ],
        },
        "readiness": {
            "format": "world-forge.creation_readiness",
            "format_version": 1,
            "id": "readiness_neutral",
            "content_hash": _HASH_A,
        },
        "handoff": {
            "format": "world-forge.creation_handoff",
            "format_version": 1,
            "id": "handoff_neutral",
            "content_hash": _HASH_B,
        },
        "content_hash": "",
    }
    value["content_hash"] = canonical_creation_hash(value)
    return value


def _request(
    method: str,
    params: dict[str, object],
    *,
    request_id: str,
) -> dict[str, object]:
    return {
        "protocol": "rpg-world-forge.studio_protocol",
        "protocol_version": 4,
        "kind": "request",
        "request_id": request_id,
        "method": method,
        "params": params,
    }


def _registered_service(base: Path) -> tuple[StudioService, Path, dict[str, object]]:
    root = base / "project"
    create_creation_project(root, project_id="evidence_project", title="Evidence project")
    loaded = load_creation_project(root / "project.json")
    service = StudioService(StudioStore(base / "studio"))
    grant = service.handle(
        {
            **_request("service.initialize", {}, request_id="initialize_v3"),
            "protocol_version": 3,
        }
    )
    assert grant["result"]["protocol_version"] == 3
    grant = service.handle(
        {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "request",
            "request_id": "grant",
            "method": "creation_root_grant.create",
            "params": {
                "grant_id": "grant_evidence_project",
                "role": "existing_root",
                "display_name": "Evidence project",
                "path": str(root),
                "expected_project_hash": loaded.project["content_hash"],
            },
        }
    )["result"]["grant"]
    workspace = service.handle(
        {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "request",
            "request_id": "workspace",
            "method": "creation_workspace.register",
            "params": {
                "workspace_id": "workspace_evidence_project",
                "grant_id": grant["grant_id"],
                "expected_grant_generation": grant["generation"],
                "expected_project_hash": loaded.project["content_hash"],
            },
        }
    )["result"]["workspace"]
    return service, root, workspace


def _register_root(
    base: Path,
    root: Path,
) -> tuple[StudioService, dict[str, object]]:
    loaded = load_creation_project(root / "project.json")
    service = StudioService(StudioStore(base / "studio"))
    grant = service.handle(
        {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "request",
            "request_id": "grant",
            "method": "creation_root_grant.create",
            "params": {
                "grant_id": "grant_puzzle_evidence",
                "role": "existing_root",
                "display_name": "Puzzle evidence",
                "path": str(root),
                "expected_project_hash": loaded.project["content_hash"],
            },
        }
    )["result"]["grant"]
    workspace = service.handle(
        {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "request",
            "request_id": "workspace",
            "method": "creation_workspace.register",
            "params": {
                "workspace_id": "workspace_puzzle_evidence",
                "grant_id": grant["grant_id"],
                "expected_grant_generation": grant["generation"],
                "expected_project_hash": loaded.project["content_hash"],
            },
        }
    )["result"]["workspace"]
    return service, workspace


def _puzzle_asset_graph() -> tuple[dict[str, object], ...]:
    source = _resolve_generic_assetpack_cli_source(_PUZZLE_ROOT / "assets/manifest.json")
    assetpack = build_generic_assetpack_manifest(**source)
    records: list[dict[str, object]] = []
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


def _phase_report(
    loaded: object,
    *,
    phase: str,
    status: str,
    role: str | None,
    subject: dict[str, object] | None,
    rationale_code: str = "phase_ready",
    artifact_registry: tuple[dict[str, object], ...] = (),
    additional_evidence: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    reviewer = {"id": "lead_reviewer", "role": "validation_analyst"}
    evidence_subject = loaded.profile if subject is None else subject
    evidence = [
        {
            "evidence_id": "reviewed_subject",
            "claim": "The exact subject was reviewed.",
            "subject": document_identity(evidence_subject),
        }
    ]
    for index, document in enumerate(additional_evidence):
        evidence.append(
            {
                "evidence_id": f"supporting_subject_{index:02d}",
                "claim": "The exact supporting subject was reviewed.",
                "subject": document_identity(document),
            }
        )
    evidence.sort(key=lambda item: item["evidence_id"].encode("utf-8"))
    output = None
    if status == "ready":
        assert role is not None and subject is not None
        output = build_phase_output_evidence_v2(
            evidence_id=f"{phase}_output",
            phase=phase,
            role=role,
            subject=document_identity(subject),
            reviewer_id=reviewer["id"],
            reviewer_role=reviewer["role"],
            artifact_registry=artifact_registry,
            source_project=loaded,
        )
    return build_phase_report_v3(
        loaded,
        phase=phase,
        status=status,
        rationale_code=rationale_code,
        rationale_message=f"Reviewed {phase}.",
        evidence=evidence,
        output_evidence=output,
        reviewer_id=reviewer["id"],
        reviewer_role=reviewer["role"],
        invalidation_dependencies=None,
        artifact_registry=artifact_registry,
    )


def _prepared_puzzle_service(
    base: Path,
) -> tuple[StudioService, Path, dict[str, object]]:
    root = base / "puzzle-project"
    shutil.copytree(_PUZZLE_ROOT, root)
    loaded = load_creation_project(root / "project.json")
    internal = root / ".worldforge"
    history = internal / "artifact_history"
    (internal / "phase_reports").mkdir(parents=True)
    history.mkdir()
    status = initial_creation_workflow_status(loaded)
    (internal / "status.json").write_bytes(
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    for document in (
        loaded.project,
        loaded.profile,
        loaded.manifest,
        *loaded.world_modules,
        *loaded.activity_modules,
        *loaded.narrative_modules,
        *loaded.system_modules,
        *loaded.logic_modules,
    ):
        (history / f"{document['content_hash']}.json").write_bytes(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            + b"\n"
        )

    gamepack = build_gamepack(loaded)
    ledger = build_authoring_capability_ledger(gamepack)
    asset_graph = _puzzle_asset_graph()
    by_format = {str(document["format"]): document for document in asset_graph}
    runtime_documents = tuple(
        json.loads(path.read_text(encoding="utf-8"))
        for path in (
            _ROOT / "examples/multigenre-contracts/runtime/snapshot.json",
            _ROOT / "examples/multigenre-contracts/runtime/registry.json",
            _PUZZLE_ROOT / "runtime/composition.json",
            _PUZZLE_ROOT / "runtime/support-report.json",
        )
    )
    runtime_registry = (*asset_graph, ledger, *runtime_documents)
    ready_phases = (
        ("p00_brief", "project_brief", loaded.project, (), ()),
        ("p01_genre_style", "experience_classification", loaded.profile, (), ()),
        ("p02_world_laws", "interaction_ontology", loaded.logic_modules[0], (), ()),
    )
    for phase, role, subject, registry, evidence in ready_phases:
        report = _phase_report(
            loaded,
            phase=phase,
            status="ready",
            role=role,
            subject=subject,
            artifact_registry=registry,
            additional_evidence=evidence,
        )
        status = complete_creation_phase_inline(
            root,
            report,
            expected_status_hash=status["content_hash"],
            artifact_registry=registry,
        )
    for phase, rationale in (
        ("p03_geography", "world_absent"),
        ("p04_timeline", "chronology_absent"),
        ("p05_societies", "group_structures_absent"),
        ("p06_characters", "actors_absent"),
    ):
        report = _phase_report(
            loaded,
            phase=phase,
            status="not_applicable",
            role=None,
            subject=None,
            rationale_code=rationale,
        )
        status = complete_creation_phase_inline(
            root, report, expected_status_hash=status["content_hash"]
        )
    for phase, role, subject in (("p07_systems", "systems_design", loaded.system_modules[0]),):
        report = _phase_report(loaded, phase=phase, status="ready", role=role, subject=subject)
        status = complete_creation_phase_inline(
            root, report, expected_status_hash=status["content_hash"]
        )
    report = _phase_report(
        loaded,
        phase="p08_world_arcs",
        status="not_applicable",
        role=None,
        subject=None,
        rationale_code="narrative_absent",
    )
    status = complete_creation_phase_inline(
        root, report, expected_status_hash=status["content_hash"]
    )
    report = _phase_report(
        loaded,
        phase="p09_narrative_content",
        status="ready",
        role="typed_content",
        subject=loaded.manifest,
    )
    status = complete_creation_phase_inline(
        root, report, expected_status_hash=status["content_hash"]
    )
    report = _phase_report(
        loaded,
        phase="p10_canon_lock",
        status="ready",
        role="content_lock",
        subject=gamepack,
        artifact_registry=(gamepack, ledger),
        additional_evidence=(ledger,),
    )
    status = complete_creation_phase_inline(
        root,
        report,
        expected_status_hash=status["content_hash"],
        artifact_registry=(gamepack, ledger),
    )
    report = _phase_report(
        loaded,
        phase="p11_art_audio",
        status="ready",
        role="presentation_direction",
        subject=by_format["world-forge.asset_target"],
        artifact_registry=(*asset_graph, ledger),
        additional_evidence=(by_format["world-forge.asset_style"],),
    )
    status = complete_creation_phase_inline(
        root,
        report,
        expected_status_hash=status["content_hash"],
        artifact_registry=(*asset_graph, ledger),
    )
    report = _phase_report(
        loaded,
        phase="p12_asset_specs",
        status="ready",
        role="asset_plan",
        subject=by_format["world-forge.asset_manifest"],
        artifact_registry=(*asset_graph, ledger),
        additional_evidence=(by_format["world-forge.assetpack"],),
    )
    status = complete_creation_phase_inline(
        root,
        report,
        expected_status_hash=status["content_hash"],
        artifact_registry=(*asset_graph, ledger),
    )
    support = runtime_documents[-1]
    report = _phase_report(
        loaded,
        phase="p13_asset_production",
        status="ready",
        role="runtime_compatibility",
        subject=support,
        artifact_registry=runtime_registry,
    )
    complete_creation_phase_inline(
        root,
        report,
        expected_status_hash=status["content_hash"],
        artifact_registry=runtime_registry,
    )
    service, workspace = _register_root(base, root)
    return service, root, workspace


def _authority_params(workspace: dict[str, object]) -> dict[str, object]:
    return {
        "workspace_id": workspace["workspace_id"],
        "expected_root_generation": workspace["root_generation"],
        "expected_source_revision": workspace["source_revision"],
        "expected_workflow_status_hash": workspace["workflow_status_hash"],
        "expected_artifact_snapshot_hash": None,
    }


class StudioCreationEvidenceV4ContractTests(unittest.TestCase):
    def test_v4_schemas_catalog_and_workspace_kind_contract_are_synchronized(self) -> None:
        artifact_schema = json.loads(
            (_ROOT / "schemas/studio-creation-artifact.schema.json").read_text(encoding="utf-8")
        )
        evidence_schema = json.loads(
            (_ROOT / "schemas/studio-creation-evidence.schema.json").read_text(encoding="utf-8")
        )
        protocol_schema = json.loads(
            (_ROOT / "schemas/studio-protocol-v4.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "world-forge.studio_creation_artifact",
            artifact_schema["properties"]["format"]["const"],
        )
        self.assertEqual(
            "world-forge.studio_creation_evidence",
            evidence_schema["properties"]["format"]["const"],
        )
        self.assertEqual(sorted(METHODS_V4), sorted(protocol_schema["$defs"]["method"]["enum"]))

        workspace_schema = json.loads(
            (_ROOT / "schemas/studio-creation-workspace.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["asset_library", "game", "universe_library"],
            sorted(workspace_schema["properties"]["project_kind"]["enum"]),
        )
        catalog = json.loads((_ROOT / "contracts/catalog.json").read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        self.assertEqual(
            "world-forge.studio_creation_artifact",
            entries["studio-creation-artifact"]["format"],
        )
        self.assertEqual(
            "world-forge.studio_creation_evidence",
            entries["studio-creation-evidence"]["format"],
        )
        self.assertEqual(4, entries["studio-protocol-v4"]["version"])
        generated_v4 = (_ROOT / "apps/studio/src/generated/studio-protocol-v4.d.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn("WorldForgeStudioCreationEvidenceProtocolV4", generated_v4)
        generated_contracts = (
            _ROOT / "apps/studio/src/generated/world-forge-contracts.d.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("WorldForgeStudioCreationArtifactEvidenceV1", generated_contracts)
        self.assertIn("WorldForgeStudioCreationEvidenceProjectionV1", generated_contracts)

    def test_asset_projection_exposes_exact_pathless_license_coverage_bindings(self) -> None:
        asset_root = _PUZZLE_ROOT / "assets/production/board_ui"
        license_document = json.loads((asset_root / "license.json").read_text(encoding="utf-8"))
        selection_document = json.loads((asset_root / "selection.json").read_text(encoding="utf-8"))

        license_projection = _projection(
            license_document,
            {"subject": document_identity(license_document)},
            {},
        )
        selection_projection = _projection(
            selection_document,
            {"subject": document_identity(selection_document)},
            {},
        )
        license_facts = {fact["key"]: fact["value"] for fact in license_projection["facts"]}
        selection_facts = {fact["key"]: fact["value"] for fact in selection_projection["facts"]}

        self.assertEqual("board_ui_candidate", license_facts["candidate_artifact_id"])
        self.assertEqual("texture", license_facts["candidate_role"])
        self.assertEqual(
            ["board_ui_candidate:texture"],
            selection_facts["selected_output_bindings"],
        )
        self.assertNotIn("path", json.dumps((license_projection, selection_projection)))

    def test_runtime_support_projection_exposes_bounded_pre_execution_reason_authority(
        self,
    ) -> None:
        support = json.loads(
            (_PUZZLE_ROOT / "runtime/support-report.json").read_text(encoding="utf-8")
        )
        projection = _projection(
            support,
            {"subject": document_identity(support)},
            {},
        )
        facts = {fact["key"]: fact["value"] for fact in projection["facts"]}

        self.assertEqual(support["reason_codes"], facts["reason_codes"])
        self.assertEqual(len(support["reason_codes"]), facts["reason_code_count"])
        self.assertEqual([], facts["missing_capabilities"])
        self.assertEqual(0, facts["missing_capability_count"])
        self.assertEqual(0, facts["evidence_count"])
        self.assertEqual("valid", facts["authoring"])
        self.assertEqual("compiled", facts["compilation"])
        self.assertEqual("sealed", facts["assets"])
        self.assertEqual("declared", facts["adapter"])
        self.assertEqual("unverified", facts["packaging"])
        self.assertEqual("blocked", facts["release"])
        self.assertEqual(
            [
                "platform:linux_x86_64:untested",
                "platform:windows_x86_64:untested",
            ],
            facts["execution_statuses"],
        )
        self.assertNotIn("path", json.dumps(projection))

    def test_mechanic_ledger_is_a_registered_integral_phase_artifact(self) -> None:
        loaded = load_creation_project(
            Path(__file__).resolve().parents[1]
            / "examples/multigenre-contracts/abstract-puzzle/project.json"
        )
        gamepack = build_gamepack(loaded)
        ledger = build_authoring_capability_ledger(gamepack)

        self.assertEqual(
            "world-forge.mechanic_capability_ledger",
            document_identity(ledger)["format"],
        )
        self.assertEqual(
            (gamepack, ledger),
            validate_artifact_documents(loaded, (gamepack, ledger)),
        )

    def test_v4_records_are_closed_pathless_and_redacted(self) -> None:
        artifact = _artifact()
        self.assertEqual(artifact, validate_studio_creation_artifact(artifact))
        evidence = _evidence()
        self.assertEqual(evidence, validate_studio_creation_evidence(evidence))

        for forbidden in (
            {"path": "/private/project"},
            {"prompt": "secret prompt"},
            {"provider_id": "provider"},
            {"toolchain": ["private-tool"]},
        ):
            with self.subTest(forbidden=next(iter(forbidden))):
                changed = copy.deepcopy(artifact)
                changed.update(forbidden)
                with self.assertRaises(ValueError):
                    validate_studio_creation_artifact(changed)

    def test_protocol_v4_is_additive_closed_and_does_not_broaden_v3(self) -> None:
        expected = {
            "service.initialize",
            "creation_artifact.list",
            "creation_artifact.inspect",
            "creation_evidence.inspect",
            "creation_output_grant.create",
            "creation_output_grant.get",
            "creation_output_grant.list",
            "creation_output_grant.revoke",
            "creation_preview.open",
            "creation_preview.read",
            "creation_preview.close",
            "creation_job.create",
            "creation_job.get",
            "creation_job.list",
            "creation_job.cancel",
            "creation_job.recover",
            "creation_event.list",
        }
        self.assertEqual(expected, set(METHODS_V4))
        self.assertTrue(expected.isdisjoint(METHODS - {"service.initialize"}))
        self.assertTrue(expected.isdisjoint(METHODS_V2 - {"service.initialize"}))
        self.assertTrue(expected.isdisjoint(METHODS_V3 - {"service.initialize"}))

        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "request",
            "request_id": "request_01",
            "method": "creation_artifact.list",
            "params": {
                "workspace_id": "workspace_01",
                "expected_root_generation": 3,
                "expected_source_revision": _HASH_A,
                "expected_workflow_status_hash": _HASH_B,
                "expected_artifact_snapshot_hash": None,
                "lifecycle": "active",
                "cursor": None,
                "limit": 32,
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        with self.assertRaisesRegex(ValueError, "not available"):
            validate_studio_protocol_envelope({**request, "protocol_version": 3})
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_protocol_envelope(
                {**request, "params": {**request["params"], "path": "/private/root"}}
            )
        malformed_response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "kind": "response",
            "request_id": "malformed_response",
            "method": "creation_artifact.list",
            "result": {},
        }
        with self.assertRaisesRegex(ValueError, "missing fields"):
            validate_studio_protocol_envelope(malformed_response)

        create_request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "request",
            "request_id": "create_non_scaffolded_kind",
            "method": "creation_workspace.create",
            "params": {
                "grant_id": "grant_game",
                "expected_grant_generation": 0,
                "project_kind": "game",
                "project_id": "game_project",
                "title": "Game project",
                "default_locale": "en",
                "project_version": "0.1.0",
            },
        }
        with self.assertRaisesRegex(
            ValueError,
            (
                "^envelope/params game project is missing facets: "
                "gameplay_family, initial_core_loop, initial_core_verb, "
                "narrative_authorship, narrative_requirement, narrative_topology, "
                "presentation_mode, runtime_support_intent, world_presence$"
            ),
        ):
            validate_studio_protocol_envelope(create_request)
        asset_library_request = {
            **create_request,
            "params": {**create_request["params"], "project_kind": "asset_library"},
        }
        self.assertEqual(
            asset_library_request,
            validate_studio_protocol_envelope(asset_library_request),
        )


class StudioCreationEvidenceV4ServiceTests(unittest.TestCase):
    def test_v3_missing_workflow_remains_distinct_from_invalid_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            (root / ".worldforge/status.json").unlink()
            try:
                readiness = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 3,
                        "kind": "request",
                        "request_id": "missing_workflow_readiness",
                        "method": "creation_readiness.inspect",
                        "params": {"workspace_id": workspace["workspace_id"]},
                    }
                )["result"]["readiness"]
                self.assertEqual("missing", readiness["state"])
                self.assertIn("workflow_missing", readiness["blocker_reason_codes"])
                self.assertNotIn("source_invalid", readiness["blocker_reason_codes"])
            finally:
                service.close()

    def test_noncanonical_archived_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            loaded = load_creation_project(root / "project.json")
            archived = (
                root / ".worldforge/artifact_history" / f"{loaded.project['content_hash']}.json"
            )
            archived.write_bytes(archived.read_bytes() + b" ")
            try:
                with self.assertRaisesRegex(StudioError, "not canonical JSON"):
                    service.handle(
                        _request(
                            "creation_evidence.inspect",
                            _authority_params(workspace),
                            request_id="noncanonical_archive",
                        )
                    )
            finally:
                service.close()

    def test_tampered_canonical_archived_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            loaded = load_creation_project(root / "project.json")
            archived = (
                root / ".worldforge/artifact_history" / f"{loaded.project['content_hash']}.json"
            )
            changed = copy.deepcopy(loaded.project)
            changed["title"] = "Tampered title"
            archived.write_bytes(canonical_json_bytes(changed))
            try:
                with self.assertRaisesRegex(StudioError, "identity or hash changed"):
                    service.handle(
                        _request(
                            "creation_evidence.inspect",
                            _authority_params(workspace),
                            request_id="tampered_archive",
                        )
                    )
            finally:
                service.close()

    def test_hardlinked_archived_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            loaded = load_creation_project(root / "project.json")
            archived = (
                root / ".worldforge/artifact_history" / f"{loaded.project['content_hash']}.json"
            )
            alias = Path(temp) / "archive-alias.json"
            try:
                os.link(archived, alias)
            except OSError as exc:
                service.close()
                self.skipTest(f"hard links are unavailable: {exc}")
            try:
                with self.assertRaisesRegex(StudioError, "missing or unsafe"):
                    service.handle(
                        _request(
                            "creation_evidence.inspect",
                            _authority_params(workspace),
                            request_id="hardlinked_archive",
                        )
                    )
            finally:
                service.close()

    def test_unrelated_history_is_counted_but_never_enters_the_census(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            unrelated = root / ".worldforge/artifact_history" / f"{'f' * 64}.json"
            unrelated.write_text("{}\n", encoding="utf-8")
            try:
                result = service.handle(
                    _request(
                        "creation_artifact.list",
                        {
                            **_authority_params(workspace),
                            "lifecycle": None,
                            "cursor": None,
                            "limit": 64,
                        },
                        request_id="unrelated_history",
                    )
                )["result"]
                self.assertEqual(1, result["counts"]["ignored"])
                self.assertEqual(4, len(result["artifacts"]))
                self.assertNotIn("unrelated-private-name", json.dumps(result, sort_keys=True))
            finally:
                service.close()

    def test_ignored_history_count_is_exact_at_100000(self) -> None:
        names = tuple(f"{index:064x}.json" for index in range(100_000))
        census = RetainedDirectoryFileCensus(
            root=Path("unused/.worldforge/artifact_history"),
            root_identity=(1, 2),
            names=names,
        )
        with mock.patch(
            "worldforge.studio.creation_evidence.capture_retained_directory_file_census",
            return_value=census,
        ) as capture:
            self.assertEqual(100_000, _ignored_history_count(Path("unused"), (1, 2), set()))
        capture.assert_called_once_with(
            Path("unused/.worldforge/artifact_history"),
            maximum_entries=100_000,
            authority_root=Path("unused"),
            expected_authority_identity=(1, 2),
        )

    def test_ignored_history_fails_closed_at_100001_without_a_snapshot(self) -> None:
        with mock.patch(
            "worldforge.studio.creation_evidence.capture_retained_directory_file_census",
            side_effect=RetainedTreeCapacityError(100_000),
        ):
            with self.assertRaises(StudioError) as caught:
                _ignored_history_count(Path("unused"), (1, 2), set())
        self.assertEqual("invalid_state", caught.exception.code)
        self.assertEqual(
            "history_capacity_exceeded",
            caught.exception.details["reason_code"],
        )

    def test_noncanonical_unreachable_history_filename_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            (root / ".worldforge/artifact_history/not-a-content-hash.json").write_bytes(b"{}\n")
            try:
                with self.assertRaisesRegex(StudioError, "canonical content-hash filename"):
                    service.handle(
                        _request(
                            "creation_evidence.inspect",
                            _authority_params(workspace),
                            request_id="noncanonical_history_name",
                        )
                    )
            finally:
                service.close()

    def test_v3_can_register_and_open_an_existing_asset_library(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "asset-library"
            create_creation_project(root, project_id="asset_library", title="Asset library")
            project = json.loads((root / "project.json").read_text(encoding="utf-8"))
            project["project_kind"] = "asset_library"
            project["content_hash"] = canonical_creation_hash(project)
            (root / "project.json").write_bytes(
                json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
                + b"\n"
            )
            loaded = load_creation_project(root / "project.json")
            status = initial_creation_workflow_status(loaded)
            (root / ".worldforge/status.json").write_bytes(
                json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
                + b"\n"
            )
            history = root / ".worldforge/artifact_history"
            (history / f"{project['content_hash']}.json").write_bytes(
                json.dumps(project, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
                + b"\n"
            )

            service, workspace = _register_root(base, root)
            opened = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 3,
                    "kind": "request",
                    "request_id": "open_asset_library",
                    "method": "creation_workspace.open",
                    "params": {"workspace_id": workspace["workspace_id"]},
                }
            )["result"]
            self.assertEqual("asset_library", opened["project_kind"])
            service.close()

    def test_active_integral_closure_drives_assets_mechanics_and_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _prepared_puzzle_service(Path(temp))
            response = service.handle(
                _request(
                    "creation_evidence.inspect",
                    _authority_params(workspace),
                    request_id="integral_evidence",
                )
            )["result"]
            evidence = response["evidence"]

            self.assertEqual("compiled", evidence["dimensions"]["compilation"])
            self.assertEqual("sealed", evidence["dimensions"]["assets"])
            self.assertEqual("declared", evidence["dimensions"]["adapter"])
            self.assertEqual(
                ["untested", "untested"],
                [entry["status"] for entry in evidence["dimensions"]["execution"]],
            )
            self.assertEqual("unverified", evidence["dimensions"]["packaging"])
            self.assertEqual("blocked", evidence["dimensions"]["release"])
            self.assertIn(
                "runtime_evidence_authority_missing",
                evidence["blocker_reason_codes"],
            )
            self.assertIn(
                "native_evidence_authority_unavailable",
                evidence["blocker_reason_codes"],
            )
            self.assertGreater(evidence["mechanics"]["total"], 0)
            self.assertIsNotNone(evidence["mechanics"]["artifact_id"])
            self.assertEqual(
                evidence["mechanics"]["total"],
                evidence["mechanics"]["status_counts"]["authoring_only"],
            )
            self.assertGreater(evidence["assets"]["inventory_assets"], 0)
            self.assertEqual(
                evidence["assets"]["inventory_assets"],
                evidence["assets"]["lineage_complete"],
            )
            self.assertGreater(evidence["assets"]["qa_passed"], 0)
            self.assertEqual(0, evidence["assets"]["qa_failed"])
            self.assertFalse(evidence["materialization"]["enabled"])

            listing = service.handle(
                _request(
                    "creation_artifact.list",
                    {
                        **_authority_params(workspace),
                        "lifecycle": "active",
                        "cursor": None,
                        "limit": 64,
                    },
                    request_id="integral_artifacts",
                )
            )["result"]
            formats = {item["subject"]["format"] for item in listing["artifacts"]}
            self.assertIn("world-forge.gamepack", formats)
            self.assertIn("world-forge.mechanic_capability_ledger", formats)
            self.assertIn("world-forge.assetpack", formats)
            self.assertIn("world-forge.runtime_support_report", formats)
            self.assertNotIn(str(root), json.dumps(response, sort_keys=True))
            legacy_readiness = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 3,
                    "kind": "request",
                    "request_id": "legacy_active_readiness",
                    "method": "creation_readiness.inspect",
                    "params": {"workspace_id": workspace["workspace_id"]},
                }
            )["result"]["readiness"]
            self.assertEqual("compiled", legacy_readiness["report"]["dimensions"]["compilation"])
            self.assertEqual("sealed", legacy_readiness["report"]["dimensions"]["assets"])
            self.assertEqual(
                ["untested", "untested"],
                [
                    entry["status"]
                    for entry in legacy_readiness["report"]["dimensions"]["execution"]
                ],
            )
            self.assertEqual(
                "unverified",
                legacy_readiness["report"]["dimensions"]["packaging"],
            )

            reopened = service.handle(
                {
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 3,
                    "kind": "request",
                    "request_id": "reopen_compilation",
                    "method": "creation_phase.reopen",
                    "params": {
                        "workspace_id": workspace["workspace_id"],
                        "expected_root_generation": workspace["root_generation"],
                        "expected_source_revision": workspace["source_revision"],
                        "expected_workflow_status_hash": workspace["workflow_status_hash"],
                        "phase_id": "p10_canon_lock",
                        "reason": "Compiled evidence must be reviewed again.",
                        "approved_by": "lead_reviewer",
                    },
                }
            )["result"]
            invalidated = service.handle(
                _request(
                    "creation_artifact.list",
                    {
                        **_authority_params(reopened["workspace"]),
                        "lifecycle": "invalidated",
                        "cursor": None,
                        "limit": 64,
                    },
                    request_id="invalidated_artifacts",
                )
            )["result"]
            self.assertGreater(invalidated["counts"]["invalidated"], 0)
            invalidated_formats = {item["subject"]["format"] for item in invalidated["artifacts"]}
            self.assertIn("world-forge.gamepack", invalidated_formats)
            self.assertIn("world-forge.assetpack", invalidated_formats)
            self.assertIn("world-forge.runtime_support_report", invalidated_formats)
            with self.assertRaisesRegex(StudioError, "no longer active"):
                service.handle(
                    _request(
                        "creation_artifact.inspect",
                        {
                            **_authority_params(reopened["workspace"]),
                            "expected_artifact_snapshot_hash": invalidated[
                                "artifact_snapshot_hash"
                            ],
                            "artifact_id": invalidated["artifacts"][0]["artifact_id"],
                        },
                        request_id="inspect_invalidated",
                    )
                )
            reopened_evidence = service.handle(
                _request(
                    "creation_evidence.inspect",
                    _authority_params(reopened["workspace"]),
                    request_id="reopened_evidence",
                )
            )["result"]["evidence"]
            self.assertEqual("not_requested", reopened_evidence["dimensions"]["compilation"])
            self.assertEqual("unplanned", reopened_evidence["dimensions"]["assets"])
            self.assertEqual("declared", reopened_evidence["dimensions"]["adapter"])
            service.close()

    def test_v4_initializes_with_evidence_and_creation_job_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, _root, _workspace = _registered_service(Path(temp))
            response = service.handle(_request("service.initialize", {}, request_id="init_v4"))

            self.assertEqual(4, response["result"]["protocol_version"])
            self.assertEqual(sorted(METHODS_V4), response["result"]["methods"])
            self.assertEqual(
                {
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
                },
                response["result"]["capabilities"],
            )
            service.close()

    def test_source_and_workflow_census_is_pathless_paginated_and_cas_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            params = {
                **_authority_params(workspace),
                "lifecycle": "active",
                "cursor": None,
                "limit": 2,
            }
            first = service.handle(
                _request("creation_artifact.list", params, request_id="artifacts_1")
            )["result"]
            self.assertEqual(2, len(first["artifacts"]))
            self.assertIsNotNone(first["next_cursor"])
            self.assertEqual(4, first["counts"]["active"])
            self.assertNotIn(str(root), json.dumps(first, sort_keys=True))
            self.assertNotIn("path", json.dumps(first, sort_keys=True).casefold())

            second = service.handle(
                _request(
                    "creation_artifact.list",
                    {
                        **params,
                        "expected_artifact_snapshot_hash": first["artifact_snapshot_hash"],
                        "cursor": first["next_cursor"],
                    },
                    request_id="artifacts_2",
                )
            )["result"]
            self.assertEqual(2, len(second["artifacts"]))
            self.assertIsNone(second["next_cursor"])
            self.assertEqual(first["artifact_snapshot_hash"], second["artifact_snapshot_hash"])

            with self.assertRaisesRegex(StudioError, "snapshot"):
                service.handle(
                    _request(
                        "creation_artifact.list",
                        {
                            **params,
                            "expected_artifact_snapshot_hash": _HASH_C,
                        },
                        request_id="stale_snapshot",
                    )
                )
            service.close()

    def test_evidence_is_blocked_and_materialization_remains_inspect_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, root, workspace = _registered_service(Path(temp))
            response = service.handle(
                _request(
                    "creation_evidence.inspect",
                    _authority_params(workspace),
                    request_id="evidence",
                )
            )["result"]
            evidence = response["evidence"]

            self.assertEqual("valid", evidence["dimensions"]["authoring"])
            self.assertEqual("blocked", evidence["dimensions"]["release"])
            self.assertFalse(evidence["materialization"]["enabled"])
            self.assertEqual("blocked", evidence["materialization"]["state"])
            self.assertEqual(4, evidence["artifact_counts"]["active"])
            serialized = json.dumps(response, sort_keys=True).casefold()
            self.assertNotIn(str(root).casefold(), serialized)
            for forbidden in ("provider_id", "prompt", "credentials", "command", "environment"):
                self.assertNotIn(forbidden, serialized)
            service.close()

    def test_v5_qa_report_projection_exposes_exact_criterion_hashes_without_changing_v4(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            service, _root, workspace = _prepared_puzzle_service(Path(temp))
            qa_documents = {
                document["content_hash"]: document
                for document in _puzzle_asset_graph()
                if document["format"] == "world-forge.asset_qa_report"
            }
            listing = None
            qa_artifact = None
            cursor = None
            snapshot_hash = None
            request_index = 0
            while qa_artifact is None:
                listing = service.handle(
                    _request(
                        "creation_artifact.list",
                        {
                            **_authority_params(workspace),
                            "expected_artifact_snapshot_hash": snapshot_hash,
                            "lifecycle": "active",
                            "cursor": cursor,
                            "limit": 64,
                        },
                        request_id=f"list_v5_qa_reports_{request_index}",
                    )
                )["result"]
                qa_artifact = next(
                    (
                        artifact
                        for artifact in listing["artifacts"]
                        if artifact["subject"]["format"] == "world-forge.asset_qa_report"
                    ),
                    None,
                )
                cursor = listing["next_cursor"]
                snapshot_hash = listing["artifact_snapshot_hash"]
                request_index += 1
                if cursor is None and qa_artifact is None:
                    self.fail("prepared puzzle service did not expose an active QA report")
            assert listing is not None
            qa_document = qa_documents[qa_artifact["subject"]["content_hash"]]
            expected_hashes = [
                criterion["criterion_sha256"] for criterion in qa_document["acceptance_criteria"]
            ]

            inspect_params = {
                **_authority_params(workspace),
                "expected_artifact_snapshot_hash": listing["artifact_snapshot_hash"],
                "artifact_id": qa_artifact["artifact_id"],
            }
            v4_projection = service.handle(
                _request(
                    "creation_artifact.inspect",
                    inspect_params,
                    request_id="inspect_v4_qa_report",
                )
            )["result"]["projection"]
            v5_projection = service.handle(
                {
                    **_request(
                        "creation_artifact.inspect",
                        inspect_params,
                        request_id="inspect_v5_qa_report",
                    ),
                    "protocol_version": 5,
                }
            )["result"]["projection"]

            self.assertEqual(
                ["asset_id", "output_count", "blocker_count"],
                [fact["key"] for fact in v4_projection["facts"]],
            )
            self.assertNotIn("criterion_hashes", json.dumps(v4_projection))
            self.assertEqual(
                expected_hashes,
                next(
                    fact["value"]
                    for fact in v5_projection["facts"]
                    if fact["key"] == "criterion_hashes"
                ),
            )
            self.assertEqual(len(expected_hashes), len(set(expected_hashes)))
            service.close()

    def test_v5_qa_criterion_projection_fails_closed_for_malformed_or_duplicate_hashes(
        self,
    ) -> None:
        qa_document = next(
            document
            for document in _puzzle_asset_graph()
            if document["format"] == "world-forge.asset_qa_report"
        )
        self.assertEqual(
            [criterion["criterion_sha256"] for criterion in qa_document["acceptance_criteria"]],
            _qa_criterion_hashes(qa_document),
        )

        missing = copy.deepcopy(qa_document)
        missing["acceptance_criteria"] = []
        with self.assertRaisesRegex(StudioError, "criterion hashes"):
            _qa_criterion_hashes(missing)

        malformed = copy.deepcopy(qa_document)
        malformed["acceptance_criteria"][0]["criterion_sha256"] = "0" * 63
        with self.assertRaisesRegex(StudioError, "criterion hashes"):
            _qa_criterion_hashes(malformed)

        duplicated = copy.deepcopy(qa_document)
        duplicated["acceptance_criteria"][1]["criterion_sha256"] = duplicated[
            "acceptance_criteria"
        ][0]["criterion_sha256"]
        with self.assertRaisesRegex(StudioError, "criterion hashes"):
            _qa_criterion_hashes(duplicated)


if __name__ == "__main__":
    unittest.main()
