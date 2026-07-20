# M5 offline asset-production pipeline

## Boundary and order

M5 starts only from a validated, canon-locked worldpack. Asset production is an
offline authoring concern: GPT orchestrates decisions and delegates bounded work
to an executor, but no provider, model, MCP server, credential, prompt, source
file, or generation workflow is part of the game runtime.

Implement the routes in this order:

1. Prove the OpenAI/Codex route for complete 2D and 2.5D production.
2. Stabilize the provider-neutral 3D contracts and GLB handoff.
3. Add the OpenAI/Codex 3D route: reference images first, then Blender MCP for
   modeling, rigging, animation, refinement, and export; run deterministic GLB
   processing and independent QA afterward.
4. Add the local route last. GPT operates reviewed Modly extensions through
   `modly-cli-mcp`; Blender MCP may refine a selected Modly result before export.

The fourth step is gated on explicit research of the Modly contract, useful
extensions per operation, extension revisions, model/weight identity, licensing,
and reproducibility. Do not invoke a local model directly or infer an extension
from a desired output.

## Target-scoped state

Every production target has one `rpg-world-forge.asset_target` and one
`rpg-world-forge.asset_manifest` v3. A target fixes `2d`, `2_5d`, or `3d`, its
delivery profile, coordinate contract, and optional runtime adapter. The
manifest separately fixes enabled routes and executors. Multiple targets for one
world remain separate and carry the same locked `world_content_hash`.

`init-assets --target-dimension` creates only the target and an
`art_direction` manifest. At that phase `bibles.visual`, `bibles.audio`, and
`inventory` are deliberately `null`; initialization must not invent creative
decisions or requirements. It enables only the `openai` route by default. The
local route is an explicit initialization-time opt-in:

```bash
worldforge init-assets build/world.worldpack.json \
  --output assets/manifest.json --target-dimension 3d --enable-modly
```

That flag adds both route `modly` and executor `modly_cli_mcp`; omitting it adds
neither. `production` and `release` require hash-bound bible
and inventory references. The internal build profile accepts only a complete
`production` manifest with no deliverable. Release requires that the exact built
deliverable has been hash-sealed into the manifest and every required asset is
fully processed; optional unproduced inventory entries are omitted from the
compiled deliverable.

The state progression is:

```text
locked worldpack
  -> target + art_direction manifest
  -> approved visual/audio bibles
  -> derived inventory
  -> reviewed per-asset specifications
  -> production requests and receipts
  -> authorized candidate selection
  -> deterministic processing and receipt
  -> license record + QA report
  -> complete production manifest (build profile)
  -> renderpack (2D/2.5D) or assetpack (3D) under assets/release/
  -> hash-sealed release manifest
  -> release-profile validation
```

A later worldpack hash invalidates the target lineage. A changed bible
invalidates dependent inventories and specifications; a changed specification
invalidates its request, selection, processing, QA, and release evidence.

## Contracts and lineage

M5 records are strict JSON objects validated by the schemas in `schemas/`:

- `asset-target`: target identity, dimension, delivery, coordinates, and runtime
  adapter (`isoworld_raylib_2_5d` or `null`).
- `visual-bible` and `audio-bible`: approved target-specific direction.
- `asset-inventory`: requirements derived from canon plus clearly separated
  manual additions.
- `asset-spec` v2: provider-neutral purpose, canonical sources, acceptance
  criteria, representation-specific technical contract, semantic slots,
  expected outputs, and allowed routes/executors.
- `asset-production-request`: one bounded operation delegated by GPT.
- `modly-capability-discovery`: sanitized, hash-bound live capability evidence
  approved before a local request executes.
- `asset-production-receipt`: successful status, sanitized typed toolchain,
  replayability, exact candidate outputs, and parent receipt hashes.
- `asset-processing-recipe` and `asset-processing-receipt`: one allowlisted
  deterministic `png_canonical`, `atlas`, `wav_pcm`, `glb_validate`, or
  `file_validate` operation
  and its verified inputs, outputs, recipe identity, and toolchain.
- `asset-license-record`: output hashes plus required asset/source/model/weight/
  dataset/output scope records, notices, and approval.
