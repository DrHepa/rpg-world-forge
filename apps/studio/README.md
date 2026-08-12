# World Forge Studio shell

This app-local Electron/React project owns two separate local stdio boundaries:
the provider-free Python Studio service and an optional, workspace-bound Codex
app-server 0.144.6. Codex can reach the Forge only through an argv-bound MCP
process exposing three changeset staging/read tools. It cannot approve or apply
changesets. The Python service already provides bounded workspace overview,
manifest-authorized source inspection, release validation, and in-memory
narrative analysis methods. It also exposes revision-bound, manifest-authorized
asset catalog listing and metadata/text inspection, plus bounded PNG/WAV
preview leases under the same revision authority. Electron maps those reads
and the human review boundary to exact named preload methods; catalog pages have
a main-owned limit of 64, staging is only a one-file base-hashed replacement,
and no generic RPC is exposed. Preview calls accept no paths, media types,
offsets, sizes, encodings, or renderer-supplied base64. Main validates each
fixed 64 KiB protocol chunk, immediately decodes canonical base64, and exposes
only a fresh `Uint8Array` through preload. The World cockpit can
stage an explicit dirty syntax-valid draft, open bounded immutable v2 text and
JSON Pointer evidence, and require separate human confirmations for approval,
rejection, and apply. Approval never auto-applies; v1 reviews remain readable
but cannot be freshly approved or applied without exact evidence. Asset
catalog reads are presented in an accessible read-only Assets cockpit. It
lazy-loads the first 64-entry revision snapshot, replaces pages under
revision-bound next/previous controls, keeps category filters page-local, and
renders bounded semantic JSON, escaped GLSL, or verified PNG/WAV/font/GLB
metadata. The cockpit never reconstructs paths or creates image, audio, font,
WebGL, or 3D runtime previews. The accessible Game cockpit uses only the three
existing named jobs for assetpack metadata/handoff verification, reference
headless ticks without graphics, and verification of an existing replay. It
accepts blank portable workspace-relative paths, correlates replies to the
selected workspace generation, and defensively presents only valid current-
workspace v2 records. Results are structured and bounded; raw payloads, stderr,
absolute roots, replay recording, generated-game slots, launch/play controls,
bundle/package mutation, and M6 3D behavior are not exposed. Its bounded job
view is explicitly not chronological, and percentages appear only for running
jobs with a reliably associated observed progress event. Asset production,
Modly, Blender, file watching, and native playtests remain later batches.

Generic creation workspaces use additive Studio protocol v4 for trusted,
read-only evidence and fixed durable creation jobs. Its closed surface includes
artifact/evidence inspection, job create/get/list/cancel/recover, event listing,
creation-output-grant create/get/revoke, and ephemeral creation-asset preview
open/read/close. Compilation, artifact admission,
deterministic asset processing, asset-release sealing, and runtime composition
each have a distinct fixed preload/IPC method. Runtime-bundle and game-
materialization-bundle publication use separate fixed methods and grant kinds;
the renderer cannot select a generic operation, adapter descriptor, grant kind,
or filesystem path.

