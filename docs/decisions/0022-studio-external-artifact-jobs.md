# ADR-0022: Studio external artifact jobs retain path authority in Electron main

- Status: accepted
- Date: 2026-07-30

## Context

ADR-0014 deliberately limited Studio protocol v1 and managed job v2 to
workspace-bound, read-only execution. Generic standalone materialization and
game-package publication now require user-selected filesystem locations outside
the registered Forge workspace. Extending the renderer API with absolute paths,
or extending the read-only worker with a generic command surface, would turn
untrusted renderer input into ambient filesystem or process authority.

These operations may also be interrupted after creating externally visible
bytes. A successful validation before execution is insufficient: source and
target identities, recovery evidence, and publication ownership must remain
bound across the complete operation.

## Decision

Studio protocol v2 is a separate, closed external-artifact capability surface.
It retains protocol v1 initialization and adds only:

- external grant create, inspect, and revoke;
- standalone game materialization;
- deterministic game-package publication and extraction;
- external job inspect, list, cancel, and explicit recovery.

The renderer sends no path, display name, command, module, environment, or
arbitrary operation value. Electron main owns the exact IPC method-to-operation
mapping, opens the native file or directory dialog, and passes the selected path
to the Python service over a private protocol-v2 request. Renderer-visible grant
and job documents are pathless.

The Python service stores native source or target paths and retained filesystem
identities only in its private SQLite authority. External grant v1 separates
source and target capabilities. Source grants reject links, hardlinks, non-
regular objects, workspace overlap, portable aliases, and identity drift.
Target grants reserve an absent leaf atomically beneath a retained parent and
reject overlap or replacement.

External job v3 is distinct from legacy job v1 and managed read-only job v2.
Its operation and parameter unions are closed. The worker calls the trusted
`worldforge.game_package` and standalone materialization APIs directly; it never
executes a generated-game script or renderer-selected process. Expected source,
game, package, and materialization hashes are mandatory operation inputs and are
rechecked at the publication boundary.

Interrupted running v3 jobs become `orphaned` and mark their target grant as
requiring explicit recovery. Recovery verifies the retained identity and
artifact evidence before resuming or rolling back. Ambiguous ownership or drift
fails closed and preserves bytes for review. Queued cancellation is terminal;
running cancellation follows the existing managed process-tree termination and
reaping boundary.

Protocol replies are correlated by request ID, method, and protocol version.
Protocol v1 remains unchanged and cannot create or operate on external v3 jobs.
SQLite schema v2 is an additive migration and preserves existing v1 rows.

The same authority rule applies to additive creation protocol v4 asset-release
sealing. A separate pathless `studio_creation_output_grant` v1 retains one
absent `generic_assetpack_directory` target selected by Electron main. The
renderer receives fixed select/get/revoke/seal methods and can provide only the
workspace, immutable artifact authority, QA artifact IDs, manifest ID, grant ID,
and expected grant generation. It cannot provide a path, grant kind, operation,
command, provider, or process configuration.

`studio_creation_job` and its isolated worker advance additively to v3 only for
`asset.release.seal`; v1 compilation/admission and v2 deterministic processing
remain closed. The worker derives the release manifest and generic assetpack
manifest from the exact candidate lineage, while the parent owns no-replacement
publication. A successful transaction binds the same assetpack identity to the
published grant, job result, success event, and candidate artifacts. Publication
intent and retained filesystem identity survive restart. Resume verifies or
recovers the exact directory. Windows rollback removes only the
identity/hash-bound publication through retained delete handles. Linux cannot
identity-delete an open directory, so rollback retains the exact publication,
keeps the output grant `recovery_required`, keeps the job `orphaned`, and fails
before pathname deletion. Foreign or ambiguous bytes are always preserved.

Successful Linux publication does not depend on deleting a journal. Its exact
validated append-only journal is retired with `RENAME_NOREPLACE` into retained,
identity-addressed terminal evidence. A successfully committed internal job
stage is likewise revalidated and renamed without replacement to a
identity-addressed `.worldforge-retained-creation-stage-*` terminal evidence
name before the job leaves `cleanup_pending`. Incomplete stages remain under
their active names and force `orphaned`/`recovery_required`; they are never
reported as cleaned up. Recovery-required errors carry pathless retained-entry
locators and recorded filesystem identities. Partial stages and terminal evidence have no automatic
garbage collector under the active same-UID namespace threat model; disposal
requires an explicit operator boundary outside that model.

