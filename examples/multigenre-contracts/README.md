# Multi-genre contract fixtures

These fixtures separate contract authoring, local deterministic execution, and
release evidence.

- `abstract-puzzle` and `branching-narrative` are executable end-to-end cases.
  Their local deterministic lineage includes compile, 16 asset files per case,
  processing/QA, sealing, runtime and materialization bundles, standalone
  output, headless persistence, package, and extraction. Hosted native evidence
  for the exact reviewed revision remains pending.
- `action-framing`, `faction-strategy`, `modular-roguelite`, and
  `sports-career` are authoring-only representatives. Each has an exact authored
  gamepack and 16-file production/processing/QA lineage. Its capability ledger
  remains `authoring_only` with `adapter_not_evaluated`; the adapter is absent,
  execution is untested, packaging is unverified, and release is blocked.
- The six asset-bearing cases contain 96 D2 asset-fixture files in total. A
  `release_ready` asset manifest is not a sealed assetpack and not a released
  game.
- `systemic-simulation` demonstrates that assets may be `not_applicable` while
  runtime is requested. Runtime therefore remains blocked without placeholder
  asset generation.
- `universe-library` is a focused typed-world-module example, not an executable
  game case.

The catalog lists representative fixtures for contract discoverability. It is
not an exhaustive inventory of every file in each case.
