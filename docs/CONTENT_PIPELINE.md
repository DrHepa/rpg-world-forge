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

## Sources and artifacts

- `<game-repo>/source/`: editable material for the independent world.
- `<game-repo>/build/`: generated artifacts; never hand-edited.
- `<game-repo>/assets/`: specifications, sources, and approved results with
  separate provenance and license records.

For external resources, track source-code, model, weight, dataset, and final
asset licenses separately. An MIT or Apache-2.0 repository does not
automatically make its weights or datasets compatible.
