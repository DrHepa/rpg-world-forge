# ADR-0016: Runtime composition precedes runtime implementation

- Status: accepted
- Date: 2026-07-23

## Context

M5 ends at immutable, engine-neutral asset production. The current reference
runtime and runtime bundle remain 2D/2.5D renderpack consumers, while a valid
assetpack proves only the portable 3D handoff. M6 needs one vocabulary for 2D,
2.5D, 3D, and layered presentation without mutating those M5 packs or treating
an implementation choice as verified capability.

Renderpack v1 intentionally has no per-asset 2D-versus-2.5D field. Changing it
would invalidate an established M5 boundary. UI and audio are also orthogonal
to how the world plane is represented.

## Decision

M6 starts with five closed, hash-bound format-v1 contracts:

- a static capability catalog containing the existing runtime feature IDs and
  finite content, presentation, packaging, and fixed-step capabilities;
- six exact world-presentation profiles for 2D, 2.5D, 3D, and the supported
  two-layer combinations;
- an adapter declaration with a strict version, state, runtime API range,
  supported platforms/modes/capabilities, pinned component identities, and
  numeric budgets;
- a composition that binds world, release, profile, catalog, adapter, unchanged
  M5 pack references, and explicit semantic-slot ownership;
- a compatibility report with eight static checks and finite issue codes.

The composition owns the hash-bound 2D/2.5D classification for renderpack
bindings. It never rewrites the renderpack. Assetpack bindings must retain their
declared representation. World base and overlay planes follow the exact profile
layer order; UI and audio use separate optional planes and capabilities.
Bindings not selected by the composition are inert.

`verify_runtime_composition` uses the integral worldpack v1-v5 loader,
renderpack v1 loader, and assetpack v1 verifier. It does not execute an adapter
or choose an engine. An adapter in `declared` state can never produce a
compatible report.

The public contract catalog remains format v1. Its historically named
`m5_phases` provenance field now permits `M6`; the name is retained to avoid a
catalog migration and all existing entries remain unchanged.

## Consequences

- 2D, 2.5D, 3D, and layered intent can be reviewed before runtime code exists.
- M5 pack bytes and the stdlib-only `isoworld` boundary remain unchanged.
- Capability and compatibility claims are finite, deterministic, and
  machine-readable.
- A verified adapter, composed bundle, native runtime, collision, animation,
  benchmarks, packaging, and release evidence remain separate M6 work.
- Provider, model, MCP, module, command, executable, and filesystem locator
  data cannot enter the adapter contract.

## Rejected alternatives

### Add representation fields to renderpack v1

Rejected because the outer hash-bound composition can classify selected
bindings without changing a stable M5 runtime pack.

### Treat a declared adapter as runnable

Rejected because names, versions, and budgets are requirements, not evidence
that loading, animation, collision, determinism, or packaging works.

### Put UI and audio into every world profile

Rejected because those planes are independently selectable and should require
their own capabilities only when a composition owns corresponding slots.