- `asset-qa-report`: passed typed checks with hash-bound evidence, an empty
  blocker list, target hash, and authorized approval.
- `asset-manifest` v3: target-scoped state and selected release lineage.
- `assetpack`: provider-neutral, runtime-only 3D GLB handoff.

Authoring contract references use `file` and `sha256`; executable processing
artifacts may additionally record byte size. Every contract has a canonical
`content_hash`. Production receipts use `parent_receipt_hashes` to preserve
derivation across generation and Blender/Modly refinement. Processing receipts
bind the selected input artifacts and exact recipe hashes. Raw MCP transcripts,
signed URLs, credentials, environment dumps, and unreviewed candidates are never
accepted as receipts.

## Build, seal, and validate

Publication is deliberately two-step. The manifest cannot claim a deliverable
before that deliverable exists, and a builder must not consume a self-declared
release. First complete the manifest in phase `production`; `build-renderpack`
or `build-assetpack` validates the internal build profile and refuses an
incomplete lineage. Write the artifact inside the manifest root, conventionally
under `assets/release/`:

```bash
# 2D/2.5D target
worldforge build-renderpack assets/manifest.json \
  --worldpack build/world.worldpack.json \
  --output assets/release/renderpack.json

# 3D target (alternative to the command above)
worldforge build-assetpack assets/manifest.json \
  --worldpack build/world.worldpack.json \
  --output assets/release/assetpack.json
worldforge verify-assetpack assets/release/assetpack.json \
  --worldpack build/world.worldpack.json
```

Then bind the exact artifact SHA-256 and content hash into the manifest using an
optimistic lock on the current production-manifest hash. Only this operation
changes the phase to `release`; validate that sealed state afterward:

```bash
worldforge finalize-asset-release assets/manifest.json \
  --deliverable assets/release/renderpack.json \
  --worldpack build/world.worldpack.json \
  --expected-hash <production-manifest-content-hash>
worldforge validate-assets assets/manifest.json \
  --profile release \
  --worldpack build/world.worldpack.json
```

Use `assets/release/assetpack.json` as `--deliverable` for a 3D target. A byte
change to the manifest or deliverable invalidates the optimistic lock or sealed
hash; rebuild and finalize again instead of editing a released record in place.

## Orchestration and executors

`generation_policy.orchestrator` is always `gpt`. A route says where authoring
runs (`openai` or `modly`); an executor says which bounded capability performed
the operation:

| Executor | Route | Bounded responsibility |
| --- | --- | --- |
| `openai_image` | `openai` | Generate reviewed 2D/2.5D candidates and 3D design references. |
| `blender_mcp` | `openai` or `modly` | Model, retopologize, unwrap, materialize, rig, animate, add collision, refine, or export 3D authoring files while preserving the owning route. |
| `modly_cli_mcp` | `modly` | Run a named, reviewed Modly extension and return auditable candidates. |
| `human` | `openai` or `modly` | Make an explicitly recorded manual contribution in that production envelope. |
| `procedural` | `openai` or `modly` | Execute a deterministic named tool or generator in that production envelope. |

Executor details belong only in typed production receipts. The manifest stores
references to those receipts, not provider payloads. Blender automation must use
bounded operations, a hash-bound reviewed script, explicit approval, and disabled
telemetry; it must not accept arbitrary code from an MCP response.

### Researched MCP baseline

