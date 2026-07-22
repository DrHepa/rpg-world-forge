# ADR-0011: Forge Studio starts as a provider-free local application service

- Status: accepted
- Date: 2026-07-22

## Context

M5 completed deterministic authoring, asset production, immutable handoff, and
standalone 2D/2.5D runtime contracts. The next product layer needs an
interactive desktop workbench, but coupling a renderer directly to repository
files, SQLite, Codex, local models, Modly, or Blender would create multiple
privileged writers and bypass the repository boundaries established by
ADR-0009 and ADR-0010.

The desktop and provider integrations also need a stable testable seam before
they exist. Provider execution is not required to prove workspace identity,
durable job state, explicit approval, safe file publication, or crash recovery.

## Decision

Forge Studio begins with a long-lived Python application service in
`worldforge.studio`. It communicates through schema-versioned strict NDJSON
objects over standard input/output. It has no network listener and no runtime
dependency outside the Python standard library and existing Forge code.

The service owns four public v1 contracts:

- a workspace record joining separately validated Forge, world, optional game,
  and optional bundle roots;
- request, response, error, and event protocol envelopes;
- explicitly approved, base-hashed source changesets;
- durable job state records that execute no provider operation.

Canonical files remain authoritative. SQLite contains only Studio registry and
coordination state and lives under an explicit external user-data directory.
The same directory owns content-addressed proposed blobs and durable apply
journals. A restart orphans interrupted running jobs. Incomplete file journals
are rolled back; journals whose file state is already committed complete the
SQLite transition after revalidation.

Changeset application is limited to portable UTF-8 files under a world's
`source/` tree. Creation, replacement, and deletion require existing safe
parents, standalone regular files, exact base identity/hash checks, and the
existing world lifecycle lock. The journal records every reserved stage name
before its file is created. Same-directory exclusive publication plus the
identity/hash journal is used instead of claiming a filesystem-wide or
SQLite/filesystem atomic transaction. The portable source-path policy is named
in the schema and implemented by one shared Python validator. POSIX pins the
directory chain and performs entry operations relative to its descriptors;
Windows holds no-delete handles across the full chain. Both recheck visible
identities through commit. POSIX requires directory `fsync`; Windows requires
write-through journal replacement and a successful directory-handle
`FlushFileBuffers`. If the active filesystem cannot provide the applicable
primitive, apply fails closed. Unknown or replaced identities also fail closed.

## Consequences

- A future Electron main process has one narrow service to supervise instead of
  granting filesystem or database access to its renderer.
- CLI/runtime behavior and the stdlib-only `isoworld` boundary remain unchanged.
- Studio can list and recover jobs before any model or provider adapter exists.
- Human approval is a durable state transition rather than UI-only state.
- Repository files and SQLite remain separate transactional domains; recovery
  is explicit and hash/identity based.
- A world source parent directory must already exist before a changeset creates
  a file in it. Directory creation is intentionally outside v1.

## Rejected alternatives

### Put Studio state in each world repository

Rejected because UI layouts, job logs, staged AI output, and provider state are
not canon and must not contaminate or be shipped with a world.

### Let the desktop renderer write files or open SQLite directly

Rejected because it would broaden the trusted surface, duplicate validation,
and make crash recovery dependent on UI lifetime.

### Execute providers in the first service batch

Rejected because it would mix credential, network, cost, sandbox, and output
quarantine policy into the foundational persistence and approval contract.

### Describe a multi-file apply as atomic

Rejected because SQLite and multiple repository directory entries do not share
a portable transaction. The service instead journals intent and identities,
then deterministically completes or rolls back after interruption.
