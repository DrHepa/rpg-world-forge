"""Generate canonical generic headless execution scripts from exact bundles."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from gamepack_runtime import (
    build_game_execution_script,
    serialize_game_execution_script,
)
from worldforge.__main__ import _resolve_generic_assetpack_cli_source
from worldforge.game_runtime_bundle import build_game_runtime_bundle
from worldforge.generic_assetpack import seal_generic_assetpack

ROOT = Path(__file__).resolve().parents[1]


def _scenarios(fixture: str) -> list[dict[str, object]]:
    if fixture == "abstract-puzzle":
        return [
            {
                "scenario_id": "restart_board",
                "actions": [{"action_id": "restart_board", "parameters": {}}],
            },
            {
                "scenario_id": "swap_tiles",
                "actions": [
                    {
                        "action_id": "swap_tiles",
                        "parameters": {
                            "first_index": 0,
                            "second_index": 1,
                        },
                    }
                ],
            },
        ]
    return [
        {
            "scenario_id": "choose_left",
            "actions": [{"action_id": "choose_left", "parameters": {}}],
        },
        {
            "scenario_id": "choose_right",
            "actions": [{"action_id": "choose_right", "parameters": {}}],
        },
    ]


def _document(bundle: object, relative: str) -> dict[str, object]:
    value = json.loads(bundle.read_bytes(relative))
    if type(value) is not dict:
        raise RuntimeError(f"{relative} is not an object")
    return value


def _build_bundle(fixture: str, root: Path, output_root: Path):
    fixture_root = root / "examples" / "multigenre-contracts" / fixture
    source = _resolve_generic_assetpack_cli_source(fixture_root / "assets" / "manifest.json")
    assetpack = seal_generic_assetpack(
        output_root / f"{fixture}-assetpack",
        **source,
    )
    try:
        return build_game_runtime_bundle(
            output_root / f"{fixture}-runtime-bundle",
            gamepack_path=fixture_root / "artifacts" / f"{fixture}.gamepack.json",
            inventory_path=fixture_root / "assets" / "inventory.json",
            assetpack_root=assetpack.root,
            snapshot_path=root / "examples/multigenre-contracts/runtime/snapshot.json",
            registry_path=root / "examples/multigenre-contracts/runtime/registry.json",
            composition_path=fixture_root / "runtime/composition.json",
            support_report_path=fixture_root / "runtime/support-report.json",
        )
    finally:
        assetpack.close()


def build_fixtures(source_root: str | Path = ROOT) -> dict[str, bytes]:
    root = Path(source_root)
    generated: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="world-forge-headless-fixtures-") as temporary:
        temporary_root = Path(temporary)
        for fixture in ("abstract-puzzle", "branching-narrative"):
            bundle = _build_bundle(fixture, root, temporary_root)
            try:
                adapter_path = bundle.manifest["contracts"]["runtime_adapter"]["path"]
                script = build_game_execution_script(
                    bundle.manifest,
                    gamepack=_document(bundle, "contracts/gamepack.json"),
                    composition=_document(bundle, "contracts/runtime-composition.json"),
                    adapter=_document(bundle, adapter_path),
                    runtime_snapshot=_document(
                        bundle,
                        "contracts/runtime-snapshot.json",
                    ),
                    scenarios=_scenarios(fixture),
                )
                generated[
                    "examples/multigenre-contracts/"
                    f"{fixture}/runtime/headless/execution-script.json"
                ] = serialize_game_execution_script(script)
            finally:
                bundle.close()
    return generated


def generate(*, check: bool = False) -> None:
    for relative, payload in build_fixtures(ROOT).items():
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
