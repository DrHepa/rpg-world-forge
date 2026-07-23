# ADR-0020: Consume composed releases beside legacy worlds

## Status

Accepted for M6.

## Decision

Generated games retain the byte-stable `isoworld.world_catalog` v1 path and add
an independent `isoworld.composed_runtime_catalog` v1. A composed entry is
selected by world, release, presentation profile, exact adapter identity and
exact bundle identity. The derived storage tree contains only immutable
composed-bundle payloads.

The standalone game recomputes bundle inventory, pack correlations, world
identity and the code-owned adapter key. Compatibility reports stored in a
bundle are diagnostic evidence, never runtime authorization. Only
`isoworld_raylib_2_5d@0.1.0` with its exact declaration hash may dispatch the
existing native `GameApp`, and only on Linux x86_64. `pyray_3d_v1` remains a
bounded loading proof and cannot dispatch a game because its required collision
capability is absent.

Headless simulation, saves and replays remain presentation-neutral and keyed by
world/release. Representation variants with the same world hash therefore share
deterministic state and replay slots. Windows can verify and simulate a
Linux-targeted bundle headlessly, but MUST NOT claim or attempt native
presentation compatibility.

## Consequences

- No composed selector preserves exact legacy CLI behavior.
- A native-incompatible composed selection fails before importing `pyray`.
- Generated games remain offline, standalone and free of `worldforge`,
  provider, MCP, Modly or Blender dependencies.
- Composition ordering is a pure plan: world base, world overlay, UI overlay;
  audio remains a separate slot sequence.
