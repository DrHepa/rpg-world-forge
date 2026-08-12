from __future__ import annotations

import contextlib
import copy
import inspect
import io
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from scripts.generate_generic_runtime_fixtures import build_runtime_fixtures
from scripts.generate_generic_runtime_schemas import build_schemas
from worldforge.__main__ import _resolve_generic_assetpack_cli_source, main
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.generic_assetpack import seal_generic_assetpack
from worldforge.generic_runtime import (
    RuntimeContractError,
    build_builtin_runtime_adapters,
    build_game_runtime_composition,
    build_game_runtime_snapshot,
    build_historical_runtime_adapters,
    build_runtime_adapter_registry,
    build_runtime_evidence,
    build_runtime_support_report,
    load_game_runtime_composition,
    load_runtime_evidence,
    load_runtime_support_report,
    resolve_required_feature_support,
    resolve_runtime_adapter,
    resolve_runtime_build_readiness,
    resolve_runtime_compatibility,
    serialize_game_runtime_composition,
    serialize_runtime_adapter,
    serialize_runtime_adapter_registry,
    serialize_runtime_evidence,
    serialize_runtime_snapshot,
    serialize_runtime_support_report,
    validate_game_runtime_composition,
    validate_game_runtime_composition_document,
    validate_runtime_adapter_document,
    validate_runtime_adapter_registry_document,
    validate_runtime_evidence_document,
    validate_runtime_snapshot_document,
    validate_runtime_support_report,
    validate_runtime_support_report_document,
)
from worldforge.runtime_support_authority import (
    RUNTIME_SUPPORT_AUTHORITY_FORMAT,
    validate_runtime_support_authority_document,
)

ROOT = Path(__file__).resolve().parents[1]


def _fixture(name: str, relative: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "examples" / "multigenre-contracts" / name / relative).read_text(encoding="utf-8")
    )


def _with_required_features(
    gamepack: dict[str, object],
    features: tuple[str, ...],
) -> dict[str, object]:
    document = copy.deepcopy(gamepack)
    actions = document["logic"]["actions"]
    mechanics = document["logic"]["mechanics"]
    requirements = document["mechanic_requirements"]
    for index, feature in enumerate(features):
        action = actions[index]
        mechanic = next(item for item in mechanics if item["action_id"] == action["id"])
        requirement = next(item for item in requirements if item["mechanic_id"] == mechanic["id"])
        action["required_feature_ids"] = sorted(
            [*action["required_feature_ids"], feature],
            key=lambda item: item.encode("utf-8"),
        )
        mechanic["required_feature_ids"] = copy.deepcopy(action["required_feature_ids"])
        requirement["required_feature_ids"] = copy.deepcopy(action["required_feature_ids"])
    document["runtime_requirements"]["required_features"] = sorted(
        {feature for mechanic in mechanics for feature in mechanic["required_feature_ids"]},
        key=lambda item: item.encode("utf-8"),
    )
    document["content_hash"] = canonical_creation_hash(document)
    return document


@contextmanager
def _sealed_fixture(name: str, root: Path):
    source = _resolve_generic_assetpack_cli_source(
        ROOT / "examples" / "multigenre-contracts" / name / "assets" / "manifest.json"
    )
    verified = seal_generic_assetpack(root / f"{name}-assetpack", **source)
    try:
        yield verified
    finally:
        verified.close()


