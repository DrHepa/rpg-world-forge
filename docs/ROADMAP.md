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

## M5 - Asset production (complete)

- [x] Derive target-specific inventories from canon-locked worldpacks and bind
  approved visual/audio bibles plus per-asset specifications by hash.
- [x] Complete the OpenAI/Codex 2D/2.5D authoring route with immutable requests,
  sanitized receipts, explicit candidate selection, and no provider SDK in the
  Forge or runtime.
- [x] Canonicalize PNGs, build deterministic atlases/clipsets, and process PCM
  WAV audio through finite recipe v1 contracts with independently verifiable
  receipt v2 records bound to the exact asset-root-relative recipe; preserve
  identity-only receipt v1 read compatibility.
- [x] Extend the provider-neutral contract to 3D targets, strict GLB inspection,
  structural budgets, semantic bindings, and runtime-only `assetpack_v1`.
- [x] Enforce two-step publication: validate a complete `production` manifest
  while building under `assets/release/`, then hash-seal the exact deliverable
  and validate the resulting `release` manifest.
- [x] Add reference-image-first Blender MCP workflows for modeling, rigging,
  animation, refinement/export, and independent QA under GPT orchestration.
- [x] Add the optional local route through `modly-cli-mcp`, gated by live
  capability discovery, pinned extension/model/weights evidence, reviewed setup,
  and Blender refinement that preserves the Modly parent lineage.
- [x] Reject bibles, requests, receipts, MCP packages, provider data, authoring
  formats, `.blend` files, weights, and workflows at runtime/game boundaries.

The Modly route deliberately rejects an operation when the installed package
and extension cannot report a supported, versioned capability. That is an
availability result, not permission to call a model or infer a workflow
directly. The researched `modly-cli-mcp@0.1.1` baseline executes only TripoSG or
Hunyuan3D image-to-mesh, mesh optimization, and default-path mesh export through
`capability_execute`; UniRig, 2D/2.5D, unknown/UI capabilities, and generic
chains remain unavailable or require a separately discovered future extension.

The current pyray reference runtime and M4 immutable game bundle remain
2D/2.5D renderpack consumers. M5's 3D result is an engine-neutral assetpack for
a later game implementation/runtime-adapter phase, not a claim that the
reference game already loads 3D assets.

M5 closure is recorded in the
[versioned readiness evidence](AUDIT_M5_2026-07-21.md) and
[ADR-0010](decisions/0010-m5-asset-production-and-m6-3d-runtime-boundary.md).
The committed [`examples/m5-neutral`](../examples/m5-neutral/) tree is
narrative-neutral, local, procedural, and offline production evidence. Its
authoring manifests remain in `production`; disposable gates build and
independently validate runtime packs outside the repository, then finalize
copied manifests and validate their release profiles. The schema-required
`openai` route value is a contract namespace, not evidence that a provider,
model, or network call executed.

## M6 - 3D game implementation and runtime adapters (planned)

M6 starts from a sealed M5 `assetpack_v1`; it does not reopen asset production.
The milestone will:

- [x] Define hash-bound capability, presentation-profile, adapter,
  composition, and compatibility-report contracts without selecting an engine
  or changing M5 packs.
- [x] Define and implement an immutable composed-runtime bundle that preserves
  exact M5 bytes, recomputes compatibility evidence, resolves only a static
  adapter registry, and makes no adapter-execution or release-readiness claim.
- [x] Prove a bounded pyray GLB load/animation/draw lifecycle on Linux x86_64
  with deterministic render-state planning and separate Windows ABI-only
  evidence. The adapter proves `animation_gltf` only and remains incompatible
  with every current 3D profile because collision is not implemented.
- Select and pin at least one explicit 3D game runtime/engine contract outside
  the Forge, including platform, renderer, physics, animation, packaging, and
  performance budgets.
- Map provider-neutral assetpack coordinates, semantic bindings, node names,
  materials, rigs, animation clips, colliders, and LODs into that adapter with
  deterministic validation and compatibility reporting.
- Integrate the existing worldpack systems, narrative, living-world state,
  saves, replay, and multi-world catalog without introducing Blender, Modly,
  MCP, provider SDKs, authoring evidence, or weights into the game repository.
- Add a standalone 3D game scaffold, native smoke scenes, representative
  benchmarks, package verification, and desktop CI while preserving the
  supported 2D/2.5D pyray path unchanged.
- Define the immutable 3D bundle/import boundary only after the adapter proves
  end-to-end load, animation, collision, save/replay, and release verification.

Entry gate for a selected game: its M5 release validation is green and the
exact sealed assetpack hash is recorded. The repository's temporary readiness
assetpack proves that gate can be enforced; it is not a committed game asset or
an engine choice. Exit gate: a separate game repository imports a verified 3D
bundle, runs without Forge or network access, and passes boundary, native-smoke,
determinism, performance, and packaging checks.

The completed contract, bundle, and bounded animation proof are not M6 runtime
readiness. The neutral contract adapter remains deliberately declared and its
report incompatible. The separate verified `pyray_3d_v1` declaration omits
collision, assetpack consumption, world presentation, packaging, and
performance capabilities, so it cannot satisfy a 3D composition. Game
integration, collision, representative performance, packaging, and release
gates remain open.
