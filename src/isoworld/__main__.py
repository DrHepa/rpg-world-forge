from __future__ import annotations

import argparse
from pathlib import Path

from isoworld.content.loader import load_worldpack
from isoworld.core.app import GameApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a compiled isometric worldpack")
    parser.add_argument(
        "--pack",
        type=Path,
        default=Path("content/compiled/foundation.worldpack.json"),
        help="compiled worldpack",
    )
    parser.add_argument(
        "--headless-ticks",
        type=int,
        default=0,
        help="simulate N ticks without opening a window",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pack = load_worldpack(args.pack)
    app = GameApp(pack)
    if args.headless_ticks > 0:
        state = app.run_headless(args.headless_ticks)
        print(
            f"world={pack.world_id} tick={state.tick} "
            f"active_actor={state.active_actor_id}"
        )
        return 0
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
