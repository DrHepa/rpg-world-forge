---
name: forge-world-release
description: Compile, package, and verify one immutable runtime bundle from an approved world-authoring repository. Use only for the world-release phase; game import is a separate skill.
---

# Forge a world release

Operate with explicit `FORGE_ROOT`, external `WORLD_ROOT`, and new `BUNDLE_ROOT`.
No game path is in scope.

## Release sequence

1. Read ADR-0009, `docs/CONTENT_PIPELINE.md`, and
   `docs/ASSET_PIPELINE.md`.
2. Require a release-valid, canon-locked world source.
3. Compile the worldpack and run narrative analysis.
4. Require a complete v3 asset manifest in `production`, bound to that exact
   worldpack and containing no self-declared deliverable.
5. Build the runtime-only renderpack under `assets/release/`; the builder must
   pass the internal build profile against the exact worldpack.
6. Run `worldforge finalize-asset-release` with the production-manifest hash,
   then require `worldforge validate-assets --profile release` to pass.
7. Export a new release ID with `worldforge export-bundle`.
8. Run `worldforge verify-bundle` against the completed directory.
9. Record the verified output path and bundle hash for the import phase.

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
- This skill releases the current 2D/2.5D renderpack bundle only. A sealed 3D
  assetpack is an engine-neutral implementation handoff and is not accepted by
  `export-bundle` or the generated pyray game.

Do not modify a game repository in this phase. `$import-world-bundle` owns the
separate compatibility, copy, catalog-update, and game-load gate.

## Completion

Return bundle/world/release hashes, capability result, license inventory, and
verification evidence.
