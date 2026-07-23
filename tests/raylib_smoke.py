from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pyray as pr

from isoworld.content.loader import load_worldpack
from isoworld.content.renderpack import (
    AnimationClip,
    AssetFile,
    ClipFrame,
    RenderAsset,
    RenderBinding,
    RenderPack,
)
from isoworld.render.pyray_2_5d import (
    PYRAY_2_5D_ADAPTER,
    PYRAY_2_5D_KEY,
    PYRAY_2_5D_REGISTRY,
)
from isoworld.render.render_state import build_render_state
from isoworld.render.resources import RaylibAssetRegistry


def main() -> int:
    image = None
    registry = None
    try:
        pr.set_config_flags(pr.FLAG_WINDOW_HIDDEN)
        pr.init_window(96, 64, "RPG World Forge raylib smoke test")
        if not pr.is_window_ready():
            raise RuntimeError("raylib did not produce a ready graphical window")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texture_path = root / "smoke.png"
            image = pr.gen_image_color(8, 8, pr.MAGENTA)
            if not pr.export_image(image, str(texture_path)):
                raise RuntimeError("raylib could not export the smoke-test texture")
            texture_hash = hashlib.sha256(texture_path.read_bytes()).hexdigest()

            pack = load_worldpack("content/compiled/foundation.worldpack.json")
            renderpack = RenderPack(
                world_id=pack.world_id,
                world_content_hash=pack.content_hash,
                content_hash="0" * 64,
                root=root,
                assets=(
                    RenderAsset(
                        id="smoke_sprite",
                        kind="sprite",
                        files=(AssetFile("texture", "smoke.png", texture_hash, "image/png"),),
                        clips=(
                            AnimationClip(
                                id="idle",
                                frames=(ClipFrame(0, 0, 8, 8, 1),),
                                pivot_x=4.0,
                                pivot_y=8.0,
                                loop=True,
                            ),
                        ),
                    ),
                ),
                bindings=(RenderBinding("actor:explorer", "smoke_sprite", "idle"),),
            )
            adapter = PYRAY_2_5D_REGISTRY.resolve(PYRAY_2_5D_KEY)
            if adapter is not PYRAY_2_5D_ADAPTER:
                raise RuntimeError("legacy adapter registry returned a non-canonical value")
            preflight = adapter.preflight(pack, renderpack)
            if preflight.adapter_key != PYRAY_2_5D_KEY:
                raise RuntimeError("legacy adapter preflight returned a non-canonical key")
            app = adapter.create_app(pack, renderpack)
            registry = RaylibAssetRegistry(pr, renderpack)
            registry.load()
            snapshot = build_render_state(app.simulation.state, pack)
            renderer = app.renderer
            renderer.screen_width = 96
            renderer.screen_height = 64
            renderer.attach_resources(registry)
            pr.begin_drawing()
            renderer.draw(pr, snapshot)
            pr.end_drawing()
    finally:
        if registry is not None:
            registry.close()
        if image is not None:
            pr.unload_image(image)
        pr.close_window()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
