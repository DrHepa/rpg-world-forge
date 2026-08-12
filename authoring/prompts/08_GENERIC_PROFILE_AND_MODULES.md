# Generic creation profile and typed modules

Use after `00_BOUNDARY.md` for a `world-forge.project` v1 authoring task.

## Task

Review or author the composable creation profile before modules. Keep these
facets independent: experience, gameplay, world presence, narrative, fiction
and tone, presentation/accessibility/localization, production, and runtime
target. Treat `world.presence: none` and `narrative.requirement: none` as
complete choices.

Then author only applicable discriminated modules:

- `world_module` for world canon/chronology/space/groups/characters/knowledge;
- `activity_module` for levels, missions, puzzles, matches, challenges, and
  other activity kinds;
- `narrative_module` for arcs, scenes, choices, storylets, clues, or endings;
- `system_module` for rules, events, economies, schedules, simulations, or
  seasons;
- exactly one `logic_module` for an executable v1 game.

Do not add RPG fields to a neutral puzzle, create geography for a no-world
project, or create narrative units for a narrative-none project. Required
namespaced extensions need a registered validator; unknown required extensions
fail closed.

## Output

Return exact portable paths, format/version/ID/hash identities, validation
results, open design decisions, and affected phase dependencies. Do not compile,
produce assets, select a runtime, or claim support.

Authoring validity is not runtime executability.
