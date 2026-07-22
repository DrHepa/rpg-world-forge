from __future__ import annotations

import tempfile
from pathlib import Path

import pyray as pr


def main() -> int:
    generated = None
    loaded = None
    try:
        generated = pr.gen_image_color(8, 8, pr.MAGENTA)
        if not pr.is_image_valid(generated):
            raise RuntimeError("raylib could not generate the CPU smoke-test image")

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "raylib-cpu-media-smoke.png"
            if not pr.export_image(generated, str(image_path)):
                raise RuntimeError("raylib could not encode the CPU smoke-test image")
            loaded = pr.load_image(str(image_path))
            if not pr.is_image_valid(loaded):
                raise RuntimeError("raylib could not decode the CPU smoke-test image")
            if (loaded.width, loaded.height) != (8, 8):
                raise RuntimeError("raylib decoded unexpected CPU smoke-test dimensions")
            pixel = pr.get_image_color(loaded, 0, 0)
            if (pixel.r, pixel.g, pixel.b, pixel.a) != pr.MAGENTA:
                raise RuntimeError("raylib decoded unexpected CPU smoke-test pixels")
    finally:
        if loaded is not None:
            pr.unload_image(loaded)
        if generated is not None:
            pr.unload_image(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
