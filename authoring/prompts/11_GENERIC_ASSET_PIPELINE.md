# Derive and seal generic assets

Use only when the production profile requires assets and one exact compiled
logic subject has been reviewed.

The subject contract recognizes `gamepack` and `legacy_worldpack`, but the generic D1 derivation path requires `gamepack`
and an integrally validated
`world-forge.gamepack` v1. Never substitute an ID/hash tuple for the loaded
artifact.

Derive requirements from mechanics, actions, interaction states, feedback,
activities, narrative when applicable, rules/goals, UI/data visualization,
audio/timing, accessibility, localization, presentation, and runtime target.
Complete the target-scoped chain:

```text
subject -> target/style -> inventory -> specifications
-> request/receipt -> selection -> provenance/license
-> deterministic processing -> retained-byte QA -> release-ready manifest
-> sealed runtime-only assetpack
```

Each state is independent. Planned is not produced; produced is not processed;
processed is not QA-passed; release-ready is not sealed; sealed is not runtime-
compatible. Prompts, provider/model details, credentials, editable sources, and
authoring receipts stay outside the sealed pack.

Authoring validity is not runtime executability. Return exact identities,
portable paths, media/format evidence, license scope, QA status, seal evidence,
and blockers without claiming adapter or native support.
