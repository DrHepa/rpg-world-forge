# Installed contract material

This directory is the public, installed location for World Forge contract material.
`catalog.json` is the machine-readable index of public contracts. It traces each
schema to its format/version, owning Python and CLI surfaces, tests, docs, and
milestone provenance. Schemas remain under `schemas/`.

Catalog format v1 retains the historically named `m5_phases` provenance field.
It now accepts `M6` for additive runtime-composition contracts; the legacy name
does not mean those entries claim M5 readiness.

The M6 contract group defines a static capability catalog, six exact world
presentation profiles, adapter declarations, hash-bound compositions, and
compatibility reports. These contracts select no engine or executable and do
not make a declared adapter runtime-ready.

`composed-runtime-bundle.schema.json` seals one compatible composition, its
four contracts, freshly recomputed compatibility evidence, unchanged M5 packs,
and approved notices into an exact runtime-only tree. The catalog intentionally
lists no committed built-bundle fixture: tests build temporary bundles from
neutral inputs and compare their exact bytes and hashes.

`game-package-extraction.schema.json` defines deterministic extraction evidence
for one exact `world-forge.game_package` v1 archive. It binds the archive hash
and size to the extracted standalone identity, payload lock, lineage, and tree
hash. Studio creation-job/worker v9 produces this evidence without exposing
native paths or archive bytes through the public protocol; publication and
recovery remain coordinator-owned.

`studio-creation-preview.schema.json` defines the pathless ephemeral PNG/WAV
lease projected by Studio protocol v4. It binds the opaque handle to exact
workspace, assetpack artifact, published output grant generation, asset ID,
byte identity, fixed chunk size, and validated media metadata; native paths and
renderer-controlled byte ranges are not part of the contract.
