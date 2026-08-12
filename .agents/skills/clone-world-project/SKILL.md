---
name: clone-world-project
description: Clone canonical authoring inputs into one new world identity and lineage. Use only for the safe world-clone phase, not creation from scratch, version bumps, releases, or games.
---

# Clone a world project

> **Retained legacy specialization.** This skill belongs to the published
> worldpack/M5/isoworld/pyray lane named by its existing inputs. It is not a
> generic creation/gamepack workflow and must not be used to infer generic
> runtime support. Use the bounded generic skills for `world-forge.*` projects.

1. Resolve explicit source and new target world roots outside the Forge.
2. Inspect the source as a valid retained v2 or v3 `project_kind: world`
   project; ordinary inspection must not migrate it.
3. Run `worldforge clone-world` with a new ID, title, and initial SemVer.
4. Verify copied canonical allowlists, exclusions, new lineage, and reset workflow.

Preserve source canon and authoring asset inputs. Never copy `.git`, credentials,
claims, reports, generated candidates, build outputs, or hash-bound manifests.
Reject symlinks and partial targets.
