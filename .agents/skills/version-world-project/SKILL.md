---
name: version-world-project
description: Apply one reviewed SemVer transition to an existing v2 or v3 world project with optimistic locking. Use only for world versioning, not cloning, release export, or games.
---

# Version a world project

> **Retained legacy specialization.** This skill belongs to the published
> worldpack/M5/isoworld/pyray lane named by its existing inputs. It is not a
> generic creation/gamepack workflow and must not be used to infer generic
> runtime support. Use the bounded generic skills for `world-forge.*` projects.

1. Resolve and inspect the explicit external world root.
2. Record current version, expected version, reason, approver, and bump part.
3. Run `worldforge bump-world-version` with the expected current version.
4. Verify the new version log and invalidated prior release metadata.
5. Rerun the validation required by the next authoring/release phase.

Do not edit project/version JSON manually. Stop on optimistic-lock mismatch,
invalid SemVer, wrong project kind, symlinks, or uncommitted identity ambiguity.
