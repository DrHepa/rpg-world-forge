# Generic phase workflow and evidence

Use for one phase-report v3 transition only.

Load the exact project/profile/source graph, workflow status, prior immutable
reports, current phase, phase-role contract, and proposed output artifacts.
Revalidate every referenced artifact and invalidation dependency. Create either:

- `ready` with exact reviewed output evidence; or
- `not_applicable` only when profile-aware validation proves the phase's exact
  absence code.

P03 uses `world_absent`, P04 `chronology_absent`, P05
`group_structures_absent`, P06 `actors_absent`, and P08 `narrative_absent`.
P11/P12 use `assets_not_applicable`; P13 uses `runtime_not_applicable`. Each
code still requires its canonical absence proof. P00-P02, P07, P09, P10, and
P14 cannot be waived.

Capture the current status hash before changing any bound upstream identity.
After the change, run `reconcile-creation` with that expected hash and every
current artifact before status, reopen, or completion. Reconciliation archives
the new immutable inputs and appends invalidation history. Use `complete-phase`
or `reopen-phase` only afterward; never edit status, histories, reports, or
hashes manually.

Return the report identity, reviewer, evidence identities, invalidation set,
new workflow revision, and remaining blockers. P13 is compatibility review, not
execution proof; P14 is a reviewed handoff, not a release claim.

Authoring validity is not runtime executability.
