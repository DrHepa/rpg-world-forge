# Session: asset inventory and specification

Receive a locked worldpack, one asset target, and approved visual/audio bibles.
GPT first derives the complete target inventory, then writes one
provider-neutral specification per requirement. Do not generate candidates in
this session.

## Inventory

Return a strict `rpg-world-forge.asset_inventory` bound to the exact world,
target, and bible hashes. Derive requirements from actors, biomes, objects,
actions, scenes, events, construction blueprints, UI, audio events, and semantic
runtime slots. Each requirement declares ID, kind, representation, whether it is
required, purpose, canonical sources, and semantic slots. Put non-canonical
creative additions in `manual_additions`; never disguise them as derived canon.

The inventory may mix 2D references, textures, models, rigs, animations, audio,
fonts, shaders, and collisions when the target needs them, but each item must be
usable by the declared target and delivery profile.

## Per-asset specification

Return strict `rpg-world-forge.asset_spec` v2 documents derived from the reviewed
inventory and bound to the exact target, inventory, visual-bible, and
audio-bible hashes. Each specification's ID, kind, representation, canonical
sources, and semantic slots must match its inventory requirement. Every
specification includes:

- Stable ID, kind, representation (`2d`, `2_5d`, `3d`, or `audio`), target ID/
  hash, inventory hash, both bible hashes, purpose, canonical sources, and
  verifiable acceptance criteria.
- Exact target-dependent technical contract. For 2D/2.5D use the applicable
  `width`, `height`, `runtime_format`, `palette`, `alpha_mode`, `pivot`,
  `cell_layout`/`frame_layout`, `directions`, `actions`, `frames`, `fps`,
  `padding`, and `memory_budget_bytes` fields. For audio use the applicable
  `event`, `variants`, `duration_seconds_max`, `loop`, `integrated_lufs`,
  `sample_rate`, `channels`, `priority`, `max_distance`, `cooldown_seconds`,
  and memory-budget fields.
- Fonts/shaders use their binary format and memory budget without invented image
  width, height, or alpha fields.
- For 3D: GLB delivery, physical dimensions, memory, triangle/vertex/material/
  texture budgets, and applicable LOD, collider, bone/influence, and animation
  requirements. The target supplies right-handed `Y`-up/`-Z`-forward coordinates.
- Semantic slots, portable acceptance-check IDs, and exact expected outputs as
  `{role, media_type}` pairs. M5 final visual/audio processing emits canonical
  PNG and PCM WAV; intermediate candidates may use other declared image/audio
  media types.
- `production.allowed_routes` and `production.allowed_executors`, kept separate.
  OpenAI Image, Blender MCP, `modly-cli-mcp`, human, and procedural work consume
  the same specification.

Authorized references and permissions remain hash-bound authoring evidence and
become role-tagged request inputs; do not embed a prompt, provider payload, MCP
configuration, source file, or local-model assumption in the specification. A
local route must allow `modly_cli_mcp`; the exact capability discovery,
extension/model/workflow identity belongs to its production receipt.
