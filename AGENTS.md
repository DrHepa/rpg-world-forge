# AGENTS.md

## Mission

Maintain RPG World Forge as a public, world-agnostic toolkit for creating
complete narrative game projects and preparing them for deterministic
Python/raylib implementation.

GPT is the default lead agent. It may execute every phase itself or delegate
bounded tasks, but it remains responsible for integration, validation and final
handoff quality.

## Hard boundaries

- Never add the canon, cast, lore or assets of a generated game to this toolkit.
- Never add LLM/model inference, provider SDKs, credentials or prompt execution
  to `src/isoworld`.
- AI and generative tools are authoring-time only.
- Generated games must live in independent directories/repositories.
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

- `src/isoworld/`: game runtime. It must not import `worldforge`.
- `src/worldforge/`: offline authoring/build/QA tools.
- `agents/`: reusable agent protocol and role cards.
- `authoring/prompts/`: provider-agnostic authoring prompts.
- `schemas/`: public data contracts.
- `examples/`: neutral fixtures only.
- `docs/decisions/`: architectural decisions.

## Change requirements

- Runtime behavior change: add or update headless tests.
- Source/worldpack contract change: update validator, schema, docs and fixture.
- Generated-project layout change: update scaffold tests.
- Agent workflow change: keep phase definitions, generated `AGENTS.md` and CLI
  transitions aligned.
- Asset pipeline change: test draft and release failure modes.

## Multi-agent protocol

The lead GPT may delegate only bounded tasks with explicit inputs, outputs,
owned paths and dependencies. Subagents must not modify canonical files outside
their claim. They return evidence and proposed patches to the lead. The lead
resolves conflicts, runs validation and records decisions.

Use the protocol in `agents/ORCHESTRATION.md`. A generated game receives its own
custom `AGENTS.md` and `.worldforge/` control directory.

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
