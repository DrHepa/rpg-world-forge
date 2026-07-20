---
name: forge-world-release
description: Compile, package, and verify one immutable runtime bundle from an approved world-authoring repository. Use only for the world-release phase; game import is a separate skill.
---

# Forge a world release

Operate with explicit `FORGE_ROOT`, external `WORLD_ROOT`, and new `BUNDLE_ROOT`.
No game path is in scope.

## Release sequence

1. Read ADR-0009 and `docs/CONTENT_PIPELINE.md`.
2. Require a release-valid, canon-locked world source and approved renderpack.
3. Compile the worldpack and run narrative analysis.
4. Validate asset provenance and build the runtime-only renderpack.
5. Export a new release ID with `worldforge export-bundle`.
6. Run `worldforge verify-bundle` against the completed directory.
7. Record the verified output path and bundle hash for the import phase.

## Release invariants

- The bundle contains only its manifest, worldpack, renderpack, processed
  assets, and runtime license files.
- Every file is declared by relative POSIX path, size, and SHA-256.
- A byte change creates a new bundle hash and release; never patch an imported
  release in place.
- World ID, release ID, content hashes, schema versions, and required runtime
  features agree at every layer.
- Prompts, provider/model metadata, references, candidates, phase state, and
  production manifests never cross the bundle boundary.

Do not modify a game repository in this phase. `$import-world-bundle` owns the
separate compatibility, copy, catalog-update, and game-load gate.

## Completion

Return bundle/world/release hashes, capability result, license inventory, and
verification evidence.
