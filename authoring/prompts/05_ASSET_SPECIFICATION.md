# Session: asset specification

Receive the worldpack, approved bibles, and one inventory item. Return a
provider-agnostic specification.

Include:

- ID, type, and gameplay/narrative purpose.
- Minimum canonical context and applicable knowledge boundaries.
- Dimensions, format, palette, pivot, alpha, and memory budget.
- Animation: actions, directions, frames, FPS, and layout.
- Audio: event, variations, duration, loop, loudness, and sample rate.
- Authorized references and elements that must not be imitated.
- Verifiable acceptance criteria and in-engine test cases.
- Required variants and details that must remain identical across them.
- Expected processed outputs and semantic runtime slots. Spritesheets and
  tilesets require a texture plus deterministic clipset.
- Permitted generation route. A local-model route must identify a Modly
  extension and version; OpenAI covers GPT/Codex/GPT Image.

The result can later be adapted to GPT Image, a local model, a procedural tool,
or an artist without changing the asset's identity.
