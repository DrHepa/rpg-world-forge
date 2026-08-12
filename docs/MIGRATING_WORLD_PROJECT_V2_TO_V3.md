# Migrating a retained world project from v2 to v3

This guide applies only to published `rpg-world-forge.project` world projects.
It does not migrate a generic `world-forge.project` and it does not convert a
worldpack into a gamepack.

Version 2 uses the retained repository identity; version 3 changes
`tool_repository` to `world-forge`. Ordinary inspection/load never changes a
project. Version 1 must first use the separate `upgrade-world` operation.

## 1. Back up and inspect

Keep a user-controlled backup outside the repository. Require a clean,
quiescent world-authoring repository and inspect it with `world-status`. Compute
SHA-256 over the exact current `.worldforge/project.json` bytes; a pretty-print,
parsed object hash, or later copy is not equivalent.

## 2. Dry-run

```bash
worldforge migrate-world-project /path/to/world \
  --expected-source-hash <exact-lowercase-sha256> \
  --mode dry-run
```

Dry-run is side-effect free. It validates the complete retained world boundary,
requires coherent v2 source, calculates the exact v3 target hash, reports
`apply_supported` and `apply_capability_reason`, and refuses to call the project
ready when durable recovery artifacts exist.

## 3. Apply with the same hash

```bash
worldforge migrate-world-project /path/to/world \
  --expected-source-hash <same-exact-sha256> \
  --mode apply
```

Apply acquires the retained lifecycle lock, uses compare-and-swap source
identity, writes a durable backup, appends the ordered migration journal,
publishes v3 through the platform's identity-preserving primitive, verifies the
complete project, writes terminal evidence, and performs only evidence-
authorized cleanup.

The canonical internal records are:

- `.worldforge/project-migration.backup.json`;
- `.worldforge/project-migration.journal.json`;
- `.worldforge/project-migration-v3.evidence.json`.

Do not edit, copy over, or delete them manually during recovery.

## Platform semantics

Linux requires the audited identity-preserving exchange and retained-directory
durability primitives. Windows requires the declared supported build/filesystem
capabilities, retained strict file identity, no-delete ancestry handles, and
native rename/disposition primitives. Unsupported hosts fail closed.

Windows uses commit-forward recovery after an ambiguous rename. It does not
pretend that reconstructing the old pathname is a safe rollback. On either
platform, an indeterminate outcome preserves evidence for explicit review.

## Retry, verification, and rollback boundary

Re-running apply with the same expected source hash is the recovery/idempotence
entry point. A completed v3 project returns a stable migrated result only when
its exact evidence remains coherent. Hash, identity, ancestry, journal, backup,
or source divergence stops recovery.

After success, run `world-status`, source validation, applicable worldpack
goldens, and repository tests. The source content contract version and existing
worldpack hashes must not change merely because the project repository identity
was migrated.

Rollback from a completed or ambiguous native publication is not automatic.
Use the user-controlled preflight backup only after preserving Forge evidence
and reviewing the platform-specific outcome.
