# ADR-0023: Separate authoring contracts from runtime adapters

- Status: accepted
- Date: 2026-07-31

## Context

World Forge must describe many kinds of games without claiming that every valid
design can execute in the current runtimes. Gameplay, world, narrative,
fiction, presentation, production, and runtime target are independent facets.
Genre or presentation labels cannot prove that executable mechanics exist.

## Decision

Authoring contracts describe reviewed intent, typed content, requirements,
provenance, and evidence identities. They can validate and compile even when no
runtime implements their required mechanics.

Runtime adapters are separate, versioned, code-owned implementation
descriptors. Resolution requires an exact accepted logic format/version,
execution semantics, required features, presentation, input, asset formats,
platform/architecture/backend, persistence, packaging target, and immutable
runtime snapshot. Adapter selection never comes from genre or fiction.

Support is reported in independent dimensions. Authoring validity is not
runtime executability. A sealed assetpack or static compatibility result does
not prove headless, native, hosted, packaging, or release status. Every positive
claim cites exact machine-readable evidence for the same immutable logic hash.

## Consequences

- Valid-but-unsupported genres fail closed with missing capability reasons.
- Required mechanics cannot be hidden as optional.
- Studio and CLI must display authoring and execution separately.
- New adapters can be added without widening source contracts or `isoworld`.
- No runtime AI, arbitrary code, or provider dependency enters compiled data.

## Rejected alternatives

### Select a runtime from a genre or presentation label

Rejected because labels do not define executable actions, state, rules,
effects, assets, persistence, or platform evidence.

### Treat validation or sealing as support

Rejected because contract integrity and runtime behavior are different claims.