Subsequent additive protocol-v4 transitions keep this authority model:
`runtime.compose` uses job/worker v4, `runtime.bundle.build` uses v5 with output
grant v2, and `game.materialization.bundle.build` uses v6 with output grant v3.
`game.materialize` uses job/worker v7 with output grant v4 for one
`standalone_game_directory`. The isolated worker derives only the deterministic
standalone manifest from an exact staged source. Trusted Python coordination
alone retains source/target filesystem authority, invokes canonical standalone
publication, binds the payload-lock `tree_hash`, and owns explicit restart
recovery and rollback. The public request and every durable projection remain
pathless, and successful publication stays release-blocked with native execution
untested.
Each version remains operation-discriminated and pathless publicly. The latter
three publish immutable directories without replacement from exact verified
source grants; materialization and standalone output remain candidate-only,
release-blocked builds
until separate native and packaging evidence exists.

## Consequences

- Users can select external artifact locations without granting the renderer raw
  path authority.
- Existing Studio protocol v1, job v1/v2, workspace v1, and read-only worker
  behavior remain compatible.
- External operations are auditable and recoverable without pretending that an
  interrupted publication is safe to retry automatically.
- Generic assetpack output remains reusable after publication, but its private
  path authority is never projected into a renderer-visible record.
- The assembled Studio runtime must contain the external authority modules and
  every versioned creation-job, worker, output-grant, and protocol schema.
- Native Windows identity, cancellation, recovery, and package evidence remains
  a required platform gate; Linux evidence is not a substitute.

## Rejected alternatives

### Accept absolute paths from the renderer

Rejected because schema validation cannot make a compromised renderer a trusted
filesystem authority.

### Add external operations to protocol v1 or job v2

Rejected because it would silently broaden published read-only semantics and
make compatibility depend on implementation details rather than explicit
versions.

### Invoke standalone-game verification or packaging scripts

Rejected because game-local scripts are mutable inputs. Studio must call the
trusted Forge APIs it packages and audits.

### Resume every interrupted job automatically

Rejected because interruption can occur after publication. Recovery requires an
explicit action and identity/hash evidence.

## Additive amendment: v8/v9 package evolution and grant v5

The authority model above is unchanged and now covers two additional closed
creation operations. `game.package` uses creation job/worker v8 and output
grant v5 for one `game_package_file`. `game.package.extract` uses creation
job/worker v9, re-verifies that exact published v5 source grant, and publishes
one `standalone_game_directory` through the canonical extraction and recovery
boundary. Neither public operation accepts a path or exposes the private
archive.

Protocol v4 remains exactly 17 transport methods, while the creation job/worker
operation union now totals ten operations from v1 through v9. The renderer may
invoke only the corresponding fixed package and extraction controls. Package
or extraction success preserves lineage but does not create native execution,
hosted, packaged-shell, or release evidence.

## Additive amendment: v5/v12/v6/v2 publication surface

The v4/v9/v5 package and extraction decision is preserved as history. The
current authority surface is protocol v5 with 18 transport methods,
creation job/worker contracts through v12, output grants through persisted v6,
and creation previews as published v1 plus pre-release QA candidate v2. Historical
v3 create rejects `asset_content_mode`; v5 create accepts `asset_content_mode`. The
private output-grant methods are `create`, `get`, `list`, and `revoke`; renderer
state remains pathless and main-selected. Legacy protocol v4 listing continues
to hide v6 grants and projects only v1-v5, while protocol v5 listing exposes the
persisted v6 authority rows.

Standalone and package local output paths now exist through main-owned save
selection and pathless renderer grants. That is local authority evidence only:
native/release evidence stays blocked/PENDING until the exact hosted authority
workflow run succeeds, and raw authoring validity never implies executable or
native support.
