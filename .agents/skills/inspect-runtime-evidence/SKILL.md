---
name: inspect-runtime-evidence
description: Read-only inspection of generic adapter compatibility, mechanic coverage, and per-platform execution/package evidence.
---

# Inspect generic runtime evidence

## Scope

Use `inspect-game-runtime` and the code-owned registry/snapshot. Do not mutate
logic, assets, runtime implementations, evidence, or packages.

## Invariants

Select by exact requested adapter/capabilities, never by genre or presentation
label. Map every required mechanic and feature to supported, extension-
verified, authoring-only, or blocked. Distinguish untested, headless-verified,
native-verified, and failed per platform. Fake/recording backends are local
deterministic evidence, not native evidence. Missing hosted rows are not passes.

## Completion

Return the exact support-report identity, status dimensions, evidence IDs,
missing-capability reason codes, and stale/crossed evidence findings.
Authoring validity is not runtime executability. Do not materialize or claim
release.
