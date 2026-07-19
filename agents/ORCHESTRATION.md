# GPT orchestration protocol

## Principal-agent responsibilities

The lead GPT owns the complete result. It must:

- Maintain the distinction between proposal, approved canon and compiled data.
- Preserve user constraints and cite the decision or source behind canon.
- Detect when a new decision invalidates dependent history, dialogue or assets.
- Keep the timeline, knowledge boundaries and causal graph coherent.
- Ensure narrative features have implementable state, conditions and effects.
- Stop at phase gates, run validators and write a phase report.
- Produce the final implementation handoff without runtime AI dependencies.

## Session start

At the beginning of every session:

1. Read `AGENTS.md`.
2. Read `.worldforge/project.json` and `.worldforge/status.json`.
3. Read unresolved entries in `.worldforge/DECISIONS.md` and `.worldforge/TASKS.md`.
4. Inspect active task claims.
5. Read the current phase report template and only the canonical sources needed
   for the task.
6. State the intended deliverable and validation before editing.

## Canon states

- `proposal`: exploration; can contradict other proposals.
- `candidate`: selected direction awaiting gate review.
- `canon`: accepted and dependency-tracked.
- `deprecated`: retained for history but forbidden in new content.

Agents never silently overwrite canon. A change records reason, affected IDs,
superseded decision and required migrations.

## Delegation

The lead creates one claim per bounded task. A claim specifies:

- agent/role;
- objective and non-goals;
- exact owned paths;
- canonical inputs and knowledge boundary;
- expected output and validation;
- dependencies and blocking questions.

Two active claims must not own the same canonical path. Research and QA agents
may read broadly but write only their report path. The lead integrates results
after checking contradictions and licenses.

Run `worldforge validate-claims .` before launching delegated work and before
integration. Prefix overlap also counts as a conflict: a claim on `source/canon`
conflicts with one on `source/canon/facts.json`.

## Context discipline

Do not feed every document into every task. Use IDs and dependency lists to load
only relevant canon. For a character dialogue, include that character's facts,
relationships, current arc stage, location, timeline point and forbidden
knowledge. This reduces accidental omniscience and tonal drift.

## Completion

A phase is complete only when its report lists existing deliverables, passed
checks, resolved blockers and reviewer. Use `worldforge complete-phase`; do not
edit `status.json` to bypass a failed gate.

If an accepted change invalidates earlier canon, use `worldforge reopen-phase`
with a reason and approver. This reopens the selected phase and invalidates all
dependent phase completions, worldpack hashes and asset manifests as needed.
