# ADR-0015: Studio changesets retain immutable review evidence

- Status: accepted
- Date: 2026-07-23

## Context

The original Studio changeset stored proposed bytes in external
content-addressed storage but retained only the hash of an existing base file.
After the workspace changed, a reviewer could no longer reconstruct the exact
base-to-proposed change that approval would authorize. Approval also identified
only a changeset ID, and the row remained `approved` while apply was mutating
files, leaving reject and apply able to race.

## Decision

New changesets use `studio_changeset` format version 2. Each operation retains
both base and proposed UTF-8 byte snapshots, when present, in the existing
Studio-owned content-addressed store. Hash, byte size, and retained bytes come
from the same bounded stable read. The record commits to the ordered operation
descriptors with a canonical `review_sha256`; operation order is significant.

Review output is derived only from retained CAS bytes, never from the mutable
workspace. It contains exact line-preserving text hunks and, for strict JSON,
a deterministic JSON Pointer supplement. Invalid JSON only removes the
supplement. Review output has a fixed byte bound and fails closed rather than
truncating exact evidence. Missing, linked, replaced, or hash/size-mismatched
CAS content makes review unavailable as an error.

Approve, reject, and apply require the caller to echo the exact
`expected_review_sha256` for v2. Legacy v1 rows remain readable and actionable
without that field, but return a typed `legacy_base_bytes_not_retained` result
instead of pretending an exact diff exists.

Apply first atomically transitions `approved` to durable `applying`. Only then
may it create a version 2 apply journal or touch source files. The journal binds
the changeset format version, review hash, ordered public operations, workspace,
and world identity. Recovery validates that identity before completing or
rolling back. An `applying` row with no published journal is safely restored to
`approved`, because no source temporary or target mutation occurs before the
journal publication. Unknown or inconsistent durable state fails closed.

The Python service exposes exact stage, get, list, diff, approve, reject, and
apply request/response contracts. Electron exposes only named human controls:
staging maps to one `replace` operation with `expected_base_sha256`, while get,
diff, and action replies are correlated to the requested ID, review hash, and
resulting status. This does not claim a visual review workflow. The Codex MCP
boundary continues to expose exactly stage, get, and list; it cannot diff,
approve, reject, or apply.

## Consequences

- A human action authorizes the exact immutable review identity it inspected.
- Workspace edits after staging cannot alter displayed review evidence.
- Base and proposed snapshots increase external user-data storage usage; the
  existing per-file and aggregate retained-byte bounds limit that cost.
- v1 rows remain operational but cannot acquire evidence that was never stored.
- `applying` is externally observable and blocks reject or a second apply.
- Journal recovery remains compatible with legacy version 1 journals while new
  journals close review-identity and state-race gaps.

## Rejected alternatives

### Diff the current workspace on demand

Rejected because mutable workspace bytes are not the bytes originally staged
and therefore cannot prove what was reviewed.

### Store only a generated diff

Rejected because a diff is another derived representation and cannot replace
the exact retained inputs needed for independent verification or future views.

### Truncate large review output

Rejected because truncated output can conceal changes while appearing
reviewable. Creation instead rejects a changeset whose exact review exceeds the
fixed bound.
