# Offline asset pipeline

## Principle

Finish and compile the world before producing its visual and audio material.
`init-assets` copies the worldpack ID and hash into the asset manifest;
`validate-assets` detects later canon changes.

```text
validated, canon-locked worldpack
          |
          v
art/audio direction + asset inventory
          |
          v
reviewed per-asset specifications
          |
          v
GPT Image / Codex / local models / human / procedural
          |
          v
human selection -> cleanup -> slicing -> atlasing -> normalization
          |
          v
technical QA + licenses + hashes
          |
          v
processed assets loaded by raylib
```

Models, prompts, references, weights, and generation tools belong to authoring.
They are neither shipped with the game nor invoked during play.

## Initialization

From the generated game repository:

```bash
worldforge init-assets build/world.worldpack.json \
  --output assets/manifest.json

worldforge validate-assets assets/manifest.json \
  --profile draft \
  --worldpack build/world.worldpack.json
```

The command creates directories for specifications, references, recipes, raw
generated results, and processed files. It generates nothing until the art and
audio direction is approved.

## Production order

1. **Art bible**: perspective, resolution, scale, silhouettes, palette, light,
   materials, outline, camera, UI, and animation rules.
2. **Audio bible**: dynamic range, sample rate, layers, duration, loops,
   timbral families, and mixing rules.
3. **Derived inventory**: actors, biomes, objects, actions, scenes, and events
   become concrete asset requirements.
4. **Style proofs**: produce a few difficult pieces before batching.
5. **Family production**: share references, scale, and palette.
6. **Deterministic processing**: remove backgrounds, crop, set pivots, build
   atlases/metadata, normalize audio, and calculate hashes.
7. **In-engine QA**: test isometric readability, visual collision, popping,
   seams, loops, mix, memory, and performance.

## Intended tools

- **GPT Image**: direction exploration, character sheets, sprites, tiles,
  props, portraits, UI, and controlled variations.
- **Codex**: derive inventories from data, prepare briefs, automate slicing and
  atlasing, generate metadata, review consistency, and build conversion/QA tools.
- **Local models**: image or pixel-art generation/editing, audio, voice, music,
  SFX, and specialized processing when licenses are compatible.
- **Procedural/human**: maps, particles, shaders, typography, editing, and final
  polish.

Origins are interchangeable. The asset contract and processed file are stable.

## Required record per asset

- ID and type: `sprite`, `spritesheet`, `tileset`, `portrait`, `ui`, `vfx`,
  `sfx`, `music`, `font`, or `shader`.
- Specification and acceptance criteria.
- Origin: human, GPT Image, Codex, local model, procedural, or third party.
- Model, version, and recipe/prompt for assisted generation.
- References and permission to use them.
- Final asset, source, model, weight, and dataset licenses. Mark non-applicable
  fields explicitly as `not_applicable`.
- Human approver.
- Runtime path and SHA-256 of the processed file.

## Validation profiles

- `draft`: permits planned assets but requires existing specifications.
- `release`: requires every asset to be processed, approved, licensed, present,
  and correctly hashed. An empty manifest cannot be released.

## Isometric spritesheets

Specifications define cell size, foot pivot, directions, actions, frames, FPS,
row/column order, padding, alpha, palette, and shadow rules. Processing produces
the atlas and clip JSON; runtime never guesses layout from implicit conventions.

## SFX and music

Specifications define domain event, variations, duration, looping, target
loudness, sample rate, channels, priority, distance, and cooldown. Deterministic
events trigger SFX; no model synthesizes audio during play.
