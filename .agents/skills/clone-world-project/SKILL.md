---
name: clone-world-project
description: Clone canonical authoring inputs into one new world identity and lineage. Use only for the safe world-clone phase, not creation from scratch, version bumps, releases, or games.
---

# Clone a world project

1. Resolve explicit source and new target world roots outside the Forge.
2. Inspect the source as a valid v2 `project_kind: world` project.
3. Run `worldforge clone-world` with a new ID, title, and initial SemVer.
4. Verify copied canonical allowlists, exclusions, new lineage, and reset workflow.

Preserve source canon and authoring asset inputs. Never copy `.git`, credentials,
claims, reports, generated candidates, build outputs, or hash-bound manifests.
Reject symlinks and partial targets.
