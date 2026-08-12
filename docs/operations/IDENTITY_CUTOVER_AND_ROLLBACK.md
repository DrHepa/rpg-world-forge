# World Forge identity cutover and rollback

This runbook is preparatory. Do not execute remote rename, registry
publication, app-ID migration, or package publication without one explicit
owner approval after exact hosted CI is green.

## Preflight

1. Freeze the exact reviewed commit and require a clean worktree.
2. Verify the target GitHub, PyPI, and npm names and ownership immediately
   before cutover.
3. Run contract and legacy-identity audits; require the generated allowlist to
   be exact.
4. Build reproducible wheel/sdist and Studio packages; inspect metadata,
   inventories, entry points, licenses, and dual installed-data lookup.
5. Test canonical and deprecated environment aliases; conflicting values must
   fail closed.
6. Test old projects/games without mutation and run explicit v2-to-v3 migration
   fixtures separately.
7. Require hosted Linux and Windows evidence on the exact reviewed commit.

## Authorized sequence

1. Create reviewed conventional commits without attribution trailers.
2. Push once and wait for every mandatory job to finish green.
3. Publish no package until ownership, normalized name, and artifact inventory
   are rechecked.
4. Rename `DrHepa/rpg-world-forge` to `DrHepa/world-forge` only as the final
   authorized public repository cutover.
5. Update local `origin` to the verified new URL.
6. Verify the old URL redirect, clone, Actions, branch protection, secrets,
   environments, security settings, releases, and badges.
7. Rerun critical hosted contract, package, migration, standalone-game, and
   native smoke gates under the new repository identity.

The Python imports and CLI remain `worldforge`; `isoworld`, published formats,
schema IDs, `.worldforge/`, and historical records remain readable.

## Rollback boundaries

- Before GitHub rename: do not push a failing repair; restore only through a
  reviewed conventional revert, never history rewriting.
- After GitHub rename: GitHub repository-name rollback is an owner operation;
  preserve evidence and verify both URLs/remotes again.
- Package publication is immutable. A bad release is followed by a corrected
  version or documented yank/deprecation according to the registry; do not
  overwrite artifacts.
- World project migration follows
  `docs/MIGRATING_WORLD_PROJECT_V2_TO_V3.md`; ambiguous native publication uses
  commit-forward evidence, not guessed rollback.
- Studio app ID/user-data migration is excluded from this cutover until a
  separate native bridge proves divergent-root rejection, backup, retry, and
  rollback.

## Stop conditions

Stop on a name collision, remote movement, dirty tree, allowlist drift,
non-reproducible build, missing native row, red/cancelled/running CI, divergent
Studio roots, or ambiguous migration evidence. Report the exact boundary; do
not rebase, merge, force-push, or rename automatically.
