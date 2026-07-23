from __future__ import annotations

import hashlib
import math
import os
import platform
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Protocol, final

from isoworld.content.portability import portable_relative_path
from isoworld.render.render_state import RenderState
from isoworld.runtime_adapter import RuntimeAdapterKey, StaticRuntimeAdapterRegistry

PYRAY_3D_BINDING_DISTRIBUTION = "raylib"
PYRAY_3D_BINDING_VERSION = "6.0.1.0"
PYRAY_3D_HEADER_VERSION = "6.1-dev"
PYRAY_3D_RLGL_VERSION = "6.0"
PYRAY_3D_ADAPTER_ID = "pyray_3d_v1"
PYRAY_3D_ADAPTER_VERSION = "0.1.0"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PORTABLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ANIMATION_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,30}$")
_ACTOR_SLOT_PATTERN = re.compile(r"^actor:([a-z][a-z0-9_]{1,63})$")
_READ_CHUNK_BYTES = 64 * 1024
_WINDOW_WIDTH = 96
_WINDOW_HEIGHT = 64

_PYRAY_3D_V1_BUDGETS = MappingProxyType(
    {
        "max_assets": 1,
        "max_bindings": 1,
        "max_draw_calls": 1,
        "max_loaded_bytes": 1_048_576,
        "max_triangles": 1,
    }
)

_REQUIRED_PYRAY_FUNCTIONS = (
    "begin_drawing",
    "begin_mode_3d",
    "clear_background",
    "close_window",
    "draw_model",
    "end_drawing",
    "end_mode_3d",
    "get_model_bounding_box",
    "init_window",
    "is_model_animation_valid",
    "is_model_valid",
    "is_window_ready",
    "load_model",
    "load_model_animations",
    "set_config_flags",
    "unload_model",
    "unload_model_animations",
    "update_model_animation",
)
_MODEL_ANIMATION_FIELDS = (
    "name",
    "boneCount",
    "keyframeCount",
    "keyframePoses",
)
_LOAD_ANIMATIONS_CTYPE = "<ctype 'struct ModelAnimation *(*)(char *, int *)'>"
_UNLOAD_ANIMATIONS_CTYPE = "<ctype 'void(*)(struct ModelAnimation *, int)'>"


class Pyray3DError(RuntimeError):
    """Raised when the bounded pyray 3D presentation proof fails closed."""


