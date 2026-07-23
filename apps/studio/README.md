# RPG World Forge Studio shell

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

## Development

Use Node 24.14.1 and npm 11.13.0. Install the Forge into a supported Python 3.11
or 3.12 environment and install Codex 0.144.6, then provide both native
executables explicitly. The app never searches `PATH`:

```bash
cd apps/studio
npm ci
RWF_STUDIO_DEV_PYTHON=/absolute/path/to/venv/bin/python \
RWF_STUDIO_DEV_CODEX=/absolute/path/to/codex npm start
```

There is no renderer development server. Vite writes static files and Electron
serves them through `rwf-studio://app` in development, tests, and packages.

Run the local gates with:

```bash
npm run check:generated
npm run lint
npm run typecheck
npm test
npm run build
```

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
replacement cannot redirect build output into the repository. The after-pack
hook statically
hardens Electron fuses and writes an exact
`shell_only` inventory. The verifier pins the package tree, validates the ASAR
entrypoints, compares the runtime manifest, Codex protocol provenance, runtime
source contract, and schemas byte for byte, and rejects missing, altered,
linked, replaced, or extra resources. Each process build first removes its
generated process tree and Vite empties its renderer tree. Electron-builder can
select only the five exact clean build files plus its sanitized `package.json`;
the verifier pins those clean source trees and requires identical ASAR file,
directory, size, and hash inventories. Extra vendors, executables, runtimes,
or stale outputs therefore fail instead of being hidden by `shell_only`. It
does not launch the GUI.

This is not a self-contained release: Python and Codex runtimes are absent,
`release_ready` is false, and redistribution remains blocked by the seven
checked-in provenance blockers. Linux uses descriptor-relative no-follow
reads. Windows uses the stdlib Python backend and the audited Forge
`NtCreateFile`/no-delete-sharing handle primitives while Node performs only
static ASAR and fuse inspection against retained private snapshots. Set
`RWF_STUDIO_BUILD_PYTHON` to an absolute supported Python 3.11/3.12 executable
for Windows build and verification; the tool never searches `PATH`. Windows
deletes only its two private snapshot files through their still-retained
delete-capable handles. It deliberately leaves the now-empty unique temporary
directory for OS or CI-job cleanup rather than recursively deleting a pathname
after its anti-replacement guards have been released. A failed or uncertain
snapshot is likewise preserved fail-closed.

Tests use only `tests/fixtures/app-server`. They never log in, start a model,
contact a provider, or enable network access.
