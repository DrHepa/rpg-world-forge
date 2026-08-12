# Studio runtime provenance

`runtime-sources.json` is the authoritative, x64-only provenance input for a
future self-contained Studio runtime. It pins exact OpenAI Codex and
python-build-standalone archives for `linux-x64` and `win32-x64`, their
metadata, and the complete known Codex payload inventories.

Validate it without network access:

```console
python3 scripts/studio_runtime_sources.py
```

The validator is stdlib-only and performs no downloads, extraction, process
execution, or package assembly.

## Acquire pinned build inputs

The separate stdlib-only acquisition tool resolves only the exact target
archives, checksum files, metadata archive, and CPython source archive from the
validated checked-in contract. Use an absolute cache path outside the
repository:

```console
python3 scripts/studio_runtime_inputs.py fetch \
  --target linux-x64 \
  --cache-dir /absolute/path/to/studio-runtime-inputs
python3 scripts/studio_runtime_inputs.py verify \
  --offline \
  --target linux-x64 \
  --cache-dir /absolute/path/to/studio-runtime-inputs
```

Use `win32-x64` for the Windows inventory. Cache entries live at the fixed
`target/component/filename` layout. Fetching uses direct HTTPS plus one pinned
GitHub release-asset redirect, bounded responses, pinned sizes and digests,
exclusive temporary files, and no-replace publication. Offline verification is
read-only and never creates a network client. Neither command extracts archives
or assembles a package.

Both commands report `redistribution_status` as `blocked`. Acquiring every
technical input does not close, waive, or replace any provenance blocker.

## Deterministic assembly contract

`scripts/studio_runtime_assembly.py` defines the bounded x64-only assembly and
verification contract for `linux-x64` and `win32-x64`. A real assembly request
first validates `runtime-sources.json` and calls its redistribution assertion
before opening an archive or creating an output directory:

```console
python3 scripts/studio_runtime_assembly.py assemble \
  --target linux-x64 \
  --cache-dir /absolute/path/to/studio-runtime-inputs \
  --output-dir /absolute/path/to/studio-runtime-resources \
  --source-date-epoch 1784407912
```

That command currently fails with all seven open blocker codes and leaves both
cache and output untouched. This is intentional. The same assembly core is
covered with explicitly synthetic, non-publishable archives. Those tests prove
closed archive inventories, portable paths, pinned reads, exact Forge source
installation, output-tree verification, and byte-identical ZIP creation across
different roots without redistributing real runtime artifacts.

The pinned Linux Python archive is normalized only through
`runtime-archive-normalization-linux-x64.json`. That canonical, target-scoped
receipt is bound to the exact archive digest: it materializes its 1,048 bounded
relative symlinks as regular files at their exact source paths and retains all
3,474 regular paths. The receipt inventories every resulting source, link,
resolved target, mode, size, and digest, including eight case-sensitive
directory pairs and 25 case-sensitive file pairs. The Linux target therefore
requires a case-sensitive destination filesystem; assembly fails closed before
archive materialization on Windows and during the pinned private-stage binding
probe on any filesystem that aliases a required pair. Hardlinks, absolute or
escaping links, missing targets, cycles, and special-file targets remain
forbidden.

For that exact PBS source, the same canonical receipt bytes are packaged as the
inventoried control resource
`runtime/python/linux-x64/runtime-archive-normalization.json` (1,031,213 bytes,
SHA-256
`3c4fea7af2d435c036d412a56d7b762131e780560b339cbffe80e7637416db0e`).
Python and Studio validate the descriptor-read receipt, its archive identity,
link graph, counts, collision groups, and all 4,522 Python inventory entries.
Windows package manifests require `normalization: null` and cannot authorize the
Linux receipt.

An assembled test tree contains the complete selected Codex vendor root, the
selected Python payload, pure-Python `isoworld` and `worldforge`, public schemas
and contracts, Codex protocol material, `runtime-manifest.json`, and the
canonical `runtime-package-manifest.json`. The package manifest follows
`runtime-package-manifest.schema.json`, inventories every other file, records
the source archive identities and target launch paths, and always states
`release_ready: false`. ARM64 runtimes are neither declared nor accepted.

The retained `app.asar` verification includes the dedicated generic game
runtime bundle CJS surface. Its puzzle and branching-narrative smoke creates
only temporary pre-execution directories, verifies exact transfer integrity,
rejects same-size tampering, and keeps release support blocked. It never starts
Electron, Python, raylib, a model, or a provider. The same retained bytes also
exercise the generic headless structural inspector against both canonical
execution scripts and self-resealed order tampering. That inspection explicitly
requires packaged Python for execution and evidence semantics.

Existing synthetic trees and deterministic ZIPs can be checked without
creating or changing artifacts:

```console
python3 scripts/studio_runtime_assembly.py verify \
  --output-dir /absolute/path/to/studio-runtime-resources
python3 scripts/studio_runtime_assembly.py verify-zip \
  --zip /absolute/path/to/studio-runtime-resources.zip
```

These verification commands do not make a package publishable.

Assembly creates the destination exclusively. If writing or verification later
fails, the partial destination is deliberately preserved and the command fails
closed. The assembler never deletes or changes a pathname after merely checking
its identity, because another process could have replaced that name. An
operator may remove a failed synthetic destination only after independently
confirming its identity and contents.

