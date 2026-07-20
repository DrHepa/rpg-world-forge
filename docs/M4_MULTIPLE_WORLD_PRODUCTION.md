# M4: Multiple World Production

M4 turns the Forge from a single technical-slice compiler into a production
toolchain for independent worlds, immutable releases, and clean standalone
pyray/raylib games. The Forge remains world-agnostic; Modly World and every
later game are external products of this toolchain.

## Outcomes

M4 provides:

- atomic v2 world-project creation, inspection, cloning, legacy upgrade, and
  optimistic-lock SemVer versioning;
- deterministic worldpack v5 with localization, personal campaigns, and an
  explicit runtime compatibility contract;
- runtime-only bundle export, strict verification, atomic game import, and a
  multi-world/multi-release catalog;
- a standalone game scaffold with a vendored immutable `isoworld` snapshot,
  locked pyray platform baseline, independent verification, native smoke,
  benchmark, package, and CI entry points;
- Forge-only game-construction skills, each limited to one implementation
  phase; and
- enforced separation between Forge, world, bundle, and game responsibilities.

M4 does not generate final visual/audio assets. M5 consumes M4's semantic asset
requirements and produces reviewed, licensed assets through OpenAI or optional
local models exclusively through Modly extensions.

## Release chain

```text
Forge tools/skills
      |
      | explicit external path
      v
world-authoring project v2
      |
      | validate + compile + approve
      v
worldpack v5 + renderpack v1
      |
      | export + verify
      v
runtime bundle v1 (immutable)
      |
      | atomic verified copy
      v
standalone game catalog + locked release
```

No game command reads a world checkout. No runtime module imports `worldforge`,
an AI provider, or Modly.

## World-project lifecycle

Every new world project uses:

```json
{
  "format": "rpg-world-forge.project",
  "format_version": 2,
  "project_kind": "world",
  "world_id": "my_world",
  "world_version": "0.1.0"
}
```

The complete contract is `schemas/world-project.schema.json`. World versions
are stable `MAJOR.MINOR.PATCH` values only: no aliases, prereleases, build
metadata, or leading zeroes.

Create and inspect:

```bash
worldforge new-world ../my-world \
  --id my_world --title "My World" --language en --version 0.1.0
worldforge world-status ../my-world
```

Clone canonical inputs into a new identity:

```bash
worldforge clone-world ../my-world ../derived-world \
  --id derived_world --title "Derived World" --version 0.1.0
```

Clone copies all canonical `source/**` material and allowlisted authoring asset
inputs (`specs`, `references`, `recipes`, `qa`, and licenses). It excludes Git
state, credentials, claims, phase reports, generated candidates/builds, and
manifests bound to the old world hash. It records lineage and resets workflow.

Apply one reviewed version transition:

```bash
worldforge bump-world-version ../my-world \
  --expected-version 0.1.0 --part minor \
  --reason "Add the northern campaign" --approved-by lead-agent
```

The expected version is an optimistic lock. A bump updates project/source
identity, appends the version log, unlocks canon, and invalidates release-bound
metadata. Legacy v1 projects require the explicit `upgrade-world` command; an
inspection or clone never upgrades implicitly and never accepts a game project.

World lifecycle operations reject symlinks, credential-like inputs, unsafe
targets, and partial writes. Creation and cloning stage beside the destination
and install atomically. Inspection, version changes, phase completion, phase
reopening, upgrade, and clone snapshots share one identity-safe lifecycle lock;
multi-file control updates roll back together on an observed publication error.

## Worldpack v5

Worldpack v5 preserves v1-v4 loading compatibility and adds three contracts.

### Runtime requirements

```json
{
  "runtime_requirements": {
    "runtime_api": {
      "minimum": "0.5.0",
      "maximum_exclusive": "0.6.0"
    },
    "required_features": ["grid_movement", "path_navigation"],
    "optional_features": []
  }
}
```

Compatibility is a pure comparison against caller-owned runtime version and
features. Missing required features or an API outside the half-open interval is
incompatible. Missing optional features is reported but non-fatal. Worlds do
not select, download, or initialize runtime implementations.

Check the reference profile or an explicit feature set:

```bash
worldforge check-compatibility build/my_world.worldpack.json
worldforge check-compatibility build/my_world.worldpack.json \
  --runtime-version 0.5.0 --feature grid_movement --feature path_navigation
```

### Localization

