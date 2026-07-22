# Pyray Runtime Guide

This guide defines the supported raylib boundary for generated 2D/2.5D game
repositories. It is an implementation guide for agents and maintainers, not a
claim that raylib is a complete game engine. Simulation and content contracts
remain owned by RPG World Forge; raylib owns the window, input polling,
graphics, and audio presentation boundary.

## Verified baseline

The baseline was verified against primary sources on 2026-07-19:

- [raylib 6.0](https://github.com/raysan5/raylib/releases) is the current stable
  native library. Its release added the software renderer, memory platform,
  revised filesystem API, and revised high-DPI/fullscreen behavior. The exact
  API changes are recorded in the official
  [CHANGELOG](https://github.com/raysan5/raylib/blob/master/CHANGELOG).
- Python uses the community-maintained
  [`electronstudio/raylib-python-cffi`](https://github.com/electronstudio/raylib-python-cffi)
  binding. Raylib lists it in its official
  [bindings registry](https://github.com/raysan5/raylib/blob/master/BINDINGS.md),
  but raylib does not provide a first-party Python binding.
- The stable Python distribution is
  [`raylib==6.0.1.0`](https://pypi.org/project/raylib/6.0.1.0/), which targets
  raylib 6.0. Generated game repositories pin this exact release. The consumed
  `requirements.lock` also pins its `cffi`/`pycparser` runtime graph and the
  editable-build toolchain; a newer repository revision is not a release.
- The distribution contains two importable modules: `raylib`, which closely
  follows the C API, and `pyray`, which supplies snake-case names, automatic
  string/pointer conversions, and structure helpers. There is no separate
  `pyray` distribution. Do **not** install `pyray` or the unrelated,
  older `pyraylib` project. See the binding's
  [installation and API guidance](https://electronstudio.github.io/raylib-python-cffi/README.html#how-to-use).

Generated games should use an explicit import:

```python
import pyray as pr
```

Wildcard imports obscure resource ownership and make agent-generated patches
harder to audit. The lower-level `raylib` module is allowed only behind the
raylib adapter after profiling demonstrates a meaningful hot path. The
maintainer notes that it can be slightly faster, while calls across the Python
to C boundary remain the larger concern. See the official
[performance notes](https://electronstudio.github.io/raylib-python-cffi/README.html#performance).

Use the standard statically compiled `raylib` wheel as the desktop baseline.
The binding maintainer recommends it over `raylib_dynamic`, whose failures can
be silent and whose calls are slower; see the
[dynamic-binding warning](https://electronstudio.github.io/raylib-python-cffi/dynamic.html).
`raylib_sdl` exposes the same Python API and may improve controller support,
but its maintainer describes that backend as not well tested. Keep backend
selection outside domain code and prove an SDL switch with the full platform
matrix before adopting it.

The generated game records this baseline in `platform.lock.json`. That lock is
separate from `runtime.lock.json`, which hashes the complete vendored
`src/isoworld/` snapshot. `requirements.lock` is the resolver input actually
installed by CI; the verifier requires it to agree with `platform.lock.json`
and `pyproject.toml`. It also pins Ruff as CI-only quality tooling; Ruff is not
a runtime import. A G01 runtime migration atomically replaces the whole
snapshot and runtime lock without changing Python or raylib. A G02 platform
migration changes both platform/dependency locks, exact direct dependency,
build configuration, CI matrix, notices, and native evidence together without
editing the runtime snapshot. Game-specific implementation remains under
`src/game/` in both cases.

## Runtime boundary

Raylib is deliberately modular but low level. Its seven primary modules cover
window/input, the graphics abstraction, images/textures, fonts/text, shapes,
models, and audio. It does not supply a scene graph, asset ownership, game
rules, navigation, persistence, or a narrative engine. The official
[architecture overview](https://github.com/raysan5/raylib/wiki/raylib-architecture)
describes that scope.

A generated game therefore keeps these dependencies one-way:

```text
validated bundle -> deterministic WorldState -> immutable RenderState
                                                   |
                                                   v
                                         game presentation plan
                                                   |
                                                   v
                                          raylib adapter -> pyray
```

- Domain and simulation modules must not import `pyray`.
- The renderer consumes only immutable render snapshots.
- Input is translated into typed game actions before it reaches simulation.
- Filesystem, window, graphics, and audio calls are isolated behind the raylib
  adapter.
- A headless game run must work without importing pyray or opening a window.
- No runtime layer imports `worldforge`, provider SDKs, or model clients.

## Application and resource lifecycle

Raylib teaches a four-part lifecycle: initialization, repeated update/draw,
and deinitialization. The official
[introductory course](https://github.com/raysan5/raylib-intro-course) and
[game template](https://github.com/raysan5/raylib-game-template/blob/main/src/raylib_game.c)
show the expected order.

Use this order in a generated game:

1. Resolve and validate the world bundle before creating native resources.
2. Set window configuration flags that must precede window creation.
3. Initialize the window and graphics context.
4. Initialize the audio device only if the selected renderpack needs audio.
5. Load validated textures, fonts, render textures, shaders, sounds, and music.
6. Run input, fixed simulation updates, audio streaming, and drawing.
7. Stop streams and unload resources in reverse ownership order.
8. Close the audio device, then close the window/graphics context.

GPU resources must be unloaded before `close_window()`. Audio resources must be
unloaded before `close_audio_device()`. The entire acquired-resource section
must be protected by `try/finally`; partial load failures must release resources
that were already created.

One registry owns all native handles. It must:

- be safe to load and close at most once;
- validate results with `is_texture_valid`, `is_font_valid`,
  `is_render_texture_valid`, `is_shader_valid`, `is_sound_valid`, and
  `is_music_valid` where applicable;
- resolve paths only through the bundle or shared-game asset root;
- reject path traversal and files not declared by the renderpack;
- load nothing from `update()` or `draw()`;
- unload every successfully loaded handle exactly once; and
- retain primitive and silent fallbacks for optional presentation bindings.

Raylib distinguishes CPU `Image` data from GPU `Texture` data. If an image is
used only to create a texture, unload the image after upload. The official
[image-loading example](https://www.raylib.com/examples/textures/loader.html?name=textures_image_loading)
documents that RAM/VRAM distinction.

## Fixed-step loop

Frame time controls presentation cadence, not simulation semantics. The game
uses an accumulator and an integer simulation tick:

```text
poll semantic input once for this display frame
accumulator += clamp(frame_time)
while accumulator >= fixed_dt and updates < catch_up_limit:
    consume queued actions once
    simulate one deterministic tick
    build and publish an immutable RenderState
    accumulator -= fixed_dt
update streamed audio once
draw the latest RenderState once
```

The current reference runtime uses 20 simulation ticks per second, clamps one
display frame to 0.25 seconds, and permits at most five catch-up updates. A
generated game may change those constants only as a versioned game-runtime
decision; a world may not silently change them.

Simulation code must never call `get_frame_time()`, `get_time()`, input
functions, random functions without the deterministic seed, or drawing APIs.
Pressed/released input edges are queued once per display frame so that catch-up
ticks cannot repeat one physical press. Held input may become explicitly
repeatable game actions according to a deterministic repeat policy.

Raylib polls platform input as part of its frame lifecycle; the official
[input-system description](https://github.com/raysan5/raylib/wiki/raylib-input-system)
explains that `EndDrawing()` swaps buffers and polls events. Do not call
`end_drawing()` multiple times to drive simulation. Rendering may later
interpolate between snapshots, but interpolation must never alter `WorldState`.

## 2D/2.5D isometric presentation

An isometric sprite game should use `Camera2D`, not `Camera3D`, unless a derived
game makes an explicit decision to render real 3D geometry. Raylib provides
camera pan/zoom and screen/world conversion; see the official
[2D camera example](https://www.raylib.com/examples/core/loader.html?name=core_2d_camera)
and the binding's
[`Camera2D` API](https://electronstudio.github.io/raylib-python-cffi/pyray.html).

Keep projection as pure, tested functions. For tile width `w`, tile height `h`,
and authored vertical elevation `e`, the reference convention is:

```text
screen_x = (grid_x - grid_y) * w / 2
screen_y = (grid_x + grid_y) * h / 2 - e
```

Pointer picking reverses the full presentation transform in this order:

1. Convert physical-window coordinates into virtual-resolution coordinates,
   removing letterbox offsets and scale.
2. Call `get_screen_to_world_2d()` with the active camera.
3. Apply the inverse isometric projection.
4. Apply the game's documented cell-selection rule at tile boundaries.

The same transform must be used by navigation previews, interactions, editor
overlays, and tests. Never compensate for camera or letterbox offsets with
ad-hoc constants.

Use one stable depth plan for terrain and entities:

```text
terrain: (x + y, elevation, y, x)
entity:  (x + y, binding_layer, y, x, kind, stable_id)
```

Tall sprites use authored pivots so their feet, not their texture origin,
occupy the cell. Actors, interactions, constructions, vegetation, and other
occluding objects belong in one merged entity ordering. A practical render
graph is:

1. terrain and ground decals;
2. route/debug ground overlays;
3. depth-sorted world entities;
4. roofs and foreground occluders;
5. particles and world-space effects;
6. lighting/postprocessing; and
7. screen-space UI and dialogue.

## Virtual resolution and render textures

Render the world into a fixed virtual-resolution `RenderTexture`, then present
that texture into a letterboxed destination rectangle. This makes layout,
camera behavior, captures, and pointer conversion independent of window size.
The official
[letterbox example](https://www.raylib.com/examples/core/loader.html?name=core_window_letterbox)
demonstrates the scale and virtual-mouse calculation. The
[render-texture example](https://www.raylib.com/examples/core/loader.html?name=core_render_texture)
demonstrates explicit creation and unloading.

Raylib render textures have an inverted texture-space Y orientation when drawn
back to the screen; present them with a negative source height, as shown in the
official examples. Use nearest-neighbor filtering for pixel-locked art and a
reviewed bilinear policy for higher-resolution painted art. Treat high-DPI,
resize, fullscreen, and display-scale changes as input to the viewport
calculation rather than as simulation events.

## Assets and renderpacks

The game loads only the immutable bundle's worldpack, renderpack, approved
processed files, and license notices. It never loads prompts, candidate files,
provider metadata, model identifiers, Modly workflows, credentials, or asset
production manifests.

Use semantic bindings such as `tile_type:ground`, `actor:guide`,
`construction:workshop`, `event:construction_completed`, and `music:map:garden`.
The runtime registry resolves those slots to handles; simulation never stores a
texture or sound path.

For sprites and tiles:

- prefer atlases/spritesheets over one native texture per frame;
- validate every clip rectangle against texture bounds during load;
- select animation frames from integer simulation ticks;
- retain stable pivots, scale, and layer in the render binding;
- cull invisible tiles/entities before issuing CFFI calls; and
- consider chunk render-texture caches only after profiling, because dynamic
  terrain and construction invalidate cached chunks.

The official
[sprite-animation example](https://www.raylib.com/examples/textures/loader.html?name=textures_sprite_animation)
uses a spritesheet source rectangle. `draw_texture_pro()` is the appropriate
high-level operation for clip rectangles, pivots, scale, rotation, and tint.

Do not rely on the process working directory. A generated game has one resolved
read-only data root for packaged shared data and world bundles, plus a separate
writable user-data root for saves and settings. Development hot reload is an
optional adapter feature; release mode uses only hash-checked files.

Game-owned common presentation files are declared by
`game_data/shared.lock.json`. G23 may change shared graphics and G25 may change
shared UI/common audio; both must run `python scripts/lock_shared_assets.py`
before verification. Other phases consume the locked bytes read-only. The lock
binds the full inventory and `THIRD_PARTY_NOTICES.md`; every shared file needs
an origin and license entry there, including an explicit game-owned statement
for original work. World-specific media remains in the world's bundle.

## Audio

Initialize audio before loading sound or music. Use `Sound` for short resident
effects and `Music` for streamed music/ambience. Call `update_music_stream()`
once per display frame for every playing stream. The official
[music-stream example](https://www.raylib.com/examples/audio/loader.html?name=audio_music_stream)
shows the required update and unload lifecycle.

The game adapter owns logical `master`, `music`, `ambience`, `sfx`, and `ui`
volume buses and applies them through raylib's per-resource volume functions.
Event-to-SFX selection must be deterministic when multiple approved variations
exist. Avoid Python callbacks on raylib's real-time audio thread; pre-authored
sounds and ordinary music streaming cover the supported runtime contract.

Audio initialization failure may become a clear silent-mode warning when audio
is optional. It must not leave a half-loaded registry or alter simulation.

## Input

Physical devices map to semantic actions before dispatch:

```text
keyboard/mouse/gamepad -> action map -> typed GameAction -> simulation
```

Keep bindings and accessibility settings outside world content. Define
deadzones and normalization for gamepad axes, support keyboard and controller
navigation for menus, and distinguish pressed, released, held, and analog
values. The official
[input-actions example](https://www.raylib.com/examples/core/loader.html?name=core_input_actions)
shows the action-mapping pattern, while the
[gamepad example](https://www.raylib.com/examples/core/loader.html?name=core_input_gamepad)
shows device discovery and deadzones.

Input tests exercise semantic actions without raylib. Platform smoke tests
exercise representative keyboard, pointer-coordinate, and gamepad queries
through the adapter. Keeping this boundary stable also permits an evaluated
switch between the standard and SDL wheels without changing game rules.

## Shaders and lighting

Ship platform variants from the start:

```text
game_data/shared/shaders/glsl330/  # desktop OpenGL 3.3
game_data/shared/shaders/glsl100/  # OpenGL ES 2.0/web-compatible path
```

Official raylib shader examples use GLSL 330 on desktop and GLSL 100 on
Android/Web/ES2. See the
[postprocessing example](https://www.raylib.com/examples/shaders/loader.html?name=shaders_postprocessing).
Load the variant selected by the platform profile, validate it, cache uniform
locations, and provide a no-shader fallback when an effect is optional.

For the initial isometric runtime, use shaders for bounded presentation work:
palette/color grading, a 2D light mask, weather, outlines, and fullscreen
postprocessing. Do not move simulation visibility, combat, or authoritative
lighting rules into a shader. Avoid `rlgl` and compute-shader paths until the
high-level API is demonstrably insufficient and the platform baseline is
revised.

## Performance policy

The CFFI maintainer explicitly notes that every Python-to-C call has a cost.
Optimize in this order:

1. measure update and draw time independently;
2. cull off-screen tiles and entities;
3. use atlases and precomputed presentation commands;
4. avoid native load/unload and avoidable structure conversion per frame;
5. cache static calculations and only then consider chunk render textures;
6. compare CPython and PyPy with the real representative scene; and
7. use the lower-level `raylib` module only for a measured inner-loop win.

Record visible tiles, visible entities, emitted draw operations, simulation
updates, audio events, and frame timings in the benchmark script. PyPy can be
substantially faster according to the binding's benchmark, but it is an
optional verified deployment profile, not a reason to compromise packaging,
debugging, or library compatibility. CPython remains the first release
baseline.

## Testing and verification

The default suite remains headless and has no GPU/audio requirement:

- unit-test projection and inverse picking at tile edges;
- test stable depth ordering, pivots, culling, and animation-frame selection;
- replay identical actions and compare state hashes;
- test worldpack/renderpack/hash compatibility and contained paths;
- test resource plans with a fake raylib adapter;
- verify that domain and simulation modules cannot import pyray;
- verify that game runtime cannot import `worldforge` or AI/provider packages;
- run save/replay compatibility tests independently of presentation; and
- test every imported world bundle independently and in the game catalog.

A separate native smoke profile opens a window, loads the minimal processed
pack, draws at least one frame, unloads it, and closes cleanly. Run it on the
supported Linux and Windows release environments. Screenshot/golden tests are
useful only with a pinned platform/backend and explicit tolerance; they do not
replace semantic render-plan tests. Raylib 6.0's memory/software backends are
promising for future headless rendering, but the standard Python wheel's
supported path must be proven before making them a CI requirement.

## Packaging and platform policy

Desktop Linux and Windows are the supported game-template targets. Use a
`pyproject.toml`, the exact `raylib==6.0.1.0` dependency, and the committed
`platform.lock.json`. The binding's
official [installation table](https://electronstudio.github.io/raylib-python-cffi/README.html#installation)
documents available wheels and the native build fallback. Test the actual
release runners rather than assuming that one wheel proves all targets.

The binding documents [Nuitka](https://electronstudio.github.io/raylib-python-cffi/README.html#packaging-your-app)
for standalone binaries. Packaging scripts must explicitly include:

- the immutable world bundle and its lock manifest;
- all shared and world-specific processed files;
- shader variants needed by the target;
- the game license and third-party notices; and
- no authoring source, prompts, credentials, model data, or production evidence.

M4 G32 packages the exact read-only `GAME_ROOT` that passed G30 as one
deterministic unpack-and-run source ZIP and writes only to an external
staging/output root. It does not create a wheel, installer, or native
executable; each of those requires a later dedicated release skill. If
packaging reveals a needed source,
configuration, lock, catalog, bundle, notice, or test change, return to the
owning phase and rerun the complete G30 matrix before resuming release.

The binding maintainer also provides a Python
[project template](https://github.com/electronstudio/python-raylib-template)
with `uv`, resizable render-texture scaling, binary workflows, and a pygbag
route. It is a useful reference, not our generated-game template: it defaults
to the less-tested SDL wheel and does not enforce World Forge bundle or runtime
boundaries.

Web is a later compatibility target. The binding's pygbag path requires an
async main loop and explicitly warns that some features, including audio, may
not work. Do not let web requirements weaken the tested desktop contract.

## Licensing and notices

- Raylib uses the permissive
  [zlib/libpng license](https://github.com/raysan5/raylib/blob/master/LICENSE).
- `raylib-python-cffi` uses
  [EPL-2.0](https://github.com/electronstudio/raylib-python-cffi/blob/master/LICENSE).
- The official raylib game template and examples use zlib/libpng notices.

Generated games may keep their own chosen license, but every distribution must
include a `THIRD_PARTY_NOTICES.md`, the applicable dependency licenses, exact
versions, and source locations. Do not vendor or modify the binding unless the
corresponding EPL obligations have been reviewed. If source from an official
example is adapted rather than independently implemented, retain its notice
and mark the altered version. Asset, model, weight, dataset, and generated-file
licenses remain separate records.

## Forge tooling and materialized game entry points

Game-construction operations remain Forge-side and accept an explicit external
game-repository path:

- scaffold a clean pyray/raylib game;
- import a bundle atomically and verify every hash;
- update the versioned runtime snapshot deliberately; and
- audit template, dependency, capability, path, and authoring-boundary rules.

The materialized game exposes ordinary cross-platform Python entry points for
responsibilities it owns:

- `python scripts/verify_game.py`: validate both locks, imported catalogs,
  bundles, assets, paths, licenses, and runtime imports without `worldforge`;
- `python -m game`: list/select locked releases, run headlessly, or start the
  desktop adapter;
- `python scripts/native_smoke.py`: record a parameterized native window,
  DPI/input, drawing, and optional-audio probe;
- `python scripts/benchmark_scene.py`: measure a representative deterministic
  scene; and
- `python scripts/package_game.py`: create and independently reverify one
  private snapshot, then publish a deterministic allowlisted source archive
  with scripts, tests/allowlisted fixtures, shared assets, file hashes, and notices.

The Forge has 24 reusable, phase-scoped skills: four independent world/release
operations and twenty standalone-game phases. Scaffolding, runtime migration,
platform migration, bundle import, test harness, each gameplay family, each
pyray presentation layer, verification, optimization, and release remain
separate; there is no all-in-one game-builder skill. The exact order and exit
gates are in [GAME_IMPLEMENTATION_PHASES.md](GAME_IMPLEMENTATION_PHASES.md).

Every game-phase skill declares `Scope`, `Inputs`, `Outputs`, `Invariants`,
`Workflow`, and `Completion`. G20 only owns window/loop orchestration; G21-G25
separately own rendering, input/camera, graphics resources, one UI flow, and
audio. G30 is read-only verification, G31 changes one measured bottleneck, and
G32 packages the exact verified tree as the source ZIP only.

These skills live in the Forge only and operate through explicit `FORGE_ROOT`
and external `GAME_ROOT` paths. A generated game receives only the materialized
runtime/application template and game-local entry points. It never receives
`AGENTS.md`, `.agents/skills`, `.worldforge`, authoring source, prompts, or
production evidence. M5 owns assisted production of the final visual and audio
assets.