Protocol v5 has exactly 18 transport methods. The durable
creation-job and isolated-worker contracts advance through v12 and admit the
closed creation operations through headless evidence publication. Output grants
are persisted as v6 for v5 clients; legacy v4 `creation_output_grant.list` still
projects only v1-v5 grants, while v5 listing exposes the persisted v6 rows.
Output-grant authority is pathless outside main and supports the closed
`create`, `get`, `list`, and `revoke` method surface. Previews remain published
v1 plus the v2 pre-release QA candidate opened by main-owned authority. The v5
handshake exposes only `asset_authority_reviews`, `asset_release_authority`,
`runtime_headless_authority`, and `creation_preview_pre_release` as activated
authority capabilities. The renderer now has fixed, visible controls for
compilation; asset processing and sealing; bounded PNG/WAV preview; runtime
composition and bundle publication; materialization-bundle and standalone
publication; package creation; explicit extraction; and headless evidence
publication. These are typed controls over main-owned authority, not a generic
RPC or proof of native, hosted, packaged-shell, or release readiness.
Electron main alone opens native save dialogs for absent output targets and
sends paths to private Python requests. The renderer selects main-owned actions
and grant IDs, never raw filesystem paths. Renderer-visible grants, jobs,
results, and events remain pathless and are bound to exact root generation,
source revision, workflow hash, artifact-snapshot hash, and output-grant
generation. Successful sealing publishes a reusable `world-forge.assetpack` v1
directory without replacement and commits the same immutable publication
identity to the grant, job result, success event, and candidate registry.
Interrupted visible publication becomes explicit recovery state; resume and
rollback verify retained identities and hashes, and ambiguous foreign bytes are
preserved rather than removed. The fixed `runtime.compose` job accepts exact
gamepack, inventory, assetpack, and published-grant identities. Python main
re-verifies the published directory, stages its exact retained bytes, and
derives the runtime snapshot and adapter registry exclusively from code-owned
packages. It commits snapshot, registry, composition, and evidence-free support
report as candidate artifacts. Optional feature gaps remain explicit without
blocking candidate materialization; missing required features fail closed, and
the support report remains release blocked until native and packaging evidence
exists. The fixed `runtime.bundle.build` job seals that exact composition into a
published runtime bundle. The fixed `game.materialization.bundle.build` job then
derives its runtime implementation and four audited Linux/Windows Python locks
from the exact published runtime bundle, publishes without replacement, and
commits the same lineage-bound manifest as a candidate. It does not execute a
game or claim native/package evidence, so release remains blocked. The
fixed `game.materialize` job uses creation-job/worker v7 and output grant v4.
Its isolated worker receives only a staged, hash-bound materialization bundle
and derives the deterministic standalone manifest without a target path. The
trusted coordinator then re-verifies the exact published source grant and owns
canonical no-replace publication, payload-lock tree binding, restart recovery,
and rollback for one external `standalone_game_directory`. Public requests,
jobs, grants, events, and results remain pathless; publication records
`release_blocked` and `native_execution_unverified` rather than claiming native
or packaging evidence. The fixed `game.package` job uses creation-job/worker v8
and output grant v5. Its
isolated worker rebuilds one canonical `world-forge.game_package` v1 manifest
and one fixed private archive from exact staged standalone bytes; the archive
never crosses the public protocol. Electron main alone selects one absent
portable `.wfgame` target, while the trusted coordinator re-verifies source,
archive, parent, and file identities before committing the same manifest,
archive SHA-256, and byte size to the pathless grant/job/event/candidate
projection. Interrupted unbound visibility fails closed. Package extraction is
an additive fixed `game.package.extract` job using creation-job/worker v9. Its
isolated worker verifies the exact published v5 package grant and emits only
deterministic `world-forge.game_package_extraction` v1 evidence. Electron main
alone selects an absent `standalone_game_directory`; the coordinator invokes
the canonical extractor, binds its journal/stage/published identities, and
re-verifies the extracted tree before committing the pathless result. Restart
resume and rollback use the same authority-bound canonical recovery boundary.
The fixed preload/main API exposes only the operation-specific controls above;
it adds no generic operation selector, renderer-visible filesystem path, or
implicit extraction.

The additive `creation_preview.*` methods inspect only one current candidate
`world-forge.assetpack` v1 asset whose exact published output-grant generation
still matches. The Python service integrally verifies the sealed pack and its
regular single-link portable tree, then owns an ephemeral snapshot of exactly
one PNG or PCM16 WAV up to 64 MiB. Reads are fixed sequential 64 KiB chunks;
only the immediately previous chunk may be replayed. Handles are opaque,
quota- and lifetime-bounded, and closed by explicit close, expiry, reaping, or
service shutdown. NDJSON carries canonical base64, but no native path,
renderer-chosen offset/range/media type, provider data, or authoring prompt.
Electron exposes fixed preload methods only. The renderer provides bounded PNG
and WAV preview controls backed by those leases; it never receives a path or
chooses a byte range or media type.

The Assets, Compatibility, and Materialize tabs lazy-load the complete active
artifact closure, expose redacted semantic projections, and keep authoring,
compilation, assets, adapter, per-platform execution, packaging, and release as
independent states. The views expose the fixed creation, preview, runtime, and
materialization controls described above but no provider production, arbitrary
binary reader, or generic request API. Every downstream control remains gated
by exact current candidate, grant, and revision authority.
Tabs that are irrelevant for the validated asset or runtime evidence remain
visible but accessibly disabled; the renderer does not infer applicability from
project kind, raw QA, or optimistic authoring status.

