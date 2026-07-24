# ADR-0021: Keep Studio runtime distribution fail-closed

- Status: accepted for partial M6
- Date: 2026-07-24

## Context

The Studio desktop shell can supervise exact Python and Codex executables, and
the repository now has pinned x64 provenance, secure acquisition, deterministic
assembly, and static package-verification mechanics. Those technical controls
do not establish permission or sufficient provenance to redistribute every file
in the selected runtime payloads.

The authoritative
`apps/studio/packaging/runtime-sources.json` contract records
`release_ready=false` and these seven open blockers:

1. `codex_ripgrep_static_dependency_notice_sbom_incomplete`
2. `linux_bwrap_lgpl_corresponding_source_relink_build_materials_incomplete`
3. `linux_bwrap_musl_provenance_incomplete`
4. `pbs_zlib_ng_license_incomplete`
5. `linux_berkeley_db_dbm_route_unresolved`
6. `windows_vc_runtime_redistribution_authority_unresolved`
7. `github_attestation_trust_root_rfc3161_verification_pending`

Treating known hashes, successful downloads, or deterministic packaging as a
license or provenance grant would collapse two independent trust decisions.

## Decision

Self-contained Studio assembly and publication remain fail-closed until every
blocker is closed with reviewed exact evidence. The real assembly entry point
must reject the provenance contract before opening a cached archive or creating
an output. CI must not download or package the blocked runtimes and must not
define artifact publication or signing for them.

Acquisition caches, assembled resources, package directories, archives, games,
and end-to-end artifacts live outside the repository. Checked-in material may
contain only the source, validation policy, schemas, normalization receipts,
tests, and provenance records required to reproduce and review the mechanics.

Synthetic, non-publishable archives may exercise deterministic assembly and ZIP
verification. The Electron shell may also be packaged and statically verified
without bundled runtimes. Such a package must use mode `shell_only`, retain
redistribution status `blocked`, retain all seven blocker codes, state
`release_ready=false`, and prove that Python and Codex payloads are absent.

No future change may flip `release_ready` as an isolated boolean edit. Closing
the boundary requires one reviewed update that synchronizes the provenance
contract, exact notices and SBOM, corresponding-source/relink materials,
pruning decisions, redistribution authority, attestation verification,
validators, package inventories, documentation, and Linux/Windows evidence.

## Consequences

- Local development can use explicit operator-owned Python and Codex
  executables without making a redistribution claim.
- Secure acquisition and deterministic assembly remain useful, testable
  preparation but are not release evidence.
- A verified shell-only package is not a self-contained Studio artifact and
  cannot be published as one.
- M6 remains partial even when local implementation gates pass; hosted and
  native evidence is still required after the final push.
- The distribution boundary fails closed rather than silently omitting notices,
  guessing legal authority, or downloading blocked payloads in CI.

## Rejected alternatives

### Publish because every archive has a pinned hash

Rejected because integrity identifies bytes; it does not provide dependency
notices, corresponding source, relink materials, redistribution authority, or a
complete attestation trust decision.

### Hide unresolved components from the package manifest

Rejected because a manifest must describe the exact shipped tree. Removing
evidence would not remove a statically linked or bundled dependency.

### Download and assemble in CI without uploading an artifact

Rejected while the boundary is blocked. It would create undistributable runtime
copies without adding authoritative legal evidence and would make an avoidable
network dependency part of the readiness gate.
