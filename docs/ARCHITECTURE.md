# Architecture

## Primary boundary

```text
Forge repository                       world-authoring repository
(skills, templates, tools) --operates--> source + .worldforge + assets
                                                   |
                                      validate, compile, approve
                                                   |
                                                   v
                                      immutable runtime bundle
                                      (worldpack, renderpack,
                                       processed assets, licenses)
                                                   |
                                      verified external import
                                                   |
                                                   v
independent game repository <---------- copied, hash-locked data
(pyray/raylib code, UX, saves, packaging; no authoring control plane)
```

## Forge Studio application-service boundary

Forge Studio is an authoring-time control plane under `worldforge`; it is not a
runtime feature and never enters a worldpack, renderpack, assetpack, immutable
bundle, or generated game. The first service boundary is deliberately
provider-free:

```text
sandboxed React renderer
        |
        | fixed typed preload operations; validated top-frame IPC
        v
Electron main supervisor -- serves --> rwf-studio://app static artifacts
        |
        | bounded strict NDJSON request/response/error/event envelopes
        | over stdio; shell=false; fixed executable and arguments
        v
worldforge.studio application service
        |-- public schema validators
        |-- workspace boundary inspection
        |-- revision-guarded PNG/WAV preview leases
        |-- SQLite registry, events, and job state
        `-- approved, base-hashed source changesets
                |
                `-- world lifecycle lock + durable external apply journal
```

Electron main also owns a distinct Codex app-server boundary. It binds one
registered canonical world root as the child working directory, creates a
dedicated `CODEX_HOME`, and starts exactly Codex 0.144.6 with
`app-server --stdio --strict-config`. The generated config fixes approval to
`never`, sandboxing to read-only, network/web search off, empty shell
inheritance, and one Forge MCP server. That stdlib MCP child is bound by argv to
the Studio data directory and workspace ID and exposes only changeset stage,
get, and list. It uses a secondary store attachment that never migrates the
database or performs primary-service job or journal recovery.

Canonical project files remain the source of truth. The SQLite database, staged
content-addressed blobs, and apply journals live only under the explicit
`--data-dir`; they are never placed in the Forge, world, bundle, or game roots.
The database uses foreign keys, WAL, FULL synchronous writes, and a bounded busy
timeout. A service restart marks interrupted `running` jobs as `orphaned` and
records that transition as an event.

The service's read-only authoring boundary is manifest-authorized rather than a
general filesystem browser. `source.list` and `source.read` expose only the
manifest, world document, and collection documents named by the loaded source
manifest. Paths are portable, depth/count/byte bounded, collision checked, and
read through the same pinned no-link/no-hardlink boundary as changesets. The
reported hash, UTF-8 content, and strict JSON value derive from one stable byte
snapshot. `world.validate` and `world.analyze` construct the existing
`SourceProject` in memory and reuse the established release validator and
narrative analyzer; they never call report writers. `workspace.overview`
projects identity and lifecycle status without returning absolute roots.

A workspace requires the active Forge repository and one canonical v2 world
repository. Optional game and bundle roots must pass their existing standalone
boundary inspectors. Symlinked roots, repeated filesystem identities,
casefold/NFC aliases, and nested or overlapping responsibilities are rejected.

Studio changesets are not arbitrary filesystem patches. New version 2 records
permit creation, replacement, or deletion of standalone UTF-8 regular files
only beneath `source/`, with portable paths and existing parent directories.
Each operation retains exact base and proposed snapshots, when present, by
SHA-256 outside the repository. A canonical review hash commits to the ordered
path, operation, hashes, and byte sizes. Exact bounded text hunks and optional
strict-JSON Pointer changes are reconstructed only from those owned snapshots;
they never reopen the mutable workspace. Version 1 records remain readable and
actionable, but expose a typed unavailable review because their base bytes were
not retained.

Approval and apply are separate state transitions. Every v2 action must echo
the exact reviewed hash. Apply atomically claims the durable `applying` state
before publishing a journal or touching source files, which blocks rejection
and concurrent application. Apply rechecks roots, parents, link counts,
identities, hashes, sizes, and base state while holding the existing world
lifecycle lock. The public path
schema names the `rpg-world-forge-portable-source-path` format and carries the
NFC, traversal, reserved-name, trailing-dot/space, and UTF-8 component limit
policy consumed by the shared Python validator. POSIX mutations are relative to
a verified directory-descriptor chain; Windows holds no-delete handles for the
entire directory chain. Visible identities are rechecked through the durable
file and SQLite commit. Version 2 journal intent binds the changeset version,
review hash, and ordered operations; every reserved stage name is durable before
a stage is created. Startup validates that identity before recovery and releases
an orphaned `applying` claim only when no journal was published, the point before
which source mutation is impossible.
Same-directory exclusive link/unlink publication and the identity/hash journal
provide crash recovery and rollback. POSIX flushes file and directory metadata;
Windows uses write-through journal replacement plus `FlushFileBuffers` on the
affected directory handle. If either platform cannot expose its required
durability primitive, apply fails closed instead of claiming an equivalent
guarantee or overwriting an unowned path.

