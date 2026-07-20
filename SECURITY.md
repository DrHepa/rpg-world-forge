# Security policy

## Supported versions

The latest `main` branch is supported during the alpha phase.

## Reporting

Do not open a public issue for credential exposure, path traversal, unsafe asset
processing or arbitrary code execution. Report the problem privately to the
repository owner through GitHub's security reporting channel when enabled.

World projects, worldpacks, renderpacks, asset manifests, runtime bundles, and
game catalogs are untrusted inputs. Validators must reject paths outside their
declared root. Generated projects must never commit API keys, model-service
credentials, private reference material, or local-model weights.

Worldpack loaders verify structural runtime invariants and the canonical content
hash and reject packs over 64 MiB. Narrative conditions and effects are strict
allowlists; the runtime never evaluates source text, templates, or scripts.
Knowledge boundaries are checked again at dialogue/effect execution. Saves and
replays are limited in size, versioned, content-hash-bound, and
digest-checked. Tiled/LDtk import accepts only the documented finite JSON
subsets; it never evaluates scripts or external object payloads.

## Runtime bundle and catalog boundary

Bundle export and import must use canonical manifests and verify every file's
relative path, size, media type, file signature, and SHA-256 before publication
or catalog mutation. Export destinations and import sources must be standalone
and external to Forge/world/bundle/game repositories. Reject missing or extra entries, empty unmanaged directories,
duplicate JSON keys, noncanonical JSON, path traversal, absolute paths,
non-NFC/case-colliding/Windows-reserved paths, and declared payloads that exceed
the documented limits.

Symlinks, hardlinks, sockets, devices, and other special files are invalid in a
world project release, bundle, or imported game-data tree. Never follow a link
to “resolve” it into the accepted tree. Bundle verification must cross-check
world ID, stable SemVer release ID, worldpack/renderpack content hashes, runtime
requirements, asset inventory, and license inventory rather than trusting the
outer manifest alone.

Only runtime-safe formats may cross the release boundary. Reject authoring
source, prompts, candidates, production manifests, phase state, credentials,
provider/model/workflow identifiers, Modly extensions, and AI SDK metadata at
any depth. An MIT or Apache-2.0 code license is not evidence that model weights,
datasets, references, or generated outputs may be redistributed.

Game import must verify the existing catalog and all installed releases before
mutation, require the caller's expected bundle SHA-256, copy through private
staging, take the catalog lock, install by atomic
rename, and roll back on failure. Imported releases are immutable and are never
mounted from a mutable world checkout, symlinked, or used as Git submodules.
Changing one byte requires a new release and catalog entry.

## Generated game boundary

A generated game contains no Forge or authoring control plane: no `worldforge`
dependency/import, `AGENTS.md`, `.agents/`, `.worldforge/`, editable canon,
provider SDK, model client, prompt execution, or runtime network requirement.
Its vendored `src/isoworld/` snapshot is verified against `runtime.lock.json`;
game-owned code lives under `src/game/`. Snapshot updates replace the complete
tree with an expected-current-hash check and precheck every installed bundle.
The independent verifier/package path does not execute mutable `src/game` code
to establish trust: it parses declarative identity, verifies the runtime lock,
then uses the locked catalog verifier. Platform changes are separately locked
in `platform.lock.json` plus the CI-consumed `requirements.lock` and must keep
the supported exact Python/raylib/build dependency closure, direct/build
declarations, CI matrix, notices, and native evidence consistent. Alternate
PEP 517 backend paths, dependency groups, optional dependencies, and package
manager tables fail closed until their owning G02 migration explicitly extends
the contract.

Run the Forge-side game boundary audit and the generated game's independent
verifier before packaging. Release archives use an explicit allowlist and must
include the runtime lock, platform lock, immutable bundle catalog, payload
hashes, and required notices while excluding caches, local saves, authoring
evidence, and undeclared files. Packaging operates on one private snapshot,
verifies that copy independently, derives its identity from the copy, then
reopens the completed archive and checks its exact inventory, metadata, sizes,
and hashes before publishing it.
