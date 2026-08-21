from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from gamepack_runtime import (
    build_game_replay,
    build_game_save,
    play_game_replay,
    restore_game_save,
)
from tests.test_multigenre_game_runtime_bundle import (
    _replace_manifest_file,
    _reseal_composition,
    _reseal_support,
)
from worldforge.__main__ import _resolve_generic_assetpack_cli_source
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.game_runtime_bundle import build_game_runtime_bundle_from_objects
from worldforge.generic_assetpack import seal_generic_assetpack
from worldforge.generic_runtime import (
    build_builtin_runtime_adapters,
    build_game_runtime_composition,
    build_game_runtime_snapshot,
    build_runtime_adapter_registry,
    build_runtime_support_report,
)
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
PUZZLE_HASH = "0510d69d0f78d3e80810aa26dd4b76752416809f7733e731274ac8d7f35dac09"
NARRATIVE_HASH = "56b8a5393615603ca3a6bbc1a55cf557cadee2e05cf03a8b4714b4536e6cb7b7"
BOARD_HASH = "69801bb77d5a0ddd63b59700fb567ad003bceb23e303488167d2da14ecd56d8b"
FONT_HASH = "c7362ddaf3102b66b24e4ea5a566b9cd339b082b14165fc73ba04f76f8a2273e"


def _read_fixture(name: str, relative: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "examples" / "multigenre-contracts" / name / relative).read_text(encoding="utf-8")
    )


def _build_live_bundle(name: str, root: Path):
    adapters = build_builtin_runtime_adapters()
    snapshot = build_game_runtime_snapshot(
        ROOT / "src/gamepack_runtime",
        adapter_runtime_root=ROOT / "src/gamepack_raylib_2d",
        adapters=adapters,
    )
    registry = build_runtime_adapter_registry(adapters=adapters, snapshot=snapshot)
    gamepack = _read_fixture(name, f"artifacts/{name}.gamepack.json")
    inventory = _read_fixture(name, "assets/inventory.json")
    source = _resolve_generic_assetpack_cli_source(
        ROOT / "examples" / "multigenre-contracts" / name / "assets/manifest.json"
    )
    assetpack = seal_generic_assetpack(root / f"{name}-assetpack", **source)
    try:
        composition = build_game_runtime_composition(
            gamepack,
            inventory,
            assetpack.root,
            registry=registry,
            snapshot=snapshot,
        )
        support = build_runtime_support_report(
            composition,
            gamepack=gamepack,
            registry=registry,
            snapshot=snapshot,
            evidence=[],
        )
        return build_game_runtime_bundle_from_objects(
            root / f"{name}-runtime-bundle",
            gamepack=gamepack,
            inventory=inventory,
            assetpack=assetpack.manifest,
            assetpack_root=assetpack.root,
            snapshot=snapshot,
            registry=registry,
            composition=composition,
            support_report=support,
        )
    finally:
        assetpack.close()


class RaylibAdapterTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="wf-raylib-2d-tests-")
        cls.root = Path(cls._temporary.name)
        puzzle_root = cls.root / "puzzle"
        narrative_root = cls.root / "narrative"
        puzzle_root.mkdir()
        narrative_root.mkdir()
        cls.puzzle_bundle = _build_live_bundle("abstract-puzzle", puzzle_root)
        cls.narrative_bundle = _build_live_bundle("branching-narrative", narrative_root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.narrative_bundle.close()
        cls.puzzle_bundle.close()
        cls._temporary.cleanup()


class FixedStepAndInputTests(RaylibAdapterTestCase):
    def test_fixed_step_clamps_catchup_and_commits_at_most_one_action(self) -> None:
        from gamepack_raylib_2d.fixed_step import FixedStepClock
        from gamepack_raylib_2d.puzzle import PuzzleController
        from gamepack_raylib_2d.resources import load_runtime_bundle
        from gamepack_raylib_2d.types import SemanticIntent

        clock = FixedStepClock()
        self.assertEqual(clock.consume(1.0 / 120.0), 0)
        self.assertEqual(clock.consume(1.0 / 120.0), 1)
        self.assertEqual(clock.consume(10.0), 5)

        loaded = load_runtime_bundle(self.puzzle_bundle.root)
        controller = PuzzleController(loaded.gamepack, max_actions=128)
        controller.queue_intent(SemanticIntent("select_cell", 0))
        controller.queue_intent(SemanticIntent("select_cell", 1))
        controller.step()
        self.assertEqual(controller.session.state_hash, loaded.initial_state_hash)
        self.assertEqual(controller.selected_cell, 0)
        controller.step()
        self.assertEqual(controller.session.classification.ending_ids, ("puzzle_complete",))
        self.assertEqual(len(controller.accepted_results), 1)

    def test_keyboard_and_pointer_map_to_the_same_puzzle_and_narrative_actions(self) -> None:
        from gamepack_raylib_2d.input import InputFrame, InputRouter

        puzzle = InputRouter("gamepack_raylib_2d_puzzle")
        keyboard = [
            *puzzle.map_frame(InputFrame(keys_pressed=("SPACE",))),
            *puzzle.map_frame(InputFrame(keys_pressed=("RIGHT",))),
            *puzzle.map_frame(InputFrame(keys_pressed=("ENTER",))),
        ]
        pointer = [
            *puzzle.map_frame(InputFrame(pointer_pressed=True, pointer_x=192.0, pointer_y=280.0)),
            *puzzle.map_frame(InputFrame(pointer_pressed=True, pointer_x=320.0, pointer_y=280.0)),
        ]
        self.assertEqual(
            [(intent.kind, intent.value) for intent in keyboard if intent.authoritative],
            [("select_cell", 0), ("select_cell", 1)],
        )
        self.assertEqual(
            [(intent.kind, intent.value) for intent in pointer if intent.authoritative],
            [("select_cell", 0), ("select_cell", 1)],
        )

        narrative = InputRouter("gamepack_raylib_2d_text")
        keyboard_choice = narrative.map_frame(InputFrame(keys_pressed=("2",)))
        pointer_choice = narrative.map_frame(
            InputFrame(pointer_pressed=True, pointer_x=400.0, pointer_y=340.0)
        )
        self.assertEqual(
            [(intent.kind, intent.value) for intent in keyboard_choice],
            [("choose", 1)],
        )
        self.assertEqual(
            [(intent.kind, intent.value) for intent in pointer_choice],
            [("choose", 1)],
        )


class RuntimeBundleResourceTests(RaylibAdapterTestCase):
    def test_regular_binary_assets_request_binary_descriptor_mode(self) -> None:
        from gamepack_raylib_2d import resources

        payload = b"\x89PNG\r\n\x1a\n" + b"retained payload after ctrl-z"
        asset = self.root / "binary-mode.png"
        asset.write_bytes(payload)
        binary_flag = 1 << 30
        requested_flags: list[int] = []
        real_open = os.open

        def tracked_open(path: os.PathLike[str] | str, flags: int, mode: int = 0o777) -> int:
            requested_flags.append(flags)
            return real_open(path, flags & ~binary_flag, mode)

        with (
            mock.patch.object(resources.os, "O_BINARY", binary_flag, create=True),
            mock.patch.object(resources.os, "open", side_effect=tracked_open),
        ):
            self.assertEqual(
                resources._read_regular(asset, "assetpack/assets/ui/board.png"),
                payload,
            )

        self.assertEqual(len(requested_flags), 1)
        flags = requested_flags[0]
        self.assertEqual(flags & binary_flag, binary_flag)
        self.assertFalse(flags & os.O_WRONLY)
        self.assertFalse(flags & os.O_RDWR)
        for platform_flag in (getattr(os, "O_CLOEXEC", 0), getattr(os, "O_NOFOLLOW", 0)):
            if platform_flag:
                self.assertEqual(flags & platform_flag, platform_flag)

    def test_exact_bundle_assets_load_with_dimensions_hashes_and_font_deduplication(
        self,
    ) -> None:
        from gamepack_raylib_2d.backend import RecordingBackend
        from gamepack_raylib_2d.resources import ResourceManager, load_runtime_bundle

        puzzle = load_runtime_bundle(self.puzzle_bundle.root)
        self.assertEqual(puzzle.gamepack["content_hash"], PUZZLE_HASH)
        self.assertEqual(puzzle.bindings["board_texture"].sha256, BOARD_HASH)
        self.assertEqual(puzzle.bindings["board_texture"].dimensions, (256, 256))
        puzzle_backend = RecordingBackend()
        puzzle_resources = ResourceManager(puzzle, puzzle_backend)
        puzzle_resources.load()
        self.assertEqual(
            [event[:2] for event in puzzle_backend.events if event[0] == "load_texture"],
            [("load_texture", BOARD_HASH)],
        )
        puzzle_resources.close()

        narrative = load_runtime_bundle(self.narrative_bundle.root)
        self.assertEqual(narrative.gamepack["content_hash"], NARRATIVE_HASH)
        self.assertEqual(narrative.bindings["choice_panel"].sha256, FONT_HASH)
        self.assertEqual(narrative.bindings["ending_panel"].sha256, FONT_HASH)
        narrative_backend = RecordingBackend()
        narrative_resources = ResourceManager(narrative, narrative_backend)
        narrative_resources.load()
        self.assertEqual(
            [event[:2] for event in narrative_backend.events if event[0] == "load_font"],
            [("load_font", FONT_HASH)],
        )
        self.assertIs(
            narrative_resources.handle("choice_panel"),
            narrative_resources.handle("ending_panel"),
        )
        narrative_resources.close()

    def test_tamper_missing_links_hardlinks_and_wrong_media_fail_closed(self) -> None:
        from gamepack_raylib_2d.resources import RaylibResourceError, load_runtime_bundle

        cases = ("tamper", "missing", "symlink", "hardlink")
        for case in cases:
            with self.subTest(case=case):
                root = self.root / f"unsafe-{case}"
                shutil.copytree(self.puzzle_bundle.root, root)
                board = root / "assetpack/assets/ui/board.png"
                if case == "tamper":
                    board.write_bytes(b"not a png")
                elif case == "missing":
                    board.unlink()
                elif case == "symlink":
                    replacement = root / "replacement.png"
                    replacement.write_bytes(board.read_bytes())
                    board.unlink()
                    board.symlink_to(replacement)
                else:
                    replacement = root / "replacement.png"
                    os.link(board, replacement)
                with self.assertRaises(RaylibResourceError):
                    load_runtime_bundle(root)

    def test_fully_resealed_crossed_composition_lineage_fails_closed(self) -> None:
        from gamepack_raylib_2d.resources import RaylibResourceError, load_runtime_bundle

        root = self.root / "crossed-lineage"
        shutil.copytree(self.puzzle_bundle.root, root)
        manifest_path = root / "game-runtime-bundle.json"
        composition_path = root / "contracts/runtime-composition.json"
        support_path = root / "status/runtime-support-report.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        composition = json.loads(composition_path.read_text(encoding="utf-8"))
        support = json.loads(support_path.read_text(encoding="utf-8"))
        composition["registry"]["content_hash"] = "0" * 64
        _reseal_composition(composition)
        support["composition"]["content_hash"] = composition["content_hash"]
        _reseal_support(support)
        composition_payload = canonical_json_bytes(composition)
        support_payload = canonical_json_bytes(support)
        composition_path.write_bytes(composition_payload)
        support_path.write_bytes(support_payload)
        manifest["contracts"]["runtime_composition"]["content_hash"] = composition["content_hash"]
        manifest["contracts"]["runtime_support_report"]["content_hash"] = support["content_hash"]
        _replace_manifest_file(
            manifest,
            "contracts/runtime-composition.json",
            composition_payload,
        )
        _replace_manifest_file(
            manifest,
            "status/runtime-support-report.json",
            support_payload,
        )
        manifest_path.write_bytes(canonical_json_bytes(manifest))
        with self.assertRaisesRegex(RaylibResourceError, "bundle_binding_mismatch"):
            load_runtime_bundle(root)

    def test_required_loaded_resources_are_drawn_and_cleanup_is_reverse_order(self) -> None:
        from gamepack_raylib_2d.app import RuntimeApp
        from gamepack_raylib_2d.backend import RecordingBackend
        from gamepack_raylib_2d.types import SemanticIntent

        puzzle_backend = RecordingBackend()
        puzzle = RuntimeApp.from_bundle(self.puzzle_bundle.root, backend=puzzle_backend)
        puzzle.run_scripted([SemanticIntent("select_cell", 0), SemanticIntent("select_cell", 1)])
        self.assertEqual(puzzle.structured_state()["ending_ids"], ["puzzle_complete"])
        self.assertEqual(puzzle.resource_report()["board_texture"]["draw_count"], 2)
        puzzle.close()
        self.assertEqual(puzzle_backend.events[-2][0], "unload_texture")
        self.assertEqual(puzzle_backend.events[-1][0], "close_window")

        narrative_backend = RecordingBackend()
        narrative = RuntimeApp.from_bundle(
            self.narrative_bundle.root,
            backend=narrative_backend,
            locale="en",
        )
        narrative.render()
        narrative.run_scripted([SemanticIntent("choose", 1)])
        state = narrative.structured_state()
        self.assertEqual(state["ending_ids"], ["ending_right"])
        self.assertEqual(state["knowledge"], ["learned_right"])
        report = narrative.resource_report()
        self.assertGreaterEqual(report["choice_panel"]["draw_count"], 1)
        self.assertGreaterEqual(report["ending_panel"]["draw_count"], 1)
        narrative.close()
        self.assertEqual(
            [event[0] for event in narrative_backend.events if event[0] == "unload_font"],
            ["unload_font"],
        )
        self.assertEqual(narrative_backend.events[-1][0], "close_window")


class ControllerAndPersistenceTests(RaylibAdapterTestCase):
    def test_puzzle_restart_then_solution_and_save_replay_are_kernel_compatible(self) -> None:
        from gamepack_raylib_2d.app import RuntimeApp
        from gamepack_raylib_2d.backend import RecordingBackend
        from gamepack_raylib_2d.types import SemanticIntent

        app = RuntimeApp.from_bundle(self.puzzle_bundle.root, backend=RecordingBackend())
        app.run_scripted([SemanticIntent("restart")])
        self.assertEqual(app.structured_state()["move_count"], 0)
        app.run_scripted([SemanticIntent("select_cell", 0), SemanticIntent("select_cell", 1)])
        save = build_game_save(app.persistence_context, app.controller.session.state)
        replay = build_game_replay(app.persistence_context, app.controller.accepted_results)
        self.assertEqual(
            restore_game_save(app.persistence_context, save),
            app.controller.session.state,
        )
        self.assertEqual(
            play_game_replay(app.persistence_context, replay).state_hash,
            app.controller.session.state_hash,
        )
        app.close()

    def test_cumulative_action_budget_blocks_before_unreplayable_history(self) -> None:
        from gamepack_raylib_2d.app import RuntimeApp
        from gamepack_raylib_2d.backend import RecordingBackend
        from gamepack_raylib_2d.types import SemanticIntent

        app = RuntimeApp.from_bundle(self.puzzle_bundle.root, backend=RecordingBackend())
        self.assertEqual(app.implementation.max_actions, 128)
        for _ in range(app.implementation.max_actions):
            app.controller.queue_intent(SemanticIntent("restart"))
            result = app.controller.step()
            self.assertIsNotNone(result)
            self.assertTrue(result.accepted)
        app.controller.queue_intent(SemanticIntent("restart"))
        with self.assertRaisesRegex(ValueError, "action budget"):
            app.controller.step()
        self.assertEqual(len(app.controller.accepted_results), 128)
        build_game_replay(app.persistence_context, app.controller.accepted_results)
        app.close()

    def test_both_authored_narrative_endings_and_structured_text_are_exact(self) -> None:
        from gamepack_raylib_2d.app import RuntimeApp
        from gamepack_raylib_2d.backend import RecordingBackend
        from gamepack_raylib_2d.types import SemanticIntent

        expected = (
            (0, "ending_left", "Left ending", ["learned_left"]),
            (1, "ending_right", "Right ending", ["learned_right"]),
        )
        for choice, ending, title, knowledge in expected:
            with self.subTest(ending=ending):
                app = RuntimeApp.from_bundle(
                    self.narrative_bundle.root,
                    backend=RecordingBackend(),
                    locale="en",
                )
                initial = app.structured_state()
                self.assertEqual(initial["title"], "A visible choice")
                self.assertEqual(
                    [item["label"] for item in initial["choices"]],
                    ["Choose the left symbol", "Choose the right symbol"],
                )
                app.run_scripted([SemanticIntent("choose", choice)])
                final = app.structured_state()
                self.assertEqual(final["ending_ids"], [ending])
                self.assertEqual(final["title"], title)
                self.assertEqual(final["knowledge"], knowledge)
                app.close()

    def test_narrative_dispatches_compiler_action_when_option_identity_differs(self) -> None:
        from gamepack_raylib_2d.narrative_text import NarrativeTextController
        from gamepack_raylib_2d.resources import load_runtime_bundle
        from gamepack_raylib_2d.types import SemanticIntent

        loaded = load_runtime_bundle(self.narrative_bundle.root)
        narrative = copy.deepcopy(loaded.gamepack)
        options = narrative["modules"]["narrative"][0]["units"][0]["options"]
        transitions = narrative["logic"]["narrative_transitions"]
        for index, option in enumerate(options):
            option_id = f"visible_option_{index + 1}"
            old_option_id = option["id"]
            option["id"] = option_id
            transitions[index]["option_id"] = option_id
            action = next(
                item
                for item in narrative["logic"]["actions"]
                if item["id"] == transitions[index]["action_id"]
            )
            action["source_bindings"][0]["option_id"] = option_id
            reference = next(
                item
                for item in narrative["localization"]["references"]
                if item["subject_id"] == f"central_choice_{old_option_id}"
            )
            reference["subject_id"] = f"central_choice_{option_id}"
            reference["key"] = f"narrative_option.central_choice_{option_id}.label"
        narrative["localization"]["references"].sort(key=lambda item: item["key"].encode("utf-8"))
        narrative["content_hash"] = canonical_creation_hash(narrative)

        controller = NarrativeTextController(narrative, locale="en", max_actions=128)
        self.assertEqual(
            [choice["action_id"] for choice in controller.structured_state()["choices"]],
            ["choose_left", "choose_right"],
        )
        controller.queue_intent(SemanticIntent("choose", 1))
        result = controller.step()
        self.assertIsNotNone(result)
        self.assertTrue(result.accepted)
        self.assertEqual(result.action.action_id, "choose_right")
        self.assertEqual(controller.session.classification.ending_ids, ("ending_right",))

    def test_non_english_locale_fails_closed_and_focus_is_not_persisted(self) -> None:
        from gamepack_raylib_2d.app import RuntimeApp
        from gamepack_raylib_2d.backend import RecordingBackend
        from gamepack_raylib_2d.resources import RaylibResourceError
        from gamepack_raylib_2d.types import SemanticIntent

        with self.assertRaisesRegex(RaylibResourceError, "locale_unsupported"):
            RuntimeApp.from_bundle(
                self.narrative_bundle.root,
                backend=RecordingBackend(),
                locale="es",
            )
        app = RuntimeApp.from_bundle(self.narrative_bundle.root, backend=RecordingBackend())
        before_hash = app.controller.session.state_hash
        app.run_scripted([SemanticIntent("focus_next", authoritative=False)])
        self.assertEqual(app.controller.session.state_hash, before_hash)
        self.assertEqual(app.structured_state()["focused_choice"], 1)
        save = build_game_save(app.persistence_context, app.controller.session.state)
        self.assertNotIn("focused_choice", json.dumps(save, sort_keys=True))
        app.close()


class RegistryBackendAndAuditTests(RaylibAdapterTestCase):
    def test_exact_registry_accepts_only_1_1_descriptor_and_bound_snapshot(self) -> None:
        from gamepack_raylib_2d.registry import AdapterResolutionError, resolve_adapter
        from gamepack_raylib_2d.resources import load_runtime_bundle

        loaded = load_runtime_bundle(self.puzzle_bundle.root)
        resolved = resolve_adapter(loaded)
        self.assertEqual(resolved.adapter_id, "gamepack_raylib_2d_puzzle")
        self.assertEqual(resolved.adapter_version, "1.1.0")
        old = copy.deepcopy(loaded.adapter)
        old["adapter_version"] = "1.0.0"
        with self.assertRaises(AdapterResolutionError):
            resolve_adapter(loaded.with_adapter(old))
        crossed = copy.deepcopy(loaded.snapshot)
        crossed["content_hash"] = "0" * 64
        with self.assertRaises(AdapterResolutionError):
            resolve_adapter(loaded.with_snapshot(crossed))

    def test_registry_and_direct_controller_reject_unsupported_executable_shape(self) -> None:
        from gamepack_raylib_2d.app import RuntimeApp
        from gamepack_raylib_2d.backend import RecordingBackend
        from gamepack_raylib_2d.executable_shape import AdapterExecutableShapeError
        from gamepack_raylib_2d.puzzle import PuzzleController
        from gamepack_raylib_2d.registry import AdapterResolutionError, resolve_adapter
        from gamepack_raylib_2d.resources import load_runtime_bundle

        loaded = load_runtime_bundle(self.puzzle_bundle.root)
        four_cells = copy.deepcopy(loaded.gamepack)
        for state_id in ("board", "target"):
            state = next(
                item for item in four_cells["logic"]["state_schema"] if item["id"] == state_id
            )
            state["allowed_values"].append("D")
            state["initial"].append("D")
            state["min_items"] = 4
            state["max_items"] = 4
            four_cells["logic"]["initial_state"][state_id].append("D")
        for parameter in four_cells["logic"]["actions"][1]["parameters"]:
            parameter["maximum"] = 3
        unsupported = replace(loaded, gamepack=four_cells)

        with self.assertRaisesRegex(
            AdapterResolutionError,
            "^adapter_executable_shape_unsupported:",
        ):
            resolve_adapter(unsupported)
        with self.assertRaises(AdapterExecutableShapeError) as raised:
            PuzzleController(four_cells, max_actions=128)
        self.assertEqual(
            raised.exception.reason_code,
            "adapter_executable_shape_unsupported",
        )

        unsupported_implementation = replace(
            resolve_adapter(loaded),
            controller_kind="headless",
        )
        with (
            mock.patch("gamepack_raylib_2d.app.NarrativeTextController") as fallback,
            self.assertRaisesRegex(
                AdapterResolutionError,
                "^adapter_executable_shape_unsupported:",
            ),
        ):
            RuntimeApp(
                loaded,
                unsupported_implementation,
                RecordingBackend(),
                locale="en",
                hidden=True,
            )
        fallback.assert_not_called()

    def test_valid_narrative_shape_violation_propagates_one_exact_failure(self) -> None:
        from gamepack_raylib_2d.executable_shape import AdapterExecutableShapeError
        from gamepack_raylib_2d.narrative_text import NarrativeTextController
        from gamepack_raylib_2d.registry import AdapterResolutionError, resolve_adapter
        from gamepack_raylib_2d.resources import load_runtime_bundle
        from worldforge.gamepack import validate_gamepack_document
        from worldforge.generic_runtime import (
            RuntimeContractError,
            resolve_runtime_adapter,
            resolve_runtime_build_readiness,
        )

        loaded = load_runtime_bundle(self.narrative_bundle.root)
        narrative = copy.deepcopy(loaded.gamepack)
        narrative["localization"]["supported_locales"].append("es")
        narrative["presentation"]["localization"]["supported_locales"].append("es")
        narrative["runtime_requirements"]["requested_adapter"] = None
        narrative["content_hash"] = canonical_creation_hash(narrative)
        self.assertEqual(validate_gamepack_document(narrative), narrative)

        reason_code = "adapter_executable_shape_unsupported"
        detail = "narrative executable shape supports exact English localization only"
        with self.assertRaises(RuntimeContractError) as forge_raised:
            resolve_runtime_adapter(
                narrative,
                registry=loaded.registry,
                snapshot=loaded.snapshot,
            )
        self.assertEqual(forge_raised.exception.reason_code, reason_code)
        self.assertEqual(forge_raised.exception.detail, detail)

        readiness = resolve_runtime_build_readiness(
            narrative,
            registry=loaded.registry,
            snapshot=loaded.snapshot,
        )
        self.assertEqual(readiness["status"], "unsupported")
        self.assertIsNone(readiness["adapter"])
        self.assertEqual(readiness["reason_codes"], [reason_code])
        self.assertEqual(readiness["reason_details"], {reason_code: detail})

        unsupported = replace(loaded, gamepack=narrative)
        with self.assertRaises(AdapterResolutionError) as registry_raised:
            resolve_adapter(unsupported)
        self.assertEqual(registry_raised.exception.reason_code, reason_code)
        self.assertEqual(registry_raised.exception.detail, detail)

        with self.assertRaises(AdapterExecutableShapeError) as controller_raised:
            NarrativeTextController(narrative, locale="en", max_actions=128)
        self.assertEqual(controller_raised.exception.reason_code, reason_code)
        self.assertEqual(controller_raised.exception.detail, detail)

    def test_pyray_is_lazy_backend_only_and_native_smoke_fails_closed(self) -> None:
        sys.modules.pop("pyray", None)
        module = importlib.import_module("gamepack_raylib_2d.backend")
        self.assertNotIn("pyray", sys.modules)
        fake_pyray = object()
        with mock.patch.object(importlib, "import_module", return_value=fake_pyray) as importer:
            backend = module.PyrayBackend()
        importer.assert_called_once_with("pyray")
        self.assertIs(backend.native_module, fake_pyray)

        from gamepack_raylib_2d.native_smoke import NativeSmokeError, native_smoke

        with (
            mock.patch("gamepack_raylib_2d.native_smoke._machine", return_value="aarch64"),
            self.assertRaisesRegex(NativeSmokeError, "native_platform_unsupported"),
        ):
            native_smoke(self.puzzle_bundle.root, max_frames=2, hidden=True)

    def test_native_smoke_reports_only_completed_frames(self) -> None:
        from gamepack_raylib_2d.app import RuntimeApp
        from gamepack_raylib_2d.backend import RecordingBackend
        from gamepack_raylib_2d.input import InputFrame
        from gamepack_raylib_2d.native_smoke import NativeSmokeError, native_smoke

        app = RuntimeApp.from_bundle(
            self.puzzle_bundle.root,
            backend=RecordingBackend([InputFrame()]),
        )
        self.assertEqual(app.run(max_frames=5), 1)
        app.close()

        fake_app = mock.Mock()
        fake_app.implementation.adapter_id = "gamepack_raylib_2d_puzzle"
        fake_app.implementation.adapter_version = "1.1.0"
        fake_app.run.return_value = 0
        with (
            mock.patch(
                "gamepack_raylib_2d.native_smoke._platform_id",
                return_value="platform:linux_x86_64",
            ),
            mock.patch(
                "gamepack_raylib_2d.native_smoke.audit_adapter_boundary",
                return_value={"violations": []},
            ),
            mock.patch("gamepack_raylib_2d.native_smoke.PyrayBackend"),
            mock.patch(
                "gamepack_raylib_2d.native_smoke.RuntimeApp.from_bundle",
                return_value=fake_app,
            ),
        ):
            with self.assertRaisesRegex(NativeSmokeError, "native_execution_incomplete"):
                native_smoke(self.puzzle_bundle.root, max_frames=5, hidden=True)
            fake_app.run.return_value = 2
            report = native_smoke(self.puzzle_bundle.root, max_frames=5, hidden=True)
        self.assertEqual(report["frames"], 2)
        self.assertEqual(report["status"], "native_smoke_executed")
        self.assertEqual(fake_app.close.call_count, 2)

    def test_adapter_boundary_forbids_services_and_allows_pyray_only_in_backend(self) -> None:
        from gamepack_raylib_2d.audit import audit_adapter_boundary

        report = audit_adapter_boundary(ROOT / "src/gamepack_raylib_2d")
        self.assertEqual(report["violations"], [])
        sources = sorted((ROOT / "src/gamepack_raylib_2d").glob("*.py"))
        self.assertGreaterEqual(len(sources), 8)
        pyray_sources = []
        for path in sources:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=os.fspath(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == "pyray":
                    pyray_sources.append(path.name)
        self.assertEqual(sorted(set(pyray_sources)), ["backend.py"])

    def test_gamepack_hashes_and_required_asset_hashes_remain_stable(self) -> None:
        from gamepack_raylib_2d.resources import load_runtime_bundle

        puzzle = load_runtime_bundle(self.puzzle_bundle.root)
        narrative = load_runtime_bundle(self.narrative_bundle.root)
        self.assertEqual(puzzle.gamepack["content_hash"], PUZZLE_HASH)
        self.assertEqual(narrative.gamepack["content_hash"], NARRATIVE_HASH)
        self.assertEqual(
            hashlib.sha256(puzzle.bindings["board_texture"].payload).hexdigest(),
            BOARD_HASH,
        )
        self.assertEqual(
            hashlib.sha256(narrative.bindings["choice_panel"].payload).hexdigest(),
            FONT_HASH,
        )


if __name__ == "__main__":
    unittest.main()