Managed v2 durable jobs execute only four closed, read-only operations:
`asset.receipt.validate`, `assetpack.verify`, `runtime.headless`, and
`runtime.replay`. The original broad v1 records remain valid for read, recovery,
and cancellation, but are never executable or retried. One FIFO scheduler owns
a secondary SQLite connection and atomically claims at most one eligible queued
v2 job. Each claimed job runs in a fixed child worker with bounded strict JSON
pipes, a sanitized environment, workspace-derived roots, stable standalone-file
proofs, a timeout, and process-tree termination.
Progress and cancellation intent are durable events. A service shutdown reaps
its active child before marking the job orphaned; restart recovery never retries
an orphan automatically. Public transitions cannot impersonate the executor.

The Electron renderer can list and cancel jobs and create them only through four
fixed named capabilities: receipt validation, assetpack verification, headless
runtime, and replay runtime. Electron main maps those capabilities to the four
managed operation literals, validates each correlated v2 reply and echoed input,
and never exposes arbitrary `job.create` or operation dispatch. Codex proposals
remain staged changesets and cannot approve or apply themselves. Provider/model
execution, Blender, Modly, Ollama, arbitrary commands, file watching, and M6
presentation work remain outside this boundary.

The renderer's Game cockpit is a defensive view over three of those named
capabilities: `assetpack.verify`, `runtime.headless`, and `runtime.replay`.
Forms start with blank portable workspace-relative paths; headless ticks are
integers from 0 through 1,000,000. Each request captures the selected workspace,
workspace generation, and a local request token, so a late reply cannot enter a
new workspace view. The immediate correlated queued record is merged before the
bounded job/event lists refresh. Projection admits only current-workspace v2
records with exact operation-specific inputs and results; legacy, malformed,
other-operation, and other-workspace rows are absent. Progress is a running-only
observation associated by workspace, job entity, and event identity. A running
job without such evidence is indeterminate, and terminal jobs do not invent a
final percentage. The main Game view renders controlled structured facts and
error copy rather than raw JSON, details, stderr, or roots. It verifies an
existing replay only and provides no recording, generated-game slot, launch,
bundle/package, native renderer, or M6 3D capability.

Asset previews are a separate three-method named boundary, not generic file
access. The renderer can provide only a workspace/revision/entry tuple to open,
then an opaque handle and bounded sequence to read or close. Python owns one
preview manager, immutable snapshot lifetime, quotas, and shutdown cleanup.
Electron main validates handle, authority, sequence, cumulative length, EOF,
and SHA correlations, decodes canonical base64 immediately, and removes the
encoded field before returning a fresh `Uint8Array` through preload. Only PNG
and WAV are authorized; no preview UI is part of this boundary change.

Development selects Python and Codex through explicit absolute
`RWF_STUDIO_DEV_PYTHON` and `RWF_STUDIO_DEV_CODEX` paths. A package may select
them only through the closed runtime manifest and pinned protocol provenance in
Electron resources; it never searches `PATH`. Packages without both native
runtimes report the bridge unavailable. Renderer artifacts use the same registered custom
scheme in development and release, with no localhost or HMR server.

AI is not a game subsystem. It does not decide dialogue, quests, routes, or
actions during play. It may propose authoring material, but that material must
be reviewed and compiled before it reaches the runtime.

## M6 runtime-composition contract boundary

M6 composes unchanged M5 artifacts before it implements or selects a runtime:

```text
worldpack v1-v5 ----+
renderpack v1 ------+--> hash-bound composition --> compatibility report
assetpack v1 -------+          |                          |
capability catalog -+          v                          v
presentation profile       declared/verified adapter   static checks
```

Six profiles define only the world planes: 2D, 2.5D, 3D, 2D over 2.5D, 2D over
3D, and 2.5D over 3D. UI and audio are orthogonal planes. The composition binds
each selected semantic slot to one plane, pack, asset, and representation.
Renderpack v1 remains unchanged; its selected bindings receive 2D/2.5D
classification from the outer composition. Assetpack representation must match
the sealed binding exactly.