def _finite_number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise Pyray3DError(f"{context} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise Pyray3DError(f"{context} must be a finite number") from exc
    if not math.isfinite(result):
        raise Pyray3DError(f"{context} must be a finite number")
    return result


def _vector3(value: object, context: str) -> tuple[float, float, float]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise Pyray3DError(f"{context} must be a three-number tuple")
    return tuple(
        _finite_number(component, f"{context}/{index}") for index, component in enumerate(value)
    )  # type: ignore[return-value]


def _portable_id(value: object, context: str) -> str:
    if not isinstance(value, str) or _PORTABLE_ID_PATTERN.fullmatch(value) is None:
        raise Pyray3DError(f"{context} must be a portable lowercase ID")
    return value


@dataclass(frozen=True, slots=True)
class Pyray3DAssetPlan:
    """One exact payload-relative skinned GLB authorized for the native boundary."""

    asset_id: str
    payload_path: PurePosixPath
    sha256: str
    size_bytes: int
    triangles: int
    animation_id: str
    animation_keyframes: int

    def __post_init__(self) -> None:
        _portable_id(self.asset_id, "asset_id")
        if type(self.payload_path) is not PurePosixPath:
            raise Pyray3DError("payload_path must be an exact PurePosixPath")
        normalized = portable_relative_path(self.payload_path.as_posix())
        if normalized != self.payload_path:
            raise Pyray3DError("payload_path must be a portable relative path")
        if not isinstance(self.sha256, str) or _SHA256_PATTERN.fullmatch(self.sha256) is None:
            raise Pyray3DError("sha256 must be a lowercase SHA-256")
        for name in ("size_bytes", "triangles", "animation_keyframes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise Pyray3DError(f"{name} must be a positive integer")
        if (
            not isinstance(self.animation_id, str)
            or not self.animation_id.isascii()
            or len(self.animation_id.encode("ascii")) > 31
            or _ANIMATION_ID_PATTERN.fullmatch(self.animation_id) is None
        ):
            raise Pyray3DError("animation_id must be one portable ASCII ID of at most 31 bytes")


@dataclass(frozen=True, slots=True)
class Pyray3DBindingPlan:
    """One actor slot mapped to an asset with uniform scale and a stable layer."""

    slot: str
    asset_id: str
    uniform_scale: float
    layer: int

    def __post_init__(self) -> None:
        if not isinstance(self.slot, str) or _ACTOR_SLOT_PATTERN.fullmatch(self.slot) is None:
            raise Pyray3DError("slot must be one portable actor:<id> binding")
        _portable_id(self.asset_id, "asset_id")
        if _finite_number(self.uniform_scale, "uniform_scale") <= 0:
            raise Pyray3DError("uniform_scale must be positive")
        if (
            isinstance(self.layer, bool)
            or not isinstance(self.layer, int)
            or not 1 <= self.layer <= 2**31 - 1
        ):
            raise Pyray3DError("layer must be a positive bounded integer")


@dataclass(frozen=True, slots=True)
class Pyray3DBounds:
    """Finite ordered presentation bounds; never collision or navigation geometry."""

    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]

    def __post_init__(self) -> None:
        minimum = _vector3(self.minimum, "minimum")
        maximum = _vector3(self.maximum, "maximum")
        if any(left > right for left, right in zip(minimum, maximum, strict=True)):
            raise Pyray3DError("bounds minimum must not exceed maximum")


@dataclass(frozen=True, slots=True)
class Pyray3DActorInstance:
    """One deterministic actor draw instance derived from an immutable render state."""

    actor_id: str
    asset_id: str
    animation_id: str
    animation_frame: float
    translation: tuple[float, float, float]
    uniform_scale: float
    layer: int


class Pyray3DPayloadResolver(Protocol):
    """Narrow authority for resolving a payload inside an owned bundle snapshot."""

    def resolve_payload(self, relative_path: PurePosixPath) -> Path: ...


@dataclass(frozen=True, slots=True)
class Pyray3DABIReport:
    binding_distribution: str
    binding_version: str
    header_version: str
    header_components: tuple[int, int, int]
    rlgl_version: str
    required_functions: tuple[str, ...]


def _verify_pyray_abi(pr: Any, *, installed_version: str) -> Pyray3DABIReport:
    if installed_version != PYRAY_3D_BINDING_VERSION:
        raise Pyray3DError(
            "pyray 3D requires exact distribution "
            f"{PYRAY_3D_BINDING_DISTRIBUTION}=={PYRAY_3D_BINDING_VERSION}"
        )
    observed_components = (
        getattr(pr, "RAYLIB_VERSION_MAJOR", None),
        getattr(pr, "RAYLIB_VERSION_MINOR", None),
        getattr(pr, "RAYLIB_VERSION_PATCH", None),
    )
    if observed_components != (6, 1, 0):
        raise Pyray3DError("pyray bundled header components must be exactly 6.1.0")
    if getattr(pr, "RAYLIB_VERSION", None) != PYRAY_3D_HEADER_VERSION:
        raise Pyray3DError("pyray bundled header label must be exactly 6.1-dev")
    if getattr(pr, "RLGL_VERSION", None) != PYRAY_3D_RLGL_VERSION:
        raise Pyray3DError("pyray bundled RLGL version must be exactly 6.0")
    if not hasattr(pr, "ffi") or not callable(getattr(pr.ffi, "new", None)):
        raise Pyray3DError("pyray must expose its CFFI pointer allocator")
    try:
        model_animation = pr.ffi.typeof("ModelAnimation")
        model_animation_fields = tuple(name for name, _field in model_animation.fields)
        raw_library = pr.rl
        load_animations_ctype = str(pr.ffi.typeof(raw_library.LoadModelAnimations))
        unload_animations_ctype = str(pr.ffi.typeof(raw_library.UnloadModelAnimations))
        pr.ffi.new("int *", 0)
    except (AttributeError, TypeError, ValueError) as exc:
        raise Pyray3DError("pyray must expose the exact ModelAnimation CFFI ABI") from exc
    if model_animation_fields != _MODEL_ANIMATION_FIELDS:
        raise Pyray3DError("pyray ModelAnimation fields do not match the audited ABI")
    if load_animations_ctype != _LOAD_ANIMATIONS_CTYPE:
        raise Pyray3DError("pyray LoadModelAnimations must use an exact signed int pointer")
    if unload_animations_ctype != _UNLOAD_ANIMATIONS_CTYPE:
        raise Pyray3DError("pyray UnloadModelAnimations does not match the audited ABI")
    missing = tuple(
        name for name in _REQUIRED_PYRAY_FUNCTIONS if not callable(getattr(pr, name, None))
    )
    if missing:
        raise Pyray3DError(f"pyray is missing required functions: {', '.join(missing)}")
    for name in ("Camera3D", "Vector3"):
        if not callable(getattr(pr, name, None)):
            raise Pyray3DError(f"pyray is missing required structure constructor: {name}")
    for name in ("CAMERA_PERSPECTIVE", "FLAG_WINDOW_HIDDEN", "BLACK", "WHITE"):
        if getattr(pr, name, None) is None:
            raise Pyray3DError(f"pyray is missing required constant: {name}")
    return Pyray3DABIReport(
        binding_distribution=PYRAY_3D_BINDING_DISTRIBUTION,
        binding_version=installed_version,
        header_version=PYRAY_3D_HEADER_VERSION,
        header_components=(6, 1, 0),
        rlgl_version=PYRAY_3D_RLGL_VERSION,
        required_functions=_REQUIRED_PYRAY_FUNCTIONS,
    )


def _pyray_native_factory() -> tuple[Any, Pyray3DABIReport]:
    """Load and verify the single code-owned native binding."""

    try:
        import pyray
    except ImportError as exc:
        raise Pyray3DError(
            "pyray is unavailable; install the exact audited game dependency"
        ) from exc
    try:
        installed = distribution_version(PYRAY_3D_BINDING_DISTRIBUTION)
    except PackageNotFoundError as exc:
        raise Pyray3DError("the audited raylib distribution is unavailable") from exc
    return pyray, _verify_pyray_abi(pyray, installed_version=installed)


def inspect_pyray_3d_abi() -> Pyray3DABIReport:
    """Verify distribution, bundled header/RLGL labels, and the required ABI surface."""

    _pr, report = _pyray_native_factory()
    return report


def grid_to_world(
    grid_x: int,
    grid_y: int,
    *,
    cell_size: float = 1.0,
    elevation: float = 0.0,
) -> tuple[float, float, float]:
    """Map grid coordinates to the X/Z cell origin on a Y-up presentation plane."""

    if isinstance(grid_x, bool) or not isinstance(grid_x, int):
        raise Pyray3DError("grid_x must be an integer")
    if isinstance(grid_y, bool) or not isinstance(grid_y, int):
        raise Pyray3DError("grid_y must be an integer")
    size = _finite_number(cell_size, "cell_size")
    if size <= 0:
        raise Pyray3DError("cell_size must be positive")
    height = _finite_number(elevation, "elevation")
    x = _finite_number(_finite_number(grid_x, "grid_x") * size, "world_x")
    z = _finite_number(_finite_number(grid_y, "grid_y") * size, "world_z")
    return (x, height, z)


def world_to_grid(
    world_x: float,
    world_z: float,
    *,
    cell_size: float = 1.0,
) -> tuple[int, int]:
    """Map X/Z into half-open cells using floor, including for negative coordinates."""

    x = _finite_number(world_x, "world_x")
    z = _finite_number(world_z, "world_z")
    size = _finite_number(cell_size, "cell_size")
    if size <= 0:
        raise Pyray3DError("cell_size must be positive")
    grid_x = _finite_number(x / size, "world_x/cell_size")
    grid_y = _finite_number(z / size, "world_z/cell_size")
    return (math.floor(grid_x), math.floor(grid_y))


def pick_grid_cell(
    render_state: RenderState,
    ray_origin: tuple[float, float, float],
    ray_direction: tuple[float, float, float],
    *,
    plane_y: float = 0.0,
    cell_size: float = 1.0,
) -> tuple[int, int] | None:
    """Intersect a ray with the grid plane and admit only a tile in the snapshot."""

    if type(render_state) is not RenderState:
        raise Pyray3DError("render_state must be an exact immutable RenderState")
    origin = _vector3(ray_origin, "ray_origin")
    direction = _vector3(ray_direction, "ray_direction")
    grid_y = _finite_number(plane_y, "plane_y")
    if direction[1] == 0.0:
        return None
    distance = (grid_y - origin[1]) / direction[1]
    if not math.isfinite(distance) or distance < 0.0:
        return None
    point_x = origin[0] + direction[0] * distance
    point_z = origin[2] + direction[2] * distance
    selected = world_to_grid(point_x, point_z, cell_size=cell_size)
    admitted = {(tile.x, tile.y) for tile in render_state.tiles}
    return selected if selected in admitted else None


def transform_bounds(
    bounds: Pyray3DBounds,
    *,
    translation: tuple[float, float, float],
    uniform_scale: float,
) -> Pyray3DBounds:
    """Apply presentation-only uniform scale and translation to ordered bounds."""

    if type(bounds) is not Pyray3DBounds:
        raise Pyray3DError("bounds must be an exact Pyray3DBounds")
    offset = _vector3(translation, "translation")
    scale = _finite_number(uniform_scale, "uniform_scale")
    if scale <= 0:
        raise Pyray3DError("uniform_scale must be positive")
    return Pyray3DBounds(
        minimum=tuple(bounds.minimum[index] * scale + offset[index] for index in range(3)),  # type: ignore[arg-type]
        maximum=tuple(bounds.maximum[index] * scale + offset[index] for index in range(3)),  # type: ignore[arg-type]
    )


def animation_frame_at_tick(tick: int, keyframes: int) -> float:
    """Select one animation frame solely from a non-negative simulation tick."""

    if isinstance(tick, bool) or not isinstance(tick, int) or tick < 0:
        raise Pyray3DError("tick must be a non-negative integer")
    if isinstance(keyframes, bool) or not isinstance(keyframes, int) or keyframes <= 0:
        raise Pyray3DError("keyframes must be a positive integer")
    return float(tick % keyframes)


def _normalized_plans(
    asset_plans: Sequence[Pyray3DAssetPlan],
    binding_plans: Sequence[Pyray3DBindingPlan],
) -> tuple[tuple[Pyray3DAssetPlan, ...], tuple[Pyray3DBindingPlan, ...]]:
    if (
        not isinstance(asset_plans, tuple)
        or len(asset_plans) != _PYRAY_3D_V1_BUDGETS["max_assets"]
        or any(type(item) is not Pyray3DAssetPlan for item in asset_plans)
    ):
        raise Pyray3DError("pyray_3d_v1 requires exactly one immutable asset plan")
    if (
        not isinstance(binding_plans, tuple)
        or len(binding_plans) != _PYRAY_3D_V1_BUDGETS["max_bindings"]
        or any(type(item) is not Pyray3DBindingPlan for item in binding_plans)
    ):
        raise Pyray3DError("pyray_3d_v1 requires exactly one immutable actor binding")
    assets = {item.asset_id: item for item in asset_plans}
    if len(assets) != len(asset_plans):
        raise Pyray3DError("asset plan IDs must be unique")
    binding = binding_plans[0]
    if binding.asset_id not in assets:
        raise Pyray3DError("actor binding references an unknown asset plan")
    return asset_plans, binding_plans


def build_actor_instances(
    render_state: RenderState,
    asset_plans: Sequence[Pyray3DAssetPlan],
    binding_plans: Sequence[Pyray3DBindingPlan],
    *,
    cell_size: float = 1.0,
) -> tuple[Pyray3DActorInstance, ...]:
    """Build stable actor instances without reducing or mutating simulation state."""

    if type(render_state) is not RenderState:
        raise Pyray3DError("render_state must be an exact immutable RenderState")
    assets, bindings = _normalized_plans(asset_plans, binding_plans)
    by_id = {item.asset_id: item for item in assets}
    plans_by_actor = {
        _ACTOR_SLOT_PATTERN.fullmatch(binding.slot).group(1): binding for binding in bindings
    }
    instances: list[Pyray3DActorInstance] = []
    for actor in render_state.actors:
        binding = plans_by_actor.get(actor.actor_id)
        if binding is None:
            continue
        asset = by_id[binding.asset_id]
        instances.append(
            Pyray3DActorInstance(
                actor_id=actor.actor_id,
                asset_id=asset.asset_id,
                animation_id=asset.animation_id,
                animation_frame=animation_frame_at_tick(
                    render_state.tick,
                    asset.animation_keyframes,
                ),
                translation=grid_to_world(actor.x, actor.y, cell_size=cell_size),
                uniform_scale=binding.uniform_scale,
                layer=binding.layer,
            )
        )
    return tuple(
        sorted(
            instances,
            key=lambda item: (
                item.layer,
                item.translation[2],
                item.translation[0],
                item.actor_id,
                item.asset_id,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class _ResolvedAsset:
    plan: Pyray3DAssetPlan
    path: Path


def _plain_payload_state(path: Path) -> tuple[int, int, int, int, int, int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise Pyray3DError(f"could not inspect resolved payload: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise Pyray3DError("resolved payload must be a regular non-link file")
    if info.st_nlink != 1:
        raise Pyray3DError("resolved payload must not be hard-linked")
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _verify_resolved_payload(plan: Pyray3DAssetPlan, path: object) -> _ResolvedAsset:
    if not isinstance(path, Path) or not path.is_absolute():
        raise Pyray3DError("payload resolver must return an absolute pathlib Path")
    before = _plain_payload_state(path)
    if before[4] != plan.size_bytes:
        raise Pyray3DError("resolved payload size does not match its exact asset plan")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            opened_state = (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_nlink,
                opened.st_size,
                opened.st_mtime_ns,
                opened.st_ctime_ns,
            )
            if opened_state != before:
                raise Pyray3DError("resolved payload identity changed before validation")
            while True:
                chunk = stream.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
            after_descriptor = os.fstat(stream.fileno())
        after_opened_state = (
            after_descriptor.st_dev,
            after_descriptor.st_ino,
            after_descriptor.st_mode,
            after_descriptor.st_nlink,
            after_descriptor.st_size,
            after_descriptor.st_mtime_ns,
            after_descriptor.st_ctime_ns,
        )
    except OSError as exc:
        raise Pyray3DError(f"could not validate resolved payload: {exc}") from exc
    if opened_state != after_opened_state or before != _plain_payload_state(path):
        raise Pyray3DError("resolved payload identity changed during validation")
    if digest.hexdigest() != plan.sha256:
        raise Pyray3DError("resolved payload SHA-256 does not match its exact asset plan")
    return _ResolvedAsset(plan=plan, path=path)


class _NativeOwner(Protocol):
    @property
    def local_bounds(self) -> Mapping[str, Pyray3DBounds]: ...

    def draw(self, instances: tuple[Pyray3DActorInstance, ...]) -> None: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class _NativeResource:
    plan: Pyray3DAssetPlan
    model: Any
    animations: Any
    animation_count: int
    bounds: Pyray3DBounds


@final
class _PyrayNativeOwner:
    __slots__ = ("_cleanup_error", "_closed", "_pr", "_resources", "_window_owned")

    def __init__(self, pr: Any) -> None:
        self._pr = pr
        self._window_owned = False
        self._resources: list[_NativeResource] = []
        self._closed = False
        self._cleanup_error: Pyray3DError | None = None

    @classmethod
    def open(cls, assets: tuple[_ResolvedAsset, ...]) -> _PyrayNativeOwner:
        pr, _abi = _pyray_native_factory()
        owner = cls(pr)
        try:
            pr.set_config_flags(pr.FLAG_WINDOW_HIDDEN)
            pr.init_window(_WINDOW_WIDTH, _WINDOW_HEIGHT, "RPG World Forge pyray 3D proof")
            owner._window_owned = True
            if not pr.is_window_ready():
                raise Pyray3DError("raylib did not create a ready hidden window")
            for resolved in assets:
                model = pr.load_model(str(resolved.path))
                if not pr.is_model_valid(model):
                    raise Pyray3DError("raylib rejected the exact resolved GLB model")
                resource = _NativeResource(
                    plan=resolved.plan,
                    model=model,
                    animations=None,
                    animation_count=0,
                    bounds=Pyray3DBounds((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                )
                owner._resources.append(resource)
                count_pointer = pr.ffi.new("int *", 0)
                animations = pr.load_model_animations(str(resolved.path), count_pointer)
                animation_count = int(count_pointer[0])
                if animations != pr.ffi.NULL:
                    resource.animations = animations
                    resource.animation_count = max(0, animation_count)
                if animations == pr.ffi.NULL or animation_count <= 0:
                    raise Pyray3DError("raylib did not load the required GLB animation array")
                if animation_count != 1:
                    raise Pyray3DError("raylib must expose exactly one GLB animation")
                animation = animations[0]
                try:
                    animation_name = pr.ffi.string(animation.name).decode("ascii")
                except (UnicodeDecodeError, TypeError, ValueError) as exc:
                    raise Pyray3DError("raylib exposed an invalid animation name") from exc
                if animation_name != resolved.plan.animation_id:
                    raise Pyray3DError("raylib animation name does not match the exact asset plan")
                frame_count = int(animation.keyframeCount)
                if frame_count <= 0 or frame_count != resolved.plan.animation_keyframes:
                    raise Pyray3DError(
                        "raylib animation keyframes do not match the exact asset plan"
                    )
                if int(animation.boneCount) <= 0 or int(model.boneCount) <= 0:
                    raise Pyray3DError("raylib requires a positive model and animation skeleton")
                if not pr.is_model_animation_valid(model, animation):
                    raise Pyray3DError("raylib rejected the model/animation skeleton pairing")
                raw_bounds = pr.get_model_bounding_box(model)
                resource.bounds = Pyray3DBounds(
                    (
                        float(raw_bounds.min.x),
                        float(raw_bounds.min.y),
                        float(raw_bounds.min.z),
                    ),
                    (
                        float(raw_bounds.max.x),
                        float(raw_bounds.max.y),
                        float(raw_bounds.max.z),
                    ),
                )
            return owner
        except BaseException as original:
            try:
                owner.close()
            except Pyray3DError as cleanup_error:
                original.add_note(f"native cleanup also failed: {cleanup_error}")
            if isinstance(original, Pyray3DError):
                raise
            raise Pyray3DError(
                f"could not open the bounded pyray 3D session: {original}"
            ) from original

    @property
    def local_bounds(self) -> Mapping[str, Pyray3DBounds]:
        return MappingProxyType(
            {resource.plan.asset_id: resource.bounds for resource in self._resources}
        )

    def draw(self, instances: tuple[Pyray3DActorInstance, ...]) -> None:
        if self._closed or self._cleanup_error is not None:
            raise Pyray3DError("native pyray 3D owner is closed")
        resources = {resource.plan.asset_id: resource for resource in self._resources}
        drawing = False
        mode = False
        try:
            self._pr.begin_drawing()
            drawing = True
            self._pr.clear_background(self._pr.BLACK)
            camera = self._pr.Camera3D(
                self._pr.Vector3(2.0, 2.0, 2.0),
                self._pr.Vector3(0.0, 0.0, 0.0),
                self._pr.Vector3(0.0, 1.0, 0.0),
                45.0,
                self._pr.CAMERA_PERSPECTIVE,
            )
            self._pr.begin_mode_3d(camera)
            mode = True
            for instance in instances:
                resource = resources[instance.asset_id]
                animation = resource.animations[0]
                frame = int(instance.animation_frame)
                self._pr.update_model_animation(resource.model, animation, frame)
                self._pr.draw_model(
                    resource.model,
                    self._pr.Vector3(*instance.translation),
                    instance.uniform_scale,
                    self._pr.WHITE,
                )
        finally:
            if mode:
                self._pr.end_mode_3d()
            if drawing:
                self._pr.end_drawing()

    def close(self) -> None:
        if self._closed:
            return
        if self._cleanup_error is not None:
            raise self._cleanup_error
        errors: list[str] = []
        for resource in reversed(self._resources):
            if resource.animations is not None:
                animations = resource.animations
                animation_count = resource.animation_count
                resource.animations = None
                resource.animation_count = 0
                try:
                    self._pr.unload_model_animations(animations, animation_count)
                except BaseException as exc:
                    errors.append(f"unload animation array: {exc}")
            if resource.model is not None:
                model = resource.model
                resource.model = None
                try:
                    self._pr.unload_model(model)
                except BaseException as exc:
                    errors.append(f"unload model: {exc}")
        if self._window_owned:
            self._window_owned = False
            try:
                self._pr.close_window()
            except BaseException as exc:
                errors.append(f"close window: {exc}")
        if errors:
            self._cleanup_error = Pyray3DError(
                "native pyray 3D cleanup became uncertain: " + "; ".join(errors)
            )
            raise self._cleanup_error
        self._closed = True
        self._resources.clear()


_NativeFactory = Callable[[tuple[_ResolvedAsset, ...]], _NativeOwner]


@final
class Pyray3DSession:
    """Context-managed native owner that retains its payload resolver through unload."""

    __slots__ = (
        "_assets",
        "_bindings",
        "_closed",
        "_native",
        "_resolver",
        "_selected_cell",
    )

    def __init__(
        self,
        resolver: Pyray3DPayloadResolver,
        assets: tuple[Pyray3DAssetPlan, ...],
        bindings: tuple[Pyray3DBindingPlan, ...],
        native: _NativeOwner,
    ) -> None:
        self._resolver: Pyray3DPayloadResolver | None = resolver
        self._assets = assets
        self._bindings = bindings
        self._native = native
        self._selected_cell: tuple[int, int] | None = None
        self._closed = False

    @property
    def selected_cell(self) -> tuple[int, int] | None:
        return self._selected_cell

    @property
    def local_bounds(self) -> Mapping[str, Pyray3DBounds]:
        if self._closed:
            raise Pyray3DError("pyray 3D session is closed")
        return MappingProxyType(dict(self._native.local_bounds))

    def draw(
        self,
        render_state: RenderState,
        *,
        ray_origin: tuple[float, float, float] | None = None,
        ray_direction: tuple[float, float, float] | None = None,
    ) -> tuple[int, int] | None:
        if self._closed:
            raise Pyray3DError("pyray 3D session is closed")
        if (ray_origin is None) != (ray_direction is None):
            raise Pyray3DError("ray_origin and ray_direction must be supplied together")
        instances = build_actor_instances(render_state, self._assets, self._bindings)
        if len(instances) > _PYRAY_3D_V1_BUDGETS["max_draw_calls"]:
            raise Pyray3DError("render state exceeds the pyray 3D draw-call budget")
        selected = (
            None
            if ray_origin is None or ray_direction is None
            else pick_grid_cell(render_state, ray_origin, ray_direction)
        )
        self._native.draw(instances)
        self._selected_cell = selected
        return selected

    def close(self) -> None:
        if self._closed:
            return
        self._native.close()
        self._resolver = None
        self._closed = True

    def __enter__(self) -> Pyray3DSession:
        if self._closed:
            raise Pyray3DError("pyray 3D session is closed")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        try:
            self.close()
        except Pyray3DError as cleanup_error:
            if exception is None:
                raise
            exception.add_note(f"pyray 3D session cleanup failed: {cleanup_error}")


@final
@dataclass(frozen=True, slots=True)
class Pyray3DAdapter:
    """Code-owned bounded adapter for one exact animation-only pyray proof."""

    _native_factory: _NativeFactory = field(
        default=_PyrayNativeOwner.open,
        repr=False,
        compare=False,
    )

    @property
    def declaration_key(self) -> RuntimeAdapterKey:
        return PYRAY_3D_V1_KEY

    def open_session(
        self,
        resolver: Pyray3DPayloadResolver,
        asset_plans: Sequence[Pyray3DAssetPlan],
        binding_plans: Sequence[Pyray3DBindingPlan],
    ) -> Pyray3DSession:
        if not callable(getattr(resolver, "resolve_payload", None)):
            raise Pyray3DError("resolver must expose resolve_payload")
        machine = platform.machine().casefold()
        if os.name != "posix" or not sys_platform_linux() or machine not in {"amd64", "x86_64"}:
            raise Pyray3DError("pyray_3d_v1 native sessions support Linux x86_64 only")
        assets, bindings = _normalized_plans(asset_plans, binding_plans)
        if sum(item.size_bytes for item in assets) > _PYRAY_3D_V1_BUDGETS["max_loaded_bytes"]:
            raise Pyray3DError("asset plans exceed the pyray 3D loaded-byte budget")
        if sum(item.triangles for item in assets) > _PYRAY_3D_V1_BUDGETS["max_triangles"]:
            raise Pyray3DError("asset plans exceed the pyray 3D triangle budget")
        resolved = tuple(
            _verify_resolved_payload(item, resolver.resolve_payload(item.payload_path))
            for item in assets
        )
        native = self._native_factory(resolved)
        return Pyray3DSession(resolver, assets, bindings, native)


def sys_platform_linux() -> bool:
    """Narrow test seam for the one verified native host."""

    return platform.system() == "Linux"


PYRAY_3D_V1_KEY = RuntimeAdapterKey(
    id=PYRAY_3D_ADAPTER_ID,
    version=PYRAY_3D_ADAPTER_VERSION,
    content_hash="08d0452babc5f7f14975d346065c54e9a8335a71d9914261f6ef377c987872c8",
)
PYRAY_3D_V1_ADAPTER = Pyray3DAdapter()
PYRAY_3D_V1_REGISTRY = StaticRuntimeAdapterRegistry(((PYRAY_3D_V1_KEY, PYRAY_3D_V1_ADAPTER),))

__all__ = [
    "PYRAY_3D_ADAPTER_ID",
    "PYRAY_3D_ADAPTER_VERSION",
    "PYRAY_3D_BINDING_DISTRIBUTION",
    "PYRAY_3D_BINDING_VERSION",
    "PYRAY_3D_HEADER_VERSION",
    "PYRAY_3D_RLGL_VERSION",
    "PYRAY_3D_V1_ADAPTER",
    "PYRAY_3D_V1_KEY",
    "PYRAY_3D_V1_REGISTRY",
    "Pyray3DABIReport",
    "Pyray3DActorInstance",
    "Pyray3DAdapter",
    "Pyray3DAssetPlan",
    "Pyray3DBindingPlan",
    "Pyray3DBounds",
    "Pyray3DError",
    "Pyray3DPayloadResolver",
    "Pyray3DSession",
    "animation_frame_at_tick",
    "build_actor_instances",
    "grid_to_world",
    "inspect_pyray_3d_abi",
    "pick_grid_cell",
    "transform_bounds",
    "world_to_grid",
]
