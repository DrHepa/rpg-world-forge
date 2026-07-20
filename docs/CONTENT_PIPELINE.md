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
7. **Bundle/reference-runtime QA**: run headless contract tests and an optional
   playable preview before release to a separate game repository.

These stages are expanded into P00-P14 in
`agents/WORLD_CREATION_PHASES.md`. Active phase state and evidence belong to the
generated world-authoring repository, not the Forge or the game repository.

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

## Executable content through M4

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
- `resources`, `stockpiles`, and `production_recipes`: finite economy and
  scarcity rules.
- `needs`, `goals`, `constructions`, and `consequences`: deterministic living-
  world state and delayed causal chains.
- `personal_arcs`: validated per-playable-actor campaign graphs.
- `locales`: typed BCP47 locale tables with one explicit default locale.
- `runtime_requirements`: a bounded runtime API interval plus required and
  optional feature IDs.

The shared effect vocabulary is `set_flag`, `clear_flag`, `change_resource`,
`learn_fact`, `change_relationship`, `change_reputation`,
`change_stockpile_resource`, and `change_need`. Unknown operations are rejected
during source validation and loading; the runtime never evaluates arbitrary
expressions or scripts.

The compiler emits worldpack format 5. The loader retains explicit compatibility
with formats 1 through 5 and verifies the canonical content hash before
constructing typed runtime models. Format 5 requires the localization and
runtime-compatibility contracts; it does not infer either from a game. See
[M1_SYSTEMS.md](M1_SYSTEMS.md), [M2_NARRATIVE.md](M2_NARRATIVE.md),
[M3_LIVING_WORLD.md](M3_LIVING_WORLD.md), and
[M4_MULTIPLE_WORLD_PRODUCTION.md](M4_MULTIPLE_WORLD_PRODUCTION.md) for exact
semantics and accepted limits.

## Sources and artifacts

- `<world-repo>/source/`: editable material for the independent world.
- `<world-repo>/build/`: generated artifacts; never hand-edited.
- `<world-repo>/assets/`: specifications, sources, and approved results with
  separate provenance and license records.

Only an immutable runtime bundle crosses into a game repository. The editable
directories above, `.worldforge/`, `AGENTS.md`, prompts, candidates, and
production evidence never do.

The release boundary is:

```text
worldpack v5 + renderpack + approved processed assets + runtime licenses
        |
        | worldforge export-bundle / verify-bundle
        v
content-addressed immutable runtime bundle
        |
        | worldforge import-bundle
        v
game_data/worlds/<world-id>/<release-id> + worlds.lock.json
```

The bundle and game catalog are runtime-neutral JSON contracts. Import verifies
every declared byte, runtime requirement, license entry, and cross-file ID/hash
before an atomic catalog update. The game does not read the authoring repository
or rebuild content at startup.

For external resources, track source-code, model, weight, dataset, and final
asset licenses separately. An MIT or Apache-2.0 repository does not
automatically make its weights or datasets compatible.

External Tiled/LDtk map files are references, not runtime inputs. Convert them
with `worldforge import-map`, review the semantic mapping and imported rows,
then add the internal map JSON to the source manifest. The generated import
metadata records source and mapping hashes for reproducibility.