Creation and publication retain the output ancestry, directories, and files
through final verification. POSIX walks from `/` with descriptor-relative
no-follow operations. Windows walks from a retained volume/share root using
`NtCreateFile` `RootDirectory` handles, creates both directories and files
relative to those handles, rejects reparse points, writes with `WriteFile`, and
omits delete sharing until final path binding succeeds.
Hosts without either complete primitive set report
`secure_primitive_unavailable` before mutating the destination.

## Shell-only package verification

The Electron `--dir` boundary is separate from runtime assembly. Package into
an explicit directory outside the repository, then verify the exact unpacked
tree:

```console
npm run package:dir -- \
  --output /absolute/path/to/studio-shell \
  --target linux-x64
npm run package:verify -- \
  --path /absolute/path/to/studio-shell/linux-unpacked \
  --target linux-x64
```

`win32-x64` is the other accepted target layout and produces `win-unpacked`.
The packaging wrapper accepts no raw electron-builder flags. Before spawning
the build, it rejects a missing, relative, existing, repository-contained, or
resolved repository-alias output, and requires its parent to exist. It then
creates the exact output exclusively and keeps the binding live: Linux passes
electron-builder a retained descriptor path under `/proc`, while Windows keeps
a stdlib no-delete output guard alive. Final verification rebinds the requested
name to that retained identity. The checked-in electron-builder output is a
required environment macro, and the wrapper supplies the same retained path as
an exact command-line override. There is no working in-repo default.

Electron-builder `afterPack` only flips the audited Electron fuses. After
electron-builder exits, the supported `npm run package:dir` parent wrapper
writes the canonical `resources/shell-package-manifest.json` through the
hardened snapshot backend, verifies the package, and finalizes the reserved
output identity. Invoking raw `electron-builder` directly is unsupported: it
leaves an incomplete, unverified directory without the wrapper-owned manifest
or finalization, so that directory must never be published or treated as a
package. On Windows only, the parent wrapper permits one immediate manifest
publication retry when the first backend terminates before its report with
either exact `windows_snapshot_package_sharing_conflict` or
`windows_snapshot_source_sharing_conflict` code. The setup code
`windows_snapshot_setup_sharing_conflict` is non-retryable and fails closed. It
waits for the failed backend process and its private snapshot handles to be
fully reaped; failed snapshot files remain preserved as described below. It
then reuses the same retained output identity, package path, source, target, and
Python executable without sleeping, rebuilding, or reserving another directory.
Existing or partial manifests and callback, publication, finalization, cleanup,
timeout, protocol, verification, or second sharing failures are never retried.
Verification never launches Electron. It retains the package root, directories,
and regular files while hashing; rejects symlinks, hardlinks, replacements,
aliases, empty extra directories, and extra files; checks the ASAR entrypoints;
and compares the committed runtime manifest, Codex protocol provenance/tree,
runtime sources, and runtime package/source schemas byte for byte. The pinned
Electron 43.2.0 Windows shell root includes exactly the literal `dxcompiler.dll`
and `dxil.dll` DXC pair; missing or additional root files fail closed. Process
builds first remove the generated process tree, Vite empties the renderer tree,
and electron-builder is limited to the exact 17 clean source/build files plus
its sanitized `package.json` (18 ASAR entries total before directories). The
verifier pins the clean `dist-electron` and `dist-renderer` source snapshots
through final binding and requires exact ASAR file/directory equality plus
matching sizes and hashes; stale files and hidden vendor/runtime payloads are
rejected.

The result is deliberately `shell_only`, with no Python or Codex runtime
payload. It always records `release_ready: false`, `blocked`, and the same seven
open blocker codes as `runtime-sources.json`. Linux retains descriptor-relative
no-follow handles. On Windows, a stdlib Python backend reuses the audited Forge
`NtCreateFile` RootDirectory, no-reparse, no-delete-sharing, identity, and final
binding primitives. It keeps original and private snapshot handles live while
Node performs supported static ASAR and fuse inspection. The generic-asset
runtime smoke consumes the exact retained ASAR bytes during this same binding
window and must return the inventory SHA-256 and byte count before finalization;
it never reopens the published ASAR after the snapshot closes. Windows requires
an absolute Python 3.11/3.12 path in `WORLD_FORGE_STUDIO_BUILD_PYTHON` and never
searches `PATH`; the deprecated `RWF_STUDIO_BUILD_PYTHON` alias is accepted
only when it does not conflict, and unavailable primitives fail closed. The
Windows backend marks
only the two snapshot files that it created for deletion through their retained
delete-capable handles, revalidates them, and closes those handles while the
snapshot directory binding is still guarded. On successful finalization it
captures the root identity, closes every package, source, snapshot-chain, and
guard handle, then reopens that exact root with delete, list, read-attribute,
and synchronize access while sharing reads and writes but never deletion. It
revalidates the same non-reparse directory identity, proves emptiness by
enumerating the retained handle, marks that handle for deletion, closes it, and
only then acknowledges finalization. Neither Python nor Node uses path-based or
recursive snapshot cleanup. Failed or uncertain roots, including identity,
reparse, nonempty, open, disposition, or close failures, are preserved
fail-closed and no final acknowledgement is emitted.

## Redistribution remains blocked

The checked-in contract is intentionally fail-closed. It records every known
open legal/provenance blocker as structured data. Consequently, the release
assertion must fail:

```console
python3 scripts/studio_runtime_sources.py --require-redistributable
```

Do not publish these runtime inputs until every blocker is closed with exact
reviewed evidence and the contract, validator, notices, and SBOM are updated
together. A valid provenance document is not evidence that redistribution or a
self-contained Studio package is ready.
