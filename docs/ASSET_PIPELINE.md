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
authorized selection -> cleanup -> slicing -> atlasing -> normalization
          |
          v
technical QA + licenses + hashes
          |
          v
asset manifest v2 --build-renderpack--> runtime-only renderpack
                                      |
                                      v
                          processed assets loaded by raylib
```

Models, prompts, references, weights, and generation tools belong to authoring.
They are neither shipped with the game nor invoked during play.

## Initialization

From the generated world-authoring repository:

```bash
worldforge init-assets build/world.worldpack.json \
  --output assets/manifest.json

worldforge validate-assets assets/manifest.json \
  --profile draft \
  --worldpack build/world.worldpack.json
```

The command creates directories for specifications, references, recipes, raw
generated results, and processed files. It enables only `openai` by default in
`generation_policy`; a project explicitly opts into `modly` if it chooses local
models. It generates nothing until the art and audio direction is approved.

After every asset is processed and the release profile passes:

```bash
worldforge build-renderpack assets/manifest.json \
  --worldpack build/world.worldpack.json \
  --output build/runtime/renderpack.json

# Optional reference-runtime QA only:
isoworld --pack build/world.worldpack.json \
  --renderpack build/runtime/renderpack.json
```

The compiler copies referenced processed files below `build/runtime/`, verifies
them with the runtime loader, and strips production-only evidence.
The subsequent release step creates an immutable bundle; a separate game
repository imports only that bundle, never this production workspace.

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
- **Local models through Modly**: optional image, pixel-art, audio, voice,
  music, SFX, or processing extensions. A local asset records the Modly
  extension ID/version and workflow. Direct local-model invocation is invalid.
- **Procedural/human**: maps, particles, shaders, typography, editing, and final
  polish.

Origins are interchangeable. The asset contract and processed file are stable.

## Generation routes

`openai` is the authoring route for GPT, Codex, and GPT Image. `modly` is the
optional authoring route for local models. Both operate outside the game and
produce candidates under `assets/generated/`. The manifest requires recipes,
model identifiers, and versions for assisted output. Modly additionally
requires:

- `extension_id`
- `extension_version`
- `workflow_file`

No OpenAI SDK, Modly runtime, extension, model, weights, provider credentials,
or prompt is copied into the renderpack.

## Required record per asset

- ID and type: `sprite`, `spritesheet`, `tileset`, `portrait`, `ui`, `vfx`,
  `sfx`, `music`, `font`, or `shader`.
- Specification and acceptance criteria.
- Machine-readable runtime format and file budget; visual assets also declare
  exact width/height, while audio declares sample rate and channels. PNG/WAV
  outputs are checked against those values before packaging.
- Origin: human, GPT Image, Codex, local model, procedural, or third party.
- Model, version, and recipe/prompt for assisted generation.
- References and permission to use them.
- Final asset, source, model, weight, and dataset licenses. Mark non-applicable
  fields explicitly as `not_applicable`.
- Human or project-authorized lead approver.
- Authorized-reference records with file, permission, and license when used.
- Non-empty QA report plus explicit in-engine and raylib-load results.
- One or more typed outputs: texture, clipset, audio, font, or shader, with
  runtime path, media type, and SHA-256.
- Semantic bindings from world/runtime slots to asset IDs and clips.
- Optional construction bindings use `construction:<blueprint_id>`; the M3
  renderer keeps a primitive fallback when a world has not produced them yet.

## Validation profiles

- `draft`: permits planned assets but requires existing specifications.
- `release`: requires every asset to be processed, approved, licensed, present,
  and correctly hashed. An empty manifest cannot be released.

## Isometric spritesheets

Specifications define cell size, foot pivot, directions, actions, frames, FPS,
row/column order, padding, alpha, palette, and shadow rules. Processing produces
the texture and an `isoworld.clipset` JSON. Clip frames use deterministic integer
tick durations; runtime never guesses layout from implicit conventions.

## SFX and music

Specifications define domain event, variations, duration, looping, target
loudness, sample rate, channels, priority, distance, and cooldown. Deterministic
events trigger SFX; no model synthesizes audio during play.
