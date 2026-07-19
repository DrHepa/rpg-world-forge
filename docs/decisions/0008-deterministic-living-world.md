# ADR-0008: Typed deterministic living-world systems

- Status: accepted
- Date: 2026-07-19

## Decision

Construction, economy, actor needs, goals, and delayed consequences are
compiled data contracts executed by the offline-capable runtime. Worldpack v4
adds seven optional-by-content but required-by-key M3 collections. Actor goals
use parent references, declarative conditions, integer priorities, and four
allowlisted actions instead of embedded scripts or behavior generated during
play.

Construction instances and production jobs use absolute completion minutes.
Their footprints participate in the same collision model as movement and A*.
Resources move through finite stockpiles; scarcity is derived rather than
stored. Delayed consequences persist a due minute and resolve through the
existing condition/effect/event model.

The pyray renderer consumes construction instances through immutable
`RenderState` and optional `construction:<blueprint_id>` renderpack bindings.
Game-specific menus remain in derived game repositories and dispatch typed
actions into the common reducer.

## Consequences

- The same worldpack and action sequence reproduce the same living-world state.
- Narrative can react to material systems without runtime text generation.
- Saves/replays must include needs, goals, stockpiles, structures, jobs, and
  delayed reactions and therefore advance to format version 3.
- Old worldpacks continue to load with empty M3 systems; new v4 packs fail
  closed when an M3 collection or cross-reference is missing.
- Authored systems remain world- and genre-agnostic, but derived games can
  replace presentation and input UX without forking simulation semantics.
