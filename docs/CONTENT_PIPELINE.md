# Offline content pipeline

## Stages

1. **Canon**: record confirmed facts, tone, themes, and exclusions.
2. **Design**: create structured actors, factions, places, scenes, quests,
   abilities, routes, and dialogue.
3. **Table simulation**: test contradictions, narrative exploits, locks, and
   system combinations outside the game.
4. **Review**: accept, rewrite, or reject each proposal.
5. **Validation**: check schemas, IDs, references, and knowledge boundaries.
6. **Compilation**: produce a deterministic, content-hashed worldpack.
7. **Game QA**: run headless tests and a playable vertical slice.

These stages are expanded into P00-P14 in
`agents/WORLD_CREATION_PHASES.md`. Active phase state and evidence belong to the
generated game repository, not the forge.

AI is optional during design and simulation. `worldforge` does not call a model;
it turns approved sources into safe game data.

## What an actor should model

- Identity and diegetic role.
- Needs, short/long-term goals, and red lines.
- Abilities with costs, requirements, and verifiable effects.
- Directed relationships: trust, debt, fear, affinity, and rivalry.
- Separate facts, suspicions, protected secrets, and impossible knowledge.
- Segmented schedule, routes, and alternatives when a location is blocked.
- Discrete emotional state, never improvised runtime prose.
- Dialogue graphs with conditions and explicit effects.

A world may define editorial rules in `world.content_policy`. For example,
`exact_playable_actor_count` validates a closed roster and
`playable_requires_personal_arc` requires a campaign for every playable actor.
The engine does not know the concrete policy values.

## Knowledge boundaries

Every world fact has an ID. An actor may act or speak only from:

- `knows`: facts treated as true.
- `suspects`: hypotheses that must be expressed with uncertainty.
- `secrets`: known facts protected by reveal rules.
- `forbidden`: facts that actor cannot know.

The compiler rejects incompatible intersections and references to nonexistent
facts, preventing accidental omniscience without an LLM.

## Executable content through M2

The runtime executes a deliberately bounded subset of compiled content:

- `world.simulation`: clock rate, starting time, and route movement interval.
- `actors`: spawn, resources, abilities, schedule, and schedule mode.
- `schedules`: time segments with primary and fallback locations.
- `interactions`: location/range, flag conditions, repeatability, and effects.
- `abilities`: target/range, positive costs, cooldown, and effects.
- `facts` and actor knowledge: distinct epistemic state and hard boundaries.
- `factions` and actor social state: directed relationships and reputation.
- `dialogues`: conditional localized graphs with fact-gated nodes.
- `quests`: event/state-reactive stage machines.
- `scenes`: prioritized time-windowed narrative overlays.

The shared effect vocabulary is `set_flag`, `clear_flag`, `change_resource`,
`learn_fact`, `change_relationship`, and `change_reputation`. Unknown operations
are rejected during source validation and loading; the runtime never evaluates
arbitrary expressions or scripts.

The compiler emits worldpack format 3. The loader accepts formats 1, 2, and 3 and
verifies the canonical content hash before constructing typed runtime models.
See [M1_SYSTEMS.md](M1_SYSTEMS.md) and [M2_NARRATIVE.md](M2_NARRATIVE.md) for
exact semantics and accepted limits.

## Sources and artifacts

- `<game-repo>/source/`: editable material for the independent world.
- `<game-repo>/build/`: generated artifacts; never hand-edited.
- `<game-repo>/assets/`: specifications, sources, and approved results with
  separate provenance and license records.

For external resources, track source-code, model, weight, dataset, and final
asset licenses separately. An MIT or Apache-2.0 repository does not
automatically make its weights or datasets compatible.

External Tiled/LDtk map files are references, not runtime inputs. Convert them
with `worldforge import-map`, review the semantic mapping and imported rows,
then add the internal map JSON to the source manifest. The generated import
metadata records source and mapping hashes for reproducibility.
