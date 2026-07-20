---
name: version-world-project
description: Apply one reviewed SemVer transition to an existing v2 world project with optimistic locking. Use only for world versioning, not cloning, release export, or games.
---

# Version a world project

1. Resolve and inspect the explicit external world root.
2. Record current version, expected version, reason, approver, and bump part.
3. Run `worldforge bump-world-version` with the expected current version.
4. Verify the new version log and invalidated prior release metadata.
5. Rerun the validation required by the next authoring/release phase.

Do not edit project/version JSON manually. Stop on optimistic-lock mismatch,
invalid SemVer, wrong project kind, symlinks, or uncommitted identity ambiguity.
