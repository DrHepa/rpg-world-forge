"""Generate canonical executable-materialization contract fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from worldforge.integrity import canonical_json_bytes
from worldforge.runtime_implementation import build_runtime_implementation
from worldforge.runtime_platform_lock import build_builtin_runtime_platform_locks

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "multigenre-contracts"


def _documents() -> dict[Path, bytes]:
    snapshot = json.loads((EXAMPLES / "runtime/snapshot.json").read_text(encoding="utf-8"))
    registry = json.loads((EXAMPLES / "runtime/registry.json").read_text(encoding="utf-8"))
    locks = build_builtin_runtime_platform_locks()
    result = {
        EXAMPLES / "runtime/platform-locks" / f"{lock['lock_id']}.json": (
            canonical_json_bytes(lock)
        )
        for lock in locks
    }
    for name, adapter_id in (
        ("abstract-puzzle", "gamepack_raylib_2d_puzzle"),
        ("branching-narrative", "gamepack_raylib_2d_text"),
    ):
        adapter = next(item for item in registry["adapters"] if item["adapter_id"] == adapter_id)
        implementation = build_runtime_implementation(
            adapter=adapter,
            snapshot=snapshot,
            platform_locks=locks,
        )
        result[EXAMPLES / name / "runtime/runtime-implementation.json"] = canonical_json_bytes(
            implementation
        )
    return result


def generate(*, check: bool = False) -> None:
    documents = _documents()
    lock_root = EXAMPLES / "runtime/platform-locks"
    expected_lock_names = {path.name for path in documents if path.parent == lock_root}
    if check:
        actual_lock_names = (
            {path.name for path in lock_root.iterdir() if path.is_file()}
            if lock_root.is_dir()
            else set()
        )
        if actual_lock_names != expected_lock_names:
            raise SystemExit(f"{lock_root} fixture closure is out of date")
        for path, payload in documents.items():
            if not path.is_file() or path.read_bytes() != payload:
                raise SystemExit(f"{path} is out of date")
        return
    for path, payload in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generate(check=args.check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