The adapter document is a requirement declaration, not an implementation
locator. It contains no module, command, executable, provider, model, or MCP
endpoint. Only `verified` state can satisfy compatibility, and verification
still requires all pack, profile, platform, runtime API, capability, semantic
ownership, and world-identity checks. These contracts do not claim a native 3D
runtime, collision, animation, composed bundle, package, or M6 release.

[ADR-0016](decisions/0016-runtime-composition-contracts.md) records this seam.

### Immutable composed bundle

Once an exact static registry can satisfy a verified adapter declaration, the
Forge can seal the four M6 contracts, a freshly recomputed compatibility
report, the unchanged selected worldpack/renderpack/assetpack payloads, and
approved notices into `rpg-world-forge.composed_runtime_bundle` v1. The bundle
has an exact portable tree and file inventory, is published without
replacement, and is loaded only through private identity-owned snapshots.

The bundle stores declarations and compatibility evidence; it contains no
Python locator, command, provider, model, MCP endpoint, authoring receipt, or
workflow. Loading resolves an exact code-owned registry value but never invokes
it. Therefore a valid composed bundle is not evidence of native rendering,
physics, animation, playability, performance, packaging, or M6 release
readiness. [ADR-0017](decisions/0017-immutable-composed-runtime-bundle.md)
records the ownership and publication boundary.

### Bounded pyray GLB proof

The first code-owned M6 adapter is deliberately narrower than every committed
3D presentation profile. `pyray_3d_v1` accepts one exact snapshot-relative
skinned GLB, one `actor:<id>` binding, one named animation, one uniform scale,
and one presentation layer. Pure functions map the immutable `RenderState`
grid, select animation frames from `RenderState.tick`, transform finite local
bounds, and return an admitted picked cell. They never dispatch an action,
reduce `WorldState`, or treat model bounds as collision or navigation data.

The native session resolves only a portable payload path through the owner of a
private bundle snapshot. It verifies the declared size, SHA-256, triangle and
loaded-byte budgets before opening a hidden window, then uses path-only
`load_model`, the CFFI animation-count pointer, exactly one animation name and
positive compatible skeleton/keyframes. The pointer is the binding's audited
signed `int *`, and the synthetic one-second animation's two source samples
become exactly 61 binding keyframes at raylib's 60 Hz glTF sampling rate.
Cleanup unloads the complete animation array before the model and closes the
window last. The resolver remains owned until cleanup succeeds.

The declaration proves only `animation_gltf`, only on Linux x86_64, and omits
assetpack consumption, 3D/mixed world presentation, collision, packaging, UI,
audio, and performance claims. All current 3D/mixed profiles require
`collision_gltf`, so they remain incompatible. The pinned Python distribution
is exactly `raylib==6.0.1.0`; ABI inspection records that its bundled constants
identify the header as `6.1-dev` and RLGL as `6.0` rather than relabeling that
observation as stable raylib 6.0.

Ubuntu x86_64 CI must execute the synthetic skinned-GLB hidden-window smoke.
Windows CI records distribution/header/RLGL/function-surface evidence only and
explicitly does not claim native 3D verification. This slice is not a playable
3D game, a standalone package, a composed-bundle consumer, or M6 readiness.
[ADR-0018](decisions/0018-bounded-pyray-3d-proof.md) records these limits.

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

Navigation, clock advancement, schedules, needs, goals, construction,
production, interactions, abilities, narrative events, delayed consequences,
quests, dialogues, scenes, and reservations stay inside reproducible state
transitions. Persistence serializes only `WorldState`; rendering still consumes
a separate frozen snapshot.

