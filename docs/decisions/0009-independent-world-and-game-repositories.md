# ADR-0009: Independent forge, world, bundle, and game repositories

- Status: accepted
- Date: 2026-07-19

## Context

ADR-0003 established a world-agnostic core and used “game repository” broadly
for the independent home of generated content. M4 needs a stricter production
boundary. Authoring a world and implementing a game are different lifecycles:
the first owns canon and production evidence; the second owns executable code,
game-specific UX, releases, and platform support.

Keeping those concerns in one repository would make it easy for a game to read
authoring sources, prompts, candidates, or provider metadata. It would also
couple canon history to engine changes and make reuse of one validated world or
one game runtime needlessly difficult.

The repository currently contains `src/isoworld` as a tested reference runtime
snapshot. That is useful while the public contracts are still evolving, but it
does not authorize a generated game to import the offline `worldforge` package
or to run directly from a mutable world-authoring checkout.

## Decision

M4 establishes this one-way release chain:

```text
rpg-world-forge repository
        |
        | scaffolds and validates
        v
independent world repository
        |
        | compiles, approves, and releases
        v
immutable runtime bundle
        |
        | copied/imported and hash-locked
        v
independent game repository
```

### Forge repository

`rpg-world-forge` owns generic schemas, validators, compilers, analysis,
workflow protocols, reusable agent skills, neutral fixtures, game scaffolding
templates, and the reference `isoworld` runtime snapshot. It owns no generated
world's canon, roster, production candidates, or final game assets.

### World repository

Each world is scaffolded into its own repository. It owns:

- canon, chronology, actors, factions, maps, dialogue, quests, and rules data;
- `.worldforge/` phase state, decisions, reports, and agent coordination;
- authoring references and optional AI-assisted production evidence;
- asset specifications, candidates, provenance, licenses, and approvals; and
- build outputs that remain mutable until an explicit release succeeds.

A world repository may use external GPT/Codex/OpenAI authoring or local models
through Modly, subject to the existing authoring and review gates. Those tools
never become runtime dependencies.

### Immutable runtime bundle

A successful world release creates a content-addressed bundle containing only
runtime-safe artifacts:

```text
bundle.manifest.json
worldpack.json
renderpack.json
assets/**
licenses/**
```

The bundle manifest records the world ID, release ID, source worldpack hash,
renderpack hash, bundle hash, schema versions, runtime capability requirements,
file hashes, and license inventory. Paths are relative and contained. Bundle
contents are immutable: changing one byte creates a different bundle release.

The bundle excludes source canon files, prompts, references, candidates,
provider or model identifiers, workflows, weights, credentials, phase state,
and production evidence. A correction is made in the world repository and
exported as a new bundle; it is never patched in place inside a game.

### Game repository

Each game is scaffolded into a separate repository. It owns:

- its application entry point, game-specific composition, adapters, and UX
  under `src/game/`;
- an immutable, hash-locked `src/isoworld/` runtime snapshot;
- a separate `platform.lock.json` for the Python, binding, backend, and native
  raylib baseline;
- shared presentation resources under a canonical path/hash/media lock that
  also binds the game notices file, plus optional game-level binding overrides;
- a catalog of imported, immutable world bundles and their lock data;
- saves/settings migration policy, packaging, platform CI, and releases; and
- only the runtime licenses and notices required for distribution.

A generated game receives materialized application/runtime template files and
only game-local development, test, capture, benchmark, packaging, and release
entry points. It receives no `AGENTS.md`, `AGENTS.override.md`, `.agents/`,
`.worldforge/`, reusable skills, authoring prompts, phase reports, editable
canon, production manifest, or model/provider workflow. Reusable agent methods
remain versioned in the Forge and operate on the game repository externally.

Forge-side commands own game scaffolding, immutable bundle import, runtime
snapshot updates, and compatibility auditing. Game-local commands may verify
already imported runtime data, but they do not call `worldforge`, read a world
checkout, or mutate an imported bundle. This keeps the game executable and
maintainable as a normal pyray/raylib project without awareness of the
authoring control plane.

The game imports a bundle by copying verified files into its controlled data
root and recording the exact bundle hash. It does not use a mutable path,
symlink, Git submodule, or live world-repository checkout at runtime. Multiple
bundles may coexist, namespaced by world and release ID, but cross-world
references are invalid unless a future explicit contract defines them.

The game must never import `worldforge`. It loads only compiled worldpacks,
renderpacks, processed assets, and the bundle manifest through its runtime
content layer. It also contains no AI/model inference, provider SDK, prompt
execution, or network requirement.

Game-specific work never edits the vendored `src/isoworld/` tree. Reusable
runtime changes are implemented and tested in the Forge, then replace the
complete snapshot and `runtime.lock.json` atomically with an expected current
hash check. Game-owned extensions remain in `src/game/`, so a snapshot update
cannot silently overwrite application work.

### Runtime distribution

