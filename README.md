# RPG World Forge

[![CI](https://github.com/DrHepa/rpg-world-forge/actions/workflows/ci.yml/badge.svg)](https://github.com/DrHepa/rpg-world-forge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-lilac.svg)](LICENSE)

A toolkit and base runtime for creating 2D/2.5D isometric RPGs in Python with
`pyray`/raylib. The runtime is deterministic, data-driven, and works without an
LLM, an AI API, or an Internet connection.

This repository contains **the forge**, not a particular world or game. Every
result is created in its own directory/repository, where it owns its canon,
characters, assets, and builds. No game-specific lore is hardcoded here.

AI can assist **outside the game** while designing lore, actors, cultures,
quests, dialogue, arcs, scenes, maps, schedules, abilities, motivations, and
knowledge boundaries. Its output is never accepted directly: a human or the
authorized lead agent reviews it, `worldforge` validates it, and the build step
compiles it into a static worldpack consumed by the runtime.

## Current systemic slice (M1)

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
- Offline content compiler and cross-reference validator.
- Offline asset pipeline with provenance and license tracking.
- A 15-phase workflow with GPT as lead agent and verifiable quality gates.
- Runtime audit that rejects AI SDK imports.
- Headless tests that do not open a window.

The included pack is a **neutral technical vertical slice**. Real worlds live
in independent game repositories and can define different actors, genres,
rules, maps, languages, and campaigns without changing the forge.

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

`../my-world` is independent and can become its own Git repository. The
generated project includes `AGENTS.md`, 15 ordered phases, decision and task
logs, multi-agent claims, phase reports, narrative source directories, and an
asset-production workspace. GPT can run the entire process as lead agent;
specialist roles are optional.

After the canon is complete:

```bash
worldforge compile ../my-world/source/manifest.json \
  --output ../my-world/build/my_world.worldpack.json
worldforge init-assets ../my-world/build/my_world.worldpack.json \
  --output ../my-world/assets/manifest.json
```

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
```

## Repository layout

```text
src/isoworld/              game runtime; never imports worldforge or AI SDKs
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
Visual and audio production is described in
[ASSET_PIPELINE.md](docs/ASSET_PIPELINE.md). The GPT and multi-agent protocol is
documented in [agents/README.md](agents/README.md).

## Public project

Contributions must follow [AGENTS.md](AGENTS.md) and
[CONTRIBUTING.md](CONTRIBUTING.md). Contract changes must include their schema,
documentation, migration impact, and tests in the same pull request.

Repository documentation and tooling interfaces are maintained in English.
Each generated game declares its own content and localization languages.
