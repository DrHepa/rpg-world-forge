# ADR-0004: Assets are produced after canon lock

- Status: accepted
- Date: 2026-07-19

## Decision

Sprite, spritesheet, tile, portrait, UI, VFX, music, and SFX production starts
from a validated, hashed worldpack. It may use GPT Image, Codex, local models,
procedural tools, or human work, always outside runtime and inside the generated
world-authoring repository. Processed results enter a separate game repository
only through an immutable released bundle.

Each asset retains its specification, provider/origin, model and version,
references, prompt or recipe, license, human review, and approved-file hash. The
game loads only processed files—never prompts, models, weights, credentials, or
inference clients.

## Consequences

- A canon change explicitly invalidates manifests bound to the old hash.
- Assets can be regenerated or replaced without changing game rules.
- Repository, model, weight, dataset, and final-file licenses are tracked
  separately.
- Art direction precedes mass generation to avoid incompatible style and scale.

## Later refinement

M5's engine-neutral 3D assetpack handoff and the separate M6 runtime boundary
extend this decision in
[ADR-0010](0010-m5-asset-production-and-m6-3d-runtime-boundary.md). This
historical decision remains unchanged for its original offline-production
scope.
