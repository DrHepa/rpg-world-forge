---
name: rig-3d-asset-with-blender
description: Rig one reviewed Blender model through bounded Blender MCP operations under GPT orchestration. Use for skeleton, skinning, and deformation checks only, not modeling redesign, animation, export, or QA approval.
---

# Rig a 3D asset with Blender

## Scope

Add the specification-defined rig and skinning to one reviewed model lineage.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact 3D specification and selected Blender-model artifact/receipt hashes.
- Required bone names/hierarchy, influence limits, rest pose, sockets, deformation
  tests, and root-motion policy.
- A strict `blender_mcp` production request with operation `rig`, a `model`
  input that exactly matches a compatible parent output, and at least one
  parent modeling/refinement receipt hash.

## Outputs

- Rigged authoring candidate and pose/deformation inspection captures.
- Typed Blender receipt retaining the model receipt in
  `parent_receipt_hashes` and recording exact tool identities and outputs.

## Invariants

- Preserve approved geometry, scale, axes, origin, UVs, materials, LODs, and node
  names unless a blocker returns the asset to modeling.
- Enforce bone, hierarchy, naming, skin, and maximum-influence budgets.
- Require a hash-bound reviewed script, explicit approval, disabled telemetry,
  and keep `.blend` authoring-only.
- Do not author clips, refine unrelated art, export the final GLB, or approve QA.

## Completion

Complete when every required bone/socket exists, skinning budgets pass, required
pose/deformation captures are recorded, and the receipt validates. Stop before
animation or export.
