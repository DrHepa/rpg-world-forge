# M3 living-world runtime

M3 turns authored simulation data into interacting, deterministic systems. It
does not add runtime AI. GPT, Codex, GPT Image, or optional local models through
Modly may help author the source and assets; the game receives only validated
worldpack v4 data and a runtime renderpack.

## Source collections

- `resources`: stable IDs, localized names, base values, and scarcity targets.
- `needs`: 0..100 actor values, integer decay cadence, critical threshold, and
  the resource/amount that restores the need.
- `goals`: parent-linked hierarchy, priority, declarative conditions, and one
  allowlisted action (`satisfy_need`, `travel`, `build`, or `produce`).
- `stockpiles`: a map location, finite capacity, and initial resources.
- `constructions`: footprint offsets, resource costs, build duration, dynamic
  collision, and an optional linked stockpile.
- `production_recipes`: a required construction, inputs, outputs, and duration.
- `consequences`: event/subject trigger, delay, conditions, effects, and
  once/repeat semantics.

Actors opt into needs and goal roots with `needs` and `goal_ids`. An empty M3
collection is valid, so a world can use only the systems appropriate to its
genre.

## Deterministic tick order

On a world-minute boundary the runtime performs this order:

1. Decay authored actor needs on their integer cadence.
2. Complete due construction and production jobs and emit domain events.
3. Schedule matching delayed consequences and resolve due reactions.
4. Select eligible goal leaves by priority, hierarchy depth, then stable ID.
5. Execute or route toward the selected typed goal action.
6. Plan schedules for actors not currently controlled by a goal.
7. Advance reserved routes at the configured movement interval.
8. Process quests and the highest-priority eligible scene.

There are no scripts, expressions, random model calls, or provider callbacks in
this sequence.

## Construction and navigation

`GameAction(kind="build", ...)` validates range, base terrain, actors,
footprint overlap, and inventory costs. A building occupies its footprint while
under construction and after completion when `blocks_movement` is true. Direct
movement, A* planning, actor schedules, active routes, and persistence all use
the same dynamic collision rule.

The immutable render snapshot contains each construction instance. Pyray first
tries the optional semantic binding `construction:<blueprint_id>` and otherwise
draws a primitive state-aware fallback. A derived game should implement its own
build menu and dispatch the typed action rather than putting genre-specific UI
inside the forge.

## Economy, production, and scarcity

`transfer_resource` moves a positive integer amount between an adjacent actor
and stockpile. `start_production` consumes inputs immediately and reserves
enough capacity for all pending outputs attached to that stockpile. The output
arrives only when its absolute due minute is reached.

Scarcity is a derived 0..100 percentage from all actor and stockpile holdings
relative to the resource target. It is available through the
`scarcity_at_least` condition and is not duplicated in saved state.

## Needs and hierarchical goals

Needs clamp to 0..100. Authors explicitly decide which actors have them. A goal
root gates its descendants; every condition in the root-to-leaf chain must be
true. Only leaf/action goals are candidates. The stable ordering is highest
priority, deepest eligible goal, then ID. Active goals override authored
schedules, but never invent an action outside the four allowlisted kinds.

## Delayed consequences

A matching event schedules `(consequence_id, due_at_minute, source_actor_id)`.
Pending and once-triggered histories are part of save/replay v3. Resolution
uses the same validated effects as narrative content and emits
`consequence_resolved`; another consequence may listen to that ID to create a
multi-stage arc. Delays must be positive, so a chain cannot recurse within one
simulation instant.

## Persistence and compatibility

Worldpack v4 requires all M2 and M3 collection keys. The loader still accepts
worldpack v1-v3 and treats absent living-world systems as empty. Saves and
replays use version 3, bind to the exact world content hash, and reject invalid
positions, capacities, jobs, construction overlap, goal references, and
consequence history.
