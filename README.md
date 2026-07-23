# RPG World Forge

[![CI](https://github.com/DrHepa/rpg-world-forge/actions/workflows/ci.yml/badge.svg)](https://github.com/DrHepa/rpg-world-forge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-lilac.svg)](LICENSE)

A toolkit for producing world-agnostic RPG content and a tested 2D/2.5D
isometric reference runtime in Python with `pyray`/raylib. The authoring
contracts also cover engine-neutral 3D asset handoff. Runtime data is
deterministic and works without an LLM, an AI API, or an Internet connection.

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

## Current systemic slice (M5 complete)

- Canon-locked asset targets for 2D, 2.5D, or 3D, with approved visual/audio
  bibles, deterministic inventory derivation, and per-asset specifications.
- Hash-bound production requests and sanitized receipts for OpenAI Image,
  Blender MCP, and capability-discovered Modly extensions; GPT remains the
  orchestrator and provider tooling stays outside Forge and runtime code.
- Deterministic PNG canonicalization, atlas/clipset assembly, PCM WAV
  processing, strict TTF/OTF/GLSL validation, and receipt re-verification.
- Provider-neutral GLB inspection and runtime-only `assetpack_v1` handoff for
  3D targets, plus a two-step build/hash-seal release lifecycle. The pyray
  reference runtime and current immutable game bundle remain explicitly
  2D/2.5D renderpack consumers.
- Thirteen bounded M5 skills covering OpenAI/Codex 2D/2.5D production,
  reference-led Blender modeling/rigging/animation/export, independent 3D QA,
  and fail-closed Modly operation/refinement.

The committed [`examples/m5-neutral`](examples/m5-neutral/) fixture is
**narrative-neutral**, local, procedural, and offline. Its schema-required
`openai` route value is a contract namespace; no provider, model, or network
call executes. The committed manifests are authoring-side `production`
evidence, not runtime packs. Readiness gates regenerate the fixture externally
and build/verify temporary renderpack and assetpack outputs, finalize copied
manifests, and validate their release profiles without committing those runtime
packs.

- Atomic v2 creation, inspection, cloning, explicit legacy upgrade, and
  optimistic-lock SemVer versioning for independent world repositories.
- Worldpack v5 with typed BCP47 localization, per-playable-actor campaigns,
  and pure runtime API/feature compatibility checks; v1-v4 remain loadable.
- Deterministic runtime-only bundles with complete hashes/licenses, portable
  paths, provider/authoring-metadata rejection, and stable SemVer releases.
- Atomic multi-world/multi-release game import under a runtime-neutral locked
  catalog, with rollback and verification of every existing release.
- Clean standalone game materialization with a locked `isoworld` snapshot,
  separate pyray platform lock, exact `raylib==6.0.1.0` baseline, independent
  verifier, native smoke, benchmark, deterministic package, and desktop CI.
- Canonical `game_data/shared.lock.json` for game-owned common presentation
  assets, including byte/media validation and a hash-bound notices contract.
- Thirty-seven Forge-only skills: one bounded world, asset-production, or game
  implementation phase per skill, with no all-in-one game-building skill.
- Enforced immutable seams: game extensions live under `src/game`; vendored
  runtime snapshots and imported bundles change only through dedicated Forge
  operations.

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
- Target-scoped asset manifest v3 with hash-bound specifications, complete
  receipt lineage, multi-file processed outputs, semantic bindings, and strict
  OpenAI/optional-Modly generation routes.
- Runtime-only hashed renderpacks compiled without prompts, model data,
  workflows, credentials, or production evidence.
- Pyray resource registry for textures, clipsets, fonts, shaders, SFX, and
  streamed music with deterministic cleanup.
- Texture-backed isometric tiles/actors, tick-based animation, depth layers,
  portraits, event audio, and zoom/pan camera with primitive fallbacks.
- A 15-phase workflow with GPT as lead agent and verifiable quality gates.
- Runtime audit that rejects AI SDK imports.
- Headless tests that do not open a window.

The included foundation pack is a **neutral technical vertical slice**. Real
worlds live in independent world-authoring repositories and can define
different actors, genres, rules, maps, languages, and campaigns without
changing the Forge. Independent game repositories import only immutable,
hash-locked releases.

## Forge Studio application service

Forge Studio v1 begins with a local, provider-free Python application service.
It preserves the separate Forge, world-authoring, bundle, and game repository
roots while giving a future desktop client one strict interface for durable
workspace registration, events, job state, and explicitly approved source
changesets. It does not run models, providers, Blender, Modly, watchers, or the
graphical game runtime. Its only executable jobs are offline receipt validation,
world-anchored assetpack verification, deterministic headless runtime ticks,
and deterministic replay verification.

Start the service with an explicit user-data directory outside every project:

```bash
worldforge-studio-service --data-dir /path/to/user-data/rpg-world-forge-studio
```

The process reads and writes one strict UTF-8 JSON object per line on standard
input/output. Public v1 request methods are `service.initialize`,
`workspace.register/list/get/overview`, `source.list/read`,
`world.validate/analyze`, `events.list`, `changeset.create/get/list`,
`changeset.diff/approve/reject/apply`, and `job.create/get/list/transition/cancel`.
Contract errors are returned as correlated `error` envelopes; a malformed line
does not terminate the stream.

`job.create` is a closed operation-specific contract that writes managed v2 job
records. Paths are portable and relative to the registered world
(`asset.receipt.validate` is relative to `world_root/assets`), and
`runtime.headless` accepts integer ticks from 0 through 1,000,000. Previously
stored v1 jobs remain readable, recoverable, listable, and cancelable, but are
never claimed or retried. A single durable FIFO scheduler runs eligible v2 jobs
in a fixed isolated worker; no job can select an executable, module, working
directory, root, argument vector, or environment. `job.cancel` records durable
intent, and a running managed job becomes canceled only after its process tree
has been terminated and reaped.

The read-only authoring methods expose only manifest-declared world source
documents. They return portable paths and hashes from bounded, pinned reads;
never absolute repository paths. Validation and narrative analysis reuse the
existing Forge domain logic entirely in memory and do not publish reports.
The Electron preload exposes these reads and the four offline jobs only as
named capabilities. Electron main owns protocol methods, operation names, and
request IDs; renderer input is limited to closed workspace IDs, portable
relative paths, bounded ticks, and job IDs.
The World cockpit groups those authorized sources, keeps drafts only in memory,
checks JSON syntax, and renders bounded neutral map previews on Canvas. A dirty,
syntax-valid draft can be explicitly staged as one exact base-hashed replacement
for human review; it is never autosaved or written directly. The cockpit opens
the returned immutable v2 text and JSON Pointer diff, requires separate approve
and apply confirmations, and refreshes verified sources only after apply
succeeds. Legacy v1 records remain readable with exact diff unavailable; the
desktop does not offer fresh v1 approval or apply. Assets and Game remain
labeled future work rather than simulated capabilities.

Changesets edit only UTF-8 files beneath a registered world's `source/`
directory. New v2 records retain the exact base and proposed snapshots in
content-addressed storage under the external data directory and bind their
ordered operation descriptors with `review_sha256`. Exact bounded text hunks,
with a strict-JSON Pointer supplement when applicable, are derived only from
those retained bytes. A v2 approve, reject, or apply must echo the reviewed
hash. Apply durably claims `applying` before any repository mutation, rechecks
every base hash and filesystem identity under the world lifecycle lock, and
uses a review-bound journal so an interrupted multi-file apply is completed or
rolled back without claiming cross-filesystem atomicity. Existing v1 records
remain actionable but report that exact base review bytes were not retained.

The public contracts are
[`forge-workspace`](schemas/forge-workspace.schema.json),
[`studio-protocol`](schemas/studio-protocol.schema.json),
[`studio-changeset`](schemas/studio-changeset.schema.json), and
[`studio-job`](schemas/studio-job.schema.json). The application boundary is
recorded in [ADR-0011](docs/decisions/0011-forge-studio-application-service.md),
with immutable review evidence and apply claiming specified by
[ADR-0015](docs/decisions/0015-studio-reviewable-changesets.md).

The desktop shell lives in [`apps/studio`](apps/studio). It is a sandboxed
Electron client that loads only `rwf-studio://app`, exposes named typed preload
operations, and supervises both the Python service and an optional exact Codex
0.144.6 app-server without a shell or `PATH` fallback. Codex is bound to one
registered workspace and can only stage or inspect changesets through the
three-tool Forge MCP boundary; approval and apply remain human-controlled.
Development requires explicit `RWF_STUDIO_DEV_PYTHON` and
`RWF_STUDIO_DEV_CODEX` executables. Native packaged runtimes and the broader
visual authoring tools remain separate release slices; see
[ADR-0012](docs/decisions/0012-forge-studio-desktop-shell.md) and
[ADR-0013](docs/decisions/0013-workspace-bound-codex-bridge.md).

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
  --language en \
  --version 0.1.0

worldforge world-status ../my-world
worldforge phase-status ../my-world
worldforge validate ../my-world/source/manifest.json --profile draft
```

`../my-world` is an independent **world-authoring repository**, not a game
repository. It includes `AGENTS.md`, 15 ordered phases, decision and task logs,
multi-agent claims, phase reports, narrative source directories, and an
asset-production workspace. GPT can run the entire process as lead agent;
specialist roles are optional.

World identity and lineage operations are explicit:

```bash
worldforge clone-world ../my-world ../another-world \
  --id another_world --title "Another World" --version 0.1.0
worldforge bump-world-version ../my-world \
  --expected-version 0.1.0 --part minor \
  --reason "Add a reviewed campaign" --approved-by lead-agent
```

After the canon is complete:

```bash
worldforge compile ../my-world/source/manifest.json \
  --output ../my-world/build/my_world.worldpack.json
worldforge analyze-narrative ../my-world/source/manifest.json \
  --output ../my-world/build/narrative-analysis.json \
  --fail-on warning
worldforge init-assets ../my-world/build/my_world.worldpack.json \
  --target-dimension 2_5d \
  --output ../my-world/assets/manifest.json

# Optional and fail-closed: add only after reviewing the installed Modly stack.
# Omit this flag to keep the local route and executor disabled.
worldforge init-assets ../my-world/build/my_world.worldpack.json \
  --target-dimension 3d --enable-modly \
  --output ../my-world/assets-3d/manifest.json

# After authoring and approving target-bound visual/audio bibles:
worldforge validate-asset-bibles \
  --target ../my-world/assets/target.json \
  --visual ../my-world/assets/bibles/visual.json \
  --audio ../my-world/assets/bibles/audio.json
worldforge derive-asset-inventory \
  ../my-world/build/my_world.worldpack.json \
  --target ../my-world/assets/target.json \
  --visual-bible ../my-world/assets/bibles/visual.json \
  --audio-bible ../my-world/assets/bibles/audio.json \
  --output ../my-world/assets/inventory/assets.json

# After every required asset has complete production, processing, license, and
# QA evidence, the builder validates the manifest's production/build profile:
worldforge build-renderpack ../my-world/assets/manifest.json \
  --worldpack ../my-world/build/my_world.worldpack.json \
  --output ../my-world/assets/release/renderpack.json

# Seal that exact deliverable into the manifest, then validate the release:
worldforge finalize-asset-release ../my-world/assets/manifest.json \
  --deliverable ../my-world/assets/release/renderpack.json \
  --worldpack ../my-world/build/my_world.worldpack.json \
  --expected-hash <production-manifest-content-hash>
worldforge validate-assets ../my-world/assets/manifest.json \
  --profile release \
  --worldpack ../my-world/build/my_world.worldpack.json

# Optional reference-runtime preview only; this is not the game handoff:
isoworld --pack ../my-world/build/my_world.worldpack.json \
  --renderpack ../my-world/assets/release/renderpack.json
```

For a 3D target, replace `build-renderpack` with `build-assetpack` and write
`../my-world/assets/release/assetpack.json`; verify it with
`worldforge verify-assetpack`, then use the same `finalize-asset-release` and
release-validation steps. The assetpack is an engine-neutral implementation
handoff. The current `isoworld` reference runtime, `export-bundle`, and generated
standalone game consume only the 2D/2.5D renderpack path; a 3D game must add and
validate its own runtime adapter before importing the assetpack. This milestone
boundary is defined by
[ADR-0010](docs/decisions/0010-m5-asset-production-and-m6-3d-runtime-boundary.md).
The closed assetpack contains no license or authoring metadata. Required runtime
licenses and notices travel beside it as separately verified immutable-handoff
material.

The current 2D/2.5D production handoff becomes an immutable runtime bundle.
Forge-side tooling verifies and copies that bundle into a separate game
repository. It never copies the world's `AGENTS.md`, `.worldforge/`, editable
`source/`, production manifests, prompts, candidates, or model/provider
metadata.

```bash
worldforge export-bundle \
  ../my-world/build/my_world.worldpack.json \
  ../my-world/assets/release/renderpack.json \
  ../releases/my_world-1.0.0 \
  --release-id 1.0.0 \
  --licenses ../my-world/assets/release/licenses

worldforge verify-bundle ../releases/my_world-1.0.0 \
  --expected-hash <bundle-sha256>

worldforge new-game ../my-game \
  --id my_game --title "My Game" --source-revision <forge-commit>
worldforge import-bundle ../releases/my_world-1.0.0 ../my-game \
  --expected-hash <bundle-sha256>
worldforge audit-game ../my-game
```

The game is a normal standalone project. It contains no agent files or
authoring control plane and verifies/runs without `worldforge` installed.

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
PYTHONPATH=src python -m worldforge audit-contracts --source-root .
PYTHONPATH=src python -m worldforge audit-runtime src/isoworld
PYTHONPATH=src python -m worldforge audit-game /path/to/materialized-game
PYTHONPATH=src python -m scripts.verify_m5_release
```

The complete M5 release-readiness command requires a clean source tree and uses
only disposable directories outside the repository. It regenerates and closes
the narrative-neutral renderpack/assetpack paths, exercises offline standalone
bundle/replay/package behavior, builds reproducible wheel and sdist artifacts,
and audits clean isolated installs. Exact dependency versions are pinned, but
the requirement files do not provide hashes for every dependency and are not
described as fully hash-locked. GitHub Actions are pinned by full commit SHA;
hosted Ubuntu/Windows results exist only after the corresponding push.

## Repository layout

```text
src/isoworld/              reference runtime; never imports worldforge or AI SDKs
src/worldforge/            offline authoring, build, workflow, and QA tools
src/worldforge/templates/  clean standalone pyray/raylib game materialization
.agents/skills/            Forge-only, phase-scoped construction workflows
examples/                  foundation slice and narrative-neutral M5 evidence
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
the multiple-world lifecycle, bundle, catalog, and game scaffold are documented
in [M4_MULTIPLE_WORLD_PRODUCTION.md](docs/M4_MULTIPLE_WORLD_PRODUCTION.md).
The corresponding adversarial implementation review is recorded in
[AUDIT_M4_2026-07-19.md](docs/AUDIT_M4_2026-07-19.md).
The one-skill-per-phase game workflow is defined in
[GAME_IMPLEMENTATION_PHASES.md](docs/GAME_IMPLEMENTATION_PHASES.md), and
the supported game-runtime conventions are documented in
[PYRAY_RUNTIME_GUIDE.md](docs/PYRAY_RUNTIME_GUIDE.md).
Visual and audio production is described in
[ASSET_PIPELINE.md](docs/ASSET_PIPELINE.md), including the 3D/Blender and
capability-gated Modly routes. The M5/M6 boundary is fixed by
[ADR-0010](docs/decisions/0010-m5-asset-production-and-m6-3d-runtime-boundary.md),
and the local pre-push implementation/readiness evidence is recorded in
[AUDIT_M5_2026-07-21.md](docs/AUDIT_M5_2026-07-21.md). The GPT and multi-agent
protocol is documented in [agents/README.md](agents/README.md).

## Public project

Contributions must follow [AGENTS.md](AGENTS.md) and
[CONTRIBUTING.md](CONTRIBUTING.md). Contract changes must include their schema,
documentation, migration impact, and tests in the same pull request.

Repository documentation and tooling interfaces are maintained in English.
Each world declares its content locales. Each game target separately declares
the locales and UI policy it supports.
