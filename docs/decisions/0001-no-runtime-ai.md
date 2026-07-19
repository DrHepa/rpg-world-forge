# ADR-0001: AI is authoring-time only

- Status: accepted
- Date: 2026-07-19

## Decision

The game contains no models, agents, inference, provider SDKs, or AI-service
calls. AI may participate in external design sessions, but proposals become
reviewed and compiled sources before reaching runtime.

## Consequences

- The experience works offline and behaves reproducibly.
- Content supports testing, localization, and editorial control.
- There is no token cost, network latency, prompt injection, or provider-driven
  tone drift during play.
- Variety comes from data, composable rules, and authored content rather than
  live improvisation.
- `worldforge audit-runtime` and tests block AI imports in `isoworld`.
