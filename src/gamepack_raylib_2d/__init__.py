"""Bounded raylib 2D adapters for exact generic runtime bundles."""

from gamepack_raylib_2d.app import RuntimeApp
from gamepack_raylib_2d.audit import AdapterBoundaryError, audit_adapter_boundary
from gamepack_raylib_2d.backend import PyrayBackend, RaylibBackend, RecordingBackend
from gamepack_raylib_2d.executable_shape import (
    ADAPTER_EXECUTABLE_SHAPE_UNSUPPORTED,
    AdapterExecutableShape,
    AdapterExecutableShapeError,
    inspect_adapter_executable_shape,
)
from gamepack_raylib_2d.fixed_step import FixedStepClock
from gamepack_raylib_2d.input import InputRouter
from gamepack_raylib_2d.narrative_text import NarrativeTextController
from gamepack_raylib_2d.native_smoke import NativeSmokeError, native_smoke
from gamepack_raylib_2d.puzzle import PuzzleController
from gamepack_raylib_2d.registry import (
    AdapterImplementation,
    AdapterResolutionError,
    resolve_adapter,
)
from gamepack_raylib_2d.resources import (
    BoundResource,
    LoadedRuntimeBundle,
    RaylibResourceError,
    ResourceManager,
    load_runtime_bundle,
)
from gamepack_raylib_2d.types import (
    FontHandle,
    InputFrame,
    SemanticIntent,
    TextureHandle,
)

__all__ = [
    "ADAPTER_EXECUTABLE_SHAPE_UNSUPPORTED",
    "AdapterBoundaryError",
    "AdapterExecutableShape",
    "AdapterExecutableShapeError",
    "AdapterImplementation",
    "AdapterResolutionError",
    "BoundResource",
    "FixedStepClock",
    "FontHandle",
    "InputFrame",
    "InputRouter",
    "LoadedRuntimeBundle",
    "NarrativeTextController",
    "NativeSmokeError",
    "PuzzleController",
    "PyrayBackend",
    "RaylibBackend",
    "RaylibResourceError",
    "RecordingBackend",
    "ResourceManager",
    "RuntimeApp",
    "SemanticIntent",
    "TextureHandle",
    "audit_adapter_boundary",
    "inspect_adapter_executable_shape",
    "load_runtime_bundle",
    "native_smoke",
    "resolve_adapter",
]
