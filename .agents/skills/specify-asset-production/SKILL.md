---
name: specify-asset-production
description: Write one strict provider-neutral M5 asset specification from a reviewed inventory requirement. Use before production for 2D, 2.5D, audio, or 3D, not to generate or approve candidates.
---

# Specify asset production

## Scope

Specify one inventory requirement without coupling asset identity to a provider.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact world, target, bible, and reviewed inventory hashes.
- One inventory requirement, its canonical sources, and semantic slots.

## Outputs

- One strict asset-spec v2 document derived from the inventory and bound to the
  exact target, inventory, visual-bible, and audio-bible hashes.
- Purpose, canonical sources, acceptance criteria, semantic slots, expected
  outputs, and separate allowed routes/executors.
- Exact representation-specific technical contract and numeric budgets.

## Invariants

- For 2D/audio, fix dimensions/layout/pivot/format or sample/loop/mix behavior.
- For 3D, fix GLB coordinate, scale, geometry/material/texture/rig/animation/
  collider/LOD contracts before modeling starts.
- Treat `openai_image`, `blender_mcp`, `modly_cli_mcp`, `human`, and `procedural`
  as executors, never as the orchestrator or runtime.
- Fonts/shaders use binary technical contracts without image dimensions.
- Do not embed prompts, MCP configuration, source files, or an assumed extension.

## Completion

Complete when the single specification validates, is objectively testable, stays
within the target budgets, and covers every declared semantic slot. Do not issue
a production request or select a route in this skill.
