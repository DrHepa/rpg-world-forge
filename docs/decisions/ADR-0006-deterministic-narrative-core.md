# ADR 0006: Typed deterministic narrative core

- Status: accepted
- Date: 2026-07-19

## Context

M1 could compile narrative collections but did not execute them. M2 needs
knowledge boundaries, dialogue, quests, scenes, relationships, and reputation
without introducing runtime scripts, expression evaluation, or model inference.

## Decision

- Emit worldpack format 3 and load formats 1, 2, and 3.
- Represent conditions and effects as small, validated allowlists.
- Store knowledge per actor; `forbidden` facts cannot be learned or spoken.
- Treat relationships as directed actor/dimension values and reputation as
  actor/faction values, both clamped to `-100..100`.
- Process domain events, quest transitions, and scenes in deterministic stable
  order with a bounded fixed point.
- Pause simulation while a dialogue or scene overlay is active.
- Persist all M2 state in save format 2 and all M2 actions in replay format 2.
- Analyze graph reachability and likely softlocks offline before compilation.

## Consequences

Narrative behavior is reproducible, inspectable, localizable, and testable. It
cannot execute arbitrary code or improvise content at runtime. Static analysis
is conservative: warnings identify risks for author review, while the runtime
still rechecks every condition and knowledge boundary.