class GenericRuntimeContractTests(unittest.TestCase):
    def _registry(self) -> tuple[dict[str, object], dict[str, object]]:
        adapters = build_builtin_runtime_adapters()
        snapshot = build_game_runtime_snapshot(
            ROOT / "src" / "gamepack_runtime",
            adapter_runtime_root=ROOT / "src" / "gamepack_raylib_2d",
            adapters=adapters,
        )
        registry = build_runtime_adapter_registry(
            adapters=adapters,
            snapshot=snapshot,
        )
        return snapshot, registry

    def test_builtin_descriptors_are_closed_declarative_and_profile_specific(self) -> None:
        adapters = build_builtin_runtime_adapters()

        self.assertEqual(
            [adapter["adapter_id"] for adapter in adapters],
            ["gamepack_raylib_2d_puzzle", "gamepack_raylib_2d_text"],
        )
        puzzle, narrative = adapters
        self.assertEqual(puzzle["adapter_version"], "1.1.0")
        self.assertEqual(puzzle["implementation"]["backend"], "backend:raylib")
        self.assertEqual(puzzle["implementation"]["renderer"], "raylib")
        self.assertEqual(
            puzzle["supported_profiles"],
            ["profile:abstract_puzzle"],
        )
        self.assertEqual(
            narrative["supported_profiles"],
            ["profile:branching_narrative"],
        )
        encoded = b"".join(serialize_runtime_adapter(adapter) for adapter in adapters)
        for forbidden in (b"import_path", b"module_path", b"source_path", b"python", b"script"):
            self.assertNotIn(forbidden, encoded)

        for adapter in adapters:
            self.assertEqual(adapter, validate_runtime_adapter_document(adapter))
        historical = build_historical_runtime_adapters()
        self.assertEqual(
            [
                (item["adapter_id"], item["adapter_version"], item["content_hash"])
                for item in historical
            ],
            [
                (
                    "gamepack_raylib_2d_puzzle",
                    "1.0.0",
                    "7122c4b2d27e64511c76ee3fa1d4a29962bebfcc006d60566d3da5697827d05e",
                ),
                (
                    "gamepack_raylib_2d_text",
                    "1.0.0",
                    "4372a09ca27ba05b748f6f0f545d8b340ad29957cbcfba4485cd26893317e87b",
                ),
            ],
        )

    def test_snapshot_and_registry_are_root_order_mtime_and_platform_independent(self) -> None:
        adapters = build_builtin_runtime_adapters()
        with (
            tempfile.TemporaryDirectory(prefix="wf-runtime-a-") as first,
            tempfile.TemporaryDirectory(prefix="wf-runtime-b-") as second,
        ):
            first_root = Path(first)
            second_root = Path(second)
            payloads = {
                "__init__.py": b'"""neutral runtime"""\\n',
                "engine.py": b"VALUE = 1\\n",
                "nested/state.py": b"STATE = {}\\n",
            }
            for root, order in (
                (first_root, tuple(payloads)),
                (second_root, tuple(reversed(payloads))),
            ):
                for relative in order:
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payloads[relative])
                for path in root.rglob("*.py"):
                    path.touch()

            first_snapshot = build_game_runtime_snapshot(first_root, adapters=adapters)
            second_snapshot = build_game_runtime_snapshot(second_root, adapters=adapters)
            self.assertEqual(
                serialize_runtime_snapshot(first_snapshot),
                serialize_runtime_snapshot(second_snapshot),
            )
            self.assertNotIn(first.encode(), serialize_runtime_snapshot(first_snapshot))
            self.assertNotIn(second.encode(), serialize_runtime_snapshot(second_snapshot))
            self.assertEqual(
                first_snapshot,
                validate_runtime_snapshot_document(first_snapshot),
            )

            registry = build_runtime_adapter_registry(
                adapters=adapters,
                snapshot=first_snapshot,
            )
            self.assertEqual(
                registry,
                validate_runtime_adapter_registry_document(
                    registry,
                    snapshot=first_snapshot,
                ),
            )
            self.assertTrue(serialize_runtime_adapter_registry(registry).endswith(b"\n"))

            tampered_snapshot = copy.deepcopy(first_snapshot)
            descriptor_file = next(
                item
                for item in tampered_snapshot["files"]
                if item["path"].startswith("descriptors/gamepack_raylib_2d_puzzle@")
            )
            descriptor_file["sha256"] = "f" * 64
            tampered_snapshot["tree_hash"] = canonical_creation_hash(
                {"files": tampered_snapshot["files"]}
            )
            tampered_snapshot["snapshot_id"] = (
                "runtime_snapshot_"
                + canonical_creation_hash(
                    {
                        "runtime_api": tampered_snapshot["runtime_api"],
                        "adapter_descriptors": tampered_snapshot["adapter_descriptors"],
                        "files": tampered_snapshot["files"],
                        "tree_hash": tampered_snapshot["tree_hash"],
                    }
                )[:40]
            )
            tampered_snapshot["content_hash"] = canonical_creation_hash(tampered_snapshot)
            tampered_registry = copy.deepcopy(registry)
            tampered_registry["runtime_snapshot"] = {
                "format": tampered_snapshot["format"],
                "format_version": tampered_snapshot["format_version"],
                "id": tampered_snapshot["snapshot_id"],
                "content_hash": tampered_snapshot["content_hash"],
            }
            tampered_registry["registry_id"] = (
                "runtime_registry_"
                + canonical_creation_hash(
                    {
                        "runtime_snapshot": tampered_registry["runtime_snapshot"],
                        "adapters": tampered_registry["adapters"],
                    }
                )[:40]
            )
            tampered_registry["content_hash"] = canonical_creation_hash(tampered_registry)
            with self.assertRaisesRegex(
                RuntimeContractError,
                "runtime_snapshot_descriptor_mismatch",
            ):
                validate_runtime_adapter_registry_document(
                    tampered_registry,
                    snapshot=tampered_snapshot,
                )

    def test_resolution_is_capability_based_and_fails_closed_on_zero_or_ambiguous_match(
        self,
    ) -> None:
        self.assertNotIn(
            "_validated_gamepack",
            inspect.signature(resolve_runtime_adapter).parameters,
        )
        snapshot, registry = self._registry()
        puzzle = _fixture(
            "abstract-puzzle",
            "artifacts/abstract-puzzle.gamepack.json",
        )
        narrative = _fixture(
            "branching-narrative",
            "artifacts/branching-narrative.gamepack.json",
        )

        resolved = resolve_runtime_adapter(
            puzzle,
            registry=registry,
            snapshot=snapshot,
        )
        self.assertEqual(resolved["adapter_id"], "gamepack_raylib_2d_puzzle")
        self.assertEqual(resolved["state"], "declared")
        self.assertEqual(
            resolve_runtime_adapter(
                narrative,
                registry=registry,
                snapshot=snapshot,
            )["adapter_id"],
            "gamepack_raylib_2d_text",
        )

        wrong_request = copy.deepcopy(puzzle)
        wrong_request["runtime_requirements"]["requested_adapter"] = "gamepack_raylib_2d_text"
        wrong_request["content_hash"] = ""
        wrong_request["content_hash"] = canonical_creation_hash(wrong_request)
        with self.assertRaisesRegex(RuntimeContractError, "adapter_zero_match"):
            resolve_runtime_adapter(
                wrong_request,
                registry=registry,
                snapshot=snapshot,
            )

        duplicate = copy.deepcopy(registry)
        duplicate_adapter = copy.deepcopy(duplicate["adapters"][0])
        duplicate_adapter["adapter_id"] = "gamepack_raylib_2d_puzzle_alias"
        duplicate_adapter["content_hash"] = canonical_creation_hash(duplicate_adapter)
        duplicate["adapters"].append(duplicate_adapter)
        duplicate["adapters"].sort(key=lambda item: item["adapter_id"].encode("utf-8"))
        duplicate["registry_id"] = (
            "runtime_registry_"
            + canonical_creation_hash(
                {
                    "runtime_snapshot": duplicate["runtime_snapshot"],
                    "adapters": duplicate["adapters"],
                }
            )[:40]
        )
        duplicate["content_hash"] = canonical_creation_hash(duplicate)
        with self.assertRaisesRegex(
            RuntimeContractError,
            "runtime_snapshot_descriptor_mismatch",
        ):
            resolve_runtime_adapter(
                puzzle,
                registry=duplicate,
                snapshot=snapshot,
            )

    def test_executable_shape_blocks_resolution_and_readiness_without_adapter_fallback(
        self,
    ) -> None:
        snapshot, registry = self._registry()
        puzzle = _fixture(
            "abstract-puzzle",
            "artifacts/abstract-puzzle.gamepack.json",
        )
        for state_id in ("board", "target"):
            state = next(item for item in puzzle["logic"]["state_schema"] if item["id"] == state_id)
            state["allowed_values"].append("D")
            state["initial"].append("D")
            state["min_items"] = 4
            state["max_items"] = 4
            puzzle["logic"]["initial_state"][state_id].append("D")
        for parameter in puzzle["logic"]["actions"][1]["parameters"]:
            parameter["maximum"] = 3
        puzzle["runtime_requirements"]["requested_adapter"] = None
        puzzle["content_hash"] = canonical_creation_hash(puzzle)

        with self.assertRaisesRegex(
            RuntimeContractError,
            "^adapter_executable_shape_unsupported:",
        ):
            resolve_runtime_adapter(
                puzzle,
                registry=registry,
                snapshot=snapshot,
            )
        readiness = resolve_runtime_build_readiness(
            puzzle,
            registry=registry,
            snapshot=snapshot,
        )
        self.assertEqual(readiness["status"], "unsupported")
        self.assertIsNone(readiness["adapter"])
        self.assertEqual(readiness["missing_required_feature_ids"], [])
        self.assertEqual(
            readiness["reason_codes"],
            ["adapter_executable_shape_unsupported"],
        )

    def test_invalid_compiler_transition_remains_runtime_gamepack_invalid(self) -> None:
        snapshot, registry = self._registry()
        narrative = _fixture(
            "branching-narrative",
            "artifacts/branching-narrative.gamepack.json",
        )
        narrative["logic"]["narrative_transitions"][0]["effect"]["value"] = "ending_right"
        narrative["content_hash"] = canonical_creation_hash(narrative)

        with self.assertRaisesRegex(RuntimeContractError, "^runtime_gamepack_invalid:"):
            resolve_runtime_adapter(
                narrative,
                registry=registry,
                snapshot=snapshot,
            )

    def test_build_readiness_reports_exact_missing_topology_capability(self) -> None:
        snapshot, registry = self._registry()
        gamepack = _with_required_features(
            _fixture("abstract-puzzle", "artifacts/abstract-puzzle.gamepack.json"),
            ("action:realtime_combat",),
        )

        readiness = resolve_runtime_build_readiness(
            gamepack,
            registry=registry,
            snapshot=snapshot,
        )

        self.assertEqual(readiness["status"], "unsupported")
        self.assertEqual(
            readiness["missing_required_feature_ids"],
            ["action:realtime_combat"],
        )
        self.assertIn("required_feature_unsupported", readiness["reason_codes"])
        self.assertNotIn("supported", readiness)

    def test_build_readiness_keeps_candidate_matching_authoritative_while_aggregating_gaps(
        self,
    ) -> None:
        features = ("action:realtime_combat", "roguelite:run_reset")
        gamepack = _with_required_features(
            _fixture("abstract-puzzle", "artifacts/abstract-puzzle.gamepack.json"),
            features,
        )
        gamepack["runtime_requirements"]["requested_adapter"] = None
        gamepack["content_hash"] = canonical_creation_hash(gamepack)
        first = copy.deepcopy(build_builtin_runtime_adapters()[0])
        second = copy.deepcopy(first)
        first["adapter_id"] = "candidate_action"
        second["adapter_id"] = "candidate_roguelite"
        first["supported_features"] = sorted(
            [*first["supported_features"], features[0]],
            key=lambda item: item.encode("utf-8"),
        )
        second["supported_features"] = sorted(
            [*second["supported_features"], features[1]],
            key=lambda item: item.encode("utf-8"),
        )
        for candidate in (first, second):
            candidate["content_hash"] = canonical_creation_hash(candidate)

        with (
            mock.patch(
                "worldforge.generic_runtime._validate_trusted_runtime_inputs",
                return_value=({}, {}, [first, second]),
            ),
            mock.patch("worldforge.generic_runtime.inspect_adapter_executable_shape"),
        ):
            readiness = resolve_runtime_build_readiness(
                gamepack,
                registry={},
                snapshot={},
            )

        self.assertEqual(readiness["status"], "unsupported")
        self.assertIsNone(readiness["adapter"])
        self.assertEqual(
            readiness["missing_required_feature_ids"],
            ["action:realtime_combat", "roguelite:run_reset"],
        )
        self.assertIn("required_feature_unsupported", readiness["reason_codes"])

        full = copy.deepcopy(first)
        full["adapter_id"] = "candidate_complete"
        full["supported_features"] = sorted(
            [*full["supported_features"], features[1]],
            key=lambda item: item.encode("utf-8"),
        )
        full["content_hash"] = canonical_creation_hash(full)
        with (
            mock.patch(
                "worldforge.generic_runtime._validate_trusted_runtime_inputs",
                return_value=({}, {}, [full, second]),
            ),
            mock.patch("worldforge.generic_runtime.inspect_adapter_executable_shape"),
        ):
            ready = resolve_runtime_build_readiness(
                gamepack,
                registry={},
                snapshot={},
            )
        self.assertEqual(ready["status"], "materialization_ready")
        self.assertEqual(ready["adapter"]["id"], "candidate_complete")
        self.assertEqual(ready["missing_required_feature_ids"], [])

    def test_composition_binds_exact_d1_d3_and_never_embeds_authoring_inventory(
        self,
    ) -> None:
        snapshot, registry = self._registry()
        cases = (
            (
                "abstract-puzzle",
                [
                    (
                        "board_texture",
                        "board_ui",
                        "texture",
                        "assets/ui/board.png",
                    )
                ],
            ),
            (
                "branching-narrative",
                [
                    (
                        "choice_panel",
                        "narrative_ui_font",
                        "font",
                        "assets/fonts/narrative-ui.ttf",
                    ),
                    (
                        "ending_panel",
                        "narrative_ui_font",
                        "font",
                        "assets/fonts/narrative-ui.ttf",
                    ),
                ],
            ),
        )
        with tempfile.TemporaryDirectory(prefix="wf-runtime-compose-") as temporary:
            root = Path(temporary)
            for name, expected in cases:
                with self.subTest(name=name), _sealed_fixture(name, root) as verified:
                    gamepack = _fixture(name, f"artifacts/{name}.gamepack.json")
                    inventory = _fixture(name, "assets/inventory.json")
                    composition = build_game_runtime_composition(
                        gamepack,
                        inventory,
                        verified.root,
                        registry=registry,
                        snapshot=snapshot,
                    )
                    self.assertEqual(
                        [
                            (
                                item["binding_id"],
                                item["asset_id"],
                                item["role"],
                                item["runtime_path"],
                            )
                            for item in composition["bindings"]
                        ],
                        expected,
                    )
                    self.assertEqual(
                        composition["gamepack"]["content_hash"],
                        gamepack["content_hash"],
                    )
                    self.assertEqual(
                        composition["assetpack"]["content_hash"],
                        verified.manifest["content_hash"],
                    )
                    self.assertEqual(len(composition["assetpack"]["root_hash"]), 64)
                    encoded = serialize_game_runtime_composition(composition)
                    self.assertNotIn(b'"assets"', encoded)
                    self.assertNotIn(b'"referencing_subjects"', encoded)
                    self.assertEqual(
                        composition,
                        validate_game_runtime_composition(
                            composition,
                            gamepack=gamepack,
                            inventory=inventory,
                            assetpack_root=verified.root,
                            registry=registry,
                            snapshot=snapshot,
                        ),
                    )
                    path = root / f"{name}.composition.json"
                    path.write_bytes(encoded)
                    self.assertEqual(
                        composition,
                        load_game_runtime_composition(
                            path,
                            gamepack_path=(
                                ROOT
                                / "examples"
                                / "multigenre-contracts"
                                / name
                                / "artifacts"
                                / f"{name}.gamepack.json"
                            ),
                            inventory_path=(
                                ROOT
                                / "examples"
                                / "multigenre-contracts"
                                / name
                                / "assets"
                                / "inventory.json"
                            ),
                            assetpack_root=verified.root,
                            registry=registry,
                            snapshot=snapshot,
                        ),
                    )

    def test_composition_rejects_crossed_lineage_extra_binding_and_mutated_asset_bytes(
        self,
    ) -> None:
        snapshot, registry = self._registry()
        gamepack = _fixture(
            "abstract-puzzle",
            "artifacts/abstract-puzzle.gamepack.json",
        )
        inventory = _fixture("abstract-puzzle", "assets/inventory.json")
        crossed_inventory = _fixture("branching-narrative", "assets/inventory.json")
        with tempfile.TemporaryDirectory(prefix="wf-runtime-reject-") as temporary:
            root = Path(temporary)
            with _sealed_fixture("abstract-puzzle", root) as verified:
                with self.assertRaisesRegex(
                    RuntimeContractError,
                    "runtime_inventory_binding_mismatch",
                ):
                    build_game_runtime_composition(
                        gamepack,
                        crossed_inventory,
                        verified.root,
                        registry=registry,
                        snapshot=snapshot,
                    )

                composition = build_game_runtime_composition(
                    gamepack,
                    inventory,
                    verified.root,
                    registry=registry,
                    snapshot=snapshot,
                )
                mutated = copy.deepcopy(composition)
                mutated["bindings"].append(copy.deepcopy(mutated["bindings"][0]))
                mutated["content_hash"] = canonical_creation_hash(mutated)
                with self.assertRaisesRegex(
                    RuntimeContractError,
                    "runtime_binding_(collision|mismatch)",
                ):
                    validate_game_runtime_composition_document(mutated)

                runtime_path = composition["bindings"][0]["runtime_path"]
                target = verified.root / runtime_path
                target.write_bytes(target.read_bytes() + b"tampered")
                with self.assertRaisesRegex(
                    RuntimeContractError,
                    "runtime_assetpack_invalid",
                ):
                    validate_game_runtime_composition(
                        composition,
                        gamepack=gamepack,
                        inventory=inventory,
                        assetpack_root=verified.root,
                        registry=registry,
                        snapshot=snapshot,
                    )

    def test_blocked_support_report_maps_every_mechanic_without_claiming_execution(
        self,
    ) -> None:
        snapshot, registry = self._registry()
        gamepack = _fixture(
            "abstract-puzzle",
            "artifacts/abstract-puzzle.gamepack.json",
        )
        inventory = _fixture("abstract-puzzle", "assets/inventory.json")
        with tempfile.TemporaryDirectory(prefix="wf-runtime-report-") as temporary:
            root = Path(temporary)
            with _sealed_fixture("abstract-puzzle", root) as verified:
                result = resolve_runtime_compatibility(
                    gamepack,
                    inventory,
                    verified.root,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=[],
                )
                composition = result["composition"]
                report = result["report"]
                self.assertEqual(report["compatibility_status"], "partially_supported")
                self.assertFalse(report["supported"])
                self.assertEqual(report["dimensions"]["authoring"], "valid")
                self.assertEqual(report["dimensions"]["compilation"], "compiled")
                self.assertEqual(report["dimensions"]["assets"], "sealed")
                self.assertEqual(report["dimensions"]["adapter"], "declared")
                self.assertEqual(report["dimensions"]["packaging"], "unverified")
                self.assertEqual(report["dimensions"]["release"], "blocked")
                self.assertEqual(
                    [entry["status"] for entry in report["dimensions"]["execution"]],
                    ["untested", "untested"],
                )
                self.assertEqual(
                    report["reason_codes"],
                    [
                        "adapter_not_verified",
                        "headless_evidence_missing",
                        "native_evidence_missing",
                        "packaging_evidence_missing",
                        "save_replay_evidence_missing",
                    ],
                )
                self.assertEqual(
                    [entry["mechanic_id"] for entry in report["mechanics"]],
                    ["restart_mechanic", "swap_mechanic"],
                )
                self.assertEqual(
                    report["mechanics"][1]["runtime_action_id"],
                    "swap_tiles",
                )
                self.assertEqual(
                    report["mechanics"][1]["authoritative_state_ids"],
                    ["board", "move_count"],
                )
                self.assertEqual(
                    report["mechanics"][1]["asset_binding_ids"],
                    ["board_texture"],
                )
                self.assertEqual(
                    report["mechanics"][1]["save_replay"],
                    {
                        "event_ids": ["tile_swapped"],
                        "state_ids": ["board", "move_count"],
                    },
                )
                self.assertTrue(
                    all(
                        item["status"] == "authoring_only"
                        for item in (*report["features"], *report["mechanics"])
                    )
                )
                self.assertEqual(
                    report,
                    build_runtime_support_report(
                        composition,
                        gamepack=gamepack,
                        registry=registry,
                        snapshot=snapshot,
                        evidence=[],
                    ),
                )
                self.assertEqual(
                    report,
                    validate_runtime_support_report(
                        report,
                        composition=composition,
                        gamepack=gamepack,
                        registry=registry,
                        snapshot=snapshot,
                        evidence=[],
                    ),
                )

                composition_path = root / "composition.json"
                report_path = root / "support.json"
                composition_path.write_bytes(serialize_game_runtime_composition(composition))
                report_path.write_bytes(serialize_runtime_support_report(report))
                self.assertEqual(
                    report,
                    load_runtime_support_report(
                        report_path,
                        composition_path=composition_path,
                        gamepack_path=(
                            ROOT
                            / "examples"
                            / "multigenre-contracts"
                            / "abstract-puzzle"
                            / "artifacts"
                            / "abstract-puzzle.gamepack.json"
                        ),
                        registry=registry,
                        snapshot=snapshot,
                        evidence=[],
                    ),
                )

    def test_external_evidence_is_exactly_bound_and_declared_adapter_still_blocks_release(
        self,
    ) -> None:
        snapshot, registry = self._registry()
        gamepack = _fixture(
            "abstract-puzzle",
            "artifacts/abstract-puzzle.gamepack.json",
        )
        inventory = _fixture("abstract-puzzle", "assets/inventory.json")
        with tempfile.TemporaryDirectory(prefix="wf-runtime-evidence-") as temporary:
            root = Path(temporary)
            with _sealed_fixture("abstract-puzzle", root) as verified:
                composition = build_game_runtime_composition(
                    gamepack,
                    inventory,
                    verified.root,
                    registry=registry,
                    snapshot=snapshot,
                )
                checks = [
                    {
                        "check_id": "check:headless_determinism",
                        "kind": "headless",
                        "status": "passed",
                        "evidence_id": "puzzle_headless_linux",
                        "content_hash": "1" * 64,
                    },
                    {
                        "check_id": "check:native_raylib",
                        "kind": "native",
                        "status": "passed",
                        "evidence_id": "puzzle_native_linux",
                        "content_hash": "2" * 64,
                    },
                    {
                        "check_id": "check:package_verification",
                        "kind": "packaging",
                        "status": "passed",
                        "evidence_id": "puzzle_package_linux",
                        "content_hash": "3" * 64,
                    },
                    {
                        "check_id": "check:save_replay",
                        "kind": "save_replay",
                        "status": "passed",
                        "evidence_id": "puzzle_replay_linux",
                        "content_hash": "4" * 64,
                    },
                ]
                evidence = build_runtime_evidence(
                    composition,
                    platform_id="platform:linux_x86_64",
                    execution_status="native_verified",
                    packaging_status="verified",
                    checks=checks,
                )
                self.assertEqual(
                    evidence,
                    validate_runtime_evidence_document(
                        evidence,
                        composition=composition,
                    ),
                )
                self.assertTrue(serialize_runtime_evidence(evidence).endswith(b"\n"))
                evidence_path = root / "evidence.json"
                evidence_path.write_bytes(serialize_runtime_evidence(evidence))
                self.assertEqual(
                    evidence,
                    load_runtime_evidence(
                        evidence_path,
                        composition=composition,
                    ),
                )

                report = build_runtime_support_report(
                    composition,
                    gamepack=gamepack,
                    registry=registry,
                    snapshot=snapshot,
                    evidence=[evidence],
                )
                self.assertFalse(report["supported"])
                self.assertEqual(report["dimensions"]["adapter"], "declared")
                self.assertEqual(report["dimensions"]["release"], "blocked")
                self.assertEqual(
                    [entry["status"] for entry in report["dimensions"]["execution"]],
                    ["native_verified", "untested"],
                )
                self.assertIn("adapter_not_verified", report["reason_codes"])
                self.assertIn("native_evidence_missing", report["reason_codes"])

                wrong_platform = copy.deepcopy(evidence)
                wrong_platform["platform"]["platform_id"] = "platform:windows_x86_64"
                wrong_platform["content_hash"] = canonical_creation_hash(wrong_platform)
                with self.assertRaisesRegex(
                    RuntimeContractError,
                    "runtime_(evidence_platform_mismatch|platform_invalid)",
                ):
                    validate_runtime_evidence_document(
                        wrong_platform,
                        composition=composition,
                    )

                incomplete = copy.deepcopy(evidence)
                incomplete["checks"] = [
                    check for check in incomplete["checks"] if check["kind"] != "native"
                ]
                incomplete["content_hash"] = canonical_creation_hash(incomplete)
                with self.assertRaisesRegex(
                    RuntimeContractError,
                    "runtime_evidence_status_mismatch",
                ):
                    validate_runtime_evidence_document(
                        incomplete,
                        composition=composition,
                    )

                overclaim = copy.deepcopy(report)
                overclaim["supported"] = True
                overclaim["dimensions"]["release"] = "ready"
                overclaim["content_hash"] = canonical_creation_hash(overclaim)
                with self.assertRaisesRegex(
                    RuntimeContractError,
                    "runtime_support_overclaim",
                ):
                    validate_runtime_support_report_document(overclaim)

    def test_required_features_never_downgrade_to_optional_or_genre_inference(self) -> None:
        puzzle, narrative = build_builtin_runtime_adapters()

        self.assertEqual(
            resolve_required_feature_support(
                ["logic:finite_state", "logic:foldback"],
                puzzle,
            ),
            {
                "missing_feature_ids": ["logic:foldback"],
                "status": "unsupported",
                "supported_feature_ids": ["logic:finite_state"],
            },
        )
        self.assertEqual(
            resolve_required_feature_support(
                [
                    "action:realtime_combat",
                    "roguelite:run_reset",
                    "simulation:economy",
                    "sports:season",
                    "strategy:turn_order",
                ],
                narrative,
            )["missing_feature_ids"],
            [
                "action:realtime_combat",
                "roguelite:run_reset",
                "simulation:economy",
                "sports:season",
                "strategy:turn_order",
            ],
        )

    def test_runtime_schemas_are_additive_closed_and_semantically_bound(self) -> None:
        schemas = build_schemas()
        expected = {
            "generic-runtime-adapter.schema.json": "world-forge.runtime_adapter",
            "generic-runtime-adapter-registry.schema.json": (
                "world-forge.runtime_adapter_registry"
            ),
            "game-runtime-snapshot.schema.json": ("world-forge.game_runtime_snapshot"),
            "game-runtime-composition.schema.json": ("world-forge.game_runtime_composition"),
            "generic-runtime-evidence.schema.json": "world-forge.runtime_evidence",
            "generic-runtime-support-report.schema.json": ("world-forge.runtime_support_report"),
            "runtime-support-authority.schema.json": ("world-forge.runtime_support_authority"),
        }
        self.assertEqual(set(schemas), set(expected))
        for name, format_name in expected.items():
            with self.subTest(schema=name):
                schema = schemas[name]
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(schema["properties"]["format"]["const"], format_name)
                self.assertEqual(schema["properties"]["format_version"]["const"], 1)
                self.assertTrue(schema["x-world-forge-canonical-content-hash"])
                self.assertIsInstance(
                    schema["x-world-forge-generic-runtime-coherent"],
                    str,
                )
                self.assertTrue(schema["$id"].startswith("https://world-forge.local/"))

        legacy = json.loads(
            (ROOT / "schemas" / "runtime-adapter.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            legacy["properties"]["format"]["const"],
            "rpg-world-forge.runtime_adapter",
        )

    def test_runtime_fixture_generator_is_deterministic_and_blocked_truthfully(self) -> None:
        generated = build_runtime_fixtures(ROOT)
        expected_paths = {
            "examples/multigenre-contracts/runtime/adapters/gamepack_raylib_2d_puzzle.json",
            "examples/multigenre-contracts/runtime/adapters/gamepack_raylib_2d_text.json",
            (
                "examples/multigenre-contracts/runtime/adapters/historical/"
                "gamepack_raylib_2d_puzzle@1.0.0.json"
            ),
            (
                "examples/multigenre-contracts/runtime/adapters/historical/"
                "gamepack_raylib_2d_text@1.0.0.json"
            ),
            "examples/multigenre-contracts/runtime/snapshot.json",
            "examples/multigenre-contracts/runtime/registry.json",
            "examples/multigenre-contracts/abstract-puzzle/runtime/composition.json",
            "examples/multigenre-contracts/abstract-puzzle/runtime/support-report.json",
            "examples/multigenre-contracts/abstract-puzzle/runtime/support-authority.json",
            "examples/multigenre-contracts/branching-narrative/runtime/composition.json",
            "examples/multigenre-contracts/branching-narrative/runtime/support-report.json",
            "examples/multigenre-contracts/branching-narrative/runtime/support-authority.json",
            "tests/fixtures/generic-runtime/parity-corpus.json",
            "tests/fixtures/generic-runtime/unsupported-capabilities.json",
        }
        self.assertEqual(set(generated), expected_paths)
        for relative, payload in generated.items():
            with self.subTest(path=relative):
                self.assertEqual((ROOT / relative).read_bytes(), payload)
        for name in ("abstract-puzzle", "branching-narrative"):
            report = json.loads(
                generated[f"examples/multigenre-contracts/{name}/runtime/support-report.json"]
            )
            authority = validate_runtime_support_authority_document(
                json.loads(
                    generated[
                        f"examples/multigenre-contracts/{name}/runtime/support-authority.json"
                    ]
                )
            )
            self.assertFalse(report["supported"])
            self.assertEqual(report["dimensions"]["release"], "blocked")
            self.assertEqual(report["dimensions"]["adapter"], "declared")
            self.assertIn("native_evidence_missing", report["reason_codes"])
            self.assertEqual(RUNTIME_SUPPORT_AUTHORITY_FORMAT, authority["format"])
            self.assertEqual(
                report["content_hash"],
                authority["runtime_support_report"]["content_hash"],
            )
            self.assertEqual([], authority["headless_evidence"])
            self.assertIsNone(authority["package_evidence"])
            self.assertEqual(
                _fixture(name, f"artifacts/{name}.gamepack.json")["content_hash"],
                authority["gamepack"]["content_hash"],
            )

    def test_inspect_game_runtime_cli_reports_blocked_machine_status_without_certifying(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-cli-") as temporary:
            root = Path(temporary)
            snapshot, registry = self._registry()
            snapshot_path = root / "snapshot.json"
            registry_path = root / "registry.json"
            snapshot_path.write_bytes(serialize_runtime_snapshot(snapshot))
            registry_path.write_bytes(serialize_runtime_adapter_registry(registry))
            with _sealed_fixture("abstract-puzzle", root) as verified:
                arguments = [
                    "worldforge",
                    "inspect-game-runtime",
                    str(
                        ROOT
                        / "examples"
                        / "multigenre-contracts"
                        / "abstract-puzzle"
                        / "artifacts"
                        / "abstract-puzzle.gamepack.json"
                    ),
                    str(
                        ROOT
                        / "examples"
                        / "multigenre-contracts"
                        / "abstract-puzzle"
                        / "assets"
                        / "inventory.json"
                    ),
                    str(verified.root),
                    "--registry",
                    str(registry_path),
                    "--snapshot",
                    str(snapshot_path),
                ]
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch("sys.argv", arguments),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(main(), 0)
                self.assertEqual(stderr.getvalue(), "")
                payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["status"], "blocked")
                self.assertEqual(
                    payload["compatibility_status"],
                    "partially_supported",
                )
                self.assertFalse(payload["supported"])
                self.assertEqual(payload["adapter"], "declared")
                self.assertIn("native_evidence_missing", payload["reason_codes"])
                self.assertRegex(payload["composition_hash"], "^[0-9a-f]{64}$")
                self.assertRegex(payload["report_hash"], "^[0-9a-f]{64}$")

                composition = build_game_runtime_composition(
                    _fixture(
                        "abstract-puzzle",
                        "artifacts/abstract-puzzle.gamepack.json",
                    ),
                    _fixture("abstract-puzzle", "assets/inventory.json"),
                    verified.root,
                    registry=registry,
                    snapshot=snapshot,
                )
                raw_evidence = build_runtime_evidence(
                    composition,
                    platform_id="platform:linux_x86_64",
                    execution_status="headless_verified",
                    packaging_status="unverified",
                    checks=[
                        {
                            "check_id": "check:headless_determinism",
                            "kind": "headless",
                            "status": "passed",
                            "evidence_id": "raw_cli_headless",
                            "content_hash": "1" * 64,
                        },
                        {
                            "check_id": "check:save_replay",
                            "kind": "save_replay",
                            "status": "passed",
                            "evidence_id": "raw_cli_replay",
                            "content_hash": "2" * 64,
                        },
                    ],
                )
                evidence_path = root / "raw-evidence.json"
                evidence_path.write_bytes(serialize_runtime_evidence(raw_evidence))
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch("sys.argv", [*arguments, "--evidence", str(evidence_path)]),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(main(), 0)
                self.assertEqual(stderr.getvalue(), "")
                raw_payload = json.loads(stdout.getvalue())
                self.assertEqual(payload["report_hash"], raw_payload["report_hash"])
                self.assertEqual("blocked", raw_payload["status"])
                self.assertFalse(raw_payload["supported"])
                self.assertIn(
                    "runtime_evidence_authority_missing",
                    raw_payload["reason_codes"],
                )
