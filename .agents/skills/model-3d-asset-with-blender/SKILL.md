---
name: model-3d-asset-with-blender
description: Model one specified 3D asset through bounded Blender MCP operations under GPT orchestration. Use for geometry, UVs, and material slots only, not rigging, animation, final export, or QA approval.
---

# Model a 3D asset with Blender

## Scope

Delegate one modeling operation to `blender_mcp` and keep it independently
reviewable from rigging, animation, refinement/export, and QA.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact 3D target, visual bible, specification, approved reference-set, and parent
  production receipt hashes.
- A strict production request with orchestrator `gpt`, route `openai`, executor
  `blender_mcp`, operation `model_from_reference`, one or more `reference`
  inputs that exactly match parent `concept_reference` outputs, and those
  receipt hashes in `parent_receipt_hashes`. If the specification
  requires separate topology, UV, material, or collision passes, follow with
  individual `retopology`, `uv_unwrap`, `material_bake`, or `collision`
  requests linked by parent receipt hashes.

## Outputs

- Authoring `.blend` candidate plus hash-addressed inspection renders/metrics.
- One typed successful Blender production receipt per operation with exact
  request/parent hashes, Blender/MCP/add-on identities,
  `telemetry_disabled: true`, hash-bound reviewed script, explicit approval
  mode, replayability, and outputs.

## Invariants

- Honor coordinates, dimensions, topology, triangle/vertex, UV, material,
  texture, node-name, LOD, and collider-preparation contracts.
- Keep one executable operation per request/receipt. Use only bounded,
  hash-bound reviewed scripts with explicit approval; reject arbitrary response
  code, credentials, and filesystem escape.
- `.blend` stays in authoring and is never copied to assetpack, bundle, or game.
- Do not add a rig, author animation, declare final GLB, or approve your own work.

## Completion

Complete when the model candidate and receipt validate, inspection evidence shows
the geometry contract is met, and blockers are recorded. Stop before rigging,
animation, final export, deterministic processing, or QA.
