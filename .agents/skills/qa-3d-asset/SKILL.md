---
name: qa-3d-asset
description: Run read-only contract, lineage, license, and neutral-GLB QA for one processed 3D asset. Use for M5 evidence and approval decisions only, not Blender fixes, generation, processing, or packaging.
---

# QA a 3D asset

## Scope

Evaluate one processed 3D asset independently of its production executor.

## Inputs

- Explicit `FORGE_ROOT` and read-only external `WORLD_ROOT` asset evidence.
- Exact world, target, bibles, inventory, specification, selected-candidate,
  production/processing receipt, license, and output hashes.
- Neutral GLB plus required automated checks, representative captures, and
  authorized reviewer identity.

## Outputs

- On success, one strict release asset-QA report with exact asset/target identity,
  passed checks, hash-bound evidence, empty blockers, approver, and content hash.
- On failure, correction evidence routed to model, rig, animate, refine/export,
  processing, or license; do not emit an approved release report.

## Invariants

- Parse and inspect GLB outside Blender/Modly/MCP; require zero external URIs.
- Verify coordinates, dimensions, transforms, nodes, geometry/material/texture/
  LOD/collider budgets, skeleton/skin limits, and every animation contract.
- Treat skipped/unavailable evidence as missing, never passing.
- Remain read-only: do not repair files, rewrite receipts, or let an executor
  approve its own result.

## Completion

Emit the canonical report only when every automated/manual/neutral integration
check passes and no blocker remains. Otherwise record exact correction evidence
and return the asset to its owning bounded phase; do not build an assetpack here.
The later P13 release step must build the assetpack from a complete `production`
manifest, place it under `assets/release/`, hash-seal it, and validate the
resulting `release` manifest.
