from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worldforge.integrity import canonical_payload_hash
from worldforge.project import SourceProject, load_source_project
from worldforge.validation import ValidationIssue, validate_project

WORLDPACK_V4_COLLECTIONS = {
    "consequences",
    "constructions",
    "dialogues",
    "facts",
    "factions",
    "goals",
    "needs",
    "production_recipes",
    "quests",
    "resources",
    "scenes",
    "stockpiles",
}


class CompilationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        super().__init__("The project contains validation errors")
        self.issues = issues


def build_worldpack(project: SourceProject) -> dict[str, Any]:
    issues = validate_project(project)
    if issues:
        raise CompilationError(issues)
    payload: dict[str, Any] = {
        "format": "isoworld.worldpack",
        "format_version": 4,
        "world": project.world,
        "collections": {
            name: sorted(project.collections.get(name, []), key=lambda item: item["id"])
            for name in sorted(set(project.collections) | WORLDPACK_V4_COLLECTIONS)
        },
    }
    payload["content_hash"] = canonical_payload_hash(payload)
    return payload


def compile_project(manifest_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    project = load_source_project(manifest_path)
    payload = build_worldpack(project)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload
