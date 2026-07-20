---
name: verify-pyray-game
description: Read-only verification of a standalone pyray/raylib game through boundary, deterministic, bundle, native, scene, and budget gates. Use for G30 evidence only, not fixes, optimization, or packaging.
---

# Verify a pyray game

## Scope

Run only G30 read-only verification and route findings to their owning phase.
Do not fix, optimize, package, or broaden target support in this phase.

## Inputs

- Absolute `FORGE_ROOT` and read-only external `GAME_ROOT`.
- Exact game commit/source-tree, runtime lock, platform lock,
  `requirements.lock`, shared-asset lock, catalog, and every selected bundle
  hash plus prior phase evidence.
- Declared OS/architecture/Python/backend/resolution/DPI/input/audio matrix,
  representative scenes, benchmark fixture, and numeric budgets.

## Outputs

- Forge-owned external command/results report, scene captures, native evidence,
  benchmark baseline, skipped-gate list, and exact identity envelope.
- Findings mapped to one owning G01-G25 phase; no game-tree changes.

## Invariants

- Keep `GAME_ROOT` read-only, including tests, locks, bundles, and configuration.
- Treat skipped/unavailable native evidence as missing, never passing.
- Never copy `AGENTS.md`, `.agents`, Forge skills, reports, or captures into
  `GAME_ROOT`.

## Workflow

1. Read the verification gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Run Forge-side clean-game/imported-bundle/shared-asset audits, locked Ruff
   lint/format checks, and verify that platform, resolver, direct, build, and
   quality-tool dependency declarations are one exact closure.
3. Run domain, determinism, replay/save, compatibility, and catalog tests for
   every declared OS/architecture/Python row.
4. Run fake-adapter failure/teardown tests for every native resource class.
5. Run window/scene profiles for every backend/resolution/DPI row, semantic and
   native availability probes for every input class, and every declared audio row.
6. Capture representative scenes and inspect viewport, depth, fallback, and UI.
7. Record a representative benchmark baseline and evaluate declared budgets.

## Completion

Complete only when every required matrix row, deterministic suite, static
lint/format gate, boundary and bundle audit, reference scene, and declared
budget passes. Record exact commands, identities, platform/backend, failures,
and skips outside `GAME_ROOT`; otherwise return to the owning phase and leave
G30 incomplete.
