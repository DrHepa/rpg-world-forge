from __future__ import annotations

import hashlib
import os
import platform
import stat
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import final

from isoworld.content.models import RUNTIME_API_VERSION, WorldPack
from isoworld.content.renderpack import AssetFile, RenderPack, RenderPackError
from isoworld.core.app import GameApp
from isoworld.runtime_adapter import RuntimeAdapterKey, StaticRuntimeAdapterRegistry
from isoworld.world.state import WorldState

PYRAY_2_5D_ADAPTER_ID = "isoworld_raylib_2_5d"
PYRAY_2_5D_ADAPTER_VERSION = "0.1.0"
PYRAY_2_5D_CONTENT_HASH = "2628adad118585ffc15c6509a49c92954660d7ea42788eed1ba69fef99e54fa8"

PYRAY_2_5D_CAPABILITY_IDS = (
    "action_replay",
    "actor_needs",
    "conditional_dialogue",
    "construction",
    "content_renderpack_v1",
    "content_worldpack_v1_v5",
    "contextual_interactions",
    "costed_abilities",
    "delayed_consequences",
    "directed_relationships",
    "grid_movement",
    "hierarchical_goals",
    "path_navigation",
    "playable_actor_switching",
    "presentation_world_2_5d",
    "reactive_quests",
    "resource_economy",
    "schedules",
    "timed_scenes",
    "typed_knowledge",
    "versioned_persistence",
    "world_clock",
)

PYRAY_2_5D_BUDGETS = MappingProxyType(
    {
        "max_assets": 5,
        "max_bindings": 3,
        "max_draw_calls": 1024,
        "max_loaded_bytes": 1_048_576,
        "max_triangles": 1,
        "target_frame_milliseconds": 1000,
    }
)

_READ_CHUNK_BYTES = 64 * 1024
_READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


class Pyray25DError(RuntimeError):
    """Raised when the exact legacy 2.5D presentation seam fails closed."""


@dataclass(frozen=True, slots=True)
class Pyray25DPreflight:
    """Bounded evidence for one already-loaded worldpack/renderpack pair."""

    adapter_key: RuntimeAdapterKey
    platform: str
    asset_count: int
    binding_count: int
    loaded_bytes: int
    smoke_draw_call_ceiling: int
    smoke_target_frame_milliseconds: int


def _host_target() -> tuple[str, str, str]:
    """Return the exact OS family, system, and normalized machine target."""

    return os.name, platform.system(), platform.machine().casefold()


def _linux_x86_64() -> bool:
    os_family, system, machine = _host_target()
    return os_family == "posix" and system == "Linux" and machine in {"amd64", "x86_64"}


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
        left.st_nlink,
        left.st_size,
        left.st_mtime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
        right.st_nlink,
        right.st_size,
        right.st_mtime_ns,
    )


