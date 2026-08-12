"""Generate code-owned executable snapshot identities from canonical runtime fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from worldforge.creation_contracts import canonical_creation_hash

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "examples/multigenre-contracts/runtime/snapshot.json"
REGISTRY = ROOT / "examples/multigenre-contracts/runtime/registry.json"
TARGET = ROOT / "src/worldforge/runtime_implementation_policy.py"


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{path} must contain one object")
    return value


def build_policy_module() -> bytes:
    snapshot = _read(SNAPSHOT)
    registry = _read(REGISTRY)
    adapters = registry["adapters"]
    files = snapshot["files"]
    assert type(adapters) is list and type(files) is list
    adapter_hashes = {
        adapter["adapter_id"]: adapter["content_hash"]
        for adapter in adapters
        if type(adapter) is dict
    }
    package_hashes = {}
    for package in ("gamepack_raylib_2d", "gamepack_runtime"):
        prefix = f"{package}/"
        selected = [
            {
                "path": str(record["path"])[len(prefix) :],
                "sha256": record["sha256"],
                "size_bytes": record["size_bytes"],
            }
            for record in files
            if type(record) is dict and str(record.get("path", "")).startswith(prefix)
        ]
        package_hashes[package] = canonical_creation_hash({"files": selected})
    lines = [
        '"""AUTO-GENERATED exact runtime implementation policy."""',
        "",
        "from __future__ import annotations",
        "",
        "TRUSTED_ADAPTER_HASHES = {",
    ]
    for adapter_id, content_hash in sorted(
        adapter_hashes.items(),
        key=lambda item: str(item[0]).encode("utf-8"),
    ):
        lines.append(f'    "{adapter_id}": "{content_hash}",')
    lines.extend(
        [
            "}",
            "",
            "TRUSTED_SNAPSHOT_IDENTITY = {",
            f'    "snapshot_id": "{snapshot["snapshot_id"]}",',
            f'    "content_hash": "{snapshot["content_hash"]}",',
            f'    "tree_hash": "{snapshot["tree_hash"]}",',
            "}",
            "",
            "TRUSTED_PACKAGE_TREE_HASHES = {",
        ]
    )
    for package, tree_hash in sorted(
        package_hashes.items(),
        key=lambda item: item[0].encode("utf-8"),
    ):
        lines.append(f'    "{package}": "{tree_hash}",')
    lines.extend(
        [
            "}",
            "",
            "__all__ = [",
            '    "TRUSTED_ADAPTER_HASHES",',
            '    "TRUSTED_PACKAGE_TREE_HASHES",',
            '    "TRUSTED_SNAPSHOT_IDENTITY",',
            "]",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = build_policy_module()
    if args.check:
        if not TARGET.is_file() or TARGET.read_bytes() != payload:
            raise SystemExit(f"{TARGET.relative_to(ROOT)} is out of date")
    else:
        TARGET.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
