# Session: target-scoped visual and audio bibles

Work only from a canon-locked worldpack, its canonical documentation, and one
reviewed `rpg-world-forge.asset_target`. GPT is the decision orchestrator. Do not
generate assets, choose providers, or invent requirements that canon and the
target do not support.

Return one strict `rpg-world-forge.visual_bible` and one strict
`rpg-world-forge.audio_bible`, both bound to the exact world and target hashes.
Use applicable, testable decisions rather than vague adjectives.

## Visual bible

- Dimension (`2d`, `2_5d`, or `3d`), projection, camera, axes, units, scale,
  base resolution, and scaling policy.
- Palette, contrast, lighting, material response, and shadow rules.
- Silhouette and proportion language by faction, role, and asset family.
- Animation cadence, locomotion, transition, direction, deformation, and loop
  rules appropriate to the target dimension.
- Tile/prop/texture/model, VFX, UI, portrait, and accessibility rules.
- Authorized examples, explicitly forbidden imitation, and observable
  acceptance tests at target presentation scale.
- For 3D, the intended neutral GLB appearance and stable camera/reference-sheet
  views without choosing a modeling executor.

## Audio bible

- Runtime format, sample rate, channels, loudness targets, peak limits, and mix
  buses.
- Timbral families for the world, environments, groups, objects, and UI.
- Ambience layers, music structure, transitions, priorities, and ducking.
- SFX duration, variations, loops, cooldowns, distance behavior, and tails.
- Accessibility rules and acceptance tests for clarity, masking, repetition,
  clicks, truncation, and seamless loops.

Flag every decision that cannot be derived from canon. Record an authorized
approver and canonical hashes. Do not advance the manifest beyond
`art_direction` until both bibles validate and are approved. The bibles may
describe outcomes but must contain no provider, MCP server, Modly extension,
credential, or game-runtime generation configuration.
