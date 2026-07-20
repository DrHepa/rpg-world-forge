---
name: optimize-pyray-game
description: Profile and optimize a verified pyray/raylib game using representative scenes and explicit budgets. Use only for measured performance work after correctness gates pass.
---

# Optimize a pyray game

## Scope

Optimize one measured G31 bottleneck in its game-owned implementation surface.
Do not combine unrelated optimizations or change contracts/features.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT`.
- Exact G30 source-tree/runtime/platform/catalog/bundle identities and baseline,
  with correctness gates passing and one budget miss or documented goal.
- One reproducible scene, measured metric, numeric acceptance threshold, target
  platform/backend, and owning G10-G25 writable surface.

## Outputs

- At most one game-owned implementation optimization and focused regression tests.
- Forge-owned before/after distributions, threshold result, optimized-tree identity,
  invalidated prior G30 evidence, and explicit handoff to a separate G30 invocation.

## Invariants

- Keep `src/isoworld`, locks, bundles, public contracts, determinism, visual
  semantics, path validation, and resource teardown intact.
- Stop and re-enter the owning phase if a contract/API change is required.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the performance gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Record a reproducible baseline scene, platform, backend, and bundle hash.
3. Measure simulation, render planning, drawing, load time, memory, and CFFI calls.
4. Optimize in order: culling, atlases, precomputed commands, caching, batching.
5. Apply only an implementation-level change inside the measured owning surface.
6. Compare before/after medians and tails, run focused/shared regressions and the
   clean-game audit, then hand the exact optimized identity to a separate G30.

## Completion

Complete only when the predeclared metric reaches its numeric threshold without
regression and focused/shared tests plus the clean-game audit pass. Invalidate
old G30 evidence and require a separate G30 invocation before G32; never claim or
write G30 evidence from this skill.
