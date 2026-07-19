# Quality gates

Every phase report must answer:

- What became canon and why?
- Which files and IDs changed?
- Which prior decisions were superseded?
- What dependencies were checked?
- What remains uncertain but non-blocking?
- Which automated/manual validations passed?
- Who reviewed the result?

## Universal blockers

- Unresolved placeholders in candidate/release content.
- Canon without a source or recorded decision.
- Broken references, duplicate IDs or contradictory facts.
- A character using forbidden or not-yet-known information.
- Timeline events without satisfiable prerequisites.
- Narrative effects that cannot be represented by state/events.
- Assets without specifications, provenance or compatible license evidence.
- Local-model assets without a named/versioned Modly extension and workflow.
- Generated content copied directly into runtime without review/compilation.
- Model/API/credential dependency in the game runtime.

## Canon-lock gate

P10 additionally requires a reproducible worldpack, hash, zero validator errors,
reachability/softlock report and an impact list for known uncertainties.

## Asset-release gate

P13 additionally requires processed status, authorized approval, typed output
files, matching media signatures and SHA-256, complete license fields, semantic
bindings, a successfully compiled renderpack and in-engine QA for every asset.

## Handoff gate

P14 requires a clean consumer-facing bundle. The implementation agent must not
need authoring chat history to understand data, assets, rules or acceptance tests.
