# Security policy

## Supported versions

The latest `main` branch is supported during the alpha phase.

## Reporting

Do not open a public issue for credential exposure, path traversal, unsafe asset
processing or arbitrary code execution. Report the problem privately to the
repository owner through GitHub's security reporting channel when enabled.

Worldpacks and asset manifests are untrusted inputs. Validators must reject
paths outside their project root. Generated projects must never commit API keys,
model-service credentials or private reference material.
