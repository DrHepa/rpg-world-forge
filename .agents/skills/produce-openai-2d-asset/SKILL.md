---
name: produce-openai-2d-asset
description: Produce candidates for one approved 2D or 2.5D asset specification through OpenAI Image under GPT orchestration. Use for candidate generation only, not selection, processing, QA, or 3D geometry.
---

# Produce an OpenAI 2D asset

## Scope

Delegate one bounded 2D/2.5D candidate-generation operation to `openai_image`.

## Inputs

- Explicit `FORGE_ROOT` and external `WORLD_ROOT`.
- Exact target, visual bible, inventory, and approved asset-specification hashes.
- Authorized reference files and permissions named by the specification.
- One strict production request with orchestrator `gpt`, route `openai`, executor
  `openai_image`, operation `image_generate` or `image_edit`, parameters,
  expected outputs, and parent receipt hashes.

## Outputs

- Candidate files in the world authoring area, never a runtime tree.
- One sanitized typed production receipt with successful status, exact OpenAI
  surface/requested/resolved model policy, replayability, candidate hashes, and
  parent receipt hashes.

## Invariants

- Generate only the requested asset/variants and preserve required invariants.
- Do not imitate forbidden elements or use unrecorded references.
- Keep raw provider payloads, signed URLs, credentials, and chat/MCP transcripts
  out of receipts, manifests, bundles, and games.
- GPT evaluates contract compliance; the executor never approves its own output.

## Completion

Complete only when a successful receipt validates and every returned candidate
is hash-addressed. A failed provider attempt produces no production receipt;
record the failure outside the successful lineage and stop or issue a newly
approved request. Do not select, crop, atlas, normalize, license, approve, or
package a candidate in this skill.
