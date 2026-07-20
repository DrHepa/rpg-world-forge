---
name: design-3d-asset-references
description: Produce a coherent reference-image set for one approved 3D asset specification through OpenAI Image. Use for 3D design evidence only, not modeling, rigging, animation, GLB export, or QA.
---

# Design 3D asset references

## Scope

Delegate one bounded reference-design operation to `openai_image` before 3D
geometry work begins.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact 3D target, visual bible, inventory, and asset-specification hashes.
- Authorized style/reference evidence and required orthographic/perspective views.
- A production request with orchestrator `gpt`, route `openai`, executor
  `openai_image`, and operation `concept_reference`.

## Outputs

- Hash-addressed reference candidates with consistent scale, proportions,
  materials, silhouettes, and view labels.
- One sanitized OpenAI production receipt binding inputs, outputs, exact model
  resolution policy, successful status, replayability, and parent receipt hashes.

## Invariants

- Reference images inform design; they are never runtime geometry or proof that a
  mesh satisfies dimensions, topology, rig, collider, or animation contracts.
- Make discrepancies among views explicit rather than asking a modeler to guess.
- Do not produce `.blend`, GLB, mesh, rig, animation, or game configuration.
- Keep provider payloads, signed URLs, prompts, and credentials out of handoffs.

## Completion

Complete when the request/receipt validate and the reviewed set gives sufficient
non-contradictory views for the next separately authorized modeling operation.
Do not start Blender or approve the finished asset.
