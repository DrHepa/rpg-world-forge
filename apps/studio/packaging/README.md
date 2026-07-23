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
