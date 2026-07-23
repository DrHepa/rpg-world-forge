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
execution, or package assembly. Runtime acquisition is deliberately not
implemented in this slice.

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
