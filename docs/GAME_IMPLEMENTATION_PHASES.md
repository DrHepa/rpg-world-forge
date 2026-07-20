# Standalone Game Implementation Phases

This document is the Forge-side execution map for building a standalone
2D/2.5D isometric game with pyray/raylib. It is not copied into generated game
repositories.

Each phase has one primary skill, a bounded responsibility, and an observable
exit gate. An agent must invoke only the skill for the active phase. Completing
one phase does not authorize work in later phases, and there is intentionally
no all-in-one game-building skill.

There are exactly twenty game-phase skills: G00-G04, G10-G15, G20-G25, and
G30-G32. The four W-prefixed skills are separate world/release operations, not
game implementation. Every game `SKILL.md` uses the same compact contract:
`Scope`, `Inputs`, `Outputs`, `Invariants`, `Workflow`, and `Completion`. A phase
must name exact repository/lock/catalog identities, its writable surface, prior
gate, external evidence output, and objective exit test. It never invokes the
next skill automatically.

## Repository boundary

The Forge owns the skills, templates, bundle operations, runtime snapshot
operations, and external audits. The standalone game owns application code,
game-owned tests and UX, immutable imported bundles, saves, packaging, and
releases. The game never receives `AGENTS.md`, `.agents`, `.worldforge`, editable
canon, prompts, production evidence, or a Forge/AI dependency.

## External world prerequisites

World operations are not game-implementation phases and do not use a game path
or game audit. They have their own atomic skills:

| Operation | Primary skill | Bounded output | Exit gate |
| --- | --- | --- | --- |
| W00 | `$create-world-project` | New independent authoring project | Valid v2 world status and identity |
| W01 | `$clone-world-project` | New authoring lineage from canonical inputs | New identity, lineage, and reset workflow verify |
| W02 | `$version-world-project` | One optimistic-lock SemVer transition | Version log and invalidated release metadata verify |
| W10 | `$forge-world-release` | Verified immutable runtime bundle | Bundle hash, licenses, and compatibility metadata verify |

## Game phase map

| Phase | Primary skill | Bounded output | Exit gate |
| --- | --- | --- | --- |
| G00 | `$scaffold-pyray-game` | Empty clean standalone game project | Both locks, empty catalog, bundle-free headless shell, and boundary audit pass |
| G01 | `$update-game-runtime` | Whole vendored runtime upgrade | Compatibility and migration suite pass; otherwise rollback |
| G02 | `$update-pyray-platform` | One locked binding/backend/Python baseline | Full declared native platform profile passes |
| G03 | `$import-world-bundle` | One locked catalog release | Copied hashes and explicit headless world selection pass |
| G04 | `$establish-gameplay-test-harness` | Action/replay/digest test harness | Identical fixtures and actions reproduce state hashes |
| G10 | `$implement-gameplay-navigation` | Collision, movement, pathfinding, reservations | Deterministic route and replay tests pass |
| G11 | `$implement-gameplay-time-schedules` | Clock, calendar, schedules, fallback routes | Boundary and missed-window tests pass |
| G12 | `$implement-gameplay-interactions` | Context actions, abilities, costs, cooldowns | Availability/reduction and replay tests pass |
| G13 | `$implement-gameplay-narrative` | Knowledge, dialogue, quests, scenes, campaigns | Reachability, knowledge, softlock, and replay tests pass |
| G14 | `$implement-gameplay-living-world` | One economy/needs/goals/construction/consequence slice | Causal-chain and conservation tests pass |
| G15 | `$implement-gameplay-persistence` | Versioned save/restore and migrations | Round-trip, corruption, wrong-bundle, and replay-equivalence tests pass |
| G20 | `$implement-pyray-app-loop` | Window ownership and fixed-step port orchestration | Headless parity and window teardown tests pass |
| G21 | `$implement-pyray-isometric-rendering` | Projection/inverse, culling, depth plan, render passes | Projection/depth/culling scene tests pass |
| G22 | `$implement-pyray-input-camera` | Action map, virtual viewport, Camera2D, picking | Device-edge and coordinate tests pass |
| G23 | `$implement-pyray-resources` | Validated native graphics resource registry | Failure cleanup and hidden-window smoke pass |
| G24 | `$implement-pyray-ui` | One accessible, localized UI flow | Focus, overflow, resize, and action tests pass |
| G25 | `$implement-pyray-audio` | Event SFX, streams, buses, silent fallback | Fake-adapter and native lifecycle tests pass |
| G30 | `$verify-pyray-game` | Reproducible static, correctness, and native evidence | Locked lint/format and all declared target gates recorded as pass |
| G31 | `$optimize-pyray-game` | One measured performance improvement | Before/after evidence plus correctness regression pass |
| G32 | `$release-pyray-game` | Audited unpack-and-run source ZIP | Offline headless startup, allowlist, hashes, and notices pass |

