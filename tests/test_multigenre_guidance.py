from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from isoworld.content.loader import load_worldpack
from worldforge import creation_scaffold
from worldforge.__main__ import _resolve_generic_assetpack_cli_source, build_parser
from worldforge.asset_io import AssetContractError, resolve_artifact
from worldforge.compiler import compile_project
from worldforge.creation_contracts import (
    LoadedCreationProject,
    canonical_creation_hash,
    load_creation_project,
    validate_creation_documents,
)
from worldforge.creation_readiness import (
    CreationReadinessError,
    build_creation_handoff,
    build_creation_readiness,
    validate_creation_handoff,
)
from worldforge.creation_workflow import initial_creation_workflow_status, phase_catalog
from worldforge.generic_assetpack import build_generic_assetpack_manifest
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.phase_report_v3 import (
    _NOT_APPLICABLE_CODES,
    PhaseReportV3Error,
    build_phase_report_v3,
    document_identity,
    validate_phase_report_v3,
)

ROOT = Path(__file__).resolve().parents[1]
PUZZLE_ROOT = ROOT / "examples/multigenre-contracts/abstract-puzzle"
BRANCHING_ROOT = ROOT / "examples/multigenre-contracts/branching-narrative"
SYSTEMIC_ROOT = ROOT / "examples/multigenre-contracts/systemic-simulation"

GENERIC_PROMPTS = (
    "08_GENERIC_PROFILE_AND_MODULES.md",
    "09_GENERIC_PHASE_EVIDENCE.md",
    "10_GAMEPACK_AND_MECHANIC_LEDGER.md",
    "11_GENERIC_ASSET_PIPELINE.md",
    "12_RUNTIME_COMPATIBILITY_AND_READINESS.md",
)
GENERIC_SKILLS = (
    "create-creation-project",
    "author-creation-profile",
    "author-typed-modules",
    "manage-creation-phases",
    "compile-audit-gamepack",
    "derive-seal-generic-assets",
    "inspect-runtime-evidence",
    "prepare-creation-handoff",
    "materialize-generic-game",
)
REQUIRED_DOCS = (
    "docs/SUPPORT_MATRIX.md",
    "docs/MIGRATING_WORLD_PROJECT_V2_TO_V3.md",
    "docs/LEGACY_IDENTITY_ALLOWLIST.md",
    "docs/operations/IDENTITY_CUTOVER_AND_ROLLBACK.md",
    "docs/decisions/0023-authoring-contracts-and-runtime-adapters.md",
    "docs/decisions/0024-gamepack-and-worldpack-are-additive.md",
    "docs/decisions/0025-world-forge-identity-migration.md",
)
AUTHORITATIVE_GUIDANCE_DOCS = (
    "README.md",
    "SECURITY.md",
    "docs/ARCHITECTURE.md",
    "docs/M4_MULTIPLE_WORLD_PRODUCTION.md",
    "docs/MULTI_GENRE_ARCHITECTURE.md",
    "docs/SUPPORT_MATRIX.md",
    "docs/ASSET_PIPELINE.md",
    "docs/CONTENT_PIPELINE.md",
    "docs/decisions/0012-forge-studio-desktop-shell.md",
    "docs/decisions/0022-studio-external-artifact-jobs.md",
    "agents/README.md",
    "agents/ORCHESTRATION.md",
    "agents/WORLD_CREATION_PHASES.md",
    "agents/QUALITY_GATES.md",
    "apps/studio/README.md",
    "apps/studio/packaging/README.md",
)
FORBIDDEN_CURRENT_OVERCLAIMS = (
    "protocol v5 keeps exactly 17 transport methods",
    "protocol v5 has exactly the same 17 transport methods",
    "current authority surface is protocol v5 with the same 17 transport methods",
    "native windows server 2022 ci exercises clean apply, idempotence, lock recovery",
    "--native off permits unsupported hosts",
    "--native off passes on arm64",
    "arm64 can mint v1 headless/release evidence",
    "hosted evidence passed",
    "hosted x86 2x2 evidence is passed",
    "hosted x86 2x2 evidence is green",
    "app-id migration is complete",
    "app id migration is complete",
    "app-id migrated",
    "asset content mode determines runtime support",
    "runtime support intent determines asset applicability",
    "assets not_applicable means runtime not_applicable",
)


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _subparser_help(name: str) -> str:
    parser = build_parser()
    action = next(item for item in parser._actions if getattr(item, "choices", None) is not None)
    return action.choices[name].format_help()


def _squash(text: str) -> str:
    return " ".join(text.split())


def _project_with_group_module() -> LoadedCreationProject:
    source = load_creation_project(SYSTEMIC_ROOT / "project.json")
    group = {
        "format": "world-forge.world_module",
        "format_version": 1,
        "module_id": "simulation_groups",
        "module_type": "group",
        "project_id": "systemic_simulation",
        "title": "Simulation groups",
        "groups": [
            {
                "id": "operators",
                "name": "Operators",
                "group_type": "team",
            }
        ],
        "extensions": [],
        "content_hash": "",
    }
    group["content_hash"] = canonical_creation_hash(group)
    manifest = copy.deepcopy(source.manifest)
    manifest["modules"]["world_modules"] = [
        {
            "format": group["format"],
            "format_version": group["format_version"],
            "id": group["module_id"],
            "path": "world/groups.json",
            "content_hash": group["content_hash"],
        }
    ]
    manifest["content_hash"] = canonical_creation_hash(manifest)
    project = copy.deepcopy(source.project)
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project["content_hash"] = canonical_creation_hash(project)
    return validate_creation_documents(
        project,
        source.profile,
        manifest,
        (group,),
        source.activity_modules,
        source.narrative_modules,
        source.system_modules,
        source.logic_modules,
    )


