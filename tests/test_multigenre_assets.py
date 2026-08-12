from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.generate_generic_asset_fixtures import build_fixture_documents
from worldforge import generic_assets
from worldforge.creation_contracts import (
    MAX_CREATION_CONTRACT_BYTES,
    MAX_CREATION_JSON_DEPTH,
    LoadedCreationProject,
    canonical_creation_hash,
    load_creation_project,
)
from worldforge.gamepack import build_gamepack, load_gamepack
from worldforge.generic_assets import (
    GENERIC_ASSET_MATRIX,
    GenericAssetError,
    build_asset_inventory,
    build_asset_specification,
    build_asset_style,
    build_asset_subject,
    build_asset_target,
    load_asset_inventory,
    load_asset_specification,
    load_asset_style,
    load_asset_subject,
    load_asset_target,
    publish_asset_inventory,
    publish_asset_specification,
    publish_asset_style,
    publish_asset_subject,
    publish_asset_target,
    serialize_asset_contract,
    validate_asset_inventory,
    validate_asset_inventory_document,
    validate_asset_specification,
    validate_asset_specification_document,
    validate_asset_specification_set,
    validate_asset_style,
    validate_asset_style_document,
    validate_asset_subject,
    validate_asset_subject_document,
    validate_asset_target,
    validate_asset_target_document,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"


def _gamepack(case: str) -> dict[str, object]:
    return load_gamepack(
        EXAMPLES / case / "artifacts" / f"{case}.gamepack.json",
    )


def _review() -> dict[str, object]:
    return {
        "reviewer_id": "asset_director",
        "rationale": "The binding plan was reviewed against every compiled requirement.",
        "evidence": [
            {
                "evidence_id": "binding_review",
                "content_hash": "1" * 64,
            }
        ],
    }


def _puzzle_target_input() -> list[dict[str, object]]:
    return [
        {
            "binding_id": "board_texture",
            "asset_id": "board_ui",
            "selected_format": "asset:png",
            "kind": "ui",
            "representation": "2d",
            "outputs": [{"role": "texture", "media_type": "image/png"}],
            "sharing": {"policy": "exclusive", "group_id": None},
        }
    ]


def _narrative_target_input() -> list[dict[str, object]]:
    common = {
        "asset_id": "narrative_ui_font",
        "selected_format": "asset:font",
        "kind": "font",
        "representation": "2d",
        "outputs": [{"role": "font", "media_type": "font/ttf"}],
        "sharing": {"policy": "shared_exact", "group_id": "narrative_ui"},
    }
    return [
        {"binding_id": "choice_panel", **copy.deepcopy(common)},
        {"binding_id": "ending_panel", **copy.deepcopy(common)},
    ]


def _style_input(gamepack: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    presentation = gamepack["presentation"]
    visual = {
        "presentation_mode": presentation["mode"],
        "visual_language": presentation["visual_language"],
        "camera": presentation["camera"],
        "coordinate_system": ("text_flow" if presentation["mode"] == "text" else "screen_2d"),
        "reference_resolution": {"width": 1280, "height": 720},
        "aspect_ratio": {"width": 16, "height": 9},
        "palette": {
            "direction": "A restrained neutral palette keeps interactive states distinct.",
            "minimum_contrast_ratio": 7,
            "color_independent": True,
        },
        "readability": {
            "silhouette_direction": "Every interactive state remains identifiable without color.",
            "minimum_feature_pixels": 2,
        },
        "typography": {
            "direction": "Use a highly legible interface family with stable metrics.",
            "minimum_text_scale_percent": 200,
        },
        "motion": {
            "direction": "State changes use brief non-essential transitions.",
            "reduced_motion": True,
        },
        "ui": {
            "hierarchy": "Primary action, current state, and outcome feedback remain distinct.",
            "density": presentation["ui_density"],
        },
        "accessibility": {
            "captions": presentation["accessibility"]["captions"],
            "screen_reader_structure": presentation["accessibility"]["screen_reader_structure"],
            "keyboard_only": presentation["accessibility"]["keyboard_only"],
        },
        "localization": {
            "source_locale": presentation["localization"]["source_locale"],
            "supported_locales": presentation["localization"]["supported_locales"],
            "expansion_budget_percent": 35,
        },
    }
    audio = {
        "status": "not_applicable",
        "rationale": "No audio asset is required by this bounded release target.",
    }
    return visual, audio


def _puzzle_spec_inputs() -> tuple[list[dict[str, object]], list[str]]:
    outputs = [
        {
            "role": "texture",
            "media_type": "image/png",
            "runtime_path": "assets/ui/board.png",
            "expectations": {
                "kind": "png",
                "width": 256,
                "height": 256,
                "color_type": "rgba8",
                "max_bytes": 262144,
            },
        }
    ]
    return outputs, [
        "Every board symbol remains distinguishable without color.",
        "The exact runtime output matches the reviewed target.",
    ]


def _font_spec_inputs() -> tuple[list[dict[str, object]], list[str]]:
    outputs = [
        {
            "role": "font",
            "media_type": "font/ttf",
            "runtime_path": "assets/fonts/narrative-ui.ttf",
            "expectations": {
                "kind": "font",
                "container": "ttf",
                "glyph_ranges": ["U+0020-007E"],
                "max_glyphs": 256,
                "max_bytes": 524288,
            },
        }
    ]
    return outputs, [
        "Choice and ending text use the same reviewed font bytes.",
        "The glyph inventory covers every source-locale fixture string.",
    ]


def _build_chain(
    case: str,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    return _build_chain_from_gamepack(_gamepack(case), case)


def _build_chain_from_gamepack(
    gamepack: dict[str, object],
    case: str,
) -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    subject = build_asset_subject(gamepack)
    bindings = _puzzle_target_input() if case == "abstract-puzzle" else _narrative_target_input()
    target = build_asset_target(gamepack, subject, review=_review(), bindings=bindings)
    visual, audio = _style_input(gamepack)
    style = build_asset_style(
        gamepack,
        subject,
        target,
        style_id=f"{case.replace('-', '_')}_style",
        reviewer=_review(),
        visual=visual,
        audio=audio,
    )
    inventory = build_asset_inventory(gamepack, subject, target, style)
    return gamepack, subject, target, style, inventory


def _gamepack_with_project_id(project_id: str) -> dict[str, object]:
    loaded = load_creation_project(
        EXAMPLES / "abstract-puzzle" / "project.json",
    )

    def project_bound(document: dict[str, object]) -> dict[str, object]:
        checked = copy.deepcopy(document)
        if "project_id" in checked:
            checked["project_id"] = project_id
        checked["content_hash"] = canonical_creation_hash(checked)
        return checked

    project = project_bound(loaded.project)
    profile = project_bound(loaded.profile)
    manifest = project_bound(loaded.manifest)
    module_fields = {
        "world_modules": tuple(project_bound(item) for item in loaded.world_modules),
        "activity_modules": tuple(project_bound(item) for item in loaded.activity_modules),
        "narrative_modules": tuple(project_bound(item) for item in loaded.narrative_modules),
        "system_modules": tuple(project_bound(item) for item in loaded.system_modules),
        "logic_modules": tuple(project_bound(item) for item in loaded.logic_modules),
    }
    manifest["profile"]["content_hash"] = profile["content_hash"]
    for field, modules in module_fields.items():
        by_id = {module["module_id"]: module for module in modules}
        for reference in manifest["modules"][field]:
            reference["content_hash"] = by_id[reference["id"]]["content_hash"]
    manifest["content_hash"] = canonical_creation_hash(manifest)
    project["profile"]["content_hash"] = profile["content_hash"]
    project["source_manifest"]["id"] = project_id
    project["source_manifest"]["content_hash"] = manifest["content_hash"]
    project["content_hash"] = canonical_creation_hash(project)
    return build_gamepack(
        LoadedCreationProject(
            project=project,
            profile=profile,
            manifest=manifest,
            **module_fields,
        )
    )


class GenericAssetFoundationTests(unittest.TestCase):
    def test_subject_integrally_binds_exact_gamepack(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        subject = build_asset_subject(gamepack)

        self.assertEqual("world-forge.asset_subject", subject["format"])
        self.assertEqual("gamepack", subject["subject"]["kind"])
        self.assertEqual(gamepack["content_hash"], subject["subject"]["content_hash"])
        self.assertEqual(subject, validate_asset_subject(subject, gamepack=gamepack))

        different = _gamepack("branching-narrative")
        with self.assertRaisesRegex(GenericAssetError, "subject.*mismatch"):
            validate_asset_subject(subject, gamepack=different)

    def test_exact_identifier_domain_and_maximum_ids_complete_the_default_chain(
        self,
    ) -> None:
        expected_id_schema = {
            "maxLength": 64,
            "minLength": 2,
            "pattern": ("^(?!(?:aux|con|nul|prn|com[1-9]|lpt[1-9])$)[a-z][a-z0-9_]{1,63}$"),
            "type": "string",
        }
        for path in sorted((ROOT / "schemas").glob("generic-asset-*.schema.json")):
            with self.subTest(schema=path.name):
                schema = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(expected_id_schema, schema["$defs"]["id"])

        maximum_game_id = "g" + ("a" * 63)
        maximum_asset_id = "a" * 64
        gamepack = _gamepack_with_project_id(maximum_game_id)
        subject = build_asset_subject(gamepack)
        bindings = _puzzle_target_input()
        bindings[0]["asset_id"] = maximum_asset_id
        target = build_asset_target(
            gamepack,
            subject,
            review=_review(),
            bindings=bindings,
        )
        visual, audio = _style_input(gamepack)
        style = build_asset_style(
            gamepack,
            subject,
            target,
            reviewer=_review(),
            visual=visual,
            audio=audio,
        )
        inventory = build_asset_inventory(gamepack, subject, target, style)
        outputs, criteria = _puzzle_spec_inputs()
        specification = build_asset_specification(
            gamepack,
            subject,
            target,
            style,
            inventory,
            asset_id=maximum_asset_id,
            outputs=outputs,
            acceptance_criteria=criteria,
            production_class="procedural_offline",
            review_requirements={
                "human_review_required": True,
                "qa_profile": "ui_readability",
                "evidence_required": True,
            },
        )
        self.assertEqual(maximum_game_id, subject["subject"]["id"])
        self.assertEqual(maximum_asset_id, specification["asset"]["asset_id"])
        derived_ids = {
            "asset_subject": subject["subject_id"],
            "asset_target": target["target_id"],
            "asset_style": style["style_id"],
            "asset_inventory": inventory["inventory_id"],
            "asset_spec": specification["spec_id"],
        }
        for prefix, value in derived_ids.items():
            self.assertGreaterEqual(len(value), 2)
            self.assertLessEqual(len(value), 64)
            self.assertTrue(value.startswith(f"{prefix}_"))
            self.assertEqual(
                generic_assets.DERIVED_GENERIC_ASSET_ID_HASH_HEX,
                len(value.removeprefix(f"{prefix}_")),
            )
            self.assertTrue(
                all(character in "0123456789abcdef" for character in value.split("_")[-1])
            )
        self.assertEqual(
            (subject, target, style, inventory, specification),
            (
                build_asset_subject(gamepack),
                build_asset_target(
                    gamepack,
                    subject,
                    review=_review(),
                    bindings=bindings,
                ),
                build_asset_style(
                    gamepack,
                    subject,
                    target,
                    reviewer=_review(),
                    visual=visual,
                    audio=audio,
                ),
                build_asset_inventory(gamepack, subject, target, style),
                build_asset_specification(
                    gamepack,
                    subject,
                    target,
                    style,
                    inventory,
                    asset_id=maximum_asset_id,
                    outputs=outputs,
                    acceptance_criteria=criteria,
                    production_class="procedural_offline",
                    review_requirements={
                        "human_review_required": True,
                        "qa_profile": "ui_readability",
                        "evidence_required": True,
                    },
                ),
            ),
        )

        documents = (
            (target, "target_id", validate_asset_target_document),
            (style, "style_id", validate_asset_style_document),
            (inventory, "inventory_id", validate_asset_inventory_document),
            (specification, "spec_id", validate_asset_specification_document),
        )
        for length in (1, 2, 49, 56, 64, 65, 100):
            candidate_id = "a" + ("b" * max(0, length - 1))
            for original, field, validator in documents:
                with self.subTest(length=length, field=field):
                    candidate = copy.deepcopy(original)
                    candidate[field] = candidate_id
                    candidate["content_hash"] = canonical_creation_hash(candidate)
                    if 2 <= length <= 64:
                        self.assertEqual(candidate, validator(candidate))
                    else:
                        with self.assertRaisesRegex(
                            GenericAssetError,
                            "portable lowercase ID",
                        ):
                            validator(candidate)

        reserved = copy.deepcopy(target)
        reserved["target_id"] = "con"
        reserved["content_hash"] = canonical_creation_hash(reserved)
        with self.assertRaisesRegex(GenericAssetError, "portable lowercase ID"):
            validate_asset_target_document(reserved)

    def test_legacy_subject_is_structurally_recognized_but_not_accepted_for_derivation(
        self,
    ) -> None:
        gamepack = _gamepack("abstract-puzzle")
        subject = build_asset_subject(gamepack)
        subject["subject"] = {
            "kind": "legacy_worldpack",
            "format": "isoworld.worldpack",
            "format_version": 5,
            "id": "legacy_world",
            "content_hash": "2" * 64,
        }
        subject["subject_id"] = generic_assets._derived_contract_id(
            "asset_subject",
            subject["subject"],
        )
        subject["content_hash"] = canonical_creation_hash(subject)

        self.assertEqual(
            "legacy_worldpack",
            validate_asset_subject_document(subject)["subject"]["kind"],
        )
        with self.assertRaisesRegex(GenericAssetError, "gamepack.*required"):
            validate_asset_subject(subject, gamepack=gamepack)

    def test_target_covers_every_requirement_and_preserves_source_fields(self) -> None:
        gamepack, subject, target, _, _ = _build_chain("abstract-puzzle")
        validated = validate_asset_target(target, gamepack=gamepack, subject=subject)

        requirement = gamepack["asset_requirements"][0]
        binding = validated["bindings"][0]
        for field in (
            "binding_id",
            "required",
            "roles",
            "usage_contexts",
            "referencing_subjects",
        ):
            self.assertEqual(requirement[field], binding[field])

        missing = copy.deepcopy(target)
        missing["bindings"] = []
        missing["content_hash"] = canonical_creation_hash(missing)
        with self.assertRaisesRegex(GenericAssetError, "exactly cover"):
            validate_asset_target(missing, gamepack=gamepack, subject=subject)

    def test_target_rejects_bad_format_and_implicit_or_incompatible_sharing(self) -> None:
        gamepack = _gamepack("branching-narrative")
        subject = build_asset_subject(gamepack)

        bindings = _narrative_target_input()
        bindings[0]["selected_format"] = "asset:wav"
        with self.assertRaisesRegex(GenericAssetError, "accepted_formats"):
            build_asset_target(gamepack, subject, review=_review(), bindings=bindings)

        bindings = _narrative_target_input()
        bindings[1]["sharing"] = {"policy": "exclusive", "group_id": None}
        with self.assertRaisesRegex(GenericAssetError, "sharing"):
            build_asset_target(gamepack, subject, review=_review(), bindings=bindings)

        bindings = _narrative_target_input()
        bindings[1]["outputs"] = [{"role": "font", "media_type": "font/otf"}]
        with self.assertRaisesRegex(GenericAssetError, "incompatible"):
            build_asset_target(gamepack, subject, review=_review(), bindings=bindings)

    def test_target_sharing_is_closed_under_inventory_array_limits(self) -> None:
        _, _, target, style, _ = _build_chain("branching-narrative")

        exact_roles = copy.deepcopy(target)
        exact_roles["bindings"][0]["roles"] = [f"role_{index:04d}" for index in range(512)]
        exact_roles["bindings"][1]["roles"] = [f"role_{index:04d}" for index in range(512, 1024)]
        exact_roles["content_hash"] = canonical_creation_hash(exact_roles)
        self.assertEqual(exact_roles, validate_asset_target_document(exact_roles))

        disjoint_roles = copy.deepcopy(target)
        disjoint_roles["bindings"][0]["roles"] = [f"role_{index:04d}" for index in range(1024)]
        disjoint_roles["bindings"][1]["roles"] = [
            f"role_{index:04d}" for index in range(1024, 2048)
        ]
        disjoint_roles["content_hash"] = canonical_creation_hash(disjoint_roles)
        with self.assertRaisesRegex(GenericAssetError, "shared roles exceed 1024"):
            validate_asset_target_document(disjoint_roles)

        repeated_references = [
            {"kind": "mechanic", "id": f"subject_{index:04d}"} for index in range(1024)
        ]
        repeated = copy.deepcopy(target)
        for binding in repeated["bindings"]:
            binding["referencing_subjects"] = copy.deepcopy(repeated_references)
        repeated["content_hash"] = canonical_creation_hash(repeated)
        self.assertEqual(repeated, validate_asset_target_document(repeated))

        disjoint_references = copy.deepcopy(target)
        disjoint_references["bindings"][0]["referencing_subjects"] = [
            {"kind": "mechanic", "id": f"subject_{index:04d}"} for index in range(1024)
        ]
        disjoint_references["bindings"][1]["referencing_subjects"] = [
            {"kind": "mechanic", "id": f"subject_{index:04d}"} for index in range(1024, 2048)
        ]
        disjoint_references["content_hash"] = canonical_creation_hash(disjoint_references)
        with self.assertRaisesRegex(
            GenericAssetError,
            "shared referencing subjects exceed 1024",
        ):
            validate_asset_target_document(disjoint_references)

        casefold_aliases = copy.deepcopy(target)
        casefold_aliases["bindings"][0]["usage_contexts"] = ["Context"]
        casefold_aliases["bindings"][1]["usage_contexts"] = ["context"]
        casefold_aliases["content_hash"] = canonical_creation_hash(casefold_aliases)
        self.assertEqual(casefold_aliases, validate_asset_target_document(casefold_aliases))
        self.assertEqual(
            ["Context"],
            generic_assets._build_inventory_assets(casefold_aliases, style)[0]["usage_contexts"],
        )

        casefold_maximum = copy.deepcopy(target)
        casefold_maximum["bindings"][0]["usage_contexts"] = [
            f"Context_{index:04d}" for index in range(1024)
        ]
        casefold_maximum["bindings"][1]["usage_contexts"] = [
            f"context_{index:04d}" for index in range(1024)
        ]
        casefold_maximum["content_hash"] = canonical_creation_hash(casefold_maximum)
        self.assertEqual(casefold_maximum, validate_asset_target_document(casefold_maximum))
        self.assertEqual(
            1024,
            len(
                generic_assets._build_inventory_assets(casefold_maximum, style)[0]["usage_contexts"]
            ),
        )

    def test_style_is_independent_but_cross_checked_against_gamepack_and_target(
        self,
    ) -> None:
        gamepack, subject, target, style, _ = _build_chain("abstract-puzzle")
        self.assertEqual(
            style,
            validate_asset_style(
                style,
                gamepack=gamepack,
                subject=subject,
                target=target,
            ),
        )

        mismatch = copy.deepcopy(style)
        mismatch["visual"]["camera"] = "free"
        mismatch["content_hash"] = canonical_creation_hash(mismatch)
        with self.assertRaisesRegex(GenericAssetError, "camera"):
            validate_asset_style(
                mismatch,
                gamepack=gamepack,
                subject=subject,
                target=target,
            )

        fake_audio = copy.deepcopy(style)
        fake_audio["audio"]["music_direction"] = "Invented music."
        fake_audio["content_hash"] = canonical_creation_hash(fake_audio)
        with self.assertRaisesRegex(GenericAssetError, "audio"):
            validate_asset_style(
                fake_audio,
                gamepack=gamepack,
                subject=subject,
                target=target,
            )

    def test_short_runtime_string_bounds_match_schemas_and_python(self) -> None:
        _, _, _, style, _ = _build_chain("abstract-puzzle")
        style_schema = json.loads(
            (ROOT / "schemas/generic-asset-style.schema.json").read_text(encoding="utf-8")
        )
        spec_schema = json.loads(
            (ROOT / "schemas/generic-asset-spec.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(256, style_schema["$defs"]["runtimeShortString"]["allOf"][1]["maxLength"])
        self.assertEqual(
            "#/$defs/runtimeShortString",
            style_schema["$defs"]["visual"]["properties"]["camera"]["$ref"],
        )
        self.assertEqual(
            "#/$defs/runtimeShortString",
            style_schema["$defs"]["visual"]["properties"]["ui"]["properties"]["density"]["$ref"],
        )
        self.assertEqual(256, spec_schema["$defs"]["runtimeShortString"]["allOf"][1]["maxLength"])
        self.assertEqual(
            "#/$defs/runtimeShortString",
            spec_schema["$defs"]["jsonExpectation"]["properties"]["schema_id"]["$ref"],
        )

        for field_path in (("camera",), ("ui", "density")):
            with self.subTest(field_path=field_path):
                valid = copy.deepcopy(style)
                target = valid["visual"]
                for field in field_path[:-1]:
                    target = target[field]
                target[field_path[-1]] = "a" * 256
                valid["content_hash"] = canonical_creation_hash(valid)
                self.assertEqual(valid, validate_asset_style_document(valid))

                invalid = copy.deepcopy(valid)
                target = invalid["visual"]
                for field in field_path[:-1]:
                    target = target[field]
                target[field_path[-1]] = "a" * 257
                invalid["content_hash"] = canonical_creation_hash(invalid)
                with self.assertRaisesRegex(GenericAssetError, "256"):
                    validate_asset_style_document(invalid)

        valid_json_expectations = {
            "kind": "schema_json",
            "schema_id": "a" * 256,
            "schema_version": 1,
            "max_records": 1,
            "max_bytes": 1024,
        }
        self.assertEqual(
            valid_json_expectations,
            generic_assets._validate_expectations(
                valid_json_expectations,
                "expectations",
                role="localized_text",
                media_type="application/json",
            ),
        )
        invalid_json_expectations = copy.deepcopy(valid_json_expectations)
        invalid_json_expectations["schema_id"] = "a" * 257
        with self.assertRaisesRegex(GenericAssetError, "256"):
            generic_assets._validate_expectations(
                invalid_json_expectations,
                "expectations",
                role="localized_text",
                media_type="application/json",
            )

    def test_asset_specification_criteria_accept_exactly_64_and_reject_65(self) -> None:
        specification = json.loads(
            (EXAMPLES / "abstract-puzzle" / "assets" / "specs" / "board_ui.json").read_text(
                encoding="utf-8"
            )
        )
        schema = json.loads(
            (ROOT / "schemas/generic-asset-spec.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(64, schema["properties"]["acceptance_criteria"]["maxItems"])

        maximum = copy.deepcopy(specification)
        maximum["acceptance_criteria"] = [
            f"Criterion {index:03d} remains exact." for index in range(64)
        ]
        maximum["content_hash"] = canonical_creation_hash(maximum)
        self.assertEqual(maximum, validate_asset_specification_document(maximum))

        oversized = copy.deepcopy(maximum)
        oversized["acceptance_criteria"].append("Criterion 064 remains exact.")
        oversized["content_hash"] = canonical_creation_hash(oversized)
        with self.assertRaisesRegex(GenericAssetError, "acceptance criteria exceeds"):
            validate_asset_specification_document(oversized)

    def test_legitimate_creative_prose_is_not_treated_as_provider_metadata(
        self,
    ) -> None:
        gamepack, subject, target, _, _ = _build_chain("abstract-puzzle")
        target["review"]["rationale"] = (
            "The model of play and provider relationship are human-reviewed."
        )
        target["content_hash"] = canonical_creation_hash(target)

        self.assertEqual(
            target,
            validate_asset_target(target, gamepack=gamepack, subject=subject),
        )

    def test_style_rejects_3d_target_under_2d_only_gamepack(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        subject = build_asset_subject(gamepack)
        bindings = _puzzle_target_input()
        bindings[0].update(
            {
                "kind": "model_3d",
                "representation": "3d",
                "selected_format": "asset:glb",
                "outputs": [{"role": "model", "media_type": "model/gltf-binary"}],
            }
        )
        gamepack = copy.deepcopy(gamepack)
        gamepack["asset_requirements"][0]["accepted_formats"] = ["asset:glb"]
        gamepack["runtime_requirements"]["asset_formats"] = ["asset:glb"]
        gamepack["content_hash"] = canonical_creation_hash(gamepack)
        subject = build_asset_subject(gamepack)
        target = build_asset_target(
            gamepack,
            subject,
            review=_review(),
            bindings=bindings,
        )
        visual, audio = _style_input(gamepack)
        with self.assertRaisesRegex(GenericAssetError, "3d"):
            build_asset_style(
                gamepack,
                subject,
                target,
                style_id="invalid_3d_style",
                reviewer=_review(),
                visual=visual,
                audio=audio,
            )

    def test_runtime_matrix_is_complete_and_rejects_impossible_combinations(self) -> None:
        expected_kinds = {
            "ui",
            "portrait",
            "sprite",
            "vfx",
            "spritesheet",
            "tileset",
            "font",
            "sfx",
            "music",
            "shader",
            "localization",
            "animation_3d",
            "collision_3d",
            "rig",
            "character_3d",
            "environment_3d",
            "model_3d",
            "material_set",
            "vfx_3d",
        }
        self.assertEqual(expected_kinds, {entry.kind for entry in GENERIC_ASSET_MATRIX})
        self.assertFalse(
            any(
                entry.kind == "sprite" and entry.representation == "3d"
                for entry in GENERIC_ASSET_MATRIX
            )
        )

    def test_generic_schemas_do_not_hide_narrowing_in_ref_siblings(self) -> None:
        for path in sorted((ROOT / "schemas").glob("generic-asset-*.schema.json")):
            with self.subTest(schema=path.name):
                document = json.loads(path.read_text(encoding="utf-8"))
                stack = [document]
                while stack:
                    value = stack.pop()
                    if isinstance(value, dict):
                        self.assertFalse(
                            "$ref" in value and len(value) > 1,
                            f"{path.name} uses generator-unsafe $ref siblings",
                        )
                        stack.extend(value.values())
                    elif isinstance(value, list):
                        stack.extend(value)

    def test_inventory_is_a_pure_exact_rebuild_and_shares_only_explicit_assets(
        self,
    ) -> None:
        gamepack, subject, target, style, inventory = _build_chain("branching-narrative")
        validated = validate_asset_inventory(
            inventory,
            gamepack=gamepack,
            subject=subject,
            target=target,
            style=style,
        )

        self.assertEqual(1, len(validated["assets"]))
        self.assertEqual(
            ["choice_panel", "ending_panel"],
            validated["assets"][0]["binding_ids"],
        )
        self.assertEqual(
            serialize_asset_contract(inventory),
            serialize_asset_contract(build_asset_inventory(gamepack, subject, target, style)),
        )

        addition = copy.deepcopy(inventory)
        addition["assets"].append(copy.deepcopy(addition["assets"][0]))
        addition["assets"][-1]["asset_id"] = "manual_extra"
        addition["assets"][-1]["binding_ids"] = ["manual_binding"]
        addition["assets"].sort(key=lambda item: item["asset_id"].encode())
        addition["content_hash"] = canonical_creation_hash(addition)
        with self.assertRaisesRegex(GenericAssetError, "rebuild"):
            validate_asset_inventory(
                addition,
                gamepack=gamepack,
                subject=subject,
                target=target,
                style=style,
            )

    def test_inventory_python_array_limits_match_the_schema(self) -> None:
        _, _, _, _, inventory = _build_chain("branching-narrative")
        oversized_values = {
            "binding_ids": [f"binding_{index:04d}" for index in range(1025)],
            "source_roles": [f"role_{index:04d}" for index in range(1025)],
            "usage_contexts": [f"context_{index:04d}" for index in range(1025)],
            "referencing_subjects": [
                {"kind": "mechanic", "id": f"subject_{index:04d}"} for index in range(1025)
            ],
        }
        for field, values in oversized_values.items():
            with self.subTest(field=field):
                candidate = copy.deepcopy(inventory)
                candidate["assets"][0][field] = values
                candidate["content_hash"] = canonical_creation_hash(candidate)
                with self.assertRaisesRegex(GenericAssetError, "1024|bounded"):
                    validate_asset_inventory_document(candidate)

    def test_inventory_provenance_remains_bounded_at_maximum_shared_fanout(
        self,
    ) -> None:
        _, _, target, style, _ = _build_chain("branching-narrative")
        template = target["bindings"][0]
        maximum = generic_assets.MAX_GENERIC_ASSETS
        target = copy.deepcopy(target)
        target["bindings"] = []
        for index in range(maximum):
            binding = copy.deepcopy(template)
            binding["binding_id"] = f"binding_{index:04d}"
            binding["asset_id"] = "shared_asset"
            binding["roles"] = [f"role_{index:04d}"]
            binding["usage_contexts"] = [f"context_{index:04d}"]
            binding["referencing_subjects"] = [{"kind": "mechanic", "id": f"subject_{index:04d}"}]
            binding["sharing"] = {
                "policy": "shared_exact",
                "group_id": "maximum_fanout",
            }
            target["bindings"].append(binding)

        assets = generic_assets._build_inventory_assets(target, style)

        self.assertEqual(1, len(assets))
        self.assertEqual(maximum, len(assets[0]["binding_ids"]))
        self.assertEqual(maximum, len(assets[0]["source_roles"]))
        self.assertEqual(maximum, len(assets[0]["usage_contexts"]))
        self.assertEqual(maximum, len(assets[0]["referencing_subjects"]))
        self.assertLessEqual(
            len(assets[0]["provenance_reason"]),
            generic_assets.MAX_GENERIC_ASSET_TEXT,
        )

    def test_specification_set_exactly_fulfils_inventory(self) -> None:
        gamepack, subject, target, style, inventory = _build_chain("abstract-puzzle")
        outputs, criteria = _puzzle_spec_inputs()
        spec = build_asset_specification(
            gamepack,
            subject,
            target,
            style,
            inventory,
            asset_id="board_ui",
            outputs=outputs,
            acceptance_criteria=criteria,
            production_class="procedural_offline",
            review_requirements={
                "human_review_required": True,
                "qa_profile": "ui_readability",
                "evidence_required": True,
            },
        )
        validated = validate_asset_specification_set(
            [spec],
            gamepack=gamepack,
            inventory=inventory,
            subject=subject,
            target=target,
            style=style,
        )
        self.assertEqual([spec], validated)

        with self.assertRaisesRegex(GenericAssetError, "exactly one"):
            validate_asset_specification_set(
                [],
                gamepack=gamepack,
                inventory=inventory,
                subject=subject,
                target=target,
                style=style,
            )
        with self.assertRaisesRegex(GenericAssetError, "duplicate"):
            validate_asset_specification_set(
                [spec, spec],
                gamepack=gamepack,
                inventory=inventory,
                subject=subject,
                target=target,
                style=style,
            )

    def test_specs_reject_paths_collisions_provider_fields_and_wrong_media_details(
        self,
    ) -> None:
        gamepack, subject, target, style, inventory = _build_chain("abstract-puzzle")
        outputs, criteria = _puzzle_spec_inputs()
        spec = build_asset_specification(
            gamepack,
            subject,
            target,
            style,
            inventory,
            asset_id="board_ui",
            outputs=outputs,
            acceptance_criteria=criteria,
            production_class="procedural_offline",
            review_requirements={
                "human_review_required": True,
                "qa_profile": "ui_readability",
                "evidence_required": True,
            },
        )

        for bad_path in (
            "../board.png",
            "assets\\board.png",
            "CON/out.png",
            "assets/fonts./font.ttf",
            "assets/fonts /font.ttf",
            "assets/aux/font.ttf",
        ):
            with self.subTest(path=bad_path):
                invalid = copy.deepcopy(spec)
                invalid["outputs"][0]["runtime_path"] = bad_path
                invalid["content_hash"] = canonical_creation_hash(invalid)
                with self.assertRaisesRegex(GenericAssetError, "portable"):
                    validate_asset_specification(
                        invalid,
                        gamepack=gamepack,
                        inventory=inventory,
                        subject=subject,
                        target=target,
                        style=style,
                    )

        invalid = copy.deepcopy(spec)
        invalid["provider"] = "remote"
        invalid["content_hash"] = canonical_creation_hash(invalid)
        with self.assertRaisesRegex(GenericAssetError, "unknown|forbidden"):
            validate_asset_specification(
                invalid,
                gamepack=gamepack,
                inventory=inventory,
                subject=subject,
                target=target,
                style=style,
            )

        decomposed = copy.deepcopy(spec)
        decomposed["outputs"][0]["runtime_path"] = "assets/ui/cafe\u0301.png"
        decomposed["content_hash"] = canonical_creation_hash(decomposed)
        with self.assertRaisesRegex(GenericAssetError, "NFC|portable"):
            validate_asset_specification(
                decomposed,
                gamepack=gamepack,
                inventory=inventory,
                subject=subject,
                target=target,
                style=style,
            )

        invalid = copy.deepcopy(spec)
        invalid["outputs"][0]["expectations"]["width"] = 0
        invalid["content_hash"] = canonical_creation_hash(invalid)
        with self.assertRaisesRegex(GenericAssetError, "width"):
            validate_asset_specification(
                invalid,
                gamepack=gamepack,
                inventory=inventory,
                subject=subject,
                target=target,
                style=style,
            )

    def test_font_glyph_ranges_use_one_exact_canonical_grammar(self) -> None:
        gamepack, subject, target, style, inventory = _build_chain("branching-narrative")
        outputs, criteria = _font_spec_inputs()
        specification = build_asset_specification(
            gamepack,
            subject,
            target,
            style,
            inventory,
            asset_id="narrative_ui_font",
            outputs=outputs,
            acceptance_criteria=criteria,
            production_class="human",
            review_requirements={
                "human_review_required": True,
                "qa_profile": "localized_text",
                "evidence_required": True,
            },
        )
        schema = json.loads(
            (ROOT / "schemas/generic-asset-spec.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            generic_assets.GENERIC_ASSET_GLYPH_RANGE_PATTERN,
            schema["$defs"]["fontExpectation"]["properties"]["glyph_ranges"]["items"]["pattern"],
        )

        for glyph_ranges in (
            ["U+0000-0000"],
            ["U+0020-007E", "U+10000-10FFFF"],
            ["U+10FFFF-10FFFF"],
        ):
            with self.subTest(valid=glyph_ranges):
                candidate = copy.deepcopy(specification)
                candidate["outputs"][0]["expectations"]["glyph_ranges"] = glyph_ranges
                candidate["content_hash"] = canonical_creation_hash(candidate)
                self.assertEqual(
                    candidate,
                    validate_asset_specification(
                        candidate,
                        gamepack=gamepack,
                        inventory=inventory,
                        subject=subject,
                        target=target,
                        style=style,
                    ),
                )

        for glyph_ranges in (
            ["U+-"],
            ["U+007F-0020"],
            ["U+0020-007E", "U+007E-00FF"],
            ["U+0020-007E", "U+0020-007E"],
            ["U+00000-0007E"],
            ["U+10FFFF-110000"],
            ["u+0020-007e"],
        ):
            with self.subTest(invalid=glyph_ranges):
                candidate = copy.deepcopy(specification)
                candidate["outputs"][0]["expectations"]["glyph_ranges"] = glyph_ranges
                candidate["content_hash"] = canonical_creation_hash(candidate)
                with self.assertRaisesRegex(
                    GenericAssetError,
                    "glyph|Unicode|collision",
                ):
                    validate_asset_specification(
                        candidate,
                        gamepack=gamepack,
                        inventory=inventory,
                        subject=subject,
                        target=target,
                        style=style,
                    )

    def test_complete_spec_set_rejects_casefold_runtime_path_collision(self) -> None:
        gamepack = _gamepack("branching-narrative")
        subject = build_asset_subject(gamepack)
        bindings = _narrative_target_input()
        bindings[0]["asset_id"] = "choice_font"
        bindings[0]["sharing"] = {"policy": "exclusive", "group_id": None}
        bindings[1]["asset_id"] = "ending_font"
        bindings[1]["sharing"] = {"policy": "exclusive", "group_id": None}
        target = build_asset_target(
            gamepack,
            subject,
            review=_review(),
            bindings=bindings,
        )
        visual, audio = _style_input(gamepack)
        style = build_asset_style(
            gamepack,
            subject,
            target,
            style_id="separate_font_style",
            reviewer=_review(),
            visual=visual,
            audio=audio,
        )
        inventory = build_asset_inventory(gamepack, subject, target, style)
        outputs, criteria = _font_spec_inputs()
        first_outputs = copy.deepcopy(outputs)
        first_outputs[0]["runtime_path"] = "assets/fonts/choice.ttf"
        first = build_asset_specification(
            gamepack,
            subject,
            target,
            style,
            inventory,
            asset_id="choice_font",
            outputs=first_outputs,
            acceptance_criteria=criteria,
            production_class="human",
            review_requirements={
                "human_review_required": True,
                "qa_profile": "localized_text",
                "evidence_required": True,
            },
        )
        second_outputs = copy.deepcopy(outputs)
        second_outputs[0]["runtime_path"] = "assets/fonts/ending.ttf"
        second = build_asset_specification(
            gamepack,
            subject,
            target,
            style,
            inventory,
            asset_id="ending_font",
            outputs=second_outputs,
            acceptance_criteria=criteria,
            production_class="human",
            review_requirements={
                "human_review_required": True,
                "qa_profile": "localized_text",
                "evidence_required": True,
            },
        )
        collision_pairs = (
            ("assets/fonts/shared.ttf", "ASSETS/FONTS/SHARED.TTF"),
            ("assets/fonts", "assets/fonts/child.ttf"),
            ("assets/fonts/child.ttf", "assets/fonts"),
            ("assets/Fonts/choice.ttf", "ASSETS/fonts/ending.ttf"),
        )
        for first_path, second_path in collision_pairs:
            with self.subTest(first=first_path, second=second_path):
                first_candidate = copy.deepcopy(first)
                first_candidate["outputs"][0]["runtime_path"] = first_path
                first_candidate["content_hash"] = canonical_creation_hash(first_candidate)
                second_candidate = copy.deepcopy(second)
                second_candidate["outputs"][0]["runtime_path"] = second_path
                second_candidate["content_hash"] = canonical_creation_hash(second_candidate)
                with self.assertRaisesRegex(
                    GenericAssetError,
                    "path.*collision|prefix collision",
                ):
                    validate_asset_specification_set(
                        [first_candidate, second_candidate],
                        gamepack=gamepack,
                        inventory=inventory,
                        subject=subject,
                        target=target,
                        style=style,
                    )

    def test_canonical_fixture_chain_loads_through_secure_readers(self) -> None:
        for case in ("abstract-puzzle", "branching-narrative"):
            with self.subTest(case=case):
                root = EXAMPLES / case
                gamepack_path = root / "artifacts" / f"{case}.gamepack.json"
                gamepack = load_gamepack(gamepack_path)
                asset_root = root / "assets"
                subject = load_asset_subject(
                    asset_root / "subject.json",
                    gamepack_path=gamepack_path,
                )
                target = load_asset_target(
                    asset_root / "target.json",
                    gamepack_path=gamepack_path,
                    subject_path=asset_root / "subject.json",
                )
                style = load_asset_style(
                    asset_root / "style.json",
                    gamepack_path=gamepack_path,
                    subject_path=asset_root / "subject.json",
                    target_path=asset_root / "target.json",
                )
                inventory = load_asset_inventory(
                    asset_root / "inventory.json",
                    gamepack_path=gamepack_path,
                    subject_path=asset_root / "subject.json",
                    target_path=asset_root / "target.json",
                    style_path=asset_root / "style.json",
                )
                specs = sorted((asset_root / "specs").glob("*.json"))
                loaded_specs = [
                    load_asset_specification(
                        path,
                        gamepack=gamepack,
                        inventory=inventory,
                        subject=subject,
                        target=target,
                        style=style,
                    )
                    for path in specs
                ]
                self.assertEqual(
                    len(inventory["assets"]),
                    len(
                        validate_asset_specification_set(
                            loaded_specs,
                            gamepack=gamepack,
                            inventory=inventory,
                            subject=subject,
                            target=target,
                            style=style,
                        )
                    ),
                )

    def test_publishers_are_integral_create_only_and_byte_exact(self) -> None:
        gamepack, subject, target, style, inventory = _build_chain("abstract-puzzle")
        outputs, criteria = _puzzle_spec_inputs()
        specification = build_asset_specification(
            gamepack,
            subject,
            target,
            style,
            inventory,
            asset_id="board_ui",
            outputs=outputs,
            acceptance_criteria=criteria,
            production_class="procedural_offline",
            review_requirements={
                "human_review_required": True,
                "qa_profile": "ui_readability",
                "evidence_required": True,
            },
        )
        publications = (
            (
                "subject.json",
                subject,
                publish_asset_subject,
                {"gamepack": gamepack},
            ),
            (
                "target.json",
                target,
                publish_asset_target,
                {"gamepack": gamepack, "subject": subject},
            ),
            (
                "style.json",
                style,
                publish_asset_style,
                {
                    "gamepack": gamepack,
                    "subject": subject,
                    "target": target,
                },
            ),
            (
                "inventory.json",
                inventory,
                publish_asset_inventory,
                {
                    "gamepack": gamepack,
                    "subject": subject,
                    "target": target,
                    "style": style,
                },
            ),
            (
                "specification.json",
                specification,
                publish_asset_specification,
                {
                    "gamepack": gamepack,
                    "inventory": inventory,
                    "subject": subject,
                    "target": target,
                    "style": style,
                },
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, document, publisher, dependencies in publications:
                with self.subTest(format=document["format"]):
                    path = root / filename
                    published = publisher(path, document, **dependencies)
                    self.assertEqual(path, published.path)
                    self.assertEqual(document["format"], published.format)
                    self.assertEqual(document["content_hash"], published.content_hash)
                    self.assertEqual(serialize_asset_contract(document), path.read_bytes())
                    with self.assertRaisesRegex(GenericAssetError, "output_exists"):
                        publisher(path, document, **dependencies)

            mismatched = copy.deepcopy(target)
            mismatched["gamepack"]["content_hash"] = "f" * 64
            mismatched["content_hash"] = canonical_creation_hash(mismatched)
            rejected_path = root / "mismatched-target.json"
            with self.assertRaisesRegex(GenericAssetError, "gamepack.*mismatch"):
                publish_asset_target(
                    rejected_path,
                    mismatched,
                    gamepack=gamepack,
                    subject=subject,
                )
            self.assertFalse(rejected_path.exists())

    def test_fixture_generator_and_cross_root_bytes_are_deterministic(self) -> None:
        for case in ("abstract-puzzle", "branching-narrative"):
            with self.subTest(case=case):
                for path, _, expected in build_fixture_documents(case):
                    self.assertEqual(expected, path.read_bytes())

                source = EXAMPLES / case / "artifacts" / f"{case}.gamepack.json"
                with tempfile.TemporaryDirectory() as temp:
                    roots = [Path(temp) / "left", Path(temp) / "right"]
                    chains = []
                    for root in roots:
                        root.mkdir()
                        copied = root / "gamepack.json"
                        copied.write_bytes(source.read_bytes())
                        loaded = load_gamepack(copied)
                        chains.append(_build_chain_from_gamepack(loaded, case))
                serialized_chains: list[list[bytes]] = []
                for gamepack, subject, target, style, inventory in chains:
                    outputs, criteria = (
                        _puzzle_spec_inputs() if case == "abstract-puzzle" else _font_spec_inputs()
                    )
                    specification = build_asset_specification(
                        gamepack,
                        subject,
                        target,
                        style,
                        inventory,
                        asset_id=inventory["assets"][0]["asset_id"],
                        outputs=outputs,
                        acceptance_criteria=criteria,
                        production_class=(
                            "procedural_offline" if case == "abstract-puzzle" else "human"
                        ),
                        review_requirements={
                            "human_review_required": True,
                            "qa_profile": (
                                "ui_readability" if case == "abstract-puzzle" else "localized_text"
                            ),
                            "evidence_required": True,
                        },
                    )
                    serialized_chains.append(
                        [
                            serialize_asset_contract(item)
                            for item in (
                                subject,
                                target,
                                style,
                                inventory,
                                specification,
                            )
                        ]
                    )
                left, right = serialized_chains
                self.assertEqual(left, right)

    def test_secure_readers_reject_symlinks_hardlinks_and_malformed_json(self) -> None:
        gamepack_path = EXAMPLES / "abstract-puzzle" / "artifacts" / "abstract-puzzle.gamepack.json"
        gamepack = load_gamepack(gamepack_path)
        subject = build_asset_subject(gamepack)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid = root / "subject.json"
            valid.write_bytes(serialize_asset_contract(subject))

            link = root / "link.json"
            try:
                link.symlink_to(valid.name)
            except OSError:
                pass
            else:
                with self.assertRaises(GenericAssetError):
                    load_asset_subject(link, gamepack_path=gamepack_path)

            hardlink = root / "hardlink.json"
            try:
                os.link(valid, hardlink)
            except OSError:
                pass
            else:
                with self.assertRaises(GenericAssetError):
                    load_asset_subject(hardlink, gamepack_path=gamepack_path)

            malformed = root / "malformed.json"
            malformed.write_text('{"format":"x","format":"y"}', encoding="utf-8")
            with self.assertRaises(GenericAssetError):
                load_asset_subject(malformed, gamepack_path=gamepack_path)

            oversized = root / "oversized.json"
            oversized.write_bytes(b"{" + b" " * MAX_CREATION_CONTRACT_BYTES + b"}")
            with self.assertRaisesRegex(GenericAssetError, "limit|exceeds"):
                load_asset_subject(oversized, gamepack_path=gamepack_path)

            deep = root / "deep.json"
            nested = "null"
            for _ in range(MAX_CREATION_JSON_DEPTH + 2):
                nested = '{"x":' + nested + "}"
            deep.write_text(nested, encoding="utf-8")
            with self.assertRaisesRegex(GenericAssetError, "depth"):
                load_asset_subject(deep, gamepack_path=gamepack_path)

    def test_pre_expansion_bounds_fail_before_grouping(self) -> None:
        gamepack = _gamepack("abstract-puzzle")
        subject = build_asset_subject(gamepack)
        bindings = _puzzle_target_input() * 1025
        with self.assertRaisesRegex(GenericAssetError, "preflight|limit"):
            build_asset_target(
                gamepack,
                subject,
                review=_review(),
                bindings=bindings,
            )


if __name__ == "__main__":
    unittest.main()
