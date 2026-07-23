from __future__ import annotations

import copy
import gc
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import weakref
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch

from m6_pyray_3d_fixture import write_neutral_skinned_glb

from isoworld.render.pyray_3d import (
    PYRAY_3D_ADAPTER_ID,
    PYRAY_3D_ADAPTER_VERSION,
    PYRAY_3D_BINDING_DISTRIBUTION,
    PYRAY_3D_BINDING_VERSION,
    PYRAY_3D_HEADER_VERSION,
    PYRAY_3D_RLGL_VERSION,
    PYRAY_3D_V1_ADAPTER,
    PYRAY_3D_V1_KEY,
    PYRAY_3D_V1_REGISTRY,
    Pyray3DABIReport,
    Pyray3DActorInstance,
    Pyray3DAdapter,
    Pyray3DAssetPlan,
    Pyray3DBindingPlan,
    Pyray3DBounds,
    Pyray3DError,
    _PyrayNativeOwner,
    _verify_pyray_abi,
    _verify_resolved_payload,
    animation_frame_at_tick,
    build_actor_instances,
    grid_to_world,
    pick_grid_cell,
    transform_bounds,
    world_to_grid,
)
from isoworld.render.render_state import ActorView, RenderState, TileView
from isoworld.runtime_adapter import RuntimeAdapterKey, RuntimeAdapterRegistryError
from worldforge.game_boundary import audit_game_repository
from worldforge.game_scaffold import create_game_project
from worldforge.integrity import canonical_payload_hash
from worldforge.runtime_composition import (
    validate_runtime_adapter,
    verify_runtime_composition,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "examples/m6-contracts"
ADAPTER_FIXTURE = FIXTURES / "adapters/pyray_3d_v1.json"


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _render_state(*, tick: int = 5) -> RenderState:
    return RenderState(
        revision=3,
        world_title="Neutral",
        map_id="neutral_map",
        map_title="Neutral",
        tick=tick,
        time_text="00:00",
        tiles=(
            TileView("neutral_tile", -1, 0, 0, (80, 80, 80, 255)),
            TileView("neutral_tile", 0, 0, 0, (80, 80, 80, 255)),
            TileView("neutral_tile", 1, 0, 0, (80, 80, 80, 255)),
        ),
        actors=(
            ActorView(
                actor_id="other",
                display_name="Other",
                x=1,
                y=0,
                color=(128, 128, 128, 255),
                active=False,
                route=(),
            ),
            ActorView(
                actor_id="neutral",
                display_name="Neutral",
                x=-1,
                y=0,
                color=(128, 128, 128, 255),
                active=True,
                route=((0, 0),),
            ),
        ),
        interactions=(),
        constructions=(),
        events=(),
        hud_lines=(),
        overlay=None,
    )


def _asset_plan(path: Path) -> Pyray3DAssetPlan:
    payload = path.read_bytes()
    return Pyray3DAssetPlan(
        asset_id="neutral_actor",
        payload_path=PurePosixPath("payload/neutral.glb"),
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        triangles=1,
        animation_id="idle",
        animation_keyframes=61,
    )


def _binding_plan() -> Pyray3DBindingPlan:
    return Pyray3DBindingPlan(
        slot="actor:neutral",
        asset_id="neutral_actor",
        uniform_scale=2.0,
        layer=1,
    )


class _Resolver:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[PurePosixPath] = []

    def resolve_payload(self, relative_path: PurePosixPath) -> Path:
        self.requests.append(relative_path)
        return self.path


class _FakeOwner:
    def __init__(self) -> None:
        self.events: list[object] = []
        self._bounds = MappingProxyTypeForTest(
            {"neutral_actor": Pyray3DBounds((0.0, 0.0, 0.0), (1.0, 1.0, 0.0))}
        )

    @property
    def local_bounds(self) -> dict[str, Pyray3DBounds]:
        return dict(self._bounds)

    def draw(self, instances: tuple[Pyray3DActorInstance, ...]) -> None:
        self.events.append(("draw", instances))

    def close(self) -> None:
        self.events.append("close")


class MappingProxyTypeForTest(dict[str, Pyray3DBounds]):
    pass


class Pyray3DContractTests(unittest.TestCase):
    def test_verified_fixture_matches_code_owned_exact_registry_key(self) -> None:
        fixture = _read_json(ADAPTER_FIXTURE)

        self.assertEqual(fixture, validate_runtime_adapter(fixture))
        self.assertEqual(fixture["content_hash"], canonical_payload_hash(fixture))
        self.assertEqual(PYRAY_3D_ADAPTER_ID, fixture["id"])
        self.assertEqual(PYRAY_3D_ADAPTER_VERSION, fixture["version"])
        self.assertEqual("verified", fixture["state"])
        self.assertEqual(["linux_x86_64"], fixture["platforms"])
        self.assertEqual(["3d"], fixture["presentation_modes"])
        self.assertEqual(["animation_gltf"], fixture["capability_ids"])
        self.assertEqual(
            RuntimeAdapterKey(
                id=str(fixture["id"]),
                version=str(fixture["version"]),
                content_hash=str(fixture["content_hash"]),
            ),
            PYRAY_3D_V1_KEY,
        )
        self.assertIs(PYRAY_3D_V1_ADAPTER, PYRAY_3D_V1_REGISTRY.resolve(PYRAY_3D_V1_KEY))
        self.assertEqual(PYRAY_3D_V1_KEY, PYRAY_3D_V1_ADAPTER.declaration_key)
        for near in (
            RuntimeAdapterKey(
                "pyray_3d_v2",
                PYRAY_3D_ADAPTER_VERSION,
                str(fixture["content_hash"]),
            ),
            RuntimeAdapterKey(PYRAY_3D_ADAPTER_ID, "0.1.1", str(fixture["content_hash"])),
            RuntimeAdapterKey(PYRAY_3D_ADAPTER_ID, PYRAY_3D_ADAPTER_VERSION, "f" * 64),
        ):
            with self.subTest(near=near), self.assertRaises(RuntimeAdapterRegistryError):
                PYRAY_3D_V1_REGISTRY.resolve(near)

    def test_fixture_components_and_capabilities_make_no_unproved_claims(self) -> None:
        fixture = _read_json(ADAPTER_FIXTURE)

        self.assertEqual(
            {"animation_gltf"},
            set(fixture["capability_ids"]),
        )
        for unproved in (
            "collision_gltf",
            "content_assetpack_v1",
            "packaging_standalone",
            "presentation_world_3d",
            "presentation_world_mixed",
        ):
            self.assertNotIn(unproved, fixture["capability_ids"])
        components = fixture["components"]
        assert isinstance(components, dict)
        self.assertEqual(
            {"id": "not_provided", "version": "0.0.0"},
            components["physics"],
        )
        self.assertEqual(
            {"id": "not_provided", "version": "0.0.0"},
            components["packager"],
        )
        self.assertEqual(1000, fixture["budgets"]["target_frame_milliseconds"])

    def test_every_current_3d_profile_stays_incompatible_without_collision(self) -> None:
        fixture = _read_json(ADAPTER_FIXTURE)
        catalog = _read_json(FIXTURES / "capability-catalog.json")
        worldpack = _read_json(ROOT / "content/compiled/foundation.worldpack.json")
        profile_names = (
            "profile_2_5d_over_3d",
            "profile_2d_over_3d",
            "profile_3d",
        )
        for name in profile_names:
            profile = _read_json(FIXTURES / f"profiles/{name}.json")
            layers = list(profile["layers"])
            packs: dict[str, object] = {
                "assetpack": {
                    "content_hash": "1" * 64,
                    "format": "rpg-world-forge.assetpack",
                    "format_version": 1,
                    "path": "unavailable.assetpack.json",
                },
                "worldpack": {
                    "content_hash": worldpack["content_hash"],
                    "format": "isoworld.worldpack",
                    "format_version": 5,
                    "path": "content/compiled/foundation.worldpack.json",
                },
            }
            owners: list[dict[str, object]] = [
                {
                    "asset_id": "neutral_actor",
                    "pack": "assetpack",
                    "plane": "world_base",
                    "representation": "3d",
                    "slot": "actor:neutral",
                }
            ]
            if len(layers) == 2:
                packs["renderpack"] = {
                    "content_hash": "2" * 64,
                    "format": "isoworld.renderpack",
                    "format_version": 1,
                    "path": "unavailable.renderpack.json",
                }
                owners.append(
                    {
                        "asset_id": "neutral_overlay",
                        "pack": "renderpack",
                        "plane": "world_overlay",
                        "representation": layers[1],
                        "slot": "tile_type:neutral",
                    }
                )
            composition: dict[str, object] = {
                "adapter": {
                    "content_hash": fixture["content_hash"],
                    "id": fixture["id"],
                    "version": fixture["version"],
                },
                "capability_catalog_hash": catalog["content_hash"],
                "format": "rpg-world-forge.runtime_composition",
                "format_version": 1,
                "packs": packs,
                "profile": {
                    "content_hash": profile["content_hash"],
                    "id": profile["id"],
                },
                "release_id": "1.0.0",
                "required_capability_ids": list(profile["required_capability_ids"]),
                "slot_owners": sorted(
                    owners,
                    key=lambda item: (
                        item["slot"],
                        item["plane"],
                        item["pack"],
                        item["asset_id"],
                        item["representation"],
                    ),
                ),
                "world_content_hash": worldpack["content_hash"],
                "world_id": worldpack["world"]["id"],
            }
            composition["content_hash"] = canonical_payload_hash(composition)

            with self.subTest(profile=name):
                result = verify_runtime_composition(
                    catalog,
                    profile,
                    fixture,
                    composition,
                    root=ROOT,
                    platform="linux_x86_64",
                )

                self.assertFalse(result.compatible)
                self.assertIn(
                    ("capability_missing", "adapter/capability_ids/collision_gltf"),
                    {(issue.code, issue.path) for issue in result.issues},
                )


class Pyray3DPureSurfaceTests(unittest.TestCase):
    def test_asset_and_binding_plans_are_closed_portable_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "neutral.glb"
            write_neutral_skinned_glb(path)
            valid_asset = _asset_plan(path)
            valid_binding = _binding_plan()

        self.assertEqual("idle", valid_asset.animation_id)
        self.assertEqual("actor:neutral", valid_binding.slot)
        invalid_assets = (
            {"payload_path": Path("payload/neutral.glb")},
            {"payload_path": PurePosixPath("../neutral.glb")},
            {"sha256": "A" * 64},
            {"size_bytes": 0},
            {"triangles": True},
            {"animation_id": "x" * 32},
            {"animation_keyframes": 0},
        )
        values = {
            "asset_id": "neutral_actor",
            "payload_path": PurePosixPath("payload/neutral.glb"),
            "sha256": "a" * 64,
            "size_bytes": 1,
            "triangles": 1,
            "animation_id": "idle",
            "animation_keyframes": 2,
        }
        for update in invalid_assets:
            with self.subTest(update=update), self.assertRaises(Pyray3DError):
                Pyray3DAssetPlan(**{**values, **update})  # type: ignore[arg-type]
        for update in (
            {"slot": "tile:neutral"},
            {"uniform_scale": 0.0},
            {"uniform_scale": float("nan")},
            {"layer": 0},
            {"layer": -1},
            {"layer": True},
        ):
            with self.subTest(update=update), self.assertRaises(Pyray3DError):
                Pyray3DBindingPlan(
                    **{
                        "slot": "actor:neutral",
                        "asset_id": "neutral_actor",
                        "uniform_scale": 1.0,
                        "layer": 1,
                        **update,
                    }
                )

    def test_grid_math_uses_documented_half_open_floor_cells(self) -> None:
        self.assertEqual((2.0, 3.0, -4.0), grid_to_world(1, -2, cell_size=2.0, elevation=3))
        self.assertEqual((0, 0), world_to_grid(0.0, 0.0))
        self.assertEqual((0, 0), world_to_grid(0.999999, 0.999999))
        self.assertEqual((1, 1), world_to_grid(1.0, 1.0))
        self.assertEqual((-1, -1), world_to_grid(-0.000001, -0.000001))
        for function, arguments in (
            (grid_to_world, (True, 0)),
            (grid_to_world, (10**10000, 0)),
            (world_to_grid, (float("nan"), 0.0)),
        ):
            with self.subTest(function=function.__name__), self.assertRaises(Pyray3DError):
                function(*arguments)
        with self.assertRaises(Pyray3DError):
            world_to_grid(1.0, 0.0, cell_size=5e-324)

    def test_picking_admits_only_tiles_from_the_frozen_render_snapshot(self) -> None:
        state = _render_state()

        self.assertEqual(
            (-1, 0),
            pick_grid_cell(
                state,
                ray_origin=(-0.5, 2.0, 0.5),
                ray_direction=(0.0, -1.0, 0.0),
            ),
        )
        self.assertIsNone(
            pick_grid_cell(
                state,
                ray_origin=(4.5, 2.0, 4.5),
                ray_direction=(0.0, -1.0, 0.0),
            )
        )
        self.assertIsNone(
            pick_grid_cell(
                state,
                ray_origin=(0.5, 2.0, 0.5),
                ray_direction=(1.0, 0.0, 0.0),
            )
        )
        self.assertIsNone(
            pick_grid_cell(
                state,
                ray_origin=(0.5, -1.0, 0.5),
                ray_direction=(0.0, -1.0, 0.0),
            )
        )

    def test_bounds_and_animation_are_presentation_only_and_tick_driven(self) -> None:
        bounds = Pyray3DBounds((-1.0, 0.0, -2.0), (1.0, 2.0, 2.0))

        self.assertEqual(
            Pyray3DBounds((8.0, 2.0, -1.0), (12.0, 6.0, 7.0)),
            transform_bounds(
                bounds,
                translation=(10.0, 2.0, 3.0),
                uniform_scale=2.0,
            ),
        )
        self.assertEqual(0.0, animation_frame_at_tick(0, 2))
        self.assertEqual(1.0, animation_frame_at_tick(5, 2))
        with self.assertRaises(Pyray3DError):
            Pyray3DBounds((1.0, 0.0, 0.0), (0.0, 1.0, 1.0))
        with self.assertRaises(Pyray3DError):
            animation_frame_at_tick(-1, 2)

    def test_actor_instances_are_stable_and_do_not_mutate_render_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "neutral.glb"
            write_neutral_skinned_glb(path)
            asset = _asset_plan(path)
        state = _render_state(tick=5)
        before = copy.deepcopy(state)

        instances = build_actor_instances(state, (asset,), (_binding_plan(),))

        self.assertEqual(state, before)
        self.assertEqual(1, len(instances))
        self.assertEqual("neutral", instances[0].actor_id)
        self.assertEqual((-1.0, 0.0, 0.0), instances[0].translation)
        self.assertEqual(5.0, instances[0].animation_frame)
        self.assertEqual(instances, build_actor_instances(state, (asset,), (_binding_plan(),)))


class Pyray3DSessionTests(unittest.TestCase):
    def test_session_validates_exact_payload_draws_and_returns_selection_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "neutral.glb"
            write_neutral_skinned_glb(path)
            asset = _asset_plan(path)
            resolver = _Resolver(path)
            owner = _FakeOwner()
            adapter = Pyray3DAdapter(_native_factory=lambda _assets: owner)

            with (
                patch("isoworld.render.pyray_3d.platform.machine", return_value="x86_64"),
                patch("isoworld.render.pyray_3d.sys_platform_linux", return_value=True),
                adapter.open_session(resolver, (asset,), (_binding_plan(),)) as session,
            ):
                state = _render_state()
                before = copy.deepcopy(state)
                selected = session.draw(
                    state,
                    ray_origin=(-0.5, 2.0, 0.5),
                    ray_direction=(0.0, -1.0, 0.0),
                )

                self.assertEqual((-1, 0), selected)
                self.assertEqual((-1, 0), session.selected_cell)
                self.assertEqual(state, before)
                self.assertEqual([asset.payload_path], resolver.requests)
                self.assertEqual(
                    Pyray3DBounds((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)),
                    session.local_bounds["neutral_actor"],
                )

            self.assertEqual("close", owner.events[-1])
            draw = owner.events[0]
            self.assertEqual("draw", draw[0])
            self.assertEqual(1, len(draw[1]))

    def test_session_retains_resolver_until_successful_native_close(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "neutral.glb"
            write_neutral_skinned_glb(path)
            asset = _asset_plan(path)
            resolver = _Resolver(path)
            resolver_ref = weakref.ref(resolver)
            owner = _FakeOwner()
            adapter = Pyray3DAdapter(_native_factory=lambda _assets: owner)
            with (
                patch("isoworld.render.pyray_3d.platform.machine", return_value="x86_64"),
                patch("isoworld.render.pyray_3d.sys_platform_linux", return_value=True),
            ):
                session = adapter.open_session(resolver, (asset,), (_binding_plan(),))
            del resolver
            gc.collect()
            self.assertIsNotNone(resolver_ref())

            session.close()
            gc.collect()

            self.assertIsNone(resolver_ref())
            self.assertEqual(["close"], owner.events)
            session.close()
            self.assertEqual(["close"], owner.events)

    def test_payload_identity_hash_size_and_budget_fail_before_native_factory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "neutral.glb"
            write_neutral_skinned_glb(path)
            valid = _asset_plan(path)
            called = False

            def factory(_assets: object) -> _FakeOwner:
                nonlocal called
                called = True
                return _FakeOwner()

            adapter = Pyray3DAdapter(_native_factory=factory)
            changes = (
                {"sha256": "f" * 64},
                {"size_bytes": valid.size_bytes + 1},
                {"triangles": 2},
                {"size_bytes": 1_048_577},
            )
            for change in changes:
                plan = Pyray3DAssetPlan(
                    asset_id=valid.asset_id,
                    payload_path=valid.payload_path,
                    sha256=str(change.get("sha256", valid.sha256)),
                    size_bytes=int(change.get("size_bytes", valid.size_bytes)),
                    triangles=int(change.get("triangles", valid.triangles)),
                    animation_id=valid.animation_id,
                    animation_keyframes=valid.animation_keyframes,
                )
                with (
                    self.subTest(change=change),
                    patch("isoworld.render.pyray_3d.platform.machine", return_value="x86_64"),
                    patch("isoworld.render.pyray_3d.sys_platform_linux", return_value=True),
                    self.assertRaises(Pyray3DError),
                ):
                    adapter.open_session(_Resolver(path), (plan,), (_binding_plan(),))
            self.assertFalse(called)

            alias = path.with_name("alias.glb")
            try:
                alias.hardlink_to(path)
            except OSError:
                pass
            else:
                with self.assertRaisesRegex(Pyray3DError, "hard-linked"):
                    _verify_resolved_payload(valid, path)

    def test_native_session_fails_closed_outside_declared_linux_x64(self) -> None:
        adapter = Pyray3DAdapter(_native_factory=lambda _assets: _FakeOwner())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "neutral.glb"
            write_neutral_skinned_glb(path)
            asset = _asset_plan(path)
            with (
                patch("isoworld.render.pyray_3d.platform.machine", return_value="aarch64"),
                self.assertRaisesRegex(Pyray3DError, "Linux x86_64 only"),
            ):
                adapter.open_session(_Resolver(path), (asset,), (_binding_plan(),))


class _FakeCType:
    def __init__(
        self,
        label: str,
        *,
        fields: tuple[str, ...] = (),
    ) -> None:
        self.label = label
        self.fields = tuple((name, object()) for name in fields)

    def __str__(self) -> str:
        return self.label


class _FakeFFI:
    NULL = object()

    def new(self, declaration: str, value: int) -> list[int]:
        if declaration != "int *":
            raise AssertionError(declaration)
        return [value]

    def string(self, value: bytes) -> bytes:
        return value.split(b"\x00", 1)[0]

    def typeof(self, value: object) -> _FakeCType:
        if value == "ModelAnimation":
            return _FakeCType(
                "<ctype 'struct ModelAnimation'>",
                fields=("name", "boneCount", "keyframeCount", "keyframePoses"),
            )
        if value == "load_model_animations_raw":
            return _FakeCType("<ctype 'struct ModelAnimation *(*)(char *, int *)'>")
        if value == "unload_model_animations_raw":
            return _FakeCType("<ctype 'void(*)(struct ModelAnimation *, int)'>")
        raise TypeError(value)


class _FakePyray:
    RAYLIB_VERSION_MAJOR = 6
    RAYLIB_VERSION_MINOR = 1
    RAYLIB_VERSION_PATCH = 0
    RAYLIB_VERSION = "6.1-dev"
    RLGL_VERSION = "6.0"
    FLAG_WINDOW_HIDDEN = 128
    CAMERA_PERSPECTIVE = 0
    BLACK = object()
    WHITE = object()
    ffi = _FakeFFI()

    def __init__(self) -> None:
        self.events: list[object] = []
        self.model = SimpleNamespace(boneCount=1)
        self.animation = SimpleNamespace(name=b"idle", keyframeCount=61, boneCount=1)
        self.animations = [self.animation]
        self.loaded_animation_count = 1
        self.cleanup_failure: str | None = None
        self.rl = SimpleNamespace(
            LoadModelAnimations="load_model_animations_raw",
            UnloadModelAnimations="unload_model_animations_raw",
        )

    def __getattr__(self, name: str) -> object:
        if name in {"Camera3D", "Vector3"}:
            return lambda *values: tuple(values)
        raise AttributeError(name)

    def set_config_flags(self, flags: int) -> None:
        self.events.append(("set_config_flags", flags))

    def init_window(self, width: int, height: int, title: str) -> None:
        self.events.append(("init_window", width, height, title))

    def is_window_ready(self) -> bool:
        self.events.append("is_window_ready")
        return True

    def load_model(self, path: str) -> object:
        self.events.append(("load_model", path))
        return self.model

    def is_model_valid(self, model: object) -> bool:
        self.events.append(("is_model_valid", model))
        return True

    def load_model_animations(self, path: str, count: list[int]) -> list[object]:
        self.events.append(("load_model_animations", path, count))
        count[0] = self.loaded_animation_count
        return self.animations

    def is_model_animation_valid(self, model: object, animation: object) -> bool:
        self.events.append(("is_model_animation_valid", model, animation))
        return True

    def get_model_bounding_box(self, model: object) -> object:
        self.events.append(("get_model_bounding_box", model))
        return SimpleNamespace(
            min=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            max=SimpleNamespace(x=1.0, y=1.0, z=0.0),
        )

    def begin_drawing(self) -> None:
        self.events.append("begin_drawing")

    def clear_background(self, color: object) -> None:
        self.events.append(("clear_background", color))

    def begin_mode_3d(self, camera: object) -> None:
        self.events.append(("begin_mode_3d", camera))

    def update_model_animation(self, model: object, animation: object, frame: int) -> None:
        self.events.append(("update_model_animation", model, animation, frame))

    def draw_model(
        self,
        model: object,
        translation: object,
        scale: float,
        tint: object,
    ) -> None:
        self.events.append(("draw_model", model, translation, scale, tint))

    def end_mode_3d(self) -> None:
        self.events.append("end_mode_3d")

    def end_drawing(self) -> None:
        self.events.append("end_drawing")

    def unload_model_animations(self, animations: object, count: int) -> None:
        self.events.append(("unload_model_animations", animations, count))
        if self.cleanup_failure == "animations":
            raise RuntimeError("animation cleanup failed")

    def unload_model(self, model: object) -> None:
        self.events.append(("unload_model", model))
        if self.cleanup_failure == "model":
            raise RuntimeError("model cleanup failed")

    def close_window(self) -> None:
        self.events.append("close_window")
        if self.cleanup_failure == "window":
            raise RuntimeError("window cleanup failed")


class Pyray3DNativeBoundaryTests(unittest.TestCase):
    def _resolved(self, root: Path) -> object:
        path = root / "neutral.glb"
        write_neutral_skinned_glb(path)
        return _verify_resolved_payload(_asset_plan(path), path)

    def test_abi_requires_exact_distribution_header_rlgl_and_function_surface(self) -> None:
        fake = _FakePyray()

        report = _verify_pyray_abi(fake, installed_version="6.0.1.0")

        self.assertEqual(PYRAY_3D_BINDING_DISTRIBUTION, report.binding_distribution)
        self.assertEqual(PYRAY_3D_BINDING_VERSION, report.binding_version)
        self.assertEqual(PYRAY_3D_HEADER_VERSION, report.header_version)
        self.assertEqual(PYRAY_3D_RLGL_VERSION, report.rlgl_version)
        for update, message in (
            ({"installed_version": "6.0.2.0"}, "exact distribution"),
            ({"header": "6.0"}, "header label"),
            ({"rlgl": "5.0"}, "RLGL"),
            ({"load_ctype": "unload_model_animations_raw"}, "signed int pointer"),
        ):
            changed = _FakePyray()
            changed.RAYLIB_VERSION = str(update.get("header", changed.RAYLIB_VERSION))
            changed.RLGL_VERSION = str(update.get("rlgl", changed.RLGL_VERSION))
            changed.rl.LoadModelAnimations = str(
                update.get("load_ctype", changed.rl.LoadModelAnimations)
            )
            with self.subTest(update=update), self.assertRaisesRegex(Pyray3DError, message):
                _verify_pyray_abi(
                    changed,
                    installed_version=str(update.get("installed_version", "6.0.1.0")),
                )

    def test_fake_native_load_draw_and_cleanup_use_exact_order_and_full_array(self) -> None:
        fake = _FakePyray()
        report = Pyray3DABIReport(
            "raylib",
            "6.0.1.0",
            "6.1-dev",
            (6, 1, 0),
            "6.0",
            (),
        )
        with tempfile.TemporaryDirectory() as temporary:
            resolved = self._resolved(Path(temporary).resolve())
            with patch(
                "isoworld.render.pyray_3d._pyray_native_factory",
                return_value=(fake, report),
            ):
                owner = _PyrayNativeOwner.open((resolved,))
            owner.draw(
                (
                    Pyray3DActorInstance(
                        actor_id="neutral",
                        asset_id="neutral_actor",
                        animation_id="idle",
                        animation_frame=1.0,
                        translation=(0.0, 0.0, 0.0),
                        uniform_scale=1.0,
                        layer=0,
                    ),
                )
            )
            owner.close()
            events = list(fake.events)
            owner.close()

        self.assertEqual(events, fake.events)
        load_model = next(
            item for item in events if isinstance(item, tuple) and item[0] == "load_model"
        )
        load_animation = next(
            item
            for item in events
            if isinstance(item, tuple) and item[0] == "load_model_animations"
        )
        self.assertIsInstance(load_model[1], str)
        self.assertEqual(load_model[1], load_animation[1])
        update = next(
            item
            for item in events
            if isinstance(item, tuple) and item[0] == "update_model_animation"
        )
        self.assertEqual(1, update[3])
        cleanup_names = [
            item[0] if isinstance(item, tuple) else item
            for item in events
            if (isinstance(item, tuple) and item[0].startswith("unload_")) or item == "close_window"
        ]
        self.assertEqual(
            ["unload_model_animations", "unload_model", "close_window"],
            cleanup_names,
        )
        unload_animations = next(
            item
            for item in events
            if isinstance(item, tuple) and item[0] == "unload_model_animations"
        )
        self.assertIs(fake.animations, unload_animations[1])
        self.assertEqual(1, unload_animations[2])

    def test_cleanup_uncertainty_never_double_unloads_and_window_is_last(self) -> None:
        fake = _FakePyray()
        fake.cleanup_failure = "animations"
        report = Pyray3DABIReport(
            "raylib",
            "6.0.1.0",
            "6.1-dev",
            (6, 1, 0),
            "6.0",
            (),
        )
        with tempfile.TemporaryDirectory() as temporary:
            resolved = self._resolved(Path(temporary).resolve())
            with patch(
                "isoworld.render.pyray_3d._pyray_native_factory",
                return_value=(fake, report),
            ):
                owner = _PyrayNativeOwner.open((resolved,))
            with self.assertRaisesRegex(Pyray3DError, "cleanup became uncertain"):
                owner.close()
            events = list(fake.events)
            with self.assertRaisesRegex(Pyray3DError, "cleanup became uncertain"):
                owner.close()

        self.assertEqual(events, fake.events)
        self.assertEqual("close_window", fake.events[-1])
        self.assertEqual(
            1,
            sum(
                isinstance(item, tuple) and item[0] == "unload_model_animations"
                for item in fake.events
            ),
        )
        self.assertEqual(
            1,
            sum(isinstance(item, tuple) and item[0] == "unload_model" for item in fake.events),
        )

    def test_partial_animation_load_failure_releases_array_before_model_and_window(self) -> None:
        fake = _FakePyray()
        fake.loaded_animation_count = 0
        report = Pyray3DABIReport(
            "raylib",
            "6.0.1.0",
            "6.1-dev",
            (6, 1, 0),
            "6.0",
            (),
        )
        with tempfile.TemporaryDirectory() as temporary:
            resolved = self._resolved(Path(temporary).resolve())
            with (
                patch(
                    "isoworld.render.pyray_3d._pyray_native_factory",
                    return_value=(fake, report),
                ),
                self.assertRaisesRegex(Pyray3DError, "animation array"),
            ):
                _PyrayNativeOwner.open((resolved,))

        cleanup = [
            item[0] if isinstance(item, tuple) else item
            for item in fake.events
            if (isinstance(item, tuple) and item[0].startswith("unload_")) or item == "close_window"
        ]
        self.assertEqual(
            ["unload_model_animations", "unload_model", "close_window"],
            cleanup,
        )
        unloaded = next(
            item
            for item in fake.events
            if isinstance(item, tuple) and item[0] == "unload_model_animations"
        )
        self.assertEqual(0, unloaded[2])

    def test_source_excludes_forbidden_native_shortcuts_and_runtime_dependencies(self) -> None:
        source = (ROOT / "src/isoworld/render/pyray_3d.py").read_text(encoding="utf-8")

        self.assertNotIn("update_model_animation_bones", source)
        self.assertNotIn("load_model_from_memory", source)
        self.assertNotIn("import_module", source)
        self.assertNotIn("worldforge", source)
        self.assertEqual(1, source.count("import pyray"))
        self.assertNotIn('ffi.new("unsigned int *"', source)
        self.assertNotIn("animation.frameCount", source)
        self.assertNotIn(".unload_model_animation(", source)

    def test_ci_keeps_linux_native_and_windows_abi_evidence_distinct(self) -> None:
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

        self.assertIn(
            "xvfb-run -a python tests/pyray_3d_native_smoke.py",
            workflow,
        )
        self.assertIn("python tests/pyray_3d_abi_smoke.py", workflow)
        abi_source = (ROOT / "tests/pyray_3d_abi_smoke.py").read_text(encoding="utf-8")
        self.assertIn('"native_3d_verified": False', abi_source)
        self.assertIn('"evidence": "abi_and_function_surface_only"', abi_source)

    def test_generated_game_allows_only_the_two_locked_pyray_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "game"
            create_game_project(game, game_id="pyray_boundary", title="Pyray Boundary")
            command = [
                sys.executable,
                "-S",
                str(game / "scripts/verify_game.py"),
            ]
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)

            accepted = subprocess.run(
                command,
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
            self.assertEqual([], audit_game_repository(game))

            rogue = game / "src/isoworld/render/rogue.py"
            rogue.write_text("import pyray\n", encoding="utf-8")
            expected_detail = (
                "pyray import outside a game presentation adapter: isoworld/render/rogue.py"
            )
            forge_rejected = [
                finding
                for finding in audit_game_repository(game)
                if finding.detail == expected_detail
            ]
            rejected = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-c",
                    (
                        "import runpy,sys; "
                        "ns=runpy.run_path(sys.argv[1]); "
                        "ns['verify_source_boundary']()"
                    ),
                    str(game / "scripts/verify_game.py"),
                ],
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(1, len(forge_rejected), forge_rejected)
        self.assertEqual(Path("src/isoworld/render/rogue.py"), forge_rejected[0].path)
        self.assertEqual(1, forge_rejected[0].line)
        self.assertEqual("forbidden_game_import", forge_rejected[0].rule)
        self.assertNotEqual(0, rejected.returncode)
        self.assertIn(expected_detail, rejected.stderr)


if __name__ == "__main__":
    unittest.main()
