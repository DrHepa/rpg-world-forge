# ADR-0024: Add gamepack beside the legacy worldpack

- Status: accepted
- Date: 2026-07-31

## Context

The published `isoworld.worldpack` v1-v5 formats encode a deterministic RPG
world and are consumed by the stdlib-only `isoworld` runtime. Widening them into
a universal union would change semantics and hashes, burden non-RPG games with
world/actor/narrative fields, and risk existing saves, replays, bundles, and
generated games.

## Decision

Keep two explicit compilation lanes:

1. Retained RPG source compiles through the existing `worldforge compile` path
   to `isoworld.worldpack` v5 with compatible canonical valid output bytes and
   content hashes. Published worldpack v1-v5 readers and the
   `rpg-world-forge.project`, `rpg-world-forge.phase_report`,
   `isoworld.renderpack`, `isoworld.save`, and `isoworld.replay`
   discriminators remain readable according to their published versions.
2. Generic `world-forge.project` v1 game source compiles through
   `worldforge compile-game` to declarative `world-forge.gamepack` v1 and is
   consumed only by adapters that explicitly accept it.

The formats are additive. There is no rename, implicit migration, generic
worldpack union, or silent projection between them. Shared definitions are
extracted only where their semantics are genuinely identical.

This compatibility promise does not freeze implementation files or accept
unsafe or ambiguous input representations. Strict JSON, portable-path,
no-link/no-hardlink, retained-identity, bounded-resource, and durable
persistence checks may reject invalid inputs earlier. They must not relabel
published formats, reinterpret canonical valid documents, or change the
canonical valid output bytes and content hashes promised above.

## Consequences

- Legacy bytes, hashes, saves, replays, bundles, and games remain compatible.
- No-world/no-actor games use typed generic modules rather than fake RPG data.
- Generic gamepacks carry no scripts, prompts, credentials, runtime AI, or
  mutable source paths.
- Asset subjects explicitly distinguish `gamepack` from `legacy_worldpack`.

## Rejected alternatives

### Rename worldpack to gamepack

Rejected because changing a discriminator changes public meaning and canonical
hashes while concealing a breaking migration.

### Make `isoworld` the universal runtime kernel

Rejected because its bounded RPG semantics are a compatibility asset, not a
generic engine contract.
