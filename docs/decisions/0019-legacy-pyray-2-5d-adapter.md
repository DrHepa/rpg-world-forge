# ADR-0019: Register the legacy pyray runtime as a 2.5D adapter

- Status: accepted
- Date: 2026-07-23

## Context

The reference runtime already consumes worldpack v5 and renderpack v1 through
an isometric projection with authored elevation and deterministic depth
sorting. M6 composition cannot use that implementation until it has an exact
code-owned registry value. Calling it a general 2D or mixed renderer merely
because it uses raylib `Camera2D` would confuse an API type with proved
presentation behavior.

## Decision

Register `isoworld_raylib_2_5d@0.1.0` for Linux x86_64 and presentation mode
`2_5d` only. Its 22 sorted capabilities were derived from and verified against
the exact union of the 19 required features in the checked-in foundation
worldpack and the three requirements of `profile_2_5d`: worldpack v1-v5
content, renderpack v1 content, and 2.5D world presentation. `locales` and
`personal_campaigns` remain absent because the verification worldpack does not
require them. The declaration is not bound to that world's hash.

The adapter delegates to the existing `GameApp`, `IsometricRenderer`, and
tick-based renderpack clips. Registry resolution returns a frozen opaque value
without importing pyray or executing preflight. Explicit `preflight()` accepts
one already-loaded worldpack/renderpack pair, requires matching world identity
and coverage of every required runtime feature, enforces five assets, three
bindings, and 1 MiB of hash-verified resource bytes, and rejects every host
except Linux x86_64. A different world can therefore pass when its
requirements are a subset of the declaration and its renderpack identity
matches. `create_app()` then constructs the established `GameApp`; it does not
duplicate rendering or simulation.

The component declaration is:

- engine `isoworld_game_app@0.6.0`;
- renderer `isoworld_isometric_renderer@0.6.0`;
- animation `isoworld_tick_clips@0.6.0`; and
- physics and packager `not_provided@0.0.0`.

The declaration's 1024 draw-call and 1000 ms values are neutral smoke ceilings,
not representative benchmark results. `max_triangles: 1` is only the positive
schema floor for a non-3D adapter and proves no triangle, mesh, GLB, collision,
or 3D capability.

Ubuntu x86_64 CI routes the existing hidden-window raylib smoke through the
exact registry key, preflight, and app-construction seam without a skip path.
Running the same smoke locally on aarch64 is not verification for the declared
platform.

## Consequences

- The foundation renderpack verifies that `profile_2_5d` can resolve the exact
  static registration; it is not the only accepted world hash.
- The neutral declared adapter fixture remains unchanged and incompatible.
- No 2D, mixed, 3D, assetpack, collision, UI, audio, packaging, Windows,
  representative performance, or M6 release-readiness claim follows.
- `isoworld` remains stdlib-only; pyray is still imported only when
  `GameApp.run()` crosses the native presentation boundary.

## Rejected alternatives

### Declare both 2D and 2.5D because the renderer uses Camera2D

Rejected because camera API selection does not prove orthographic 2D
presentation. The implemented projection, elevation, and depth plan prove the
single 2.5D profile.

### Put a Python module locator in the adapter document

Rejected because contract data is a requirement declaration. Executable
selection remains an exact code-owned registry lookup.

### Treat the legacy game package as verified

Rejected because this slice has no packager component or standalone package
evidence and verifies only the hosted Linux x86_64 presentation seam.
