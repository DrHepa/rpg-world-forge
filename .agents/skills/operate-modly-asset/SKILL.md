---
name: operate-modly-asset
description: Run one reviewed Modly extension for one approved M5 asset specification through modly-cli-mcp under GPT orchestration. Use for the optional local route only after capability research, not direct model calls, selection, refinement, or QA.
---

# Operate a Modly asset extension

## Scope

Delegate one bounded local 2D, 2.5D, or 3D production operation through
`modly-cli-mcp`. This route is enabled only after the OpenAI flow and neutral
contracts are established.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact world/target/inventory/specification hashes and authorized references.
- Reviewed evidence that one named Modly extension/revision supports the exact
  operation, input/output contract, model, weights, licenses, and platform.
- A strict request with orchestrator `gpt`, route `modly`, executor
  `modly_cli_mcp`, and a live-discovered `capability_execute`, `workflow_run`,
  or `process_run` operation. Before execution it binds the hash-addressed
  discovery snapshot, canonical `workflow_run` or `process_run` surface,
  CLI-MCP/Modly versions, capability ID, exact extension identity and hashes,
  exact model/weights identity, reviewed setup, and bounded arguments.

## Outputs

- Hash-addressed authoring candidates returned by the reviewed extension.
- Sanitized successful Modly receipt containing CLI-MCP/Modly versions,
  canonical surface, capability/run identity, supported discovery snapshot hash,
  extension version/revision/manifest/workflow hashes, model/version/weights
  hash, reviewed setup, replayability, outputs, and parent receipt hashes.

## Invariants

- GPT remains the orchestrator; `modly-cli-mcp` is the only permitted local
  model bridge and the named extension is the executor capability.
- Refuse unknown/unavailable capabilities, floating revisions, unverified
  weights, unreviewed setup, undeclared downloads, incompatible licenses,
  arbitrary code, and direct model invocation.
- Treat the `0.1.1` wrapper baseline narrowly: TripoSG and Hunyuan3D
  image-to-mesh, mesh optimizer, and default-path mesh exporter are the only
  `capability_execute` operations currently known as executable. UniRig,
  unknown/UI-only capabilities, generic chains, and explicit exporter paths are
  unavailable; 2D/2.5D requires a separately discovered reviewed extension.
- Require every receipt identity and discovery hash to match the pre-execution
  request exactly; runtime `run_id` and supported state do not authorize a
  different extension, model, workflow, or setup.
- Keep tokens, local paths outside scope, raw transcripts, configuration, models,
  weights, workflows, and extensions out of bundles and games.
- Do not select, refine, process, approve, or package the returned candidate.

## Completion

Complete when the exact extension contract was reviewed before execution and the
request/receipt plus all candidate hashes validate. Otherwise fail closed and
record the missing capability evidence.
