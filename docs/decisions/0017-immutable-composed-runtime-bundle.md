# ADR-0017: Composed runtime bundles preserve exact verified inputs

- Status: accepted
- Date: 2026-07-23

## Context

The M6 composition contracts can prove that a world, profile, capability
catalog, adapter declaration, and selected unchanged M5 packs agree. A runtime
or standalone game still needs one immutable transport boundary that cannot
silently discover files, retain authoring evidence, execute an adapter, or
replace an existing release.

Legacy runtime-bundle v1 and world-catalog v1 are established 2D/2.5D
contracts. Extending either would change their meaning and byte-level release
evidence.

## Decision

Add the independent
`rpg-world-forge.composed_runtime_bundle` format v1. Its exact tree contains:

- the four M6 contract documents at fixed paths;
- a compatibility report recomputed by the builder from those exact documents
  and the integral worldpack, renderpack, and assetpack loaders;
- one mandatory worldpack and explicit nullable renderpack/assetpack
  selections, with at least one presentation pack selected;
- every exact pack-referenced runtime payload; and
- a non-empty explicit set of approved notice files.

The canonical manifest binds an independent bundle ID and stable SemVer, the
platform and runtime API target, typed/hash-bound contract and pack references,
the compatibility report, and a sorted exact byte inventory. Its bundle hash
is the canonical manifest-payload hash. The composition remains the sole source
of world, release, profile, adapter, pack, and semantic-slot identity; the
bundle invents no composition ID.

Builds capture source files through private, sequential stable reads, copy the
captured exact bytes into a same-parent exclusive stage, verify the complete
stage, journal its identity and ready hash, and publish with a native
no-replace directory primitive. Recovery changes only an identity-owned stage.
It verifies a ready stage or already-published destination before cleanup and
preserves any replacement or mismatch for inspection.

Loads first materialize the exact public tree into a private identity-owned
snapshot. They validate inventory and the runtime-only boundary, recompute and
byte-compare compatibility evidence, require an exact static registry key, and
then load the selected packs from the private owner. A loaded result closes an
inner renderpack snapshot before its outer bundle snapshot. It never exposes a
mutable source path and never invokes the opaque adapter value.

Legacy runtime-bundle v1, world-catalog v1, generated games, and all M5 pack
bytes remain unchanged.

## Consequences

- Bundles built in different roots have identical payload bytes and hashes.
- Source or published-path mutation cannot change a loaded bundle.
- Links, reparse points, hardlinks, special files, extra files, nonportable
  paths, authoring formats, provider/model/MCP data, receipts, workflows, and
  weights fail closed.
- Linux and Windows use exclusive native publication; other platforms fail
  before staging.
- A valid bundle proves only the declared static compatibility boundary. It
  does not prove adapter execution, rendering, physics, animation, collision,
  playability, performance, packaging, or M6 release readiness.

## Rejected alternatives

### Extend runtime-bundle v1

Rejected because it is a renderpack-specific M4/M5 contract with existing
catalog and generated-game consumers.

### Persist caller-supplied compatibility evidence

Rejected because stale or forged evidence could disagree with the exact pack
bytes. The builder always recomputes it.

### Discover or dynamically import an adapter

Rejected because adapter declarations are data, not implementation locators.
Only an exact code-owned static registry may resolve an opaque value, and this
boundary never calls it.
