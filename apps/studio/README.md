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
WebGL, or 3D runtime previews. Asset production, Modly, Blender, file watching,
the Game cockpit, and native playtests remain later batches.

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
npm run package:dir
```

An optional provenance regeneration check does not start app-server or a model:

```bash
node scripts/codex-protocol.mjs --check-generator /absolute/path/to/codex
```

`package:dir` currently produces the Electron shell plus the pinned stable
Codex protocol artifacts. The runtime manifest points at the audited future
Python/Codex layouts; until native runtimes are supplied by the release
pipeline, the package reports them unavailable instead of consulting `PATH`.

Tests use only `tests/fixtures/app-server`. They never log in, start a model,
contact a provider, or enable network access.
