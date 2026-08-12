"""Generate creation content-mode projections from the canonical profile schema."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCHEMA = ROOT / "schemas/creation-profile.schema.json"
PROTOCOL_V5_SCHEMA = ROOT / "schemas/studio-protocol-v5.schema.json"
PYTHON_TARGET = ROOT / "src/worldforge/generated_creation_content_modes.py"
TYPESCRIPT_TARGET = ROOT / "apps/studio/src/generated/creation-content-modes.ts"
ASSET_CONTENT_MODE_ENUM_SENTINEL = [
    "__WORLD_FORGE_CREATION_CONTENT_MODE_ENUM_PROJECTION__",
]
REVIEWED_PROTOCOL_V5_NONPROJECTED_SHA256 = (
    "6eab1472bba522ce0897022765ecda0783cc88397b9414e499ca1236b98db580"
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{path.relative_to(ROOT)} must contain one JSON object")
    return value


def creation_content_modes() -> tuple[str, ...]:
    schema = _read_json(PROFILE_SCHEMA)
    modes = schema.get("$defs", {}).get("productionMode", {}).get("enum")
    if (
        type(modes) is not list
        or not modes
        or any(type(mode) is not str for mode in modes)
        or "authored" not in modes
        or len(set(modes)) != len(modes)
    ):
        raise ValueError("creation-profile productionMode enum is not a closed string vocabulary")
    return tuple(modes)


def _python_tuple_literal(values: tuple[str, ...]) -> str:
    rendered = "\n".join(f'    "{value}",' for value in values)
    return f"(\n{rendered}\n)"


def build_python_module() -> bytes:
    modes = creation_content_modes()
    lines = [
        '"""AUTO-GENERATED from schemas/creation-profile.schema.json. Do not edit."""',
        "",
        "from __future__ import annotations",
        "",
        f"CREATION_CONTENT_MODES = {_python_tuple_literal(modes)}",
        'DEFAULT_CREATION_CONTENT_MODE = "authored"',
        "",
        "__all__ = [",
        '    "CREATION_CONTENT_MODES",',
        '    "DEFAULT_CREATION_CONTENT_MODE",',
        "]",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def build_typescript_module() -> bytes:
    modes = creation_content_modes()
    lines = [
        "/* AUTO-GENERATED from schemas/creation-profile.schema.json. Do not edit by hand. */",
        "export const CREATION_CONTENT_MODES = [",
        *(f'  "{mode}",' for mode in modes[:-1]),
        f'  "{modes[-1]}"',
        "] as const;",
        "export type CreationContentMode = (typeof CREATION_CONTENT_MODES)[number];",
        'export const DEFAULT_CREATION_CONTENT_MODE: CreationContentMode = "authored";',
        "const CREATION_CONTENT_MODE_SET: ReadonlySet<string> = new Set(CREATION_CONTENT_MODES);",
        "export function isCreationContentMode(value: unknown): value is CreationContentMode {",
        '  return typeof value === "string" && CREATION_CONTENT_MODE_SET.has(value);',
        "}",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _replace_asset_mode_enums(node: Any, modes: tuple[str, ...]) -> int:
    replacements = 0
    if type(node) is dict:
        for key, value in node.items():
            if key == "asset_content_mode" and type(value) is dict and "enum" in value:
                value["enum"] = list(modes)
                replacements += 1
            else:
                replacements += _replace_asset_mode_enums(value, modes)
    elif type(node) is list:
        for item in node:
            replacements += _replace_asset_mode_enums(item, modes)
    return replacements


def _nonproject_protocol_v5_schema(schema: dict[str, Any]) -> dict[str, Any]:
    nonprojected = copy.deepcopy(schema)
    replacements = _replace_asset_mode_enums(
        nonprojected,
        tuple(ASSET_CONTENT_MODE_ENUM_SENTINEL),
    )
    if replacements != 2:
        raise ValueError(
            "studio-protocol-v5 asset_content_mode enum projection expected 2 occurrences, "
            f"found {replacements}"
        )
    return nonprojected


def protocol_v5_nonprojected_sha256(schema: dict[str, Any]) -> str:
    canonical = json.dumps(
        _nonproject_protocol_v5_schema(schema),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _require_reviewed_protocol_v5_nonprojection(schema: dict[str, Any]) -> None:
    actual = protocol_v5_nonprojected_sha256(schema)
    if actual != REVIEWED_PROTOCOL_V5_NONPROJECTED_SHA256:
        raise SystemExit(
            "unreviewed studio-protocol-v5 drift: non-projected sha256 "
            f"{actual} != reviewed {REVIEWED_PROTOCOL_V5_NONPROJECTED_SHA256}; "
            "update REVIEWED_PROTOCOL_V5_NONPROJECTED_SHA256 only after reviewing the "
            "non-enum schema change"
        )


def build_protocol_v5_schema() -> bytes:
    modes = creation_content_modes()
    schema = _read_json(PROTOCOL_V5_SCHEMA)
    _require_reviewed_protocol_v5_nonprojection(schema)
    replacements = _replace_asset_mode_enums(schema, modes)
    if replacements != 2:
        raise ValueError(
            "studio-protocol-v5 asset_content_mode enum projection expected 2 occurrences, "
            f"found {replacements}"
        )
    return (json.dumps(schema, indent=2) + "\n").encode("utf-8")


def build_generated_artifacts() -> dict[Path, bytes]:
    return {
        Path("src/worldforge/generated_creation_content_modes.py"): build_python_module(),
        Path("apps/studio/src/generated/creation-content-modes.ts"): build_typescript_module(),
        Path("schemas/studio-protocol-v5.schema.json"): build_protocol_v5_schema(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    artifacts = build_generated_artifacts()
    if args.check:
        stale = [
            str(path)
            for path, payload in artifacts.items()
            if not (ROOT / path).is_file() or (ROOT / path).read_bytes() != payload
        ]
        if stale:
            raise SystemExit(
                "creation content-mode projections are out of date: " + ", ".join(stale)
            )
    else:
        for path, payload in artifacts.items():
            target = ROOT / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
