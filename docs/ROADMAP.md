# Roadmap

## M0 - Foundation (current)

- Pyray runtime with a 2.5D isometric map.
- Fixed step, reducer, and immutable RenderState.
- Compiled worldpack and offline validation.
- Dynamic selection of worldpack-defined actors.
- Headless tests and runtime AI-import audit.
- GPT-led P00-P14 workflow and offline asset contracts.

## M1 - Systemic vertical slice

- Collisions, A* pathfinding, and cell reservations.
- Clock, schedules, and fallback actor routes.
- Contextual interaction and abilities with costs.
- Versioned persistence and action replay.
- Tiled/LDtk import into the internal format.

## M2 - Narrative core

- Facts, secrets, rumors, and knowledge boundaries.
- Directed relationships and faction reputation.
- Conditional dialogue graphs.
- Event-reactive quests and time-windowed scenes.
- Reachability and softlock analysis.

## M3 - Living world

- Construction affecting navigation, economy, and scenes.
- Actor routines, needs, and hierarchical goals.
- Resources, production, and scarcity.
- Delayed consequences and multi-stage arcs.

## M4 - Multiple world production

- Create, clone, and version independent world repositories.
- Allow each world to define roster, genre, rules, and localization.
- Package personal campaigns per playable actor.
- Validate worldpack compatibility without engine coupling.

## M5 - Asset production

- Derive inventories from canon-locked worldpacks.
- Manage art/audio bibles and per-asset specifications.
- Offline adapters for GPT Image, Codex, and local models.
- Deterministic spritesheet, tileset, atlas, SFX, and music processing.
- In-engine QA, provenance, licenses, and release hashes.
