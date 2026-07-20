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
- Production without exact world/target/bible/inventory/specification hashes.
- A route treated as an executor, or an executor acting as the GPT orchestrator.
- Local-model assets not executed through `modly-cli-mcp`, or without a reviewed
  pre-execution discovery snapshot, canonical surface, Modly
  extension/revision, workflow hash, model/revision, and weights hash.
- Blender/Modly work without typed requests/receipts, exact parent-produced
  inputs, and retained parent receipt hashes.
- 3D runtime outputs with external URIs, undeclared axes/units, unstable node or
  animation names, exceeded budgets, or authoring `.blend` files in handoff.
- Generated content copied directly into runtime without review/compilation.
- Provider/model/MCP/API/credential dependency or production metadata in the
  renderpack, assetpack, immutable bundle, or game runtime.

## Canon-lock gate

P10 additionally requires a reproducible worldpack, hash, zero validator errors,
reachability/softlock report and an impact list for known uncertainties.

## Asset-direction gate

P11 requires one strict target bound to the locked world hash plus approved,
hash-bound visual and audio bibles. `art_direction` initialization may leave
bible and inventory references `null`; this is an incomplete state, not a
failure and not permission to generate assets.

## Asset-planning gate

P12 requires an inventory bound to the target and bible hashes. Every required
item has canonical sources, semantic slots, and one strict provider-neutral v2
specification with exact budgets, expected outputs, and separate allowed routes/
executors. Manual additions are separate and explicitly reviewed.

## Asset-release gate

P13 requires an authorized selected candidate, successful typed production
receipt lineage, deterministic processing receipt, approved QA, complete
asset/source/model/weights/dataset/output license evidence, typed output files,
matching signatures and SHA-256, and resolved semantic bindings for every
asset. The complete manifest must first pass the build profile in phase
`production` with no deliverable. The resulting renderpack/assetpack must be
built under `assets/release/`, independently verified, hash-sealed with
`finalize-asset-release`, and only then pass the `release` profile. A 2D/2.5D
target must compile and verify `isoworld.renderpack` v1. A 3D target must compile
and verify an engine-neutral assetpack whose GLBs match coordinates, budgets,
nodes, rigs, animations, colliders, embedded-resource policy, and the
zero-external-URI rule.

The release manifest must reference non-null bibles and inventory, include a
deliverable, contain at least one asset, and mark every required asset
`processed`. Optional unproduced assets are excluded from the deliverable.

## Handoff gate

P14 requires a clean consumer-facing handoff. For 2D/2.5D, the current immutable
bundle contains only the worldpack, renderpack, approved runtime files, hashes,
bindings, and required license/notices subset. For 3D, the engine-neutral
assetpack goes to a separate implementation/runtime-adapter phase; it is not an
input supported by the current pyray bundle or game. The implementation agent
must not need authoring chat history, provider/MCP configuration, receipts,
source files, or Forge skills to understand data, assets, rules, or acceptance
tests.
