from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import worldforge.lorepack as lorepack_module
from worldforge.creation_contracts import (
    MAX_CREATION_CONTRACT_BYTES,
    MAX_CREATION_JSON_DEPTH,
    LoadedCreationProject,
    canonical_creation_hash,
    load_creation_project,
    validate_creation_documents,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.lorepack import (
    LorepackError,
    build_lorepack,
    load_lorepack,
    serialize_lorepack,
    validate_lorepack,
    validate_lorepack_document,
)
from worldforge.phase_report_v2 import (
    PhaseReportV2Error,
    build_phase_output_evidence,
    build_phase_report_v2,
    load_phase_report_v2,
    validate_phase_report_v2,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "multigenre-contracts"
PUZZLE_PROJECT = FIXTURES / "abstract-puzzle" / "project.json"
NARRATIVE_PROJECT = FIXTURES / "branching-narrative" / "project.json"


def _reseal(document: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(document)
    value["content_hash"] = canonical_creation_hash(value)
    return value


def _phase_evidence(
    loaded: object,
    *,
    phase: str,
    role: str,
    subject: str,
) -> dict[str, object]:
    document = getattr(loaded, subject)
    return build_phase_output_evidence(
        evidence_id=f"{phase}_{role}",
        phase=phase,
        role=role,
        subject=document,
        reviewer_id="lead_reviewer",
        reviewer_role="validation_analyst",
    )


def _project_with_world_fact() -> object:
    loaded = load_creation_project(NARRATIVE_PROJECT)
    world_module = json.loads(
        (FIXTURES / "universe-library" / "source" / "world" / "canon.json").read_text(
            encoding="utf-8"
        )
    )
    world_module["project_id"] = loaded.project["project_id"]
    world_module["facts"][0]["sources"] = [
        "/mutable/authoring/source.json",
        "provider=remote model=v1 prompt=private credentials=none",
    ]
    world_module = _reseal(world_module)
    manifest = copy.deepcopy(loaded.manifest)
    manifest["modules"]["world_modules"] = [
        {
            "format": world_module["format"],
            "format_version": world_module["format_version"],
            "id": world_module["module_id"],
            "path": "world/canon.json",
            "content_hash": world_module["content_hash"],
        }
    ]
    manifest = _reseal(manifest)
    project = copy.deepcopy(loaded.project)
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project = _reseal(project)
    return validate_creation_documents(
        project,
        loaded.profile,
        manifest,
        (world_module,),
        loaded.activity_modules,
        loaded.narrative_modules,
        loaded.system_modules,
        loaded.logic_modules,
    )


def _rebind_dependency(
    dependent: dict[str, object],
    dependency: dict[str, object],
) -> dict[str, object]:
    value = copy.deepcopy(dependent)
    identity = {
        "format": dependency["format"],
        "format_version": dependency["format_version"],
        "id": dependency["lorepack_id"],
        "content_hash": dependency["content_hash"],
    }
    value["dependencies"][0] = identity
    for provenance in value["provenance"]:
        if provenance["kind"] == "dependency_lorepack":
            provenance["subject"] = copy.deepcopy(identity)
    return _reseal(value)


def _malformed_loaded_project(
    loaded: LoadedCreationProject,
    *,
    profile_mutation: object | None = None,
    narrative_mutation: object | None = None,
    reseal: bool = True,
) -> LoadedCreationProject:
    profile = copy.deepcopy(loaded.profile)
    manifest = copy.deepcopy(loaded.manifest)
    project = copy.deepcopy(loaded.project)
    narrative_modules = tuple(copy.deepcopy(item) for item in loaded.narrative_modules)
    if profile_mutation is not None:
        profile_mutation(profile)
        if reseal:
            profile = _reseal(profile)
            manifest["profile"]["content_hash"] = profile["content_hash"]
            project["profile"]["content_hash"] = profile["content_hash"]
    if narrative_mutation is not None:
        changed_modules = list(narrative_modules)
        narrative_mutation(changed_modules[0])
        if reseal:
            changed_modules[0] = _reseal(changed_modules[0])
            manifest["modules"]["narrative_modules"][0]["content_hash"] = changed_modules[0][
                "content_hash"
            ]
        narrative_modules = tuple(changed_modules)
    if reseal:
        manifest = _reseal(manifest)
        project["source_manifest"]["content_hash"] = manifest["content_hash"]
        project = _reseal(project)
    return LoadedCreationProject(
        project=project,
        profile=profile,
        manifest=manifest,
        world_modules=tuple(copy.deepcopy(item) for item in loaded.world_modules),
        activity_modules=tuple(copy.deepcopy(item) for item in loaded.activity_modules),
        narrative_modules=narrative_modules,
        system_modules=tuple(copy.deepcopy(item) for item in loaded.system_modules),
        logic_modules=tuple(copy.deepcopy(item) for item in loaded.logic_modules),
    )


class PhaseReportV2Tests(unittest.TestCase):
    def test_ready_reports_load_against_the_exact_generic_project(self) -> None:
        for phase in ("p00_brief", "p08_world_arcs"):
            with self.subTest(phase=phase):
                path = FIXTURES / "branching-narrative" / "phase-reports" / f"{phase}.json"
                report = load_phase_report_v2(path, project_path=NARRATIVE_PROJECT)

                self.assertEqual("world-forge.phase_report", report["format"])
                self.assertEqual(2, report["format_version"])
                self.assertEqual("ready", report["status"])
                self.assertEqual(phase, report["phase"])

    def test_no_world_project_accepts_only_proven_conditional_not_applicable_phases(
        self,
    ) -> None:
        loaded = load_creation_project(PUZZLE_PROJECT)
        for phase in (
            "p03_geography",
            "p04_timeline",
            "p05_societies",
            "p06_characters",
        ):
            with self.subTest(phase=phase):
                path = FIXTURES / "abstract-puzzle" / "phase-reports" / f"{phase}.json"
                report = load_phase_report_v2(path, project_path=PUZZLE_PROJECT)
                self.assertEqual("not_applicable", report["status"])

        ready = build_phase_report_v2(
            loaded,
            phase="p08_world_arcs",
            status="ready",
            rationale_code="phase_ready",
            rationale_message="The explicit no-narrative design was reviewed.",
            reviewer_id="lead_reviewer",
            reviewer_role="validation_analyst",
            output_evidence=_phase_evidence(
                loaded,
                phase="p08_world_arcs",
                role="narrative_architecture",
                subject="profile",
            ),
        )
        invalid = _reseal(
            {
                **ready,
                "status": "not_applicable",
                "rationale": {
                    "code": "narrative_absent",
                    "message": "Narrative is intentionally absent.",
                },
            }
        )
        with self.assertRaisesRegex(PhaseReportV2Error, "cannot be not_applicable"):
            validate_phase_report_v2(invalid, loaded)

        temporal_system = copy.deepcopy(loaded.system_modules[0])
        temporal_system["systems"][0]["system_type"] = "schedule"
        temporal_system = _reseal(temporal_system)
        manifest = copy.deepcopy(loaded.manifest)
        manifest["modules"]["system_modules"][0]["content_hash"] = temporal_system["content_hash"]
        manifest = _reseal(manifest)
        project = copy.deepcopy(loaded.project)
        project["source_manifest"]["content_hash"] = manifest["content_hash"]
        project = _reseal(project)
        temporal_project = validate_creation_documents(
            project,
            loaded.profile,
            manifest,
            loaded.world_modules,
            loaded.activity_modules,
            loaded.narrative_modules,
            (temporal_system,),
            loaded.logic_modules,
        )
        with self.assertRaisesRegex(PhaseReportV2Error, "profile does not prove"):
            build_phase_report_v2(
                temporal_project,
                phase="p04_timeline",
                status="not_applicable",
                rationale_code="chronology_absent",
                rationale_message="A schedule makes chronology applicable.",
                reviewer_id="lead_reviewer",
                reviewer_role="validation_analyst",
                output_evidence=None,
            )

    def test_not_applicable_requires_profile_proof_not_only_rationale(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        report = build_phase_report_v2(
            loaded,
            phase="p03_geography",
            status="ready",
            rationale_code="phase_ready",
            rationale_message="The abstract world treatment was reviewed.",
            reviewer_id="lead_reviewer",
            reviewer_role="validation_analyst",
            output_evidence=_phase_evidence(
                loaded,
                phase="p03_geography",
                role="world_topology",
                subject="profile",
            ),
        )
        report = _reseal(
            {
                **report,
                "status": "not_applicable",
                "rationale": {
                    "code": "world_absent",
                    "message": "A rationale cannot erase an abstract world.",
                },
            }
        )

        with self.assertRaisesRegex(PhaseReportV2Error, "profile does not prove"):
            validate_phase_report_v2(report, loaded)

    def test_report_hash_evidence_and_invalidation_dependencies_are_exact(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        report = build_phase_report_v2(
            loaded,
            phase="p00_brief",
            status="ready",
            rationale_code="phase_ready",
            rationale_message="The brief is complete.",
            reviewer_id="lead_reviewer",
            reviewer_role="validation_analyst",
            output_evidence=_phase_evidence(
                loaded,
                phase="p00_brief",
                role="brief_review",
                subject="project",
            ),
        )

        with self.subTest("content hash drift"):
            changed = copy.deepcopy(report)
            changed["rationale"]["message"] = "Changed without resealing."
            with self.assertRaisesRegex(PhaseReportV2Error, "content hash"):
                validate_phase_report_v2(changed, loaded)

        with self.subTest("evidence mismatch"):
            changed = copy.deepcopy(report)
            changed["evidence"][0]["subject"]["content_hash"] = "0" * 64
            changed = _reseal(changed)
            with self.assertRaisesRegex(PhaseReportV2Error, "unknown or mismatched subject"):
                validate_phase_report_v2(changed, loaded)

        with self.subTest("missing invalidation dependency"):
            changed = copy.deepcopy(report)
            changed["invalidation_dependencies"].pop()
            changed = _reseal(changed)
            with self.assertRaisesRegex(PhaseReportV2Error, "must cover"):
                validate_phase_report_v2(changed, loaded)

        with self.subTest("casefold evidence collision"):
            changed = copy.deepcopy(report)
            duplicate = copy.deepcopy(changed["evidence"][0])
            changed["evidence"].append(duplicate)
            changed = _reseal(changed)
            with self.assertRaisesRegex(PhaseReportV2Error, "NFC/casefold collision"):
                validate_phase_report_v2(changed, loaded)

        with self.subTest("unknown required extension"):
            changed = copy.deepcopy(report)
            changed["extensions"] = [
                {
                    "id": "example.required-phase-extension",
                    "version": 1,
                    "required": True,
                    "content_hash": "0" * 64,
                }
            ]
            changed = _reseal(changed)
            with self.assertRaisesRegex(PhaseReportV2Error, "unknown required extension"):
                validate_phase_report_v2(changed, loaded)

    def test_ready_requires_caller_supplied_phase_specific_output_evidence(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        build_arguments = {
            "phase": "p00_brief",
            "status": "ready",
            "rationale_code": "phase_ready",
            "rationale_message": "The brief is complete.",
            "reviewer_id": "lead_reviewer",
            "reviewer_role": "validation_analyst",
        }
        with self.assertRaisesRegex(PhaseReportV2Error, "caller-supplied output evidence"):
            build_phase_report_v2(loaded, **build_arguments)

        evidence = _phase_evidence(
            loaded,
            phase="p00_brief",
            role="brief_review",
            subject="project",
        )
        report = build_phase_report_v2(
            loaded,
            **build_arguments,
            output_evidence=evidence,
        )
        self.assertEqual(evidence, report["output_evidence"])

        with self.subTest("evidence hash drift"):
            changed = copy.deepcopy(report)
            changed["output_evidence"]["reviewer"]["role"] = "other_reviewer"
            changed["reviewer"]["role"] = "other_reviewer"
            changed = _reseal(changed)
            with self.assertRaisesRegex(PhaseReportV2Error, "output evidence content hash"):
                validate_phase_report_v2(changed, loaded)

        with self.subTest("wrong phase"):
            changed_evidence = _reseal({**evidence, "phase": "p01_genre_style"})
            with self.assertRaisesRegex(PhaseReportV2Error, "must match report phase"):
                build_phase_report_v2(
                    loaded,
                    **build_arguments,
                    output_evidence=changed_evidence,
                )

        with self.subTest("wrong role"):
            changed_evidence = _reseal({**evidence, "role": "experience_classification"})
            with self.assertRaisesRegex(PhaseReportV2Error, "role is unsupported"):
                build_phase_report_v2(
                    loaded,
                    **build_arguments,
                    output_evidence=changed_evidence,
                )

        with self.subTest("wrong subject format"):
            profile_evidence = _phase_evidence(
                loaded,
                phase="p01_genre_style",
                role="experience_classification",
                subject="profile",
            )
            changed_evidence = _reseal(
                {
                    **profile_evidence,
                    "phase": "p00_brief",
                    "role": "brief_review",
                }
            )
            with self.assertRaisesRegex(PhaseReportV2Error, "subject format is unsupported"):
                build_phase_report_v2(
                    loaded,
                    **build_arguments,
                    output_evidence=changed_evidence,
                )

        with self.subTest("future asset phases fail closed"):
            with self.assertRaisesRegex(PhaseReportV2Error, "phase is unsupported"):
                build_phase_output_evidence(
                    evidence_id="p11_asset_inventory",
                    phase="p11_art_audio",
                    role="asset_inventory",
                    subject=loaded.project,
                    reviewer_id="lead_reviewer",
                    reviewer_role="validation_analyst",
                )
            future_report = copy.deepcopy(report)
            future_report["phase"] = "p11_art_audio"
            future_report["output_evidence"]["phase"] = "p11_art_audio"
            future_report["output_evidence"]["role"] = "asset_inventory"
            future_report["output_evidence"] = _reseal(future_report["output_evidence"])
            future_report = _reseal(future_report)
            with self.assertRaisesRegex(PhaseReportV2Error, "unsupported phase"):
                validate_phase_report_v2(future_report, loaded)

    def test_logic_modules_are_phase_evidence_only_for_logic_authoring_phases(self) -> None:
        loaded = load_creation_project(PUZZLE_PROJECT)
        logic = loaded.logic_modules[0]
        for phase, role in (
            ("p02_world_laws", "interaction_ontology"),
            ("p07_systems", "systems_design"),
            ("p09_narrative_content", "typed_content"),
        ):
            with self.subTest(phase=phase):
                evidence = build_phase_output_evidence(
                    evidence_id=f"{phase}_logic",
                    phase=phase,
                    role=role,
                    subject=logic,
                    reviewer_id="lead_reviewer",
                    reviewer_role="validation_analyst",
                )
                report = build_phase_report_v2(
                    loaded,
                    phase=phase,
                    status="ready",
                    rationale_code="phase_ready",
                    rationale_message="The declarative logic boundary was reviewed.",
                    reviewer_id="lead_reviewer",
                    reviewer_role="validation_analyst",
                    output_evidence=evidence,
                )
                self.assertEqual(
                    "world-forge.logic_module",
                    report["output_evidence"]["subject"]["format"],
                )

        with self.assertRaisesRegex(
            PhaseReportV2Error,
            "subject format is unsupported for role chronology",
        ):
            build_phase_output_evidence(
                evidence_id="p04_logic",
                phase="p04_timeline",
                role="chronology",
                subject=logic,
                reviewer_id="lead_reviewer",
                reviewer_role="validation_analyst",
            )

        p00_evidence = build_phase_output_evidence(
            evidence_id="p00_brief_review",
            phase="p00_brief",
            role="brief_review",
            subject=loaded.project,
            reviewer_id="lead_reviewer",
            reviewer_role="validation_analyst",
        )
        p00_report = build_phase_report_v2(
            loaded,
            phase="p00_brief",
            status="ready",
            rationale_code="phase_ready",
            rationale_message="The brief was reviewed.",
            reviewer_id="lead_reviewer",
            reviewer_role="validation_analyst",
            output_evidence=p00_evidence,
        )
        p00_report["evidence"].append(
            {
                "claim": "Logic was cited outside its authoring phases.",
                "evidence_id": "logic_subject",
                "subject": {
                    "content_hash": logic["content_hash"],
                    "format": logic["format"],
                    "format_version": logic["format_version"],
                    "id": logic["module_id"],
                },
            }
        )
        p00_report["evidence"].sort(key=lambda item: item["evidence_id"].encode("utf-8"))
        p00_report = _reseal(p00_report)
        with self.assertRaisesRegex(PhaseReportV2Error, "logic module.*unsupported.*p00"):
            validate_phase_report_v2(p00_report, loaded)

    def test_phase_report_malformed_discriminators_raise_domain_errors(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        evidence = _phase_evidence(
            loaded,
            phase="p00_brief",
            role="brief_review",
            subject="project",
        )
        report = build_phase_report_v2(
            loaded,
            phase="p00_brief",
            status="ready",
            rationale_code="phase_ready",
            rationale_message="The brief is complete.",
            reviewer_id="lead_reviewer",
            reviewer_role="validation_analyst",
            output_evidence=evidence,
        )
        for field in ("status", "phase"):
            with self.subTest(field=field):
                changed = _reseal({**report, field: []})
                with self.assertRaises(PhaseReportV2Error):
                    validate_phase_report_v2(changed, loaded)

    def test_malformed_exact_source_projects_never_escape_domain_errors(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        evidence = _phase_evidence(
            loaded,
            phase="p00_brief",
            role="brief_review",
            subject="project",
        )
        report = build_phase_report_v2(
            loaded,
            phase="p00_brief",
            status="ready",
            rationale_code="phase_ready",
            rationale_message="The brief is complete.",
            reviewer_id="lead_reviewer",
            reviewer_role="validation_analyst",
            output_evidence=evidence,
        )
        lorepack = build_lorepack(loaded, lorepack_id="source_boundary_lore")
        mutations = (
            (
                "world presence array",
                lambda profile: profile["world"].__setitem__("presence", []),
                None,
                True,
            ),
            (
                "gameplay family object",
                lambda profile: profile["gameplay"].__setitem__("primary_family", {}),
                None,
                True,
            ),
            (
                "narrative requirement array",
                lambda profile: profile["narrative"].__setitem__("requirement", []),
                None,
                True,
            ),
            (
                "presentation mode array",
                lambda profile: profile["presentation"].__setitem__("mode", []),
                None,
                True,
            ),
            (
                "production mode array",
                lambda profile: profile["production"]["content_modes"].__setitem__("gameplay", []),
                None,
                True,
            ),
            (
                "runtime presentation array",
                lambda profile: profile["runtime_target"].__setitem__("presentation_mode", []),
                None,
                True,
            ),
            (
                "narrative unit discriminator array",
                None,
                lambda module: module["units"][0].__setitem__("unit_type", []),
                True,
            ),
            (
                "non-string nested object key",
                lambda profile: profile["world"].__setitem__(1, "invalid"),
                None,
                False,
            ),
        )
        malformed_projects: list[tuple[str, LoadedCreationProject]] = []
        for label, profile_mutation, narrative_mutation, reseal in mutations:
            malformed = _malformed_loaded_project(
                loaded,
                profile_mutation=profile_mutation,
                narrative_mutation=narrative_mutation,
                reseal=reseal,
            )
            malformed_projects.append((label, malformed))
            with self.subTest(label=label, boundary="phase"):
                with self.assertRaises(PhaseReportV2Error):
                    validate_phase_report_v2(report, malformed)
            with self.subTest(label=label, boundary="lorepack"):
                with self.assertRaises(LorepackError):
                    validate_lorepack(lorepack, source_project=malformed)

        malformed_collections = LoadedCreationProject(
            project=copy.deepcopy(loaded.project),
            profile=copy.deepcopy(loaded.profile),
            manifest=copy.deepcopy(loaded.manifest),
            world_modules=loaded.world_modules,
            activity_modules=loaded.activity_modules,
            narrative_modules=42,
            system_modules=loaded.system_modules,
            logic_modules=loaded.logic_modules,
        )
        with self.assertRaises(PhaseReportV2Error):
            validate_phase_report_v2(report, malformed_collections)
        with self.assertRaises(LorepackError):
            validate_lorepack(lorepack, source_project=malformed_collections)

        base = build_lorepack(loaded, lorepack_id="source_boundary_base")
        dependent = build_lorepack(
            loaded,
            lorepack_id="source_boundary_dependent",
            dependencies=(base,),
            dependency_sources={"source_boundary_base": loaded},
        )
        for label, malformed in malformed_projects:
            with self.subTest(label=label, boundary="dependency source"):
                with self.assertRaises(LorepackError):
                    validate_lorepack(
                        dependent,
                        source_project=loaded,
                        dependencies=(base,),
                        dependency_sources={"source_boundary_base": malformed},
                    )

        malformed_loader_project = malformed_projects[0][1]
        with tempfile.TemporaryDirectory() as temp:
            project_root = Path(temp) / "branching-narrative"
            shutil.copytree(FIXTURES / "branching-narrative", project_root)
            (project_root / "project.json").write_bytes(
                canonical_json_bytes(malformed_loader_project.project)
            )
            (project_root / "profile.json").write_bytes(
                canonical_json_bytes(malformed_loader_project.profile)
            )
            (project_root / "source" / "manifest.json").write_bytes(
                canonical_json_bytes(malformed_loader_project.manifest)
            )
            with self.assertRaises(PhaseReportV2Error):
                load_phase_report_v2(
                    project_root / "phase-reports" / "p00_brief.json",
                    project_path=project_root / "project.json",
                )
            with self.assertRaises(LorepackError):
                load_lorepack(
                    project_root / "artifacts" / "branching-narrative.lorepack.json",
                    project_path=project_root / "project.json",
                )

    def test_report_loader_keeps_the_strict_bounded_file_boundary(self) -> None:
        loaded = load_creation_project(PUZZLE_PROJECT)
        report = build_phase_report_v2(
            loaded,
            phase="p03_geography",
            status="not_applicable",
            rationale_code="world_absent",
            rationale_message="The profile explicitly declares no world.",
            reviewer_id="lead_reviewer",
            reviewer_role="validation_analyst",
            output_evidence=None,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "report.json"
            path.write_bytes(canonical_json_bytes(report))

            hardlink = root / "hardlink.json"
            try:
                os.link(path, hardlink)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaisesRegex(PhaseReportV2Error, "safe snapshot|standalone"):
                    load_phase_report_v2(hardlink, project_path=PUZZLE_PROJECT)
                hardlink.unlink()

            deep: object = "leaf"
            for _ in range(MAX_CREATION_JSON_DEPTH + 1):
                deep = {"child": deep}
            path.write_text(json.dumps(deep), encoding="utf-8")
            with self.assertRaisesRegex(PhaseReportV2Error, "JSON depth"):
                load_phase_report_v2(path, project_path=PUZZLE_PROJECT)

            path.write_bytes(b"{" + b" " * MAX_CREATION_CONTRACT_BYTES + b"}")
            with self.assertRaisesRegex(
                PhaseReportV2Error,
                "safe snapshot|byte limit|too large",
            ):
                load_phase_report_v2(path, project_path=PUZZLE_PROJECT)


class LorepackTests(unittest.TestCase):
    def test_branching_narrative_build_is_deterministic_and_matches_fixture(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        first = build_lorepack(loaded, lorepack_id="branching_narrative_lore")
        second = build_lorepack(loaded, lorepack_id="branching_narrative_lore")
        fixture_path = (
            FIXTURES / "branching-narrative" / "artifacts" / "branching-narrative.lorepack.json"
        )
        fixture = load_lorepack(fixture_path, project_path=NARRATIVE_PROJECT)

        self.assertEqual(first, second)
        self.assertEqual(first, fixture)
        self.assertEqual(serialize_lorepack(first), serialize_lorepack(second))
        self.assertEqual(serialize_lorepack(first), fixture_path.read_bytes())
        self.assertEqual([], first["world_projections"])
        self.assertEqual(1, len(first["narrative_projections"]))
        self.assertTrue(first["localization"]["references"])

    def test_no_world_no_narrative_project_cannot_emit_fake_lore(self) -> None:
        loaded = load_creation_project(PUZZLE_PROJECT)

        with self.assertRaisesRegex(LorepackError, "not applicable"):
            build_lorepack(loaded, lorepack_id="invented_lore")

    def test_lorepack_rejects_gameplay_runtime_authoring_and_executable_fields(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        lorepack = build_lorepack(loaded, lorepack_id="branching_narrative_lore")
        forbidden = {
            "world_modules": [],
            "narrative_modules": [],
            "activity_modules": [],
            "system_modules": [],
            "actions": [],
            "rules": [],
            "runtime_requirements": {},
            "script": "print('not allowed')",
            "prompt": "authoring prompt",
            "provider": {"name": "remote"},
            "credentials": {"token": "secret"},
            "source_path": "/mutable/source",
        }
        for field, value in forbidden.items():
            with self.subTest(field=field):
                changed = _reseal({**lorepack, field: value})
                with self.assertRaisesRegex(LorepackError, "unknown fields"):
                    validate_lorepack(changed, source_project=loaded)

    def test_lorepack_projects_only_non_executable_lore_semantics(self) -> None:
        narrative_project = load_creation_project(NARRATIVE_PROJECT)
        lorepack = build_lorepack(
            narrative_project,
            lorepack_id="branching_narrative_lore",
        )
        encoded = serialize_lorepack(lorepack)
        for forbidden in (
            b'"effect_ids"',
            b'"condition_ids"',
            b'"prerequisite_ids"',
            b'"asset_binding_ids"',
            b'"logic_modules"',
            b'"state_variables"',
            b'"actions"',
            b'"rules"',
            b"remember_left",
            b"remember_right",
            b'"provider"',
            b'"credentials"',
            b'"source_path"',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, encoded)

        world_project = _project_with_world_fact()
        world_lorepack = build_lorepack(
            world_project,
            lorepack_id="neutral_universe_lore",
        )
        self.assertEqual(1, len(world_lorepack["world_projections"]))
        self.assertNotIn("sources", world_lorepack["world_projections"][0]["records"][0])
        world_encoded = serialize_lorepack(world_lorepack)
        self.assertNotIn(b"/mutable/authoring/source.json", world_encoded)
        self.assertNotIn(b"provider=remote", world_encoded)

        for field, value in (
            ("provider", {"name": "remote"}),
            ("source_path", "/mutable/source"),
            ("effect_ids", ["remember_left"]),
            ("condition_ids", ["remember_left"]),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(lorepack)
                changed["narrative_projections"][0]["units"][0][field] = value
                changed["narrative_projections"][0] = _reseal(changed["narrative_projections"][0])
                changed = _reseal(changed)
                with self.assertRaisesRegex(LorepackError, "unknown fields"):
                    validate_lorepack(changed, source_project=narrative_project)

        changed_world = copy.deepcopy(world_lorepack)
        changed_world["world_projections"][0]["records"][0]["sources"] = [
            "mutable_authoring_source"
        ]
        changed_world["world_projections"][0] = _reseal(changed_world["world_projections"][0])
        changed_world = _reseal(changed_world)
        with self.assertRaisesRegex(LorepackError, "unknown fields"):
            validate_lorepack(changed_world, source_project=world_project)

    def test_lorepack_dependency_graph_is_closed_hash_bound_and_acyclic(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        base = build_lorepack(loaded, lorepack_id="base_lore")
        dependent = build_lorepack(
            loaded,
            lorepack_id="dependent_lore",
            dependencies=(base,),
            dependency_sources={"base_lore": loaded},
        )

        self.assertEqual(
            dependent,
            validate_lorepack(
                dependent,
                source_project=loaded,
                dependencies=(base,),
                dependency_sources={"base_lore": loaded},
            ),
        )

        with self.subTest("missing"):
            with self.assertRaisesRegex(LorepackError, "missing dependency"):
                validate_lorepack(dependent, source_project=loaded)

        with self.subTest("hash mismatch"):
            changed = copy.deepcopy(base)
            changed["localization"]["supported_locales"].append("fr")
            changed = _reseal(changed)
            with self.assertRaisesRegex(LorepackError, "dependency.*mismatch"):
                validate_lorepack(
                    dependent,
                    source_project=loaded,
                    dependencies=(changed,),
                    dependency_sources={"base_lore": loaded},
                )

        with self.subTest("cycle"):
            cyclic = copy.deepcopy(base)
            cyclic["dependencies"] = [
                {
                    "format": "world-forge.lorepack",
                    "format_version": 1,
                    "id": "base_lore",
                    "content_hash": base["content_hash"],
                }
            ]
            cyclic["provenance"].append(
                {
                    "provenance_id": "dependency_self",
                    "kind": "dependency_lorepack",
                    "subject": copy.deepcopy(cyclic["dependencies"][0]),
                }
            )
            cyclic["provenance"].sort(key=lambda item: item["provenance_id"].encode("utf-8"))
            cyclic = _reseal(cyclic)
            cyclic["dependencies"][0]["content_hash"] = cyclic["content_hash"]
            cyclic["provenance"][0]["subject"]["content_hash"] = cyclic["content_hash"]
            cyclic = _reseal(cyclic)
            with self.assertRaisesRegex(LorepackError, "cycle"):
                validate_lorepack(
                    cyclic,
                    source_project=loaded,
                )

        with self.subTest("casefold duplicate IDs"):
            duplicate = copy.deepcopy(base)
            duplicate["localization"]["supported_locales"].append("fr")
            duplicate = _reseal(duplicate)
            with self.assertRaisesRegex(LorepackError, "NFC/casefold collision"):
                validate_lorepack(
                    dependent,
                    source_project=loaded,
                    dependencies=(base, duplicate),
                    dependency_sources={"base_lore": loaded},
                )

    def test_dependency_bounds_and_resolvers_fail_before_expansion(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        base = build_lorepack(loaded, lorepack_id="bounded_base")
        dependent = build_lorepack(
            loaded,
            lorepack_id="bounded_dependent",
            dependencies=(base,),
            dependency_sources={"bounded_base": loaded},
        )
        malformed_dependent = copy.deepcopy(dependent)
        malformed_dependent["script"] = "must not mask dependency preflight"

        with self.subTest("dependency count"):
            with (
                mock.patch.object(
                    lorepack_module,
                    "validate_lorepack_document",
                    wraps=lorepack_module.validate_lorepack_document,
                ) as validate_document,
                mock.patch.object(
                    lorepack_module,
                    "_validated_source_project",
                    wraps=lorepack_module._validated_source_project,
                ) as validate_source,
            ):
                with self.assertRaisesRegex(LorepackError, "15-item limit"):
                    validate_lorepack(
                        dependent,
                        source_project=loaded,
                        dependencies=(base,) * 1000,
                        dependency_sources={"bounded_base": loaded},
                    )
                self.assertEqual(0, validate_document.call_count)
                self.assertEqual(0, validate_source.call_count)

        with self.subTest("builder dependency count"):
            with (
                mock.patch.object(
                    lorepack_module,
                    "validate_lorepack_document",
                    wraps=lorepack_module.validate_lorepack_document,
                ) as validate_document,
                mock.patch.object(
                    lorepack_module,
                    "_validated_source_project",
                    wraps=lorepack_module._validated_source_project,
                ) as validate_source,
            ):
                with self.assertRaisesRegex(LorepackError, "15-item limit"):
                    build_lorepack(
                        loaded,
                        lorepack_id="oversized_builder",
                        dependencies=(base,) * 1000,
                        dependency_sources={"bounded_base": loaded},
                    )
                self.assertEqual(0, validate_document.call_count)
                self.assertEqual(0, validate_source.call_count)

        with self.subTest("duplicate documents"):
            with (
                mock.patch.object(
                    lorepack_module,
                    "validate_lorepack_document",
                    wraps=lorepack_module.validate_lorepack_document,
                ) as validate_document,
                mock.patch.object(
                    lorepack_module,
                    "_validated_source_project",
                    wraps=lorepack_module._validated_source_project,
                ) as validate_source,
            ):
                with self.assertRaisesRegex(LorepackError, "NFC/casefold collision"):
                    validate_lorepack(
                        malformed_dependent,
                        source_project=loaded,
                        dependencies=(base, base),
                        dependency_sources={"bounded_base": loaded},
                    )
                self.assertEqual(0, validate_document.call_count)
                self.assertEqual(0, validate_source.call_count)

        with self.subTest("resolver count"):
            oversized_mapping = {f"extra_{index:04}": loaded for index in range(1000)}
            with (
                mock.patch.object(
                    lorepack_module,
                    "validate_lorepack_document",
                    wraps=lorepack_module.validate_lorepack_document,
                ) as validate_document,
                mock.patch.object(
                    lorepack_module,
                    "_validated_source_project",
                    wraps=lorepack_module._validated_source_project,
                ) as validate_source,
            ):
                with self.assertRaisesRegex(LorepackError, "15-item limit"):
                    validate_lorepack(
                        malformed_dependent,
                        source_project=loaded,
                        dependencies=(base,),
                        dependency_sources=oversized_mapping,
                    )
                self.assertEqual(0, validate_document.call_count)
                self.assertEqual(0, validate_source.call_count)

        with self.subTest("builder resolver count"):
            with (
                mock.patch.object(
                    lorepack_module,
                    "validate_lorepack_document",
                    wraps=lorepack_module.validate_lorepack_document,
                ) as validate_document,
                mock.patch.object(
                    lorepack_module,
                    "_validated_source_project",
                    wraps=lorepack_module._validated_source_project,
                ) as validate_source,
            ):
                with self.assertRaisesRegex(LorepackError, "15-item limit"):
                    build_lorepack(
                        loaded,
                        lorepack_id="oversized_builder_mapping",
                        dependencies=(base,),
                        dependency_sources=oversized_mapping,
                    )
                self.assertEqual(0, validate_document.call_count)
                self.assertEqual(0, validate_source.call_count)

        with self.subTest("resolver exactness"):
            with (
                mock.patch.object(
                    lorepack_module,
                    "validate_lorepack_document",
                    wraps=lorepack_module.validate_lorepack_document,
                ) as validate_document,
                mock.patch.object(
                    lorepack_module,
                    "_validated_source_project",
                    wraps=lorepack_module._validated_source_project,
                ) as validate_source,
            ):
                with self.assertRaisesRegex(LorepackError, "not exact"):
                    validate_lorepack(
                        malformed_dependent,
                        source_project=loaded,
                        dependencies=(base,),
                        dependency_sources={"wrong_dependency": loaded},
                    )
                self.assertEqual(0, validate_document.call_count)
                self.assertEqual(0, validate_source.call_count)

        with self.subTest("resolver canonical order"):
            base_a = build_lorepack(loaded, lorepack_id="bounded_base_a")
            base_b = build_lorepack(loaded, lorepack_id="bounded_base_b")
            two_dependencies = build_lorepack(
                loaded,
                lorepack_id="bounded_two_dependencies",
                dependencies=(base_a, base_b),
                dependency_sources={
                    "bounded_base_a": loaded,
                    "bounded_base_b": loaded,
                },
            )
            malformed_two_dependencies = copy.deepcopy(two_dependencies)
            malformed_two_dependencies["script"] = "must not mask resolver order"
            with (
                mock.patch.object(
                    lorepack_module,
                    "validate_lorepack_document",
                    wraps=lorepack_module.validate_lorepack_document,
                ) as validate_document,
                mock.patch.object(
                    lorepack_module,
                    "_validated_source_project",
                    wraps=lorepack_module._validated_source_project,
                ) as validate_source,
            ):
                with self.assertRaisesRegex(LorepackError, "canonical UTF-8 key order"):
                    validate_lorepack(
                        malformed_two_dependencies,
                        source_project=loaded,
                        dependencies=(base_a, base_b),
                        dependency_sources={
                            "bounded_base_b": loaded,
                            "bounded_base_a": loaded,
                        },
                    )
                self.assertEqual(0, validate_document.call_count)
                self.assertEqual(0, validate_source.call_count)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            root_path = root / "dependent.lorepack.json"
            dependency_path = root / "base.lorepack.json"
            root_path.write_bytes(serialize_lorepack(dependent))
            dependency_path.write_bytes(serialize_lorepack(base))

            with self.subTest("path count"):
                with (
                    mock.patch.object(lorepack_module, "read_creation_object") as read_object,
                    mock.patch.object(lorepack_module, "load_creation_project") as load_project,
                ):
                    with self.assertRaisesRegex(LorepackError, "15-item limit"):
                        load_lorepack(
                            root_path,
                            project_path=NARRATIVE_PROJECT,
                            dependency_paths=(dependency_path,) * 1000,
                        )
                    self.assertEqual(0, read_object.call_count)
                    self.assertEqual(0, load_project.call_count)

            with self.subTest("project path count"):
                oversized_paths = {f"extra_{index:04}": NARRATIVE_PROJECT for index in range(1000)}
                with (
                    mock.patch.object(lorepack_module, "read_creation_object") as read_object,
                    mock.patch.object(lorepack_module, "load_creation_project") as load_project,
                ):
                    with self.assertRaisesRegex(LorepackError, "15-item limit"):
                        load_lorepack(
                            root_path,
                            project_path=NARRATIVE_PROJECT,
                            dependency_project_paths=oversized_paths,
                        )
                    self.assertEqual(0, read_object.call_count)
                    self.assertEqual(0, load_project.call_count)

            with self.subTest("wrong project resolver"):
                with mock.patch.object(
                    lorepack_module,
                    "load_creation_project",
                    wraps=lorepack_module.load_creation_project,
                ) as load_project:
                    with self.assertRaisesRegex(LorepackError, "not exact"):
                        load_lorepack(
                            root_path,
                            project_path=NARRATIVE_PROJECT,
                            dependency_paths=(dependency_path,),
                            dependency_project_paths={"wrong_dependency": NARRATIVE_PROJECT},
                        )
                    self.assertEqual(0, load_project.call_count)

    def test_dependency_lorepacks_are_revalidated_against_their_own_source(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        base = build_lorepack(loaded, lorepack_id="base_lore")
        dependent = build_lorepack(
            loaded,
            lorepack_id="dependent_lore",
            dependencies=(base,),
            dependency_sources={"base_lore": loaded},
        )

        with self.subTest("source mapping is mandatory and exact"):
            with self.assertRaisesRegex(LorepackError, "dependency source mapping"):
                validate_lorepack(
                    dependent,
                    source_project=loaded,
                    dependencies=(base,),
                )

        with self.subTest("re-sealed missing unit"):
            changed = copy.deepcopy(base)
            projection = changed["narrative_projections"][0]
            projection["units"] = [
                {
                    "id": "central_choice",
                    "unit_type": "scene",
                    "title": "A visible choice",
                    "next_unit_ids": ["ending_left"],
                },
                projection["units"][1],
            ]
            projection = _reseal(projection)
            changed["narrative_projections"][0] = projection
            changed["localization"]["references"] = [
                reference
                for reference in changed["localization"]["references"]
                if reference["subject_id"] not in {"ending_right", "choose_left", "choose_right"}
            ]
            changed = _reseal(changed)
            rebound = _rebind_dependency(dependent, changed)
            with self.assertRaisesRegex(LorepackError, "does not match.*source"):
                validate_lorepack(
                    rebound,
                    source_project=loaded,
                    dependencies=(changed,),
                    dependency_sources={"base_lore": loaded},
                )

        with self.subTest("modified projection"):
            changed = copy.deepcopy(base)
            changed["narrative_projections"][0]["units"][0]["title"] = "Modified title"
            changed["narrative_projections"][0] = _reseal(changed["narrative_projections"][0])
            changed = _reseal(changed)
            rebound = _rebind_dependency(dependent, changed)
            with self.assertRaisesRegex(LorepackError, "does not match.*source"):
                validate_lorepack(
                    rebound,
                    source_project=loaded,
                    dependencies=(changed,),
                    dependency_sources={"base_lore": loaded},
                )

        with self.subTest("stale manifest provenance"):
            changed = copy.deepcopy(base)
            changed["source_manifest"]["content_hash"] = "0" * 64
            for provenance in changed["provenance"]:
                if provenance["subject"]["format"] == "world-forge.creation_source_manifest":
                    provenance["subject"]["content_hash"] = "0" * 64
            changed = _reseal(changed)
            rebound = _rebind_dependency(dependent, changed)
            with self.assertRaisesRegex(
                LorepackError,
                "source_manifest.*validated creation project",
            ):
                validate_lorepack(
                    rebound,
                    source_project=loaded,
                    dependencies=(changed,),
                    dependency_sources={"base_lore": loaded},
                )

    def test_lorepack_validates_module_identity_localization_and_provenance(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        lorepack = build_lorepack(loaded, lorepack_id="branching_narrative_lore")

        with self.subTest("module hash"):
            changed = copy.deepcopy(lorepack)
            changed["narrative_projections"][0]["content_hash"] = "0" * 64
            changed = _reseal(changed)
            with self.assertRaisesRegex(LorepackError, "content hash"):
                validate_lorepack(changed, source_project=loaded)

        with self.subTest("localization target"):
            changed = copy.deepcopy(lorepack)
            changed["localization"]["references"][0]["subject_id"] = "missing_subject"
            changed = _reseal(changed)
            with self.assertRaisesRegex(LorepackError, "localization.*target"):
                validate_lorepack(changed, source_project=loaded)

        with self.subTest("provenance subject"):
            changed = copy.deepcopy(lorepack)
            changed["provenance"][0]["subject"]["content_hash"] = "0" * 64
            changed = _reseal(changed)
            with self.assertRaisesRegex(LorepackError, "provenance.*subject"):
                validate_lorepack(changed, source_project=loaded)

        with self.subTest("duplicate subject under a different provenance ID"):
            changed = copy.deepcopy(lorepack)
            duplicate = copy.deepcopy(changed["provenance"][0])
            duplicate["provenance_id"] = "duplicate_subject"
            changed["provenance"].append(duplicate)
            changed["provenance"].sort(key=lambda item: item["provenance_id"].encode("utf-8"))
            changed = _reseal(changed)
            with self.assertRaisesRegex(LorepackError, "duplicate subject identity"):
                validate_lorepack(changed, source_project=loaded)

        with self.subTest("dependency kind cannot name a source contract"):
            changed = copy.deepcopy(lorepack)
            changed["provenance"][0]["kind"] = "dependency_lorepack"
            changed = _reseal(changed)
            with self.assertRaisesRegex(
                LorepackError,
                "dependency_lorepack requires a lorepack subject",
            ):
                validate_lorepack(changed, source_project=loaded)

        with self.subTest("source kind cannot name a lorepack"):
            dependency = build_lorepack(loaded, lorepack_id="dependency_lore")
            dependent = build_lorepack(
                loaded,
                lorepack_id="dependent_lore",
                dependencies=(dependency,),
                dependency_sources={"dependency_lore": loaded},
            )
            changed = copy.deepcopy(dependent)
            dependency_provenance = next(
                item for item in changed["provenance"] if item["kind"] == "dependency_lorepack"
            )
            dependency_provenance["kind"] = "source_contract"
            changed = _reseal(changed)
            with self.assertRaisesRegex(
                LorepackError,
                "source_contract cannot name a lorepack subject",
            ):
                validate_lorepack(
                    changed,
                    source_project=loaded,
                    dependencies=(dependency,),
                    dependency_sources={"dependency_lore": loaded},
                )

        with self.subTest("unknown required extension"):
            changed = copy.deepcopy(lorepack)
            changed["extensions"] = [
                {
                    "id": "example.required-lore-extension",
                    "version": 1,
                    "required": True,
                    "content_hash": "0" * 64,
                }
            ]
            changed = _reseal(changed)
            with self.assertRaisesRegex(LorepackError, "unknown required extension"):
                validate_lorepack(changed, source_project=loaded)

    def test_lorepack_malformed_discriminators_raise_domain_errors(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        lorepack = build_lorepack(loaded, lorepack_id="branching_narrative_lore")
        mutations = (
            ("localization subject kind", ("localization", "subject_kind")),
            ("localization field", ("localization", "field")),
            ("provenance kind", ("provenance", "kind")),
            ("provenance subject format", ("provenance", "format")),
        )
        for label, (section, field) in mutations:
            with self.subTest(label=label):
                changed = copy.deepcopy(lorepack)
                if section == "localization":
                    changed["localization"]["references"][0][field] = []
                elif field == "kind":
                    changed["provenance"][0]["kind"] = []
                else:
                    changed["provenance"][0]["subject"]["format"] = []
                changed = _reseal(changed)
                with self.assertRaises(LorepackError):
                    validate_lorepack(changed, source_project=loaded)

    def test_lorepack_document_and_loader_are_strict_and_bounded(self) -> None:
        loaded = load_creation_project(NARRATIVE_PROJECT)
        lorepack = build_lorepack(loaded, lorepack_id="branching_narrative_lore")
        self.assertEqual(lorepack, validate_lorepack_document(lorepack))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "lorepack.json"
            path.write_bytes(serialize_lorepack(lorepack))
            hardlink = root / "hardlink.json"
            try:
                os.link(path, hardlink)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaisesRegex(LorepackError, "safe snapshot|standalone"):
                    load_lorepack(hardlink, project_path=NARRATIVE_PROJECT)
                hardlink.unlink()

            path.write_bytes(b'{"format":"world-forge.lorepack","format":"duplicate"}\n')
            with self.assertRaisesRegex(LorepackError, "duplicate JSON object key"):
                load_lorepack(path, project_path=NARRATIVE_PROJECT)

            path.write_bytes(b"{" + b" " * MAX_CREATION_CONTRACT_BYTES + b"}")
            with self.assertRaisesRegex(
                LorepackError,
                "safe snapshot|byte limit|too large",
            ):
                load_lorepack(path, project_path=NARRATIVE_PROJECT)

    def test_catalog_schemas_and_generated_types_include_the_new_versions(self) -> None:
        catalog = json.loads((ROOT / "contracts" / "catalog.json").read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        generated = (
            ROOT / "apps" / "studio" / "src" / "generated" / "world-forge-contracts.d.ts"
        ).read_text(encoding="utf-8")

        self.assertEqual("world-forge.phase_report", entries["phase-report-v2"]["format"])
        self.assertEqual(2, entries["phase-report-v2"]["version"])
        self.assertEqual("world-forge.lorepack", entries["lorepack"]["format"])
        self.assertEqual(1, entries["lorepack"]["version"])
        self.assertIn("export type WorldForgePhaseReportV2", generated)
        self.assertIn("export type WorldForgeLorepackV1", generated)


if __name__ == "__main__":
    unittest.main()