The world declares `default_locale` and sorted `supported_locales`. Optional
typed `locales` entries use unique BCP47 language tags and complete string maps.
The default locale must agree with the legacy `world.ui` map so old presentation
paths cannot show different text. Localization changes compiled data, never
game code or runtime inference.

### Personal campaigns

The existing `personal_arcs` collection is the canonical campaign collection;
M4 does not introduce a duplicate name. Each campaign owns one playable actor,
a start act, typed acts, quest/scene references, and explicit next-act edges.
Actor-to-campaign references are bidirectional and unique. Validation rejects
non-playable owners, broken graphs, unknown narrative IDs, or mismatched links.

## Immutable runtime bundle

An exported bundle contains exactly:

```text
bundle.manifest.json
worldpack.json
renderpack.json
assets/<asset-id>/<ordered-runtime-file>
licenses/**
```

Export and verify:

```bash
worldforge export-bundle \
  ../my-world/build/my_world.worldpack.json \
  ../my-world/build/runtime/renderpack.json \
  ../releases/my_world-1.0.0 \
  --release-id 1.0.0 \
  --licenses ../my-world/build/runtime/licenses

worldforge verify-bundle ../releases/my_world-1.0.0 \
  --expected-hash <sha256>
```

The bundle destination and any later import source must be an external,
standalone root: never below the Forge, a world, another bundle, or a game.

`bundle.manifest.json` uses `isoworld.runtime_bundle` format 1. It records world
and stable release IDs, source and bundled content hashes, format versions,
runtime requirements, every payload path/hash/size/media type, the exact license
subset, and a canonical bundle hash. Export rewrites mutable renderpack paths
into deterministic `assets/**` paths and recalculates the bundled renderpack.

Verification rejects:

- missing, extra, empty-extra-directory, symlink, hardlink, or special entries;
- traversal, non-NFC, case-colliding, Windows-reserved, or overlong paths;
- size/hash/tree/license mismatches and noncanonical manifests;
- file bytes whose PNG/JPEG/WebP/WAV/OGG/MP3/font/JSON/GLSL signature disagrees
  with the declared runtime media type;
- provider/model/workflow metadata or authoring-only JSON formats at any depth;
- worldpack/renderpack/manifest ID, hash, feature, or asset-inventory mismatch;
  and
- JSON duplicate keys, unexpected envelopes, or unrecognized runtime JSON.

Changing any payload byte requires a new SemVer release. Imported releases are
never modified in place.

## Standalone game project

Create a game outside the Forge and every world project:

```bash
worldforge new-game ../my-game \
  --id my_game --title "My Game" --source-revision <forge-commit>
worldforge audit-game ../my-game

cd ../my-game
python scripts/verify_game.py
python -m game --list-worlds
python -m game --headless-ticks 0
```

The last command is the G00 bundle-free shell gate: an empty catalog is valid
and must report a ready headless shell without selecting or inventing a world.
It does not prove G03 bundle import or world execution.

The materialized project contains normal game files only:

```text
src/game/                         game-owned application/extensions
src/isoworld/                     locked vendored runtime snapshot
runtime.lock.json                 snapshot file hashes and provenance
platform.lock.json                Python/binding/backend/native API baseline
requirements.lock                 exact dependency set consumed by CI/install
game_data/shared.lock.json        shared-asset inventory plus notices hash
game_data/shared/**               hash-locked game-owned presentation resources
game_data/worlds.lock.json        runtime-neutral release catalog
game_data/worlds/**               immutable imported bundles
run_game.py                       unpack-and-run source-archive launcher
scripts/verify_game.py            independent boundary/data verification
scripts/native_smoke.py           parameterized native platform probe
scripts/benchmark_scene.py        deterministic headless benchmark
scripts/package_game.py           deterministic allowlisted package
tests/                             game-owned tests
```

It contains no `AGENTS.md`, `.agents`, `.worldforge`, phase reports, editable
canon, prompts, production evidence, `worldforge` import/dependency, provider
SDK, or model. The generated verifier and application work from their resolved
project path rather than the process working directory.

