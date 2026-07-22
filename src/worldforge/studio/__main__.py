from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from worldforge.studio.service import serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="worldforge-studio-service",
        description="Run the local Forge Studio v1 application service over NDJSON stdio.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Explicit user-data directory for the Studio registry, jobs, blobs, and journals.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return serve(sys.stdin.buffer, sys.stdout.buffer, data_dir=args.data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
