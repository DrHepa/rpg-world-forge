# M2 deterministic narrative core

M2 executes approved narrative source as typed data. The runtime remains
offline and contains no LLM, provider SDK, prompt execution, scripting engine,
or general expression evaluator.

## Facts and knowledge boundaries

A fact declares an ID, statement, kind (`truth`, `secret`, or `rumor`), and
editorial truth state (`true`, `false`, or `unknown`). Each actor independently
stores one of `unknown`, `suspected`, `known`, or `secret` for that fact.

Actor source divides initial knowledge into `knows`, `suspects`, `secrets`, and
`forbidden`. The first three are mutually resolved into runtime state;
`forbidden` is a hard boundary. The validator rejects initial conflicts, the
runtime refuses a `learn_fact` effect for a forbidden actor, and dialogue nodes
with inaccessible `fact_refs` cannot be entered. This prevents a speaker from
leaking information merely because a line exists in the worldpack.

## Social state

Relationships are directed. `explorer -> guide / trust` is independent from
`guide -> explorer / trust`. Faction reputation also belongs to an actor. Both
domains use integers in `-100..100`; effects add deltas and clamp the result.

## Conditions

All condition lists use logical AND. `negate: true` reverses one condition.
The allowlist is:

- `flag_set`, `flag_unset`
- `fact_status`
- `relationship_at_least`, `reputation_at_least`
- `quest_status`
- `event`, optionally restricted by actor and subject
- `time_window`, including overnight windows
- `actor_at`

No field is interpolated into Python, SQL, a shell, or another language.

## Effects and events

M2 extends the M1 effect vocabulary with `learn_fact`,
`change_relationship`, and `change_reputation`. Existing `set_flag`,
`clear_flag`, and `change_resource` operations remain supported. Unknown
operations fail validation and loading.

Successful actions emit bounded domain events such as
`interaction_completed`, `ability_used`, `fact_learned`, `dialogue_choice`,
`minute_changed`, `location_entered`, `quest_advanced`, and `scene_triggered`.
Quest processing uses stable quest IDs and a transition bound, so event chains
remain deterministic and cannot loop forever.

## Dialogue

A dialogue targets a nearby actor, declares entry conditions and starts at one
node. Nodes identify their speaker, visible text, referenced facts, on-enter
effects, choices, and whether manual exit is allowed. Choice conditions are
re-evaluated when displayed and selected. A next node is hidden if its speaker
cannot access every referenced fact.

`Q` starts the nearest eligible conversation; number keys select a currently
visible choice; `Esc` exits only when the node permits it. The world clock and
routes pause while the overlay is open.

## Quests and scenes

Quests contain an auto-start condition and an ordered state graph. Each stage
has completion/failure conditions, effects, and at most one next stage. Quests
react to the current action's domain events and can also inspect persistent
state.

Scenes combine a daily time window, conditions, priority, text, effects, and
`once` behavior. Repeatable scenes run at most once per in-game day. The
highest-priority eligible scene wins, with stable ID as tie-breaker. `Space`
dismisses the scene; simulation pauses while it is visible.

## Persistence and compatibility

The compiler emits worldpack format 3. The runtime reads formats 1, 2, and 3,
verifies the content hash, requires M2 collections for format 3, and limits a
worldpack to 64 MiB. Save/replay format 2 records knowledge, social values,
quest stages, dialogue/scene state, scene history, events, and M2 actions. It is
still bound to the exact world ID and content hash.

## Offline narrative analysis

Run:

```bash
worldforge analyze-narrative source/manifest.json \
  --output build/narrative-analysis.json \
  --fail-on warning
```

The analyzer reports unreachable dialogue nodes and quest stages, hard and
conditional dialogue softlocks, quests without a terminal stage, missing
flag/fact producers, and forbidden or unavailable speaker knowledge. This is a
conservative authoring check, not a proof that every combination of world state
is reachable. Release projects should treat errors as blockers and review all
warnings.

## Current limits

- Conditions are conjunctions; nested AND/OR expression trees are not present.
- A quest stage has one next stage; branching is expressed as separate quests
  or authored conditions until a versioned branching contract is added.
- Dialogue text is static and does not interpolate arbitrary state.
- Scenes pause the world instead of scheduling concurrent cinematic actors.
- Static analysis does not exhaustively model every ordering of time, movement,
  flags, resources, and player choices.
