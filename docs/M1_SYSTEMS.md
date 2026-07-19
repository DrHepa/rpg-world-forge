# M1 systemic runtime

M1 turns the foundation pack into a deterministic systemic slice. All behavior
is authored offline, compiled into worldpack format 2, and executed without a
model, network call, or authoring package.

## Navigation and reservations

`navigate` actions run four-directional Manhattan A* with a stable neighbor
order. Routes exclude the starting cell. Non-walkable and occupied cells are
blocked. On movement ticks, actors are processed by stable actor ID; the first
actor to claim a free cell reserves it for that tick. Swaps and overlaps are
rejected. A route blocked for three movement attempts is cleared so a schedule
can replan.

M1 navigation is intentionally local to one map. Portals, inter-map routing,
diagonal movement, terrain weights, moving-platform topology, and hierarchical
pathfinding belong to later milestones.

## World clock and schedules

`world.simulation` declares `start_day`, `start_minute`, `ticks_per_minute`, and
`movement_interval_ticks`. Clock state is part of every save and replay.

An actor may reference a schedule and select `always`, `when_inactive`, or
`never`. Each schedule entry has a half-open time interval, activity, primary
location, and ordered fallbacks. Overnight intervals are supported. Entries may
leave intentional gaps but cannot overlap. M1 rejects schedule destinations on
a different map from the actor's spawn because inter-map routes do not exist yet.

## Contextual interactions

Interactions are map positions with range, required/forbidden flags,
repeatability, localized prompt, and deterministic effects. `interact` chooses
the nearest eligible interaction, then stable ID. Supported effects are:

- `set_flag`
- `clear_flag`
- `change_resource` on the source or selected target actor

Non-repeatable completion is persisted.

## Abilities

Actors explicitly reference abilities and initialize every resource that an
ability costs. An ability declares self/actor targeting, Manhattan range,
positive resource costs, world-minute cooldown, and the same deterministic
effect vocabulary as interactions. A failed cost, range, or cooldown check does
not partially mutate state.

## Persistence and replay

Save format `isoworld.save` version 1 stores the complete runtime state and a
canonical SHA-256 digest. Replay format `isoworld.replay` version 1 stores every
dispatched input/tick action plus the expected final digest. Both documents bind
to `world_id` and the compiled `world_content_hash`. Incompatible or tampered
documents fail closed. Writes use a same-directory temporary file and atomic
replacement.

The current loader supports worldpack formats 1 and 2. The compiler emits format
2. Future save format changes require an explicit migration; unknown versions
are never guessed.

## Map import

`worldforge import-map` supports:

- finite Tiled JSON tile layers using uncompressed numeric arrays;
- embedded LDtk levels with IntGrid CSV or grid/auto-layer tiles.

The author supplies a numeric tile mapping to semantic internal tile IDs. The
result records source-format metadata plus source and mapping SHA-256 hashes.
Infinite Tiled chunks, compressed/base64 layers, external LDtk level files,
entities, portals, object layers, and multi-layer composition are rejected or
left for later milestones rather than being guessed.