Studio also ships an isolated validator and integral reader for the additive
`world-forge.assetpack` v1 D3 directory. It is intentionally separate from the
exact fourteen-format D1/D2 authoring dispatcher. Structural validation returns
only a frozen branded document; the integral reader returns `status: sealed`
only after the canonical manifest, exact regular single-link tree, payload
hashes/sizes/media constraints, and UTF-8 notices all match one retained
capture. The packaged runtime descriptor is v2: its `contract_formats` remain
the same fourteen authoring formats, while `sealed_pack_formats` declares only
`world-forge.assetpack`. This does not claim adapter compatibility or execution.
The packaged JavaScript GLB inspector currently accepts only bounded URI-free,
image-free, dense-accessor GLB 2.0 using the unlit or mesh-quantization
extensions; textured/data-URI, sparse-accessor, camera/light, morph-weight
animation, or other GLB shapes fail closed rather than receiving sealed Studio
evidence. The Python D3 verifier remains authoritative for the complete D2 GLB
media contract, and a shared mutation corpus prevents the documented Studio
subset from accepting node, scene, accessor, transform, or animation semantics
that Python rejects.

Studio also ships a separate
`world-forge.studio_internal_game_runtime_bundle_validator` v1 entry for
`world-forge.game_runtime_bundle` v1. It validates the canonical root manifest,
exact no-link tree, nested D3 pack, gamepack/runtime contract lineage, retained
runtime snapshot bytes, transfer bindings, notices, and code license. Its only
positive result is `integrity: valid`; it preserves `pre_execution`,
`release: blocked`, and `supported: false`. It does not join or change the
existing six-format generic runtime validator and does not constitute Python,
raylib, packaging, save/replay, or native-platform evidence.

The separate generic persistence inspector recognizes
`world-forge.game_save` v1, `world-forge.game_replay` v1, and
`world-forge.persistence_generation` v1. It owns and deeply freezes strict JSON,
checks canonical hashes plus structural save/replay coherence, and verifies the
generation kind, slot, sequence, sorted parents, operation, embedded payload,
and exact payload hash. Source, built CJS, and retained-ASAR smoke use all
sixteen canonical persistence documents and reject plain or self-resealed
tampering. Gameplay replay and generation-DAG semantics remain authoritative
in isolated packaged Python through the dedicated verification commands.

The generic headless inspector recognizes
`world-forge.game_execution_script`,
`world-forge.headless_execution_receipt`, and
`world-forge.headless_evidence_set` v1. It owns and deeply freezes strict JSON,
checks schema/coherence plus canonical IDs and hashes, and returns only
`structurally_valid`. It never interprets gameplay or upgrades support.
Execution and integral evidence verification are routed through isolated
packaged Python via `verify-game-headless` and
`verify-game-headless-evidence`. Source, built CJS, and retained-ASAR smoke
preserve `native_execution: false`, `release: blocked`, and
`supported: false`.

Studio also registers a separate structural inspector for
`world-forge.game_package` v1. Source, built CJS, and retained-ASAR smoke verify
the closed package manifest and prove that full archive semantics remain owned
by isolated packaged Python. The matching CLI boundary is `package-game`,
`verify-game-package`, `extract-game-package`,
`recover-game-package-extraction`, and
`rollback-game-package-extraction`; none is exposed as a generic renderer RPC
or as an implicit UI mutation. The fixed `game.package` preload call described
above is a narrowly typed export capability, not that generic RPC and not an
extraction control.

Packaging and extraction preserve the standalone logic/runtime/asset lineage
but do not create execution evidence. Privileged Forge publication never runs
game-local `scripts/verify_game.py`; it is an explicit post-extraction check in
a disposable standalone context. Studio therefore keeps `release: blocked`
until headless, replay, packaging, and every declared platform's hosted native
evidence are independently present.

## Development

Use Node 24.14.1 and npm 11.13.0. Install the Forge into a supported Python 3.11
or 3.12 environment and install Codex 0.144.6, then provide both native
executables explicitly. The app never searches `PATH`:

```bash
cd apps/studio
npm ci
WORLD_FORGE_STUDIO_DEV_PYTHON=/absolute/path/to/venv/bin/python \
WORLD_FORGE_STUDIO_DEV_CODEX=/absolute/path/to/codex npm start
```

`WORLD_FORGE_STUDIO_DEV_PYTHON` is canonical. The deprecated
`RWF_STUDIO_DEV_PYTHON` alias remains readable for compatibility. If both are
set they must resolve to the same non-empty value; unequal dual values fail
closed instead of choosing one.

There is no renderer development server. Vite writes static files and Electron
serves them through `rwf-studio://app` in development, tests, and packages.

Run the local gates with:

```bash
npm run check:generated
npm run lint
npm run typecheck
npm test
npm run build
npm run verify:generic-asset-runtime
npm run verify:game-runtime-bundle
npm run verify:generic-headless
```

The generic runtime verifier creates deterministic puzzle and branching-
narrative D3 directories only under the operating-system temporary root,
validates them through both packaged CJS and ASAR code, and requires same-size
tampering to fail. It does not contact a provider, start a model, or publish a
pack.

