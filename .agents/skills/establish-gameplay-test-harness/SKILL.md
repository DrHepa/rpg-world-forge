---
name: establish-gameplay-test-harness
description: Establish deterministic action, replay, state-digest, fixture, and fake-adapter test infrastructure in a standalone game. Use before gameplay implementation phases, not to implement features.
---

# Establish the gameplay test harness

## Scope

Build only G04 deterministic test infrastructure. Do not implement gameplay,
persistence formats, native presentation, or game content.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT` with valid locks/catalog.
- Current source-tree/runtime/platform/catalog hashes and prior G00-G03 evidence.
- Selected fixture world ID, release ID, and verified bundle hash.
- Stable minimal world fixture, semantic action fixture, and digest contract.
- G04 writable surface and exit gate.

## Outputs

- Game-owned fixture, action runner, replay comparator, state digest, fake
  adapter, and failure-reproduction helpers under `tests` or `src/game`.
- Focused harness tests and Forge-owned evidence record.

## Invariants

- Treat `src/isoworld`, runtime/platform locks, and bundles as immutable.
- Keep fixtures deterministic, portable, independent of wall time/native state,
  and free of save-file or gameplay-policy decisions.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.

## Workflow

1. Read the G04 gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Add game-owned fixtures/helpers under `GAME_ROOT/tests` or `src/game`.
3. Define semantic action sequences, integer ticks, and stable state digests.
4. Add replay equality, failure reproduction, and fake presentation-adapter tests.
5. Prove the harness from a working directory outside `GAME_ROOT`.

## Completion

Complete when identical fixture/action inputs reproduce identical state hashes,
a recorded failure reproduces, focused and shared headless tests pass, and the
clean-game audit passes.
