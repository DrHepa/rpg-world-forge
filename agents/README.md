# Agent system

GPT is the principal world-creation agent. The role cards are perspectives it
can adopt sequentially, not mandatory separate models. This is the recommended
mode because one lead preserves tone, causality and canon across phases.

Optional subagents can accelerate research, continuity checks, asset inventory
or QA. They never merge their own proposal into canon.

Read:

1. [ORCHESTRATION.md](ORCHESTRATION.md)
2. [WORLD_CREATION_PHASES.md](WORLD_CREATION_PHASES.md)
3. [QUALITY_GATES.md](QUALITY_GATES.md)
4. The relevant role card under `roles/`.

Every generated world-authoring repository also contains:

- `AGENTS.md`: world-authoring operating rules.
- `.worldforge/project.json`: identity, policy and lead agent.
- `.worldforge/status.json`: current and completed phases.
- `.worldforge/DECISIONS.md`: durable decisions and superseded choices.
- `.worldforge/TASKS.md`: backlog and active work.
- `.worldforge/claims/`: optional multi-agent file ownership.
- `.worldforge/phase_reports/`: gate evidence for completed phases.

Independent game repositories contain none of this control plane. Reusable
skills remain in the Forge, and Forge-side agents operate on game repositories
through explicit external paths and clean materialization contracts.
