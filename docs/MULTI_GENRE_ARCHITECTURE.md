# Multi-genre World Forge architecture

This is the central design document for the generic creation lane. It describes
contract intent and current implementation boundaries; it is not evidence that
an adapter, platform, package, or release passed. Current evidence is tracked in
[`SUPPORT_MATRIX.md`](SUPPORT_MATRIX.md).

World Forge treats gameplay, world presence, narrative, fiction, presentation,
production, and runtime targeting as independent authoring facets. A fiction
label does not select mechanics, and a 2D, 2.5D, 3D, mixed, VR, or AR
presentation does not imply a game genre or a supported runtime.

World presence and narrative are independently optional. A reviewed no-world or
narrative-none project must not invent geography, lore, actors, quests, or dialogue to
satisfy a template. Authoring validity is not runtime executability.

This foundation is additive, but its compatibility claim is exact rather than a
claim that implementation files never change. Published discriminators remain
readable, including `rpg-world-forge.project`,
`rpg-world-forge.phase_report`, `isoworld.worldpack`,
`isoworld.renderpack`, `isoworld.save`, and `isoworld.replay`. For integrally
valid retained RPG input, the canonical compiler preserves canonical valid
output bytes and content hashes for `isoworld.worldpack` v5, and readers retain
their published version ranges.

Input and runtime hardening may intentionally reject unsafe or ambiguous input
representations that were never valid canonical contracts: duplicate or
non-finite JSON, invalid UTF-8, nonportable paths, links/hardlinks, identity
races, and untrusted resource shapes. Secure persistence and retained-identity
checks may also fail earlier. Those changes do not relabel a discriminator,
reinterpret valid wire/storage semantics, or change canonical valid output
bytes and content hashes. The additive catalog gains new `world-forge.*`
entries and `https://world-forge.local/schemas/` identifiers; it does not make
`isoworld` a generic engine.

## Research baseline and design conclusions

The sources below inform taxonomy and authoring decisions only. They do not
prove that World Forge implements or supports a mechanic, presentation, or
platform.

