# M2.5 presentation and asset runtime foundation

## Outcome

M2.5 closes the executable boundary between approved asset production and the
pyray renderer. It does not generate art or audio; automatic inventory,
provider adapters, batching, processing, and production QA remain M5 work.

```text
worldpack + asset manifest v2
              |
    validate release + verify hashes
              |
              v
       build-renderpack
              |
              v
renderpack.json + runtime-assets/
              |
              v
 isoworld --renderpack ...
```

## Runtime contracts

- `isoworld.renderpack` is bound to the exact world ID and world content hash.
- Every processed file is copied beneath the renderpack root and verified by
  SHA-256 before raylib receives it.
- Semantic slots decouple canon from art paths: `actor:<id>`,
  `tile_type:<id>`, `interaction:<id>`, `portrait:<actor_id>`,
  `event:<kind>[:<subject_id>]`, `music:default`, `music:map:<map_id>`, and
  `ui:font`.
- Actor and tile-type bindings are mandatory for a release.
- Every processed asset has a non-empty QA report and explicit successful
  in-engine/raylib-load evidence before it can enter a renderpack.
- Processed file totals must remain within their specification budget; PNG
  dimensions and WAV sample-rate/channel values are verified against the spec.
- Clipsets define rectangles, foot/visual pivots, loop behavior, and integer
  `duration_ticks`. The runtime never guesses a spritesheet layout.
- `moving_clip` is selected while an actor has a route; otherwise `clip` is
  used.
- `layer` participates in stable isometric entity ordering.

## Pyray lifecycle

The window is initialized before textures, fonts, shaders, or audio resources.
Audio is initialized only when the renderpack contains music or SFX. All loaded
objects are cached and unloaded in reverse ownership order before the audio
device and window close. Invalid raylib handles and clip rectangles fail during
loading; partial-load failures still release every resource acquired earlier.

The renderer retains primitive fallbacks when no renderpack is supplied. This
keeps headless/system development independent from the final art pipeline.

## Camera and presentation

- Mouse wheel: zoom in the range `0.5..3.0`.
- Middle-button drag: pan the isometric camera.
- Left-click navigation uses the same `Camera2D` inverse transform as drawing.
- Actors and interactions are sorted by isometric depth plus configured layer.
- Dialogue can bind a processed portrait with `portrait:<speaker_id>`.
- `event:*` SFX consume immutable recent domain events exactly once per render
  revision; map/default music is streamed and updated every frame.

## Generation routes

The manifest's `generation_policy.enabled_routes` determines which authoring
routes are available. `openai` covers GPT, Codex, and GPT Image and is the only
route enabled by default. A project may explicitly opt into `modly` for local
models; that route requires `extension_id`, `extension_version`, and a checked-in
`workflow_file`. Direct local execution is invalid.

The renderpack compiler deliberately strips the complete production policy,
provenance, recipes, model data, workflow paths, approvals, and licenses. A
separate license bundle remains a P14 handoff responsibility.

## Known limits

- Directional facing and action-specific animation state will expand the
  binding/clip selection when those simulation states exist.
- Tile walls, props, occluders, multilayer maps, virtual-resolution render
  targets, and culling remain later presentation work.
- M2.5 validates finite processed formats and raylib loading; it does not yet
  perform automatic slicing, atlas packing, audio normalization, or generation.
