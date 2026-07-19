from __future__ import annotations

import json
from pathlib import Path

from worldforge.validation import ID_PATTERN
from worldforge.workflow import initial_status, phase_catalog


class ScaffoldError(ValueError):
    """Raised when a new project cannot be created safely."""


COLLECTIONS = (
    "abilities",
    "actors",
    "dialogues",
    "facts",
    "factions",
    "interactions",
    "maps",
    "personal_arcs",
    "quests",
    "scenes",
    "schedules",
    "tile_types",
)

SOURCE_GUIDES = {
    "design": "Premise, audience, experience, scope, exclusions, and constraints.",
    "style": "Genre, tone, themes, prose guide, and naming conventions.",
    "canon": "Approved facts, ontology, world laws, and sources of truth.",
    "facts": "Atomic facts with stable IDs, epistemic state, source, and dependencies.",
    "research": "External sources, evidence, inference, and reference licenses.",
    "geography": "Regions, biomes, settlements, routes, resources, and maps.",
    "history": "Eras, events, causes, consequences, and disputed accounts.",
    "timeline": "Canonical chronology, calendars, durations, and temporal dependencies.",
    "societies": "Cultures, institutions, economy, values, rituals, and conflicts.",
    "factions": "Compilable faction data, resources, relationships, and reputation.",
    "characters": "Dossiers, motivations, abilities, relationships, and personal campaigns.",
    "actors": "Compilable playable/non-playable actors and narrative references.",
    "knowledge": "Facts known, suspected, protected, or forbidden per actor.",
    "abilities": "Compilable abilities with requirements, costs, effects, and consequences.",
    "interactions": "Contextual world interactions with conditions and deterministic effects.",
    "schedules": "Schedules, routes, activities, conditions, and navigation fallbacks.",
    "mechanics": "Deterministic systems, events, state, and interacting rules.",
    "arcs": "World and personal arcs, acts, branches, failure, and recovery.",
    "personal_arcs": "Compilable personal campaigns referenced by playable actors.",
    "events": "Implementable events with conditions, effects, and causality.",
    "quests": "Quest state machines, objectives, transitions, and rewards.",
    "scenes": "Scenes with participants, time, location, conditions, and effects.",
    "dialogues": "Localized dialogue graphs, choices, conditions, and effects.",
    "localization": "Glossary, voice, visible text, and per-language rules.",
    "implementation": "Contracts, catalogs, tests, and game-repository handoff.",
}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_world_project(
    target: str | Path,
    *,
    world_id: str,
    title: str,
    language: str,
    actor_id: str | None = None,
    actor_name: str | None = None,
) -> Path:
    if not ID_PATTERN.fullmatch(world_id):
        raise ScaffoldError("world_id must use 2..64-character ASCII snake_case")
    if (actor_id is None) != (actor_name is None):
        raise ScaffoldError("actor_id and actor_name must be provided together")
    if actor_id is not None and not ID_PATTERN.fullmatch(actor_id):
        raise ScaffoldError("actor_id must use 2..64-character ASCII snake_case")
    if not title.strip() or (actor_name is not None and not actor_name.strip()):
        raise ScaffoldError("title and actor_name cannot be empty")
    if len(language.strip()) < 2:
        raise ScaffoldError("language is invalid")

    root = Path(target)
    if root.exists():
        raise ScaffoldError(f"The target already exists: {root}")
    source = root / "source"
    source.mkdir(parents=True)

    is_spanish = language.lower().startswith("es")
    ui = (
        {
            "active_actor": "Personaje activo",
            "move_help": "Flechas o WASD: mover",
            "navigate_help": "Clic izquierdo: navegar",
            "switch_help": "Tab: cambiar de personaje",
            "interact_help": "E: interactuar",
            "ability_help": "1: usar primera habilidad",
            "clock_label": "Día",
        }
        if is_spanish
        else {
            "active_actor": "Active character",
            "move_help": "Arrow keys or WASD: move",
            "navigate_help": "Left click: navigate",
            "switch_help": "Tab: switch character",
            "interact_help": "E: interact",
            "ability_help": "1: use first ability",
            "clock_label": "Day",
        }
    )
    paths = {collection: [] for collection in COLLECTIONS}
    if actor_id is not None:
        paths["actors"] = [f"actors/{actor_id}.json"]
    paths["maps"] = ["maps/starting_area.json"]
    paths["tile_types"] = ["tile_types/ground.json"]

    _write_json(
        source / "manifest.json",
        {
            "format": "isoworld.source_manifest",
            "format_version": 1,
            "world": "world.json",
            "collections": paths,
        },
    )
    _write_json(
        source / "world.json",
        {
            "id": world_id,
            "title": title.strip(),
            "language": language.strip(),
            "start_map_id": "starting_area",
            "capabilities": [
                "grid_movement",
                "path_navigation",
                "world_clock",
                "contextual_interactions",
                "costed_abilities",
                "versioned_persistence",
            ],
            "simulation": {
                "start_day": 1,
                "start_minute": 480,
                "ticks_per_minute": 20,
                "movement_interval_ticks": 4,
            },
            "ui": ui,
        },
    )
    _write_json(
        source / "tile_types/ground.json",
        {
            "id": "ground",
            "display_name": "Suelo" if is_spanish else "Ground",
            "color": [82, 112, 78, 255],
            "walkable": True,
            "arable": True,
            "height": 0,
            "tags": ["terrain"],
        },
    )
    _write_json(
        source / "maps/starting_area.json",
        {
            "id": "starting_area",
            "display_name": "Área inicial" if is_spanish else "Starting area",
            "width": 8,
            "height": 8,
            "legend": {".": "ground"},
            "rows": ["........"] * 8,
        },
    )
    if actor_id is not None and actor_name is not None:
        _write_json(
            source / f"actors/{actor_id}.json",
            {
                "id": actor_id,
                "display_name": actor_name.strip(),
                "playable": True,
                "spawn": {"map_id": "starting_area", "x": 3, "y": 3},
                "color": [194, 137, 255, 255],
                "tags": [],
            },
        )

    for directory, purpose in SOURCE_GUIDES.items():
        guide = source / directory / "README.md"
        guide.parent.mkdir(parents=True, exist_ok=True)
        guide.write_text(
            f"# {directory.replace('_', ' ').title()}\n\n{purpose}\n\n"
            "Keep `proposal`, `candidate`, `canon`, and `deprecated` separate. "
            "Record stable IDs and dependencies in every canonical document.\n",
            encoding="utf-8",
        )

    _write_json(
        root / ".worldforge/project.json",
        {
            "format": "rpg-world-forge.project",
            "format_version": 1,
            "world_id": world_id,
            "title": title.strip(),
            "language": language.strip(),
            "lead_agent": "gpt",
            "approval_mode": "lead_agent",
            "runtime_ai": False,
            "tool_repository": "rpg-world-forge",
        },
    )
    _write_json(root / ".worldforge/status.json", initial_status(world_id))
    _write_json(
        root / ".worldforge/phases.json",
        {
            "format": "rpg-world-forge.phase_catalog",
            "format_version": 1,
            "phases": phase_catalog(),
        },
    )
    (root / ".worldforge/DECISIONS.md").write_text(
        "# Decisions\n\nRecord accepted, superseded and rejected decisions with affected IDs.\n",
        encoding="utf-8",
    )
    (root / ".worldforge/TASKS.md").write_text(
        "# Tasks\n\n"
        "The lead GPT maintains ordered backlog, active work, blockers and completed work.\n",
        encoding="utf-8",
    )
    (root / ".worldforge/HANDOFF.md").write_text(
        "# Implementation handoff\n\nBuilt during P14 from validated canon, assets and tests.\n",
        encoding="utf-8",
    )
    (root / ".worldforge/claims/README.md").parent.mkdir(parents=True, exist_ok=True)
    (root / ".worldforge/claims/README.md").write_text(
        "# Task claims\n\n"
        "One JSON claim per active delegated task. Canonical paths cannot overlap.\n",
        encoding="utf-8",
    )
    (root / ".worldforge/phase_reports/README.md").parent.mkdir(parents=True, exist_ok=True)
    (root / ".worldforge/phase_reports/README.md").write_text(
        "# Phase reports\n\nReports accepted by `worldforge complete-phase` are stored here.\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        f"# Agents for {title.strip()}\n\n"
        "GPT is the principal agent and owns all phase integration. It may work "
        "alone or delegate bounded tasks, but only the lead can promote content "
        "to canon or complete a phase.\n\n"
        "## Session order\n\n"
        "1. Read this file and `.worldforge/project.json`.\n"
        "2. Read `.worldforge/status.json`, decisions, tasks and active claims.\n"
        "3. Work only on the current phase and declare owned outputs.\n"
        "4. Keep proposals separate from canon and record dependencies.\n"
        "5. Validate deliverables and submit a phase report before advancing.\n\n"
        "## Boundaries\n\n"
        "- This repository is the game result, not the forge tool.\n"
        "- AI is permitted only during offline authoring and asset production.\n"
        "- Runtime receives compiled data and processed assets, never models, "
        "prompts, provider SDKs, credentials or inference calls.\n"
        "- Do not invent unresolved canon when evidence or a user decision is required.\n"
        "- Characters may use only facts allowed by their knowledge boundaries.\n"
        "- Every narrative choice must map to implementable state, events and effects.\n"
        "- Every generated asset requires specification, provenance, license and QA.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        f"# {title.strip()}\n\n"
        "Game project created with RPG World Forge. Editable sources live in "
        "`source/`. The world content language is declared in `world.json`; "
        "tooling documentation remains in English.\n\n"
        "## Workflow\n\n"
        "```bash\n"
        "worldforge phase-status .\n"
        "worldforge validate source/manifest.json --profile draft\n"
        f"worldforge compile source/manifest.json --output build/{world_id}.worldpack.json\n"
        f"worldforge init-assets build/{world_id}.worldpack.json --output assets/manifest.json\n"
        f"isoworld --pack build/{world_id}.worldpack.json\n"
        "```\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text(
        ".venv/\n__pycache__/\n*.py[cod]\nbuild/\nassets/generated/\n",
        encoding="utf-8",
    )
    return source / "manifest.json"
