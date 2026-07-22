# ADR-0014: Studio jobs begin with a closed read-only executor

- Status: accepted
- Date: 2026-07-22

## Context

ADR-0011 established durable job records without execution so workspace,
approval, and recovery boundaries could be proven first. Studio now needs
useful offline verification and deterministic runtime feedback, but a generic
command runner or an early provider adapter would let untrusted job input choose
processes, roots, arguments, network behavior, or cost-bearing tools.

## Decision

Studio writes exact managed `studio_job_v2` records while retaining the original
broad `studio_job_v1` contract for read, recovery, and manual cleanup only. New
jobs permit exactly four executable operations:

- `asset.receipt.validate`, with a portable receipt path relative to
  `world_root/assets`;
- `assetpack.verify`, with portable world-relative assetpack and required
  worldpack anchor paths;
- `runtime.headless`, with a portable worldpack path and integer tick count from
  zero through one million;
- `runtime.replay`, with portable worldpack and replay paths.

One FIFO scheduler owns a secondary Studio store. An immediate SQLite
transaction claims the oldest eligible queued v2 job only when no managed v2
job is running, so multiple scheduler instances cannot double-claim or execute
concurrently. Legacy v1 rows are never claimed or retried, including rows whose
operation name now matches a managed operation. Startup recovery remains owned
by the primary store: interrupted running v1 and v2 jobs become orphaned,
queued jobs remain queued, and orphans are never retried automatically.

The scheduler derives every root from the registered workspace, revalidates
root and input identities immediately before spawn, and rejects symlinks,
reparse points, hardlinks, non-regular files, non-portable names, and NFC/casefold
ambiguity. It starts only a fixed Python child worker with `shell=false`, a
sanitized environment, fixed module/bootstrap and working directory, strict
bounded JSON pipes, and no user-selectable process fields. The worker repeats
the proofs, invokes the existing validators/runtime loaders, repeats the proofs
after execution, and returns only bounded projected results.

Cancellation intent and monotonic progress are durable events. Queued
cancellation is immediately terminal. Running cancellation, timeout, and
service shutdown first terminate and reap the complete managed process tree on
Linux or Windows. Shutdown then marks the in-flight job orphaned with
`service_shutdown`; cancellation marks it canceled; timeout is a structured
failure. Worker crashes, malformed or oversized output, and contract failures
never expose tracebacks or absolute workspace paths.

## Consequences

- Studio gains deterministic offline feedback without network or provider
  capability.
- Previously valid v1 rows remain readable, listable, recoverable, and
  cancelable without being promoted into the executor capability boundary.
- The renderer remains job-list-only, and public `job.transition` cannot forge
  executor-owned transitions.
- Job input cannot select commands, modules, roots, arguments, or environments.
- A future mutating or provider operation requires a separate contract and ADR;
  it cannot enter this allowlist by convention.
- Existing worldpack, assetpack, receipt, replay, and state-digest semantics
  remain authoritative rather than being reimplemented in Studio.

## Rejected alternatives

### Generic executable jobs

Rejected because validation of a command string is not equivalent to a closed
capability boundary and would make shell, process, and filesystem access part of
the public protocol.

### Execute jobs in the service process

Rejected because malformed content, long deterministic runs, native libraries,
or cancellation would share failure and lifetime with the durable coordinator.

### Retry orphaned jobs automatically

Rejected because interruption may have happened after expensive or externally
observable work in future executors. Recovery records evidence and requires an
explicit new job.
