from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from worldforge.project import SourceProject, load_source_project


@dataclass(frozen=True, slots=True)
class NarrativeFinding:
    severity: str
    code: str
    path: str
    message: str


def _effects(project: SourceProject) -> Iterable[tuple[str, dict[str, Any]]]:
    for collection in ("abilities", "interactions"):
        for item in project.collections.get(collection, []):
            for position, effect in enumerate(item.get("effects", [])):
                if isinstance(effect, dict):
                    yield f"{collection}/{item.get('id')}/effects/{position}", effect
    for dialogue in project.collections.get("dialogues", []):
        for node in dialogue.get("nodes", []):
            for field in ("on_enter",):
                for position, effect in enumerate(node.get(field, [])):
                    if isinstance(effect, dict):
                        yield (
                            f"dialogues/{dialogue.get('id')}/nodes/{node.get('id')}/"
                            f"{field}/{position}",
                            effect,
                        )
            for choice in node.get("choices", []):
                for position, effect in enumerate(choice.get("effects", [])):
                    if isinstance(effect, dict):
                        yield (
                            f"dialogues/{dialogue.get('id')}/nodes/{node.get('id')}/"
                            f"choices/{choice.get('id')}/effects/{position}",
                            effect,
                        )
    for quest in project.collections.get("quests", []):
        for stage in quest.get("stages", []):
            for field in ("on_complete", "on_fail"):
                for position, effect in enumerate(stage.get(field, [])):
                    if isinstance(effect, dict):
                        yield (
                            f"quests/{quest.get('id')}/stages/{stage.get('id')}/{field}/{position}",
                            effect,
                        )
    for scene in project.collections.get("scenes", []):
        for position, effect in enumerate(scene.get("effects", [])):
            if isinstance(effect, dict):
                yield f"scenes/{scene.get('id')}/effects/{position}", effect
    for consequence in project.collections.get("consequences", []):
        for position, effect in enumerate(consequence.get("effects", [])):
            if isinstance(effect, dict):
                yield f"consequences/{consequence.get('id')}/effects/{position}", effect


def _conditions(project: SourceProject) -> Iterable[tuple[str, dict[str, Any]]]:
    for dialogue in project.collections.get("dialogues", []):
        for position, condition in enumerate(dialogue.get("conditions", [])):
            if isinstance(condition, dict):
                yield f"dialogues/{dialogue.get('id')}/conditions/{position}", condition
        for node in dialogue.get("nodes", []):
            for choice in node.get("choices", []):
                for position, condition in enumerate(choice.get("conditions", [])):
                    if isinstance(condition, dict):
                        yield (
                            f"dialogues/{dialogue.get('id')}/nodes/{node.get('id')}/"
                            f"choices/{choice.get('id')}/conditions/{position}",
                            condition,
                        )
    for quest in project.collections.get("quests", []):
        for position, condition in enumerate(quest.get("auto_start_conditions", [])):
            if isinstance(condition, dict):
                yield f"quests/{quest.get('id')}/auto_start_conditions/{position}", condition
        for stage in quest.get("stages", []):
            for field in ("completion_conditions", "failure_conditions"):
                for position, condition in enumerate(stage.get(field, [])):
                    if isinstance(condition, dict):
                        yield (
                            f"quests/{quest.get('id')}/stages/{stage.get('id')}/{field}/{position}",
                            condition,
                        )
    for scene in project.collections.get("scenes", []):
        for position, condition in enumerate(scene.get("conditions", [])):
            if isinstance(condition, dict):
                yield f"scenes/{scene.get('id')}/conditions/{position}", condition
    for collection in ("goals", "consequences"):
        for item in project.collections.get(collection, []):
            for position, condition in enumerate(item.get("conditions", [])):
                if isinstance(condition, dict):
                    yield f"{collection}/{item.get('id')}/conditions/{position}", condition


