from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from isoworld.content.loader import WorldPackError, load_worldpack
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
from isoworld.render.resources import ResourceError


def _nonnegative_ticks(value: str) -> int:
    try:
        ticks = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if ticks < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return ticks


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
        type=_nonnegative_ticks,
        default=None,
        metavar="N",
        help="explicitly run headless and simulate N ticks (including zero)",
    )
    parser.add_argument("--load-save", type=Path, help="load a versioned save before running")
    parser.add_argument("--save", type=Path, help="quick-save path (F5 save, F9 load)")
    parser.add_argument("--save-on-exit", type=Path, help="write state after the run")
    parser.add_argument("--record-replay", type=Path, help="write the deterministic action log")
    parser.add_argument("--replay", type=Path, help="verify a replay without opening a window")
    return parser


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.replay is not None:
        conflicts = [
            flag
            for flag, selected in (
                ("--headless-ticks", args.headless_ticks is not None),
                ("--load-save", args.load_save is not None),
                ("--save", args.save is not None),
                ("--save-on-exit", args.save_on_exit is not None),
                ("--record-replay", args.record_replay is not None),
            )
            if selected
        ]
        if conflicts:
            parser.error(f"--replay cannot be combined with {', '.join(conflicts)}")
    if args.record_replay is not None and args.load_save is not None:
        parser.error("--record-replay cannot be combined with --load-save")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        pack = load_worldpack(args.pack)
        renderpack = load_renderpack(args.renderpack, pack) if args.renderpack is not None else None
        if args.replay is not None:
            actions, state = load_replay(args.replay, pack)
            print(
                f"world={pack.world_id} replay_actions={len(actions)} "
                f"tick={state.tick} digest={state_digest(state)}"
            )
            return 0
        initial = load_game(args.load_save, pack) if args.load_save is not None else None
        app = GameApp(
            pack,
            initial,
            args.save,
            renderpack,
            replay_recording=args.record_replay is not None,
        )
        if args.headless_ticks is not None:
            state = app.run_headless(args.headless_ticks)
            result = 0
        else:
            result = app.run()
            state = app.simulation.state
        if args.save_on_exit is not None:
            save_game(args.save_on_exit, state, pack)
        if args.record_replay is not None:
            write_replay(args.record_replay, app.simulation.action_log, state, pack)
        print(
            f"world={pack.world_id} tick={state.tick} "
            f"day={state.day} minute={state.minute_of_day} "
            f"active_actor={state.active_actor_id} digest={state_digest(state)}"
        )
        return result
    except (WorldPackError, RenderPackError, PersistenceError, ResourceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
