from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.models import RUNTIME_API_VERSION, SUPPORTED_RUNTIME_FEATURES
from worldforge.compiler import build_worldpack
from worldforge.integrity import canonical_payload_hash
from worldforge.project import SourceProject, load_source_project
from worldforge.validation import validate_project

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "examples/foundation/source/manifest.json"


def _foundation_project() -> SourceProject:
    original = load_source_project(MANIFEST)
    return SourceProject(
        original.manifest_path,
        deepcopy(original.world),
        deepcopy(original.collections),
    )


def _campaign_project(*, with_locales: bool = True) -> SourceProject:
    project = _foundation_project()
    for actor in project.collections["actors"]:
        if actor["id"] == "explorer":
            actor["personal_arc_id"] = "explorer_journey"
    project.collections["personal_arcs"] = [
        {
            "id": "explorer_journey",
            "actor_id": "explorer",
            "start_act_id": "first_steps",
            "acts": [
                {
                    "id": "stone_answer",
                    "quest_ids": [],
                    "scene_ids": ["morning_resonance"],
                    "next_act_ids": [],
                },
                {
                    "id": "first_steps",
                    "quest_ids": ["resonance_trial"],
                    "scene_ids": [],
                    "next_act_ids": ["stone_answer"],
                },
            ],
        }
    ]
    if with_locales:
        spanish = dict(project.world["ui"])
        english = {key: f"English: {value}" for key, value in spanish.items()}
        project.world["default_locale"] = "es"
        project.world["supported_locales"] = ["es", "en"]
        project.collections["locales"] = [
            {
                "id": "spanish",
                "language_tag": "es",
                "strings": spanish,
            },
            {
                "id": "english",
                "language_tag": "en",
                "strings": english,
            },
        ]
    return project


def _load_payload(payload: dict[str, object]):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "test.worldpack.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_worldpack(path)


def _rehash(payload: dict[str, object]) -> None:
    payload["content_hash"] = canonical_payload_hash(payload)


