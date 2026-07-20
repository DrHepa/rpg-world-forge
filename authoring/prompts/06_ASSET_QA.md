# Session: asset production QA

Compare only the selected, deterministically processed output with its exact
specification, target, bibles, production/processing lineage, license record,
and integration evidence. Return a strict release
`rpg-world-forge.asset_qa_report` with the exact asset ID and target hash. Each
check has a stable ID, `passed: true`, and one or more hash-bound evidence
references; `blockers` is empty and `approved_by` names the authorized reviewer.
Do not repair or silently replace the asset during QA.

## Universal checks

- Output paths, media signatures, sizes, hashes, budgets, semantic bindings,
  and deterministic repeatability.
- Complete request-to-receipt lineage, authorized selection, reference
  permissions, component licenses, redistribution rights, and notices.
- Consistency with target scale, visual/audio bibles, canonical context, and
  every acceptance criterion.
- Evidence files identify exact checker/tool revisions; missing or skipped
  evidence cannot produce a release QA report.

## 2D, 2.5D, and audio checks

- Perspective, scale, pivot, light, palette, silhouette, alpha, readability,
  frame layout, seams, cropping, jitter, popping, loops, and variation identity.
- Audio loudness, peaks, channels, sample rate, clicks, truncated tails,
  repetition, masking, loop boundaries, priority, and cooldown behavior.
- Successful renderpack validation, raylib load, clip bounds, representative
  scene capture, binding resolution, and resource cleanup where required.

## 3D checks

- GLB signature and parse, embedded resources with zero external URIs, declared
  coordinate system, dimensions, transforms, node names, materials, textures,
  LODs, collider, mesh metrics, and all numeric budgets.
- Rig hierarchy, skin weights/influence limits, deformation, root motion,
  animation names/ranges/loops, and representative pose/action captures.
- Neutral GLB inspection independent of Blender, Modly, any MCP server, and the
  eventual game engine. `.blend` is authoring evidence, never a runtime output.

Record failed checks, blockers, and required fixes in correction evidence and
return the asset to its owning phase. Emit the canonical release QA report only
after every automated/manual/integration check passes and the blocker list is
empty. Final approval belongs to a person or the project's explicitly authorized
lead reviewer, never an executor.
