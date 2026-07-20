---
name: process-asset-deterministically
description: Apply one reviewed declarative deterministic recipe to an authorized asset candidate and record reproducible outputs. Use after selection, not for generation, creative edits, approval, or release packaging.
---

# Process an asset deterministically

## Scope

Transform one authorized selected candidate into specification-bound runtime
outputs using only declared deterministic operations.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact specification, authorized selected candidate, and its production lineage.
- One strict executable processing recipe with hash-bound input artifacts.

## Outputs

- Processed files beneath the world authoring production area.
- One processing receipt with input/output hashes, exact recipe identity,
  toolchain versions, operation-specific details, and repeatability evidence.

## Invariants

- Permit exactly one allowlisted operation: `png_canonical`, `atlas`, `wav_pcm`,
  or `glb_validate`, with only its schema-declared options.
- Treat `glb_validate` as neutral, deterministic processing: independently
  inspect the GLB, require embedded resources and zero external URIs, enforce
  the specification budgets, and bind the republished bytes and metrics.
- Reject commands, shell fragments, arbitrary scripts, opaque plugins, network
  access, timestamps, random seeds, and undeclared environment dependence.
- Run twice when required and require byte-identical declared outputs.
- Never change canonical identity or make an artistic approval decision.

## Completion

Complete when all expected files exist, signatures/budgets/hashes match, the
receipt validates, and determinism evidence passes. Do not perform QA approval
or package a renderpack/assetpack.