def _reachable(start: str, edges: dict[str, set[str]]) -> set[str]:
    result: set[str] = set()
    pending = [start]
    while pending:
        node = pending.pop()
        if node in result:
            continue
        result.add(node)
        pending.extend(sorted(edges.get(node, set()) - result, reverse=True))
    return result


def analyze_project(project: SourceProject) -> dict[str, Any]:
    findings: list[NarrativeFinding] = []
    effects = list(_effects(project))
    conditions = list(_conditions(project))
    produced_flags = {
        effect.get("flag")
        for _, effect in effects
        if effect.get("kind") == "set_flag" and isinstance(effect.get("flag"), str)
    }
    produced_facts = {
        effect.get("fact_id")
        for _, effect in effects
        if effect.get("kind") == "learn_fact" and isinstance(effect.get("fact_id"), str)
    }
    initial_facts: set[str] = set()
    for actor in project.collections.get("actors", []):
        knowledge = actor.get("knowledge", {})
        if isinstance(knowledge, dict):
            for group in ("knows", "suspects", "secrets"):
                values = knowledge.get(group, [])
                if isinstance(values, list):
                    initial_facts.update(value for value in values if isinstance(value, str))

    for path, condition in conditions:
        if (
            condition.get("kind") == "flag_set"
            and condition.get("flag") not in produced_flags
            and not condition.get("negate", False)
        ):
            findings.append(
                NarrativeFinding(
                    "warning",
                    "missing_flag_producer",
                    path,
                    f"No authored effect sets flag {condition.get('flag')!r}.",
                )
            )
        if (
            condition.get("kind") == "fact_status"
            and condition.get("knowledge_status") != "unknown"
            and condition.get("fact_id") not in initial_facts | produced_facts
            and not condition.get("negate", False)
        ):
            findings.append(
                NarrativeFinding(
                    "warning",
                    "missing_fact_producer",
                    path,
                    f"Fact {condition.get('fact_id')!r} is neither initially held nor learnable.",
                )
            )

    unreachable_nodes = 0
    for dialogue in project.collections.get("dialogues", []):
        dialogue_id = dialogue.get("id")
        nodes = {
            node.get("id"): node
            for node in dialogue.get("nodes", [])
            if isinstance(node, dict) and isinstance(node.get("id"), str)
        }
        edges = {
            node_id: {
                choice.get("next_node_id")
                for choice in node.get("choices", [])
                if isinstance(choice, dict) and isinstance(choice.get("next_node_id"), str)
            }
            for node_id, node in nodes.items()
        }
        reachable = _reachable(str(dialogue.get("start_node_id")), edges)
        for node_id in sorted(set(nodes) - reachable):
            unreachable_nodes += 1
            findings.append(
                NarrativeFinding(
                    "warning",
                    "unreachable_dialogue_node",
                    f"dialogues/{dialogue_id}/nodes/{node_id}",
                    "Node cannot be reached from start_node_id.",
                )
            )
        for node_id, node in nodes.items():
            choices = [item for item in node.get("choices", []) if isinstance(item, dict)]
            if node.get("allow_exit", True) is False and not choices:
                findings.append(
                    NarrativeFinding(
                        "error",
                        "dialogue_hard_softlock",
                        f"dialogues/{dialogue_id}/nodes/{node_id}",
                        "The node forbids exit and has no choices.",
                    )
                )
            elif node.get("allow_exit", True) is False and all(
                choice.get("conditions") for choice in choices
            ):
                findings.append(
                    NarrativeFinding(
                        "warning",
                        "dialogue_conditional_softlock",
                        f"dialogues/{dialogue_id}/nodes/{node_id}",
                        "Every exit is conditional and manual exit is disabled.",
                    )
                )
            speaker_id = node.get("speaker_id")
            speaker = next(
                (
                    actor
                    for actor in project.collections.get("actors", [])
                    if actor.get("id") == speaker_id
                ),
                {},
            )
            knowledge = speaker.get("knowledge", {}) if isinstance(speaker, dict) else {}
            known = set()
            forbidden = set()
            if isinstance(knowledge, dict):
                for group in ("knows", "suspects", "secrets"):
                    values = knowledge.get(group, [])
                    if isinstance(values, list):
                        known.update(values)
                values = knowledge.get("forbidden", [])
                if isinstance(values, list):
                    forbidden.update(values)
            for fact_id in node.get("fact_refs", []):
                fact_path = f"dialogues/{dialogue_id}/nodes/{node_id}/fact_refs"
                if fact_id in forbidden:
                    findings.append(
                        NarrativeFinding(
                            "error",
                            "forbidden_knowledge_leak",
                            fact_path,
                            f"Speaker {speaker_id!r} is forbidden from knowing {fact_id!r}.",
                        )
                    )
                elif fact_id not in known and fact_id not in produced_facts:
                    findings.append(
                        NarrativeFinding(
                            "warning",
                            "unavailable_speaker_fact",
                            fact_path,
                            f"Speaker {speaker_id!r} cannot initially access {fact_id!r}.",
                        )
                    )

    unreachable_stages = 0
    for quest in project.collections.get("quests", []):
        quest_id = quest.get("id")
        stages = {
            stage.get("id"): stage
            for stage in quest.get("stages", [])
            if isinstance(stage, dict) and isinstance(stage.get("id"), str)
        }
        edges = {
            stage_id: {stage["next_stage_id"]}
            if isinstance(stage.get("next_stage_id"), str)
            else set()
            for stage_id, stage in stages.items()
        }
        reachable = _reachable(str(quest.get("start_stage_id")), edges)
        for stage_id in sorted(set(stages) - reachable):
            unreachable_stages += 1
            findings.append(
                NarrativeFinding(
                    "warning",
                    "unreachable_quest_stage",
                    f"quests/{quest_id}/stages/{stage_id}",
                    "Stage cannot be reached from start_stage_id.",
                )
            )
        terminal = [
            stage_id for stage_id, targets in edges.items() if not targets and stage_id in reachable
        ]
        if not terminal:
            findings.append(
                NarrativeFinding(
                    "error",
                    "quest_has_no_terminal_stage",
                    f"quests/{quest_id}/stages",
                    "Quest has no reachable terminal stage.",
                )
            )

    consequences = {
        item.get("id"): item
        for item in project.collections.get("consequences", [])
        if isinstance(item.get("id"), str)
    }
    consequence_edges = {
        consequence_id: {
            item_id
            for item_id, item in consequences.items()
            if item.get("trigger_event") == "consequence_resolved"
            and item.get("subject_id") == consequence_id
        }
        for consequence_id in consequences
    }
    for consequence_id, item in consequences.items():
        if item.get("once", True) is not False:
            continue
        if any(
            consequence_id in _reachable(target, consequence_edges)
            for target in consequence_edges[consequence_id]
        ):
            findings.append(
                NarrativeFinding(
                    "warning",
                    "repeating_consequence_cycle",
                    f"consequences/{consequence_id}",
                    "A repeatable delayed consequence participates in a reaction cycle.",
                )
            )

    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] += 1
    return {
        "format": "rpg-world-forge.narrative_analysis",
        "format_version": 1,
        "world_id": project.world.get("id"),
        "summary": {
            "dialogues": len(project.collections.get("dialogues", [])),
            "quests": len(project.collections.get("quests", [])),
            "scenes": len(project.collections.get("scenes", [])),
            "consequences": len(project.collections.get("consequences", [])),
            "unreachable_dialogue_nodes": unreachable_nodes,
            "unreachable_quest_stages": unreachable_stages,
            **counts,
        },
        "findings": [asdict(finding) for finding in findings],
    }


def analyze_manifest(manifest_path: str | Path) -> dict[str, Any]:
    return analyze_project(load_source_project(manifest_path))


def write_analysis(path: str | Path, report: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
