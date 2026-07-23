from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path, PurePosixPath

from m6_pyray_3d_fixture import write_neutral_skinned_glb

from isoworld.render.pyray_3d import (
    PYRAY_3D_V1_ADAPTER,
    Pyray3DAssetPlan,
    Pyray3DBindingPlan,
    inspect_pyray_3d_abi,
)
from isoworld.render.render_state import ActorView, RenderState, TileView


class _OwnedTemporaryResolver:
    def __init__(self, path: Path) -> None:
        self._path = path

    def resolve_payload(self, relative_path: PurePosixPath) -> Path:
        if relative_path != PurePosixPath("payload/neutral.glb"):
            raise RuntimeError("native smoke received an unexpected payload request")
        return self._path


def _render_state() -> RenderState:
    return RenderState(
        revision=0,
        world_title="Neutral",
        map_id="neutral_map",
        map_title="Neutral",
        tick=1,
        time_text="00:00",
        tiles=(TileView("neutral_tile", 0, 0, 0, (128, 128, 128, 255)),),
        actors=(
            ActorView(
                actor_id="neutral",
                display_name="Neutral",
                x=0,
                y=0,
                color=(128, 128, 128, 255),
                active=True,
                route=(),
            ),
        ),
        interactions=(),
        constructions=(),
        events=(),
        hud_lines=(),
        overlay=None,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="rwf-pyray-3d-smoke-") as temporary:
        model_path = Path(temporary) / "snapshot/payload/neutral.glb"
        inspection = write_neutral_skinned_glb(model_path)
        payload = model_path.read_bytes()
        asset = Pyray3DAssetPlan(
            asset_id="neutral_actor",
            payload_path=PurePosixPath("payload/neutral.glb"),
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            triangles=1,
            animation_id="idle",
            animation_keyframes=61,
        )
        binding = Pyray3DBindingPlan(
            slot="actor:neutral",
            asset_id=asset.asset_id,
            uniform_scale=1.0,
            layer=1,
        )
        resolver = _OwnedTemporaryResolver(model_path)
        with PYRAY_3D_V1_ADAPTER.open_session(
            resolver,
            (asset,),
            (binding,),
        ) as session:
            selected = session.draw(
                _render_state(),
                ray_origin=(0.5, 2.0, 0.5),
                ray_direction=(0.0, -1.0, 0.0),
            )
            if selected != (0, 0):
                raise RuntimeError("native smoke did not return the admitted neutral tile")
            bounds = session.local_bounds[asset.asset_id]
            if bounds.minimum == bounds.maximum:
                raise RuntimeError("native smoke model bounds are unexpectedly degenerate")

        abi = inspect_pyray_3d_abi()
        print(
            json.dumps(
                {
                    "animation_count": inspection["metrics"]["animations"],
                    "binding_distribution": abi.binding_distribution,
                    "binding_version": abi.binding_version,
                    "header_version": abi.header_version,
                    "native_3d_verified": True,
                    "platform_scope": "linux_x86_64",
                    "rlgl_version": abi.rlgl_version,
                    "triangles": inspection["metrics"]["triangles"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
