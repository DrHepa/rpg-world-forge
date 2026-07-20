from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from worldforge.integrity import canonical_payload_hash
from worldforge.project import SourceProject, load_source_project
from worldforge.validation import ValidationIssue, validate_project

WORLDPACK_V5_COLLECTIONS = {
    "consequences",
    "constructions",
    "dialogues",
    "facts",
    "factions",
    "goals",
    "locales",
    "needs",
    "personal_arcs",
    "production_recipes",
    "quests",
    "resources",
    "scenes",
    "stockpiles",
}

DEFAULT_RUNTIME_API_MINIMUM = "0.5.0"
DEFAULT_RUNTIME_API_MAXIMUM_EXCLUSIVE = "0.6.0"


class CompilationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        super().__init__("The project contains validation errors")
        self.issues = issues


def _content_features(project: SourceProject) -> set[str]:
    features: set[str] = set()
    if project.collections.get("personal_arcs"):
        features.add("personal_campaigns")
    if project.collections.get("locales"):
        features.add("locales")
    return features


def _normalized_runtime_requirements(project: SourceProject) -> dict[str, Any]:
    world = project.world
    configured = world.get("runtime_requirements")
    if configured is None:
        runtime_api = {
            "minimum": DEFAULT_RUNTIME_API_MINIMUM,
            "maximum_exclusive": DEFAULT_RUNTIME_API_MAXIMUM_EXCLUSIVE,
        }
        required_features = set(world.get("capabilities", [])) | _content_features(project)
        optional_features: list[str] = []
    else:
        runtime_api = configured["runtime_api"]
        required_features = configured["required_features"]
        optional_features = configured["optional_features"]
    return {
        "runtime_api": {
            "minimum": runtime_api["minimum"],
            "maximum_exclusive": runtime_api["maximum_exclusive"],
        },
        "required_features": sorted(required_features),
        "optional_features": sorted(optional_features),
    }


def _normalized_world(project: SourceProject) -> dict[str, Any]:
    world = deepcopy(project.world)
    world.pop("runtime_requirements", None)
    default_locale = world.get("default_locale", world["language"])
    locale_tags = [item["language_tag"] for item in project.collections.get("locales", [])]
    supported_locales = world.get(
        "supported_locales", locale_tags if locale_tags else [default_locale]
    )
    world["default_locale"] = default_locale
    world["supported_locales"] = sorted(supported_locales, key=str.casefold)
    return world


def _normalized_collection(name: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = deepcopy(items)
    if name == "personal_arcs":
        for campaign in normalized:
            campaign["acts"] = sorted(campaign["acts"], key=lambda act: act["id"])
            for act in campaign["acts"]:
                for field in ("next_act_ids", "quest_ids", "scene_ids"):
                    act[field] = sorted(act.get(field, []))
    elif name == "locales":
        for locale in normalized:
            locale["strings"] = dict(sorted(locale["strings"].items()))
    return sorted(normalized, key=lambda item: item["id"])


def build_worldpack(project: SourceProject) -> dict[str, Any]:
    issues = validate_project(project)
    if issues:
        raise CompilationError(issues)
    payload: dict[str, Any] = {
        "format": "isoworld.worldpack",
        "format_version": 5,
        "runtime_requirements": _normalized_runtime_requirements(project),
        "world": _normalized_world(project),
        "collections": {
            name: _normalized_collection(name, project.collections.get(name, []))
            for name in sorted(set(project.collections) | WORLDPACK_V5_COLLECTIONS)
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
