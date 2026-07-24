# Security policy

## Supported versions

The latest `main` branch is supported during the alpha phase.

## Reporting

Do not open a public issue for credential exposure, path traversal, unsafe asset
processing or arbitrary code execution. Report the problem privately to the
repository owner through GitHub's security reporting channel when enabled.

World projects, worldpacks, renderpacks, assetpacks, asset manifests, production
requests/receipts, runtime bundles, and game catalogs are untrusted inputs.
Validators must reject paths outside their declared root. Generated projects
must never commit API keys, model-service credentials, private reference
material, or local-model weights.

Worldpack loaders verify structural runtime invariants and the canonical content
hash and reject packs over 64 MiB. Narrative conditions and effects are strict
allowlists; the runtime never evaluates source text, templates, or scripts.
Knowledge boundaries are checked again at dialogue/effect execution. Saves and
replays are limited in size, versioned, content-hash-bound, and
digest-checked. Tiled/LDtk import accepts only the documented finite JSON
subsets; it never evaluates scripts or external object payloads.

## Asset production and assetpack boundary

Asset validation must bind the exact world, target, approved bibles, inventory,
specification, request, selected inputs, production receipt, processing receipt,
license evidence, QA report, and processed outputs. JSON records reject duplicate
keys, unknown fields, noncanonical paths, traversal, oversized values, and
content that does not match its declared media type, signature, size, and
SHA-256. File consumers accept bounded regular files and must not follow
symlinks or substitute a later filesystem object for bytes already inspected.

Three-dimensional output must be a structurally valid GLB that matches declared
coordinates, units, nodes, materials, rigs, animations, colliders, LODs, and
resource budgets. External URIs and undeclared payloads are forbidden. The
engine-neutral `assetpack_v1` itself may contain only its closed runtime schema:
identity and coordinate fields, approved runtime file records, hashes, metrics,
and semantic bindings. It explicitly excludes license/notices fields or files
and all authoring metadata, including `.blend` files, authoring manifests,
bibles, inventories, specifications, requests, receipts, recipes, candidates,
prompts, provider/model records, MCP configuration, credentials, and weights.
Required runtime licenses and notices travel beside the hash-sealed assetpack as
separately verified material in the immutable handoff or a later M6 bundle.

The committed narrative-neutral M5 fixture is local, procedural, and offline.
Its `openai` field is a schema-required route namespace and is not evidence of a
provider, model, or network execution. Its committed manifests are authoring-side
`production` records, not runtime packs. Readiness gates build, verify, finalize,
and release-validate copied manifests and runtime outputs only in disposable
external directories.

## Runtime bundle and catalog boundary

The current runtime bundle is the 2D/2.5D worldpack/renderpack handoff; it does
not accept a 3D assetpack. Bundle export and import must use canonical manifests
and verify every file's relative path, size, media type, file signature, and
SHA-256 before publication or catalog mutation. Export destinations and import
sources must be standalone and external to Forge/world/bundle/game
repositories. Reject missing or extra entries, empty unmanaged directories,
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
The legacy generated pyray path consumes only `isoworld.renderpack` v1. The
additive M6 composed-release catalog may independently verify an immutable
composed bundle, but native dispatch remains authorized only for the exact
Linux x86_64 legacy 2.5D adapter. The bounded pyray GLB proof is incompatible
without collision and must not be described as accepting or executing a
playable 3D composition.
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

Composed-release import must preserve the legacy catalog bytes, verify every
existing composed entry, require exact world/release/profile/adapter/bundle
identities, copy from a private pinned snapshot, publish without replacement,
and roll back only material it still owns. Stored compatibility reports are
diagnostic; authorization is recomputed against the static code-owned registry.
Unsupported native selections must fail before importing pyray or opening a
window. Saves and replays remain bound to the exact world content hash, not to a
presentation declaration.

## Studio runtime distribution boundary

Runtime downloads, caches, assembled resources, shell packages, ZIPs, games,
and end-to-end artifacts belong outside this repository. The checked-in Studio
runtime-source contract is untrusted input and must pass its strict schema,
inventory, hash, size, target, and cross-correlation checks before use. Fetching
or assembling pinned bytes does not establish redistribution authority.

Self-contained assembly and publication remain blocked while any of these exact
codes is open:

- `codex_ripgrep_static_dependency_notice_sbom_incomplete`
- `linux_bwrap_lgpl_corresponding_source_relink_build_materials_incomplete`
- `linux_bwrap_musl_provenance_incomplete`
- `pbs_zlib_ng_license_incomplete`
- `linux_berkeley_db_dbm_route_unresolved`
- `windows_vc_runtime_redistribution_authority_unresolved`
- `github_attestation_trust_root_rfc3161_verification_pending`

The real assembler must enforce `release_ready=false` before opening an archive
or creating an output. A synthetic assembly validates mechanics only. A
statically verified `shell_only` package must contain neither Python nor Codex,
must retain all blocker codes and redistribution status `blocked`, and must
never be presented as a self-contained release.

Closing the boundary requires a synchronized reviewed change to the provenance
contract, exact notices/SBOM, corresponding source and relink materials,
pruning decisions, redistribution authority, attestation verification,
validators, package inventories, and target evidence. CI must not download,
sign, upload, or publish blocked runtimes.

## Build and CI supply chain

The root and generated-game requirement files pin exact supported versions, but
they do not include hashes for every requirement and are not a complete
hash-locked supply-chain contract. Package metadata, requirement files, build
requirements, platform locks, and notices must remain synchronized. Clean
isolated wheel and sdist installations must pass `pip check`, installed contract
auditing, and vulnerability auditing of the exact direct requirements.

Third-party GitHub Actions are pinned by full commit SHA with checkout
credentials disabled. Downloaded security executables must be matched against a
verified upstream checksum file before execution. Secret scanning covers the
complete Git history; exceptions must be narrow, reviewed fingerprints for
intentional test fixtures rather than path-wide exclusions.

Local success does not prove unpublished hosted jobs. Ubuntu 24.04 and Windows
Server 2022 release-readiness/native-smoke results, the dependency audit, and the
full-history secret scan remain pending until a push executes those jobs. A
missing, skipped, or failed required row blocks publication.
