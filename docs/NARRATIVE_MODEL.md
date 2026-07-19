# Deterministic narrative model

Narrative depth does not require runtime prose generation. It emerges from the
interaction of small states and readable rules.

## Narrative unit

A scene activates through declarative conditions and produces effects:

```text
conditions -> scene/dialogue -> choices -> effects -> new events
```

Conditions inspect flags, clock, location, inventory, reputation,
relationships, knowledge, and quest stages. Effects change those domains
through validated actions.

## Systems that should interact

- **Relationships**: trust, debt, and fear open different paths.
- **Factions/cultures**: personal and collective reputation may disagree.
- **Economy**: scarcity, ownership, and production alter actor goals.
- **Construction**: buildings change routes, services, scenes, and conflicts.
- **Time**: schedules, seasons, and opportunity windows.
- **Knowledge**: discovering a fact does not make it universally known.
- **Abilities**: solve problems while creating costs and consequences.
- **Quests**: state machines listening to events, not linear checklists.

## Dialogue

Dialogue is a static graph localized in the world's declared languages. Each
choice may carry conditions, costs, and effects. Lines may safely interpolate
state values, but never unresolved narrative fields or text sent to a model.

## Emergence

Emergence comes from composition. Building a bridge may shorten a route, alter
a merchant's schedule, move a market, affect faction reputation, and unlock a
night scene. Every step remains deterministic, debuggable, and testable.