def _read_exact_file(
    renderpack: RenderPack,
    item: AssetFile,
    *,
    remaining_bytes: int,
) -> tuple[int, tuple[int, int]]:
    try:
        path = renderpack.resolve_file(item)
    except (OSError, RenderPackError) as exc:
        raise Pyray25DError("renderpack resource resolution failed") from exc
    if not isinstance(path, Path) or not path.is_absolute():
        raise Pyray25DError("renderpack resource must resolve to an absolute pathlib Path")
    descriptor: int | None = None
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise Pyray25DError("renderpack resources must be regular non-link files")
        if before.st_nlink != 1:
            raise Pyray25DError("renderpack resources must not be hard-linked")
        if before.st_size > remaining_bytes:
            raise Pyray25DError("renderpack resources exceed the loaded-byte budget")
        descriptor = os.open(path, _READ_FLAGS)
        opened = os.fstat(descriptor)
        if not _same_file_identity(before, opened):
            raise Pyray25DError("renderpack resource identity changed before preflight")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > remaining_bytes:
                raise Pyray25DError("renderpack resources exceed the loaded-byte budget")
            digest.update(chunk)
        after = os.fstat(descriptor)
        visible = path.lstat()
        if not _same_file_identity(opened, after) or not _same_file_identity(after, visible):
            raise Pyray25DError("renderpack resource identity changed during preflight")
        if total != opened.st_size:
            raise Pyray25DError("renderpack resource size changed during preflight")
        if digest.hexdigest() != item.sha256:
            raise Pyray25DError("renderpack resource SHA-256 does not match its declaration")
        return total, (opened.st_dev, opened.st_ino)
    except Pyray25DError:
        raise
    except OSError as exc:
        raise Pyray25DError("renderpack resource could not be inspected safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _measure_loaded_bytes(renderpack: RenderPack) -> int:
    loaded_bytes = 0
    identities: set[tuple[int, int]] = set()
    for asset in renderpack.assets:
        for item in asset.files:
            size, identity = _read_exact_file(
                renderpack,
                item,
                remaining_bytes=PYRAY_2_5D_BUDGETS["max_loaded_bytes"] - loaded_bytes,
            )
            if identity in identities:
                raise Pyray25DError("renderpack resources must have unique file identities")
            identities.add(identity)
            loaded_bytes += size
    return loaded_bytes


@final
@dataclass(frozen=True, slots=True)
class Pyray25DAdapter:
    """Opaque code-owned seam over the established isometric ``GameApp``."""

    @property
    def declaration_key(self) -> RuntimeAdapterKey:
        return PYRAY_2_5D_KEY

    def preflight(self, pack: WorldPack, renderpack: RenderPack) -> Pyray25DPreflight:
        """Validate the bounded legacy presentation inputs without importing pyray."""

        if type(pack) is not WorldPack:
            raise Pyray25DError("pack must be an exact loaded WorldPack")
        if type(renderpack) is not RenderPack:
            raise Pyray25DError("renderpack must be an exact loaded RenderPack")
        if not _linux_x86_64():
            raise Pyray25DError("isoworld_raylib_2_5d is verified only for Linux x86_64")
        if (
            renderpack.world_id != pack.world_id
            or renderpack.world_content_hash != pack.content_hash
        ):
            raise Pyray25DError("renderpack and worldpack identities do not match")
        requirements = pack.runtime_requirements
        if not requirements.runtime_api.contains(RUNTIME_API_VERSION):
            raise Pyray25DError("worldpack runtime API is incompatible with this adapter")
        missing = sorted(set(requirements.required_features) - set(PYRAY_2_5D_CAPABILITY_IDS))
        if missing:
            raise Pyray25DError(
                "worldpack requires capabilities outside the exact adapter declaration"
            )
        asset_count = len(renderpack.assets)
        binding_count = len(renderpack.bindings)
        if not 1 <= asset_count <= PYRAY_2_5D_BUDGETS["max_assets"]:
            raise Pyray25DError("renderpack asset count exceeds the verified smoke bound")
        if not 1 <= binding_count <= PYRAY_2_5D_BUDGETS["max_bindings"]:
            raise Pyray25DError("renderpack binding count exceeds the verified smoke bound")
        asset_ids = {asset.id for asset in renderpack.assets}
        if any(binding.asset_id not in asset_ids for binding in renderpack.bindings):
            raise Pyray25DError("renderpack binding references an unknown asset")
        loaded_bytes = _measure_loaded_bytes(renderpack)
        return Pyray25DPreflight(
            adapter_key=PYRAY_2_5D_KEY,
            platform="linux_x86_64",
            asset_count=asset_count,
            binding_count=binding_count,
            loaded_bytes=loaded_bytes,
            smoke_draw_call_ceiling=PYRAY_2_5D_BUDGETS["max_draw_calls"],
            smoke_target_frame_milliseconds=PYRAY_2_5D_BUDGETS["target_frame_milliseconds"],
        )

    def create_app(
        self,
        pack: WorldPack,
        renderpack: RenderPack,
        *,
        state: WorldState | None = None,
        quick_save_path: Path | None = None,
        replay_recording: bool = False,
    ) -> GameApp:
        """Create the established app only after exact bounded preflight."""

        self.preflight(pack, renderpack)
        if state is not None and type(state) is not WorldState:
            raise Pyray25DError("state must be an exact WorldState")
        if quick_save_path is not None and not isinstance(quick_save_path, Path):
            raise Pyray25DError("quick_save_path must be an exact pathlib Path")
        if type(replay_recording) is not bool:
            raise Pyray25DError("replay_recording must be a built-in bool")
        return GameApp(
            pack,
            state,
            quick_save_path,
            renderpack,
            replay_recording=replay_recording,
        )


PYRAY_2_5D_KEY = RuntimeAdapterKey(
    id=PYRAY_2_5D_ADAPTER_ID,
    version=PYRAY_2_5D_ADAPTER_VERSION,
    content_hash=PYRAY_2_5D_CONTENT_HASH,
)
PYRAY_2_5D_ADAPTER = Pyray25DAdapter()
PYRAY_2_5D_REGISTRY = StaticRuntimeAdapterRegistry(((PYRAY_2_5D_KEY, PYRAY_2_5D_ADAPTER),))

__all__ = [
    "PYRAY_2_5D_ADAPTER",
    "PYRAY_2_5D_ADAPTER_ID",
    "PYRAY_2_5D_ADAPTER_VERSION",
    "PYRAY_2_5D_BUDGETS",
    "PYRAY_2_5D_CAPABILITY_IDS",
    "PYRAY_2_5D_CONTENT_HASH",
    "PYRAY_2_5D_KEY",
    "PYRAY_2_5D_REGISTRY",
    "Pyray25DAdapter",
    "Pyray25DError",
    "Pyray25DPreflight",
]
