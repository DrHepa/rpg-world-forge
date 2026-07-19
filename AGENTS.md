# AGENTS.md

## Mission

Maintain RPG World Forge as a public, world-agnostic toolkit for creating
complete narrative game projects and preparing them for deterministic
Python/raylib implementation.

GPT is the default lead agent. It may execute every phase itself or delegate
bounded tasks, but it remains responsible for integration, validation and final
handoff quality.

## Hard boundaries

- Never add the canon, cast, lore or assets of a generated world to this toolkit.
- Never add LLM/model inference, provider SDKs, credentials or prompt execution
  to `src/isoworld`.
- AI and generative tools are authoring-time only.
- Generated world-authoring and game repositories must live outside the Forge
  and remain separate from each other.
- Game repositories contain no `AGENTS.md`, `.agents/`, `.worldforge/`,
  authoring sources, prompts, phase reports, provider tooling, or mutable world
  checkout.
- Runtime consumes only validated worldpacks and processed assets.
- Do not copy third-party code or assets without recording compatible licenses.
- Do not treat a model repository license as the license for weights, datasets
  or generated output.

## Required work order

1. Read this file, `README.md`, `docs/ARCHITECTURE.md` and the relevant ADRs.
2. Inspect the existing implementation and tests before changing contracts.
3. Keep source schemas, compiler, CLI, documentation and tests synchronized.
4. Prefer deterministic validation over prose-only requirements.
5. Run the full headless suite and runtime AI-import audit before handoff.

## Repository ownership

- `src/isoworld/`: tested reference runtime and snapshot source. It must not
  import `worldforge`.
- `src/worldforge/`: offline authoring/build/QA tools.
- `.agents/skills/`: Forge-only reusable workflows; never copied to a world or
  game repository.
- `agents/`: reusable agent protocol and role cards.
- `authoring/prompts/`: provider-agnostic authoring prompts.
- `schemas/`: public data contracts.
- `examples/`: neutral fixtures only.
- `docs/decisions/`: architectural decisions.

## Change requirements

- Runtime behavior change: add or update headless tests.
- Source/worldpack contract change: update validator, schema, docs and fixture.
- World-repository layout change: update world scaffold and workflow tests.
- Game-template layout change: run the clean-game boundary audit and update its
  materialization tests.
- Agent workflow change: keep phase definitions, world-repository `AGENTS.md`
  and CLI transitions aligned.
- Asset pipeline change: test draft and release failure modes.

## Multi-agent protocol

The lead GPT may delegate only bounded tasks with explicit inputs, outputs,
owned paths and dependencies. Subagents must not modify canonical files outside
their claim. They return evidence and proposed patches to the lead. The lead
resolves conflicts, runs validation and records decisions.

Use the protocol in `agents/ORCHESTRATION.md`. A generated world-authoring
repository receives its own `AGENTS.md` and `.worldforge/` control directory.
An independent game repository receives neither; Forge-side agents operate on
it externally through explicit template, bundle, and compatibility contracts.

## Verification

```bash
ruff check src tests
ruff format --check src tests
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m worldforge validate \
  examples/foundation/source/manifest.json --profile release
PYTHONPATH=src python -m worldforge analyze-narrative \
  examples/foundation/source/manifest.json --fail-on warning
PYTHONPATH=src python -m worldforge audit-runtime src/isoworld
```
