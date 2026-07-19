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

## Current foundation

- Fixed-step game loop rendered through pyray.
- Isometric projection and semantic terrain (ground, water, and rock).
- Reactive state updates through actions and a deterministic reducer.
- Strict `WorldState -> immutable RenderState -> Renderer` separation.
- World-defined playable and non-playable actors; no hardcoded roster.
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
- `Tab`: select the next playable actor defined by the worldpack.
- `Esc`: exit.

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
Visual and audio production is described in
[ASSET_PIPELINE.md](docs/ASSET_PIPELINE.md). The GPT and multi-agent protocol is
documented in [agents/README.md](agents/README.md).

## Public project

Contributions must follow [AGENTS.md](AGENTS.md) and
[CONTRIBUTING.md](CONTRIBUTING.md). Contract changes must include their schema,
documentation, migration impact, and tests in the same pull request.

Repository documentation and tooling interfaces are maintained in English.
Each generated game declares its own content and localization languages.
