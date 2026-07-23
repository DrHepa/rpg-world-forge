# ADR-0010: M5 ends at an engine-neutral assetpack

- Status: accepted
- Date: 2026-07-21

## Context

ADR-0004 established canon-locked offline asset production, ADR-0007 separated
authoring evidence from runtime renderpacks, and ADR-0009 separated Forge,
world-authoring, immutable bundle, and game repositories. M5 extends those
principles to complete 2D/2.5D production and to a strict 3D handoff without
pretending that the current pyray reference runtime is a 3D engine.

Conflating 3D production with 3D game implementation would either make Forge
contracts depend on an engine selected too early or let authoring manifests,
Blender/Modly state, receipts, and provider metadata leak into a game. It would
also turn successful GLB validation into a false claim that rendering,
animation, collision, persistence, performance, and packaging work in a real
3D runtime.

## Decision

### M5 ownership

M5 owns deterministic asset production from a canon-locked worldpack through a
verified and finalized release manifest. For a 3D target, the released
runtime artifact is a hash-sealed, engine-neutral `assetpack_v1`. The assetpack
itself contains only its closed runtime schema fields: target/world identity,
coordinates, approved runtime file records, hashes, metrics, semantic bindings,
and its content hash. It explicitly contains no license/notices fields or files
and no authoring metadata. Required runtime licenses and notices travel beside
the assetpack as separately verified material in the immutable handoff or a
later M6 bundle.

Before release, the production manifest must bind approved bibles, inventory,
specifications, requests, exact receipt lineage, selected inputs, deterministic
processing, license evidence, and QA. The assetpack builder and independent
verifier enforce the declared coordinate system, units, structural and memory
budgets, nodes, materials, rigs, animations, colliders, LODs, embedded-resource
policy, and zero-external-URI rule. `finalize-asset-release` binds the exact
verified deliverable before release-profile validation.

Authoring manifests and their requests, receipts, sources, candidates, recipes,
provider/model identities, MCP configuration, `.blend` files, and weights do
not become runtime packs and do not cross the handoff boundary.

### M6 ownership

M6 begins with one selected, sealed M5 `assetpack_v1` and its exact hash. It
owns selection and pinning of the 3D engine/runtime, renderer, physics,
animation, import adapter, coordinate/semantic mapping, platform dependencies,
performance budgets, native smoke scenes, packaging, and the immutable 3D
bundle/import contract. M6 must integrate deterministic world systems, saves,
replay, and catalog behavior without reopening M5 production evidence.

The current `isoworld` reference runtime, M4 immutable runtime bundle,
`export-bundle`, generated pyray game, and its package format remain 2D/2.5D
`isoworld.renderpack` consumers. A valid M5 assetpack does not make any of them
3D-runtime capable.

### Readiness evidence

The committed `examples/m5-neutral/` tree is narrative-neutral local,
procedural, offline authoring evidence. Its `openai` route value is a
schema-required contract namespace; no provider, model, or network call runs.
Its manifests remain in `production` and no built runtime pack is committed.
Automated gates regenerate the tree outside the repository, build and verify a
temporary assetpack, finalize a copied manifest, and validate the release
profile. This proves the M5 contract without selecting an M6 runtime.

The Forge Studio Game cockpit may invoke the existing offline
`assetpack.verify`, reference `runtime.headless`, and `runtime.replay` jobs and
present their structured evidence. That cockpit is verification UX over the M5
handoff and current 2D/2.5D reference runtime, not an engine adapter, native
playtest, generated-game replay recorder, or claim that M6 has begun.

## Consequences

- The Forge can validate portable 3D production independently of any engine.
- A game team can evaluate engines against the same sealed assetpack rather
  than regenerating assets per candidate runtime.
- 2D/2.5D renderpack and 3D assetpack handoffs remain explicit and cannot be
  substituted for each other.
- M6 must record adapter-specific conversions and compatibility evidence rather
  than mutating or silently reinterpreting the M5 assetpack.
- Provider or authoring compromise cannot become a runtime dependency merely
  because the resulting file is GLB.

## Rejected alternatives

### Make the current pyray game load assetpacks in M5

Rejected because file parsing alone would not define or prove the engine,
renderer, animation, collision, performance, platform, save/replay, and package
contracts required for a supported 3D game.

### Choose an engine inside the Forge assetpack contract

Rejected because the M5 artifact must remain portable and world-production
oriented. Engine-specific import and runtime policy belongs to a separate game
repository and milestone.

### Ship authoring manifests or `.blend` files as the handoff

Rejected because they expose mutable production state and are not finite,
runtime-safe, independently verifiable deliverables.

### Treat a route namespace as proof that a provider executed

Rejected because a route is contract metadata. Execution requires explicit
capability, provenance, and receipt evidence; the narrative-neutral fixture is
intentionally local, procedural, and offline.
