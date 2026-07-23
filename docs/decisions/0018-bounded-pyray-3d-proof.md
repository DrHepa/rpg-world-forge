# ADR-0018: Prove pyray GLB animation without claiming a 3D game

- Status: accepted
- Date: 2026-07-23

## Context

M6 now has hash-bound composition contracts, an immutable composed-bundle
transport, and an exact static adapter registry. None of those boundaries
proves adapter execution. A native experiment is needed before a game can
select a broader renderer/physics/packaging contract, but treating one loaded
model as collision, assetpack consumption, playability, or release readiness
would erase the reason capability declarations exist.

The pinned Python distribution is `raylib==6.0.1.0`. Its observed bundled
constants identify a `6.1-dev` header with RLGL 6.0, which is distinct from a
claim that the bundled native API is the stable raylib 6.0 release.

## Decision

Add the code-owned `pyray_3d_v1` declaration and exact static registry entry.
The declaration is verified only for Linux x86_64, runtime API
`>=0.5.0,<0.6.0`, presentation mode `3d`, and capability `animation_gltf`.
Physics and packaging components are explicitly `not_provided` at version
`0.0.0`. The renderer, session, and animation component IDs describe only this
wrapper at version `0.1.0`; they do not invent upstream SemVer. The required
`target_frame_milliseconds: 1000` field is a schema-bounded smoke ceiling, not
measured representative performance evidence.

The adapter accepts exactly one immutable asset plan and one actor binding.
The plan binds a portable payload-relative path, SHA-256, byte size, triangle
count, one portable animation ID, and keyframe count. The binding selects one
`actor:<id>`, positive uniform scale, and positive bounded layer. The resolver exposes
only `resolve_payload(PurePosixPath)` against an owner-retained private
snapshot.

Pure functions consume only frozen `RenderState`: X/Z grid conversion,
half-open floor inverse, explicit ray/plane tile picking, uniform
scale/translation of presentation bounds, tick-derived animation frame, and
deterministically ordered actor instances. Picking returns a cell; it never
dispatches a `GameAction`, invokes a reducer, or mutates simulation.

After exact file and budget validation, the native boundary opens a hidden
window and uses path-only model and animation loading. It passes an explicit
CFFI signed `int *` pointer, requires one exact animation name, positive
compatible skeleton/keyframes, a valid model, and finite ordered local bounds.
It unloads the complete animation array before the model and closes the window
last. Partial cleanup is at-most-once and retains the resolver when completion
is uncertain.

The synthetic test GLB is generated only in temporary roots and contains one
triangle, one joint/skin, JOINTS_0/WEIGHTS_0, an identity inverse bind matrix,
and two source `idle` translation samples. The audited binding resamples that
one-second animation to exactly 61 `keyframeCount` entries at 60 Hz. The
established safe GLB inspector validates it before native loading. Ubuntu x86_64
CI runs the native Xvfb smoke
without a skip path. Windows CI verifies the exact distribution, observed
header/RLGL constants, and function surface only; its output explicitly says
native 3D is not verified.

## Consequences

- One bounded path proves GLB load, animation enumeration/update, bounds, draw,
  and reverse-order cleanup.
- The adapter imports pyray only inside its fixed native factory and performs
  no dynamic discovery.
- `isoworld` remains independent of offline authoring tools and providers.
- Every current 3D/mixed profile remains incompatible because each requires
  `collision_gltf` and broader content/presentation capabilities.
- No collision, physics, navigation, LOD, performance, assetpack consumption,
  standalone packaging, Windows native graphics, playability, or M6 release
  claim follows from this proof.

## Rejected alternatives

### Declare the complete 3D profile after one model loads

Rejected because current profiles require collision, assetpack binding, world
presentation, and mixed-plane behavior that this slice does not implement.

### Select glTF nodes or implementation modules from contract data

Rejected because caller-controlled node, module, command, entry-point, or
filesystem locators would make a declaration executable discovery data.

### Treat model bounds as collision

Rejected because finite draw bounds do not establish collider semantics,
physics behavior, navigation clearance, or deterministic simulation.
