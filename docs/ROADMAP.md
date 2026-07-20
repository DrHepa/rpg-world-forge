# Roadmap

## M0 - Foundation (complete)

- Pyray runtime with a 2.5D isometric map.
- Fixed step, reducer, and immutable RenderState.
- Compiled worldpack and offline validation.
- Dynamic selection of worldpack-defined actors.
- Headless tests and runtime AI-import audit.
- GPT-led P00-P14 workflow and offline asset contracts.

## M1 - Systemic vertical slice (complete)

- [x] Collisions, deterministic A* pathfinding, and cell reservations.
- [x] Clock, schedules, and ordered fallback actor routes.
- [x] Contextual interaction and abilities with resource costs and cooldowns.
- [x] Versioned persistence and deterministic action replay.
- [x] Finite Tiled JSON and embedded LDtk JSON import into the internal format.

## M2 - Narrative core (complete)

- [x] Facts, secrets, rumors, and knowledge boundaries.
- [x] Directed relationships and faction reputation.
- [x] Conditional dialogue graphs.
- [x] Event-reactive quests and time-windowed scenes.
- [x] Reachability and softlock analysis.

## M2.5 - Presentation and asset runtime foundation (complete)

- [x] Asset-production manifest v2 with typed specifications and multi-file outputs.
- [x] Canon-integrity verification before asset planning and release.
- [x] Runtime-only renderpack compiler with semantic bindings and copied processed files.
- [x] Deterministic clipsets, pivots, animation frames, scale, and depth layers.
- [x] Pyray texture/font/shader/audio lifecycle and domain-event SFX/music bridge.
- [x] Camera zoom/pan, depth-sorted isometric entities, portraits, and primitive fallbacks.
- [x] OpenAI authoring route and optional Modly-only route for local models.
- [x] Headless integration tests and a real hidden-window raylib CI smoke test.

## M3 - Living world (complete)

- [x] Construction affecting navigation, economy, render state, and scenes.
- [x] Actor routines, needs, and hierarchical goals.
- [x] Resources, stockpiles, production, capacity, and scarcity.
- [x] Delayed consequences and multi-stage arcs.
- [x] Worldpack v4 and save/replay v3 with strict cross-reference validation.

## M4 - Multiple world production (complete)

- [x] Create, inspect, upgrade, clone, and stable-SemVer version independent
  world-authoring repositories.
- [x] Compile worldpack v5 with world-defined roster, genre, rules,
  localization, runtime requirements, and personal campaigns.
- [x] Export and verify immutable runtime bundles with canonical manifests,
  content hashes, contained paths, licenses, and compatibility declarations.
- [x] Import multiple world/release bundles atomically into a locked,
  runtime-neutral game catalog.
- [x] Materialize independent pyray/raylib game repositories with no
  `AGENTS.md`, skills, `.worldforge`, editable canon, production evidence, or
  Forge/AI dependency.
- [x] Vendor an immutable `src/isoworld/` snapshot, isolate game work under
  `src/game/`, and separate runtime migration from the locked pyray platform
  migration.
- [x] Provide 24 Forge-only, phase-scoped skills: four world/release operations
  and twenty standalone-game implementation phases.
- [x] Pin the first desktop baseline to CPython 3.11/3.12,
  `raylib==6.0.1.0` imported as `pyray`, native raylib 6.0, and a CI-consumed
  exact dependency/build/quality lock, with headless, native-smoke, benchmark, package,
  and platform-CI entry points.

## M5 - Asset production

- Derive inventories from canon-locked worldpacks.
- Manage art/audio bibles and per-asset specifications.
- Offline adapters for GPT Image/Codex and optional local generation through Modly extensions.
- Deterministic spritesheet, tileset, atlas, SFX, and music processing.
- In-engine QA, provenance, licenses, and release hashes.
