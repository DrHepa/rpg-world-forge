# Installed contract material

This directory is the public, installed location for RPG World Forge contract material.
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
