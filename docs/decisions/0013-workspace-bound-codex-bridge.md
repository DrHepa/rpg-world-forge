# ADR-0013: Codex is workspace-bound through a staging-only Forge bridge

- Status: accepted
- Date: 2026-07-22

## Context

Studio needs an interactive Codex surface without giving a renderer process,
model turn, or MCP client arbitrary filesystem, process, network, approval, or
changeset-apply capability. Codex app-server is an evolving local protocol, so
the exact reviewed wire contract and binary identity must be reproducible.

## Decision

Electron main owns Codex app-server 0.144.6. Stable TypeScript and JSON Schema
artifacts generated without experimental flags are vendored with deterministic
tree hashes. Development requires explicit absolute Codex and Python
executables; packages use only the closed platform runtime manifest.

Each binding resolves a registered workspace through the primary Forge
service, canonicalizes its world root, writes a private dedicated `CODEX_HOME`
config, and restarts the supervisor on workspace change. App-server runs with
strict config, approval `never`, read-only sandbox, network and web search off,
no inherited shell environment, and exactly one Forge MCP server.

The stdlib MCP server is bound by argv to the external Studio data directory
and one workspace ID. It opens the existing store in secondary mode, performs
no migrations or recovery, and exposes exactly `forge_stage_changeset`,
`forge_get_changeset`, and `forge_list_changesets`. It has no approve, apply,
reject, workspace registration, arbitrary path, process, environment, or
network operation.

The renderer receives only named account, login, binding, thread, turn,
interrupt, user-input, status, and event operations. Main injects the canonical
working directory, approval policy, and read-only/no-network turn sandbox.
App-server transport is bounded by line, pending-request, outbound-byte,
stderr, and renderer-event limits. Escalation-shaped server requests are
declined; unknown requests terminate the child fail-closed. Turn completion is
authoritative, deltas are sanitized and coalesced, crashes reject pending work,
and requests are never resent automatically.

## Consequences

- Codex may propose source edits only as reviewable staged changesets.
- Workspace switching cannot retain a child configured for the prior root.
- Account login is explicit user action; Studio never starts a model or incurs
  provider work merely by launching or binding.
- Tests use a fake local app-server and do not contact OpenAI or any provider.
- Native Python/Codex runtime packaging remains a release-pipeline input rather
  than a `PATH` fallback.
