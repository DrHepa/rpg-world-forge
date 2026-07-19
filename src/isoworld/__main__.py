from __future__ import annotations

import argparse
from pathlib import Path

from isoworld.content.loader import load_worldpack
from isoworld.content.renderpack import RenderPackError, load_renderpack
from isoworld.core.app import GameApp
from isoworld.persistence import (
    PersistenceError,
    load_game,
    load_replay,
    save_game,
    state_digest,
    write_replay,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a compiled isometric worldpack")
    parser.add_argument(
        "--pack",
        type=Path,
        default=Path("content/compiled/foundation.worldpack.json"),
        help="compiled worldpack",
    )
    parser.add_argument(
        "--renderpack",
        type=Path,
        help="compiled runtime renderpack with processed assets",
    )
    parser.add_argument(
        "--headless-ticks",
        type=int,
        default=0,
        help="simulate N ticks without opening a window",
    )
    parser.add_argument("--load-save", type=Path, help="load a versioned save before running")
    parser.add_argument("--save", type=Path, help="quick-save path (F5 save, F9 load)")
    parser.add_argument("--save-on-exit", type=Path, help="write state after the run")
    parser.add_argument("--record-replay", type=Path, help="write the deterministic action log")
    parser.add_argument("--replay", type=Path, help="verify a replay without opening a window")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pack = load_worldpack(args.pack)
    try:
        renderpack = load_renderpack(args.renderpack, pack) if args.renderpack is not None else None
        if args.replay is not None:
            actions, state = load_replay(args.replay, pack)
            print(
                f"world={pack.world_id} replay_actions={len(actions)} "
                f"tick={state.tick} digest={state_digest(state)}"
            )
            return 0
        initial = load_game(args.load_save, pack) if args.load_save is not None else None
        app = GameApp(pack, initial, args.save, renderpack)
        if args.headless_ticks > 0:
            state = app.run_headless(args.headless_ticks)
            result = 0
        else:
            result = app.run()
            state = app.simulation.state
        if args.save_on_exit is not None:
            save_game(args.save_on_exit, state, pack)
        if args.record_replay is not None:
            if initial is not None:
                raise PersistenceError("Replay recording must start without --load-save")
            write_replay(args.record_replay, app.simulation.action_log, state, pack)
        print(
            f"world={pack.world_id} tick={state.tick} "
            f"day={state.day} minute={state.minute_of_day} "
            f"active_actor={state.active_actor_id} digest={state_digest(state)}"
        )
        return result
    except (PersistenceError, RenderPackError) as exc:
        print(f"ERROR {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
