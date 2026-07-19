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

Derive art/audio bibles from the locked world: camera, palette, silhouettes,
animation, UI, VFX, musical language, ambience, SFX families and budgets.

## P12 — Asset inventory and specifications

Derive every required asset from maps, actors, actions, events and UI. Write
provider-agnostic specifications and acceptance tests before generation.

## P13 — Asset production and QA

Use GPT Image/Codex through the OpenAI route, optional local models exclusively
through Modly extensions, procedural tools, or human work offline.
Record provenance/licenses, process deterministic runtime files and validate
them in-engine.

## P14 — Implementation handoff

Deliver worldpack, processed assets, schemas, asset metadata, map contracts,
state/event catalog, UI text, test scenarios, open risks and a prioritized
implementation plan for the separate game repository.
