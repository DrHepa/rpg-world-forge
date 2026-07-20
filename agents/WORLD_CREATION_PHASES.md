# World-creation phases

The phases are sequential because downstream work depends on upstream canon.
Iteration is expected, but a regression reopens every affected phase.

## P00 — Brief and constraints

Define audience, platform, scope, language, exclusions, accessibility, intended
session length and implementation constraints. Separate known decisions from
questions.

## P01 — Genre, promise and style

Define fantasy/science-fiction/horror/etc. mix, player fantasy, themes, tone,
content boundaries, prose guide and the experience's unique promise.

## P02 — World laws and canon ontology

Define physical/social/economic/magical/technological laws, time model, IDs,
fact types, sources of truth and contradiction policy.

## P03 — Geography and environments

Design scales, regions, settlements, biomes, travel, resources, hazards,
landmarks and spatial dependencies that can become maps.

## P04 — History, events and timeline

Build dated/relative events, eras, causes, consequences, uncertainty and
conflicting in-world accounts. Validate that character ages and institutions fit.

## P05 — Societies, cultures and factions

Define needs, values, taboos, governance, economy, technology, rituals,
resources, alliances and conflicts without monoculture shortcuts.

## P06 — Characters and personal stories

Create playable/non-playable actors, motivations, abilities, costs,
relationships, schedules, knowledge boundaries and personal arcs independent
from the main world arc.

## P07 — Systems and interaction matrix

Translate the world into deterministic mechanics. Document how time, economy,
relationships, factions, construction, travel, knowledge, skills and quests
change one another through events.

## P08 — World arcs and scenario architecture

Create global arcs, conflicts, event chains, branch points, failure/recovery
states and consequences. Ensure personal and world arcs can cross without
collapsing into one storyline.

## P09 — Quests, scenes and dialogue

Write implementable quest state machines, scene conditions/effects and dialogue
graphs. All visible text is localized and each speaker respects knowledge limits.

## P10 — Simulation, continuity and canon lock

Test reachability, chronology, resources, routes, softlocks, contradictions,
dominant strategies and narrative dead ends. Compile and hash the accepted
worldpack.

## P11 — Visual and audio direction

Create one target (`2d`, `2_5d`, or `3d`) bound to the locked world hash. Derive
and approve target-scoped visual/audio bibles: camera and coordinates, scale,
palette, silhouettes, materials, animation, UI, VFX, musical language, ambience,
SFX families, runtime formats, budgets, and observable acceptance tests.

Asset initialization creates only the target and an `art_direction` manifest;
null bible/inventory references are valid until decisions exist. Produce no
candidates in this phase.

## P12 — Asset inventory and specifications

Derive every required asset from maps, actors, actions, events, construction,
audio and UI, with canonical sources and semantic slots. Keep manual additions
separate. Write strict provider-neutral v2 specifications, exact 2D/audio or 3D
technical budgets, canonical sources, semantic slots, expected outputs, allowed
routes/executors, and acceptance tests before generation. Keep authorized
reference files and permissions as separate hash-bound authoring evidence.

## P13 — Asset production and QA

GPT orchestrates bounded offline executors. Complete OpenAI Image/Codex 2D and
2.5D production first. Stabilize the neutral GLB contract, then use OpenAI Image
for 3D reference design and Blender MCP in separate model, rig, animate,
refine, and export operations; run independent QA afterward. Add the local flow
last: GPT uses only reviewed Modly extensions through `modly-cli-mcp`; Blender
MCP may refine selected Modly outputs without breaking lineage.

Record typed requests/receipts, parent receipt hashes, selection, deterministic
processing (`png_canonical`, `atlas`, `wav_pcm`, or `glb_validate`), provenance/
licenses, output hashes, semantic bindings, and approved QA. Complete the
`production` manifest first; build the renderpack or assetpack under
`assets/release/`, then hash-seal it with `finalize-asset-release` and validate
the resulting `release` manifest. Never ship provider/MCP configuration,
authoring sources, model weights, or production evidence.

## P14 — Implementation handoff

For 2D/2.5D, deliver the worldpack plus the sealed renderpack, processed runtime
assets, bindings and notices for the current immutable-bundle/game flow. For 3D,
deliver the worldpack plus the sealed engine-neutral assetpack to a separate
implementation/runtime-adapter phase; do not represent it as supported by the
current pyray reference game or M4 bundle. Also provide map contracts,
state/event catalog, UI text, test scenarios, open risks, and a prioritized
implementation plan. Exclude all Forge skills and M5 authoring records.
