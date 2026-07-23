# Neutral M6 composition contracts

These fixtures define the contract surface only. They select no engine,
renderer, physics implementation, provider, model, executable, module, or MCP
endpoint. The adapter is deliberately `declared`, never `verified`.

The sample composition binds the committed neutral foundation worldpack and a
deliberately unavailable renderpack hash. Its compatibility report is therefore
incompatible for both adapter state and pack integrity. It is not M6 runtime,
native, packaging, collision, animation, or release-readiness evidence.

The six profile documents classify world presentation. UI and audio remain
orthogonal optional planes and are not silently implied by a world profile.

`adapters/isoworld_raylib_2_5d.json` is the exact code-owned declaration for
the existing isometric reference runtime. Its 22 capabilities were derived
from and verified against the checked-in foundation worldpack plus
`profile_2_5d` on Linux x86_64, but the adapter is not bound to that world
hash. Another world may pass when its requirements are covered and its
renderpack identity matches. The bounded preflight and hosted graphical smoke
do not claim 2D, mixed/3D presentation, assetpack, collision, UI/audio,
packaging, Windows, representative performance, or M6 release readiness. The
required one-triangle budget is only the schema floor for a non-3D adapter.

`adapters/pyray_3d_v1.json` is separate bounded execution evidence. It proves
only one Linux x86_64 path-based GLB animation lifecycle and declares only
`animation_gltf`. It intentionally omits collision, assetpack consumption,
3D/mixed world presentation, packaging, and performance capabilities. Because
every current 3D/mixed profile requires `collision_gltf`, this verified
declaration cannot make any current 3D composition compatible.