def _puzzle_artifact_graph() -> tuple[dict[str, object], ...]:
    source = _resolve_generic_assetpack_cli_source(PUZZLE_ROOT / "assets/manifest.json")
    assetpack = build_generic_assetpack_manifest(**source)
    production_records: list[dict[str, object]] = []
    for record in source["asset_records"]:
        production_records.extend(
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
    runtime_documents = tuple(
        json.loads((ROOT / relative).read_text(encoding="utf-8"))
        for relative in (
            "examples/multigenre-contracts/runtime/snapshot.json",
            "examples/multigenre-contracts/runtime/registry.json",
            "examples/multigenre-contracts/abstract-puzzle/runtime/composition.json",
            "examples/multigenre-contracts/abstract-puzzle/runtime/support-report.json",
        )
    )
    return (
        source["gamepack"],
        source["subject"],
        source["target"],
        source["style"],
        source["inventory"],
        *production_records,
        source["manifest"],
        assetpack,
        *runtime_documents,
    )


class MultiGenreGuidanceTests(unittest.TestCase):
    def test_phase_catalog_matches_generic_guidance_and_scaffold(self) -> None:
        guidance = _read("agents/WORLD_CREATION_PHASES.md")
        block = guidance.split("<!-- phase-catalog:start -->", 1)[1].split(
            "<!-- phase-catalog:end -->", 1
        )[0]
        rows = []
        for line in block.splitlines():
            match = re.fullmatch(r"\| (P\d\d) \| `([^`]+)` \| ([^|]+) \|", line)
            if match:
                rows.append((match.group(2), match.group(3).strip()))
        expected = [(item["id"], item["title"]) for item in phase_catalog()]
        self.assertEqual(rows, expected)

        project, profile, manifest = creation_scaffold._project_documents(
            project_id="guidance_probe",
            title="Guidance Probe",
            default_locale="en",
            project_version="0.1.0",
        )
        files = creation_scaffold._file_payloads(
            project=project,
            profile=profile,
            manifest=manifest,
        )
        phases = json.loads(files[".worldforge/phases.json"])
        self.assertEqual(phases["phases"], phase_catalog())
        readme = files["README.md"].decode("utf-8")
        self.assertIn("neutral authoring library", readme)
        self.assertIn("not an executable game", readme)

        emitted_project = json.loads(files["project.json"])
        emitted_profile = json.loads(files["profile.json"])
        emitted_manifest = json.loads(files["source/manifest.json"])
        loaded = validate_creation_documents(
            emitted_project,
            emitted_profile,
            emitted_manifest,
            (),
            (),
            (),
            (),
            (),
        )
        self.assertEqual("universe_library", emitted_project["project_kind"])
        self.assertEqual(
            {
                "accepted_logic_formats": [],
                "asset_formats": [],
                "input_capabilities": [],
                "optional_features": [],
                "packaging_target": "none",
                "platforms": [],
                "presentation_mode": "text",
                "renderer": "none",
                "replay_expected": False,
                "requested_adapter": None,
                "required_features": [],
                "save_expected": False,
            },
            emitted_profile["runtime_target"],
        )
        readiness = build_creation_readiness(loaded)
        handoff = build_creation_handoff(
            loaded,
            status=initial_creation_workflow_status(loaded),
            readiness=readiness,
        )
        self.assertFalse(readiness["release_ready"])
        self.assertEqual("blocked", readiness["dimensions"]["release"])
        self.assertEqual("authoring_ready", handoff["handoff_status"])

    def test_documentation_rejects_known_multigenre_overclaims(self) -> None:
        protocol_v5 = json.loads(_read("schemas/studio-protocol-v5.schema.json"))
        protocol_v4 = json.loads(_read("schemas/studio-protocol-v4.schema.json"))
        method_schema_v5 = protocol_v5["$defs"]["initializeResult"]["properties"]["methods"]
        method_schema_v4 = protocol_v4["$defs"]["initializeResult"]["properties"]["methods"]
        self.assertEqual(18, method_schema_v5["minItems"])
        self.assertEqual(18, method_schema_v5["maxItems"])
        self.assertEqual(17, method_schema_v4["minItems"])
        self.assertEqual(17, method_schema_v4["maxItems"])

        release_status = json.loads(_read("docs/evidence/multigenre-release-status.json"))
        self.assertEqual("PENDING", release_status["hosted_evidence"])

        current_docs = "\n".join(_read(relative) for relative in AUTHORITATIVE_GUIDANCE_DOCS)
        current_semantics = _squash(current_docs)
        current_semantics_lower = current_semantics.casefold()
        for forbidden in FORBIDDEN_CURRENT_OVERCLAIMS:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, current_semantics_lower)
        self.assertIn("protocol v5 keeps exactly 18 transport methods", current_semantics_lower)
        self.assertIn("historical v3 create rejects `asset_content_mode`", current_semantics_lower)
        self.assertIn("v5 create accepts `asset_content_mode`", current_semantics_lower)
        self.assertIn(
            "asset content mode and runtime support intent are orthogonal", current_semantics
        )
        self.assertIn("unsupported required capabilities fail closed", current_semantics_lower)
        self.assertIn("raw qa cannot authorize", current_semantics_lower)
        self.assertIn("hosted x86 2x2 evidence remains **pending**", current_semantics_lower)
        self.assertIn("app-id migration remains future-gated", current_semantics_lower)
        self.assertIn("repository remote is not renamed", current_semantics_lower)
        self.assertNotIn("app-id migration is complete", current_semantics_lower)
        self.assertNotRegex(
            current_semantics,
            (
                r"native dispatch remains authorized only for the exact "
                r"linux x86_64 legacy 2\.5d adapter"
            ),
        )
        self.assertIn("generic raylib 2d puzzle", current_semantics_lower)
        self.assertIn("generic raylib 2d/text narrative", current_semantics_lower)

    def test_authoritative_guidance_keeps_evidence_boundaries_structured(self) -> None:
        release_status = json.loads(_read("docs/evidence/multigenre-release-status.json"))
        self.assertEqual(
            {
                ("ubuntu-24.04", "3.11"),
                ("ubuntu-24.04", "3.12"),
                ("windows-2022", "3.11"),
                ("windows-2022", "3.12"),
            },
            {(row["os"], row["python"]) for row in release_status["required_matrix"]},
        )
        self.assertEqual("PENDING", release_status["hosted_evidence"])

        protocol_v5 = json.loads(_read("schemas/studio-protocol-v5.schema.json"))
        protocol_v4 = json.loads(_read("schemas/studio-protocol-v4.schema.json"))
        self.assertEqual(
            18,
            protocol_v5["$defs"]["initializeResult"]["properties"]["methods"]["minItems"],
        )
        self.assertEqual(
            17,
            protocol_v4["$defs"]["initializeResult"]["properties"]["methods"]["minItems"],
        )

        guidance = {
            relative: _squash(_read(relative)).casefold()
            for relative in AUTHORITATIVE_GUIDANCE_DOCS
        }
        combined = "\n".join(guidance.values())
        for forbidden in FORBIDDEN_CURRENT_OVERCLAIMS:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)

        m4 = guidance["docs/M4_MULTIPLE_WORLD_PRODUCTION.md"]
        self.assertIn("previously green committed legacy baseline", m4)
        self.assertIn("new multi-genre/identity overlay", m4)
        self.assertIn("exact hosted windows/native rows remain pending until pushed", m4)

        security = guidance["SECURITY.md"]
        self.assertIn("raw qa cannot authorize execution", security)
        self.assertIn("unsupported required capabilities fail closed", security)
        self.assertIn("runtime ai", security)
        self.assertIn("process escape", security)

        support = guidance["docs/SUPPORT_MATRIX.md"]
        self.assertIn("local `--native off`", support)
        self.assertIn("supported headless host", support)
        self.assertIn("arm64 hosts can run lower-level logic/unit checks", support)
        self.assertIn("they cannot mint v1 headless/release evidence", support)

        asset = guidance["docs/ASSET_PIPELINE.md"]
        content = guidance["docs/CONTENT_PIPELINE.md"]
        agents = "\n".join(
            guidance[relative]
            for relative in (
                "agents/README.md",
                "agents/ORCHESTRATION.md",
                "agents/WORLD_CREATION_PHASES.md",
                "agents/QUALITY_GATES.md",
            )
        )
        for text in (asset, content, agents):
            self.assertIn("authoring validity is not runtime executability", text)
        self.assertIn("asset content mode and runtime support intent are orthogonal", combined)

    def test_not_applicable_codes_are_profile_bound_behavior_not_labels(self) -> None:
        central = _read("docs/MULTI_GENRE_ARCHITECTURE.md")
        block = central.split("<!-- not-applicable-codes:start -->", 1)[1].split(
            "<!-- not-applicable-codes:end -->", 1
        )[0]
        documented: dict[str, str] = {}
        for line in block.splitlines():
            match = re.fullmatch(r"\| `([^`]+)` \| `([^`]+)` \|", line)
            if match:
                documented[match.group(1)] = match.group(2)
        self.assertEqual(_NOT_APPLICABLE_CODES, documented)

        project, profile, manifest = creation_scaffold._project_documents(
            project_id="absence_probe",
            title="Absence Probe",
            default_locale="en",
            project_version="0.1.0",
        )
        absence_project = validate_creation_documents(
            project,
            profile,
            manifest,
            (),
            (),
            (),
            (),
            (),
        )
        branching = load_creation_project(BRANCHING_ROOT / "project.json")
        puzzle = load_creation_project(PUZZLE_ROOT / "project.json")
        wrong_profiles = {
            "p03_geography": branching,
            "p04_timeline": branching,
            "p05_societies": _project_with_group_module(),
            "p06_characters": branching,
            "p08_world_arcs": branching,
            "p11_art_audio": puzzle,
            "p12_asset_specs": puzzle,
            "p13_asset_production": puzzle,
        }

        def build_report(
            source_project: LoadedCreationProject,
            *,
            phase: str,
            rationale_code: str,
        ) -> dict[str, object]:
            return build_phase_report_v3(
                source_project,
                phase=phase,
                status="not_applicable",
                rationale_code=rationale_code,
                rationale_message="The profile proves this phase is irrelevant.",
                evidence=(
                    {
                        "evidence_id": "reviewed_profile",
                        "claim": "The exact profile was reviewed.",
                        "subject": document_identity(source_project.profile),
                    },
                ),
                output_evidence=None,
                reviewer_id="lead_reviewer",
                reviewer_role="validation_analyst",
                invalidation_dependencies=None,
            )

        for phase, rationale_code in _NOT_APPLICABLE_CODES.items():
            with self.subTest(phase=phase, behavior="matching_profile"):
                report = build_report(
                    absence_project,
                    phase=phase,
                    rationale_code=rationale_code,
                )
                self.assertEqual(
                    report,
                    validate_phase_report_v3(report, absence_project),
                )
            with self.subTest(phase=phase, behavior="wrong_code"):
                with self.assertRaisesRegex(PhaseReportV3Error, "requires rationale"):
                    build_report(
                        absence_project,
                        phase=phase,
                        rationale_code="wrong_absence_reason",
                    )
            with self.subTest(phase=phase, behavior="wrong_profile"):
                with self.assertRaisesRegex(PhaseReportV3Error, "does not prove"):
                    build_report(
                        wrong_profiles[phase],
                        phase=phase,
                        rationale_code=rationale_code,
                    )

    def test_generic_guidance_matches_fail_closed_readiness_behavior(self) -> None:
        central = _read("docs/MULTI_GENRE_ARCHITECTURE.md")
        central_semantics = _squash(central)
        self.assertIn("World presence and narrative are independently optional", central_semantics)
        self.assertIn(
            "must not invent geography, lore, actors, quests, or dialogue",
            central_semantics,
        )
        self.assertIn("Authoring validity is not runtime executability", central_semantics)
        self.assertIn("assets_not_applicable", central_semantics)
        self.assertIn("P13 is compatibility review, not execution proof", central_semantics)
        self.assertIn("P14 is a reviewed handoff, not a release claim", central_semantics)

        quality = _read("agents/QUALITY_GATES.md")
        runtime_prompt = _read("authoring/prompts/12_RUNTIME_COMPATIBILITY_AND_READINESS.md")
        for text in (quality, runtime_prompt):
            semantics = _squash(text)
            self.assertIn("blocks `implementation_ready`", semantics)
            self.assertIn(
                "does not block the required reviewed P14 `authoring_ready` handoff",
                semantics,
            )

        for relative in (
            "agents/ORCHESTRATION.md",
            "agents/QUALITY_GATES.md",
            "authoring/prompts/00_BOUNDARY.md",
            "authoring/prompts/07_GPT_WORLD_ORCHESTRATOR.md",
        ):
            text = _read(relative)
            self.assertIn("Authoring validity is not runtime executability", text, relative)

        generic_text = "\n".join(
            _read(relative)
            for relative in (
                "docs/MULTI_GENRE_ARCHITECTURE.md",
                "agents/README.md",
                "agents/ORCHESTRATION.md",
                "agents/QUALITY_GATES.md",
                "agents/WORLD_CREATION_PHASES.md",
                *(f"authoring/prompts/{name}" for name in GENERIC_PROMPTS),
                *(f".agents/skills/{name}/SKILL.md" for name in GENERIC_SKILLS),
            )
        ).casefold()
        self.assertNotRegex(
            generic_text,
            r"authoring[-_ ]ready\s+(?:means|is|equals|=)\s+(?:an?\s+)?executable",
        )
        self.assertNotRegex(
            generic_text,
            r"release_ready\s+(?:means|is|equals|=)\s+(?:fully\s+)?supported",
        )

        puzzle = load_creation_project(PUZZLE_ROOT / "project.json")
        artifacts = _puzzle_artifact_graph()
        support = next(
            document
            for document in artifacts
            if document["format"] == "world-forge.runtime_support_report"
        )
        self.assertEqual("partially_supported", support["compatibility_status"])
        readiness = build_creation_readiness(puzzle, artifacts=artifacts)
        status = initial_creation_workflow_status(puzzle)
        handoff = build_creation_handoff(
            puzzle,
            status=status,
            readiness=readiness,
            artifacts=artifacts,
        )
        self.assertEqual("valid", readiness["dimensions"]["authoring"])
        self.assertEqual("blocked", readiness["dimensions"]["release"])
        self.assertFalse(readiness["release_ready"])
        self.assertEqual("authoring_ready", handoff["handoff_status"])
        self.assertIn("runtime_adapter_not_verified", handoff["release_blockers"])

        forged = copy.deepcopy(handoff)
        forged["handoff_status"] = "implementation_ready"
        forged["content_hash"] = canonical_creation_hash(forged)
        with self.assertRaisesRegex(CreationReadinessError, "status is inconsistent"):
            validate_creation_handoff(
                forged,
                puzzle,
                status=status,
                readiness=readiness,
                artifacts=artifacts,
            )

    def test_asset_subject_and_support_evidence_terms_are_synchronized(self) -> None:
        central = _read("docs/MULTI_GENRE_ARCHITECTURE.md")
        generic_assets = _read("authoring/prompts/11_GENERIC_ASSET_PIPELINE.md")
        for token in ("`gamepack`", "`legacy_worldpack`"):
            self.assertIn(token, central)
            self.assertIn(token, generic_assets)
        self.assertIn(
            "the generic D1 derivation path requires `gamepack`",
            generic_assets,
        )

        support = _read("docs/SUPPORT_MATRIX.md")
        for evidence_level in (
            "Contract",
            "Local deterministic",
            "Native",
            "Hosted",
        ):
            self.assertIn(evidence_level, support)
        self.assertIn("Generic raylib 2D puzzle", support)
        self.assertIn("Generic raylib 2D/text narrative", support)
        self.assertIn("Playable generic 3D", support)
        self.assertIn("Windows world-project v2 to v3 migration", support)
        self.assertGreaterEqual(support.count("Pending"), 3)
        self.assertIn("Unsupported", support)

    def test_required_design_migration_prompt_and_skill_surfaces_exist(self) -> None:
        for relative in REQUIRED_DOCS:
            self.assertTrue((ROOT / relative).is_file(), relative)
        for name in GENERIC_PROMPTS:
            path = ROOT / "authoring" / "prompts" / name
            self.assertTrue(path.is_file(), str(path))
            self.assertIn("Authoring validity is not runtime executability", path.read_text())
        for name in GENERIC_SKILLS:
            path = ROOT / ".agents" / "skills" / name / "SKILL.md"
            self.assertTrue(path.is_file(), str(path))
            self.assertIn("Authoring validity is not runtime executability", path.read_text())

        existing = {path.parent.name for path in (ROOT / ".agents" / "skills").glob("*/SKILL.md")}
        legacy = sorted(existing - set(GENERIC_SKILLS))
        self.assertGreaterEqual(len(legacy), 30)
        for name in legacy:
            text = _read(f".agents/skills/{name}/SKILL.md")
            self.assertIn("Retained legacy specialization", text, name)

    def test_roles_and_cli_state_their_bounded_semantics(self) -> None:
        role_paths = sorted((ROOT / "agents" / "roles").glob("*.md"))
        self.assertGreaterEqual(len(role_paths), 6)
        for path in role_paths:
            text = path.read_text(encoding="utf-8")
            self.assertIn("## Applicability", text, path.name)
            self.assertRegex(text, r"(?i)(conditional|legacy specialization)", path.name)

        root_help = build_parser().format_help()
        self.assertIn("multi-genre", root_help)
        self.assertIn("legacy RPG", root_help)
        new_creation_help = _subparser_help("new-creation")
        self.assertIn("neutral authoring library", new_creation_help)
        self.assertIn("not an executable game", new_creation_help)
        compile_help = _subparser_help("compile-game")
        self.assertIn("does not certify runtime support", compile_help)
        inspect_help = _subparser_help("inspect-game-runtime")
        self.assertIn("does not certify release", inspect_help)
        reconcile_help = _subparser_help("reconcile-creation")
        self.assertIn("--expected-status-hash", reconcile_help)
        self.assertIn("before status, reopen, or completion", reconcile_help)

    def test_legacy_claims_are_backed_by_golden_readers_and_path_hardening(self) -> None:
        catalog = json.loads(_read("contracts/catalog.json"))
        published = {
            entry["format"]
            for entry in catalog["contracts"]
            if entry.get("format")
            in {
                "isoworld.worldpack",
                "isoworld.renderpack",
                "isoworld.save",
                "isoworld.replay",
                "rpg-world-forge.project",
                "rpg-world-forge.phase_report",
            }
        }
        self.assertEqual(6, len(published))
        central = _read("docs/MULTI_GENRE_ARCHITECTURE.md")
        adr = _read("docs/decisions/0024-gamepack-and-worldpack-are-additive.md")
        combined = central + "\n" + adr
        for discriminator in published:
            self.assertIn(f"`{discriminator}`", combined)
        self.assertNotIn("the `isoworld` runtime remain unchanged", adr)
        self.assertNotIn(
            "RPG compiler, worldpack readers, runtime, saves,\nreplays, and hashes are unchanged",
            central,
        )
        self.assertIn("canonical valid output bytes and content hashes", combined)
        self.assertIn("unsafe or ambiguous input representations", combined)

        golden_path = ROOT / "content/compiled/foundation.worldpack.json"
        golden_bytes = golden_path.read_bytes()
        source = json.loads(golden_bytes)
        with tempfile.TemporaryDirectory() as temp:
            temporary = Path(temp)
            compiled_path = temporary / "foundation.worldpack.json"
            compiled = compile_project(
                ROOT / "examples/foundation/source/manifest.json",
                compiled_path,
            )
            self.assertEqual(source["content_hash"], compiled["content_hash"])
            self.assertEqual(golden_bytes, compiled_path.read_bytes())

            for version in range(1, 6):
                with self.subTest(version=version):
                    payload = copy.deepcopy(source)
                    payload["format_version"] = version
                    if version < 5:
                        payload.pop("runtime_requirements", None)
                        payload["world"].pop("default_locale", None)
                        payload["world"].pop("supported_locales", None)
                    payload["content_hash"] = canonical_payload_hash(payload)
                    path = temporary / f"worldpack-v{version}.json"
                    path.write_bytes(canonical_json_bytes(payload))
                    loaded = load_worldpack(path)
                    self.assertEqual(version, loaded.format_version)
                    self.assertEqual(payload["content_hash"], loaded.content_hash)

            artifact_root = temporary / "artifacts"
            artifact_root.mkdir()
            outside = temporary / "outside.worldpack.json"
            outside.write_bytes(golden_bytes)
            with self.assertRaisesRegex(AssetContractError, "Unsafe artifact path"):
                resolve_artifact(artifact_root, "../outside.worldpack.json")
            self.assertEqual(golden_bytes, outside.read_bytes())

    def test_navigation_exposes_generic_and_legacy_lanes(self) -> None:
        for relative in (
            "README.md",
            "docs/ARCHITECTURE.md",
            "docs/CONTENT_PIPELINE.md",
            "docs/GAME_IMPLEMENTATION_PHASES.md",
        ):
            text = _read(relative)
            self.assertIn("Generic creation lane", text, relative)
            self.assertIn("Legacy RPG lane", text, relative)
            self.assertIn("docs/MULTI_GENRE_ARCHITECTURE.md", text, relative)
            self.assertIn("docs/SUPPORT_MATRIX.md", text, relative)


if __name__ == "__main__":
    unittest.main()
