# RPG World Forge Studio shell

This directory is an app-local Electron/React project. It is not a root npm
workspace and it does not enter world, bundle, or game repositories.

The current shell proves the secure desktop-to-service boundary: it starts the
provider-free Python Studio service, performs the v1 handshake, sends
correlated requests, and displays real status, responses, events, and bounded
errors. Lore editors, asset tools, Codex, Modly, Blender, file watching, and
native playtests are later batches and are not represented as available.

## Development

Use the exact versions from `.nvmrc`, `engines`, and `packageManager`: Node
24.14.1 and npm 11.13.0. Install the Forge into a supported Python 3.11 or 3.12
environment, then provide that interpreter explicitly:

```bash
cd apps/studio
npm ci
RWF_STUDIO_DEV_PYTHON=/absolute/path/to/venv/bin/python npm start
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

`package:dir` currently produces the Electron shell only. The checked runtime
manifest intentionally points at the future packaged Python layout; until that
audited runtime is added, a packaged shell reports the service as unavailable
instead of consulting `PATH`.
