# Support and evidence matrix

This document separates what the contracts can describe from what the current
implementation and exact reviewed revision have proved. An evidence level is:

- **Contract**: schema/validator/descriptor semantics exist.
- **Local deterministic**: headless, fake-backend, or bounded local tests pass.
- **Native**: the real OS/architecture/backend executed the behavior.
- **Hosted**: required CI executed the exact reviewed commit on that platform.

No lower level implies a higher one. Pending, skipped, unavailable, or stale
evidence is not a pass.

`scripts/verify_multigenre_release.py` defines the canonical source-to-native
lineage and its exact four-report aggregation. It hashes one retained fixture
snapshot, restores every emitted save in both packaged copies, and requires the
host-locked raylib wheel digest before native execution. Local `--native off`
can mint a deterministic v1 release report only on a supported headless host:
Linux x86_64 or Windows x86_64 with CPython 3.11/3.12. It proves the
deterministic external compile, asset, bundle, standalone, persistence,
package, and extraction chain while recording native as `untested`. ARM64 hosts
can run lower-level logic/unit checks, but they cannot mint v1 headless/release
evidence and must fail closed instead of publishing a passed report. Hosted
status is
**PENDING** until `--native required` passes on Ubuntu 24.04 and Windows Server 2022
for Python 3.11 and 3.12 and the aggregate verifies one source revision and
identical fixture hashes.
The authoritative machine-readable status is
`docs/evidence/multigenre-release-status.json`.

## Current implementation matrix

| Capability | Contract | Local deterministic | Native | Hosted | Current conclusion |
| --- | --- | --- | --- | --- | --- |
| Generic profiles, typed modules, phase-report v3, gamepack v1 | Yes | Implemented and covered by local contract/determinism tests | Not applicable to authoring | Pending for the uncommitted overlay | Authoring and compilation contracts implemented |
| Abstract puzzle compilation, analysis, assets, headless/save/replay/package | Yes | Implemented with deterministic fixture and recording backend | Pending for raylib 2D | Pending for exact overlay on Linux and Windows | Runtime support remains blocked |
| Generic raylib 2D puzzle | Yes | Bounded recording-backend behavior implemented | Pending | Pending | Not native-verified |
| Generic raylib 2D/text narrative | Yes | Bounded recording-backend behavior implemented | Pending | Pending | Not native-verified |
| Action-framing authoring case | Yes | Exact authored gamepack plus 16-file asset production/QA lineage | Adapter absent; execution untested | Pending | `authoring_only` / `adapter_not_evaluated`; packaging unverified and release blocked |
| Faction-strategy authoring case | Yes | Exact authored gamepack plus 16-file asset production/QA lineage | Adapter absent; execution untested | Pending | `authoring_only` / `adapter_not_evaluated`; packaging unverified and release blocked |
| Modular-roguelite authoring case | Yes | Exact authored gamepack plus 16-file asset production/QA lineage | Adapter absent; execution untested | Pending | `authoring_only` / `adapter_not_evaluated`; packaging unverified and release blocked |
| Sports-career authoring case | Yes | Exact authored gamepack plus 16-file asset production/QA lineage | Adapter absent; execution untested | Pending | `authoring_only` / `adapter_not_evaluated`; packaging unverified and release blocked |
| Systemic-simulation authoring case | Yes | Assets are explicitly `not_applicable` | Runtime requested but adapter absent and execution untested | Pending | Runtime blocked; no asset placeholder is permitted |
| Legacy RPG `isoworld` raylib 2.5D | Yes | Existing deterministic/runtime tests | Baseline Linux x86-64 evidence exists; exact overlay not yet promoted | Baseline CI only; exact overlay Pending | Retained legacy specialization, not generic 2D/3D |
| Bounded GLB load/animation/draw proof | Partial | Local structural and bounded adapter tests | Baseline Linux-only proof; exact overlay Pending | Pending | Not a playable 3D profile |
| Playable generic 3D | No complete adapter | No | No | No | **Unsupported**: collision, interaction, camera/gameplay, packaging, and platform evidence are absent |
| Mixed 2D/2.5D/3D presentation intent | Yes | Composition contracts only | No complete generic mixed adapter | No | Authoring-valid can remain runtime-unsupported |
| Windows world-project v2 to v3 migration | Yes | Cross-platform unit/adversarial tests and CI gate definition | Native Windows rerun Pending for exact overlay | Pending | Do not claim hosted migration proof yet |
| Generic Studio editing and fixed creation controls | Yes | Implemented and covered by local source tests | Native desktop/package proof incomplete | Pending | Local implementation only; not hosted, packaged-shell, or native proven |
| Self-contained Studio redistribution | Blocked contract | Synthetic/shell-only verification | No authorized redistributable runtime | No | **Unsupported** until provenance/legal blockers close |

## Format compatibility

| Lane | Read | Write | Meaning |
| --- | --- | --- | --- |
| `isoworld.worldpack` v1-v5 | Yes | v5 through retained compiler | Legacy deterministic RPG runtime content |
| `rpg-world-forge.project` v2/v3 | Yes without mutation | New legacy worlds use v3 | Retained world-authoring identity |
| `world-forge.project` v1 | Yes | Yes | Generic creation project/library/game source identity |
| `world-forge.gamepack` v1 | Yes | Yes for supported generic logic shapes | Immutable generic logic artifact |
| `world-forge.assetpack` v1 | Yes | Yes from exact release-ready generic lineage | Sealed runtime assets, not runtime support |

## Status dimensions

Authoring is `valid|invalid`; compilation is
`not_requested|compiled|unsupported|failed`; assets are
`unplanned|planned|produced|processed|sealed|failed`; adapter is
`absent|declared|verified`; execution is per-platform
`untested|headless_verified|native_verified|failed`; packaging is
`unverified|verified|failed`; release is `blocked|ready`.

`supported` is permitted only when all required mechanics/assets/adapter/
platform/package evidence is complete for the same immutable logic hash.

The six asset-bearing fixtures contain 96 D2 asset-fixture files in total: 16
for each executable puzzle/narrative case and each of the four authoring-only
cases. A `release_ready` asset manifest proves complete reviewed production and
QA lineage. It is not a sealed assetpack and is never a released game.
