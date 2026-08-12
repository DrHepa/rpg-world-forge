"""Generate deterministic generic save and replay fixtures from exact bundles."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from gamepack_runtime import (
    GameSession,
    build_game_replay,
    build_game_save,
    build_persistence_generation,
    serialize_game_replay,
    serialize_game_save,
    serialize_persistence_generation,
)
from worldforge.__main__ import _resolve_generic_assetpack_cli_source
from worldforge.game_persistence import persistence_context_from_bundle
from worldforge.game_runtime_bundle import build_game_runtime_bundle
from worldforge.generic_assetpack import seal_generic_assetpack

ROOT = Path(__file__).resolve().parents[1]


def _fixture_root(name: str) -> Path:
    return ROOT / "examples" / "multigenre-contracts" / name


def build_fixtures(source_root: str | Path = ROOT) -> dict[str, bytes]:
    root = Path(source_root)
    generated: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="world-forge-game-persistence-") as temporary:
        temporary_root = Path(temporary)
        for fixture in ("abstract-puzzle", "branching-narrative"):
            fixture_root = root / "examples" / "multigenre-contracts" / fixture
            assetpack_source = _resolve_generic_assetpack_cli_source(
                fixture_root / "assets" / "manifest.json"
            )
            assetpack = seal_generic_assetpack(
                temporary_root / f"{fixture}-assetpack",
                **assetpack_source,
            )
            try:
                bundle = build_game_runtime_bundle(
                    temporary_root / f"{fixture}-bundle",
                    gamepack_path=(fixture_root / "artifacts" / f"{fixture}.gamepack.json"),
                    inventory_path=fixture_root / "assets" / "inventory.json",
                    assetpack_root=assetpack.root,
                    snapshot_path=(root / "examples/multigenre-contracts/runtime/snapshot.json"),
                    registry_path=(root / "examples/multigenre-contracts/runtime/registry.json"),
                    composition_path=fixture_root / "runtime/composition.json",
                    support_report_path=fixture_root / "runtime/support-report.json",
                )
            finally:
                assetpack.close()
            try:
                context = persistence_context_from_bundle(bundle)
                relative_root = f"examples/multigenre-contracts/{fixture}/runtime/persistence"

                def add_fixture(
                    *,
                    kind: str,
                    name: str,
                    document: dict[str, object],
                    _context=context,
                    _relative_root=relative_root,
                ) -> None:
                    plural = "saves" if kind == "save" else "replays"
                    serializer = serialize_game_save if kind == "save" else serialize_game_replay
                    generated[f"{_relative_root}/{plural}/{name}.json"] = serializer(
                        document,
                        _context,
                    )
                    generation = build_persistence_generation(
                        document,
                        kind=kind,
                        slot=name,
                        sequence=0,
                        parent_hashes=[],
                        operation="write",
                        context=_context,
                    )
                    generated[f"{_relative_root}/generations/{plural}/{name}.json"] = (
                        serialize_persistence_generation(
                            generation,
                            context=_context,
                        )
                    )

                if fixture == "abstract-puzzle":
                    initial = GameSession(context.gamepack)
                    add_fixture(
                        kind="save",
                        name="initial",
                        document=build_game_save(context, initial.state),
                    )
                    add_fixture(
                        kind="replay",
                        name="zero-step",
                        document=build_game_replay(context, []),
                    )
                    solved = GameSession(context.gamepack)
                    result = solved.apply(
                        "swap_tiles",
                        {"first_index": 0, "second_index": 1},
                    )
                    if not result.accepted:
                        raise RuntimeError("canonical puzzle solution was rejected")
                    add_fixture(
                        kind="save",
                        name="solved",
                        document=build_game_save(context, solved.state),
                    )
                    add_fixture(
                        kind="replay",
                        name="solve",
                        document=build_game_replay(context, [result]),
                    )
                else:
                    for branch in ("left", "right"):
                        session = GameSession(context.gamepack)
                        result = session.apply(f"choose_{branch}", {})
                        if not result.accepted:
                            raise RuntimeError(f"canonical narrative {branch} choice was rejected")
                        add_fixture(
                            kind="save",
                            name=branch,
                            document=build_game_save(context, session.state),
                        )
                        add_fixture(
                            kind="replay",
                            name=branch,
                            document=build_game_replay(context, [result]),
                        )
            finally:
                bundle.close()
    return generated


def generate(*, check: bool = False) -> None:
    generated = build_fixtures(ROOT)
    for relative, payload in generated.items():
        path = ROOT / relative
        if check:
            if not path.exists() or path.read_bytes() != payload:
                raise SystemExit(f"{path} is out of date")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate(check=args.check)


if __name__ == "__main__":
    main()
