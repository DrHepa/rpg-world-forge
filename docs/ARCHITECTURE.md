# Architecture

## Primary boundary

```text
per-world design session (human + optional AI)
                    |
                    v
        reviewable <game-repo>/source JSON
                    |
          worldforge validate + compile
                    |
                    v
              static JSON worldpack
                    |
                    +--------> asset-production manifest
                    |          (authoring evidence only)
                    |                    |
                    |                    v
                    |             runtime renderpack
                    |          (processed files + bindings)
                    |                    |
                    +--------------------+
                                         v
                           raylib runtime without AI,
                              API, models, or network
```

AI is not a game subsystem. It does not decide dialogue, quests, routes, or
actions during play. It may propose authoring material, but that material must
be reviewed and compiled before it reaches the runtime.

## State flow

The runtime uses a small reactive-style flow:

```text
Input -> GameAction -> reducer(WorldState) -> new WorldState
                                             |
                                             v
                                  immutable RenderState
                                             |
                                             v
                                          Renderer
```

- `WorldState` is the simulation source of truth.
- The reducer applies pure, reproducible actions.
- `RenderState` is a frozen snapshot; rendering cannot touch `WorldState`.
- `FixedStep` keeps simulation stable across frame rates.
- Future systems react to domain events instead of calling one another through
  circular dependencies.

Navigation, clock advancement, schedules, interactions, abilities, narrative
events, quests, dialogues, scenes, and reservations stay inside pure state
transitions. Persistence serializes only `WorldState`; rendering still consumes
a separate frozen snapshot.

```text
tick -> advance clock -> plan eligible schedules -> reserve cells -> advance routes
input -> move/navigate/interact/use_ability -> reducer -> new WorldState
event -> bounded quest transitions -> eligible scene -> new WorldState
input -> dialogue choice/dismiss scene -> reducer -> new WorldState
save/replay -> world ID + content hash + state/action digest
```

## Layers

1. `core`: application, input-to-action mapping, and fixed step.
2. `world`: state, deterministic rules, navigation, and simulation.
3. `content`: loading of already-compiled worldpacks.
4. `render`: isometric projection, snapshots, and raylib drawing.
5. `ui`: presentation in the worldpack's language; no domain rules.
6. `worldforge`: authoring/build tools that runtime never imports.

The worldpack owns simulation and narrative semantics. The renderpack owns the
replaceable mapping from semantic slots such as `actor:hero` or
`tile_type:ground` to processed textures, deterministic clipsets, fonts,
shaders, SFX, and music. The asset-production manifest is never loaded by the
game because it may contain recipes, model identifiers, extension workflows,
references, and licensing evidence.

## Creative-process control plane

Every generated game repository contains `.worldforge/`. GPT reads its status,
works only on the active phase, and submits a phase report with deliverables,
decisions, blockers, and validation evidence. `complete-phase` prevents phase
skips or evidence-free completion. Optional subagents claim non-overlapping
paths; only the lead GPT integrates canon.

## Non-negotiable contracts

- Runtime does not import `openai`, `anthropic`, `transformers`, `langchain`,
  `litellm`, `ollama`, `llama_cpp`, or equivalent packages.
- Assets and worldpacks are distributable without credentials.
- One seed and the same action sequence produce the same state.
- Saves and replays must match the exact compiled world content hash.
- No two actors may occupy or reserve the same destination cell in one tick.
- Water is not walkable or arable.
- Rock and vegetation are not arable by default.
- Manual tile decisions take priority over generated decisions.
- Each world declares its visible language and localization policy.
- Release worldpacks contain no `TODO`, `TBD`, template braces, or broken refs.
- Runtime contains no names, roster sizes, or lore from a particular game.
- Local model execution is allowed only through an external Modly extension;
  the OpenAI route is likewise external authoring, never runtime inference.
- Game repositories live outside the forge and distribute only approved
  runtime code, worldpacks, and processed assets.
