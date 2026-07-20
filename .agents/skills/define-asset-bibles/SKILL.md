---
name: define-asset-bibles
description: Define and approve target-scoped visual and audio bibles from a locked worldpack. Use for M5 art direction only, not inventory derivation, asset generation, processing, or QA.
---

# Define asset bibles

## Scope

Complete only target-scoped M5 art and audio direction. GPT owns the synthesis;
do not delegate creative policy to a production executor.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Validated canon-locked worldpack and exact content hash.
- One validated asset target with dimension and delivery profile.
- Authorized creative decisions, reference permissions, and approver identity.

## Outputs

- Strict visual-bible and audio-bible documents bound to world and target hashes.
- Updated authoring manifest references only after both bibles validate and are
  explicitly approved.

## Invariants

- Derive rules from locked canon and target constraints; flag every manual choice.
- Specify observable camera, scale, palette/material, animation, audio, UI/VFX,
  accessibility, budget, and acceptance rules applicable to the target.
- Keep provider, model, MCP, credential, and runtime-generation configuration out.
- Never copy this Forge skill or its authoring evidence into a game or bundle.

## Completion

Complete when both bibles match their schemas, hashes and target dimension, have
an authorized approver, and contain testable acceptance rules. Do not derive the
inventory or generate candidates in this skill.
