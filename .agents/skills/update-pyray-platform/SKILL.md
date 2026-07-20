---
name: update-pyray-platform
description: Change the locked pyray binding version, raylib backend, native API, Python range, and matching CI configuration in a standalone game. Use only for an explicit platform-baseline migration.
---

# Update the pyray platform baseline

## Scope

Change one locked Python/pyray/raylib/backend baseline in G02. Do not change the
runtime API, gameplay, content, or presentation features.

## Inputs

- Absolute `FORGE_ROOT` and external `GAME_ROOT`.
- Current platform lock, proposed exact baseline, migration reason, and target
  OS/architecture matrix.
- Current source-tree, runtime-lock, and catalog hashes plus previous gate.
- Official binding/backend support evidence for every declared target.

## Outputs

- Matching `platform.lock.json`, CI-consumed `requirements.lock`, direct/build
  dependency declarations, CI, notices, and focused platform tests.
- Forge-owned native evidence and G20-G32 invalidation record.

## Invariants

- Keep `src/isoworld`, `runtime.lock.json`, bundles, and gameplay immutable.
- Pin the complete supported dependency closure and keep native API, backend,
  Python range, `pyproject.toml`, both locks, CI, and notices consistent.
- Never copy `AGENTS.md`, `.agents`, Forge skills, or phase evidence into `GAME_ROOT`.
- Return runtime changes to G01 and adapter changes to their G20-G25 phase.

## Workflow

1. Read the G02 gate in `docs/GAME_IMPLEMENTATION_PHASES.md`.
2. Record the current platform lock and the reason for the candidate baseline.
3. Verify official binding/backend support on every declared target platform.
4. Update `platform.lock.json`, `requirements.lock`, and direct/build dependency
   declarations together; never leave a floating or alternate dependency surface.
5. Run headless tests plus native window, input, DPI, graphics, and audio profiles.
6. Invalidate G20-G32 evidence and rerun affected presentation phases and G30.

## Completion

Complete only when the lock, dependency graph, CI, and notices agree and every
required native matrix row passes. Record unsupported or skipped rows as missing
evidence, never as passes.
