---
name: compile-audit-gamepack
description: Deterministically compile one valid generic game project and audit its gamepack, bounded analysis, and authoring-only mechanic ledger.
---

# Compile and audit a gamepack

## Scope

Compile one integrally validated `project_kind: game` through `compile-game` and
run `analyze-game`. Build the authoring ledger only when requested.

## Invariants

Require exactly one supported logic module. Do not derive behavior from genre,
fiction, presentation, or adapter naming. The output is declarative and may not
contain arbitrary code, prompts, provider data, credentials, mutable source
paths, or runtime AI. Compile twice from independent roots and compare bytes and
hashes. Record analyzer limits and `inconclusive` honestly.

## Completion

Return gamepack, analysis, and ledger identities plus reason codes. An
authoring-only ledger has no test/native evidence. Authoring validity is not runtime executability. Compilation does not prove assets, adapter, platform,
package, or release.
