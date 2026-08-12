from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worldforge import creation_vocabulary
from worldforge.__main__ import main
from worldforge.creation_contracts import load_creation_project
from worldforge.creation_readiness import build_creation_readiness
from worldforge.creation_scaffold import CreationScaffoldError, create_creation_project
from worldforge.studio import contracts as studio_contracts
from worldforge.studio.contracts import validate_studio_protocol_envelope
from worldforge.studio.creation_grants import CreationRootGrantManager
from worldforge.studio.creation_workspaces import CreationWorkspaceManager
from worldforge.studio.errors import StudioError
from worldforge.studio.storage import StudioStore, encode_json

ROOT = Path(__file__).resolve().parents[1]


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _game_options(**overrides: object) -> dict[str, object]:
    options: dict[str, object] = {
        "project_kind": "game",
        "gameplay_family": "puzzle",
        "initial_core_verb": "solve",
        "initial_core_loop": "inspect, act, and review deterministic feedback",
        "world_presence": "none",
        "narrative_requirement": "none",
        "narrative_authorship": "none",
        "narrative_topology": "none",
        "presentation_mode": "2d",
        "runtime_support_intent": "authoring_only",
    }
    options.update(overrides)
    return options


class KindAwareCreationScaffoldTests(unittest.TestCase):
    def test_vocabularies_are_shared_by_python_and_public_studio_schema(self) -> None:
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1] / "schemas/studio-protocol-v3.schema.json"
            ).read_text(encoding="utf-8")
        )
        v5_schema = json.loads(
            (
                Path(__file__).resolve().parents[1] / "schemas/studio-protocol-v5.schema.json"
            ).read_text(encoding="utf-8")
        )
        library_properties = schema["$defs"]["workspaceCreateLibraryParams"]["properties"]
        no_narrative = schema["$defs"]["workspaceCreateGameWithoutNarrativeParams"]["properties"]
        properties = schema["$defs"]["workspaceCreateGameWithNarrativeParams"]["properties"]
        self.assertEqual(
            set(creation_vocabulary.CREATION_PROJECT_KINDS),
            set(library_properties["project_kind"]["enum"]) | {properties["project_kind"]["const"]},
        )
        pairs = (
            (creation_vocabulary.GAMEPLAY_FAMILIES, "gameplay_family"),
            (creation_vocabulary.WORLD_PRESENCES, "world_presence"),
            (creation_vocabulary.PRESENTATION_MODES, "presentation_mode"),
            (creation_vocabulary.RUNTIME_SUPPORT_INTENTS, "runtime_support_intent"),
        )
        for vocabulary, field in pairs:
            with self.subTest(field=field):
                self.assertEqual(set(vocabulary), set(properties[field]["enum"]))
        v5_no_narrative = v5_schema["$defs"]["workspaceCreateGameWithoutNarrativeParams"][
            "properties"
        ]
        v5_properties = v5_schema["$defs"]["workspaceCreateGameWithNarrativeParams"]["properties"]
        for params in (v5_no_narrative, v5_properties):
            with self.subTest(field="asset_content_mode", params=tuple(params)):
                self.assertEqual(
                    set(creation_vocabulary.CREATION_CONTENT_MODES),
                    set(params["asset_content_mode"]["enum"]),
                )
        self.assertNotIn("asset_content_mode", no_narrative)
        self.assertNotIn("asset_content_mode", properties)
        for vocabulary, field in (
            (creation_vocabulary.NARRATIVE_REQUIREMENTS, "narrative_requirement"),
            (creation_vocabulary.NARRATIVE_AUTHORSHIP_MODES, "narrative_authorship"),
            (creation_vocabulary.NARRATIVE_TOPOLOGIES, "narrative_topology"),
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    set(vocabulary),
                    {no_narrative[field]["const"]} | set(properties[field]["enum"]),
                )
        self.assertEqual(
            creation_vocabulary.CREATION_IDENTIFIER_PATTERN,
            schema["$defs"]["creationScaffoldIdentifier"]["pattern"],
        )
        self.assertEqual(
            set(creation_vocabulary.CREATION_PROJECT_KINDS),
            set(studio_contracts.CREATION_PROJECT_KINDS),
        )

    def test_studio_v3_protocol_is_kind_aware_without_broadening_v1_or_v2(self) -> None:
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "kind": "request",
            "request_id": "kind_aware_create",
            "method": "creation_workspace.create",
            "params": {
                "workspace_id": "workspace_game",
                "grant_id": "grant_game",
                "expected_grant_generation": 0,
                "project_kind": "game",
                "project_id": "neutral_game",
                "title": "Neutral game",
                "default_locale": "en",
                "project_version": "0.1.0",
                "gameplay_family": "puzzle",
                "initial_core_verb": "solve",
                "initial_core_loop": "inspect and solve",
                "world_presence": "none",
                "narrative_requirement": "none",
                "narrative_authorship": "none",
                "narrative_topology": "none",
                "presentation_mode": "2d",
                "runtime_support_intent": "authoring_only",
            },
        }
        expected_v3_bytes = (
            '{"kind":"request","method":"creation_workspace.create","params":{'
            '"default_locale":"en","expected_grant_generation":0,"gameplay_family":"puzzle",'
            '"grant_id":"grant_game","initial_core_loop":"inspect and solve",'
            '"initial_core_verb":"solve","narrative_authorship":"none",'
            '"narrative_requirement":"none","narrative_topology":"none",'
            '"presentation_mode":"2d","project_id":"neutral_game","project_kind":"game",'
            '"project_version":"0.1.0","runtime_support_intent":"authoring_only",'
            '"title":"Neutral game","workspace_id":"workspace_game","world_presence":"none"},'
            '"protocol":"rpg-world-forge.studio_protocol","protocol_version":3,'
            '"request_id":"kind_aware_create"}'
        )
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        self.assertEqual(expected_v3_bytes, encode_json(request))

        rejected = copy.deepcopy(request)
        rejected["params"]["asset_content_mode"] = "not_applicable"
        with self.assertRaisesRegex(ValueError, "asset_content_mode"):
            validate_studio_protocol_envelope(rejected)
        with self.assertRaisesRegex(ValueError, "asset_content_mode"):
            validate_studio_protocol_envelope({**rejected, "protocol_version": 3})
        with self.assertRaisesRegex(ValueError, "not available"):
            validate_studio_protocol_envelope({**request, "protocol_version": 2})

    def test_creation_content_modes_are_generated_from_profile_schema(self) -> None:
        from scripts import generate_creation_content_modes

        profile_schema = json.loads((ROOT / "schemas/creation-profile.schema.json").read_text())
        expected = tuple(profile_schema["$defs"]["productionMode"]["enum"])
        artifacts = generate_creation_content_modes.build_generated_artifacts()

        self.assertIn(
            Path("src/worldforge/generated_creation_content_modes.py"),
            artifacts,
        )
        self.assertEqual(0, generate_creation_content_modes.main(["--check"]))
        from worldforge import generated_creation_content_modes

        self.assertEqual(expected, generated_creation_content_modes.CREATION_CONTENT_MODES)
        self.assertEqual(expected, creation_vocabulary.CREATION_CONTENT_MODES)

    def test_studio_v5_workspace_create_adds_game_only_asset_mode(self) -> None:
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 5,
            "kind": "request",
            "request_id": "asset_mode_create",
            "method": "creation_workspace.create",
            "params": {
                "workspace_id": "workspace_game",
                "grant_id": "grant_game",
                "expected_grant_generation": 0,
                "project_kind": "game",
                "project_id": "neutral_game",
                "title": "Neutral game",
                "default_locale": "en",
                "project_version": "0.1.0",
                "gameplay_family": "puzzle",
                "initial_core_verb": "solve",
                "initial_core_loop": "inspect and solve",
                "world_presence": "none",
                "narrative_requirement": "none",
                "narrative_authorship": "none",
                "narrative_topology": "none",
                "presentation_mode": "2d",
                "runtime_support_intent": "authoring_only",
                "asset_content_mode": "not_applicable",
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        omitted = copy.deepcopy(request)
        del omitted["params"]["asset_content_mode"]
        self.assertEqual(omitted, validate_studio_protocol_envelope(omitted))
        library = copy.deepcopy(request)
        library["params"] = {
            "workspace_id": "workspace_library",
            "grant_id": "grant_library",
            "expected_grant_generation": 0,
            "project_kind": "universe_library",
            "project_id": "neutral_library",
            "title": "Neutral library",
            "default_locale": "en",
            "project_version": "0.1.0",
            "asset_content_mode": "authored",
        }
        with self.assertRaisesRegex(ValueError, "library projects cannot include game facets"):
            validate_studio_protocol_envelope(library)
        mismatch = copy.deepcopy(request)
        mismatch["protocol_version"] = 4
        with self.assertRaisesRegex(ValueError, "not available"):
            validate_studio_protocol_envelope(mismatch)

    def test_game_scaffold_defaults_assets_to_authored_but_accepts_explicit_not_applicable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            default_root = base / "default-game"
            explicit_root = base / "authoring-only-game"
            create_creation_project(
                default_root,
                project_id="default_game",
                title="Default game",
                **_game_options(),
            )
            create_creation_project(
                explicit_root,
                project_id="authoring_only_game",
                title="Authoring only game",
                **_game_options(asset_content_mode="not_applicable"),
            )

            default = load_creation_project(default_root / "project.json")
            explicit = load_creation_project(explicit_root / "project.json")

            self.assertEqual("authored", default.profile["production"]["content_modes"]["assets"])
            self.assertEqual(
                "not_applicable",
                explicit.profile["production"]["content_modes"]["assets"],
            )
            self.assertEqual(1, len(default.activity_modules))
            self.assertEqual(1, len(default.logic_modules))
            self.assertEqual(1, len(explicit.activity_modules))
            self.assertEqual(1, len(explicit.logic_modules))
            self.assertEqual(default.profile["runtime_target"], explicit.profile["runtime_target"])
            self.assertEqual([], explicit.profile["runtime_target"]["accepted_logic_formats"])
            self.assertEqual([], explicit.profile["runtime_target"]["required_features"])
            self.assertEqual([], explicit.profile["runtime_target"]["input_capabilities"])
            readiness = build_creation_readiness(explicit)
            self.assertNotIn("assets_not_sealed", readiness["blocker_reason_codes"])

            with self.assertRaisesRegex(CreationScaffoldError, "asset_content_mode"):
                create_creation_project(
                    base / "bad-game",
                    project_id="bad_game",
                    title="Bad game",
                    **_game_options(asset_content_mode="unknown"),
                )
            with self.assertRaisesRegex(CreationScaffoldError, "does not accept"):
                create_creation_project(
                    base / "bad-library",
                    project_id="bad_library",
                    title="Bad library",
                    project_kind="universe_library",
                    asset_content_mode="authored",
                )

    def test_asset_content_mode_never_changes_game_runtime_target_for_same_intent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            pairs = (
                ("authoring_only", "none"),
                ("compatibility_assessment", "compat"),
            )
            for runtime_intent, suffix in pairs:
                authored_root = base / f"authored-{suffix}"
                not_applicable_root = base / f"not-applicable-{suffix}"
                create_creation_project(
                    authored_root,
                    project_id=f"authored_{suffix}",
                    title="Authored assets",
                    **_game_options(runtime_support_intent=runtime_intent),
                )
                create_creation_project(
                    not_applicable_root,
                    project_id=f"not_applicable_{suffix}",
                    title="No asset applicability",
                    **_game_options(
                        runtime_support_intent=runtime_intent,
                        asset_content_mode="not_applicable",
                    ),
                )
                authored = load_creation_project(authored_root / "project.json")
                not_applicable = load_creation_project(not_applicable_root / "project.json")

                with self.subTest(runtime_intent=runtime_intent):
                    self.assertEqual(
                        authored.profile["runtime_target"],
                        not_applicable.profile["runtime_target"],
                    )
                    self.assertEqual(1, len(not_applicable.activity_modules))
                    self.assertEqual(1, len(not_applicable.logic_modules))

    def test_required_branching_narrative_and_abstract_world_are_explicit_neutral_seeds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "branching"
            create_creation_project(
                root,
                project_id="branching_seed",
                title="Branching seed",
                **_game_options(
                    gameplay_family="narrative",
                    initial_core_verb="choose",
                    initial_core_loop="read, choose, and observe the recorded consequence",
                    world_presence="abstract",
                    narrative_requirement="required",
                    narrative_authorship="authored",
                    narrative_topology="branching",
                    presentation_mode="text",
                    runtime_support_intent="compatibility_assessment",
                ),
            )
            loaded = load_creation_project(root / "project.json")

            self.assertEqual("abstract", loaded.profile["world"]["presence"])
            self.assertEqual("required", loaded.profile["narrative"]["requirement"])
            self.assertEqual("branching", loaded.profile["narrative"]["topology"])
            self.assertEqual(
                ["choice", "ending", "ending"],
                [unit["unit_type"] for unit in loaded.narrative_units],
            )
            self.assertIsNone(loaded.profile["runtime_target"]["requested_adapter"])
            self.assertEqual(
                ["platform:linux_x86_64", "platform:windows_x86_64"],
                loaded.profile["runtime_target"]["platforms"],
            )

    def test_invalid_kind_facet_combinations_fail_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            cases = (
                {"project_kind": "game"},
                _game_options(narrative_requirement="required"),
                {
                    "project_kind": "asset_library",
                    "gameplay_family": "puzzle",
                },
                _game_options(
                    narrative_requirement="none",
                    narrative_authorship="authored",
                ),
            )
            for index, options in enumerate(cases):
                target = base / f"invalid-{index}"
                with self.subTest(options=options):
                    with self.assertRaises(CreationScaffoldError) as raised:
                        create_creation_project(
                            target,
                            project_id=f"invalid_{index}",
                            title="Invalid",
                            **options,
                        )
                    self.assertEqual(
                        "creation_scaffold_inputs_invalid",
                        raised.exception.reason_code,
                    )
                    self.assertFalse(target.exists())

    def test_cli_supports_kind_facets_and_returns_exit_two_for_cross_field_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "cli-game"
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
                        "cli_game",
                        "--title",
                        "CLI game",
                        "--kind",
                        "game",
                        "--gameplay-family",
                        "puzzle",
                        "--core-verb",
                        "solve",
                        "--core-loop",
                        "inspect and solve",
                        "--world-presence",
                        "none",
                        "--narrative-requirement",
                        "none",
                        "--presentation-mode",
                        "2d",
                        "--runtime-support-intent",
                        "authoring_only",
                        "--asset-content-mode",
                        "not_applicable",
                        "--json",
                    ],
                ),
            ):
                self.assertEqual(0, main())
            result = json.loads(stdout.getvalue())
            self.assertEqual("game", result["project"]["project_kind"])
            self.assertEqual(
                "not_applicable",
                result["project"]["profile"]["content_hash"]
                and load_creation_project(target / "project.json").profile["production"][
                    "content_modes"
                ]["assets"],
            )
            self.assertEqual("generic", result["route"])

            stderr = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "new-creation",
                        str(Path(temp) / "invalid"),
                        "--id",
                        "invalid_game",
                        "--title",
                        "Invalid game",
                        "--kind",
                        "game",
                        "--json",
                    ],
                ),
            ):
                self.assertEqual(2, main())
            error = json.loads(stderr.getvalue())
            self.assertEqual("creation_scaffold_inputs_invalid", error["reason_code"])

            stderr = io.StringIO()
            with (
                contextlib.redirect_stderr(stderr),
                self.assertRaises(SystemExit) as invalid_asset_mode,
                mock.patch(
                    "sys.argv",
                    [
                        "worldforge",
                        "new-creation",
                        str(Path(temp) / "invalid-asset-mode"),
                        "--id",
                        "invalid_asset_mode",
                        "--title",
                        "Invalid asset mode",
                        "--kind",
                        "game",
                        "--gameplay-family",
                        "puzzle",
                        "--core-verb",
                        "solve",
                        "--core-loop",
                        "inspect and solve",
                        "--world-presence",
                        "none",
                        "--narrative-requirement",
                        "none",
                        "--presentation-mode",
                        "2d",
                        "--runtime-support-intent",
                        "authoring_only",
                        "--asset-content-mode",
                        "unknown",
                    ],
                ),
            ):
                main()
            self.assertEqual(2, invalid_asset_mode.exception.code)
            self.assertIn("invalid choice", stderr.getvalue())

            stdout = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                self.assertRaises(SystemExit) as raised,
                mock.patch("sys.argv", ["worldforge", "new-creation", "--help"]),
            ):
                main()
            self.assertEqual(0, raised.exception.code)
            self.assertIn("--asset-content-mode", stdout.getvalue())

    def test_studio_manager_uses_the_same_kind_aware_scaffold_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            for project_kind, extra in (
                ("asset_library", {}),
                ("game", _game_options(asset_content_mode="not_applicable")),
            ):
                target = base / project_kind
                with StudioStore(base / f"studio-{project_kind}") as store:
                    grants = CreationRootGrantManager(store)
                    grant = grants.create(
                        {
                            "grant_id": f"grant_{project_kind}",
                            "role": "new_target",
                            "display_name": project_kind,
                            "path": str(target),
                            "expected_project_hash": None,
                        }
                    )
                    manager = CreationWorkspaceManager(store, grants=grants)
                    workspace = manager.create(
                        {
                            "workspace_id": f"workspace_{project_kind}",
                            "grant_id": grant["grant_id"],
                            "expected_grant_generation": grant["generation"],
                            "project_kind": project_kind,
                            "project_id": f"{project_kind}_project",
                            "title": f"{project_kind} project",
                            "default_locale": "en",
                            "project_version": "0.1.0",
                            **{key: value for key, value in extra.items() if key != "project_kind"},
                        }
                    )
                    self.assertEqual(project_kind, workspace["project_kind"])
                    opened = manager.open(workspace["workspace_id"])
                    self.assertEqual(project_kind, opened["project_kind"])
                    self.assertEqual(
                        project_kind,
                        load_creation_project(target / "project.json").project["project_kind"],
                    )
                    if project_kind == "game":
                        self.assertEqual(
                            extra.get("asset_content_mode", "authored"),
                            load_creation_project(target / "project.json").profile["production"][
                                "content_modes"
                            ]["assets"],
                        )

    def test_studio_recovery_normalizes_only_stored_legacy_universe_specs(self) -> None:
        class SimulatedCrash(BaseException):
            pass

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            with StudioStore(base / "studio") as store:
                grants = CreationRootGrantManager(store)
                grant = grants.create(
                    {
                        "grant_id": "grant_legacy_recovery",
                        "role": "new_target",
                        "display_name": "Legacy recovery",
                        "path": str(base / "legacy-recovery"),
                        "expected_project_hash": None,
                    }
                )
                request = {
                    "workspace_id": "workspace_legacy_recovery",
                    "grant_id": grant["grant_id"],
                    "expected_grant_generation": grant["generation"],
                    "project_kind": "universe_library",
                    "project_id": "legacy_recovery",
                    "title": "Legacy recovery",
                    "default_locale": "en",
                    "project_version": "0.1.0",
                }

                def crash(phase: str, _context: dict[str, object]) -> None:
                    if phase == "reservation_committed":
                        raise SimulatedCrash(phase)

                with self.assertRaises(SimulatedCrash):
                    CreationWorkspaceManager(
                        store,
                        grants=grants,
                        transition_hook=crash,
                    ).create(request)
                legacy_spec = dict(request)
                legacy_spec.pop("project_kind")
                with store.connection:
                    store.connection.execute(
                        "UPDATE creation_root_grants SET creation_spec_json = ? WHERE grant_id = ?",
                        (encode_json(legacy_spec), grant["grant_id"]),
                    )

                recovered = CreationWorkspaceManager(store, grants=grants).recover(
                    request["workspace_id"],
                    expected_root_generation=0,
                )
                self.assertEqual("complete", recovered["state"])
                self.assertEqual("universe_library", recovered["workspace"]["project_kind"])
                self.assertEqual(
                    "universe_library",
                    load_creation_project(base / "legacy-recovery/project.json").project[
                        "project_kind"
                    ],
                )
                with self.assertRaisesRegex(StudioError, "project_kind"):
                    CreationWorkspaceManager._create_params(
                        {
                            **legacy_spec,
                            "expected_grant_generation": grants.get(grant["grant_id"])[
                                "generation"
                            ],
                        }
                    )


if __name__ == "__main__":
    unittest.main()