World-specific assets remain inside immutable bundles. Game-owned common
textures, fonts, shaders, UI-neutral graphics, or UI/common audio may live only
below `game_data/shared/**`. `python scripts/lock_shared_assets.py` rebuilds the
canonical `game_data/shared.lock.json`; the verifier checks its complete
path/hash/size/media inventory, extension-to-media agreement, real directory
tree, content signatures, authoring/provider boundary, and the hash of
`THIRD_PARTY_NOTICES.md`. Every shared asset must have an origin and license
entry there (including an explicit game-owned entry for original work). G23
owns shared graphics, G25 owns shared audio, and all other phases treat both
the bytes and lock as immutable. M4 establishes this ingestion contract; M5
produces and reviews final assets. The public lock shape is
`schemas/shared-assets.schema.json`; executable verification additionally
enforces canonical ordering, byte signatures, and cross-file hashes.

`src/isoworld/**` is immutable inside a game. Game-specific systems,
composition, adapters, and presentation live under `src/game/**`. A reusable
runtime correction is implemented/tested in the Forge and replaces the whole
snapshot through `update-game-runtime` with an expected current hash. It is
never hand-copied module by module.

The pyray platform baseline is a separate lock and phase. The initial desktop
profile is CPython 3.11/3.12, standard `raylib==6.0.1.0`, native raylib 6.0,
imported as `pyray`. Its exact `raylib`, `cffi`, `pycparser`, `ruff`,
`setuptools`, and `wheel` versions are installed from `requirements.lock`. A
binding/backend/Python migration updates the platform lock, dependency lock,
build configuration, CI, notices, and native evidence together; it is not a
runtime-snapshot edit.

Import one verified release:

```bash
worldforge import-bundle ../releases/my_world-1.0.0 ../my-game \
  --expected-hash <sha256>
worldforge audit-game ../my-game

cd ../my-game
python scripts/verify_game.py
python -m game --world my_world --release 1.0.0 --headless-ticks 20
```

Import takes an exclusive lock, verifies the existing catalog and every
installed release before mutation, copies through staging, atomically updates
`game_data/worlds.lock.json`, and rolls back on failure. Catalog paths are
derived as `game_data/worlds/<world-id>/<release-id>`; unmanaged directories or
duplicate bundle hashes are invalid. Because an import changes catalog/source
hashes, it invalidates prior G30 verification, all G31 optimization evidence,
and G32 release evidence.

Runtime snapshot update, bundle import, and packaging share
`.isoworld-mutation.lock`, so a package cannot mix files from two states. G01
prechecks every installed release against the candidate API/features and rolls
back the whole snapshot if pre- or post-publication validation fails. Saves are
kept outside the package under
`<user-data>/saves/<world>/<release>/<slot>.json`, preventing same-named slots
from colliding across worlds. The packager copies one allowlisted private
snapshot, reruns the independent verifier against that snapshot in an isolated
Python process, derives package identity from the verified copy, and verifies
every archived byte before atomic publication. G32 emits that deterministic
source ZIP—including its verifier, Python scripts, tests and their allowlisted
fixtures, shared assets, and notices—to a fresh
external path; extract it and run `python run_game.py`. It is not a wheel or a
native executable.

## Phase-scoped game skills

All skills remain in `.agents/skills` in the Forge. None is copied into a game.
There is no all-in-one game-builder skill. Bootstrap, runtime migration,
platform migration, bundle import, test harness, each gameplay system family,
each pyray presentation layer, verification, optimization, and release have
separate skills and exit gates.

The game path has exactly twenty phase skills. Each declares one concrete scope,
explicit inputs and outputs, phase-specific invariants, a short workflow, and an
objective completion gate. G14 and G24 repeat one named system or UI-flow slice;
no invocation silently starts another phase or copies agent-control material to
the game.

The complete order, writable surfaces, repeatable phase IDs, downstream
invalidation, platform matrix, and external evidence rules are defined in
`docs/GAME_IMPLEMENTATION_PHASES.md`.

## Verification checklist

```bash
ruff check src tests
ruff format --check src tests
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m unittest \
  tests.test_m4_world_lifecycle tests.test_m4_content \
  tests.test_m4_bundle tests.test_m4_game_scaffold -v
PYTHONPATH=src python -m worldforge validate \
  examples/foundation/source/manifest.json --profile release
PYTHONPATH=src python -m worldforge analyze-narrative \
  examples/foundation/source/manifest.json --fail-on warning
PYTHONPATH=src python -m worldforge audit-runtime src/isoworld
```

M4 is complete only when worldpack regeneration is byte-stable, public schemas
parse, lifecycle/bundle/game tests pass, generated games pass their own
verifier and locked Ruff gates without the Forge on `PYTHONPATH`, and a real
exported bundle imports and runs headlessly from a working directory outside
the game.
