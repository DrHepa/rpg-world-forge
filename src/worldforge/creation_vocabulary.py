from __future__ import annotations

import re

from worldforge.generated_creation_content_modes import (
    CREATION_CONTENT_MODES as _GENERATED_CREATION_CONTENT_MODES,
)

CREATION_PROJECT_KINDS = ("game", "asset_library", "universe_library")
GAMEPLAY_FAMILIES = (
    "action",
    "adventure",
    "educational",
    "narrative",
    "puzzle",
    "rhythm",
    "role_playing",
    "sandbox",
    "simulation",
    "sports",
    "strategy",
)
WORLD_PRESENCES = ("none", "abstract", "symbolic", "diegetic")
NARRATIVE_REQUIREMENTS = ("none", "optional", "required")
NARRATIVE_AUTHORSHIP_MODES = (
    "none",
    "authored",
    "emergent",
    "procedural",
    "player_authored",
    "social",
    "hybrid",
)
NARRATIVE_TOPOLOGIES = (
    "none",
    "linear",
    "foldback",
    "branching",
    "branch_and_bottleneck",
    "hub_and_spoke",
    "modular",
    "storylet",
    "loop_reset",
    "episodic",
    "seasonal",
    "open_ended",
)
PRESENTATION_MODES = ("text", "2d", "2_5d", "3d", "mixed", "vr", "ar")
RUNTIME_SUPPORT_INTENTS = ("authoring_only", "compatibility_assessment")
CREATION_CONTENT_MODES = _GENERATED_CREATION_CONTENT_MODES
CREATION_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"
_CREATION_IDENTIFIER_RE = re.compile(CREATION_IDENTIFIER_PATTERN)


def is_creation_identifier(value: object) -> bool:
    """Return whether *value* is the closed portable initial-scaffold identifier."""

    return isinstance(value, str) and _CREATION_IDENTIFIER_RE.fullmatch(value) is not None
