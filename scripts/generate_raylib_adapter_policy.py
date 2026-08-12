"""Generate the exact runtime-side descriptor admission policy."""

from __future__ import annotations

import argparse
from pathlib import Path

from worldforge.generic_runtime import build_builtin_runtime_adapters

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "gamepack_raylib_2d" / "descriptor_policy.py"


def build_policy_module() -> bytes:
    adapters = build_builtin_runtime_adapters()
    entries = "".join(
        (
            "    (\n"
            f'        "{adapter["adapter_id"]}@{adapter["adapter_version"]}",\n'
            f'        "{adapter["content_hash"]}",\n'
            "    ),\n"
        )
        for adapter in adapters
    )
    return (
        '"""AUTO-GENERATED exact descriptor policy; regenerate through the canonical script."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from types import MappingProxyType\n"
        "\n"
        "_ADAPTER_DESCRIPTOR_HASH_ITEMS = (\n"
        f"{entries}"
        ")\n"
        "\n"
        "ADAPTER_DESCRIPTOR_HASHES = MappingProxyType(dict(_ADAPTER_DESCRIPTOR_HASH_ITEMS))\n"
        "\n"
        '__all__ = ["ADAPTER_DESCRIPTOR_HASHES"]\n'
    ).encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    payload = build_policy_module()
    if arguments.check:
        if not TARGET.exists() or TARGET.read_bytes() != payload:
            raise SystemExit(f"raylib adapter policy is stale: {TARGET.relative_to(ROOT)}")
        return 0
    TARGET.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
