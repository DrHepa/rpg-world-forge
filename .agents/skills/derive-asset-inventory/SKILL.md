---
name: derive-asset-inventory
description: Derive a complete target-scoped M5 asset inventory from locked canon and approved bibles. Use for requirements and semantic slots only, not specifications, generation, or processing.
---

# Derive an asset inventory

## Scope

Translate locked world needs into one auditable inventory for one asset target.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Locked worldpack, target, and approved visual/audio bibles with exact hashes.
- Canonical actors, biomes, objects, actions, events, construction, UI, and audio
  event catalogs.

## Outputs

- One strict asset-inventory document bound to the world, target, and bible hashes.
- Requirements with ID, kind, representation, required flag, purpose, canonical
  sources, and semantic slots.
- Separately identified manual additions with review rationale.

## Invariants

- Preserve target dimension and delivery compatibility for mixed representations.
- Never turn an unsupported creative guess into a canon-derived requirement.
- Keep duplicate concepts, unresolved slots, and missing canonical sources as
  blockers rather than silently merging or inventing them.
- Do not choose an executor, provider, prompt, extension, or production route.

## Completion

Complete when every applicable world/runtime need is covered exactly once or
explicitly excluded, semantic slots are stable, manual additions are separated,
and the inventory validates. Do not write specifications or generate assets.
