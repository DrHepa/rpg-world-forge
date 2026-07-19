# ADR 0005: Deterministic M1 systems and versioned state

- Status: accepted
- Date: 2026-07-19

## Context

M0 compiled schedules, abilities, and other design collections but executed
only movement and actor selection. M1 needs systemic behavior while preserving
offline operation, replayability, world independence, and a small trusted data
surface.

## Decision

- Emit worldpack format 2 while continuing to load formats 1 and 2.
- Verify the canonical worldpack content hash at load time.
- Keep clock, A* routes, schedule planning, reservations, interactions, and
  abilities inside pure state transitions.
- Allow only explicit flag/resource effect operations; never evaluate content
  as Python or another scripting language.
- Bind save/replay format 1 to world ID and exact worldpack hash.
- Import only deterministic, finite subsets of Tiled and LDtk, using an explicit
  semantic tile mapping and recorded source/mapping hashes.

## Consequences

The same worldpack and action sequence reproduce the same state digest. Content
changes intentionally invalidate saves, replays, and downstream asset plans.
More expressive narrative effects must extend the allowlist, validator, loader,
reducer, schemas, documentation, and tests together.

Inter-map navigation, scriptable effects, object-layer composition, narrative
state machines, and automatic asset production remain outside M1.