```text
tick -> clock -> needs/completions/consequences -> goals -> schedules -> routes
input -> move/navigate/interact/use_ability -> reducer -> new WorldState
input -> build/produce/transfer -> reducer -> domain events -> new WorldState
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

The worldpack owns simulation and narrative semantics. Version 4 added typed
resource, need, goal, stockpile, construction, production, and consequence
collections. Version 5 adds explicit runtime API/features, BCP47 locales, and
typed personal campaigns while retaining v1-v4 loading compatibility. The renderpack owns the
replaceable mapping from semantic slots such as `actor:hero` or
`tile_type:ground` or `construction:workshop` to processed textures,
deterministic clipsets, fonts,
shaders, SFX, and music. The asset-production manifest is never loaded by the
game because it may contain recipes, model identifiers, extension workflows,
references, and licensing evidence.

Processing recipes remain format v1. The pure recipe validator resolves only
the explicitly supplied recipe beneath an authoritative asset root, verifies
its hash-bound inputs and closed operation contract, and performs no media
decode, processing, or writes. New processing receipts use format v2 and bind
that exact recipe path, raw SHA-256, canonical content hash, operation, input
IDs/files/hashes, and output filename/role/media lineage. Manifest validation
passes its own asset root explicitly. Receipt v1 remains readable as an
identity-only legacy record; it never gains inferred recipe authorization.

## Creative-process control plane

Every generated **world-authoring repository** contains `.worldforge/` and a
tailored `AGENTS.md`. GPT reads its status, works only on the active phase, and
submits a phase report with deliverables, decisions, blockers, and validation
evidence. `complete-phase` prevents phase skips or evidence-free completion.
Optional subagents claim non-overlapping paths; only the lead GPT integrates
canon.

The Forge repository is the only home for reusable `.agents/skills`. The game
repository has no `AGENTS.md`, `.agents/`, `.worldforge/`, source canon, prompts,
phase reports, or asset-production evidence. Forge-side agents may construct or
update a game through explicit external paths, but no agent control plane is
materialized into the game.

`worldforge audit-game <game-repo>` enforces this seam. It rejects known agent
and workflow paths, editable source roots, authoring-only JSON formats,
`worldforge`/Modly/AI imports, and corresponding Python project dependencies.
Game materialization, runtime/platform migrations, and bundle imports must pass
this command before handoff.

The generated game has two independent locks:

- `runtime.lock.json` inventories the complete vendored `src/isoworld` snapshot;
- `platform.lock.json` records the Python range, exact binding distribution and
  version, backend, native raylib API, and `pyray` import boundary.

The platform contract is enforced by a resolver-facing artifact:
`requirements.lock` lists the exact dependency/build graph consumed by CI and
must agree with both the platform lock and `pyproject.toml`.

Game-specific implementation lives under `src/game`. Agents never edit the
locked `src/isoworld` tree in place: reusable runtime changes are implemented
and tested here, then replace the whole snapshot through an optimistic-hash
Forge operation. Imported bundles are similarly replaced only by importing a
new stable SemVer release. Platform migration is a separate phase that updates
its lock, exact dependency, CI, notices, and native evidence together.

## Repository responsibility matrix

| Concern | Forge | World authoring | Runtime bundle | Game |
| --- | --- | --- | --- | --- |
| Agent skills and generic prompts | Owns | Uses externally | Never | Never |
| Canon and editable narrative source | Never | Owns | Compiled only | Never |
| Asset candidates and production evidence | Generic tools only | Owns | Never | Never |
| Worldpack, renderpack, processed assets | Builds/verifies | Releases | Owns immutable copies | Imports locked copies |
| Pyray/raylib application, UX, saves, packaging | Templates/reference | Never | Never | Owns |
| Runtime/platform locks and migrations | Builds and operates externally | Never | Declares requirements only | Owns locked copies/config |

## Non-negotiable contracts

- Runtime does not import `openai`, `anthropic`, `transformers`, `langchain`,
  `litellm`, `ollama`, `llama_cpp`, or equivalent packages.
- Assets and worldpacks are distributable without credentials.
- One seed and the same action sequence produce the same state.
- Saves and replays must match the exact compiled world content hash.
- No two actors may occupy or reserve the same destination cell in one tick.
- Construction footprints cannot overlap actors, terrain, or other structures.
- Stockpiles remain non-negative and cannot exceed declared capacity.
- Goal selection is stable by priority, hierarchy depth, and ID.
- Delayed consequences carry absolute due minutes and survive save/replay.
- Water is not walkable or arable.
- Rock and vegetation are not arable by default.
- Manual tile decisions take priority over generated decisions.
- Each world declares its visible language and localization policy.
- Release worldpacks contain no `TODO`, `TBD`, template braces, or broken refs.
- Runtime contains no names, roster sizes, or lore from a particular game.
- Local model execution is allowed only through an external Modly extension;
  the OpenAI route is likewise external authoring, never runtime inference.
- Game repositories live outside the Forge, pass the clean-game boundary
  audit, and distribute only game-owned runtime code plus approved immutable
  bundles and processed assets.
- Runtime bundles likewise use standalone external roots; neither export nor
  import accepts a bundle nested in Forge, world, bundle, or game storage.