For M4, the runtime in this repository is the authoritative tested snapshot.
The game scaffold receives or vendors a versioned snapshot of the runtime
contracts and implementation that it then owns. The imported snapshot records
the Forge version/commit and retains its license; updates are deliberate and
must pass the game suite instead of arriving implicitly.

Runtime migration and platform migration are distinct operations. A runtime
migration replaces all of `src/isoworld/` plus `runtime.lock.json`; it does not
change the Python/raylib platform. A platform migration updates
`platform.lock.json`, the CI-consumed `requirements.lock`, the closed
direct/build dependency declarations, CI matrix, notices, and native evidence
together; it does not patch the runtime snapshot. The M4
desktop baseline is CPython 3.11/3.12, the standard
`raylib==6.0.1.0` distribution imported as `pyray`, and native raylib 6.0.
Both migrations invalidate their documented downstream evidence and require a
fresh boundary and compatibility audit.

Source-archive packaging never mixes pre-verification identity with later file
reads. It copies the explicit allowlist into a private staging root, runs the
game verifier against that root in an isolated Python process, derives package
identity from the verified copy, and verifies the completed archive byte by
byte before publishing it to a fresh external path.

Once the runtime API and compatibility policy are sufficiently stable, it may
move to a separately versioned `isoworld-runtime` distribution. That change
requires a new ADR covering publication, dependency resolution, compatibility,
security updates, and migration from snapshots. M4 does not publish or depend
on such a distribution prematurely.

### Milestone boundary

M4 owns multiple-world isolation, catalogs, reproducible compilation, immutable
bundle export/import, game-repository contracts, scaffolding, scripts, and
neutral/fallback verification data. M5 owns assisted generation and production
of final sprites, tiles, portraits, UI, VFX, music, and SFX. M4 may prove asset
slots and fallback rendering, but it does not move asset generation into the
runtime or declare placeholder media to be production output.

## Consequences

- Forge releases, world canon, bundle releases, and game releases have separate
  histories and responsibilities.
- A game can consume a validated world without access to authoring-only data.
- A world can be rebuilt or targeted by another compatible game without
  copying its mutable authoring workspace.
- Bundle hashes and capability declarations make upgrades explicit and
  reproducible.
- A compromised prompt, model workflow, or candidate file cannot become a
  runtime input merely by living beside approved assets.
- World IDs and release IDs provide isolation when one game ships multiple
  worlds.
- Game repositories can replace presentation and UX without forking world
  canon or changing deterministic simulation data.
- Runtime snapshot duplication is temporarily accepted. Runtime fixes must be
  deliberately propagated and verified in each game until a separate runtime
  distribution is approved.
- Packaging needs an explicit import/sync step, license inventory, and data
  manifest; direct execution from an authoring checkout is no longer valid.
- Reusable agent skills and game-construction guidance have one maintenance
  home in the Forge. World repositories retain tailored authoring guidance;
  game repositories contain no agent-control files or copied skill trees.
- The architecture refines ADR-0003: an independent world repository and an
  independent game repository are now distinct concepts connected only by an
  immutable bundle.

## Rejected alternatives

### Store every world and game in the Forge repository

Rejected because canon would contaminate neutral fixtures, permissions would
be overly broad, release histories would be coupled, and authoring evidence
could leak into distributable builds.

### Let a game import `worldforge`

Rejected because validators, prompts, workflow state, model-route policy, and
authoring dependencies do not belong in a player runtime. The game needs the
compiled contracts, not the compiler.

### Put `AGENTS.md`, skills, or world workflow state in a game repository

Rejected because these files make authoring orchestration part of the game
project's responsibility and let production-only context drift into runtime
development. Agents that construct the game use Forge-owned guidance and
external paths; the materialized game remains an ordinary, self-contained
pyray/raylib codebase.

### Let a game load world source or an asset-production manifest

Rejected because mutable source and production evidence are not finite,
provider-neutral runtime inputs. Only the validated worldpack, renderpack, and
approved processed files cross the boundary.

### Mount the world repository directly or use it as a Git submodule

Rejected because branch movement and working-tree edits would bypass immutable
release hashes. Symlinks also produce inconsistent packaging behavior across
desktop platforms.

### Keep world authoring and game implementation in one generated repository

Rejected because it recreates the same trust and lifecycle coupling outside
the Forge. A bundle is the deliberate seam between content production and game
production.

### Publish the reference runtime as a package during M4

Rejected for now because its compatibility and migration policy are still
changing with worldpack milestones. A recorded source snapshot is less elegant
but more honest and reproducible until the public runtime boundary stabilizes.

### Generate final assets as part of M4 scaffolding

Rejected because scaffolding and runtime integration must be independently
testable with neutral fallbacks. Asset generation remains the offline,
reviewed, provenance-tracked responsibility of M5.

### Add live AI to bridge missing world or asset data

Rejected by ADR-0001. Missing or incompatible release data fails validation or
uses a declared presentation fallback; it never triggers inference during
play.