The OpenAI route follows the official
[`Image generation`](https://developers.openai.com/api/docs/guides/image-generation)
guide and [`gpt-image-2`](https://developers.openai.com/api/docs/models/gpt-image-2)
model page. Because provider aliases and surface behavior can change, the
request records the approved model and the sanitized receipt records whether an
exact snapshot was visible. As checked on 2026-07-20, `gpt-image-2` rejects a
transparent background, so the validator also rejects that combination instead
of silently changing the request.

The contract was reviewed against immutable upstream snapshots rather than a
floating `latest`: `modly-cli-mcp` npm `0.1.1` at
[`88af176`](https://github.com/DrHepa/modly_CLI_MCP/commit/88af176dd44f8ea2f74922fc47a63abdfc4ebc1f),
Modly at
[`60c5b07`](https://github.com/DrHepa/modly/commit/60c5b0715fbe033be9ae54dce1b994a326b98ed6),
and Blender MCP/PyPI `1.6.4` at
[`6641189`](https://github.com/ahujasid/blender-mcp/commit/6641189231caf3752302ae20591bc87fda85fc4e).
These are research anchors, not runtime dependencies. A receipt records the
versions and revisions actually executed; a different installation must be
reviewed again.

The reviewed `modly-cli-mcp@0.1.1` wrapper knows only these executable
capabilities. “Known” does not mean installed or authorized: live discovery,
the exact discovered extension identity, and model availability must still pass.

| Wrapper key / discovered target | Canonical surface | `capability_execute` in 0.1.1 | Bounded use |
| --- | --- | --- | --- |
| `triposg` / discovered `triposg` | `workflow_run` (`workflowRun.createFromImage`) | Yes, discovery-based | Reference image to mesh. |
| `hunyuan3d` / discovered `hunyuan3d` or `hunyuan3d-mini` | `workflow_run` (`workflowRun.createFromImage`) | Yes, discovery-based | Reference image to mesh. |
| `mesh-optimizer` / `mesh-optimizer/optimize` | `process_run` (`processRun.create`) | Yes, discovery-based | Mesh-to-mesh face-count optimization. |
| `mesh-exporter` / `mesh-exporter/export` | `process_run` (`processRun.create`) | Yes, default output only | Mesh export without an explicit output path. |

`scene-mesh-import` is observable but not executable through
`capability_execute`. `UniRig` (`unirig-process-extension/rig-mesh`) is
`known_unavailable_mvp`; version 0.1.1 also refuses unknown capabilities,
UI-only nodes, generic process chains, explicit exporter output paths, and
automatic multi-step chaining. Consequently this baseline has no wrapper-known
2D/2.5D generator and no executable rigging capability. Do not describe those
as currently available Modly operations.

Forge does not turn the wrapper list into a timeless allowlist. A future or
directly discovered extension is useful only when its reviewed manifest proves
one of these exact input/output categories:

| Asset process | Required extension contract | Required next gate |
| --- | --- | --- |
| 2D/2.5D generation or edit | specification/reference images to bounded PNG/WebP candidates | deterministic PNG/atlas processing |
| 2D/2.5D enhancement | selected image to same-representation image with declared dimensions | independent visual QA |
| 3D draft/model | text or reference images to embedded-resource GLB candidate | Blender review/refinement or neutral GLB processing |
| 3D material/geometry refinement | selected GLB plus declared references to GLB with preserved axes, scale, nodes, and licenses | neutral GLB processing |
| 3D rig or animation | selected GLB to GLB with declared skeleton and stable animation names | Blender refinement when needed, then independent 3D QA |

An installed extension that merely looks related is not eligible. Missing
capability metadata, an unavailable model, floating weights, unreviewed setup,
or an incompatible license prevents `support_state: supported`; no successful
receipt is accepted and the route stops. This is why Modly remains the final
optional route even though GPT can orchestrate both MCP servers.

Before a Modly call, the request must bind the installed CLI-MCP and Modly
versions, canonical `workflow_run` or `process_run` surface, capability ID,
hash-bound live-discovery snapshot, exact extension ID/version/revision plus
manifest/workflow hashes, exact model/version/weights hash, reviewed setup, and
bounded arguments. The receipt adds the runtime `run_id`; it must repeat the
pre-approved supported state and every identity exactly.
Changing the discovery file after request creation invalidates the request.

Each production request names exactly one executable operation and its own
candidate outputs. Intermediate outputs do not have to equal the specification's
final runtime outputs: for example, a 3D specification may request
`preview=image/png` from `concept_reference`, then `model=model/gltf-binary`
from Blender. Omit `--expected-output` only when that operation directly returns
the specification's final outputs:

```bash
worldforge create-production-request assets specs/hero.json \
  --output assets/requests/hero_reference.json \
  --id hero_reference_0001 --route openai --executor openai_image \
  --operation concept_reference --expected-output preview=image/png
```

The manifest still releases only processed outputs that exactly match the
provider-neutral specification. A Blender `.blend` candidate uses the typed
`authoring_source=application/x-blender` pair and can never enter a renderpack or
assetpack.

The skill map is:

| Production phase | Executor | Operation |
| --- | --- | --- |
| OpenAI 2D/2.5D | `openai_image` | `image_generate` or `image_edit` |
| 3D reference design | `openai_image` | `concept_reference` |
| Blender model | `blender_mcp` | `model_from_reference` |
| Blender topology/UV/material/collision | `blender_mcp` | `retopology`, `uv_unwrap`, `material_bake`, or `collision` |
| Blender rig | `blender_mcp` | `rig` |
| Blender animation | `blender_mcp` | `animate` |
| Blender refinement/export | `blender_mcp` | `refine`, then `export_glb` with parent lineage |
| Modly local production | `modly_cli_mcp` | discovered `capability_execute`, `workflow_run`, or `process_run` |
| Blender refinement of Modly | `blender_mcp` | `refine`, optionally followed by `export_glb` |

Every Blender operation is a derived stage, never a root receipt. The request
must bind the exact parent-produced artifacts and at least one parent receipt:

| Blender operation | Required input roles | Permitted immediate lineage |
| --- | --- | --- |
| `model_from_reference` | `reference` | A `concept_reference` parent on route `openai`. |
| `retopology`, `uv_unwrap`, `material_bake`, `rig`, `collision` | `model` | A compatible prior Blender modeling/refinement stage. |
| `animate` | `model`, `skeleton` | A compatible `rig`, prior animation, or post-rig refinement stage. |
| `refine` | `model` | A compatible prior Blender stage; on route `modly`, the single model input must be an exact output of a direct `modly_cli_mcp` parent. |
| `export_glb` | `model` | A compatible model, rig, animation, collision, material, or refinement stage. |

For each required role, the input file and SHA-256 must be an exact output of
one of those parents. This makes reference → model → optional rig/animation →
refinement/export mechanically enforceable rather than a naming convention.

Each Forge skill owns one bounded M5 responsibility:

| Stage | Skill | Stops before |
| --- | --- | --- |
| Art/audio direction | `$define-asset-bibles` | Inventory or production |
| Canon-derived requirements | `$derive-asset-inventory` | Specification or generation |
| Per-asset contract | `$specify-asset-production` | Candidate production |
| OpenAI 2D/2.5D production | `$produce-openai-2d-asset` | Selection, processing, or QA |
| Deterministic processing | `$process-asset-deterministically` | Approval or packaging |
| 3D reference design | `$design-3d-asset-references` | Modeling |
| Blender modeling | `$model-3d-asset-with-blender` | Rigging, animation, or final export |
| Blender rigging | `$rig-3d-asset-with-blender` | Animation or export |
| Blender animation | `$animate-3d-asset-with-blender` | Final refinement/export |
| Blender refinement/export | `$refine-export-3d-asset-with-blender` | Independent processing and QA |
| Independent neutral-GLB QA | `$qa-3d-asset` | Repair or packaging |
| Fail-closed local production | `$operate-modly-asset` | Selection, refinement, or QA |
| Blender refinement of Modly | `$refine-modly-output` | Independent processing and QA |

## 2D and 2.5D route

The first complete vertical slice uses OpenAI Image for visual candidates and
Codex for inventory derivation, specifications, deterministic recipes, metadata,
and QA. Specifications define exact geometry through fields such as `width`,
`height`, `alpha_mode`, `pivot`, `directions`, `actions`, `frames`, `fps`,
`palette`, `padding`, `cell_layout`, and budgets. Spritesheets and tilesets
produce canonical PNG plus deterministic clip metadata; audio uses fields such
as `event`, `variants`, `duration_seconds_max`, `loop`, `integrated_lufs`,
`sample_rate`, `channels`, `priority`, `max_distance`, and `cooldown_seconds`,
then produces canonical PCM WAV.

Selection is a separate authorized act. `selected_candidates` is a canonical,
sorted list: every selected candidate and no unselected candidate must be an
input to the declarative recipe. This permits reviewed multi-frame atlases
without admitting an unapproved receipt output. `png_canonical` may matte-key,
crop, resize, pad, and strip
metadata; `atlas` packs canonical frames and emits deterministic clip metadata;
`wav_pcm` trims, converts channels, resamples, and peak-normalizes PCM16 audio.
For 3D, `glb_validate` verifies and republishes a runtime-safe GLB.
`file_validate` copies exactly one validated TTF, OTF, or bounded UTF-8 GLSL
file; shaders reject controls, includes, URLs, credentials, and provider/tool
markers. Processing receives an explicit common asset root, so nested recipes
bind candidate paths exactly as they appear in manifests. Recipes execute
exactly one finite operation and contain no shell snippet, provider code, or
opaque script.

The existing `isoworld.renderpack` v1 remains the 2D/2.5D runtime deliverable.
It contains only approved processed files, bindings, hashes, and the runtime
license subset. This is the only asset deliverable consumed by the current
`isoworld` reference runtime, M4 immutable bundle, and generated standalone game.

## Provider-neutral 3D handoff

The 3D target uses `assetpack_v1` with right-handed coordinates, `Y` up, `-Z`
forward, and declared units per meter. A specification fixes physical dimensions,
memory, triangle/vertex/material/texture budgets and any applicable LOD,
collider, bone, influence, or required-animation rules before an executor runs.

OpenAI Image may create orthographic and perspective reference sheets. Those are
design evidence, not runtime geometry. Blender MCP then performs distinct model,
rig, animate, refine, and export operations so each receipt has one reviewable
responsibility; `glb_validate` and independent QA run outside Blender/MCP. A
`.blend` file is an authoring source only. The runtime handoff is GLB with the
declared coordinate contract, bounded metrics, stable node and animation names,
embedded resources, and zero external URIs.

`rpg-world-forge.assetpack` contains only runtime-safe GLB and approved auxiliary
files, semantic bindings, hashes, metrics, and target/world identity. Required
runtime license and notice files travel beside it in the immutable handoff.
It is engine-neutral: it contains no `.blend`, Modly extension, Blender/MCP
configuration, provider/model record, prompt, receipt, or Forge skill.
It is handed to a separate 3D implementation or runtime-adapter phase; the
current pyray reference game and `export-bundle` do not consume assetpacks.

## Local Modly route

Enable `modly` only with the explicit `init-assets --enable-modly` opt-in and
after recording which extension implements the requested operation. Before
execution, validate its revision, workflow contract, model/revision, weights
hash, output contract, licenses, reviewed setup, and hash-bound live-discovery
snapshot. GPT invokes the package through `modly-cli-mcp`, validates the
returned production receipt against that exact request, and selects a candidate
under the same specification used by every other route.

The 0.1.1 baseline currently provides only the 3D image-to-mesh,
mesh-optimization, and restricted mesh-export capabilities listed above. Modly
may serve 2D, 2.5D, rigging, animation, or other 3D operations only if a later or
directly discovered reviewed extension actually proves the required contract;
those are future categories, not current baseline claims. For 3D, a selected
output may become the direct parent of a bounded Blender `refine` receipt. The
final GLB and assetpack remain indistinguishable at runtime from an
OpenAI/Blender or human/procedural result.

## Release gate

An asset is releasable only when it has a specification reference, successful
production receipt lineage, authorized selected candidate, deterministic
processing receipt, complete license record, approved QA report, typed outputs,
matching SHA-256 values, and semantic bindings. QA must exercise the declared
delivery profile: renderpack/raylib integration for 2D/2.5D or neutral GLB
inspection for 3D.

For 2D/2.5D, the current immutable bundle and reference game receive only the
worldpack plus the sealed renderpack and its approved runtime files/notices. For
3D, M5 hands the sealed, engine-neutral assetpack to a separate implementation
or adapter; it does not imply support in the reference bundle or game. Neither
handoff may contain the asset manifest, production contracts, bibles,
inventories, specifications, authoring sources, receipts, recipes, references,
candidates, provider/MCP configuration, model weights, credentials, or
generation tools.
