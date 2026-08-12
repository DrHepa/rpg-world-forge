---
name: create-world-project
description: Create one new independent v3 world-authoring project from the Forge. Use only for initial world identity and workspace scaffolding, not cloning, versioning, release, or game work.
---

# Create a world project

> **Retained legacy specialization.** This skill belongs to the published
> worldpack/M5/isoworld/pyray lane named by its existing inputs. It is not a
> generic creation/gamepack workflow and must not be used to infer generic
> runtime support. Use the bounded generic skills for `world-forge.*` projects.

1. Resolve explicit `FORGE_ROOT` and a new external `WORLD_ROOT`.
2. Run `worldforge new-world` with stable ID, title, and initial SemVer.
3. Verify `project_kind: world`, identity, version, empty lineage, and workflow.
4. Run draft validation and inspect the generated authoring boundary files.

Do not create a game, bundle, release, or copied Forge skill. Reject existing
targets, symlinks, nested game/world targets, and credentials.
