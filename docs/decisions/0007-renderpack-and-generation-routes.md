# ADR-0007: Runtime renderpacks and explicit generation routes

- Status: accepted
- Date: 2026-07-19

## Decision

Asset production and asset consumption use different artifacts. The version 2
asset-production manifest records specifications, candidates, recipes,
provenance, licenses, approval, processed outputs, and semantic bindings.
`worldforge build-renderpack` validates that manifest against the exact
worldpack, copies only approved processed outputs, and emits a hashed
`isoworld.renderpack` for pyray.

Assisted production has two explicit authoring-time routes:

- `openai`: GPT, Codex, and GPT Image work outside the game.
- `modly`: every local model runs through a named, versioned Modly extension
  and recorded workflow file.

Direct local-model execution outside Modly is rejected. A project may disable
either route, but `local_model_route` remains `modly`. Neither route contributes
provider clients, models, prompts, weights, credentials, or workflows to the
renderpack or runtime.

## Consequences

- Art can be regenerated or replaced without changing simulation semantics.
- Production evidence remains reviewable but is not shipped to players.
- Runtime inputs are finite, hash-bound, path-contained, and provider-neutral.
- Clip timing uses integer simulation ticks and is deterministic across frame
  rates.
- Pyray owns only resource loading, presentation, audio playback, and cleanup.

## Later refinement

M5 manifest v3 and the engine-neutral 3D assetpack handoff extend this
renderpack decision in
[ADR-0010](0010-m5-asset-production-and-m6-3d-runtime-boundary.md). The current
pyray runtime and immutable game bundle remain renderpack-only.

Processing recipes remain v1. Processing receipt v2 closes a previous
authorization gap by naming the exact recipe beneath an explicit asset root and
binding its raw and canonical hashes plus operation/input/output lineage.
Receipt v1 stays readable as identity-only history; validators never scan for
or infer a recipe path.
