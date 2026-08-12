# ADR-0025: Migrate product identity without relabeling legacy contracts

- Status: accepted
- Date: 2026-07-31

## Context

The product is becoming World Forge and the intended repository/distribution
name is `world-forge`. Published schemas, formats, storage paths, generated
games, audits, and Studio data already contain older identities. A global
search-and-replace would break readers, hashes, provenance, and user data.

## Decision

Use a staged identity bridge:

1. New visible product and Python distribution identity use World Forge /
   `world-forge`; imports and CLI remain `worldforge`.
2. New generic formats use `world-forge.*` and new schema IDs.
3. Published `rpg-world-forge.*`, `isoworld.*`, schema IDs, `.worldforge/`,
   `RWFJ1`, legacy suffixes, protocol IDs, immutable audits, and provenance are
   retained and explicitly allowlisted.
4. Installed data lookup checks `share/world-forge` first and the legacy share
   path second during the bridge.
5. Legacy world project v2 remains readable without mutation; explicit v2 to v3
   migration changes only its persisted repository identity through a
   hash-anchored, journaled operation.
6. Studio app ID and user-data root remain unchanged until a separately reviewed
   native migration proves backup, divergence rejection, retry, and rollback.
7. GitHub remote rename, registry publication, and app-ID cutover require
   explicit owner approval and occur only after green hosted evidence.

Remaining old-name occurrences are governed by the exact generated identity
allowlist; the allowlist is not a wildcard waiver.

## Consequences

- Historical artifacts and readers remain trustworthy.
- New material has one canonical product identity without rewriting history.
- Repository rename is the last public cutover, not an implementation shortcut.
- A name collision or missing hosted evidence blocks cutover rather than
  triggering an automatic fallback or destructive rewrite.

## Rejected alternatives

### Global replacement

Rejected because published discriminators, schema IDs, hashes, and user-data
locations are compatibility contracts.

### Change the Studio app ID now

Rejected because it could split or silently lose SQLite/WAL, blobs, journals,
and Codex-home state without a native migration bridge.
