---
name: refine-modly-output
description: Refine one authorized Modly 3D candidate through bounded Blender MCP operations while preserving production lineage. Use for finite repairs and neutral GLB export only, not rerunning Modly, redesign, independent QA, or packaging.
---

# Refine a Modly output

## Scope

Use Blender MCP to make a finite approved repair/export pass on one selected
Modly 3D candidate without obscuring its local-production origin.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact target/specification, selected Modly candidate, and typed Modly receipt.
- Reviewed repair list covering only missing geometry, UV/material, topology,
  coordinate, rig, animation, collider, LOD, or export requirements.
- A strict request with orchestrator `gpt`, route `modly`, executor
  `blender_mcp`, operation `refine`, and the Modly receipt hash in
  `parent_receipt_hashes`; its `model` input must exactly match an output of
  that direct `modly_cli_mcp` parent. Follow only when needed with a separate
  `export_glb` request whose parent is the refinement receipt.

## Outputs

- Authoring `.blend`, refined neutral GLB candidate, metrics, and captures.
- Typed successful Blender receipt per operation, preserving the Modly lineage
  and exact Blender/MCP/add-on identities, disabled telemetry, hash-bound
  reviewed scripts, explicit approval, replayability, and outputs.

## Invariants

- Do not rerun, silently replace, or mislabel the Modly candidate as an OpenAI or
  human origin; retain all source/model/weight/license evidence.
- Make only the approved repairs and satisfy the same provider-neutral 3D spec.
- Require embedded GLB resources and zero external URIs; `.blend` remains
  authoring-only and no Modly/Blender configuration enters runtime.
- Reject arbitrary response code and do not self-approve QA.

## Completion

Complete when the repaired GLB and every request/receipt validate and each change
maps to the finite repair list. Stop for `glb_validate`, independent 3D QA, and
later assetpack release.
