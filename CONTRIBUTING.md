# Contributing

RPG World Forge is public and accepts fixes, validators, schemas, authoring
workflows, deterministic runtime systems and neutral examples.

## Before starting

1. Read `AGENTS.md` and the relevant ADRs.
2. Open or reference a focused issue.
3. State the paths and contracts the change will affect.
4. Keep game-specific canon and assets in the generated game's repository.

## Pull requests

- One coherent concern per PR.
- Explain user-visible behavior and migration impact.
- Add tests for every contract or behavior change.
- Update schemas and docs together with code.
- Record third-party licenses and provenance.
- Do not introduce runtime model inference or provider SDKs.

Run:

```bash
python -m pip install -e ".[dev]"
ruff check src tests
ruff format --check src tests
python -m unittest discover -s tests -v
python -m worldforge validate examples/foundation/source/manifest.json --profile release
python -m worldforge audit-runtime src/isoworld
```

## Agent-authored changes

Identify the principal agent and any delegated roles in the PR. Include owned
paths, validations and unresolved risks. The principal agent remains responsible
for the integrated result.