G01 and G02 are explicit migration operations rather than routine steps.
G10-G15 may already be satisfied by the vendored reference runtime; in that
case the phase records verification evidence instead of rewriting working
systems. G14 is repeated as `G14:<system-id>` for every system in the declared
living-world inventory. G24 is repeated as `G24:<flow-id>` for every declared
UI flow. G31 is entered only for a measured budget miss or a documented
optimization goal.

G04 owns generic action/replay/digest infrastructure; G15 only serializes,
restores, and migrates saves against that harness. G13 owns authored narrative
execution; G14 communicates systemic consequences through typed events without
rewriting narrative graphs. G20 owns the window and invokes ports; G21 owns
render planning, G22 input/camera translation, G23 graphics handles, G24 one UI
flow, and G25 audio-device/handle ownership.

G31 runs only focused/shared regressions and produces a handoff for a separate
G30 invocation. It never invokes G30 or writes G30 evidence itself. G32 remains
blocked until that new G30 record matches the optimized tree exactly.

## Dual-root and immutable-input contract

Every game phase operates from the Forge with two explicit absolute paths:

- `FORGE_ROOT` supplies skills, guides, commands, templates, and external
  evidence storage;
- `GAME_ROOT` is the separate standalone game project being changed.

Agents read guidance from `FORGE_ROOT` and edit only the declared game-owned
surface under `GAME_ROOT`. They do not copy the guidance into the game.
Every skill explicitly forbids copying `AGENTS.md`, `.agents`, Forge skills, or
phase evidence into `GAME_ROOT`.

The following `GAME_ROOT` inputs are immutable:

- `src/isoworld/**` and `runtime.lock.json` are one locked runtime snapshot;
- `game_data/worlds/<world>/<release>/**` and its catalog entry are imported
  immutable bundles; and
- `game_data/shared.lock.json` is the authoritative inventory for game-owned
  shared runtime assets; and
- bundle/world IDs and hashes embedded in saves are authoritative.

G10-G25 implement game-specific composition, adapters, and extensions under
`GAME_ROOT/src/game/**` plus focused tests. They never patch `src/isoworld`.
When a reusable capability is missing, change and verify the reference runtime
in the Forge as a separate task, then enter G01 to replace the whole snapshot.
When released content is wrong, produce a new W10 release and enter G03; never
patch imported bytes.

Before G03, verify the candidate bundle without importing it. If its runtime
contract is unsupported, enter G01 and verify all existing bundles/saves before
retrying G03. A G01 run after G04 invalidates G04-G32 evidence. A G02 run
invalidates G20-G32 evidence. Any new G03 import changes the catalog/source
hashes and invalidates G30-G32, including all prior G31 optimization evidence;
it also reopens any declared world-specific test/UI gaps.

| Phase family | Writable game surface |
| --- | --- |
| G00 | New target only, as materialized by the template |
| G01 | Whole runtime snapshot and lock, atomically |
| G02 | Platform lock, matching exact dependency/lock config, CI, notices, and platform tests |
| G03 | Catalog plus one new derived bundle release path, atomically |
| G04 | `tests/**` and game-owned test helpers under `src/game/**` |
| G10-G15 | Game-owned domain composition/extensions under `src/game/**` and focused tests |
| G20-G22 | Game-owned adapters/presentation under `src/game/**` and focused tests |
| G23 | Graphics adapters/tests plus game-owned textures, fonts, shaders, or UI-neutral graphics beneath `game_data/shared/**`; update notices and regenerate `shared.lock.json` with its dedicated script |
| G24 | One game-owned UI flow under `src/game/**` and focused tests; consume locked resources, and return new native-resource needs to G23 |
| G25 | Audio adapters/tests plus game-owned UI/common audio beneath `game_data/shared/audio/**`; update notices and regenerate `shared.lock.json` with its dedicated script |
| G30 | Read-only; findings return to the owning phase |
| G31 | Only the profiled game-owned surface and its tests; contract changes return to its owning phase |
| G32 | Read-only `GAME_ROOT`; already-verified configuration and external build output only |

## Game phase protocol

Before G01-G32:

1. Name the phase ID, `FORGE_ROOT`, `GAME_ROOT`, selected world/release when the
   phase consumes content, and current commit or source-tree hash.
