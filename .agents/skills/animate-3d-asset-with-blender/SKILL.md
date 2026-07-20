---
name: animate-3d-asset-with-blender
description: Animate one reviewed rig through bounded Blender MCP operations under GPT orchestration. Use for specified clips and loop evidence only, not rig changes, final GLB export, refinement, or QA approval.
---

# Animate a 3D asset with Blender

## Scope

Author only the exact animation clips declared by one 3D asset specification.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact specification and selected rigged artifact/receipt hashes.
- Clip names, frame/timing ranges, FPS, loop and transition requirements,
  root-motion policy, and acceptance poses.
- A strict `blender_mcp` production request with operation `animate`, exact
  parent-produced `model` and `skeleton` inputs, and the compatible rig or
  post-rig receipt hashes.

## Outputs

- Animated authoring candidate with stable named actions and review captures.
- Typed Blender receipt retaining the rig receipt hash and exact tool identities.

## Invariants

- Preserve the selected model, rig hierarchy, skinning, coordinates, materials,
  sockets, and names; return structural defects to their owning phase.
- Keep clip timing deterministic and report non-looping endpoints explicitly.
- Require a hash-bound reviewed script, explicit approval, disabled telemetry,
  and keep `.blend` authoring-only.
- Do not silently add clips, retopologize, rerig, export final GLB, or approve QA.

## Completion

Complete when every required clip exists with correct name/range/timing/root
motion, loop/deformation evidence is recorded, and the receipt validates. Stop
before refinement/export and independent QA.
