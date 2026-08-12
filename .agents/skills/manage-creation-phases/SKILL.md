---
name: manage-creation-phases
description: Reconcile, complete, or reopen one generic phase-report v3 workflow transition with exact CAS, evidence, and invalidation dependencies.
---

# Manage the generic P00-P14 workflow

## Scope

Perform exactly one `reconcile-creation`, `complete-phase`, or `reopen-phase`
transition. Before changing a bound upstream identity, retain the current status
hash. Reconcile with that expected hash and the complete current artifact
registry before status, reopen, or completion. Never edit workflow status,
phase reports, histories, or hashes manually.

## Invariants

Validate the entire creation graph, current workflow revision, exact phase role,
output artifacts, reviewer, and invalidation dependencies. Reconciliation must
validate recorded and current identities, append canonical invalidation/history,
and reject stale expected hashes. `not_applicable` is accepted only from the
phase-report v3 rule: P03 `world_absent`, P04 `chronology_absent`, P05
`group_structures_absent`, P06 `actors_absent`, P08 `narrative_absent`, P11/P12
`assets_not_applicable`, and P13 `runtime_not_applicable`. P00-P02, P07, P09,
P10, and P14 require evidence.

## Completion

Return the immutable report identity, workflow revision, next phase, and
invalidated descendants. Authoring validity is not runtime executability. P13
is not execution proof and P14 is not a release claim.
