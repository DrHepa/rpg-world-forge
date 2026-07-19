# RPG World Forge

[![CI](https://github.com/DrHepa/rpg-world-forge/actions/workflows/ci.yml/badge.svg)](https://github.com/DrHepa/rpg-world-forge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-lilac.svg)](LICENSE)

A toolkit and tested reference runtime for creating 2D/2.5D isometric RPGs in
Python with `pyray`/raylib. The runtime is deterministic, data-driven, and
works without an LLM, an AI API, or an Internet connection.

This repository contains **the Forge**, not a particular world or game. A world
authoring repository owns canon, characters, editable content, asset production,
and release evidence. A separate game repository owns executable code, UX,
imported immutable bundles, platform CI, and releases. No game-specific lore is
hardcoded here.

AI can assist **outside the game** while designing lore, actors, cultures,
quests, dialogue, arcs, scenes, maps, schedules, abilities, motivations, and
knowledge boundaries. Its output is never accepted directly: a human or the
authorized lead agent reviews it, `worldforge` validates it, and the build step
compiles it into a static worldpack consumed by the runtime.

## Current systemic slice (M3)

- Fixed-step game loop rendered through pyray.
- Isometric projection and semantic terrain (ground, water, and rock).
- Reactive state updates through actions and a deterministic reducer.
- Strict `WorldState -> immutable RenderState -> Renderer` separation.
- World-defined playable and non-playable actors; no hardcoded roster.
- Deterministic A* navigation, occupied-cell collision, and route reservations.
- Configurable world clock and schedules with ordered fallback destinations.
- Contextual interactions with flag/resource effects.
- Abilities with explicit resource costs, range, cooldowns, and effects.
- Versioned, worldpack-bound saves and deterministic action replays.
- Finite Tiled JSON and embedded LDtk JSON map import.
- Typed facts, rumors, secrets, and enforced per-actor knowledge boundaries.
- Directed relationship dimensions and per-actor faction reputation.
- Conditional dialogue graphs with fact-gated speakers and choices.
- Event-reactive quest stages and prioritized time-windowed scenes.
- Construction blueprints with footprints, costs, build time, dynamic collision,
  render bindings, and narrative events.
- Typed resources, located stockpiles, bounded capacity, production recipes,
  delayed outputs, and derived scarcity.
- Per-actor needs with deterministic decay and hierarchical, prioritized goals
  that can travel, consume, build, or produce.
- Persisted delayed consequences that react to domain events and form bounded,
  authored multi-stage arcs.
- Offline narrative reachability, producer, and softlock analysis.
- Offline content compiler and cross-reference validator.
- Offline asset pipeline with provenance and license tracking.
- Asset manifest v2 with typed specifications, multi-file processed outputs,
  semantic bindings, and strict OpenAI/optional-Modly generation routes.
- Runtime-only hashed renderpacks compiled without prompts, model data,
  workflows, credentials, or production evidence.
- Pyray resource registry for textures, clipsets, fonts, shaders, SFX, and
  streamed music with deterministic cleanup.
- Texture-backed isometric tiles/actors, tick-based animation, depth layers,
  portraits, event audio, and zoom/pan camera with primitive fallbacks.
- A 15-phase workflow with GPT as lead agent and verifiable quality gates.
- Runtime audit that rejects AI SDK imports.
- Headless tests that do not open a window.

The included pack is a **neutral technical vertical slice**. Real worlds live
in independent world-authoring repositories and can define different actors,
genres, rules, maps, languages, and campaigns without changing the Forge.
Independent game repositories import only immutable, hash-locked releases.

## Quick start

```bash
python -m venv .venv
python -m pip install -e ".[game]"
worldforge validate examples/foundation/source/manifest.json --profile release
worldforge compile examples/foundation/source/manifest.json \
  --output content/compiled/foundation.worldpack.json
isoworld --pack content/compiled/foundation.worldpack.json
```

Without installing the package:

```bash
PYTHONPATH=src python -m worldforge validate \
  examples/foundation/source/manifest.json --profile release
PYTHONPATH=src python -m isoworld \
  --pack content/compiled/foundation.worldpack.json
```

Vertical-slice controls:

- Arrow keys or WASD: move one cell.
- Left click: plan an A* route to a cell.
- `Tab`: select the next playable actor defined by the worldpack.
- `E`: use the nearest available contextual interaction.
- `1`: use the active actor's first ability.
- `Q`: start the nearest eligible dialogue; number keys select a choice.
- `Esc`: leave a dialogue when its current node allows it.
- `Space`: dismiss an active scene.
- `F5`/`F9`: write/load the path supplied with `--save`.
- `Esc`: exit.

Save a run and record a replay:

```bash
isoworld --pack content/compiled/foundation.worldpack.json \
  --save saves/quick.json \
  --save-on-exit saves/latest.json \
  --record-replay saves/latest.replay.json

isoworld --pack content/compiled/foundation.worldpack.json \
  --replay saves/latest.replay.json
```

Saves and replays are tied to the exact world content hash. A changed worldpack
is rejected instead of silently loading incompatible state.

M3 exposes `build`, `start_production`, and `transfer_resource` as typed
`GameAction` values. A derived game supplies its own build/economy UI and
dispatches those actions; the forge runtime deliberately does not impose a
genre-specific construction menu. Completed or in-progress constructions are
visible in the pyray renderer, with `construction:<blueprint_id>` renderpack
bindings and primitive fallbacks.

## Create an independent world

The scaffold asks for only the decisions required to create a safe draft. It
does not invent lore or characters:

```bash
worldforge new-world ../my-world \
  --id my_world \
  --title "My World" \
  --language en

worldforge phase-status ../my-world
worldforge validate ../my-world/source/manifest.json --profile draft
```

`../my-world` is an independent **world-authoring repository**, not a game
repository. It includes `AGENTS.md`, 15 ordered phases, decision and task logs,
multi-agent claims, phase reports, narrative source directories, and an
asset-production workspace. GPT can run the entire process as lead agent;
specialist roles are optional.

After the canon is complete:

```bash
worldforge compile ../my-world/source/manifest.json \
  --output ../my-world/build/my_world.worldpack.json
worldforge analyze-narrative ../my-world/source/manifest.json \
  --output ../my-world/build/narrative-analysis.json \
  --fail-on warning
worldforge init-assets ../my-world/build/my_world.worldpack.json \
  --output ../my-world/assets/manifest.json

# After approved production and release validation:
worldforge build-renderpack ../my-world/assets/manifest.json \
  --worldpack ../my-world/build/my_world.worldpack.json \
  --output ../my-world/build/runtime/renderpack.json

# Optional reference-runtime preview only; this is not the game handoff:
isoworld --pack ../my-world/build/my_world.worldpack.json \
  --renderpack ../my-world/build/runtime/renderpack.json
```

The production handoff is an immutable runtime bundle. Forge-side tooling
verifies and copies that bundle into a separate game repository. It never
copies the world's `AGENTS.md`, `.worldforge/`, editable `source/`, production
manifests, prompts, candidates, or model/provider metadata.

## Import a map

The M1 importer supports finite, uncompressed Tiled JSON tile layers and
embedded LDtk IntGrid/tile layers. Create a reviewed numeric-to-semantic tile
mapping first:

```json
{
  "0": "ground",
  "1": "ground",
  "2": "rock",
  "3": "water"
}
```

```bash
worldforge import-map references/garden.json \
  --format auto \
  --id garden \
  --display-name "Garden" \
  --mapping references/garden.tiles.json \
  --layer Ground \
  --output source/maps/garden.json
```

The output records source and mapping hashes. Imported terrain remains source
data and must pass `worldforge validate`; manual overrides take precedence.

## Verification

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m worldforge audit-runtime src/isoworld
PYTHONPATH=src python -m worldforge audit-game /path/to/materialized-game
```

## Repository layout

```text
src/isoworld/              reference runtime; never imports worldforge or AI SDKs
src/worldforge/            offline authoring, build, workflow, and QA tools
examples/                  neutral vertical slice
content/compiled/          generated worldpacks used by the example runtime
authoring/prompts/         provider-agnostic authoring prompts
agents/                    orchestration, phases, roles, and quality gates
schemas/                   public source, workflow, and asset contracts
docs/                      architecture, narrative model, ADRs, and roadmap
tests/                     headless tests and architectural boundaries
```

Read [ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[CONTENT_PIPELINE.md](docs/CONTENT_PIPELINE.md) before adding systems or canon.
M1 runtime contracts and limits are documented in
[M1_SYSTEMS.md](docs/M1_SYSTEMS.md), and the implementation audit is recorded in
[AUDIT_2026-07-19.md](docs/AUDIT_2026-07-19.md).
M2 narrative contracts and limits are documented in
[M2_NARRATIVE.md](docs/M2_NARRATIVE.md), with its security and implementation
review in [AUDIT_M2_2026-07-19.md](docs/AUDIT_M2_2026-07-19.md).
M2.5 presentation and renderpack contracts are documented in
[M2_5_PRESENTATION.md](docs/M2_5_PRESENTATION.md), with the implementation audit
in [AUDIT_M2_5_2026-07-19.md](docs/AUDIT_M2_5_2026-07-19.md).
The M4 repository boundary is defined by
[ADR-0009](docs/decisions/0009-independent-world-and-game-repositories.md), and
the supported game-runtime conventions are documented in
[PYRAY_RUNTIME_GUIDE.md](docs/PYRAY_RUNTIME_GUIDE.md).
Visual and audio production is described in
[ASSET_PIPELINE.md](docs/ASSET_PIPELINE.md). The GPT and multi-agent protocol is
documented in [agents/README.md](agents/README.md).

## Public project

Contributions must follow [AGENTS.md](AGENTS.md) and
[CONTRIBUTING.md](CONTRIBUTING.md). Contract changes must include their schema,
documentation, migration impact, and tests in the same pull request.

Repository documentation and tooling interfaces are maintained in English.
Each world declares its content locales. Each game target separately declares
the locales and UI policy it supports.
