from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from worldforge.project import SourceProject, load_source_project
from worldforge.validation import ValidationIssue, validate_project


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
        "format_version": 2,
        "world": project.world,
        "collections": {
            name: sorted(items, key=lambda item: item["id"])
            for name, items in sorted(project.collections.items())
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["content_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
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
