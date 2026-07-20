---
name: refine-export-3d-asset-with-blender
description: Apply bounded final refinements and export one selected 3D lineage from Blender to neutral GLB. Use after model, rig, and animation review, not for redesign, independent QA, or assetpack release.
---

# Refine and export a 3D asset with Blender

## Scope

Perform only authorized final corrections and produce the specification-bound
neutral GLB candidate.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact target/specification and selected model/rig/animation artifact lineage.
- A finite reviewed fix list, coordinate/export contract, permitted GLTF
  extensions, named nodes/actions, embedded-resource policy, and budgets.
- One `blender_mcp` request with operation `refine` for an approved fix list,
  followed when needed by a separate `export_glb` request whose parent is the
  refinement receipt. If there are no fixes, issue only `export_glb`. Both
  requests require a `model` input that exactly matches a compatible immediate
  parent output and at least one parent receipt hash.

## Outputs

- Updated authoring `.blend`, exported GLB candidate, metrics, and inspection
  captures in the world authoring area.
- One typed successful Blender receipt per operation, retaining direct parent
  hashes, exact Blender/MCP/add-on identities, disabled telemetry, hash-bound
  reviewed scripts, explicit approval, replayability, and outputs.

## Invariants

- Keep one executable operation per request/receipt. Change only the approved
  fix list; return redesign, retopology, rerigging, or
  reanimation beyond it to the owning skill.
- Preserve units, axes, origin, scale, names, materials, skins, clips, colliders,
  LODs, and budgets required by the specification.
- Export GLB with embedded resources and zero external URIs; `.blend` remains
  authoring-only and no provider/MCP configuration enters the GLB.
- Do not claim independent QA or build an assetpack.

## Completion

Complete when the GLB parses, preliminary coordinate/name/resource/metric checks
pass, every request/receipt validates, and all approved fixes are traceable. Stop
for `glb_validate`, independent 3D QA, and later release packaging.