2. Read the named skill from `FORGE_ROOT` and only the relevant sections of
   `docs/PYRAY_RUNTIME_GUIDE.md`.
3. Record the previous phase gate and the `GAME_ROOT` surface the phase may change.
4. Stop if the game boundary audit or imported bundle verification already
   fails; do not build on untrusted state.

G00 is the exception: its target does not yet exist. It records the explicit
new target and source template/runtime revision, then establishes the first
source-tree hash. Version-control initialization and the first commit are an
explicit owner action; subsequent records use a commit when one exists and a
source-tree hash otherwise. G32 requires either a clean version-controlled
checkout or a separately verified clean source staging tree.

During a phase:

- keep simulation deterministic and independent from pyray;
- keep presentation dependent only on immutable render/view state;
- use semantic actions and asset bindings at every boundary;
- change shared asset bytes/notices only in G23 or G25 and regenerate their
  canonical lock with `python scripts/lock_shared_assets.py` before verification;
- add failure-path tests with the implementation, not after it;
- preserve explicit package-relative and user-data roots; and
- do not silently broaden the phase to repair unrelated systems.

After a phase:

1. Run the phase's focused tests and the shared headless regression suite.
2. Run locked Ruff lint/format plus `worldforge audit-game` externally from the Forge.
3. Report changed files, contract/version changes, bundle locks, and evidence.
4. Mark skipped native or platform checks as missing evidence, never as passes.
5. Write the phase record only to Forge-owned evidence storage outside
   `GAME_ROOT`; phase reports are forbidden in the game repository.
6. Hand the exact exit state to the next phase; do not invoke it automatically.

W00-W10 instead record explicit world/source/bundle paths, world version and
lineage, relevant validation, and immutable output hashes. They do not require
a game path, game commit, world selection in a catalog, or game boundary audit.

## Layer ownership

The phase sequence preserves this one-way dependency:

```text
verified runtime bundle
        |
        v
deterministic domain/runtime (G10-G15)
        |
        v
immutable RenderState and UI view models
        |
        v
pyray application/presentation adapters (G20-G25)
```

Domain modules cannot import `pyray`. Presentation cannot mutate `WorldState`.
Catalog and bundle loaders cannot import authoring/Forge modules. Packaging
cannot rediscover mutable world repositories.

## Native evidence policy

The default test suite is headless. Native evidence is separate and explicit:

- G20 proves partial and complete window/application teardown without taking
  ownership of graphics resources or audio.
- G22 proves representative platform input queries and viewport conversion.
- G23 proves real resource creation, one frame, and reverse-order unload.
- G25 proves device/stream lifecycle or the documented optional silent path.
- G30 first records the declared target matrix (OS, architecture, Python,
  binding/backend, resolutions/DPI, input classes, and audio requirement). It
  runs headless/replay tests for every OS/architecture/Python row; window and
  representative scene profiles for every backend/resolution/DPI row; semantic
  plus native availability probes for every input class; and required/optional
  device lifecycle profiles for every audio row.

The first desktop baseline is CPython 3.11/3.12 with
`raylib==6.0.1.0`, imported as `pyray`. Any binding/backend/Python baseline
change is a G02 platform decision; any vendored runtime API change is G01.
Both require downstream invalidation and final G30 verification.

## Completion record

Every game phase handoff records, outside `GAME_ROOT`:

- phase ID and skill name;
- game commit or source-tree hash, plus runtime snapshot hash;
- selected world/release and bundle hash, when applicable;
- tests and platform/backend used;
- `system_id` for G14 or `flow_id` for G24, plus the declared aggregate inventory;
- changed contracts or migrations;
- unresolved evidence and explicit follow-up phase; and
- clean-game audit result.

G30 evidence is keyed to the exact game commit/source-tree hash, runtime lock,
platform lock, shared-asset lock, and complete catalog hash. It also records a representative
benchmark baseline. If a declared budget fails, enter G31 and attach
before/after evidence; if all declared budgets pass, G31 remains optional.
Every G31 change requires a complete G30 rerun on the optimized tree. G32
accepts only G30 (and, when used, G31) evidence whose hashes match the exact
release inputs. G32 may write only to an external output/staging root. Any
packaging configuration, source, lock, catalog, bundle, notice, or test change
returns to its owning phase and requires a new complete G30 before release
packaging resumes. M4 G32 produces one deterministic portable source ZIP, not a
wheel, installer, or native executable; native wrappers are a later, separately
scoped release skill and milestone.

All records live in Forge-owned external task/release evidence. They are never
written anywhere in the game repository, even during development.
