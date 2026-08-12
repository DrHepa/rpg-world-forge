from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

from worldforge import creation_contracts as creation_contracts_module
from worldforge.creation_contracts import (
    CREATION_PROFILE_FORMAT,
    CreationContractError,
    canonical_creation_hash,
    load_creation_project,
    read_creation_object,
    validate_creation_document,
    validate_creation_documents,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples" / "multigenre-contracts"


def _with_hash(document: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(document)
    value["content_hash"] = canonical_creation_hash(value)
    return value


class MultiGenreContractTests(unittest.TestCase):
    def test_abstract_puzzle_loads_without_world_actors_or_narrative(self) -> None:
        loaded = load_creation_project(FIXTURES / "abstract-puzzle" / "project.json")

        self.assertEqual("world-forge.project", loaded.project["format"])
        self.assertEqual("game", loaded.project["project_kind"])
        self.assertEqual("none", loaded.profile["world"]["presence"])
        self.assertEqual("none", loaded.profile["narrative"]["requirement"])
        self.assertEqual((), loaded.world_modules)
        self.assertEqual((), loaded.narrative_modules)
        self.assertEqual(["puzzle"], [item["activity_type"] for item in loaded.activities])
        self.assertEqual(["rule"], [item["system_type"] for item in loaded.systems])

    def test_neutral_fixtures_cover_universe_and_branching_narrative_modules(self) -> None:
        puzzle = load_creation_project(FIXTURES / "abstract-puzzle" / "project.json")
        universe_project = _with_hash({**puzzle.project, "project_kind": "universe_library"})
        asset_project = _with_hash({**puzzle.project, "project_kind": "asset_library"})
        world_module = validate_creation_document(
            read_creation_object(FIXTURES / "universe-library" / "source" / "world" / "canon.json")
        )
        narrative = load_creation_project(FIXTURES / "branching-narrative" / "project.json")

        self.assertEqual(
            "universe_library", validate_creation_document(universe_project)["project_kind"]
        )
        self.assertEqual("asset_library", validate_creation_document(asset_project)["project_kind"])
        self.assertEqual("canon", world_module["module_type"])
        self.assertEqual("required", narrative.profile["narrative"]["requirement"])
        self.assertEqual(
            ["choice", "ending", "ending"],
            [item["unit_type"] for item in narrative.narrative_units],
        )

    def test_library_projects_can_explicitly_have_no_gameplay(self) -> None:
        loaded = load_creation_project(FIXTURES / "abstract-puzzle" / "project.json")
        profile = copy.deepcopy(loaded.profile)
        profile["gameplay"] = {
            "challenge_model": "none",
            "core_loop": [],
            "core_verbs": [],
            "dependencies": {
                "authored": [],
                "procedural": [],
                "systemic": [],
            },
            "failure_recovery": "none",
            "goal_model": "none",
            "mechanic_tags": [],
            "player_role": "none",
            "primary_family": "none",
            "progression": "none",
            "rule_model": "none",
            "secondary_families": [],
            "session_structure": "none",
            "social_topology": "none",
            "teleology": "none",
        }
        profile = _with_hash(profile)
        manifest = copy.deepcopy(loaded.manifest)
        manifest["profile"]["content_hash"] = profile["content_hash"]
        for collection in manifest["modules"].values():
            collection.clear()
        manifest = _with_hash(manifest)

        for project_kind in ("universe_library", "asset_library"):
            with self.subTest(project_kind=project_kind):
                project = copy.deepcopy(loaded.project)
                project["project_kind"] = project_kind
                project["profile"]["content_hash"] = profile["content_hash"]
                project["source_manifest"]["content_hash"] = manifest["content_hash"]
                project = _with_hash(project)
                validated = validate_creation_documents(
                    project,
                    profile,
                    manifest,
                    (),
                    (),
                    (),
                    (),
                )
                self.assertEqual(project_kind, validated.project["project_kind"])

    def test_library_source_identities_are_globally_unique_before_kind_exit(self) -> None:
        loaded = load_creation_project(FIXTURES / "abstract-puzzle" / "project.json")
        profile = copy.deepcopy(loaded.profile)
        profile["gameplay"] = {
            "challenge_model": "none",
            "core_loop": [],
            "core_verbs": [],
            "dependencies": {
                "authored": [],
                "procedural": [],
                "systemic": [],
            },
            "failure_recovery": "none",
            "goal_model": "none",
            "mechanic_tags": [],
            "player_role": "none",
            "primary_family": "none",
            "progression": "none",
            "rule_model": "none",
            "secondary_families": [],
            "session_structure": "none",
            "social_topology": "none",
            "teleology": "none",
        }
        profile = _with_hash(profile)

        original = loaded.activity_modules[0]
        duplicate = copy.deepcopy(original)
        duplicate["module_id"] = "z_duplicate_activities"
        duplicate = _with_hash(duplicate)
        manifest = copy.deepcopy(loaded.manifest)
        manifest["profile"]["content_hash"] = profile["content_hash"]
        manifest["modules"]["logic_modules"] = []
        manifest["modules"]["activity_modules"].append(
            {
                "content_hash": duplicate["content_hash"],
                "format": duplicate["format"],
                "format_version": duplicate["format_version"],
                "id": duplicate["module_id"],
                "path": "activities/duplicate.json",
            }
        )
        manifest["modules"]["activity_modules"].sort(key=lambda item: item["id"].encode("utf-8"))
        manifest = _with_hash(manifest)
        project = copy.deepcopy(loaded.project)
        project["project_kind"] = "asset_library"
        project["profile"]["content_hash"] = profile["content_hash"]
        project["source_manifest"]["content_hash"] = manifest["content_hash"]
        project = _with_hash(project)

        with self.assertRaisesRegex(
            CreationContractError,
            "global activity ID.*collision|global source ID.*collision",
        ):
            validate_creation_documents(
                project,
                profile,
                manifest,
                loaded.world_modules,
                (original, duplicate),
                loaded.narrative_modules,
                loaded.system_modules,
                (),
            )

        system = copy.deepcopy(loaded.system_modules[0])
        system["systems"][0]["id"] = original["activities"][0]["id"]
        system = _with_hash(system)
        cross_kind_manifest = copy.deepcopy(manifest)
        cross_kind_manifest["modules"]["activity_modules"] = [
            cross_kind_manifest["modules"]["activity_modules"][0]
        ]
        cross_kind_manifest["modules"]["system_modules"][0]["content_hash"] = system["content_hash"]
        cross_kind_manifest = _with_hash(cross_kind_manifest)
        cross_kind_project = copy.deepcopy(project)
        cross_kind_project["source_manifest"]["content_hash"] = cross_kind_manifest["content_hash"]
        cross_kind_project = _with_hash(cross_kind_project)
        with self.assertRaisesRegex(
            CreationContractError,
            "global source ID.*collision",
        ):
            validate_creation_documents(
                cross_kind_project,
                profile,
                cross_kind_manifest,
                loaded.world_modules,
                (original,),
                loaded.narrative_modules,
                (system,),
                (),
            )

    def test_strict_reader_rejects_ambiguous_or_unsafe_json(self) -> None:
        samples = (
            (b'{"format":"a","format":"b"}\n', "duplicate JSON object key"),
            (b'{"value":NaN}\n', "non-finite JSON number"),
            (b'{"value":1e9999}\n', "non-finite JSON number"),
            (b'{"value":1.0}\n', "decimal or exponent"),
            (b'{"value":1e0}\n', "decimal or exponent"),
            (b'{"value":9007199254740992}\n', "JavaScript-safe integer"),
            (b'{"value":"\\ud800"}\n', "Unicode scalar"),
            (b"\xef\xbb\xbf{}", "UTF-8 BOM"),
            (b"\xff", "UTF-8"),
            (b"[]\n", "JSON object"),
        )
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "contract.json"
            for payload, expected in samples:
                with self.subTest(expected=expected):
                    source.write_bytes(payload)
                    with self.assertRaisesRegex(CreationContractError, expected):
                        read_creation_object(source)

    def test_strict_reader_rejects_symbolic_and_hard_links(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target.json"
            target.write_text("{}\n", encoding="utf-8")

            with self.subTest("hardlink"):
                hardlink = root / "hardlink.json"
                try:
                    os.link(target, hardlink)
                except (OSError, NotImplementedError):
                    pass
                else:
                    with self.assertRaisesRegex(CreationContractError, "standalone regular file"):
                        read_creation_object(hardlink)
                    hardlink.unlink()

            with self.subTest("symlink"):
                symlink = root / "symlink.json"
                try:
                    symlink.symlink_to(target)
                except (OSError, NotImplementedError):
                    pass
                else:
                    with self.assertRaisesRegex(CreationContractError, "standalone regular file"):
                        read_creation_object(symlink)

    def test_public_reader_pins_ancestors_and_rejects_mid_read_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project_root = root / "project"
            project_root.mkdir()
            source = project_root / "contract.json"
            source.write_text('{"value":1}\n', encoding="utf-8")

            with self.subTest("linked ancestor"):
                alias = root / "alias"
                try:
                    alias.symlink_to(project_root, target_is_directory=True)
                except (OSError, NotImplementedError):
                    pass
                else:
                    with self.assertRaisesRegex(
                        CreationContractError,
                        "ancestor|symbolic link|safe snapshot",
                    ):
                        read_creation_object(alias / "contract.json")

            with self.subTest("mutation before return"):
                original = creation_contracts_module.read_workspace_file_snapshot
                calls = 0

                def changing_snapshot(*args: object, **kwargs: object) -> bytes:
                    nonlocal calls
                    payload = original(*args, **kwargs)
                    calls += 1
                    if calls == 1:
                        source.write_text('{"value":2}\n', encoding="utf-8")
                    return payload

                with mock.patch.object(
                    creation_contracts_module,
                    "read_workspace_file_snapshot",
                    side_effect=changing_snapshot,
                ):
                    with self.assertRaisesRegex(CreationContractError, "changed while reading"):
                        read_creation_object(source)

            with self.subTest("same-byte identity replacement"):
                original = creation_contracts_module.read_workspace_file_snapshot
                calls = 0

                def replacing_snapshot(*args: object, **kwargs: object) -> bytes:
                    nonlocal calls
                    payload = original(*args, **kwargs)
                    calls += 1
                    if calls == 1:
                        replacement = source.with_suffix(".replacement")
                        replacement.write_bytes(payload)
                        os.replace(replacement, source)
                    return payload

                with mock.patch.object(
                    creation_contracts_module,
                    "read_workspace_file_snapshot",
                    side_effect=replacing_snapshot,
                ):
                    with self.assertRaisesRegex(CreationContractError, "identity changed"):
                        read_creation_object(source)

    def test_raw_and_in_memory_json_depth_are_bounded(self) -> None:
        depth = creation_contracts_module.MAX_CREATION_JSON_DEPTH + 1
        nested: object = "leaf"
        for _ in range(depth):
            nested = {"child": nested}

        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "deep.json"
            source.write_text(json.dumps(nested), encoding="utf-8")
            with self.assertRaisesRegex(CreationContractError, "JSON depth"):
                read_creation_object(source)

        with self.assertRaisesRegex(CreationContractError, "JSON depth"):
            canonical_creation_hash({"nested": nested})

        cyclic: list[object] = []
        cyclic.append(cyclic)
        with self.assertRaisesRegex(CreationContractError, "cycle|encode"):
            canonical_creation_hash({"cycle": cyclic})

        with self.assertRaisesRegex(CreationContractError, "encode strict creation JSON"):
            canonical_creation_hash({"unsupported": object()})

    def test_contract_identity_is_exact_hashed_and_boolean_safe(self) -> None:
        profile = read_creation_object(FIXTURES / "abstract-puzzle" / "profile.json")

        with self.subTest("boolean version"):
            mutated = {**profile, "format_version": True}
            with self.assertRaisesRegex(CreationContractError, "format or format_version"):
                validate_creation_document(mutated)

        with self.subTest("content hash"):
            mutated = {**profile, "title": "Changed without resealing"}
            with self.assertRaisesRegex(CreationContractError, "content hash"):
                validate_creation_document(mutated)

        with self.subTest("unknown field"):
            mutated = _with_hash({**profile, "legacy_genre": "rpg"})
            with self.assertRaisesRegex(CreationContractError, "unknown fields"):
                validate_creation_document(mutated)

        with self.subTest("wrong expected format"):
            with self.assertRaisesRegex(CreationContractError, "expected format"):
                validate_creation_document(profile, expected_format="world-forge.world_module")

    def test_all_contract_integers_are_javascript_safe(self) -> None:
        profile = read_creation_object(FIXTURES / "abstract-puzzle" / "profile.json")

        with self.subTest("extension version"):
            mutated = copy.deepcopy(profile)
            mutated["extensions"] = [
                {
                    "id": "example.optional-extension",
                    "version": 9_007_199_254_740_992,
                    "required": False,
                    "content_hash": "0" * 64,
                }
            ]
            with self.assertRaisesRegex(CreationContractError, "JavaScript-safe integer"):
                validate_creation_document(_with_hash(mutated))

        with self.subTest("accepted logic version"):
            mutated = copy.deepcopy(profile)
            mutated["runtime_target"]["accepted_logic_formats"][0]["versions"] = [
                9_007_199_254_740_992
            ]
            with self.assertRaisesRegex(CreationContractError, "JavaScript-safe integer"):
                validate_creation_document(_with_hash(mutated))

        with self.subTest("chronology sequence"):
            canon = read_creation_object(
                FIXTURES / "universe-library" / "source" / "world" / "canon.json"
            )
            chronology = {
                "format": canon["format"],
                "format_version": canon["format_version"],
                "module_id": "unsafe_chronology",
                "project_id": canon["project_id"],
                "module_type": "chronology",
                "title": "Unsafe chronology",
                "events": [
                    {
                        "id": "event_one",
                        "sequence": 9_007_199_254_740_992,
                        "summary": "Outside the exact JavaScript integer range.",
                    }
                ],
                "extensions": [],
            }
            with self.assertRaisesRegex(CreationContractError, "JavaScript-safe integer"):
                validate_creation_document(_with_hash(chronology))

    def test_paths_ids_and_references_are_portable_and_collision_free(self) -> None:
        manifest = read_creation_object(FIXTURES / "abstract-puzzle" / "source" / "manifest.json")

        with self.subTest("backslash path"):
            mutated = copy.deepcopy(manifest)
            mutated["modules"]["activity_modules"][0]["path"] = "activities\\puzzle.json"
            with self.assertRaisesRegex(CreationContractError, "portable relative path"):
                validate_creation_document(_with_hash(mutated))

        with self.subTest("NFD path"):
            mutated = copy.deepcopy(manifest)
            mutated["modules"]["activity_modules"][0]["path"] = unicodedata.normalize(
                "NFD", "activities/café.json"
            )
            with self.assertRaisesRegex(CreationContractError, "NFC"):
                validate_creation_document(_with_hash(mutated))

        with self.subTest("casefold collision"):
            mutated = copy.deepcopy(manifest)
            duplicate = copy.deepcopy(mutated["modules"]["activity_modules"][0])
            duplicate["id"] = "second_puzzle"
            duplicate["path"] = duplicate["path"].upper()
            mutated["modules"]["activity_modules"].append(duplicate)
            with self.assertRaisesRegex(CreationContractError, "NFC/casefold collision"):
                validate_creation_document(_with_hash(mutated))

        with self.subTest("reserved identity"):
            mutated = copy.deepcopy(manifest)
            mutated["project_id"] = "con"
            with self.assertRaisesRegex(CreationContractError, "portable lowercase ID"):
                validate_creation_document(_with_hash(mutated))

        project = read_creation_object(FIXTURES / "abstract-puzzle" / "project.json")
        with self.subTest("path depth"):
            mutated = copy.deepcopy(project)
            mutated["profile"]["path"] = "/".join(["nested"] * 17 + ["profile.json"])
            with self.assertRaisesRegex(CreationContractError, "path depth"):
                validate_creation_document(_with_hash(mutated))

        with self.subTest("path bytes"):
            mutated = copy.deepcopy(project)
            mutated["profile"]["path"] = "/".join(["a" * 200] * 6) + "/profile.json"
            with self.assertRaisesRegex(CreationContractError, "path byte"):
                validate_creation_document(_with_hash(mutated))

    def test_none_facets_forbid_invented_world_and_narrative_modules(self) -> None:
        loaded = load_creation_project(FIXTURES / "abstract-puzzle" / "project.json")
        world_module = read_creation_object(
            FIXTURES / "universe-library" / "source" / "world" / "canon.json"
        )
        narrative_module = read_creation_object(
            FIXTURES / "branching-narrative" / "source" / "narrative" / "branching.json"
        )

        with self.subTest("world none"):
            world_module = _with_hash({**world_module, "project_id": "abstract_puzzle"})
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
            manifest = _with_hash(manifest)
            project = copy.deepcopy(loaded.project)
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _with_hash(project)
            with self.assertRaisesRegex(CreationContractError, "world:none"):
                validate_creation_documents(
                    project,
                    loaded.profile,
                    manifest,
                    (world_module,),
                    loaded.activity_modules,
                    (),
                    loaded.system_modules,
                    loaded.logic_modules,
                )

        with self.subTest("narrative none"):
            narrative_module = _with_hash({**narrative_module, "project_id": "abstract_puzzle"})
            manifest = copy.deepcopy(loaded.manifest)
            manifest["modules"]["narrative_modules"] = [
                {
                    "format": narrative_module["format"],
                    "format_version": narrative_module["format_version"],
                    "id": narrative_module["module_id"],
                    "path": "narrative/branching.json",
                    "content_hash": narrative_module["content_hash"],
                }
            ]
            manifest = _with_hash(manifest)
            project = copy.deepcopy(loaded.project)
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _with_hash(project)
            with self.assertRaisesRegex(CreationContractError, "narrative:none"):
                validate_creation_documents(
                    project,
                    loaded.profile,
                    manifest,
                    (),
                    loaded.activity_modules,
                    (narrative_module,),
                    loaded.system_modules,
                    loaded.logic_modules,
                )

    def test_none_facets_forbid_actor_spatial_and_quest_semantics(self) -> None:
        loaded = load_creation_project(FIXTURES / "abstract-puzzle" / "project.json")

        for field in ("participant_ids", "spatial_context_ids"):
            with self.subTest(field=field):
                activity_module = copy.deepcopy(loaded.activity_modules[0])
                activity_module["activities"][0][field] = ["invented_reference"]
                activity_module = _with_hash(activity_module)
                manifest = copy.deepcopy(loaded.manifest)
                manifest["modules"]["activity_modules"][0]["content_hash"] = activity_module[
                    "content_hash"
                ]
                manifest = _with_hash(manifest)
                project = copy.deepcopy(loaded.project)
                project["source_manifest"]["content_hash"] = manifest["content_hash"]
                project = _with_hash(project)
                with self.assertRaisesRegex(CreationContractError, "world:none"):
                    validate_creation_documents(
                        project,
                        loaded.profile,
                        manifest,
                        (),
                        (activity_module,),
                        (),
                        loaded.system_modules,
                        loaded.logic_modules,
                    )

        activity_module = copy.deepcopy(loaded.activity_modules[0])
        activity_module["activities"][0]["activity_type"] = "quest"
        activity_module = _with_hash(activity_module)
        manifest = copy.deepcopy(loaded.manifest)
        manifest["modules"]["activity_modules"][0]["content_hash"] = activity_module["content_hash"]
        manifest = _with_hash(manifest)
        project = copy.deepcopy(loaded.project)
        project["source_manifest"]["content_hash"] = manifest["content_hash"]
        project = _with_hash(project)
        with self.assertRaisesRegex(CreationContractError, "narrative:none"):
            validate_creation_documents(
                project,
                loaded.profile,
                manifest,
                (),
                (activity_module,),
                (),
                loaded.system_modules,
                loaded.logic_modules,
            )

    def test_required_narrative_requires_units_and_graph_is_globally_closed(self) -> None:
        loaded = load_creation_project(FIXTURES / "branching-narrative" / "project.json")

        with self.subTest("required but empty"):
            manifest = copy.deepcopy(loaded.manifest)
            manifest["modules"]["narrative_modules"] = []
            manifest = _with_hash(manifest)
            project = copy.deepcopy(loaded.project)
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _with_hash(project)
            with self.assertRaisesRegex(CreationContractError, "narrative:required"):
                validate_creation_documents(
                    project,
                    loaded.profile,
                    manifest,
                    loaded.world_modules,
                    loaded.activity_modules,
                    (),
                    loaded.system_modules,
                    loaded.logic_modules,
                )

        with self.subTest("duplicate unit across modules"):
            first = loaded.narrative_modules[0]
            duplicate = _with_hash({**first, "module_id": "duplicate_branch"})
            manifest = copy.deepcopy(loaded.manifest)
            manifest["modules"]["narrative_modules"].append(
                {
                    "format": duplicate["format"],
                    "format_version": duplicate["format_version"],
                    "id": duplicate["module_id"],
                    "path": "narrative/duplicate.json",
                    "content_hash": duplicate["content_hash"],
                }
            )
            manifest = _with_hash(manifest)
            project = copy.deepcopy(loaded.project)
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _with_hash(project)
            with self.assertRaisesRegex(CreationContractError, "narrative unit ID"):
                validate_creation_documents(
                    project,
                    loaded.profile,
                    manifest,
                    loaded.world_modules,
                    loaded.activity_modules,
                    (first, duplicate),
                    loaded.system_modules,
                    loaded.logic_modules,
                )

        with self.subTest("dangling transition"):
            narrative = copy.deepcopy(loaded.narrative_modules[0])
            narrative["units"][0]["next_unit_ids"].append("missing_unit")
            narrative["units"][0]["options"].append(
                {
                    "condition_ids": [],
                    "effect_ids": [],
                    "id": "choose_missing",
                    "label": "Choose an invalid target",
                    "next_unit_id": "missing_unit",
                }
            )
            narrative = _with_hash(narrative)
            manifest = copy.deepcopy(loaded.manifest)
            manifest["modules"]["narrative_modules"][0]["content_hash"] = narrative["content_hash"]
            manifest = _with_hash(manifest)
            project = copy.deepcopy(loaded.project)
            project["source_manifest"]["content_hash"] = manifest["content_hash"]
            project = _with_hash(project)
            with self.assertRaisesRegex(CreationContractError, "missing narrative unit"):
                validate_creation_documents(
                    project,
                    loaded.profile,
                    manifest,
                    loaded.world_modules,
                    loaded.activity_modules,
                    (narrative,),
                    loaded.system_modules,
                    loaded.logic_modules,
                )

    def test_narrative_entries_reachability_and_transition_shapes_are_explicit(self) -> None:
        loaded = load_creation_project(FIXTURES / "branching-narrative" / "project.json")
        original = loaded.narrative_modules[0]

        with self.subTest("choice targets"):
            narrative = copy.deepcopy(original)
            narrative["units"][0]["next_unit_ids"] = ["ending_left"]
            with self.assertRaisesRegex(CreationContractError, "choice next_unit_ids"):
                validate_creation_document(_with_hash(narrative))

        with self.subTest("ending outgoing edge"):
            narrative = copy.deepcopy(original)
            narrative["units"][1]["next_unit_ids"] = ["ending_right"]
            with self.assertRaisesRegex(CreationContractError, "ending.*outgoing"):
                validate_creation_document(_with_hash(narrative))

        with self.subTest("undeclared root"):
            narrative = copy.deepcopy(original)
            narrative["units"].append(
                {
                    "asset_binding_ids": [],
                    "effect_ids": [],
                    "ending_kind": "neutral",
                    "id": "unlisted_root",
                    "next_unit_ids": [],
                    "prerequisite_ids": [],
                    "title": "Unlisted root",
                    "unit_type": "ending",
                }
            )
            with self.assertRaisesRegex(CreationContractError, "zero-indegree"):
                validate_creation_document(_with_hash(narrative))

        with self.subTest("unreachable cycle"):
            narrative = copy.deepcopy(original)
            narrative["units"].extend(
                [
                    {
                        "asset_binding_ids": [],
                        "effect_ids": [],
                        "id": "hidden_one",
                        "next_unit_ids": ["hidden_two"],
                        "prerequisite_ids": [],
                        "title": "Hidden one",
                        "unit_type": "scene",
                    },
                    {
                        "asset_binding_ids": [],
                        "effect_ids": [],
                        "id": "hidden_two",
                        "next_unit_ids": ["hidden_one"],
                        "prerequisite_ids": [],
                        "title": "Hidden two",
                        "unit_type": "scene",
                    },
                ]
            )
            with self.assertRaisesRegex(CreationContractError, "unreachable"):
                validate_creation_document(_with_hash(narrative))

        with self.subTest("unknown declared entry"):
            narrative = copy.deepcopy(original)
            narrative["entry_unit_ids"] = ["missing_entry"]
            with self.assertRaisesRegex(CreationContractError, "entry_unit_ids"):
                validate_creation_document(_with_hash(narrative))

        with self.subTest("multiple declared entries"):
            narrative = copy.deepcopy(original)
            narrative["entry_unit_ids"].append("second_entry")
            narrative["units"].append(
                {
                    "asset_binding_ids": [],
                    "effect_ids": [],
                    "ending_kind": "neutral",
                    "id": "second_entry",
                    "next_unit_ids": [],
                    "prerequisite_ids": [],
                    "title": "Second explicit entry",
                    "unit_type": "ending",
                }
            )
            validate_creation_document(_with_hash(narrative))

    def test_none_facets_close_production_dependencies_features_and_systems(self) -> None:
        loaded = load_creation_project(FIXTURES / "abstract-puzzle" / "project.json")

        for facet in ("world", "narrative"):
            with self.subTest(f"{facet} production"):
                profile = copy.deepcopy(loaded.profile)
                profile["production"]["content_modes"][facet] = "authored"
                with self.assertRaisesRegex(CreationContractError, f"{facet}:none.*not_applicable"):
                    validate_creation_document(_with_hash(profile))

            with self.subTest(f"{facet} dependency"):
                profile = copy.deepcopy(loaded.profile)
                profile["gameplay"]["dependencies"]["authored"].append(f"{facet}:invented")
                with self.assertRaisesRegex(CreationContractError, f"{facet}:none.*dependencies"):
                    validate_creation_document(_with_hash(profile))

            with self.subTest(f"{facet} feature"):
                profile = copy.deepcopy(loaded.profile)
                profile["runtime_target"]["required_features"].append(f"{facet}:invented")
                profile["runtime_target"]["required_features"].sort()
                with self.assertRaisesRegex(CreationContractError, f"{facet}:none.*features"):
                    validate_creation_document(_with_hash(profile))

        system_module = copy.deepcopy(loaded.system_modules[0])
        system_module["systems"][0]["system_type"] = "world_modifier"
        system_module = _with_hash(system_module)
        manifest = copy.deepcopy(loaded.manifest)
        manifest["modules"]["system_modules"][0]["content_hash"] = system_module["content_hash"]
        manifest = _with_hash(manifest)
        project = copy.deepcopy(loaded.project)
        project["source_manifest"]["content_hash"] = manifest["content_hash"]
        project = _with_hash(project)
        with self.assertRaisesRegex(CreationContractError, "world:none.*world_modifier"):
            validate_creation_documents(
                project,
                loaded.profile,
                manifest,
                (),
                loaded.activity_modules,
                (),
                (system_module,),
                loaded.logic_modules,
            )

    def test_project_locale_and_profile_presentation_are_coherent(self) -> None:
        loaded = load_creation_project(FIXTURES / "abstract-puzzle" / "project.json")

        project = _with_hash({**loaded.project, "default_locale": "fr"})
        with self.assertRaisesRegex(CreationContractError, "default locale.*source locale"):
            validate_creation_documents(
                project,
                loaded.profile,
                loaded.manifest,
                loaded.world_modules,
                loaded.activity_modules,
                loaded.narrative_modules,
                loaded.system_modules,
                loaded.logic_modules,
            )

        profile = copy.deepcopy(loaded.profile)
        profile["runtime_target"]["presentation_mode"] = "text"
        with self.assertRaisesRegex(CreationContractError, "presentation modes differ"):
            validate_creation_document(_with_hash(profile))

    def test_runtime_target_sets_are_sorted_unique_and_disjoint(self) -> None:
        profile = read_creation_object(FIXTURES / "abstract-puzzle" / "profile.json")

        with self.subTest("accepted format casefold collision"):
            mutated = copy.deepcopy(profile)
            mutated["runtime_target"]["accepted_logic_formats"].append(
                {"format": "WORLD-FORGE.GAMEPACK", "versions": [1]}
            )
            with self.assertRaisesRegex(CreationContractError, "NFC/casefold collision"):
                validate_creation_document(_with_hash(mutated))

        with self.subTest("required optional overlap"):
            mutated = copy.deepcopy(profile)
            mutated["runtime_target"]["optional_features"].append(
                mutated["runtime_target"]["required_features"][0]
            )
            with self.assertRaisesRegex(
                CreationContractError,
                "required_features.*optional_features",
            ):
                validate_creation_document(_with_hash(mutated))

        with self.subTest("canonical order"):
            mutated = copy.deepcopy(profile)
            mutated["runtime_target"]["required_features"] = list(
                reversed(mutated["runtime_target"]["required_features"])
            )
            with self.assertRaisesRegex(CreationContractError, "canonical sorted order"):
                validate_creation_document(_with_hash(mutated))

    def test_project_loader_pins_ancestors_and_enforces_aggregate_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = FIXTURES / "abstract-puzzle"
            copied = root / "project"
            shutil.copytree(source, copied)

            with self.subTest("hard-linked project"):
                hardlink = copied / "linked-project.json"
                try:
                    os.link(copied / "project.json", hardlink)
                except (OSError, NotImplementedError):
                    pass
                else:
                    try:
                        with self.assertRaisesRegex(
                            CreationContractError, "hard link|standalone regular file"
                        ):
                            load_creation_project(hardlink)
                    finally:
                        hardlink.unlink()

            with self.subTest("linked root"):
                alias = root / "alias"
                try:
                    alias.symlink_to(copied, target_is_directory=True)
                except (OSError, NotImplementedError):
                    pass
                else:
                    with self.assertRaisesRegex(
                        CreationContractError,
                        "symbolic link|reparse point",
                    ) as caught:
                        load_creation_project(alias / "project.json")
                    self.assertEqual(
                        "creation_project_root_linked",
                        caught.exception.reason_code,
                    )
                    self.assertNotIn(str(root), caught.exception.detail)

            with self.subTest("aggregate bytes"):
                with mock.patch.object(
                    creation_contracts_module,
                    "MAX_CREATION_AGGREGATE_BYTES",
                    100,
                    create=True,
                ):
                    with self.assertRaisesRegex(CreationContractError, "aggregate") as caught:
                        load_creation_project(copied / "project.json")
                    self.assertEqual(
                        "creation_project_aggregate_limit",
                        caught.exception.reason_code,
                    )
                    self.assertNotIn(str(copied), caught.exception.detail)

            with self.subTest("non-directory root"):
                non_directory = root / "not-a-directory"
                non_directory.write_bytes(b"not a directory")
                with self.assertRaisesRegex(CreationContractError, "real directory") as caught:
                    load_creation_project(non_directory / "project.json")
                self.assertEqual(
                    "creation_project_root_non_directory",
                    caught.exception.reason_code,
                )
                self.assertNotIn(str(non_directory), caught.exception.detail)

            with self.subTest("project file count"):
                with mock.patch.object(
                    creation_contracts_module,
                    "MAX_CREATION_PROJECT_FILES",
                    1,
                ):
                    with self.assertRaisesRegex(CreationContractError, "file project limit"):
                        load_creation_project(copied / "project.json")

            with self.subTest("individual file bytes"):
                with mock.patch.object(
                    creation_contracts_module,
                    "MAX_CREATION_CONTRACT_BYTES",
                    100,
                ):
                    with self.assertRaisesRegex(CreationContractError, "100-byte|100 bytes"):
                        load_creation_project(copied / "project.json")

    def test_extensions_are_namespaced_and_unknown_required_extensions_fail_closed(self) -> None:
        profile = read_creation_object(FIXTURES / "abstract-puzzle" / "profile.json")
        optional = _with_hash(
            {
                **profile,
                "extensions": [
                    {
                        "id": "example.experimental-mechanic",
                        "version": 1,
                        "required": False,
                        "content_hash": "0" * 64,
                    }
                ],
            }
        )
        validate_creation_document(optional)

        required = copy.deepcopy(optional)
        required["extensions"][0]["required"] = True
        required = _with_hash(required)
        with self.assertRaisesRegex(CreationContractError, "unknown required extension"):
            validate_creation_document(required)

        seen: list[str] = []
        validate_creation_document(
            required,
            registered_extensions={
                "example.experimental-mechanic": lambda extension: seen.append(extension["id"])
            },
        )
        self.assertEqual(["example.experimental-mechanic"], seen)

        invalid = copy.deepcopy(optional)
        invalid["extensions"][0]["id"] = "unqualified"
        with self.assertRaisesRegex(CreationContractError, "namespaced extension ID"):
            validate_creation_document(_with_hash(invalid))

    def test_catalog_schemas_and_generated_types_expose_the_same_formats(self) -> None:
        expected = {
            "creation-project": "world-forge.project",
            "creation-profile": CREATION_PROFILE_FORMAT,
            "creation-source-manifest": "world-forge.creation_source_manifest",
            "world-module": "world-forge.world_module",
            "activity-module": "world-forge.activity_module",
            "narrative-module": "world-forge.narrative_module",
            "system-module": "world-forge.system_module",
            "gamepack": "world-forge.gamepack",
            "mechanic-capability-ledger": "world-forge.mechanic_capability_ledger",
        }
        catalog = json.loads((ROOT / "contracts" / "catalog.json").read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in catalog["contracts"]}

        for contract_id, format_name in expected.items():
            with self.subTest(contract=contract_id):
                entry = entries[contract_id]
                self.assertEqual(format_name, entry["format"])
                self.assertEqual(1, entry["version"])
                schema = json.loads((ROOT / entry["schema"]).read_text(encoding="utf-8"))
                self.assertEqual(
                    f"https://world-forge.local/schemas/{contract_id}.schema.json",
                    schema["$id"],
                )
                self.assertEqual(format_name, schema["properties"]["format"]["const"])

        generated = (
            ROOT / "apps" / "studio" / "src" / "generated" / "world-forge-contracts.d.ts"
        ).read_text(encoding="utf-8")
        for type_name in (
            "CreationProject",
            "CreationProfile",
            "CreationSourceManifest",
            "WorldModule",
            "ActivityModule",
            "NarrativeModule",
            "SystemModule",
            "WorldForgeDeterministicGamepackV1",
            "WorldForgeMechanicCapabilityLedgerV1",
        ):
            self.assertIn(type_name, generated)
        self.assertNotIn("[k: string]: unknown", generated)
        self.assertIn("events?: never", generated)
        self.assertIn("options?: never", generated)
        self.assertIn("entry_unit_ids", generated)

        conformance = (
            ROOT / "apps" / "studio" / "src" / "generated" / "world-forge-contracts.conformance.ts"
        ).read_text(encoding="utf-8")
        self.assertIn("missing required reference fields", conformance)
        self.assertIn("discriminator payload must remain closed", conformance)
        self.assertIn("pre-bound discriminator payload must remain closed", conformance)
        self.assertIn("gamepacks cannot contain runtime AI declarations", conformance)
        self.assertIn("absent adapters cannot retain an adapter identity", conformance)


if __name__ == "__main__":
    unittest.main()
