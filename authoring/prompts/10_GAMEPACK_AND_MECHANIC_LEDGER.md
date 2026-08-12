# Compile and audit a generic gamepack

Use only for an integrally valid generic `project_kind: game` with exactly one
supported `world-forge.logic_module` v1.

Compile deterministically to `world-forge.gamepack` v1. Do not infer mechanics
from genre, fiction, presentation, adapter names, or IDs. Preserve the exact
profile/source/module identities and declarative state, actions, rules,
conditions, effects, goals, failures, endings, presentation hooks, asset
requirements, localization references, provenance, and runtime requirements.

Build the mechanic capability ledger separately. Each required core verb and
feature must map through runtime action, authoritative state,
preconditions/rules/effects, presentation/feedback, asset binding,
save/replay representation, and evidence. Before adapter resolution, use only
`authoring_only`; do not invent test or native evidence.

Run the compiler-selected bounded analyzer twice and compare canonical bytes and
hashes. Report its assumptions, limits, evidence kind, and inconclusive state.
Never embed scripts, prompts, credentials, providers, mutable source paths, or
runtime AI.

Authoring validity is not runtime executability. Compilation does not certify
asset readiness, adapter support, native execution, packaging, or release.
