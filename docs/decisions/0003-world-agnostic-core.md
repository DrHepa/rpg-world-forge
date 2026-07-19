# ADR-0003: World-agnostic core and independent game projects

- Status: accepted
- Date: 2026-07-19

Repository terminology is refined by ADR-0009. The independent content project
described here is now called a **world-authoring repository**; it is distinct
from the game repository that imports an immutable runtime bundle.

## Decision

The engine and `worldforge` do not encode a game's roster, lore, names, genre,
or content quantity. Every world lives in an independent world-authoring
repository and compiles into a self-contained worldpack.

An actor is playable or non-playable according to world data. A game may offer
one protagonist, a full ensemble, or separate campaigns. Personal stories are
referenced by actors rather than Python code.

## Consequences

- The same foundation can build different RPGs.
- Adding a world does not require forking the engine.
- Common rules are versioned as capabilities; exclusive rules are reviewed
  deterministic data modules or plugins.
- Core tests use a neutral vertical slice, never a game's canon.
- The Forge contains no canonical subdirectories for generated worlds.
- Game-specific constraints such as exact roster size or mandatory personal
  arcs are declared in `content_policy` and validated as data.