An optional provenance regeneration check does not start app-server or a model:

```bash
node scripts/codex-protocol.mjs --check-generator /absolute/path/to/codex
```

An optional unpacked-shell check must use an explicit output directory outside
the repository. It never launches Electron:

```bash
npm run package:dir -- \
  --output /absolute/external/studio-shell \
  --target linux-x64
npm run package:verify -- \
  --path /absolute/external/studio-shell/linux-unpacked \
  --target linux-x64
```

Use `--target win32-x64` for the Windows layout. The packaging wrapper accepts
only a nonexistent absolute output whose parent already exists outside every
lexical or resolved alias of the repository. It reserves that exact directory
before starting the build and passes the same retained binding to
electron-builder through both its required environment macro and command-line
override. Linux packages through the retained directory descriptor under
`/proc`; Windows keeps a stdlib guard process and no-delete handle chain alive.
The requested name is rebound to the retained identity before success, so a
replacement cannot redirect build output into the repository. Electron-builder
`afterPack` only flips the audited Electron fuses. After electron-builder exits,
the supported `npm run package:dir` parent wrapper writes the exact `shell_only`
inventory at `resources/shell-package-manifest.json` through the hardened
snapshot backend, verifies the package, and finalizes the reserved output
identity. Invoking raw `electron-builder` directly is unsupported: it leaves an
incomplete, unverified directory without the wrapper-owned manifest or
finalization, so that directory must never be published or treated as a
package. On Windows only, manifest publication permits one immediate retry when
the first backend terminates before its report with either exact code:
`windows_snapshot_package_sharing_conflict` or
`windows_snapshot_source_sharing_conflict`. The setup code
`windows_snapshot_setup_sharing_conflict` is non-retryable and fails closed.
The retry starts only after that failed backend process and its private
snapshot handles are fully reaped; failed snapshot files remain preserved as
described below. It reuses the same retained output identity and exact inputs,
and performs no sleep, rebuild, or re-reservation. Any existing manifest or
callback, publication, finalization, cleanup, timeout, protocol, or second
sharing failure remains fail-closed and is never retried. The verifier pins
the package tree, validates the ASAR
entrypoints, compares the runtime manifest, Codex protocol provenance, runtime
source contract, and schemas byte for byte, and rejects missing, altered,
linked, replaced, or extra resources. The pinned Electron 43.2.0 Windows shell root
includes exactly the literal `dxcompiler.dll` and `dxil.dll` DXC pair; missing
or additional root files fail closed. Each process build first removes its
generated process tree and Vite empties its renderer tree. Electron-builder can
select only the 17 exact clean source/build files plus its sanitized
`package.json` (18 ASAR entries total before directories); the verifier pins
those clean source trees and requires identical ASAR file,
directory, size, and hash inventories. Extra vendors, executables, runtimes, or
stale outputs therefore fail instead of being hidden by `shell_only`. It does
not launch the GUI.

This is not a self-contained release: Python and Codex runtimes are absent,
`release_ready` is false, and redistribution remains blocked by the seven
checked-in provenance blockers. Linux uses descriptor-relative no-follow
reads. Windows uses the stdlib Python backend and the audited Forge
`NtCreateFile`/no-delete-sharing handle primitives while Node performs only
static ASAR and fuse inspection against retained private snapshots. Set
`WORLD_FORGE_STUDIO_BUILD_PYTHON` to an absolute supported Python 3.11/3.12
executable for Windows build and verification; the tool never searches `PATH`.
The deprecated `RWF_STUDIO_BUILD_PYTHON` alias is accepted only when it is
unset or byte-equal to the canonical value. Windows
deletes only its two private snapshot files through their still-retained
delete-capable handles. On a successful finalization it records the snapshot
root identity, closes every package, source, snapshot-chain, and guard handle,
then reopens that exact root without delete sharing. The backend revalidates
that the handle still names the same non-reparse directory, proves through
handle-relative enumeration that it is empty, and marks it for deletion through
that retained handle before emitting the final acknowledgement. It never calls
path-based or recursive cleanup. A failed or uncertain snapshot, identity
check, enumeration, disposition, or handle close is preserved fail-closed and
withholds the acknowledgement.

The packaged generic-asset runtime smoke runs before final package binding from
the exact retained `app.asar` bytes already bound to the package inventory. It
returns the same ASAR SHA-256 and byte count as runtime evidence; it never
closes package evidence and then reopens `resources/app.asar` by pathname.

Tests use only `tests/fixtures/app-server`. They never log in, start a model,
contact a provider, or enable network access.
