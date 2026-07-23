# ADR-0012: Forge Studio uses a sandboxed Electron desktop shell

- Status: accepted
- Date: 2026-07-22

## Context

ADR-0011 established a provider-free Python application service over strict
NDJSON. A visual desktop client must supervise that service without granting a
web renderer filesystem, process, database, repository, or arbitrary IPC
access. Development and packaged builds also need the same origin and loading
model so a localhost development server cannot become an accidental production
dependency.

The packaged Python and Codex runtimes are not yet audited release inputs. The
shell must therefore prove its boundary without searching `PATH`, silently
substituting a system runtime, or pretending future authoring features exist.

## Decision

The first Studio client is an app-local Electron 43.2.0 and React 19.2.8
project under `apps/studio`. Node 24.14.1, npm 11.13.0, every direct dependency,
and package-lock v3 are exact. It is deliberately not a repository-wide npm
workspace.

Vite produces static renderer files. Electron registers the secure standard
`rwf-studio://app` scheme before readiness and serves only contained build
artifacts from it in every mode. The renderer has no development server,
network connection source, Node integration, webview, popup, navigation, or
permission path. It runs with sandboxing, context isolation, web security, a
closed CSP, and deny-by-default session handlers.

The preload exposes only initialization, service status, named list operations,
the five existing read-only World authoring queries, two revision-bound asset
catalog reads, four fixed offline job actions, job cancellation, six named human
changeset review controls, and Studio activity subscription on fixed IPC
channels. The changeset controls stage one base-hashed source replacement or
get, diff, approve, reject, and apply one identified changeset. Main selects
every protocol method and operation, creates every request ID, fixes asset
catalog pages at 64 entries, validates closed identifiers, revisions, portable
paths, hashes, text, scalar filters, and bounds, and correlates returned catalog
and review identities.
Neither the renderer nor preload receives a generic method/params request,
arbitrary operation name, `ipcRenderer`, root path, filesystem function,
executable, module, environment, working directory, approval transition,
process control, or arbitrary channel name.

Electron main owns a bounded NDJSON supervisor. It uses `shell: false`, one
absolute executable and fixed arguments, strict UTF-8 and schema validation,
one-megabyte lines, correlated replies, bounded stderr, pending-request and
outstanding-byte budgets, serialized backpressure-aware writes, request
timeouts, internal cancellation, crash rejection, and child-tree shutdown. POSIX children
run in a dedicated process group; Windows cleanup calls the fixed system
`taskkill.exe` path with `/T` and escalates to `/F`. The v1 service protocol has
no generic cancellation message; cancellation remains an internal supervisor
mechanism and safely ignores a late correlated reply.

Development accepts only an explicitly configured absolute
`RWF_STUDIO_DEV_PYTHON`. Packages load a closed runtime manifest from Electron
resources, choose a declared platform/architecture path, and require a regular
contained executable. Missing runtime resources fail closed. No runtime lookup
uses `PATH`, `PYTHONPATH`, `PYTHONHOME`, `NODE_OPTIONS`, or renderer input.

Electron Builder packages an ASAR shell with future Windows NSIS/ZIP and Linux
AppImage/tar targets. Its after-pack hook disables RunAsNode, `NODE_OPTIONS`,
and CLI inspect arguments; disables extra `file:` protocol privileges; enables
cookie encryption, embedded ASAR integrity, and OnlyLoadAppFromAsar. The
self-contained Python/Codex resources, signed installers, provider brokers,
watchers, asset production, and native game visualization remain separate later
changes. The renderer may project verified World/lore reads into an in-memory
draft cockpit and neutral non-authoritative Canvas preview.

## Consequences

- The named preload boundary can stage one explicit base-hashed source draft
  and drive human changeset review transitions. The renderer displays bounded
  immutable v2 text/JSON Pointer evidence, sends the displayed review hash on
  every v2 action, and keeps approval separate from a second confirmed apply.
  Legacy v1 records are readable but cannot be freshly approved or applied in
  the desktop because they have no exact immutable diff. The boundary provides
  no autosave, arbitrary filesystem operation, or generic repository write path.
- The named asset catalog boundary lists and inspects only manifest-authorized
  entries under an exact revision. The renderer cannot choose paths, media
  types, categories, cursors, page bounds, or binary payloads.
- A compromised renderer cannot name IPC channels or directly reach files,
  commands, providers, local ports, or project roots.
- Development requires one explicit interpreter setting; packaged builds are
  visibly unavailable until their audited runtime is present.
- Generated TypeScript protocol declarations are checked against the public
  Python-facing JSON Schema, while runtime envelopes are independently
  validated with AJV.
- Electron, renderer, and Python service lifecycles are independently testable;
  no code enters `isoworld` or a generated game.

## Rejected alternatives

### Browser UI on an unauthenticated localhost service

Rejected because it expands the network and origin boundary, complicates
authentication, and gives development and release different loading models.

### Expose raw Electron IPC or Node APIs to React

Rejected because arbitrary channels, filesystem access, and process execution
would bypass the service contracts and approval model.

### Discover Python from PATH

Rejected because the launched code would depend on mutable machine state and
could differ from the runtime audited for a release.

### Embed future provider integrations now

Rejected because Codex, Modly, Ollama, Blender, credentials, network/cost
approval, and output quarantine require their own contracts and threat models.
