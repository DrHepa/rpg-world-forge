from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import worldforge.gamepack as gamepack_module
from worldforge.__main__ import main
from worldforge.creation_contracts import (
    CreationContractError,
    LoadedCreationProject,
    load_creation_project,
)
from worldforge.gamepack import (
    CAPABILITY_LEDGER_SCHEMA_MAXIMA,
    GAMEPACK_SCHEMA_MAXIMA,
    CapabilityEvidenceSource,
    GamepackError,
    RegisteredGameExtension,
    RegisteredRuntimeAdapter,
    build_authoring_capability_ledger,
    build_gamepack,
    compile_game_project,
    load_gamepack,
    resolve_capability_ledger,
    serialize_capability_ledger,
    serialize_gamepack,
    validate_capability_ledger_document,
    validate_gamepack,
    validate_gamepack_document,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"
PUZZLE = EXAMPLES / "abstract-puzzle" / "project.json"
NARRATIVE = EXAMPLES / "branching-narrative" / "project.json"


def _malformed_loaded(
    loaded: LoadedCreationProject,
    *,
    logic_modules: tuple[dict[str, object], ...] | None = None,
    narrative_modules: tuple[dict[str, object], ...] | None = None,
) -> LoadedCreationProject:
    return LoadedCreationProject(
        project=copy.deepcopy(loaded.project),
        profile=copy.deepcopy(loaded.profile),
        manifest=copy.deepcopy(loaded.manifest),
        world_modules=tuple(copy.deepcopy(item) for item in loaded.world_modules),
        activity_modules=tuple(copy.deepcopy(item) for item in loaded.activity_modules),
        narrative_modules=tuple(
            copy.deepcopy(item)
            for item in (
                loaded.narrative_modules if narrative_modules is None else narrative_modules
            )
        ),
        system_modules=tuple(copy.deepcopy(item) for item in loaded.system_modules),
        logic_modules=tuple(
            copy.deepcopy(item)
            for item in (loaded.logic_modules if logic_modules is None else logic_modules)
        ),
    )


def _with_requested_adapter(
    loaded: LoadedCreationProject,
    requested_adapter: str | None,
) -> LoadedCreationProject:
    profile = copy.deepcopy(loaded.profile)
    profile["runtime_target"]["requested_adapter"] = requested_adapter
    profile["content_hash"] = gamepack_module._canonical_hash(profile)
    manifest = copy.deepcopy(loaded.manifest)
    manifest["profile"]["content_hash"] = profile["content_hash"]
    manifest["content_hash"] = gamepack_module._canonical_hash(manifest)
    project = copy.deepcopy(loaded.project)
    project["profile"]["content_hash"] = profile["content_hash"]
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project["content_hash"] = gamepack_module._canonical_hash(project)
    return LoadedCreationProject(
        project=project,
        profile=profile,
        manifest=manifest,
        world_modules=loaded.world_modules,
        activity_modules=loaded.activity_modules,
        narrative_modules=loaded.narrative_modules,
        system_modules=loaded.system_modules,
        logic_modules=loaded.logic_modules,
    )


def _with_narrative_module(
    loaded: LoadedCreationProject,
    narrative_module: dict[str, object],
) -> LoadedCreationProject:
    narrative_module["content_hash"] = gamepack_module._canonical_hash(narrative_module)
    manifest = copy.deepcopy(loaded.manifest)
    manifest["modules"]["narrative_modules"][0]["content_hash"] = narrative_module["content_hash"]
    manifest["content_hash"] = gamepack_module._canonical_hash(manifest)
    project = copy.deepcopy(loaded.project)
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project["content_hash"] = gamepack_module._canonical_hash(project)
    return LoadedCreationProject(
        project=project,
        profile=loaded.profile,
        manifest=manifest,
        world_modules=loaded.world_modules,
        activity_modules=loaded.activity_modules,
        narrative_modules=(narrative_module,),
        system_modules=loaded.system_modules,
        logic_modules=loaded.logic_modules,
    )


def _with_authored_narrative(
    loaded: LoadedCreationProject,
    *,
    topology: str,
    required_feature_id: str,
    unit_type: str,
) -> LoadedCreationProject:
    module: dict[str, object] = {
        "format": "world-forge.narrative_module",
        "format_version": 1,
        "module_id": f"authored_{unit_type}_narrative",
        "project_id": loaded.project["project_id"],
        "title": f"Authored {unit_type} narrative",
        "entry_unit_ids": [f"opening_{unit_type}"],
        "units": [
            {
                "id": "authored_ending",
                "unit_type": "ending",
                "title": "Authored ending",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": [],
                "asset_binding_ids": [],
                "ending_kind": "neutral",
            },
            {
                "id": f"opening_{unit_type}",
                "unit_type": unit_type,
                "title": f"Opening {unit_type}",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": ["authored_ending"],
                "asset_binding_ids": [],
            },
        ],
        "extensions": [],
    }
    module["content_hash"] = gamepack_module._canonical_hash(module)

    profile = copy.deepcopy(loaded.profile)
    profile["narrative"] = {
        "requirement": "required",
        "authorship_mode": "authored",
        "topology": topology,
        "delivery_channels": ["narrative:prose"],
        "protagonist_model": "none",
        "agency": "none",
        "focalization": "external",
        "canon_variability": "none",
        "pacing": "authored",
        "endings": "one authored ending",
        "information_model": "authored sequence",
    }
    profile["runtime_target"]["required_features"] = sorted(
        [*profile["runtime_target"]["required_features"], required_feature_id],
        key=lambda item: item.encode("utf-8"),
    )
    profile["content_hash"] = gamepack_module._canonical_hash(profile)

    logic = copy.deepcopy(loaded.logic_modules[0])
    action = next(item for item in logic["actions"] if item["id"] == "swap_tiles")
    action["required_feature_ids"] = sorted(
        [*action["required_feature_ids"], required_feature_id],
        key=lambda item: item.encode("utf-8"),
    )
    mechanic = next(item for item in logic["mechanics"] if item["id"] == "swap_mechanic")
    mechanic["required_feature_ids"] = copy.deepcopy(action["required_feature_ids"])
    logic["content_hash"] = gamepack_module._canonical_hash(logic)

    manifest = copy.deepcopy(loaded.manifest)
    manifest["profile"]["content_hash"] = profile["content_hash"]
    manifest["modules"]["logic_modules"][0]["content_hash"] = logic["content_hash"]
    manifest["modules"]["narrative_modules"] = [
        {
            "format": module["format"],
            "format_version": module["format_version"],
            "id": module["module_id"],
            "path": f"narrative/{unit_type}.json",
            "content_hash": module["content_hash"],
        }
    ]
    manifest["content_hash"] = gamepack_module._canonical_hash(manifest)

    project = copy.deepcopy(loaded.project)
    project["profile"]["content_hash"] = profile["content_hash"]
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project["content_hash"] = gamepack_module._canonical_hash(project)
    return LoadedCreationProject(
        project=project,
        profile=profile,
        manifest=manifest,
        world_modules=loaded.world_modules,
        activity_modules=loaded.activity_modules,
        narrative_modules=(module,),
        system_modules=loaded.system_modules,
        logic_modules=(logic,),
    )


def _with_project_extensions(
    loaded: LoadedCreationProject,
    extensions: list[dict[str, object]],
) -> LoadedCreationProject:
    project = copy.deepcopy(loaded.project)
    project["extensions"] = copy.deepcopy(extensions)
    project["content_hash"] = gamepack_module._canonical_hash(project)
    return LoadedCreationProject(
        project=project,
        profile=loaded.profile,
        manifest=loaded.manifest,
        world_modules=loaded.world_modules,
        activity_modules=loaded.activity_modules,
        narrative_modules=loaded.narrative_modules,
        system_modules=loaded.system_modules,
        logic_modules=loaded.logic_modules,
    )


def _with_profile_extensions(
    loaded: LoadedCreationProject,
    *,
    project_extensions: list[dict[str, object]],
    profile_extensions: list[dict[str, object]],
) -> LoadedCreationProject:
    profile = copy.deepcopy(loaded.profile)
    profile["extensions"] = copy.deepcopy(profile_extensions)
    profile["content_hash"] = gamepack_module._canonical_hash(profile)
    manifest = copy.deepcopy(loaded.manifest)
    manifest["profile"]["content_hash"] = profile["content_hash"]
    manifest["content_hash"] = gamepack_module._canonical_hash(manifest)
    project = copy.deepcopy(loaded.project)
    project["extensions"] = copy.deepcopy(project_extensions)
    project["profile"]["content_hash"] = profile["content_hash"]
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project["content_hash"] = gamepack_module._canonical_hash(project)
    return LoadedCreationProject(
        project=project,
        profile=profile,
        manifest=manifest,
        world_modules=loaded.world_modules,
        activity_modules=loaded.activity_modules,
        narrative_modules=loaded.narrative_modules,
        system_modules=loaded.system_modules,
        logic_modules=loaded.logic_modules,
    )


def _verified_ledger(
    gamepack: dict[str, object],
) -> tuple[
    dict[str, object],
    RegisteredRuntimeAdapter,
    dict[str, CapabilityEvidenceSource],
]:
    ledger = build_authoring_capability_ledger(gamepack)
    adapter_id = str(gamepack["runtime_requirements"]["requested_adapter"])
    adapter_version = "1.0.0"
    ledger["adapter"] = {
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "status": "verified",
    }
    evidence_sources: dict[str, CapabilityEvidenceSource] = {}
    for collection_name in ("mechanics", "features"):
        for index, entry in enumerate(ledger[collection_name]):
            test_id = f"{collection_name[:-1]}_{index}_test"
            native_id = f"{collection_name[:-1]}_{index}_native"
            test_payload = f"{test_id}:reviewed".encode()
            native_payload = f"{native_id}:reviewed".encode()
            entry.update(
                {
                    "status": "supported_current",
                    "reason_code": "adapter_verified",
                    "missing_feature_ids": [],
                    "extension": None,
                    "test_evidence": [
                        {
                            "evidence_id": test_id,
                            "content_hash": hashlib.sha256(test_payload).hexdigest(),
                        }
                    ],
                    "native_evidence": [
                        {
                            "evidence_id": native_id,
                            "content_hash": hashlib.sha256(native_payload).hexdigest(),
                        }
                    ],
                }
            )
            evidence_sources[test_id] = CapabilityEvidenceSource(
                evidence_id=test_id,
                category="test",
                payload=test_payload,
            )
            evidence_sources[native_id] = CapabilityEvidenceSource(
                evidence_id=native_id,
                category="native",
                payload=native_payload,
            )
    ledger["content_hash"] = gamepack_module._canonical_hash(ledger)
    runtime = gamepack["runtime_requirements"]
    adapter = RegisteredRuntimeAdapter(
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        accepted_logic_formats=tuple(
            (item["format"], tuple(item["versions"])) for item in runtime["accepted_logic_formats"]
        ),
        platform_matrix=tuple(
            (
                item["platform_id"],
                item["platform_family"],
                item["architecture"],
                item["backend"],
                item["renderer"],
            )
            for item in runtime["platform_matrix"]
        ),
        supported_features=frozenset(runtime["required_features"]),
        supported_mechanics=frozenset(
            item["mechanic_id"] for item in gamepack["mechanic_requirements"]
        ),
    )
    return ledger, adapter, evidence_sources


class GamepackCompilerTests(unittest.TestCase):
    def test_request_local_validation_memo_rejects_same_hash_tampering(self) -> None:
        import worldforge.gamepack as gamepack_module
        from worldforge.validation_memo import validation_memo_scope

        gamepack = build_gamepack(load_creation_project(PUZZLE))
        tampered = copy.deepcopy(gamepack)
        tampered["game"]["title"] = "Tampered without resealing"
        with (
            mock.patch.object(
                gamepack_module,
                "_validate_gamepack_document_uncached",
                wraps=gamepack_module._validate_gamepack_document_uncached,
            ) as validate,
            validation_memo_scope(),
        ):
            self.assertEqual(gamepack, validate_gamepack_document(gamepack))
            self.assertEqual(gamepack, validate_gamepack_document(gamepack))
            with self.assertRaisesRegex(GamepackError, "content_hash_mismatch"):
                validate_gamepack_document(tampered)
            self.assertEqual(gamepack, validate_gamepack_document(gamepack))

        self.assertEqual(2, validate.call_count)

    def test_validation_memo_never_crosses_request_scope(self) -> None:
        import worldforge.gamepack as gamepack_module
        from worldforge.validation_memo import validation_memo_scope

        gamepack = build_gamepack(load_creation_project(PUZZLE))
        with mock.patch.object(
            gamepack_module,
            "_validate_gamepack_document_uncached",
            wraps=gamepack_module._validate_gamepack_document_uncached,
        ) as validate:
            with validation_memo_scope():
                validate_gamepack_document(gamepack)
                validate_gamepack_document(gamepack)
            with validation_memo_scope():
                validate_gamepack_document(gamepack)
                validate_gamepack_document(gamepack)

        self.assertEqual(2, validate.call_count)

    def test_validation_memo_returns_defensive_copies(self) -> None:
        from worldforge.validation_memo import validation_memo_scope

        gamepack = build_gamepack(load_creation_project(PUZZLE))
        with (
            mock.patch.object(
                gamepack_module,
                "_validate_gamepack_document_uncached",
                wraps=gamepack_module._validate_gamepack_document_uncached,
            ) as validate,
            validation_memo_scope(),
        ):
            first = validate_gamepack_document(gamepack)
            first["game"]["title"] = "Attempted cache poisoning"
            second = validate_gamepack_document(gamepack)

        self.assertEqual(gamepack, second)
        self.assertNotEqual(first, second)
        self.assertEqual(1, validate.call_count)

    def test_validation_memo_bound_fails_safe_without_unbounded_retention(self) -> None:
        from worldforge.validation_memo import (
            memoize_document_validation,
            validation_memo_scope,
        )

        documents = [
            {
                "format": "world-forge.memo_probe",
                "format_version": 1,
                "content_hash": hashlib.sha256(f"memo:{index}".encode()).hexdigest(),
                "value": index,
            }
            for index in range(257)
        ]
        validator = mock.Mock(side_effect=copy.deepcopy)
        with validation_memo_scope():
            first = [
                memoize_document_validation("memo_probe", document, validator)
                for document in documents
            ]
            second = [
                memoize_document_validation("memo_probe", document, validator)
                for document in documents
            ]

        self.assertEqual(documents, first)
        self.assertEqual(documents, second)
        self.assertEqual(258, validator.call_count)

    def test_abstract_puzzle_compiles_without_rpg_world_or_narrative_invention(self) -> None:
        loaded = load_creation_project(PUZZLE)

        gamepack = build_gamepack(loaded)

        self.assertEqual("world-forge.gamepack", gamepack["format"])
        self.assertEqual(1, gamepack["format_version"])
        self.assertEqual("abstract_puzzle", gamepack["game"]["id"])
        self.assertEqual(
            [
                {
                    "id": "example.optional-metadata",
                    "version": 1,
                    "required": False,
                    "content_hash": (
                        "8a2e5f8ceb357542a050a8bdaafb1d3eb4deed2fd384815f2b6e392b2688880a"
                    ),
                }
            ],
            gamepack["registered_extensions"],
        )
        self.assertEqual([], gamepack["modules"]["world"])
        self.assertEqual([], gamepack["modules"]["narrative"])
        self.assertIsNone(gamepack["logic"]["narrative_cursor"])
        self.assertEqual([], gamepack["logic"]["narrative_transitions"])
        self.assertFalse(
            any(
                str(state["id"]).startswith("wf_internal_")
                for state in gamepack["logic"]["state_schema"]
            )
        )
        self.assertEqual(
            {"board", "move_count", "target"},
            set(gamepack["logic"]["initial_state"]),
        )
        self.assertEqual(
            {"board_texture"},
            {item["binding_id"] for item in gamepack["asset_requirements"]},
        )
        serialized = serialize_gamepack(gamepack).decode("utf-8").casefold()
        for forbidden in ("actor", "protagonist", "lore", "inventory", "npc"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn(
            "quest",
            {
                activity["activity_type"]
                for module in gamepack["modules"]["activities"]
                for activity in module["activities"]
            },
        )

    def test_branching_narrative_compiles_exact_cursor_and_option_transitions(self) -> None:
        loaded = load_creation_project(NARRATIVE)

        gamepack = build_gamepack(loaded)

        cursor = gamepack["logic"]["narrative_cursor"]
        self.assertEqual("wf_internal_narrative_cursor", cursor["id"])
        self.assertEqual("central_choice", cursor["initial"])
        self.assertEqual(
            ["central_choice", "ending_left", "ending_right"],
            cursor["allowed_values"],
        )
        self.assertEqual(
            {
                (
                    item["action_id"],
                    item["source_unit_id"],
                    item["option_id"],
                    item["target_unit_id"],
                )
                for item in gamepack["logic"]["narrative_transitions"]
            },
            {
                ("choose_left", "central_choice", "choose_left", "ending_left"),
                ("choose_right", "central_choice", "choose_right", "ending_right"),
            },
        )
        for transition in gamepack["logic"]["narrative_transitions"]:
            self.assertEqual(
                "reject_transition",
                transition["effect"]["invalid_transition_policy"],
            )
            self.assertEqual(
                transition["source_unit_id"],
                transition["precondition"]["value"],
            )
            self.assertEqual(
                transition["target_unit_id"],
                transition["effect"]["value"],
            )
            self.assertTrue(transition["atomic_source_condition_ids"])
            self.assertTrue(transition["atomic_source_effect_ids"])
        self.assertEqual(
            {"ending_left", "ending_right"},
            {item["id"] for item in gamepack["logic"]["endings"]},
        )
        self.assertEqual(
            {"choice_panel", "ending_panel"},
            {item["binding_id"] for item in gamepack["asset_requirements"]},
        )

    def test_nonbranching_authored_narrative_is_preserved_as_declarative_projection(
        self,
    ) -> None:
        puzzle = load_creation_project(PUZZLE)
        cases = (
            ("linear", "action:realtime_combat", "scene"),
            ("storylet", "roguelite:run_reset", "storylet"),
        )
        for topology, required_feature_id, unit_type in cases:
            with self.subTest(topology=topology):
                gamepack = build_gamepack(
                    _with_authored_narrative(
                        puzzle,
                        topology=topology,
                        required_feature_id=required_feature_id,
                        unit_type=unit_type,
                    )
                )

                self.assertEqual(
                    ["ending", unit_type],
                    [unit["unit_type"] for unit in gamepack["modules"]["narrative"][0]["units"]],
                )
                self.assertIsNone(gamepack["logic"]["narrative_cursor"])
                self.assertEqual([], gamepack["logic"]["narrative_transitions"])
                self.assertIn(
                    required_feature_id,
                    gamepack["runtime_requirements"]["required_features"],
                )
                self.assertEqual(
                    {
                        "profile": "unsupported",
                        "reason_code": "analysis_profile_unsupported",
                    },
                    {
                        "profile": gamepack["analysis_requirements"]["profile"],
                        "reason_code": gamepack["analysis_requirements"]["reason_code"],
                    },
                )
                self.assertEqual(gamepack, validate_gamepack_document(gamepack))

    def test_transitions_without_narrative_cursor_fail_closed(self) -> None:
        authored = build_gamepack(
            _with_authored_narrative(
                load_creation_project(PUZZLE),
                topology="linear",
                required_feature_id="action:realtime_combat",
                unit_type="scene",
            )
        )
        transition = copy.deepcopy(
            build_gamepack(load_creation_project(NARRATIVE))["logic"]["narrative_transitions"][0]
        )
        authored["logic"]["narrative_transitions"] = [transition]
        authored["content_hash"] = gamepack_module._canonical_hash(authored)

        with self.assertRaisesRegex(GamepackError, "narrative_transition_invalid"):
            validate_gamepack_document(authored)

    def test_authored_projection_parity_rejects_choice_units_without_cursor(self) -> None:
        authored = build_gamepack(
            _with_authored_narrative(
                load_creation_project(PUZZLE),
                topology="linear",
                required_feature_id="action:realtime_combat",
                unit_type="scene",
            )
        )
        choice = copy.deepcopy(authored)
        projection = choice["modules"]["narrative"][0]
        projection["units"].append(
            {
                "id": "authored_second_ending",
                "unit_type": "ending",
                "title": "Second authored ending",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": [],
                "asset_binding_ids": [],
                "ending_kind": "neutral",
            }
        )
        scene = next(unit for unit in projection["units"] if unit["unit_type"] == "scene")
        scene["unit_type"] = "choice"
        scene["next_unit_ids"] = ["authored_ending", "authored_second_ending"]
        scene["options"] = [
            {
                "id": "choose_first",
                "label": "First ending",
                "next_unit_id": "authored_ending",
                "condition_ids": [],
                "effect_ids": [],
            },
            {
                "id": "choose_second",
                "label": "Second ending",
                "next_unit_id": "authored_second_ending",
                "condition_ids": [],
                "effect_ids": [],
            },
        ]
        choice["content_hash"] = gamepack_module._canonical_hash(choice)

        with self.assertRaisesRegex(
            GamepackError,
            "authored narrative projection cannot contain executable choice units",
        ):
            validate_gamepack_document(choice)

    def test_build_is_deterministic_and_matches_checked_in_trusted_fixtures(self) -> None:
        cases = (
            ("abstract-puzzle", PUZZLE),
            ("branching-narrative", NARRATIVE),
        )
        for directory, project_path in cases:
            with self.subTest(directory=directory):
                with (
                    tempfile.TemporaryDirectory() as first_root,
                    tempfile.TemporaryDirectory() as second_root,
                ):
                    first_project = Path(first_root) / directory
                    second_project = Path(second_root) / directory
                    shutil.copytree(project_path.parent, first_project)
                    shutil.copytree(project_path.parent, second_project)
                    first = build_gamepack(load_creation_project(first_project / "project.json"))
                    second = build_gamepack(load_creation_project(second_project / "project.json"))
                expected_pack = (
                    EXAMPLES / directory / "artifacts" / f"{directory}.gamepack.json"
                ).read_bytes()
                self.assertEqual(serialize_gamepack(first), serialize_gamepack(second))
                self.assertEqual(expected_pack, serialize_gamepack(first))

                ledger = build_authoring_capability_ledger(first)
                expected_ledger = (
                    EXAMPLES / directory / "artifacts" / f"{directory}.authoring-ledger.json"
                ).read_bytes()
                self.assertEqual(expected_ledger, serialize_capability_ledger(ledger))

    def test_integral_validation_rebuilds_and_rejects_source_projection_drift(self) -> None:
        loaded = load_creation_project(PUZZLE)
        gamepack = build_gamepack(loaded)
        self.assertEqual(gamepack, validate_gamepack(gamepack, source_project=loaded))

        drifted = copy.deepcopy(gamepack)
        drifted["modules"]["activities"][0]["activities"][0]["title"] = "Drifted"
        drifted["content_hash"] = gamepack_module._canonical_hash(drifted)
        with self.assertRaisesRegex(GamepackError, "localization.references"):
            validate_gamepack_document(drifted)
        activity_reference = next(
            reference
            for reference in drifted["localization"]["references"]
            if reference["key"] == "activity.symbol_board.title"
        )
        activity_reference["source_text"] = "Drifted"
        drifted["content_hash"] = gamepack_module._canonical_hash(drifted)
        self.assertEqual(drifted, validate_gamepack_document(drifted))
        with self.assertRaisesRegex(GamepackError, "source_binding_mismatch"):
            validate_gamepack(drifted, source_project=loaded)

    def test_missing_or_ambiguous_executable_logic_fails_with_stable_reasons(self) -> None:
        puzzle = load_creation_project(PUZZLE)

        with self.assertRaisesRegex(GamepackError, "logic_module_required"):
            build_gamepack(_malformed_loaded(puzzle, logic_modules=()))

        with self.assertRaisesRegex(GamepackError, "logic_module_count_unsupported"):
            build_gamepack(
                _malformed_loaded(
                    puzzle,
                    logic_modules=(puzzle.logic_modules[0], puzzle.logic_modules[0]),
                )
            )

    def test_compiler_owned_state_is_exact_and_malformed_entries_fail_closed(self) -> None:
        puzzle = build_gamepack(load_creation_project(PUZZLE))
        malformed = copy.deepcopy(puzzle)
        malformed["logic"]["state_schema"] = [None]
        malformed["content_hash"] = gamepack_module._canonical_hash(malformed)
        with self.assertRaisesRegex(GamepackError, "compiled_logic_invalid"):
            validate_gamepack_document(malformed)

        backdoor = copy.deepcopy(puzzle)
        internal = copy.deepcopy(backdoor["logic"]["state_schema"][0])
        internal["id"] = "wf_internal_backdoor"
        backdoor["logic"]["state_schema"].append(internal)
        backdoor["logic"]["initial_state"]["wf_internal_backdoor"] = internal["initial"]
        backdoor["content_hash"] = gamepack_module._canonical_hash(backdoor)
        with self.assertRaisesRegex(GamepackError, "compiler-owned state"):
            validate_gamepack_document(backdoor)

        narrative = build_gamepack(load_creation_project(NARRATIVE))
        duplicate = copy.deepcopy(narrative)
        duplicate["logic"]["state_schema"].append(
            copy.deepcopy(duplicate["logic"]["narrative_cursor"])
        )
        duplicate["content_hash"] = gamepack_module._canonical_hash(duplicate)
        with self.assertRaisesRegex(GamepackError, "exactly one"):
            validate_gamepack_document(duplicate)

        self.assertIs(narrative["logic"]["narrative_cursor"]["compiler_owned"], True)
        for transition in narrative["logic"]["narrative_transitions"]:
            self.assertIs(transition["compiler_owned"], True)
            self.assertIs(transition["precondition"]["compiler_owned"], True)
            self.assertIs(transition["effect"]["compiler_owned"], True)

        reordered = copy.deepcopy(narrative)
        reordered["logic"]["state_schema"].reverse()
        reordered["content_hash"] = gamepack_module._canonical_hash(reordered)
        with self.assertRaisesRegex(GamepackError, "exact and last"):
            validate_gamepack_document(reordered)

        narrative_modules_without_logic = copy.deepcopy(narrative)
        narrative_modules_without_logic["logic"]["narrative_cursor"] = None
        narrative_modules_without_logic["logic"]["narrative_transitions"] = []
        narrative_modules_without_logic["logic"]["state_schema"] = [
            state
            for state in narrative_modules_without_logic["logic"]["state_schema"]
            if state.get("compiler_owned") is not True
        ]
        narrative_modules_without_logic["logic"]["initial_state"].pop(
            "wf_internal_narrative_cursor"
        )
        narrative_modules_without_logic["content_hash"] = gamepack_module._canonical_hash(
            narrative_modules_without_logic
        )
        with self.assertRaisesRegex(GamepackError, "compiled_logic_invalid"):
            validate_gamepack_document(narrative_modules_without_logic)

        narrative_logic_without_modules = copy.deepcopy(narrative)
        narrative_logic_without_modules["modules"]["narrative"] = []
        narrative_logic_without_modules["content_hash"] = gamepack_module._canonical_hash(
            narrative_logic_without_modules
        )
        with self.assertRaisesRegex(GamepackError, "compiled_logic_invalid"):
            validate_gamepack_document(narrative_logic_without_modules)

    def test_reachable_non_choice_narrative_units_fail_honest_v1_lowering(self) -> None:
        loaded = load_creation_project(NARRATIVE)
        module = copy.deepcopy(loaded.narrative_modules[0])
        module["entry_unit_ids"] = ["intro_scene"]
        module["units"].append(
            {
                "id": "intro_scene",
                "unit_type": "scene",
                "title": "Visible introduction",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": ["central_choice"],
                "asset_binding_ids": [],
            }
        )
        module["units"].sort(key=lambda item: item["id"].encode("utf-8"))
        with self.assertRaisesRegex(GamepackError, "narrative_transition_unsupported"):
            build_gamepack(_with_narrative_module(loaded, module))

        unreachable = copy.deepcopy(loaded.narrative_modules[0])
        unreachable["units"].append(
            {
                "id": "unreachable_ending",
                "unit_type": "ending",
                "title": "Unreachable ending",
                "prerequisite_ids": [],
                "effect_ids": [],
                "next_unit_ids": [],
                "asset_binding_ids": [],
                "ending_kind": "neutral",
            }
        )
        with self.assertRaisesRegex(GamepackError, "narrative_unit_unreachable"):
            gamepack_module._derive_narrative_runtime(
                [unreachable],
                loaded.logic_modules[0],
            )

        cursor, transitions = gamepack_module._derive_narrative_runtime(
            [
                {
                    "entry_unit_ids": ["immediate_ending"],
                    "units": [
                        {
                            "id": "immediate_ending",
                            "unit_type": "ending",
                            "next_unit_ids": [],
                        }
                    ],
                }
            ],
            {"actions": [], "rules": []},
        )
        self.assertEqual("immediate_ending", cursor["initial"])
        self.assertEqual([], transitions)

        condition_drift = copy.deepcopy(loaded.narrative_modules[0])
        condition_drift["units"][0]["options"][0]["condition_ids"] = []
        with self.assertRaisesRegex(GamepackError, "source_project_invalid"):
            build_gamepack(_with_narrative_module(loaded, condition_drift))

        narrative = load_creation_project(NARRATIVE)
        narrative_module = copy.deepcopy(narrative.narrative_modules[0])
        narrative_module["entry_unit_ids"] = ["central_choice", "ending_left"]
        narrative_module["content_hash"] = gamepack_module._canonical_hash(narrative_module)
        manifest = copy.deepcopy(narrative.manifest)
        manifest["modules"]["narrative_modules"][0]["content_hash"] = narrative_module[
            "content_hash"
        ]
        manifest["content_hash"] = gamepack_module._canonical_hash(manifest)
        project = copy.deepcopy(narrative.project)
        project["source_manifest"]["content_hash"] = manifest["content_hash"]
        project["content_hash"] = gamepack_module._canonical_hash(project)
        with self.assertRaisesRegex(GamepackError, "narrative_entry_count_unsupported"):
            build_gamepack(
                LoadedCreationProject(
                    project=project,
                    profile=narrative.profile,
                    manifest=manifest,
                    world_modules=narrative.world_modules,
                    activity_modules=narrative.activity_modules,
                    narrative_modules=(narrative_module,),
                    system_modules=narrative.system_modules,
                    logic_modules=narrative.logic_modules,
                )
            )

    def test_required_extensions_fail_closed_and_unknown_adapter_does_not_claim_support(
        self,
    ) -> None:
        loaded = load_creation_project(PUZZLE)
        project = copy.deepcopy(loaded.project)
        project["extensions"] = [
            {
                "id": "example.required-extension",
                "version": 1,
                "required": True,
                "content_hash": "0" * 64,
            }
        ]
        with self.assertRaisesRegex(GamepackError, "required_extension_unsupported"):
            build_gamepack(
                LoadedCreationProject(
                    project=project,
                    profile=loaded.profile,
                    manifest=loaded.manifest,
                    world_modules=loaded.world_modules,
                    activity_modules=loaded.activity_modules,
                    narrative_modules=loaded.narrative_modules,
                    system_modules=loaded.system_modules,
                    logic_modules=loaded.logic_modules,
                )
            )

        unknown_adapter = _with_requested_adapter(loaded, "future_puzzle_adapter")
        gamepack = build_gamepack(unknown_adapter)
        self.assertEqual(
            "future_puzzle_adapter",
            gamepack["runtime_requirements"]["requested_adapter"],
        )
        ledger = build_authoring_capability_ledger(gamepack)
        self.assertEqual("declared", ledger["adapter"]["status"])
        self.assertEqual(
            {"authoring_only"},
            {entry["status"] for entry in ledger["mechanics"]},
        )
        self.assertEqual(
            {"authoring_only"},
            {entry["status"] for entry in ledger["features"]},
        )
        self.assertTrue(all(not entry["native_evidence"] for entry in ledger["mechanics"]))

    def test_optional_extensions_lower_with_integer_versions_and_fail_closed(self) -> None:
        loaded = load_creation_project(PUZZLE)
        extension = {
            "id": "example.optional-metadata",
            "version": 7,
            "required": False,
            "content_hash": hashlib.sha256(b"optional-metadata-v7").hexdigest(),
        }
        gamepack = build_gamepack(_with_project_extensions(loaded, [extension]))
        self.assertEqual([extension], gamepack["registered_extensions"])
        self.assertEqual(gamepack, validate_gamepack_document(gamepack))
        ledger = build_authoring_capability_ledger(gamepack)
        self.assertEqual(
            ledger,
            validate_capability_ledger_document(ledger, gamepack=gamepack),
        )

        malformed_cases = (
            (
                "version",
                {
                    **extension,
                    "version": "7",
                },
            ),
            (
                "content_hash",
                {
                    **extension,
                    "content_hash": "not-a-sha256",
                },
            ),
        )
        for label, malformed in malformed_cases:
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(GamepackError, "source_project_invalid"),
            ):
                build_gamepack(_with_project_extensions(loaded, [malformed]))

        duplicate = _with_profile_extensions(
            loaded,
            project_extensions=[extension],
            profile_extensions=[extension],
        )
        self.assertEqual(
            [extension],
            build_gamepack(duplicate)["registered_extensions"],
        )

        collision = _with_profile_extensions(
            loaded,
            project_extensions=[extension],
            profile_extensions=[
                {
                    **extension,
                    "version": 8,
                    "content_hash": hashlib.sha256(b"conflicting-extension-v8").hexdigest(),
                }
            ],
        )
        with self.assertRaisesRegex(GamepackError, "extension_identity_conflict"):
            build_gamepack(collision)

        project_extensions = [
            {
                "id": f"example.extension-{index:03d}",
                "version": index + 1,
                "required": False,
                "content_hash": hashlib.sha256(f"extension-{index}".encode()).hexdigest(),
            }
            for index in range(64)
        ]
        aggregate_overflow = _with_profile_extensions(
            loaded,
            project_extensions=project_extensions,
            profile_extensions=[
                {
                    "id": "example.extension-overflow",
                    "version": 1,
                    "required": False,
                    "content_hash": hashlib.sha256(b"extension-overflow").hexdigest(),
                }
            ],
        )
        with self.assertRaisesRegex(GamepackError, "extension_limit_exceeded"):
            build_gamepack(aggregate_overflow)

    def test_capability_ledger_rejects_dishonest_support_without_adapter_evidence(self) -> None:
        gamepack = build_gamepack(load_creation_project(PUZZLE))
        ledger = build_authoring_capability_ledger(gamepack)
        dishonest = copy.deepcopy(ledger)
        dishonest["mechanics"][0]["status"] = "supported_current"
        dishonest["content_hash"] = gamepack_module._canonical_hash(dishonest)

        with self.assertRaisesRegex(GamepackError, "capability_status_inconsistent"):
            validate_capability_ledger_document(dishonest)

        mapping_drift = copy.deepcopy(ledger)
        mapping_drift["mechanics"][0]["authoritative_state_ids"].pop()
        mapping_drift["content_hash"] = gamepack_module._canonical_hash(mapping_drift)
        with self.assertRaisesRegex(GamepackError, "non-exact authoritative_state_ids"):
            validate_capability_ledger_document(mapping_drift, gamepack=gamepack)

        adapter_drift = copy.deepcopy(ledger)
        adapter_drift["adapter"]["adapter_id"] = "different_adapter"
        adapter_drift["content_hash"] = gamepack_module._canonical_hash(adapter_drift)
        with self.assertRaisesRegex(GamepackError, "adapter identity"):
            validate_capability_ledger_document(adapter_drift, gamepack=gamepack)

    def test_verified_capabilities_require_trusted_registries_and_hashed_evidence(self) -> None:
        gamepack = build_gamepack(load_creation_project(PUZZLE))
        ledger, adapter, evidence_sources = _verified_ledger(gamepack)

        with self.assertRaisesRegex(GamepackError, "trusted_capability_resolver_required"):
            validate_capability_ledger_document(ledger, gamepack=gamepack)
        self.assertEqual(
            ledger,
            resolve_capability_ledger(
                ledger,
                gamepack=gamepack,
                adapter_registry={(adapter.adapter_id, adapter.adapter_version): adapter},
                extension_registry={},
                evidence_sources=evidence_sources,
            ),
        )

        fake = copy.deepcopy(ledger)
        fake["mechanics"][0]["test_evidence"][0]["content_hash"] = "0" * 64
        fake["content_hash"] = gamepack_module._canonical_hash(fake)
        with self.assertRaisesRegex(GamepackError, "evidence_hash_mismatch"):
            resolve_capability_ledger(
                fake,
                gamepack=gamepack,
                adapter_registry={(adapter.adapter_id, adapter.adapter_version): adapter},
                extension_registry={},
                evidence_sources=evidence_sources,
            )

        reused = copy.deepcopy(ledger)
        reused["features"][0]["native_evidence"] = copy.deepcopy(
            reused["mechanics"][0]["native_evidence"]
        )
        reused["content_hash"] = gamepack_module._canonical_hash(reused)
        with self.assertRaisesRegex(GamepackError, "evidence_reused"):
            resolve_capability_ledger(
                reused,
                gamepack=gamepack,
                adapter_registry={(adapter.adapter_id, adapter.adapter_version): adapter},
                extension_registry={},
                evidence_sources=evidence_sources,
            )

    def test_trusted_resolver_rejects_malformed_boundaries_with_stable_codes(self) -> None:
        class PoisonedGetDict(dict[object, object]):
            def get(self, key: object, default: object = None) -> object:
                del key, default
                return {"poisoned": True}

        gamepack = build_gamepack(load_creation_project(PUZZLE))
        ledger, adapter, evidence_sources = _verified_ledger(gamepack)
        adapter_key = (adapter.adapter_id, adapter.adapter_version)
        valid_adapters = {adapter_key: adapter}
        malformed_adapter = RegisteredRuntimeAdapter(
            adapter_id=adapter.adapter_id,
            adapter_version=adapter.adapter_version,
            accepted_logic_formats=adapter.accepted_logic_formats,
            platform_matrix=adapter.platform_matrix,
            supported_features=tuple(adapter.supported_features),  # type: ignore[arg-type]
            supported_mechanics=adapter.supported_mechanics,
        )
        evidence_id = next(iter(evidence_sources))
        evidence_source = evidence_sources[evidence_id]
        malformed_evidence = CapabilityEvidenceSource(
            evidence_id=evidence_source.evidence_id,
            category=evidence_source.category,
            payload=bytearray(evidence_source.payload),  # type: ignore[arg-type]
        )

        cases = (
            (
                "adapter mapping",
                "adapter_registry_invalid",
                None,
                {},
                evidence_sources,
            ),
            (
                "adapter missing",
                "adapter_registry_mismatch",
                {},
                {},
                evidence_sources,
            ),
            (
                "adapter dict impostor",
                "adapter_registry_invalid",
                {adapter_key: {"adapter_id": adapter.adapter_id}},
                {},
                evidence_sources,
            ),
            (
                "adapter malformed support set",
                "adapter_registry_invalid",
                {adapter_key: malformed_adapter},
                {},
                evidence_sources,
            ),
            (
                "adapter mapping subclass",
                "adapter_registry_invalid",
                PoisonedGetDict(valid_adapters),
                {},
                evidence_sources,
            ),
            (
                "extension mapping",
                "extension_registry_invalid",
                valid_adapters,
                None,
                evidence_sources,
            ),
            (
                "extension dict impostor",
                "extension_registry_invalid",
                valid_adapters,
                {("example.extension", 1): {"extension_id": "example.extension"}},
                evidence_sources,
            ),
            (
                "extension mapping subclass",
                "extension_registry_invalid",
                valid_adapters,
                PoisonedGetDict(),
                evidence_sources,
            ),
            (
                "evidence mapping",
                "evidence_sources_invalid",
                valid_adapters,
                {},
                None,
            ),
            (
                "evidence dict impostor",
                "evidence_source_invalid",
                valid_adapters,
                {},
                {next(iter(evidence_sources)): {"category": "test"}},
            ),
            (
                "evidence malformed payload",
                "evidence_source_invalid",
                valid_adapters,
                {},
                {
                    **evidence_sources,
                    evidence_id: malformed_evidence,
                },
            ),
            (
                "evidence mapping subclass",
                "evidence_sources_invalid",
                valid_adapters,
                {},
                PoisonedGetDict(evidence_sources),
            ),
        )
        for label, reason, adapters, extensions, sources in cases:
            with self.subTest(label=label):
                with self.assertRaises(GamepackError) as raised:
                    resolve_capability_ledger(
                        ledger,
                        gamepack=gamepack,
                        adapter_registry=adapters,
                        extension_registry=extensions,
                        evidence_sources=sources,
                    )
                self.assertEqual(reason, raised.exception.reason_code)

        missing_sources = dict(evidence_sources)
        missing_sources.pop(next(iter(missing_sources)))
        with self.assertRaises(GamepackError) as raised:
            resolve_capability_ledger(
                ledger,
                gamepack=gamepack,
                adapter_registry=valid_adapters,
                extension_registry={},
                evidence_sources=missing_sources,
            )
        self.assertEqual("evidence_source_missing", raised.exception.reason_code)

        wrong_category = dict(evidence_sources)
        category_evidence_id = next(iter(wrong_category))
        source = wrong_category[category_evidence_id]
        wrong_category[category_evidence_id] = CapabilityEvidenceSource(
            evidence_id=source.evidence_id,
            category="native" if source.category == "test" else "test",
            payload=source.payload,
        )
        with self.assertRaises(GamepackError) as raised:
            resolve_capability_ledger(
                ledger,
                gamepack=gamepack,
                adapter_registry=valid_adapters,
                extension_registry={},
                evidence_sources=wrong_category,
            )
        self.assertEqual("evidence_category_mismatch", raised.exception.reason_code)

    def test_extension_verified_capability_requires_exact_registered_extension(self) -> None:
        gamepack = build_gamepack(load_creation_project(PUZZLE))
        extension = copy.deepcopy(gamepack["registered_extensions"][0])
        ledger, adapter, evidence_sources = _verified_ledger(gamepack)
        ledger["mechanics"][0].update(
            {
                "status": "game_extension_verified",
                "reason_code": "game_extension_verified",
                "extension": extension,
            }
        )
        ledger["content_hash"] = gamepack_module._canonical_hash(ledger)
        registered = RegisteredGameExtension(
            extension_id=extension["id"],
            extension_version=extension["version"],
            content_hash=extension["content_hash"],
            supported_features=frozenset(),
            supported_mechanics=frozenset({ledger["mechanics"][0]["mechanic_id"]}),
        )
        self.assertEqual(
            ledger,
            resolve_capability_ledger(
                ledger,
                gamepack=gamepack,
                adapter_registry={(adapter.adapter_id, adapter.adapter_version): adapter},
                extension_registry={
                    (registered.extension_id, registered.extension_version): registered
                },
                evidence_sources=evidence_sources,
            ),
        )
        with self.assertRaisesRegex(GamepackError, "extension_registry_mismatch"):
            resolve_capability_ledger(
                ledger,
                gamepack=gamepack,
                adapter_registry={(adapter.adapter_id, adapter.adapter_version): adapter},
                extension_registry={},
                evidence_sources=evidence_sources,
            )

    def test_asset_expansion_is_bounded_before_subject_materialization(self) -> None:
        activity = {
            "id": "activity_0000",
            "asset_binding_ids": [f"binding_{index:02d}" for index in range(64)],
        }
        activities = []
        for index in range(1001):
            item = copy.deepcopy(activity)
            item["id"] = f"activity_{index:04d}"
            activities.append(item)
        modules = {
            "world": [],
            "activities": [{"activities": activities}],
            "narrative": [],
            "systems": [],
        }
        with (
            mock.patch.object(
                gamepack_module,
                "_source_subjects",
                side_effect=AssertionError("expanded subjects were allocated"),
            ),
            self.assertRaisesRegex(GamepackError, "asset_requirement_expansion_limit"),
        ):
            gamepack_module._asset_requirements(
                modules,
                {"presentation_hooks": [], "mechanics": []},
                {"asset_formats": ["image:png"]},
            )

    def test_python_maxima_match_gamepack_schema_contract(self) -> None:
        schema = json.loads((ROOT / "schemas" / "gamepack.schema.json").read_text())
        expected = {
            "asset_requirements": schema["properties"]["asset_requirements"]["maxItems"],
            "asset_referencing_subjects": schema["$defs"]["assetRequirement"]["properties"][
                "referencing_subjects"
            ]["maxItems"],
            "module_collections": schema["$defs"]["modules"]["properties"]["activities"][
                "maxItems"
            ],
            "projected_payloads": schema["$defs"]["activityProjection"]["properties"]["activities"][
                "maxItems"
            ],
            "world_projected_payloads": schema["$defs"]["worldProjection"]["properties"]["records"][
                "maxItems"
            ],
            "id_arrays": schema["$defs"]["idArray"]["maxItems"],
            "state_schema": schema["$defs"]["logic"]["properties"]["state_schema"]["maxItems"],
            "initial_state": schema["$defs"]["initialState"]["maxProperties"],
            "narrative_transitions": schema["$defs"]["logic"]["properties"][
                "narrative_transitions"
            ]["maxItems"],
            "localization_references": schema["$defs"]["localization"]["properties"]["references"][
                "maxItems"
            ],
            "localization_supported_locales": schema["$defs"]["localization"]["properties"][
                "supported_locales"
            ]["maxItems"],
            "mechanic_requirements": schema["properties"]["mechanic_requirements"]["maxItems"],
            "provenance": schema["properties"]["provenance"]["maxItems"],
            "registered_extensions": schema["properties"]["registered_extensions"]["maxItems"],
            "runtime_accepted_logic_formats": schema["$defs"]["runtimeRequirements"]["properties"][
                "accepted_logic_formats"
            ]["maxItems"],
            "runtime_features": schema["$defs"]["nonEmptyTokenArray"]["maxItems"],
            "runtime_platform_matrix": schema["$defs"]["runtimeRequirements"]["properties"][
                "platform_matrix"
            ]["maxItems"],
        }
        self.assertEqual(expected, GAMEPACK_SCHEMA_MAXIMA)
        ledger_schema = json.loads(
            (ROOT / "schemas" / "mechanic-capability-ledger.schema.json").read_text()
        )
        self.assertEqual(
            {
                "mechanics": ledger_schema["properties"]["mechanics"]["maxItems"],
                "features": ledger_schema["properties"]["features"]["maxItems"],
                "id_arrays": ledger_schema["$defs"]["idArray"]["maxItems"],
                "evidence": ledger_schema["$defs"]["evidenceArray"]["maxItems"],
                "adapter_version": ledger_schema["$defs"]["adapter"]["oneOf"][2]["properties"][
                    "adapter_version"
                ]["maxLength"],
            },
            CAPABILITY_LEDGER_SCHEMA_MAXIMA,
        )

    def test_gamepack_schema_and_types_close_compiler_owned_boundaries(self) -> None:
        gamepack_schema = json.loads(
            (ROOT / "schemas" / "gamepack.schema.json").read_text(encoding="utf-8")
        )
        logic_schema = json.loads(
            (ROOT / "schemas" / "logic-module.schema.json").read_text(encoding="utf-8")
        )
        for definition in (
            "internalCursor",
            "narrativeTransition",
            "narrativeTransitionPrecondition",
            "narrativeTransitionEffect",
        ):
            self.assertEqual(
                {"const": True},
                gamepack_schema["$defs"][definition]["properties"]["compiler_owned"],
            )
            self.assertIn(
                "compiler_owned",
                gamepack_schema["$defs"][definition]["required"],
            )
        for definition in (
            "booleanState",
            "integerState",
            "stringState",
            "stringArrayState",
        ):
            self.assertIs(
                logic_schema["$defs"][definition]["properties"]["compiler_owned"],
                False,
            )
        self.assertIs(
            gamepack_schema["$defs"]["logic"]["allOf"][0]["else"]["properties"]["state_schema"][
                "x-world-forge-final-compiler-owned"
            ],
            True,
        )
        narrative_correlation = gamepack_schema["allOf"][0]["oneOf"]
        self.assertEqual(
            0,
            narrative_correlation[0]["properties"]["modules"]["properties"]["narrative"][
                "maxItems"
            ],
        )
        self.assertEqual(
            {"type": "null"},
            narrative_correlation[0]["properties"]["logic"]["properties"]["narrative_cursor"],
        )
        self.assertEqual(
            1,
            narrative_correlation[1]["properties"]["modules"]["properties"]["narrative"][
                "minItems"
            ],
        )
        self.assertEqual(
            {"$ref": "#/$defs/internalCursor"},
            narrative_correlation[1]["properties"]["logic"]["properties"]["narrative_cursor"],
        )
        self.assertEqual(
            1,
            narrative_correlation[2]["properties"]["modules"]["properties"]["narrative"][
                "minItems"
            ],
        )
        self.assertEqual(
            {"$ref": "#/$defs/authoredNarrativeProjection"},
            narrative_correlation[2]["properties"]["modules"]["properties"]["narrative"]["items"],
        )
        authored_unit_variants = gamepack_schema["$defs"]["authoredNarrativeProjection"][
            "properties"
        ]["units"]["items"]["oneOf"]
        self.assertEqual(
            [
                {"$ref": "#/$defs/narrativeEndingUnit"},
                {"$ref": "#/$defs/narrativeStandardUnit"},
            ],
            authored_unit_variants,
        )
        self.assertEqual(
            {"type": "null"},
            narrative_correlation[2]["properties"]["logic"]["properties"]["narrative_cursor"],
        )
        self.assertEqual(
            0,
            narrative_correlation[2]["properties"]["logic"]["properties"]["narrative_transitions"][
                "maxItems"
            ],
        )

        generated = (
            ROOT / "apps" / "studio" / "src" / "generated" / "world-forge-contracts.d.ts"
        ).read_text(encoding="utf-8")
        conformance = (
            ROOT / "apps" / "studio" / "src" / "generated" / "world-forge-contracts.conformance.ts"
        ).read_text(encoding="utf-8")
        self.assertNotIn("type GamepackLogicBase = Omit<\n  Logic", generated)
        self.assertIn("type GamepackCondition =", generated)
        self.assertIn("type GamepackEffect =", generated)
        self.assertIn("compiler_owned?: never;", generated)
        self.assertIn("compiler_owned: true;", generated)
        self.assertIn("type GamepackNarrativeFreeModules =", generated)
        self.assertIn("type GamepackNarrativeModules =", generated)
        self.assertIn("type GamepackAuthoredNarrativeProjection =", generated)
        self.assertIn("type GamepackAuthoredNarrativeModules =", generated)
        for probe in (
            "reset effects cannot retain a value payload",
            "constant conditions cannot retain a left operand",
            "standard narrative units cannot retain ending_kind",
            "narrative logic requires exactly one canonical final cursor",
            "narrative-free logic cannot retain compiler-owned cursor state",
            "authored narrative projection cannot retain executable transitions",
            "authored narrative projection cannot contain choice units",
            "narrative logic requires at least one narrative module",
        ):
            self.assertIn(probe, conformance)

    def test_document_validation_rejects_derived_and_compiler_owned_drift(self) -> None:
        puzzle = build_gamepack(load_creation_project(PUZZLE))

        missing_provenance = copy.deepcopy(puzzle)
        missing_provenance["provenance"].pop()
        missing_provenance["content_hash"] = gamepack_module._canonical_hash(missing_provenance)
        with self.assertRaisesRegex(GamepackError, "provenance must exactly"):
            validate_gamepack_document(missing_provenance)

        forbidden = copy.deepcopy(puzzle)
        forbidden["asset_requirements"][0]["provider_credentials"] = "secret"
        forbidden["content_hash"] = gamepack_module._canonical_hash(forbidden)
        with self.assertRaisesRegex(GamepackError, "unsafe runtime or authoring field"):
            validate_gamepack_document(forbidden)

        core_verb_order = copy.deepcopy(puzzle)
        core_verb_order["logic"]["core_verbs"].reverse()
        core_verb_order["content_hash"] = gamepack_module._canonical_hash(core_verb_order)
        with self.assertRaisesRegex(GamepackError, "core_verbs is not canonical"):
            validate_gamepack_document(core_verb_order)

        feature_drift = copy.deepcopy(puzzle)
        feature_drift["runtime_requirements"]["required_features"].pop()
        feature_drift["content_hash"] = gamepack_module._canonical_hash(feature_drift)
        with self.assertRaisesRegex(GamepackError, "exact mechanic requirements"):
            validate_gamepack_document(feature_drift)

        narrative = build_gamepack(load_creation_project(NARRATIVE))
        transition_drift = copy.deepcopy(narrative)
        transition_drift["logic"]["narrative_transitions"][0]["effect"]["value"] = "ending_right"
        transition_drift["content_hash"] = gamepack_module._canonical_hash(transition_drift)
        with self.assertRaisesRegex(GamepackError, "effect is not exact"):
            validate_gamepack_document(transition_drift)

        transition_condition_drift = copy.deepcopy(narrative)
        transition_condition_drift["logic"]["narrative_transitions"][0][
            "atomic_source_condition_ids"
        ] = []
        transition_condition_drift["content_hash"] = gamepack_module._canonical_hash(
            transition_condition_drift
        )
        with self.assertRaisesRegex(GamepackError, "narrative cursor/transitions"):
            validate_gamepack_document(transition_condition_drift)

    def test_secure_compile_and_load_are_exclusive_and_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "compiled" / "puzzle.gamepack.json"
            expected = build_gamepack(load_creation_project(PUZZLE))

            self.assertEqual(expected, compile_game_project(PUZZLE.parent, output))
            self.assertEqual(
                expected,
                load_gamepack(
                    output,
                    source_project=load_creation_project(PUZZLE),
                ),
            )
            with self.assertRaisesRegex(GamepackError, "output_exists"):
                compile_game_project(PUZZLE, output)

            duplicate = root / "duplicate.json"
            duplicate.write_text(
                '{"format":"world-forge.gamepack","format":"world-forge.gamepack"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(GamepackError, "invalid_json"):
                load_gamepack(duplicate)

            linked = root / "linked.json"
            linked.symlink_to(output)
            with self.assertRaisesRegex(GamepackError, "invalid_json"):
                load_gamepack(linked)

            hardlink = root / "hardlink.json"
            try:
                os.link(output, hardlink)
            except OSError:
                pass
            else:
                with self.assertRaisesRegex(GamepackError, "invalid_json"):
                    load_gamepack(hardlink)

            malformed_documents = {
                "non-object.json": b"[]",
                "invalid-utf8.json": b'{"x":"\\xff"}'.replace(b"\\xff", b"\xff"),
                "unsafe-integer.json": b'{"value":9007199254740992}',
                "float.json": b'{"value":1.5}',
                "deep.json": (('{"x":' * 70 + "0" + "}" * 70).encode("utf-8")),
                "oversize.json": b'{"value":"' + b"a" * (4 * 1024 * 1024) + b'"}',
            }
            for name, payload in malformed_documents.items():
                with self.subTest(name=name):
                    malformed = root / name
                    malformed.write_bytes(payload)
                    with self.assertRaisesRegex(GamepackError, "invalid_json"):
                        load_gamepack(malformed)

            symlink_output = root / "symlink-output.json"
            try:
                symlink_output.symlink_to(output)
            except OSError:
                pass
            else:
                with self.assertRaisesRegex(GamepackError, "output_preflight_failed"):
                    compile_game_project(PUZZLE, symlink_output)

            hardlink_output = root / "hardlink-output.json"
            try:
                os.link(output, hardlink_output)
            except OSError:
                pass
            else:
                with self.assertRaisesRegex(GamepackError, "output_exists"):
                    compile_game_project(PUZZLE, hardlink_output)

            concurrent_output = root / "concurrent.json"

            def compile_once() -> str:
                try:
                    compile_game_project(PUZZLE, concurrent_output)
                except GamepackError as exc:
                    return exc.reason_code
                return "compiled"

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: compile_once(), range(2)))
            self.assertCountEqual(["compiled", "output_exists"], results)

    def test_gamepack_publication_rejects_windows_reparse_final_without_cleanup(
        self,
    ) -> None:
        gamepack = build_gamepack(load_creation_project(PUZZLE))
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "gamepack.json"
            original_stat = gamepack_module.path_file_stat

            def reparse_published(path: str | Path) -> object:
                info = original_stat(path)
                if Path(path) != destination:
                    return info
                return SimpleNamespace(
                    st_mode=info.st_mode,
                    st_dev=info.st_dev,
                    st_ino=info.st_ino,
                    st_nlink=info.st_nlink,
                    st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns,
                    st_ctime_ns=info.st_ctime_ns,
                    st_file_attributes=getattr(
                        stat,
                        "FILE_ATTRIBUTE_REPARSE_POINT",
                        0x400,
                    ),
                )

            with (
                mock.patch.object(
                    gamepack_module,
                    "path_file_stat",
                    side_effect=reparse_published,
                ),
                self.assertRaisesRegex(GamepackError, "output_identity_failed"),
            ):
                gamepack_module.publish_gamepack(destination, gamepack)
            self.assertTrue(destination.exists())

    def test_compile_game_cli_outputs_json_and_reports_safe_partial_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "gamepack.json"
            ledger = root / "ledger.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "compile-game",
                        str(PUZZLE),
                        "--output",
                        str(output),
                        "--ledger-output",
                        str(ledger),
                    ],
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main()
            summary = json.loads(stdout.getvalue())

            self.assertEqual(0, exit_code)
            self.assertEqual("", stderr.getvalue())
            self.assertEqual("compiled", summary["compilation"])
            self.assertEqual("unplanned", summary["assets"])
            self.assertEqual("declared", summary["adapter"])
            self.assertEqual("blocked", summary["release"])
            self.assertEqual(load_gamepack(output)["content_hash"], summary["gamepack"]["hash"])
            self.assertTrue(ledger.is_file())

            partial_output = root / "partial.gamepack.json"
            partial_ledger = root / "partial.ledger.json"
            partial_stderr = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "compile-game",
                        str(PUZZLE),
                        "--output",
                        str(partial_output),
                        "--ledger-output",
                        str(partial_ledger),
                    ],
                ),
                mock.patch(
                    "worldforge.__main__.publish_capability_ledger",
                    side_effect=GamepackError("ledger_publish_failed", "injected"),
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(partial_stderr),
            ):
                self.assertEqual(1, main())
            receipt = json.loads(partial_stderr.getvalue())
            self.assertEqual("partial_publication", receipt["status"])
            self.assertEqual(
                load_gamepack(partial_output)["content_hash"],
                receipt["published"][0]["content_hash"],
            )
            self.assertFalse(partial_ledger.exists())

    def test_partial_publication_never_deletes_foreign_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "gamepack.json"
            ledger = root / "ledger.json"
            foreign = b'{"owner":"foreign"}\n'

            def replace_then_fail(*_args: object, **_kwargs: object) -> None:
                replacement = root / "foreign.json"
                replacement.write_bytes(foreign)
                output.unlink()
                replacement.replace(output)
                raise GamepackError("ledger_publish_failed", "injected")

            stderr = io.StringIO()
            with (
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "compile-game",
                        str(PUZZLE),
                        "--output",
                        str(output),
                        "--ledger-output",
                        str(ledger),
                    ],
                ),
                mock.patch(
                    "worldforge.__main__.publish_capability_ledger",
                    side_effect=replace_then_fail,
                ),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(1, main())
            self.assertEqual(foreign, output.read_bytes())
            self.assertEqual("partial_publication", json.loads(stderr.getvalue())["status"])

    def test_compile_game_contract_errors_use_stderr_without_traceback(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                [
                    "worldforge",
                    "compile-game",
                    str(EXAMPLES / "missing-project"),
                    "--output",
                    "unused.json",
                ],
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = main()

        self.assertEqual(1, exit_code)
        self.assertEqual("", stdout.getvalue())
        failure = json.loads(stderr.getvalue())
        self.assertEqual("error", failure["status"])
        self.assertEqual("creation_project_inspection_failed", failure["reason_code"])
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_compile_game_normalizes_unexpected_project_os_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sensitive = "/secret/project.json"
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch(
                    "worldforge.gamepack.load_creation_project",
                    side_effect=OSError(5, "injected project read failure", sensitive),
                ),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "compile-game",
                        str(PUZZLE),
                        "--output",
                        str(root / "unused.json"),
                    ],
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main()

            error = json.loads(stderr.getvalue())
            self.assertEqual(1, exit_code)
            self.assertEqual("", stdout.getvalue())
            self.assertEqual("error", error["status"])
            self.assertEqual(
                "creation_project_inspection_failed",
                error["reason_code"],
            )
            self.assertEqual(
                "Creation project could not be inspected safely",
                error["detail"],
            )
            self.assertNotIn(sensitive, stderr.getvalue())
            self.assertNotIn("Errno", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_compile_game_preserves_all_operational_creation_reason_codes(self) -> None:
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
            root = Path(temp)
            for index, reason_code in enumerate(operational_reasons):
                with self.subTest(reason_code=reason_code):
                    detail = f"Safe creation failure {index}"
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        mock.patch(
                            "worldforge.gamepack.load_creation_project",
                            side_effect=CreationContractError(
                                detail,
                                reason_code=reason_code,
                            ),
                        ),
                        mock.patch(
                            "sys.argv",
                            [
                                "worldforge",
                                "compile-game",
                                str(PUZZLE),
                                "--output",
                                str(root / f"unused-{index}.json"),
                            ],
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        exit_code = main()

                    error = json.loads(stderr.getvalue())
                    self.assertEqual(1, exit_code)
                    self.assertEqual("", stdout.getvalue())
                    self.assertEqual("error", error["status"])
                    self.assertEqual(reason_code, error["reason_code"])
                    self.assertEqual(detail, error["detail"])
                    self.assertNotIn(str(root), error["detail"])
                    self.assertNotIn("Traceback", stderr.getvalue())

    def test_compile_game_preserves_pathless_creation_aggregate_limit_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch(
                    "worldforge.creation_contracts.MAX_CREATION_AGGREGATE_BYTES",
                    1,
                ),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "compile-game",
                        str(PUZZLE),
                        "--output",
                        str(root / "unused.json"),
                    ],
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main()

            error = json.loads(stderr.getvalue())
            self.assertEqual(1, exit_code)
            self.assertEqual("", stdout.getvalue())
            self.assertEqual("error", error["status"])
            self.assertEqual(
                "creation_project_aggregate_limit",
                error["reason_code"],
            )
            self.assertNotIn(str(root), error["detail"])
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_compile_game_preserves_pathless_creation_boundary_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            alias = root / "project-alias"
            try:
                alias.symlink_to(PUZZLE, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symbolic links are unavailable")
            for reference in (alias / "project.json", alias):
                with self.subTest(reference=reference):
                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with (
                        mock.patch(
                            "sys.argv",
                            [
                                "worldforge",
                                "compile-game",
                                str(reference),
                                "--output",
                                str(root / "unused.json"),
                            ],
                        ),
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                    ):
                        exit_code = main()

                    error = json.loads(stderr.getvalue())
                    self.assertEqual(1, exit_code)
                    self.assertEqual("", stdout.getvalue())
                    self.assertEqual("error", error["status"])
                    self.assertEqual(
                        "creation_project_root_linked",
                        error["reason_code"],
                    )
                    self.assertNotIn(str(root), error["detail"])
                    self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
