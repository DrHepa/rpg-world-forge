# Quality gates

Every generic phase report binds the exact project, profile, source manifest,
phase, reviewer, rationale, evidence, output evidence, invalidation
dependencies, and content hash required by phase-report v3. Reports are written
only through the workflow commands.

**Authoring validity is not runtime executability.** Each gate must name the
status dimension it proves and must leave every other dimension unchanged.

## Universal blockers

- Unknown or contradictory fields, duplicate IDs, broken hashes, unresolved
  references, nonportable paths, or required unregistered extensions.
- Mechanics inferred from fiction/genre, or runtime adapters inferred from
  2D/2.5D/3D presentation labels.
- A no-world profile with invented geography, history, factions, actors, or
  spatial dependencies.
- A narrative-none profile with invented lore, quests, dialogue, arcs,
  protagonist state, or narrative modules.
- Gameplay without authoritative state, legal actions, rules/effects, goals or
  open-ended teleology, failure/recovery policy, and mechanic coverage.
- Claims that bounded, static, simulated, heuristic, or local evidence proves
  a stronger property than it measured.
- Assets without exact subject, specification, production/selection,
  provenance, license, deterministic processing, QA, and seal state when those
  stages apply.
- Provider, prompt, model, credential, MCP, mutable source, or runtime-AI data
  in a gamepack, assetpack, runtime bundle, standalone game, save, or replay.
- Required mechanics or platforms hidden as optional, skipped, or passed
  without evidence.

## Profile and module gates (P00-P09)

The creation profile must keep gameplay, world, narrative, fiction,
presentation, production, and runtime target independent. Typed module
collections use their own discriminators; non-RPG modules cannot inherit RPG
requirements. Unknown required extensions fail closed.

`not_applicable` is valid only when phase-report v3 recomputes the exact
profile-derived absence code. A prose rationale cannot grant a waiver.

## Content-lock gate (P10)

Require integral validation, deterministic rebuilds, exact hashes, applicable
bounded analysis, documented limits/assumptions, and no unresolved release
blocker disguised as an authoring success. A generic game compiles to
`world-forge.gamepack` v1; retained RPG source preserves the published
`isoworld.worldpack` v5 canonical-output contract while unsafe input and runtime
boundaries may be hardened explicitly.

## Presentation and asset gates (P11-P12)

When assets apply, require an exact `gamepack` or retained
`legacy_worldpack` subject, target and style direction, derived inventory,
complete specifications, reviewed production evidence, selection, provenance,
licenses, deterministic processing, retained-byte QA, a release-ready manifest,
and a sealed assetpack. These are distinct states: planned is not produced,
produced is not processed, processed is not QA-passed, release-ready is not
sealed, and sealed is not runtime-compatible.

When the profile proves assets do not apply, both phases may use only
`assets_not_applicable`; they must not create decorative placeholder assets.

## Compatibility gate (P13)

P13 verifies declared logic formats, required features, mechanic ledger,
presentation, input, asset formats, platform/architecture/backend matrix,
save/replay expectations, packaging target, and evidence references. It records
`supported`, `partially_supported`, or `unsupported` with exact missing reasons.
P13 is compatibility review, not execution proof. `partially_supported` blocks
`implementation_ready`; it does not block the required reviewed P14
`authoring_ready` handoff.

Headless and native execution are separate. Local fake-backend rendering is not
native evidence; native execution on one OS is not hosted evidence for another.

## Readiness and handoff gate (P14)

P14 requires a validated creation-readiness document and exact handoff. It must
preserve the immutable logic hash through asset subject, sealed assetpack,
composition, runtime bundle, standalone lock, save/replay, and evidence when
those artifacts apply. P14 is a reviewed handoff, not a release claim.

Runtime artifacts include only immutable logic, sealed assets, the selected
runtime implementation, game code, verification scripts, and notices. They
exclude authoring sources, prompts, provider SDKs, Forge dependencies, and
runtime AI.

## Release evidence

Release requires all mandatory local gates and every declared hosted native
matrix row on the exact reviewed commit. Missing, skipped, unavailable, or
running evidence is not green. See `docs/SUPPORT_MATRIX.md` for the current
truthful evidence level.

`scripts/verify_multigenre_release.py` is the canonical abstract-puzzle and
branching-narrative release-lineage gate. It rebuilds the exact source,
gamepack, processed PNG/TTF asset, sealed assetpack, runtime and materialization
bundles, standalone, save/replay, deterministic package, safe extraction, and
bounded native raylib evidence outside the repository. `--native off` records
`untested`, and `optional` never promotes unavailable or failed execution.
Release requires `--native required` on the complete Ubuntu 24.04/Windows
Server 2022 and Python 3.11/3.12 matrix, followed by exact aggregation. Hosted
status is **PENDING** until all four reports and their aggregate are green for
one source revision. The authoritative machine-readable status is
`docs/evidence/multigenre-release-status.json`.

## Retained legacy specialization

Legacy RPG M5 gates remain applicable to retained worldpack/renderpack/
assetpack production. Their exact manifests, receipts, world hashes, native
isoworld evidence, and published identities remain valid, but they do not prove
generic gamepack or generic-adapter support.

## Retained legacy RPG quality gates

The following pre-existing gates apply only to retained worldpack/M5/isoworld projects. They do not define generic gamepack support.

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