- [MDA: Mechanics, Dynamics, Aesthetics](https://users.cs.northwestern.edu/~hunicke/MDA.pdf)
  motivates separate authored mechanics/rules, observed runtime dynamics, and
  intended player experience. The creation profile records the promise;
  analyzers and play evidence test outcomes without conflating them with rules.
- The [Game Ontology Project](https://www.digra.org/wp-content/uploads/digital-library/06276.16373.pdf)
  models structural game elements and relationships rather than using setting
  or genre as an implementation taxonomy. World Forge therefore uses typed
  modules and namespaced mechanics instead of one universal genre enum.
- The multidimensional typology by
  [Aarseth, Smedstad, and Sunnanå](https://homepages.hass.rpi.edu/ruiz/EGDFall10/JesperJuulmultidimensionaltopologyofgames.pdf)
  treats space, perspective, time, and teleology as independent axes. These
  become composable profile facets; the reference does not imply that every
  combination has an adapter.
- [Jenkins, *Game Design as Narrative Architecture*](https://web.mit.edu/~21fms/People/henry3/games&narrative.html)
  treats environments as possible evocative, enacted, embedded, or emergent
  narrative spaces. Environment is therefore one delivery channel, not a
  requirement for narrative and not the only way to express it.
- The academic interactive-narrative survey by
  [Riedl and Bulitko](https://ojs.aaai.org/index.php/AAAI/article/view/8447)
  supports treating branching, planning, simulation, and adaptive control as
  distinct authorship strategies with agency tradeoffs. Linear, branching,
  branch-and-bottleneck, modular, storylet, loop, episodic, and open-ended
  topologies are modeled explicitly rather than inferred.
- [Emily Short's storylet practice](https://emshort.blog/2019/11/29/storylets-you-want-them/)
  motivates atomic narrative units with eligibility conditions and state
  effects. “Foldback” is used here descriptively for branches that reconverge at
  a shared bottleneck; it is not treated as a standardized formalism.
- The [PCG Book](https://pcgbook.com/) separates constructive, search-based,
  grammar, planning, mixed-initiative, and evaluation approaches. Procedural or
  generated-at-authoring-time content must therefore declare seeds,
  constraints, reproducibility, evaluation, selection, human review,
  provenance, licensing, and QA. Random output alone is not quality evidence.
- The official [raylib documentation](https://www.raylib.com/),
  [examples](https://www.raylib.com/examples.html), and
  [cheatsheet](https://www.raylib.com/cheatsheet/cheatsheet.html) guide bounded
  adapters. API examples do not prove this repository's version, native
  behavior, or platform matrix; only exact runtime evidence can do that.
- [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12) provides
  structural validation and reusable `$defs`/`$ref` vocabularies. Integral
  cross-document hashes, graph closure, retained filesystem identity, and
  runtime behavior remain Python/runtime responsibilities.
- [Semantic Versioning 2.0.0](https://semver.org/) applies after a public
  contract is defined: incompatible public changes require a new major version,
  compatible additions a minor version, and compatible fixes a patch. New
  namespaces/versions are additive; published IDs are never silently relabeled.

## Exact authoring-to-execution flow

Every arrow is a versioned transition with explicit input/output formats,
canonical content hashes, failure reasons, and evidence. Authoring prompts and
provider details stop before runtime artifacts.

```text
world-forge.project v1 + creation_profile v1
  -> typed world/activity/narrative/system/logic modules
  -> integral profile-aware validation + phase-report v3 (P00-P14)
  -> game projects: world-forge.gamepack v1 + analysis + mechanic ledger
     libraries: optional world-forge.lorepack v1 (never executable by itself)
  -> world-forge.asset_subject v1 (kind=gamepack)
  -> D1 target/style/inventory/specifications
  -> D2a request/receipt/selection/provenance/license
  -> D2b recipe/processing receipt/QA/release-ready manifest
  -> D3 sealed world-forge.assetpack v1
  -> code-owned adapter registry + runtime snapshot/implementation
  -> composition + evidence-backed support report
  -> immutable game runtime bundle
  -> exact materialization bundle -> external standalone game
  -> headless actions + save/replay -> deterministic package/extraction
  -> native raylib evidence per declared platform -> release decision

retained RPG project/source
  -> hardened integral input validation + canonical RPG compiler
  -> isoworld.worldpack v5 with compatible canonical valid bytes/hash
  -> retained M5 renderpack/assetpack + composed legacy bundle
  -> isoworld_raylib_2_5d adapter -> legacy standalone verification
```

The two lanes share policy only where semantics genuinely match. They never
silently convert, relabel, or broaden one another.

## Facet model

### Gameplay

The gameplay facet records a controlled primary family, optional secondary
families, namespaced mechanic tags, player role, verbs, loop, rule/goal and
challenge models, failure/recovery, progression, finite/infinite/open-ended
teleology, session structure, social topology, and authored/systemic/procedural
dependencies. These are design inputs, not adapter selection.

### World

World presence is `none`, abstract, symbolic/board-like, or diegetic. Spatial
topology, scale, time, simulation depth/domains, persistence, and authored,
modular, or procedural spatial structure are separate. A no-world project has
no world modules or fictional geography.

### Narrative, fiction, and tone

Narrative requirement is none, optional, or required. Authorship mode,
topology, delivery channels, protagonist model, agency, focalization, canon
variability, pacing, endings, and information/knowledge structure are separate.
Narrative may be authored, emergent, procedural, player-authored, social, or
hybrid. Fiction genre and tone remain independent of both narrative and
gameplay.

### Presentation and accessibility

Presentation records text/2D/2.5D/3D/mixed/VR/AR intent, camera and perspective,
visual language, UI density/information hierarchy, audio role, input assumptions,
and localization. Applicable accessibility requirements include remapping,
keyboard-only operation, captions, text scaling, contrast, color-independent
feedback, reduced motion, timing alternatives, and screen-reader-compatible
information structures. A requested presentation remains intent until a
compatible adapter and platform evidence exist.

### Production and runtime target

Production identifies authored, modular, deterministic procedural,
authoring-time generated, player-generated, or hybrid content and binds seeds,
constraints, reproducibility, selection, human review, provenance, licensing,
and QA. Runtime target separately declares the requested adapter, accepted logic
formats, required/optional features, platform/architecture/backend, input,
asset formats, persistence, and packaging. Required capabilities fail closed.

## Worked examples

| Example | Facets and proof | Honest current state |
| --- | --- | --- |
| Abstract puzzle | No world, no actors, narrative none, finite board, legal swap actions, goals/restart, high-contrast PNG, deterministic analysis/headless/save/replay/package fixtures | Authoring and local deterministic pipeline implemented; generic native/hosted raylib evidence remains pending |
| Branching narrative | Abstract/optional world, authored branching/foldback choices, persistent flags/knowledge, two endings, sealed readable font, deterministic save/replay | Authoring and local deterministic pipeline implemented; generic native/hosted text adapter evidence remains pending |
| Systemic simulation | Typed state/actions/rules; assets are explicitly not applicable while runtime capabilities are still requested | Valid, compiled, authoring-ready; adapter absent, execution untested, packaging unverified, release blocked |
| Legacy RPG foundation | Diegetic world, actors, systemic/narrative worldpack, renderpack, saves/replays, isometric pyray adapter | Retained backward-compatible specialization; it does not prove generic profiles |

The canonical operational closure is `scripts/verify_multigenre_release.py`.
It retains the complete fixture closure once, binds its topology and bytes to a
tree hash, and consumes only that external snapshot. For both rows it rebuilds
one external immutable lineage from source through
gamepack, compiler analysis and ledger, real PNG/TTF processing and QA, sealed
assetpack, runtime/materialization bundles, independent standalone execution,
save write/read restoration and replay, byte-identical packages, safe
extraction, and the exact extracted native bundle. Hosted native rows measure
the selected raylib wheel against its platform lock before hash-enforced
installation and bind that measured digest into evidence. Its internal v1 JSON
report is deliberately not a public content contract. `--native off` records
`untested` for native only; it still requires the exact v1 headless platform
matrix (Linux x86_64 or Windows x86_64 with CPython 3.11/3.12). ARM64 can run
lower-level logic/unit checks, but it cannot mint v1 headless/release evidence
or publish a passed deterministic release report. `optional` retains
unavailable or failed status; `required` fails unless bounded native raylib
executes. Release aggregation requires exactly one Ubuntu 24.04 and Windows
Server 2022 report for each Python 3.11/3.12 row with identical source and
fixture hashes. Hosted x86 2x2 evidence remains **PENDING** until this matrix
actually runs green.
The authoritative machine-readable status is
`docs/evidence/multigenre-release-status.json`.

Action, strategy, management/simulation, roguelite storylet, sports-season,
rhythm, multiplayer, VR/AR, and complete 3D profiles may be authoring-valid when
their typed contracts close. They remain runtime-unsupported until every
required mechanic, asset, adapter, platform, persistence, packaging, and native
evidence mapping is implemented. Static validation must not pretend to prove
fairness, balance, fun, performance, or open-ended emergent quality.
Roguelite remains compositional: fixtures may use namespaced mechanics such as
`roguelite:run_reset`, but no standalone primary gameplay-family tag is promoted
unless that exact taxonomy value exists in the machine-readable profile
vocabulary and fail-closed runtime support reports.

## Project and profile boundary

`world-forge.project` v1 identifies one `game`, `universe_library`, or
`asset_library`. It binds an immutable creation profile and source manifest by
format, version, portable path, ID, and canonical content hash.

`world-forge.creation_profile` v1 keeps these facets separate:

- experience promise and audience;
- gameplay family, mechanic tags, verbs, loop, rules, goals, progression,
  teleology, session structure, and social topology;
- optional world presence, topology, scale, time, simulation, and persistence;
- optional narrative requirement, authorship, topology, delivery, agency,
  focalization, variability, pacing, endings, and information structure;
- fiction and tone;
- presentation, input, accessibility, and localization;
- reproducible production, review, provenance, licensing, and QA;
- requested runtime adapter, capabilities, platforms, assets, save/replay, and
  packaging expectations.

Generic creation profile facets are independent. Asset content mode and runtime
support intent are orthogonal: a game defaults to authored assets unless the
profile explicitly proves `not_applicable`, while non-game projects use the
exact non-executable runtime target (`renderer: none`, no platforms, no input
capabilities, no accepted logic formats, no save/replay, and no packaging).
P11/P12 asset applicability is therefore separate from P13 runtime
applicability, and P14 still requires a reviewed handoff.

`world.presence: none` is complete and must keep all world topology, simulation,
and persistence fields at `none`, with no world modules, world modifiers,
world-prefixed dependencies or required features, and with its production mode
set to `not_applicable`. Likewise,
`narrative.requirement: none` is complete and permits no protagonist, arc,
dialogue, quest, ending, delivery channel, narrative-prefixed dependency or
feature, or narrative module, and requires its production mode to be
`not_applicable`. Project default locale must equal the profile source locale,
and presentation and runtime-target modes must agree. Validators reject
invented filler or contradictory handoffs rather than silently projecting
non-RPG work into RPG structures.

## Typed source modules

`world-forge.creation_source_manifest` v1 has five independently referenced
collections:

- `world_modules`;
- `activity_modules`;
- `narrative_modules`;
- `system_modules`;
- `logic_modules`.

Each collection accepts only its matching versioned format. Module
discriminators are closed:

- world: canon, chronology, space, group, character, or knowledge;
- activity: level, mission, quest, scenario, match, race, puzzle, encounter,
  contract, expedition, run, tutorial, or challenge;
- narrative: arc, beat, scene, dialogue, storylet, clue, reveal, memory,
  episode, choice, or ending;
- system: rule, event, consequence, schedule, economy, production process,
  simulation scenario, world modifier, or season.

The activity, narrative, and system shapes contain only shared declarative
identity, condition/effect, transition, presentation, asset-binding, validation,
and provenance references. They contain no executable script field and impose
no RPG-only actor, quest, inventory, geography, or lore requirement.

`world-forge.logic_module` v1 is the closed source-level definition boundary for
authoritative state, action parameters, typed operands, conditions, effects,
ordered rules, goals, failure and recovery, endings, events, presentation hooks,
and the mechanic capability ledger. Every action maps exactly one creation-profile
core verb and explicit source bindings; every required runtime feature is covered
by a mechanic, and every referenced activity, system, narrative option,
presentation hook, event, and asset binding must resolve in the same
integrally validated project. Game projects require exactly one v1 logic module.
Universe and asset libraries cannot contain executable logic.

Each action declares its own required features and owns the complete set of
rules with that `action_id`. Exactly one mechanic mirrors the action's
transitive closure: rule conditions (including composite children), effects,
events, authoritative state reads and writes, presentation hooks, derived asset
bindings, and required features. Bound activities and systems expose the exact
aggregate closure of their actions; narrative options expose the exact
condition/effect closure of their action. Activity lifecycle conditions remain
source-owned phase semantics rather than implicit action preconditions.
Direct `narrative_unit` action bindings remain fail-closed in v1 because the
current unit contract cannot express an exact event, presentation-hook, and
transition closure; option bindings are the only unambiguous narrative action
binding in this version. Activity, system, and narrative-unit source IDs are
globally and cross-kind unique under NFC/casefold for games and libraries alike,
and that check runs before the project-kind logic exit.

Inverse liveness is exact over the integrally loaded project. Every state,
action parameter, condition, effect, rule, event, presentation hook, and
hook-declared asset binding must be reachable from an action/mechanic, goal,
failure, ending, or applicable source lifecycle root. Action-scoped conditions
and effects cannot become live merely through unrelated source padding. Global
conditions legitimately used by activity lifecycle, goals, failures, endings,
or narrative prerequisites remain valid, including constant target state read
by those conditions. Every success ending belongs to exactly one goal; goals,
failures, neutral endings, and failure endings are themselves declared runtime
evaluation roots.

Every effect declares `invalid_transition_policy: reject_transition`. A
candidate action is atomic: invalid array indices, duplicates, capacity
violations, or integer/domain overflow reject the transition without partial
mutation. Set and append operands must be statically proven subsets of the
target domain; string-array cardinality must be possible given its unique
allowed values. Swap rules require exact guards for both indices and an
adjacency distance of one. Rule arrays are canonical by strictly increasing
semantic `order`, not lexical rule ID.

Logic modules are deterministic declarative authoring input, not compiled
gamepacks. They cannot contain scripts, expressions, callbacks, imports, native
code, runtime AI, provider/model/prompt data, credentials, endpoints, or mutable
paths. Their closed operators and effects fail on mixed discriminator payloads,
type mismatches, ambiguous rule order, cyclic condition graphs, constant-state
mutation, unresolved references, unmapped verbs, or unknown required extensions.
Free runtime strings use one Python/JSON-Schema-compatible grammar. It rejects
absolute POSIX, drive-qualified Windows, UNC, URI, dot-segment, extension-bearing
relative-file, scoped-package, and explicit provider/model/credential metadata
forms, including padded forms. It deliberately accepts ordinary prose and slash
notation such as `row/column`, `Choose left / right.`, and
`A prompt: choose the visible symbol.` Identifier and localization fields retain
their existing stricter grammars. Generated TypeScript brands these values as
post-validation strings, while the shared positive/negative corpus is executed
through Python and strict Draft 2020-12/AJV validation.
P02, P07, and P09 may cite an exact logic-module identity as reviewed phase
evidence; unrelated phases cannot. Lorepacks intentionally omit logic modules and
all executable state, actions, rules, conditions, and effects.

Each narrative module declares one or more sorted `entry_unit_ids`. Every
zero-indegree local root must be declared, every local unit must be reachable
from a declared entry, choice transitions must exactly equal their sorted option
targets, and endings cannot have outgoing transitions. Multiple entry points
are therefore explicit authoring semantics, not a loader guess.

## Conditional P00-P14 workflow

Published `rpg-world-forge.phase_report` v1 remains the ready-only legacy-world
contract. `world-forge.phase_report` v2 remains an additive compatible generic
P00-P10 reader and retains its original fields/meaning. Neither is
reinterpreted.

`world-forge.phase_report` v3 is the canonical generic P00-P14 workflow. It
binds exact project, profile, source manifest, reviewer, rationale, evidence,
phase-role output evidence v2, invalidation dependencies, and content hash. Its
statuses are `ready` and `not_applicable`; status never substitutes for the
phase-specific semantic check. The exact catalog and titles are exposed by
`worldforge.creation_workflow.phase_catalog()` and synchronized with
`agents/WORLD_CREATION_PHASES.md`.

The conditional rationale mapping is closed and code-derived:

<!-- not-applicable-codes:start -->
| Phase | Required rationale code |
| --- | --- |
| `p03_geography` | `world_absent` |
| `p04_timeline` | `chronology_absent` |
| `p05_societies` | `group_structures_absent` |
| `p06_characters` | `actors_absent` |
| `p08_world_arcs` | `narrative_absent` |
| `p11_art_audio` | `assets_not_applicable` |
| `p12_asset_specs` | `assets_not_applicable` |
| `p13_asset_production` | `runtime_not_applicable` |
<!-- not-applicable-codes:end -->

Each code is accepted only when phase-report v3 recomputes its corresponding
profile/module/artifact absence rule. Explicit no-narrative design remains a
reviewed profile decision. P13 is compatibility review, not execution proof.
P00-P02, P07, P09, P10, and P14 always require reviewed output. P14 is a
reviewed handoff, not a release claim.

A prose rationale cannot grant a waiver. Ready output must use the exact
phase-role/format matrix; unrelated artifacts cannot satisfy a gate. Reports
are content-addressed, ordered, and revalidated with their complete artifact
registry and history. Before an upstream project/profile/source/artifact hash is
changed, retain the current workflow status hash. After the change, run
`worldforge reconcile-creation --expected-status-hash <previous-hash>` with the
complete current artifact registry before `phase-status`, `reopen-phase`, or
`complete-phase`. The canonical reconciliation transition validates recorded
and current identities, archives current immutable inputs, appends invalidation
history, and uses status-hash CAS; it never overwrites historical reports.
Repeating it is idempotent only when the expected hash names the coherent
current status.

Authoring validity is not runtime executability. Phase completion does not
promote compilation, asset state, adapter verification, per-platform execution,
packaging, or release.

## Immutable lorepacks

`world-forge.lorepack` v1 seals reusable world and narrative material without
turning a worldpack into a generic union. A lorepack binds the exact creation
project, profile, and source manifest; embeds closed, deterministic lore
projections rather than raw source modules; records a closed, acyclic set of
hash-bound lorepack dependencies; enumerates every localizable projection
string by resolved reference; and retains exact, unique source-contract plus
dependency provenance.

Lorepacks cannot contain activity or systemic gameplay modules, actions, rules,
effects, goals, runtime requirements, scripts, prompts, provider details,
credentials, or mutable authoring paths. The builder refuses a project with no
world and no narrative modules instead of emitting fabricated or empty lore.
World projections copy only semantic fact, chronology, space, group, character,
and knowledge fields and intentionally omit raw fact sources. Narrative
projections preserve authored topology, titles, choices, targets, and endings
while omitting conditions, effects, prerequisites, asset bindings, and other
executable system hooks. Every projection carries the exact source-module
identity and is re-derived during integral validation.

The loader uses the same strict retained-snapshot boundary as the creation
contracts, revalidates every projection and source reference, checks the
complete bounded dependency graph, and rejects missing, mismatched, cyclic, or
unreachable dependency inputs. Dependency documents require an explicit bounded
mapping to their own exact loaded creation projects; structurally valid but
re-sealed dependency projections or stale source manifests therefore fail
closed.

## Deterministic generic compilation

`world-forge.gamepack` v1 is the immutable generic logic artifact. It is
parallel to, not a replacement for, `isoworld.worldpack` v5. The retained
`worldforge compile` command preserves the canonical v5 output contract and
published legacy reader/storage compatibility described above; its loaders and
runtime boundaries may still gain explicit unsafe-input and identity hardening.
Generic game projects use
`worldforge compile-game` and must supply exactly one integrally valid
`world-forge.logic_module` v1. Compilation never derives executable semantics
from a gameplay-family label, fiction tag, presentation mode, requested
adapter, or identifier spelling.

The gamepack binds exact project, profile, source-manifest, module, and logic
identities. It contains closed runtime projections of applicable world,
activity, narrative, and system modules; the typed state schema and exact
initial state; source actions, conditions, effects, semantic rule order, goals,
failures, endings, events, presentation hooks, and mechanic mappings; normalized
presentation/runtime requirements; localization references; required but
unproduced asset bindings; and source provenance. Raw activity provenance and
validation prose, world fact sources, production/provider metadata, authoring
paths, prompts, credentials, scripts, native payloads, runtime AI, and mutable
source paths are excluded.

Gamepack v1 intentionally supports one bounded branching-narrative compilation
shape: one explicit narrative entry and one exact logic action for every
authored choice option. The compiler adds the saved
`wf_internal_narrative_cursor` plus compiler-owned cursor preconditions and
effects. Every compiler-owned cursor, transition, precondition, and effect carries
the required `compiler_owned: true` discriminator; source projections forbid that
field. Narrative-free state schemas contain source states only. Narrative-bearing
state schemas contain exactly one cursor, canonically last. Narrative-module
cardinality is correlated with that logic branch: an empty narrative projection
requires narrative-free logic, while any narrative projection requires the
compiler cursor branch. Python validation, strict AJV validation, and generated
TypeScript enforce the same separation.
Each transition binds the exact source unit, option, target, action,
source condition set, and source effect set, and uses `reject_transition`, so
source effects and cursor movement form one atomic candidate transition.
Reachability is computed before lowering. Reachable scene, beat, or other
non-choice transitions fail with `narrative_transition_unsupported` until their
execution semantics are versioned; the compiler never emits a cursor that can
silently become stuck. Multiple entries, ambiguous option bindings, missing
option actions, unsupported narrative topologies, unsupported platform
identities, required extensions, or missing logic fail with stable reason
codes. A `narrative:none` puzzle receives neither a cursor nor invented
narrative content.

Asset requirements are derived only from exact source asset bindings,
presentation hooks, and mechanic closures. They name accepted target formats,
runtime roles, usage contexts, and every referencing subject, but do not claim
that an asset was produced, processed, licensed, checked, or sealed. Runtime
requirements preserve the requested adapter and accepted logic formats while
normalizing required features, platforms, architecture, renderer, input,
save/replay, and packaging expectations. An adapter request is a declaration,
never evidence of support.

`world-forge.mechanic_capability_ledger` v1 binds the exact gamepack hash and
maps every mechanic through its action, authoritative state, conditions, rules,
effects, presentation hooks, asset bindings, and save/replay representation. It
also covers every required runtime feature. The compiler's
authoring-only builder can emit only `authoring_only` with
`adapter_not_evaluated`; it emits no test or native evidence and cannot mark an
adapter verified. JSON Schema and generated TypeScript encode exact
status/reason/missing-feature/extension/evidence branches. The default Python
validator fails closed on every verified claim. Trusted resolution additionally
requires an exact registered adapter descriptor and platform matrix,
independently loaded and SHA-256-verified test/native evidence, and—when
`game_extension_verified` is claimed—an exact extension registered in both the
gamepack and the trusted extension registry. Evidence IDs cannot be reused
across claims or categories.

Optional source extensions retain their integer version and exact content hash
when lowered into `registered_extensions`. Exact declarations shared by multiple
source documents are deduplicated; conflicting versions, required flags, or
hashes for one extension ID fail closed. Trusted adapter, extension, and evidence
registries are accepted only as exact dictionaries, copied into validated
snapshots, and checked for exact frozen descriptor types before semantic access.
Malformed containers, mapping subclasses, and descriptor impostors therefore
produce stable GamePack reason codes rather than Python attribute or type errors.

The command preflights the gamepack and optional ledger destinations without
creating their parents, refuses every overwrite, and retains the complete
root-to-leaf output ancestry while the writer creates missing directories and
publishes relative to the retained final parent. Linux publication uses an
anonymous `O_TMPFILE` and descriptor-bound `linkat`; Windows retains
non-reparse directory and temporary handles that deny delete sharing and uses a
handle-relative native rename. Overwrite serialization never removes a lock by
pathname, and a failed named Linux replace stage is retained rather than
deleting a possible foreign replacement. Secure `write_json_atomic` publication
is create-only: fixed-path replacement and content-hash CAS fail closed because
Linux and Windows expose no shared identity-conditional replacement primitive.
Legacy mutable asset-manifest v3 transitions use the explicitly named
cooperative replacement API; their hash precondition is not a security boundary
against external writers. File and retained-parent
flush/fsync complete the durability boundary; a platform or filesystem without
the required primitives fails closed. Cross-platform two-file publication is
not falsely described as atomic. If ledger publication fails after the
gamepack is durable, Forge does not roll that publication back and the CLI emits
a hash-bound `partial_publication` recovery receipt; recovery must reverify the
recorded hash because an external directory mutator remains outside the
publication guarantee. Forge never deletes a pathname that another writer may
have replaced. Machine-readable success output reports
compilation as `compiled`, assets as `unplanned`, the adapter as `declared` or
`absent`, and release as `blocked`. Neither generic compilation nor an
authoring-only ledger is runtime compatibility evidence.

The canonical puzzle and branching-narrative gamepacks, ledgers, and analysis
reports are generated
only by `python -m scripts.generate_gamepack_fixtures --write`; running the same
module without `--write` compares every byte. Building from copied project roots
produces identical bytes and hashes.

## Generic asset planning boundary

Generic gamepacks now enter an additive, provider-neutral planning chain:

```text
world-forge.gamepack v1
  -> world-forge.asset_subject v1
  -> reviewed world-forge.asset_target v1
  -> reviewed world-forge.asset_style v1
  -> deterministic world-forge.asset_inventory v1
  -> complete world-forge.asset_spec v1 set
```

These contracts do not relabel or widen any `rpg-world-forge.*` asset format.
The legacy worldpack asset target, bibles, inventory, specification,
production, processing, QA, manifest, renderpack, and assetpack remain separate
published contracts. The asset subject kinds are `gamepack` and
`legacy_worldpack`. The latter is a recognized subject tuple for
future bridging, but this generic derivation path accepts only an integrally
loaded `world-forge.gamepack` v1. A copied ID and hash without full gamepack
validation is not a verified asset subject.

The reviewed target covers every compiled `asset_requirements` binding exactly
once. Each entry preserves requiredness, roles, usage contexts, and referencing
subjects, and selects an explicit asset ID, accepted format, kind,
representation, physical output roles/media types, and sharing policy. Sharing
is never inferred from filenames, genre, or list order: two bindings may name
one asset only when both declare the same sharing group and identical physical
choices. The branching-narrative fixture therefore shares one reviewed TTF
asset between `choice_panel` and `ending_panel`; the no-world/no-narrative
puzzle has one exclusive board PNG and invents no fictional or audio assets.
Target validation computes each explicit shared group's deduplicated binding,
role, context, and subject unions before inventory allocation. Per-array and
global graph limits therefore close the transformation: every accepted target
can produce an inventory without truncating or dropping lineage. NFC-normalized
usage-context aliases are unioned by casefold, with the byte-smallest spelling
selected deterministically for the inventory, so cross-binding capitalization
cannot expand the materialized array beyond the validated union.

All generic identifiers use one portable lowercase 2-to-64-character domain.
User-supplied target, style, inventory, and specification IDs remain valid only
inside that domain. Defaults never append suffixes to caller IDs: each uses a
fixed contract prefix and a 48-hex-character (192-bit) fragment of a canonical
SHA-256 over its immutable upstream identities and exact transition inputs.
Subject IDs use the same scheme over the complete subject tuple. This keeps
maximum-length game and asset IDs safe while making default IDs deterministic
and collision-resistant.

The style contract binds the exact subject and target while keeping visual and
audio direction independent from gameplay and fiction labels. It records
presentation mode, visual language, camera/coordinates, resolution/aspect,
palette and contrast, color independence, silhouette/readability, typography,
motion and reduced-motion behavior, UI hierarchy/density, accessibility, and
localization expansion. Audio is a closed `defined` or `not_applicable`
branch. A not-applicable branch cannot retain fake music, SFX, voice, mix, or
format fields; required audio prevents that branch. A 3D physical choice fails
under a text/2D-only gamepack even if its JSON shape is otherwise valid.
Camera direction, UI density, and JSON expectation schema IDs use the same
256-character structural bound in Python and strict Draft 2020-12 validation.

The closed physical matrix currently supports:

- UI, portrait, sprite, and VFX PNG textures in 2D/2.5D;
- PNG texture plus JSON clipset outputs for spritesheets and tilesets;
- TTF or OTF fonts in 2D/2.5D;
- PCM16 WAV for SFX and music;
- paired vertex/fragment GLSL outputs in 2D, 2.5D, or 3D;
- schema-validated JSON localization output;
- GLB model, animation, collision, and skeleton roles for the declared 3D
  kinds.

The inventory is a pure rebuild from the exact gamepack, subject, target, and
style. It groups only explicit compatible sharing choices and carries the
complete source-binding/context/subject lineage plus target/style identities.
Manual additions and genre inference are impossible in this version. Each
specification then binds one exact inventory item by hash and declares every
runtime output path, media-specific bounds, acceptance criteria, deterministic
production class, and mandatory review/QA evidence. A complete set contains
exactly one specification per inventory asset with no portable-path or
NFC/casefold collision. Runtime paths use printable ASCII and are compared as
normalized POSIX component trees, so file/directory prefixes and component
aliases such as `assets/Fonts` versus `ASSETS/fonts` also fail closed on Linux
and Windows.
Font glyph ranges use canonical uppercase hexadecimal endpoints in the exact
machine grammar
`^U\+[0-9A-F]{4,6}-[0-9A-F]{4,6}$`; ranges must be numerically ordered,
bounded by `U+10FFFF`, unique, and non-overlapping.

The five D1 planning contracts are closed, bounded, canonically hashed,
strict-JSON loaded, and create-only published. Their schemas, Python validators,
generated TypeScript, AJV probes, catalog entries, and fixtures share the same
discriminators and matrix. D2a extends that planning boundary with five
production-lineage contracts, and D2b adds four processing/readiness contracts.
Canonical fixtures are generated only by
`python -m scripts.generate_generic_asset_fixtures --write`; check mode compares
32 artifacts byte for byte: ten planning JSON documents, ten D2a production
JSON documents, eight D2b JSON documents, two deterministic candidate binaries,
and two deterministic processed binaries.

Generated TypeScript exports raw structural shapes and strict negative probes
for known forbidden fields and discriminated matrix branches. TypeScript's
structural assignment rules cannot enforce arbitrary
`additionalProperties: false` keys on pre-bound values. Studio boundaries
therefore call the strict AJV-backed `validateGenericAssetContract`, which
first creates an own-property-only detached clone, validates that exact clone,
and returns it frozen and branded as `ValidatedGenericAssetContract`; inherited
or unknown fields cannot enter that validated domain.

The D1 planning boundary deliberately stops before production evidence. D2a
adds requests, receipts, candidate selection, provenance, and license records
with integral verification. D2b adds deterministic recipes, processing
receipts, QA reports, and release-readiness manifests. D3 is a separate
runtime-only sealing boundary that consumes only an exact `release_ready`
manifest and publishes `world-forge.assetpack` v1. It still stops before
runtime bundles and standalone-game import. A reviewed inventory or
specification is not evidence that an asset exists; a produced candidate is not
processed, a `release_ready` manifest is not sealed, and a sealed assetpack is
not runtime-compatible or released.

## Bounded game-logic analysis

Every `world-forge.gamepack` v1 now carries compiler-owned execution semantics.
All rules owned by an action are mandatory; condition collections are ANDed
against the pre-transition snapshot; effects run by rule order and then exact
reference order; effect state operands read the current candidate state; and
events commit only after the complete transition succeeds. Active failure
recovery actions are intersected. Endings take precedence over failures and
more than one matching ending is an integrity error. A narrative choice applies
its source effects before its cursor movement in one atomic candidate
transaction. Any invalid effect, state-domain violation, cursor divergence, or
unsupported operator rejects without state, event, or cursor mutation.
`append_unique` rejects both duplicates and capacity overflow rather than
committing a no-op. Compiler-owned narrative bindings preserve semantic rule
order and exact condition/effect reference order; they are not sorted as
set-like identifiers.

Analyzer selection is structural rather than genre-derived. A no-world,
no-narrative game whose activities are explicitly puzzles uses the frozen abstract
puzzle analyzer. A game with a compiled narrative cursor and narrative
projection uses the frozen branching-narrative analyzer. Every other valid
gamepack receives an explicit unsupported requirement with
`analysis_profile_unsupported`; it is never reinterpreted as one of the
executable profiles. The gamepack pins the analyzer identity, version, limits,
and their hash.

`world-forge.game_analysis` v1 is produced by `worldforge analyze-game`. It uses
canonical breadth-first first discovery and compact canonical state hashes.
The fixed limits are 262,144 candidate evaluations, depth 512, 4,096 parameter
combinations per action, 65,536 bytes per state, 16,384 states, 67,108,864
aggregate state bytes, 128 witness traces, and 4,096 total witness steps.
Reaching any state-search bound before frontier closure is `inconclusive`, never
`passed`. Missing goals, endings, narrative units/options, solvability, and
terminal reachability are not reported as failures from a partial graph.
Exhausting the separate witness budget also makes the result `inconclusive`,
while `metrics.frontier_closed` truthfully remains `true` when the state
frontier itself was exhausted.
The report binds the exact gamepack and analysis requirement, records checks,
findings, metrics, and deterministic shortest witnesses uniquely keyed by their
semantic target. Different goals, endings, failures, narrative units/options,
or counterexample states may share the same shortest action trace without
collapsing their target identities. Reports are integrally
validated by rerunning the analyzer and comparing canonical bytes.

Puzzle analysis checks initial solvability, authored goal/ending reachability,
failure recovery, explicit reset-based recovery, nonterminal softlocks, and
whether every reachable nonterminal can reach an authored terminal. Branching
analysis additionally checks cursor/unit/option reachability, at least two
distinct reachable endings, cursor/logic ending agreement, and terminal traps.
These are exhaustive only for the closed state graph within the declared
bounds. They do not prove asset readability, interaction quality, timing,
save/replay serialization, native adapter execution, platform behavior, or
performance.

## Generic asset production lineage

Generic gamepacks use a parallel additive asset namespace rather than changing
published worldpack contracts. Planning produces an exact
`asset_subject -> asset_target -> asset_style -> asset_inventory -> asset_spec`
chain. D2a then adds five versioned authoring records:

```text
asset_spec
  -> asset_production_request
  -> asset_production_receipt
  -> asset_selection
  -> asset_provenance_record
  -> asset_license_record
```

Each transition repeats the exact gamepack and complete planning-chain
identities. Requests explicitly select a closed human, offline procedural,
external authoring, or generative authoring toolchain. Fixed and recorded
offline procedural requests require concrete integer seeds. Completed receipts
bind the exact output-role set and derive hash, size, media/container identity,
and bounded metadata from one retained capture of each candidate. GLB validation
uses those bytes directly rather than reopening a named temporary and reports
the exact seven production metrics. Input artifacts are also checked against
exact retained bytes. Receipt, selection, provenance, and license candidates
share the same structural role/media discrimination, including exact GLSL
role/stage pairing.

Selection binds only exact completed output identities; rejected candidates
cite their complete integral receipt identity. Its versioned
`asset_receipt_lineage` map contains one independent root closure for every
selected or rejected receipt, and the closure keys must exactly equal those
roots. Each parent must be a completed receipt for the same closed request and
is integrally revalidated against the planning identities, toolchain, retained
inputs, and candidate bytes. Provenance materializes the selected root's exact
transitive receipt and input edges. Exact input identities repeated across
receipts become one shared source node; crossed metadata or content-hash aliases
are rejected. Provenance scopes are mechanically derived from the actual
toolchain and request inputs, and license records must cover those same scopes.
Their separately hashed runtime notice rejects authoring-only provider, model,
weights, seed, instruction, and dataset details plus concrete credential
signatures without rejecting legitimate narrative vocabulary.

The abstract-puzzle proof uses a deterministic high-contrast PNG. The branching
narrative proof uses a project-authored readable 5x7 outline TTF. Printable
ASCII maps to distinct glyph IDs, blank space remains blank with advance,
non-space outlines are bounded and nonblank, critical lookalike pairs differ,
and deterministic authored-design masks cover every fixture string under the
versioned `narrative-fixture-design-mask-evidence-v2` domain. Pillow rendering
is only an explicit BASIC-layout diagnostic smoke, not release hash evidence
and not dependent on RAQM availability. The glyph design and emitted font bytes
are CC0-1.0 while the generator stays MIT. Both have specification, request,
receipt, human selection evidence, exact provenance and license evidence,
deterministic processing, retained-byte QA with opaque sorted evidence hashes,
and a release-readiness manifest. They remain unsealed authoring artifacts. A bounded
integration matrix additionally proves complete D2a chains for PNG, atlas
PNG/JSON, WAV, TTF, OTF, paired GLSL, schema JSON, GLB, and paired GLB. The
matrix covers all four production classes and both source-input states, and
crosses every media byte budget negatively.

## Generic asset processing and readiness

D2b continues the exact selected D2a chain without widening legacy
`rpg-world-forge.*` meanings:

```text
asset_selection + asset_provenance_record + one license per selected candidate
  -> world-forge.asset_processing_recipe v1
  -> world-forge.asset_processing_receipt v1
  -> world-forge.asset_qa_report v1
  -> world-forge.asset_manifest v1
```

The fixed `world_forge_generic_asset_processor` v1 admits only validated PNG,
PCM16 WAV, TTF/OTF, vertex/fragment GLSL, and GLB copies plus canonical clipset
or localization JSON. JSON canonicalization requires modification permission.
Every recipe step repeats the exact selected candidate, license, source hash,
source size, media role, runtime path, and portable output locator. Processing
reads retained standalone bytes, validates and hashes that same capture, and
publishes each output create-only. Candidate and role are part of the repeated
license identity, and all source/runtime/output locators share one portable
NFC/casefold path tree. Failed receipts contain no completed outputs, exactly
one failure reason, and a hash-bound recovery record. A partial publication
records the exact deterministic output prefix already retained, with complete
source, role, media, locator, hash, size, and metadata evidence. The processing
error exposes that validated receipt. Integral recovery rereads the sources and
retained outputs and recomputes the expected bytes; retry reuses an existing
output only when those bytes match exactly. It never deletes or claims a path
owned by another writer. Durability and identity failures remain failed even
when their bytes are recoverable; immediate reuse is restricted to an explicit
create-only collision.

QA independently captures each retained processed output once through the
bounded standalone boundary, then derives its hash, size, media/container
metadata, portable paths, license coverage, and checks from that same buffer.
It never treats receipt metadata as QA evidence. Applicable hash, media, and
format checks are mechanically `passed` or `failed`; irrelevant format checks
are `not_applicable`, and failed inspection records null metadata. Acceptance
criteria bind their exact index, text hash, status, and evidence hashes. The
report status and canonical criterion/output blocker union are derived rather
than caller-selected.

The manifest requires every required inventory asset, rejects unknown or
NFC/casefold-colliding assets and paths, and permits optional inventory omission.
Its three states are exact: `produced` cites no processing, `processed` cites a
completed recipe/receipt but no QA, and `release_ready` additionally requires
passed QA plus commercial-use and redistribution permission. `release_ready`
means only that D3 sealing may be attempted; the manifest contains no sealed,
published, runtime-compatible, or adapter-verified claim.

## Generic assetpack sealing

D3 introduces one additive public format, `world-forge.assetpack` v1, rooted at
the canonical `assetpack.json` of an immutable directory. It does not widen the
fourteen D1/D2 authoring formats and does not reinterpret either published
legacy `assetpack_v1`/`rpg-world-forge.assetpack` contract. The pack retains:

- exact gamepack, subject, target, style, inventory, and release-ready manifest
  identities;
- one canonical asset entry per complete D2 lineage, but only hashed lineage
  identities rather than authoring documents;
- exact processed runtime payload paths, roles, media types, bytes, hashes,
  byte-derived metadata, and sealed constraints;
- exact runtime-safe license identities and deduplicated content-addressed
  public notice bytes;
- a nested canonical file inventory whose hash participates in the pack ID and
  top-level content hash.

The writer is deterministic and create-only. Publication uses retained
parent/directory anchors, exclusive file creation, exclusive no-replace
directory primitives, and an internal append-only journal with exactly
`intent`, `copying`, and `ready` states. Recovery revalidates a fully retained
owned stage before publishing it; rollback removes only the same directory
identity and exact retained hashes. A torn transition is truncated only through
the retained journal handle after proving the entire raw complete history is
exactly `[intent]`, `[intent, copying]`, or `[intent, copying, ready]` with
unchanged operation, source, root, stage, and content identities, plus an exact
partial sole next frame. Duplicate, reordered, skipped, conflicting, extra, or
post-ready frames remain preserved before mutation. Repair then flushes the
journal and parent before retry. A partial tail without an applicable next
transition is preserved. Intent never claims an existing unbound stage path;
copying may remove only its identity-bound empty pre-file stage, while
manifestless nonempty evidence stays ambiguous. Successful publication or
recovery durably removes the exact journal and, while retaining the parent and
publication lock, rechecks every original stage and journal name after deletion
and immediately before success. Any same- or different-identity file,
directory, link, or reparse-point reappearance makes cleanup indeterminate.
Same-hash retries are idempotent. Unsupported primitives, replacement,
links/reparse points, hardlinks, identity drift, foreign files, and ambiguous
partial failure remain failed. The public directory never contains the journal
or any Forge authoring material.

Structural validation proves only contract shape and canonical coherence.
Integral verification separately pins the exact tree, rejects missing or extra
entries, captures each regular single-link payload once, derives its hash,
size, media metadata, and constraints from those bytes, validates UTF-8 notice
bytes, and rechecks the unchanged tree before returning frozen
`status: sealed` evidence. Studio ships a separate private-realm D3 schema
validator and integral verifier, plus packaged CJS/ASAR puzzle and narrative
smokes with same-size tamper rejection. D3 remains outside the D1/D2 generic
authoring dispatcher and its exact fourteen-format descriptor.
The packaged JavaScript GLB inspector is intentionally narrower than the Python
media boundary: it accepts URI-free, image-free dense-accessor GLB 2.0 with
unlit or mesh-quantization extensions and rejects broader valid GLB shapes.
A shared mutation corpus proves that this documented subset is accepted or
rejected identically by Python and Studio for node, scene, accessor, transform,
and animation boundaries. This is a fail-closed verification limitation, not a
claim that textured, sparse, camera/light, or morph-weight content was sealed
by Studio.

Sealing deliberately does not select or verify a runtime adapter. The separate
Studio `runtime.compose` transition re-verifies one published sealed assetpack,
resolves only the code-owned adapter registry, and emits candidate runtime
snapshot, registry, composition, and evidence-free support report artifacts.
Standalone-game import, deterministic execution, save/replay, native raylib
evidence, packaging, and release status remain later transitions and must cite
the same immutable assetpack and gamepack hashes.

### Studio D3 sealing authority

Studio protocol v4 exposes deterministic processing and D3 sealing through
closed durable jobs, not through a generic renderer command. The additive
`studio_creation_output_grant` v1 is the pathless public projection of a private
SQLite capability for one absent assetpack directory. Electron main owns the
native save dialog and privately supplies the absolute path and fixed
`generic_assetpack_directory` kind. The renderer supplies only content IDs and
CAS generations.

`studio_creation_job` and the isolated worker use v3 only for
`asset.release.seal`, v4 only for `runtime.compose`, v5 only for
`runtime.bundle.build`, v6 only for `game.materialization.bundle.build`, and v7
only for `game.materialize`, and v8 only for `game.package`;
earlier semantics are unchanged. Output grants similarly use v1 for sealed
assetpacks, v2 for runtime bundles, v3 for materialization bundles, and v4 for
standalone game directories, and v5 for deterministic game-package files.
Sealing validates the
complete candidate QA/license/processing lineage, reads and stages every binary
from its declared receipt, derives both canonical manifests from those exact
bytes, and publishes without replacement. The job result, success event,
candidate registry, and published grant must project the same assetpack
identity. Publication intent is durable before visible bytes; explicit resume
and rollback revalidate filesystem identity and hashes, fail closed without a
safe platform primitive, and never delete foreign or ambiguous bytes. These
fixed APIs are contract capability only; this slice adds no generic renderer
workflow and no runtime/package/preview escape hatch.

### Studio creation-asset preview leases

The closed `world-forge.studio_creation_preview` v1 record adds a separate
protocol-v4 inspection boundary for sealed generic assets; it does not broaden
the legacy protocol-v2 world-asset preview contract. `creation_preview.open`
accepts only workspace/source/workflow/artifact CAS authority, one current
assetpack candidate ID, one exact published output-grant generation, and one
asset ID. The service revalidates candidate, grant, canonical assetpack, tree,
and file identity before opening and around every read. Only a unique PNG or
PCM16 WAV output is eligible.

The lease is pathless and ephemeral: 64 MiB maximum per artifact, fixed 64 KiB
sequential chunks, immediate-previous replay only, opaque handles, bounded
workspace/global quotas, idle and absolute expiry, reaping, and shutdown
cleanup. Symlinks, reparse points, hardlinks, non-regular entries, non-NFC
names, and casefold aliases fail closed. Electron and preload expose only the
three fixed methods; NDJSON uses canonical bounded base64, while renderer-
selected paths, offsets, ranges, formats, and generic RPC remain impossible.
No renderer media component is introduced by this contract slice.

The v7 worker receives a closed, pathless request plus the exact staged
materialization-bundle bytes and emits only the deterministic
`world-forge.standalone_game` manifest candidate. It never receives the external
target path. The trusted coordinator re-verifies the published source-grant
identity before and after work, materializes through the canonical standalone
publisher, and commits one grant/job/event/candidate projection bound to the
manifest `content_hash` and payload-lock `tree_hash`. Recovery and rollback use
the same retained identities and hashes, preserve foreign bytes, and fail closed
where safe removal is unavailable. This transition proves materialization only;
native and packaging evidence remain independent and release-blocking.

The v8 worker receives the exact staged bytes of one published standalone-game
candidate and emits one canonical `world-forge.game_package` v1 manifest plus
one fixed private archive output. The trusted coordinator, never the renderer,
owns the selected absent `.wfgame` path and binds source grant generation,
standalone root identity/tree hash, target-parent identity, published file
identity, manifest hash, archive SHA-256, and byte size across restart and final
registry CAS checks. The public job, grant, event, and candidate projections are
pathless. A visible file without a previously retained identity is preserved
and requires explicit recovery rather than being adopted or removed. This
transition proves deterministic packaging only; extraction remains a separate
CLI transaction, and no renderer workflow control or native-execution claim is
added.

The v5 transition publishes the exact runtime composition as a reusable
`world-forge.game_runtime_bundle` v1. The v6 transition accepts only that exact
candidate plus its verified published grant, derives the code-owned runtime
implementation and the complete Linux/Windows Python 3.11/3.12 lock set, and
publishes `world-forge.game_materialization_bundle` v1 without replacement.
Both transitions keep absolute paths private to Electron main/Python, bind
source and target grant generations, retain recovery evidence, and commit the
same identity to the grant, job, event, and candidate registry. A v6 candidate
is materialization-ready but release-blocked: it does not claim native execution
or package verification and does not execute the game.

`runtime.compose` is a fixed, pathless renderer capability. Its public request
contains exact candidate artifact IDs plus one published output-grant ID and
generation. Python main joins that grant to the successful sealing job,
re-verifies the directory identity, manifest, bytes, hashes, and complete
transitive artifact lineage, then stages only the exact sealed files for the
isolated v4 worker. The worker derives its snapshot and registry from installed
code-owned runtime packages; authoring data cannot inject adapter IDs or code
paths. A declared adapter that covers every required feature is sufficient for
candidate materialization readiness. Optional feature gaps remain reason-coded
and non-blocking at this boundary, while the evidence-free support report stays
truthfully release-blocked. Required gaps fail closed before any candidate is
committed.

## Generic runtime compatibility boundary

The next additive boundary introduces six `world-forge.*` v1 contracts without
changing the published legacy runtime-adapter schema or making `isoworld` a
generic engine:

```text
world-forge.runtime_adapter
  + world-forge.game_runtime_snapshot
  -> world-forge.runtime_adapter_registry

gamepack + asset_inventory + sealed assetpack
  + registry + runtime snapshot
  -> world-forge.game_runtime_composition
  -> world-forge.runtime_evidence
  -> world-forge.runtime_support_report
```

The active code-owned registry contains exactly two bounded descriptors:
`gamepack_raylib_2d_puzzle@1.1.0` and
`gamepack_raylib_2d_text@1.1.0`. Exact v1.0.0 descriptor bytes remain under the
historical fixture path for audit and reader compatibility, but are deliberately
non-resolvable because an active registry cannot contain duplicate adapter IDs.
Descriptor documents are declarative, contain
no import/module/source paths or executable content, and name concrete
`backend:raylib` platforms. In contrast, a gamepack request retains
`backend:unspecified`; resolution proves that one concrete descriptor satisfies
the complete requested profile, logic format and version, execution semantics,
required capabilities, presentation, inputs, asset formats, persistence,
packaging target, and platform matrix. Zero and ambiguous matches fail closed.
Fiction or genre labels never select an adapter.

The runtime snapshot hashes the exact neutral `gamepack_runtime` source tree,
the bounded `gamepack_raylib_2d` implementation source tree, and the canonical
descriptor bytes. The registry binds the snapshot identity and
rechecks each descriptor virtual path, byte size, and SHA-256 against its
canonical serialization. Roots, mtimes, enumeration order, bytecode caches, and
host platform do not affect the snapshot. A self-consistent snapshot or registry
is only a transfer-integrity document: every resolver and CLI trust path
re-reads the installed code-owned kernel through retained, no-following,
standalone-file handles and rebuilds the complete snapshot and registry. Exact
paths, sizes, SHA-256 values, tree hash, descriptor bytes, derived identities,
and canonical bytes must match. Re-sealing arbitrary kernel hashes cannot create
trust.

The execution-semantics reference is also closed. Python owns one version/hash
pair derived from the neutral policy; the schema generator emits that exact
`const` and a generated Studio module. Source Studio, the built CJS validator,
and the ASAR smoke consume the generated value rather than maintaining separate
hand-written hashes.

A composition is created only after integral gamepack, D1 inventory, and
retained-byte D3 assetpack verification. It carries the same immutable
gamepack, inventory, assetpack, adapter, registry, and snapshot identities and
maps every runtime binding to one exact sealed path, media type, size, and
SHA-256. It contains no authoring inventory, production prompt, provider data,
or executable code.

Evidence documents are external content-addressed claims for one exact
composition, adapter, and concrete platform. Headless, native raylib,
save/replay, and package checks remain distinct. A support report maps every
required mechanic through runtime action, authoritative state,
condition/rule/effect, presentation, asset binding, and save/replay event/state
IDs. It reports authoring, compilation, assets, adapter, per-platform execution,
packaging, and release independently. A valid sealed project with a declared
adapter remains `partially_supported` and `release: blocked` until exact
evidence exists and the descriptor itself is verified. Positive execution,
packaging, adapter, mechanic, feature, and release claims require canonical
non-empty evidence identities. Those identities project exact evidence hash,
platform, terminal states, and passed check kinds; integral validation resolves
every reference against the supplied evidence objects and rejects missing,
extra, fabricated, duplicate, or crossed evidence.

Those v1 evidence and support documents remain structural claims; parsing or
re-sealing them does not grant release authority. The additive external
`world-forge.runtime_support_authority` v1 companion is created only as an
opaque in-process handle after exact re-verification of the gamepack,
inventory, composition, code-owned registry and snapshot, retained assetpack,
and authorized asset release. A serialized companion remains audit data and
cannot be loaded back into that handle. Exact headless state may be attached
only from a `VerifiedHeadlessEvidenceSet`, and packaging state only from the
same-lineage verified archive, extraction evidence, and retained extracted
standalone. Cross-composition, cross-bundle, cross-package, and duplicate
platform evidence fail closed. Native attachment is deliberately unavailable
in v1, so every derived support report remains `release: blocked` and
`supported: false`.

The puzzle and branching fixtures publish deterministic initial companions
beside their runtime contracts. Each companion derives the existing
evidence-free support-report bytes without drift. Companions remain outside the
assetpack, runtime bundle, materialization bundle, standalone game, and game
package. Creation readiness ignores raw evidence, support, and package presence
without the opaque handle and emits
`runtime_evidence_authority_missing`,
`packaging_evidence_authority_missing`, or
`native_evidence_authority_unavailable` as applicable. The inspection CLI still
validates supplied structural evidence but cannot use it to change the blocked
report hash. Studio v4 has no retained runtime-authority job and therefore stays
fail-closed; a future versioned protocol must add that job rather than trusting
renderer/imported JSON.

Platform IDs are closed projections, not labels. For this slice,
`platform:linux_x86_64` means exactly Linux, x86-64, raylib and
`backend:raylib`; `platform:windows_x86_64` means exactly Windows, x86-64,
raylib and `backend:raylib`. Adapters, compositions, evidence, and reports reject
unknown, duplicated, reordered, or contradictory projections. Runtime snapshot
files are limited to 4 MiB each and 32 MiB in aggregate; composed bindings are
limited to 16 MiB. A generated shared reseal corpus exercises these rules in
Python, source Studio, built CJS, and ASAR verification.

The additive `world-forge.game_runtime_bundle` v1 transition now packages the
exact gamepack, D3 assetpack, runtime snapshot tree, selected descriptor,
registry, composition, evidence-free support report, transfer bindings, and
runtime notices into one immutable exact-tree directory. D1 is validated while
building but is deliberately omitted from the runtime-only result. The root
manifest binds every file hash and size, the D3 root/inventory identities, the
runtime tree identity, and one canonical bundle hash. Publication uses a native
no-replace directory transition plus an append-only `intent -> copying ->
ready` journal, with identity-bound recovery and rollback on Linux and Windows.
Integral verification reconstructs the permitted closure independently from
the root inventory: fixed contracts and status, the exact D3 manifest subtree,
the code-owned trusted runtime snapshot tree, the audited code license, and the
exact UTF-8 runtime notices. A hashed and resealed authoring, provider,
evidence, cache, or otherwise unclassified file is still rejected.

Final publication keeps the native publication lease open while rechecking the
visible destination identity and exact tree, original stage-name absence,
journal history and identity, and lock identity immediately before journal
deletion. It then rechecks the destination, absent stage, absent journal, and
lock before returning evidence. A late replacement is indeterminate and never
returns cached `integrity: valid` evidence. A crash after the private stage is
created but before the `copying` journal binds its identity is deliberately
ambiguous: automatic recovery and rollback preserve both stage and journal for
manual evidence review rather than guessing ownership.

An integrally valid bundle remains `state: pre_execution`, `release: blocked`,
and `supported: false`. It contains no runtime evidence, authoring prompts,
provider records, executable adapter, or native binary. Studio exposes this
contract through a separate validator/CJS entry rather than broadening the six
generic runtime contracts. Source, built CJS, and retained-ASAR verification
compare the transferred runtime bytes against one Python-policy-generated
code-owned byte map and exercise both neutral vertical fixtures, same-size
tampering, and a shared fully resealed closure/provenance/lineage corpus. This
remains transfer integrity only.

The additive `world-forge.game_execution_script` v1 transition provides
bounded, canonical scenarios for an exact gamepack, composition, bundle,
adapter, and runtime snapshot. The code-owned
`gamepack_runtime.headless.v1` executor rejects unsupported hosts before
execution, runs every scenario twice, verifies the exact expected state and
terminal classification, restores an immutable save, and replays the accepted
action trace. Its `world-forge.headless_execution_receipt` v1 records complete
required-action and required-feature coverage, exact trace/save/replay
continuity, and two distinct passed checks: headless determinism and
save/replay. It always records `native_execution: false`; it is not raylib
window, renderer, input, or native-platform evidence.

`worldforge verify-game-headless` publishes the result outside the repository
as an immutable `world-forge.headless_evidence_set` v1 exact tree. Publication
uses an exclusive native no-replace transition and identity-bound append-only
journal. Integral verification re-executes the retained script and
byte-compares the receipt, saves, replays, runtime evidence, and blocked support
report. A self-resealed receipt or an extra, linked, replaced, or otherwise
unclassified file is rejected. A ready journal can be recovered by exact
identity; ambiguous intent-stage state remains fail-closed.

The current descriptors declare Linux x86-64 and Windows x86-64 only. A local
Linux AArch64 process therefore receives `platform_unsupported` and cannot
produce positive evidence. Unit tests may substitute a code-derived x86-64
host projection to exercise the supported branch, but that is not hosted or
native platform evidence. The resulting support report remains
`partially_supported`, `release: blocked`, and `supported: false` until separate
adapter verification, packaging, and native raylib evidence are supplied.

The external multigenre release verifier follows the same authority lifecycle:
it re-derives and byte-compares the initial companion, attaches only the
integrally re-executed headless evidence-set handle, then attaches only the
verified package/extraction/standalone lineage. Its operational report records
the platform-independent initial authority ID and hash. Hosted native smoke is
reported separately and cannot be attached to runtime-support authority v1;
the authority itself therefore remains truthful while native aggregation is
designed as a later attested boundary.

The bounded 2D implementation consumes only an integrally verified
`world-forge.game_runtime_bundle` v1. Its retained loader rejects links,
hardlinks, special files, physical/inventory drift, hash/size mismatch, and
unsupported media before construction. PNG dimensions are checked from the
same sealed bytes loaded from memory; identical TTF bindings share one exact
memory font and unload once in reverse order. Narrative rendering requires the
sealed TTF and exact compiled English titles/options/endings; it never falls
back to a default font or synthesizes authored text.

Display timing is non-authoritative. A clamped accumulator advances the generic
kernel at exactly 60 Hz with bounded catch-up, and each logical step consumes at
most one queued semantic intent. Keyboard and pointer inputs map to the same
renderer-neutral actions. The puzzle draws the sealed board texture plus
focus/selection outlines, numeric labels, and textual feedback. The narrative
surface exposes structured state and keeps UI focus transient so saves and
replays contain only declared authoritative state.

`RecordingBackend` supplies deterministic local rendering evidence without a
window. `PyrayBackend` is the only module permitted to request the native
binding, and does so lazily at backend construction. The native smoke admits
only declared Linux/Windows x86-64 hosts and fails closed before native import
on this repository's Linux AArch64 development host. Therefore the checked-in
support reports remain blocked: fake-backend execution is neither hosted nor
native graphical evidence.

## Executable materialization identity

Three additive v1 contracts close the identity gap between a verified runtime
bundle and a standalone game without changing the published runtime
snapshot, registry, composition, or `world-forge.game_runtime_bundle` v1:

- `world-forge.runtime_implementation` binds one exact adapter version and hash
  to one exact runtime snapshot ID, content hash, and tree hash. Its package
  projections list every `gamepack_runtime` and `gamepack_raylib_2d` source
  byte, and its semantic entry points are selected from a code-owned closed
  policy rather than arbitrary import strings.
- `world-forge.runtime_platform_lock` binds one exact official
  `raylib==6.0.1.0` wheel artifact to CPython 3.11 or 3.12 and Linux x86-64 or
  Windows x86-64. The audited set contains exactly those four platform/ABI
  combinations, including filename, byte size, and SHA-256.
- `world-forge.game_materialization_bundle` is an exact-tree outer envelope. It
  nests the existing runtime bundle byte-for-byte, adds the matching runtime
  implementation, all four platform locks, the exact MIT license, and a
  code-owned launcher policy, then binds their complete lineage and physical
  closure.

The envelope has two closed states. A policy-only build reports
`state: contract_only`, `materialization_ready: false`, and the three required
launcher roles as missing. A complete build reports
`state: materialization_ready`, `materialization_ready: true`, and embeds the
exact game launcher, verifier, offline/native smoke launchers, package script,
independent game source, tests, requirements, notices, and project metadata.
Both states still report `release: blocked` and `supported: false`. Integral
verification rejects a bare runtime bundle, crossed adapter implementation,
platform/ABI/artifact tamper, nested runtime tamper, missing or extra launcher
bytes, links, hardlinks, extra files or directories, and self-resealed identity
drift.

Publication of either envelope is exclusive and no-replace. Only the ready
state may enter identity-bound standalone materialization and recovery. That
transition creates an external immutable game tree without installing a Forge
package or adding a `worldforge` dependency. It does not execute pyray or
produce hosted native evidence. Studio validates the additive documents in
source, built CJS, and retained ASAR; packaged Python remains authoritative for
integral semantic verification.

Additive `world-forge.game_save` v1 and `world-forge.game_replay` v1 provide
the persistence substrate for that bounded transition. Both bind the exact
gamepack, runtime composition,
runtime bundle, neutral runtime API, and execution-semantics hash. A save stores
only state entries declared `persistence: saved`; restore rebuilds transient
entries from immutable initial state and rejects changes to constants. A replay
stores accepted actions only and verifies ordered action indices, parameters,
events, pre/post-state hashes, trace hash, final state, and terminal
classification on playback. Recording rejects restore/quick-load so one replay
cannot splice two state histories.

Logical slots use the cross-platform append-only layout
`<saves|replays>/<game-id>/<bundle-id>/<slot>.slot/v1/generations/`.
Each `world-forge.persistence_generation` v1 file has an exact
`<20-digit-sequence>-<content-hash>.json` name, embeds the unchanged save or
replay v1 document, binds its payload hash, and references sorted parent
generation hashes. Generation zero has no parents. An ordinary write requires
one unique current tip and appends at `tip.sequence + 1`; concurrent
same-parent writes may publish sibling tips, after which reads and ordinary
writes fail closed until an explicit conflict resolution references every
current tip. Rollback also appends: it copies the payload of a previously
verified generation and never rewrites history.

Publication writes an exclusive random stage under
`<slot>.slot/v1/staging/`, fsyncs it, and moves it into `generations/` with a
native no-replace primitive. Failed or ambiguous stages remain outside the
authoritative inventory. Directory enumeration, file reads, publication, and
directory durability are retained-handle operations on Linux and Windows;
links, hardlinks, reparse points, unexpected entries, filename/hash mismatch,
missing parents, cycles, forks, and count/depth/byte-limit violations fail
closed. A byte-identical destination collision is idempotent; a different
payload under the same immutable name is rejected.

Legacy `<slot>.json` documents remain readable without mutation when no
generation directory exists. Appending requires an explicit migration whose
generation zero uses `operation: legacy_migration` and anchors the exact legacy
content hash. While both representations coexist, every scan revalidates that
anchor and rejects later legacy mutation. Studio performs structural
inspection of saves, replays, and generation envelopes in source, built CJS,
and retained ASAR; semantic verification remains delegated to isolated
packaged Python.

## Deterministic standalone game package

`world-forge.game_package` v1 transports one exact standalone game without
embedding authoring state. `package-game` verifies the retained standalone tree
and publishes a canonical ZIP_STORED archive with deterministic metadata and
no replacement. `verify-game-package` applies bounded archive and JSON limits,
rejects duplicate/non-portable members and links, and binds every payload byte
to the package manifest, standalone lock, and logic/runtime/asset lineage.

`extract-game-package` verifies the complete archive before its first write,
then copies exact bytes into a retained Linux/Windows stage. Its append-only
journal binds operation, parent, stage identity, archive hash, standalone hash,
payload lock, and inventory. `recover-game-package-extraction` publishes only
that proven identity; `rollback-game-package-extraction` quarantines and removes
only a proven unpublished stage. Conflicting destinations, replaced parents,
hardlinks, reparse points, partial journals, and ambiguous ownership fail
closed.

No privileged package or recovery transaction executes the game-local
`scripts/verify_game.py` pathname. The generated verifier is an explicit
post-extraction evidence step in a disposable standalone context with no Forge
mutation authority. A packaged or extracted game preserves its immutable logic
hash, but that proves transport integrity only: it continues to report
`release: blocked` until headless, replay, packaging, and all declared hosted
native evidence are independently verified.

This slice still does **not** supply hosted Linux/Windows native graphical
evidence for the bounded 2D adapters. Existing standalone/headless
materialization and save/replay verification do not turn fake-backend rendering
into native evidence. The checked-in puzzle and narrative reports therefore
intentionally remain blocked.

## Current evidence reconciliation

The fixture set deliberately separates executable proofs from authoring-only
proofs:

- `abstract-puzzle` and `branching-narrative` are the two executable end-to-end
  cases. Their deterministic compile, asset, headless, persistence,
  materialization, package, and extraction path is locally exercised; hosted
  native evidence for the exact reviewed revision remains pending.
- `action-framing`, `faction-strategy`, `modular-roguelite`, and
  `sports-career` are authoring-valid. Each contains an exact compiled authored
  gamepack and a 16-file real asset production/processing/QA lineage. Their
  mechanic ledgers remain `authoring_only` with `adapter_not_evaluated`; the
  adapter is absent, execution is untested, packaging is unverified, and
  release is blocked.
- Those six asset-bearing cases contain exactly 96 D2 asset-fixture files.
  `release_ready` on an asset manifest is a prerequisite for D3 sealing, not a
  sealed assetpack and not a released game.
- `systemic-simulation` proves the independent negative branch: assets are
  `not_applicable`, but runtime is requested. It therefore remains runtime-
  blocked instead of receiving placeholder assets or fabricated adapter
  evidence.

Studio protocol v5 is a closed 18-method transport. Creation job/worker v12
has closed operation-discriminated jobs through headless evidence publication,
while creation output grants advance through persisted v6 and previews remain
published v1 plus pre-release QA candidate v2. Legacy v4 grant listing projects
only v1-v5 grants; v5 listing exposes persisted v6. The locally implemented
renderer exposes fixed controls for compile, asset process/seal, bounded PNG/WAV
preview, runtime compose/bundle, materialization bundle/standalone, package,
extraction, and headless evidence publication. Electron main owns output path
selection and the renderer remains pathless. Local tests prove those controls
and their authority projections; they do not prove a hosted build, packaged
shell, native execution, or release readiness, and raw authoring validity never
implies executable/native support.

The canonical development interpreter setting is
`WORLD_FORGE_STUDIO_DEV_PYTHON`. `RWF_STUDIO_DEV_PYTHON` is a deprecated reader
alias only. Equal dual values are accepted; unequal dual values fail closed.

## Determinism and extension policy

Every document has a canonical SHA-256 over strict canonical JSON with its own
`content_hash` omitted. The integral loader:

1. rejects invalid UTF-8, duplicate object keys, non-finite constants, every
   decimal or exponent number lexeme, unsafe integers, lone surrogates,
   excessive JSON depth, and non-object roots;
2. rejects booleans where integers are required;
3. validates exact fields, closed discriminators, portable IDs and relative
   POSIX paths;
4. rejects NFC/casefold identity and path collisions;
5. verifies every reference against the exact loaded document;
6. enforces `world:none` and `narrative:none` across profile, activities, and
   modules;
7. requires explicit narrative entries, local reachability, globally unique
   narrative-unit IDs, terminal endings, exact choice targets, and resolved
   transitions;
8. constrains every integer to JavaScript's exact safe range and requires
   set-like arrays to use canonical UTF-8 order;
9. incrementally accounts for logic-module JSON keys, values, escaping,
   separators, containers, depth, nodes, and collection sizes, rejecting an
   oversized direct object before canonical serialization or hashing without
   constructing another attacker-sized payload;
10. pins the project-root ancestry for integral and standalone reads and reads each
   standalone file through retained directory/file handles, rejecting symbolic
   links, reparse points, hard links, identity changes, excessive path depth,
   JSON depth, per-file bytes, aggregate bytes, and file counts; standalone
   reads also require the same bytes in a final verification read before return.

The schemas encode portable shape, closed discriminators, integer maxima, and a
conservative character-length path bound. Some lexical and cross-document
invariants are not exactly expressible in JSON Schema: NFC normalization,
UTF-8 byte length, duplicate keys, the distinction between integer `1` and
decimal `1.0`, casefold collisions, canonical array order, top-level
content-hash equality, reference hashes, retained-handle identity, and graph
closure. The Python integral loader is authoritative for those invariants.
Studio generation uses a bounded strict JavaScript decoder rather than
`JSON.parse`, validates the same lexical boundary, and independently recomputes
every committed fixture hash in Node, so Python and JavaScript canonicalization
cannot silently drift.

Extension identifiers must be qualified, such as
`studio.example-mechanic`. Unknown optional extensions remain identifiable and
ignorable. Unknown required extensions fail closed unless the caller provides
an exact registered validator. Extensions cannot introduce executable content
through these base schemas.

The neutral fixtures under `examples/multigenre-contracts/` prove an abstract
puzzle with no world or narrative, a branching text narrative, a standalone
typed canon module, deterministic gamepack compilation, and authoring-only
mechanic ledgers. Generic asset planning, D2a production lineage, D2b
deterministic processing, retained-byte QA, and release-readiness manifests are
implemented. D3 deterministic generic assetpack sealing and Studio/package
verification are also implemented. Additive generic runtime descriptors,
snapshot/registry binding, exact D1/D3 composition, capability resolution,
evidence contracts, support reporting, CLI inspection, and Studio contract
validation are implemented. Exact generic runtime bundling plus deterministic
save/restore/replay contracts and Studio structural inspection are implemented.
The additive runtime-implementation, exact platform-lock, and dual-state
materialization-envelope identities are implemented without changing closed v1
runtime bytes. Ready envelopes can create and independently verify standalone
repositories; deterministic `world-forge.game_package` transport, extraction,
recovery, and rollback are also implemented. Studio authoring editing and fixed
creation controls are implemented and tested locally; hosted, packaged-shell,
native, and release evidence remain later slices. Compilation, asset
readiness, sealing, persistence, adapter declaration, materialization, and
verified packaging do not by themselves claim runtime executability.
