# Agent system

GPT is the lead World Forge authoring agent. Role cards are conditional
perspectives that GPT may adopt sequentially; they are not mandatory separate
models and they do not make worldbuilding, narrative, actors, or asset
production universal requirements.

The reviewed `world-forge.creation_profile` is the routing authority. It keeps
gameplay, world presence, narrative, fiction, presentation, production, and
runtime target independent. A fiction genre never selects mechanics, and a
presentation mode never selects a runtime adapter.

Read, in order:

1. [ORCHESTRATION.md](ORCHESTRATION.md)
2. [WORLD_CREATION_PHASES.md](WORLD_CREATION_PHASES.md)
3. [QUALITY_GATES.md](QUALITY_GATES.md)
4. the applicable role card under `roles/`
5. [the central multi-genre architecture](../docs/MULTI_GENRE_ARCHITECTURE.md)
6. [the evidence support matrix](../docs/SUPPORT_MATRIX.md)

Focused generic perspectives:

- [Experience architect](roles/EXPERIENCE_ARCHITECT.md)
- [World architect](roles/WORLD_ARCHITECT.md)
- [Content-structure designer](roles/CONTENT_STRUCTURE_DESIGNER.md)
- [Runtime-compatibility architect](roles/RUNTIME_COMPATIBILITY_ARCHITECT.md)

These are role cards for GPT to adopt under lead and user approval. They are
not autonomous employees, worker identities, or model-selection records.

## Generic creation control plane

A generic creation project contains `project.json`, `profile.json`, a typed
source manifest, and `.worldforge/` workflow state. Phase-report v3 and the
canonical P00-P14 catalog are used for generic projects. `ready` means the
phase's declared authoring evidence passed. `not_applicable` is accepted only
when profile-aware validation proves the phase output is irrelevant.

Capture the workflow status hash before changing a bound creation or artifact
identity. After the change, `reconcile-creation` must validate and archive the
current graph and append invalidation history before status, reopen, or
completion. A stale expected hash fails closed.

Authoring validity is not runtime executability. Compilation, asset state,
adapter selection, platform execution, packaging, and release are independent
status dimensions. P13 reviews compatibility; P14 transfers a reviewed
handoff. Neither phase may invent native or hosted evidence.

`new-creation` currently scaffolds a neutral `universe_library`: an authoring
container, not an executable game. Converting an idea into a game requires an
explicit game project, typed logic, successful compilation, a complete asset
lane when applicable, compatible runtime evidence, and a reviewed handoff.

## Legacy RPG specialization

Legacy world repositories retain `.worldforge/project.json`, source canon,
worldpack phases, M5 asset production, and the `isoworld`/pyray implementation
lane. Published legacy and `isoworld.*` identities are not relabeled.
Legacy role prompts and skills are marked **Retained legacy specialization** so
they cannot be mistaken for the generic path.

Independent game repositories contain no Forge control plane. They receive
only immutable logic, sealed runtime assets, a selected runtime implementation,
game-specific code, verification scripts, and required notices.
