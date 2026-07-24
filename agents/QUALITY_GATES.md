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

The closed assetpack schema excludes license/notices fields or files and all
authoring metadata. Required runtime licenses and notices travel beside the
hash-sealed assetpack as separately verified immutable-handoff material.

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

## M5 repository-readiness gate

Closing M5 requires repository-level evidence in addition to an individual
P13/P14 report:

- The complete headless suite, Ruff lint/format checks, and Python compilation
  pass under the supported toolchain.
- Foundation release validation, zero-warning narrative analysis, the runtime
  AI-boundary audit, and source plus installed-artifact contract audits pass.
- The exact dependency versions in `requirements-m5.lock`, package metadata,
  generated-game requirements, and notices remain synchronized; `pip check` and
  dependency auditing report no known conflicts or vulnerabilities. Exact
  versions without per-requirement hashes must not be called hash-locked.
- `examples/m5-neutral/` remains narrative-neutral, local, procedural, and
  offline. Its exact lock and same-toolchain regeneration pass without executing
  a provider, model, or network call. A schema-required route namespace is not
  execution evidence.
- Disposable external gates build the 2D/2.5D renderpack, build and independently
  verify the 3D assetpack, finalize copied manifests, validate both release
  profiles, and remove the outputs. Committed authoring manifests are not runtime
  packs.
- The standalone 2D/2.5D path passes bundle export/import, independent game
  audit, offline execution, deterministic replay, reproducible packaging,
  extraction, and replay from the extracted package.
- Wheel and sdist builds are reproducible for the tested tree, install cleanly
  in isolated environments, and pass installed contract audits.
- GitHub Actions are pinned by full commit SHA. Downloaded security tooling is
  verified against its release checksum chain, and the complete Git history is
  secret-scanned with narrowly reviewed exclusions.
- The Ubuntu 24.04/Windows Server 2022 and Python 3.11/3.12 hosted rows, native
  smoke, dependency audit, and secret scan are required publication evidence.
  Before the first push that can execute them, they must be recorded as
  **pending**, never inferred as passes from local results.

## M6 partial-readiness gate

The current required verdict is:

**PARTIAL — local implementation evidence only. Self-contained Studio release
remains blocked; hosted and native evidence is pending until the final push.**

Local M6 implementation evidence requires all of the following:

- Studio protocol, durable workspace/job state, interactive World/lore, Assets,
  and Game cockpits, bounded previews, and reviewed changeset stage/diff/
  approve/apply behavior pass without provider/model execution.
- Capability, presentation, adapter, composition, compatibility-report,
  immutable composed-bundle, and composed-catalog contracts pass source and
  standalone verification. Runtime data contains no executable locator or
  authoring control plane.
- The exact legacy Linux x86_64 2.5D adapter passes its static registration and
  preflight contract. The pyray GLB proof remains deliberately incompatible
  with 3D/mixed profiles until collision is implemented; it is not accepted as
  3D runtime readiness.
- Generated games independently verify composed releases and pass
  representation-neutral headless, save, replay, package, extraction, and
  extracted-package checks without `worldforge`.
- Runtime-source validation, deterministic offline verification, secure
  acquisition tests, synthetic assembly/ZIP reproducibility, and shell-package
  static verification pass. All caches, outputs, games, and artifacts remain
  outside the repository.
- A `shell_only` package proves exact ASAR/resource inventories, hardened fuses,
  and the absence of Python and Codex. It retains redistribution `blocked`, all
  seven open blocker codes, and `release_ready=false`.

Self-contained Studio assembly, artifact publication, signing, and
runtime-download CI are blocked until every code in
`apps/studio/packaging/runtime-sources.json` is closed with synchronized legal,
provenance, SBOM/notices, source/relink, pruning, authority, attestation,
validator, and target evidence.

M6 cannot close on composition or 3D authoring evidence alone. Exit requires a
pinned 3D engine/runtime contract and end-to-end proof of assetpack mapping,
animation, collision/physics, deterministic simulation integration,
representative performance, native Linux/Windows behavior, and standalone
packaging. The SHA-pinned hosted rows and required native smokes remain
**pending** before the final push; a skipped, missing, or failed row blocks
publication.
