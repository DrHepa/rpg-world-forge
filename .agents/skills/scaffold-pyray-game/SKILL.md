---
name: scaffold-pyray-game
description: Create the initial clean standalone pyray/raylib game repository from the Forge template. Use only for the bootstrap phase, before world import or game-specific feature work.
---

# Scaffold a pyray game

## Scope

Materialize only the G00 standalone-game shell. Do not import content or
implement gameplay, presentation, optimization, or packaging.

## Inputs

- Absolute `FORGE_ROOT` and a new external `GAME_ROOT`.
- Stable snake-case game ID, title, and Forge source revision.
- G00 requirements from `docs/GAME_IMPLEMENTATION_PHASES.md`.

## Outputs

- One clean game shell with vendored runtime, runtime/platform locks, the exact
  resolver input `requirements.lock`, empty catalog, ordinary game entry points,
  tests, CI, notices, and license.
- One Forge-owned external record of the initial source-tree hash and checks.

## Invariants

- Require a nonexistent, non-symlink target outside Forge and world roots.
- Never create or copy `AGENTS.md`, `.agents`, Forge skills, `.worldforge`,
  authoring source, prompts, phase evidence, or a Forge/AI dependency in `GAME_ROOT`.
- Leave world import and all later phase work untouched.

## Workflow

1. Read ADR-0009 and the bootstrap gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Run `worldforge new-game` with the explicit ID, title, target, and revision.
3. Inspect the generated application shell, vendored `isoworld` snapshot,
   `platform.lock.json`/`requirements.lock` agreement, notices, empty catalog,
   and verification scripts.
4. Run `worldforge audit-game` and the generated headless tests.

## Completion

Complete only when the empty game starts headlessly, both runtime and exact
dependency locks verify, the clean-game audit has no findings, and its initial
source-tree hash is recorded outside the game. Version-control initialization
is an owner action.
