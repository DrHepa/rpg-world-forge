"""Bounded controller/app composition for fake and pyray backends."""

from __future__ import annotations

from pathlib import Path

from gamepack_raylib_2d.backend import RaylibBackend
from gamepack_raylib_2d.fixed_step import FixedStepClock
from gamepack_raylib_2d.input import InputFrame, InputRouter
from gamepack_raylib_2d.narrative_text import NarrativeTextController
from gamepack_raylib_2d.puzzle import PuzzleController
from gamepack_raylib_2d.registry import (
    AdapterImplementation,
    AdapterResolutionError,
    resolve_adapter,
)
from gamepack_raylib_2d.resources import (
    LoadedRuntimeBundle,
    ResourceManager,
    load_runtime_bundle,
)
from gamepack_raylib_2d.types import SemanticIntent
from gamepack_runtime import GamePersistenceContext, build_game_persistence_context

_BACKGROUND = (12, 15, 24, 255)


class RuntimeApp:
    __slots__ = (
        "_backend",
        "_closed",
        "_clock",
        "_resources",
        "_router",
        "bundle",
        "controller",
        "implementation",
        "persistence_context",
    )

    def __init__(
        self,
        bundle: LoadedRuntimeBundle,
        implementation: AdapterImplementation,
        backend: RaylibBackend,
        *,
        locale: str,
        hidden: bool,
    ) -> None:
        self.bundle = bundle
        self.implementation = implementation
        self._backend = backend
        self._clock = FixedStepClock()
        self._router = InputRouter(implementation.adapter_id)
        self._resources = ResourceManager(bundle, backend)
        self._closed = False
        self.persistence_context: GamePersistenceContext = build_game_persistence_context(
            bundle.gamepack,
            bundle.composition,
            bundle.manifest,
            bundle.adapter,
        )
        if implementation.controller_kind == "puzzle":
            self.controller: PuzzleController | NarrativeTextController = PuzzleController(
                bundle.gamepack,
                max_actions=implementation.max_actions,
            )
        elif implementation.controller_kind == "narrative_text":
            self.controller = NarrativeTextController(
                bundle.gamepack,
                locale=locale,
                max_actions=implementation.max_actions,
            )
        else:
            raise AdapterResolutionError(
                "adapter_executable_shape_unsupported: controller kind is not code-owned"
            )
        try:
            backend.open_window(800, 520, "World Forge Runtime", hidden=hidden)
            self._resources.load()
        except BaseException:
            self.close()
            raise

    @classmethod
    def from_bundle(
        cls,
        root: str | Path,
        *,
        backend: RaylibBackend,
        locale: str = "en",
        hidden: bool = True,
    ) -> RuntimeApp:
        loaded = load_runtime_bundle(root)
        implementation = resolve_adapter(loaded)
        return cls(
            loaded,
            implementation,
            backend,
            locale=locale,
            hidden=hidden,
        )

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("runtime app is closed")

    def tick(self, frame_delta: float, frame: InputFrame) -> int:
        self._require_open()
        for intent in self._router.map_frame(frame):
            self.controller.queue_intent(intent)
        steps = self._clock.consume(frame_delta)
        for _ in range(steps):
            self.controller.step()
        self.render()
        return steps

    def render(self) -> None:
        self._require_open()
        self._backend.begin_frame()
        try:
            self._backend.clear(_BACKGROUND)
            self.controller.render(self._backend, self._resources)
        finally:
            self._backend.end_frame()

    def run_scripted(self, intents: list[SemanticIntent] | tuple[SemanticIntent, ...]) -> None:
        self._require_open()
        if type(intents) not in {list, tuple} or len(intents) > 128:
            raise ValueError("scripted intents must be one bounded exact sequence")
        for intent in intents:
            self.controller.queue_intent(intent)
            self.controller.step()
            self.render()

    def run(self, *, max_frames: int) -> int:
        self._require_open()
        if type(max_frames) is not int or not 1 <= max_frames <= 600:
            raise ValueError("max_frames must be an exact integer from 1 through 600")
        completed_frames = 0
        try:
            for _ in range(max_frames):
                if self._backend.should_close():
                    break
                self.tick(self._backend.frame_delta(), self._backend.poll_input())
                completed_frames += 1
        except BaseException:
            self.close()
            raise
        return completed_frames

    def structured_state(self) -> dict[str, object]:
        self._require_open()
        return self.controller.structured_state()

    def resource_report(self) -> dict[str, dict[str, object]]:
        self._require_open()
        return self._resources.report()

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._resources.close()
        finally:
            self._backend.close_window()
            self._closed = True

    def __enter__(self) -> RuntimeApp:
        self._require_open()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


__all__ = ["RuntimeApp"]
