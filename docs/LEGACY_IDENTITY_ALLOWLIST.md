# Legacy identity allowlist

World Forge intentionally retains published and historical identity strings.
`contracts/legacy-identity-allowlist.json` is an exact generated inventory of
their file, occurrence, classification, context, and source hash. It is not a
glob, substring waiver, or permission to add new old-brand copy.

Allowed classifications are limited to compatibility concerns such as:

- published legacy contracts and schema IDs;
- compatibility readers and migration code;
- regression fixtures/tests;
- historical audits and provenance;
- licenses/notices and retained package/data-path bridges.

New visible product copy must use World Forge. New generic format names must use
the `world-forge.*` namespace. Published `rpg-world-forge.*`, `isoworld.*`,
`.worldforge/`, legacy project discriminators, protocol/storage identities, and
immutable historical evidence remain unchanged when their compatibility
meaning requires them.

## Review procedure

1. Run `PYTHONPATH=src python -m worldforge audit-identities --source-root .`.
2. Inspect every added, removed, moved, or context-changed occurrence.
3. Remove accidental new legacy branding; keep only required compatibility or
   history.
4. Run the canonical generator (write mode is the default):
   `PYTHONPATH=src python -m scripts.generate_identity_allowlist`.
5. Review the generated diff, rerun the audit, and run identity tests.

Never hand-edit hashes, offsets, counts, or rows. A changed allowlisted source
file must be rebound by the generator even when its retained identity text did
not change.