class M4ContentContractTests(unittest.TestCase):
    def test_compiler_emits_deterministic_v5_defaults_without_forcing_campaigns(self) -> None:
        project = _foundation_project()

        first = build_worldpack(project)
        second = build_worldpack(project)

        self.assertEqual(first, second)
        self.assertEqual(5, first["format_version"])
        self.assertEqual([], first["collections"]["personal_arcs"])
        self.assertEqual([], first["collections"]["locales"])
        self.assertEqual("es", first["world"]["default_locale"])
        self.assertEqual(["es"], first["world"]["supported_locales"])
        self.assertEqual("0.5.0", first["runtime_requirements"]["runtime_api"]["minimum"])
        self.assertEqual(
            sorted(project.world["capabilities"]),
            first["runtime_requirements"]["required_features"],
        )

    def test_v4_worldpack_keeps_legacy_loading_defaults(self) -> None:
        payload = build_worldpack(_foundation_project())
        payload["format_version"] = 4
        payload.pop("runtime_requirements")
        payload["world"].pop("default_locale")
        payload["world"].pop("supported_locales")
        payload["collections"].pop("personal_arcs")
        payload["collections"].pop("locales")
        _rehash(payload)
        pack = _load_payload(payload)

        self.assertEqual(4, pack.format_version)
        self.assertEqual("es", pack.default_locale)
        self.assertEqual(("es",), pack.supported_locales)
        self.assertEqual({}, pack.personal_arcs)
        self.assertEqual({}, pack.locales)
        self.assertEqual((), pack.runtime_requirements.required_features)

    def test_v5_loads_typed_campaign_acts_and_locales(self) -> None:
        project = _campaign_project()
        payload = build_worldpack(project)
        pack = _load_payload(payload)

        campaign = pack.personal_arcs["explorer_journey"]
        self.assertIs(campaign, pack.personal_campaigns["explorer_journey"])
        self.assertIs(campaign, pack.personal_campaign_for_actor("explorer"))
        self.assertEqual("first_steps", campaign.start_act_id)
        self.assertEqual(("stone_answer",), campaign.acts["first_steps"].next_act_ids)
        self.assertEqual(("resonance_trial",), campaign.acts["first_steps"].quest_ids)
        self.assertIn("personal_campaigns", pack.runtime_requirements.required_features)
        self.assertIn("locales", pack.runtime_requirements.required_features)
        self.assertEqual("english", pack.locale_for_language_tag("EN").id)
        self.assertEqual(
            sorted(pack.locales["spanish"].strings),
            list(pack.locales["spanish"].strings),
        )
        self.assertTrue(
            pack.compatibility_with(RUNTIME_API_VERSION, SUPPORTED_RUNTIME_FEATURES).compatible
        )

    def test_compatibility_missing_optional_features_is_non_fatal(self) -> None:
        project = _campaign_project(with_locales=False)
        project.world["runtime_requirements"] = {
            "runtime_api": {"minimum": "0.5.0", "maximum_exclusive": "0.6.0"},
            "required_features": [*project.world["capabilities"], "personal_campaigns"],
            "optional_features": ["photo_mode"],
        }
        pack = _load_payload(build_worldpack(project))
        runtime_features = set(pack.runtime_requirements.required_features)

        compatible = pack.compatibility_with("0.5.1", runtime_features)
        self.assertTrue(compatible.compatible)
        self.assertEqual(("photo_mode",), compatible.missing_optional_features)

        runtime_features.remove("world_clock")
        missing_required = pack.compatibility_with("0.5.1", runtime_features)
        self.assertFalse(missing_required.compatible)
        self.assertEqual(("world_clock",), missing_required.missing_required_features)

        wrong_api = pack.compatibility_with("0.6.0", pack.runtime_requirements.required_features)
        self.assertFalse(wrong_api.compatible)
        self.assertFalse(wrong_api.api_compatible)

    def test_campaign_validation_rejects_owner_graph_and_content_reference_errors(self) -> None:
        project = _campaign_project(with_locales=False)
        campaign = project.collections["personal_arcs"][0]
        campaign["start_act_id"] = "missing_act"
        campaign["acts"][0]["next_act_ids"] = ["missing_act"]
        campaign["acts"][0]["quest_ids"] = ["missing_quest"]
        campaign["acts"][0]["scene_ids"] = ["missing_scene"]
        duplicate = deepcopy(campaign)
        duplicate["id"] = "second_journey"
        project.collections["personal_arcs"].append(duplicate)

        messages = [str(issue) for issue in validate_project(project)]

        self.assertTrue(any("unknown start act" in message for message in messages))
        self.assertTrue(any("unknown act" in message for message in messages))
        self.assertTrue(any("unknown reference: missing_quest" in message for message in messages))
        self.assertTrue(any("unknown reference: missing_scene" in message for message in messages))
        self.assertTrue(any("already owns campaign" in message for message in messages))

    def test_campaign_owner_must_be_playable(self) -> None:
        project = _foundation_project()
        guide = next(actor for actor in project.collections["actors"] if actor["id"] == "guide")
        guide["personal_arc_id"] = "guide_story"
        project.collections["personal_arcs"] = [
            {
                "id": "guide_story",
                "actor_id": "guide",
                "start_act_id": "only_act",
                "acts": [{"id": "only_act"}],
            }
        ]

        messages = [issue.message for issue in validate_project(project)]

        self.assertIn("campaign owner must be playable", messages)

    def test_campaign_validation_rejects_acts_unreachable_from_start(self) -> None:
        project = _campaign_project(with_locales=False)
        project.collections["personal_arcs"][0]["acts"].append(
            {
                "id": "forgotten_path",
                "quest_ids": [],
                "scene_ids": [],
                "next_act_ids": [],
            }
        )

        messages = [str(issue) for issue in validate_project(project)]

        self.assertTrue(
            any(
                "forgotten_path" in message and "unreachable from start act: first_steps" in message
                for message in messages
            )
        )

    def test_loader_rejects_resealed_worldpack_with_unreachable_campaign_act(self) -> None:
        payload = build_worldpack(_campaign_project())
        payload["collections"]["personal_arcs"][0]["acts"].append(
            {
                "id": "forgotten_path",
                "quest_ids": [],
                "scene_ids": [],
                "next_act_ids": [],
            }
        )
        _rehash(payload)

        with self.assertRaisesRegex(
            WorldPackError,
            "acts unreachable from start act first_steps: forgotten_path",
        ):
            _load_payload(payload)

    def test_campaign_reachability_allows_reachable_cycles(self) -> None:
        project = _campaign_project(with_locales=False)
        campaign = project.collections["personal_arcs"][0]
        campaign["acts"][0]["next_act_ids"] = ["first_steps"]

        self.assertFalse(
            any(
                "unreachable from start act" in issue.message for issue in validate_project(project)
            )
        )
        pack = _load_payload(build_worldpack(project))
        self.assertEqual(
            ("first_steps",),
            pack.personal_arcs["explorer_journey"].acts["stone_answer"].next_act_ids,
        )

    def test_locale_validation_rejects_duplicates_incomplete_maps_and_mismatches(self) -> None:
        project = _campaign_project()
        project.collections["locales"][1]["language_tag"] = "ES"
        project.collections["locales"][1]["strings"].pop("active_actor")

        messages = [issue.message for issue in validate_project(project)]

        self.assertTrue(any("duplicate language tag" in message for message in messages))
        self.assertTrue(any("identical keys" in message for message in messages))
        self.assertTrue(
            any("does not match locale language tags" in message for message in messages)
        )

    def test_locale_validation_rejects_invalid_bcp47_and_legacy_default_mismatch(self) -> None:
        project = _campaign_project()
        project.world["default_locale"] = "en"
        project.collections["locales"][1]["language_tag"] = "en_US"

        messages = [str(issue) for issue in validate_project(project)]

        self.assertTrue(any("BCP47" in message for message in messages))
        self.assertTrue(any("legacy language must match" in message for message in messages))

    def test_loader_rejects_malformed_v5_runtime_and_content_contracts(self) -> None:
        base = build_worldpack(_campaign_project())
        mutations = {
            "missing runtime requirements": lambda raw: raw.pop("runtime_requirements"),
            "empty runtime range": lambda raw: raw["runtime_requirements"]["runtime_api"].update(
                {"minimum": "0.6.0", "maximum_exclusive": "0.6.0"}
            ),
            "missing M4 collection": lambda raw: raw["collections"].pop("locales"),
            "unknown next act": lambda raw: raw["collections"]["personal_arcs"][0]["acts"][
                0
            ].update({"next_act_ids": ["unknown_act"]}),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                malformed = deepcopy(base)
                mutate(malformed)
                _rehash(malformed)
                with self.assertRaises(WorldPackError):
                    _load_payload(malformed)


if __name__ == "__main__":
    unittest.main()
