from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.generate_creation_workflow_fixtures import (
    AUTHORING_CASES,
    _build_authoring_case_documents,
)
from worldforge.asset_io import (
    read_json_object,
    write_json_atomic,
    write_json_cooperative_replace,
)
from worldforge.game_analysis import analyze_gamepack, serialize_game_analysis
from worldforge.gamepack import (
    build_authoring_capability_ledger,
    build_gamepack,
    load_game_source_project,
    serialize_capability_ledger,
    serialize_gamepack,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"
CASES = (
    "abstract-puzzle",
    "branching-narrative",
    "action-framing",
    "faction-strategy",
    "modular-roguelite",
    "sports-career",
)


def _artifacts(case: str) -> tuple[tuple[Path, dict[str, object], bytes], ...]:
    if case in AUTHORING_CASES:
        _, files = _build_authoring_case_documents(case)
        gamepack = json.loads(files[f"artifacts/{case}.gamepack.json"])
    else:
        project = load_game_source_project(EXAMPLES / case)
        gamepack = build_gamepack(project)
    ledger = build_authoring_capability_ledger(gamepack)
    analysis = analyze_gamepack(gamepack)
    directory = EXAMPLES / case / "artifacts"
    return (
        (
            directory / f"{case}.gamepack.json",
            gamepack,
            serialize_gamepack(gamepack),
        ),
        (
            directory / f"{case}.authoring-ledger.json",
            ledger,
            serialize_capability_ledger(ledger),
        ),
        (
            directory / f"{case}.game-analysis.json",
            analysis,
            serialize_game_analysis(analysis),
        ),
    )


def _write(path: Path, document: dict[str, object]) -> None:
    if path.exists():
        current = read_json_object(path)
        expected_content_hash = current.get("content_hash")
        if not isinstance(expected_content_hash, str):
            raise ValueError(f"{path} has no string content_hash")
        write_json_cooperative_replace(
            path,
            document,
            expected_cooperative_content_hash=expected_content_hash,
        )
    else:
        write_json_atomic(path, document)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate or verify trusted generic gamepack fixtures",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--write",
        action="store_true",
        help="write or cooperatively replace canonical fixtures instead of checking them",
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify canonical fixtures (the default)",
    )
    args = parser.parse_args(argv)
    mismatches: list[Path] = []
    for case in CASES:
        for path, document, expected in _artifacts(case):
            if args.write:
                _write(path, document)
            elif not path.is_file() or path.read_bytes() != expected:
                mismatches.append(path)
    if mismatches:
        for path in mismatches:
            print(f"ERROR fixture differs: {path.relative_to(ROOT)}")
        return 1
    print(f"OK gamepack_fixtures={len(CASES) * 3} mode={'write' if args.write else 'check'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
