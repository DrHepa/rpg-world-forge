"""Deterministic bounded analysis for compiled generic game logic."""

from __future__ import annotations

import copy
import hashlib
import os
from collections import deque
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from worldforge.asset_io import AssetContractError, write_json_atomic
from worldforge.creation_contracts import (
    CreationContractError,
    _exact_keys,
    _identifier,
    _integer,
    _non_empty_string,
    _object,
    _sha256,
    canonical_creation_hash,
    read_creation_object,
)
from worldforge.game_logic import (
    ANALYSIS_LIMITS,
    ANALYZERS,
    MAX_SAFE_INTEGER,
    GameLogicError,
    StateClassification,
    TransitionResult,
    _Interpreter,
    canonical_state_bytes,
    canonical_trace_step,
)
from worldforge.gamepack import (
    GAMEPACK_FORMAT,
    GAMEPACK_VERSION,
    GamepackError,
    PublishedGameArtifact,
    _published_artifact,
    preflight_game_artifact_output,
    validate_gamepack_document,
)
from worldforge.integrity import canonical_json_bytes

ANALYSIS_FORMAT = "world-forge.game_analysis"
ANALYSIS_VERSION = 1

_STATUS = frozenset({"passed", "failed", "inconclusive", "unsupported"})
_CHECK_STATUS = frozenset({"passed", "failed", "inconclusive", "not_applicable"})
_SEVERITY = frozenset({"error", "warning"})
_WITNESS_KINDS = frozenset(
    {
        "ending",
        "failure",
        "goal",
        "narrative_option",
        "narrative_unit",
        "state",
    }
)
_ANALYSIS_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "analysis_id",
        "gamepack",
        "requirement",
        "analyzer",
        "status",
        "reason_codes",
        "method",
        "assumptions",
        "false_positive_risks",
        "false_negative_risks",
        "out_of_scope_claims",
        "summary",
        "metrics",
        "checks",
        "findings",
        "witnesses",
        "content_hash",
    }
)
_IDENTITY_FIELDS = frozenset({"format", "format_version", "id", "content_hash"})
_ANALYZER_FIELDS = frozenset({"id", "version", "profile"})
_METHOD_FIELDS = frozenset({"kind", "traversal", "state_identity", "bound_policy", "evidence_kind"})
_SUMMARY_FIELDS = frozenset({"checks", "passed", "failed", "inconclusive", "findings", "witnesses"})
_METRIC_FIELDS = frozenset(
    {
        "candidate_evaluations",
        "states",
        "transitions",
        "max_depth",
        "largest_state_bytes",
        "total_state_bytes",
        "goals_reached",
        "endings_reached",
        "narrative_units_reached",
        "narrative_options_reached",
        "frontier_closed",
    }
)
_CHECK_FIELDS = frozenset({"id", "status", "reason_codes"})
_FINDING_FIELDS = frozenset(
    {
        "reason_code",
        "severity",
        "subject_kind",
        "subject_id",
        "state_hash",
        "message",
        "witness_id",
    }
)
_WITNESS_FIELDS = frozenset({"id", "kind", "target_id", "steps"})
_STEP_FIELDS = frozenset(
    {
        "action_id",
        "parameters",
        "pre_state_hash",
        "post_state_hash",
        "events",
    }
)
_REQUIREMENT_FIELDS = frozenset(
    {
        "profile",
        "analyzer_id",
        "analyzer_version",
        "reason_code",
        "limits",
        "content_hash",
    }
)
_SUMMARY_MAXIMA = {
    "checks": 32,
    "passed": 32,
    "failed": 32,
    "inconclusive": 32,
    "findings": 16_384,
    "witnesses": 128,
}
_METRIC_MAXIMA = {
    "candidate_evaluations": ANALYSIS_LIMITS["candidate_evaluations"],
    "states": ANALYSIS_LIMITS["states"],
    "transitions": ANALYSIS_LIMITS["candidate_evaluations"],
    "max_depth": ANALYSIS_LIMITS["depth"],
    "largest_state_bytes": ANALYSIS_LIMITS["total_state_bytes"],
    "total_state_bytes": ANALYSIS_LIMITS["total_state_bytes"],
    "goals_reached": 64,
    "endings_reached": 64,
    "narrative_units_reached": 65_536,
    "narrative_options_reached": 65_536,
}

_METHOD = {
    "kind": "bounded_exhaustive_state_analysis",
    "traversal": "canonical_breadth_first_first_discovery",
    "state_identity": "sha256_compact_canonical_json",
    "bound_policy": "inconclusive_before_frontier_closure",
    "evidence_kind": "exhaustive_within_declared_bounds",
}
_ASSUMPTIONS = [
    "The validated gamepack is the complete authoritative logic input.",
    "Action parameter domains are finite and exactly declared by the gamepack.",
    "Array order is authoritative and state equality uses compact canonical JSON.",
]
_FALSE_POSITIVE_RISKS = [
    "A modeled state may be reachable but unusable in an unmodeled presentation adapter.",
]
_FALSE_NEGATIVE_RISKS = [
    "Any configured bound reached before frontier closure makes the result inconclusive.",
]
_OUT_OF_SCOPE = [
    "asset_readability",
    "native_adapter_execution",
    "platform_performance",
    "save_replay_serialization",
    "timing_and_input_ux",
]


class GameAnalysisError(ValueError):
    """A stable game-analysis contract, integrity, or publication failure."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise GameAnalysisError(reason_code, detail)


def _canonical_hash(value: Mapping[str, object]) -> str:
    return canonical_creation_hash(value)


def _identity(gamepack: Mapping[str, object]) -> dict[str, object]:
    game = gamepack["game"]
    assert isinstance(game, Mapping)
    return {
        "format": GAMEPACK_FORMAT,
        "format_version": GAMEPACK_VERSION,
        "id": game["id"],
        "content_hash": gamepack["content_hash"],
    }


def _analysis_id(gamepack: Mapping[str, object]) -> str:
    return f"analysis_{str(gamepack['content_hash'])[:16]}"


def _trace(
    state_hash: str,
    parents: Mapping[str, tuple[str, TransitionResult] | None],
) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    cursor = state_hash
    while parents[cursor] is not None:
        parent_hash, result = parents[cursor]  # type: ignore[misc]
        steps.append(canonical_trace_step(result))
        cursor = parent_hash
    steps.reverse()
    return steps


def _finding(
    reason_code: str,
    *,
    subject_kind: str,
    subject_id: str,
    state_hash: str | None = None,
    message: str,
) -> dict[str, object]:
    return {
        "reason_code": reason_code,
        "severity": "error",
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "state_hash": state_hash,
        "message": message,
        "witness_id": None,
    }


def _check(
    identifier: str,
    status: str,
    *reason_codes: str,
) -> dict[str, object]:
    return {
        "id": identifier,
        "status": status,
        "reason_codes": sorted(set(reason_codes), key=lambda item: item.encode("utf-8")),
    }


def _deduplicate_findings(
    findings: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    unique: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for item in findings:
        key = (
            str(item["reason_code"]),
            str(item["subject_kind"]),
            str(item["subject_id"]),
            str(item["state_hash"] or ""),
        )
        unique.setdefault(key, copy.deepcopy(dict(item)))
    return list(unique.values())


def _preflight_bound_report(
    gamepack: Mapping[str, object],
    reason_code: str,
    *,
    initial_state_bytes: int = 0,
) -> dict[str, object]:
    requirement = copy.deepcopy(gamepack["analysis_requirements"])
    assert isinstance(requirement, Mapping)
    profile = str(requirement["profile"])
    analyzer_id, analyzer_version = ANALYZERS[profile]
    checks = [
        _check("frontier_closed", "inconclusive", reason_code),
        _check("witness_evidence_complete", "not_applicable"),
    ]
    report: dict[str, object] = {
        "format": ANALYSIS_FORMAT,
        "format_version": ANALYSIS_VERSION,
        "analysis_id": _analysis_id(gamepack),
        "gamepack": _identity(gamepack),
        "requirement": requirement,
        "analyzer": {
            "id": analyzer_id,
            "version": analyzer_version,
            "profile": profile,
        },
        "status": "inconclusive",
        "reason_codes": [reason_code],
        "method": copy.deepcopy(_METHOD),
        "assumptions": copy.deepcopy(_ASSUMPTIONS),
        "false_positive_risks": copy.deepcopy(_FALSE_POSITIVE_RISKS),
        "false_negative_risks": copy.deepcopy(_FALSE_NEGATIVE_RISKS),
        "out_of_scope_claims": copy.deepcopy(_OUT_OF_SCOPE),
        "summary": {
            "checks": len(checks),
            "passed": 0,
            "failed": 0,
            "inconclusive": 1,
            "findings": 0,
            "witnesses": 0,
        },
        "metrics": {
            "candidate_evaluations": 0,
            "states": int(initial_state_bytes > 0),
            "transitions": 0,
            "max_depth": 0,
            "largest_state_bytes": initial_state_bytes,
            "total_state_bytes": initial_state_bytes,
            "goals_reached": 0,
            "endings_reached": 0,
            "narrative_units_reached": 0,
            "narrative_options_reached": 0,
            "frontier_closed": False,
        },
        "checks": checks,
        "findings": [],
        "witnesses": [],
    }
    report["content_hash"] = _canonical_hash(report)
    return validate_game_analysis_structure(report)


def _unsupported_report(gamepack: Mapping[str, object]) -> dict[str, object]:
    requirement = copy.deepcopy(gamepack["analysis_requirements"])
    assert isinstance(requirement, Mapping)
    analyzer = {
        "id": requirement["analyzer_id"],
        "version": requirement["analyzer_version"],
        "profile": requirement["profile"],
    }
    checks = [
        _check(
            "analysis_profile_supported",
            "not_applicable",
            "analysis_profile_unsupported",
        )
    ]
    report: dict[str, object] = {
        "format": ANALYSIS_FORMAT,
        "format_version": ANALYSIS_VERSION,
        "analysis_id": _analysis_id(gamepack),
        "gamepack": _identity(gamepack),
        "requirement": requirement,
        "analyzer": analyzer,
        "status": "unsupported",
        "reason_codes": ["analysis_profile_unsupported"],
        "method": copy.deepcopy(_METHOD),
        "assumptions": copy.deepcopy(_ASSUMPTIONS),
        "false_positive_risks": copy.deepcopy(_FALSE_POSITIVE_RISKS),
        "false_negative_risks": copy.deepcopy(_FALSE_NEGATIVE_RISKS),
        "out_of_scope_claims": copy.deepcopy(_OUT_OF_SCOPE),
        "summary": {
            "checks": 1,
            "passed": 0,
            "failed": 0,
            "inconclusive": 0,
            "findings": 0,
            "witnesses": 0,
        },
        "metrics": {
            "candidate_evaluations": 0,
            "states": 0,
            "transitions": 0,
            "max_depth": 0,
            "largest_state_bytes": 0,
            "total_state_bytes": 0,
            "goals_reached": 0,
            "endings_reached": 0,
            "narrative_units_reached": 0,
            "narrative_options_reached": 0,
            "frontier_closed": False,
        },
        "checks": checks,
        "findings": [],
        "witnesses": [],
    }
    report["content_hash"] = _canonical_hash(report)
    return validate_game_analysis_structure(report)


def _narrative_contract(
    gamepack: Mapping[str, object],
) -> tuple[set[str], dict[tuple[str, str], str], set[str]]:
    modules = gamepack["modules"]
    assert isinstance(modules, Mapping)
    unit_ids: set[str] = set()
    options: dict[tuple[str, str], str] = {}
    ending_ids: set[str] = set()
    projections = modules["narrative"]
    assert isinstance(projections, list)
    for projection in projections:
        assert isinstance(projection, Mapping)
        units = projection["units"]
        assert isinstance(units, list)
        for unit in units:
            assert isinstance(unit, Mapping)
            unit_id = str(unit["id"])
            unit_ids.add(unit_id)
            if unit["unit_type"] == "ending":
                ending_ids.add(unit_id)
            raw_options = unit.get("options", [])
            assert isinstance(raw_options, list)
            for option in raw_options:
                assert isinstance(option, Mapping)
                options[(unit_id, str(option["id"]))] = str(option["next_unit_id"])
    return unit_ids, options, ending_ids


def _build_witnesses(
    targets: Sequence[tuple[str, str, str]],
    parents: Mapping[str, tuple[str, TransitionResult] | None],
    *,
    explicit_traces: Mapping[
        tuple[str, str],
        Sequence[Mapping[str, object]],
    ]
    | None = None,
) -> tuple[list[dict[str, object]], dict[tuple[str, str], str], bool]:
    witnesses: list[dict[str, object]] = []
    witness_ids: dict[tuple[str, str], str] = {}
    total_steps = 0
    bounded = False
    for kind, target_id, state_hash in sorted(
        set(targets),
        key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8"), item[2]),
    ):
        explicit = explicit_traces.get((kind, target_id)) if explicit_traces is not None else None
        steps = (
            [copy.deepcopy(dict(item)) for item in explicit]
            if explicit is not None
            else _trace(state_hash, parents)
        )
        if (
            len(witnesses) >= ANALYSIS_LIMITS["witness_traces"]
            or total_steps + len(steps) > ANALYSIS_LIMITS["total_witness_steps"]
        ):
            bounded = True
            break
        witness_id = f"witness_{len(witnesses) + 1:04d}"
        witness = {
            "id": witness_id,
            "kind": kind,
            "target_id": target_id,
            "steps": steps,
        }
        witnesses.append(witness)
        witness_ids[(kind, target_id)] = witness_id
        total_steps += len(steps)
    return witnesses, witness_ids, bounded


def analyze_gamepack(value: object) -> dict[str, object]:
    """Run the compiler-selected frozen analyzer over one exact gamepack."""

    try:
        gamepack = validate_gamepack_document(value)
    except GamepackError as exc:
        _fail(exc.reason_code, exc.detail)
    requirement = gamepack["analysis_requirements"]
    assert isinstance(requirement, Mapping)
    if requirement["profile"] == "unsupported":
        return _unsupported_report(gamepack)
    expected_analyzer = ANALYZERS.get(str(requirement["profile"]))
    if expected_analyzer != (
        requirement["analyzer_id"],
        requirement["analyzer_version"],
    ):
        return _unsupported_report(gamepack)
    try:
        interpreter = _Interpreter(gamepack, already_validated=True)
        candidates = interpreter.enumerate_candidates()
        start = interpreter.initial_state()
    except GameLogicError as exc:
        if exc.reason_code in {
            "operator_unsupported",
            "execution_semantics_unsupported",
            "parameter_type_unsupported",
        }:
            return _unsupported_report(gamepack)
        if exc.reason_code == "parameter_combinations_exceeded":
            return _preflight_bound_report(
                gamepack,
                "parameter_combinations_bound_reached",
            )
        _fail(exc.reason_code, exc.detail)

    start_hash = hashlib.sha256(canonical_state_bytes(start)).hexdigest()
    start_bytes = len(canonical_state_bytes(start))
    if start_bytes > ANALYSIS_LIMITS["state_bytes"]:
        return _preflight_bound_report(
            gamepack,
            "state_bytes_bound_reached",
            initial_state_bytes=start_bytes,
        )
    if start_bytes > ANALYSIS_LIMITS["total_state_bytes"]:
        return _preflight_bound_report(
            gamepack,
            "total_state_bytes_bound_reached",
            initial_state_bytes=start_bytes,
        )
    states: dict[str, dict[str, Any]] = {start_hash: start}
    parents: dict[str, tuple[str, TransitionResult] | None] = {start_hash: None}
    depths = {start_hash: 0}
    queue: deque[str] = deque([start_hash])
    graph: dict[str, set[str]] = {start_hash: set()}
    classifications: dict[str, StateClassification] = {}
    legal_by_state: dict[str, list[TransitionResult]] = {}
    expanded_states: set[str] = set()
    candidate_evaluations = 0
    transitions = 0
    total_state_bytes = start_bytes
    largest_state_bytes = start_bytes
    max_depth = 0
    bound_reason: str | None = None
    integrity_findings: list[dict[str, object]] = []

    while queue and bound_reason is None:
        state_hash = queue.popleft()
        state = states[state_hash]
        depth = depths[state_hash]
        max_depth = max(max_depth, depth)
        try:
            classification = interpreter.classify(state)
        except GameLogicError as exc:
            integrity_findings.append(
                _finding(
                    exc.reason_code,
                    subject_kind="state",
                    subject_id=state_hash,
                    state_hash=state_hash,
                    message=exc.detail,
                )
            )
            legal_by_state[state_hash] = []
            continue
        classifications[state_hash] = classification
        graph.setdefault(state_hash, set())
        if classification.terminal:
            legal_by_state[state_hash] = []
            expanded_states.add(state_hash)
            continue
        legal: list[TransitionResult] = []
        for candidate in candidates:
            if candidate_evaluations >= ANALYSIS_LIMITS["candidate_evaluations"]:
                bound_reason = "candidate_evaluations_bound_reached"
                break
            candidate_evaluations += 1
            result = interpreter.transition(state, candidate)
            if not result.accepted:
                if result.rejection_reason in {
                    "ambiguous_ending",
                    "cursor_divergence",
                    "operator_unsupported",
                    "narrative_transition_invalid",
                }:
                    integrity_findings.append(
                        _finding(
                            str(result.rejection_reason),
                            subject_kind="transition",
                            subject_id=candidate.action_id,
                            state_hash=state_hash,
                            message="The exact candidate transition violates gamepack integrity.",
                        )
                    )
                continue
            legal.append(result)
            transitions += 1
            post_hash = result.post_state_hash
            if post_hash in states:
                graph[state_hash].add(post_hash)
                continue
            next_depth = depth + 1
            encoded = canonical_state_bytes(result.post_state)
            if next_depth > ANALYSIS_LIMITS["depth"]:
                bound_reason = "depth_bound_reached"
                break
            if len(states) >= ANALYSIS_LIMITS["states"]:
                bound_reason = "states_bound_reached"
                break
            if len(encoded) > ANALYSIS_LIMITS["state_bytes"]:
                bound_reason = "state_bytes_bound_reached"
                break
            if total_state_bytes + len(encoded) > ANALYSIS_LIMITS["total_state_bytes"]:
                bound_reason = "total_state_bytes_bound_reached"
                break
            graph[state_hash].add(post_hash)
            graph.setdefault(post_hash, set())
            states[post_hash] = copy.deepcopy(result.post_state)
            parents[post_hash] = (state_hash, result)
            depths[post_hash] = next_depth
            total_state_bytes += len(encoded)
            largest_state_bytes = max(largest_state_bytes, len(encoded))
            queue.append(post_hash)
        legal_by_state[state_hash] = legal
        if bound_reason is None:
            expanded_states.add(state_hash)

    frontier_closed = not queue and bound_reason is None
    if frontier_closed:
        # Terminal states are inserted without being expanded only when they
        # reach the queue, so classify any final discoveries not yet visited.
        for state_hash, state in states.items():
            if state_hash in classifications:
                continue
            try:
                classifications[state_hash] = interpreter.classify(state)
            except GameLogicError as exc:
                integrity_findings.append(
                    _finding(
                        exc.reason_code,
                        subject_kind="state",
                        subject_id=state_hash,
                        state_hash=state_hash,
                        message=exc.detail,
                    )
                )
            legal_by_state.setdefault(state_hash, [])

    reached_goals: dict[str, str] = {}
    reached_endings: dict[str, str] = {}
    failure_states: dict[str, StateClassification] = {}
    # ``states`` preserves canonical BFS first-discovery order.  Keeping that
    # order here makes every selected witness a deterministic shortest trace.
    for state_hash in states:
        classification = classifications.get(state_hash)
        if classification is None:
            continue
        for goal_id in classification.goal_ids:
            reached_goals.setdefault(goal_id, state_hash)
        for ending_id in classification.ending_ids:
            reached_endings.setdefault(ending_id, state_hash)
        if classification.failure_ids:
            failure_states[state_hash] = classification

    logic = gamepack["logic"]
    assert isinstance(logic, Mapping)
    authored_goals = {str(item["id"]) for item in logic["goals"]}  # type: ignore[index]
    authored_endings = {str(item["id"]) for item in logic["endings"]}  # type: ignore[index]
    integrity_findings = _deduplicate_findings(integrity_findings)
    proof_graph_closed = frontier_closed and not integrity_findings
    findings = list(integrity_findings)
    if proof_graph_closed:
        for goal_id in sorted(
            authored_goals - set(reached_goals),
            key=lambda item: item.encode("utf-8"),
        ):
            findings.append(
                _finding(
                    "authored_goal_unreachable",
                    subject_kind="goal",
                    subject_id=goal_id,
                    message="The authored goal is not reachable within the closed state graph.",
                )
            )
        for ending_id in sorted(
            authored_endings - set(reached_endings),
            key=lambda item: item.encode("utf-8"),
        ):
            findings.append(
                _finding(
                    "authored_ending_unreachable",
                    subject_kind="ending",
                    subject_id=ending_id,
                    message="The authored ending is not reachable within the closed state graph.",
                )
            )

    terminal_states = {
        state_hash
        for state_hash, classification in classifications.items()
        if classification.terminal
    }
    reverse: dict[str, set[str]] = {state_hash: set() for state_hash in states}
    for source, targets in graph.items():
        for target in targets:
            reverse.setdefault(target, set()).add(source)
    can_reach_terminal = set(terminal_states)
    reverse_queue = deque(sorted(terminal_states))
    while reverse_queue:
        target = reverse_queue.popleft()
        for source in sorted(reverse.get(target, ())):
            if source not in can_reach_terminal:
                can_reach_terminal.add(source)
                reverse_queue.append(source)
    success_terminal_states = {
        state_hash
        for state_hash, classification in classifications.items()
        if classification.ending_kind == "success"
    }
    can_reach_success = set(success_terminal_states)
    success_queue = deque(sorted(success_terminal_states))
    while success_queue:
        target = success_queue.popleft()
        for source in sorted(reverse.get(target, ())):
            if source not in can_reach_success:
                can_reach_success.add(source)
                success_queue.append(source)

    softlocks: list[str] = []
    traps: list[str] = []
    for state_hash, classification in classifications.items():
        if classification.terminal:
            continue
        if state_hash in expanded_states and not legal_by_state.get(state_hash):
            softlocks.append(state_hash)
        if proof_graph_closed and state_hash not in can_reach_terminal:
            traps.append(state_hash)
    for state_hash in sorted(softlocks):
        findings.append(
            _finding(
                "nonterminal_softlock",
                subject_kind="state",
                subject_id=state_hash,
                state_hash=state_hash,
                message="A reachable nonterminal state has zero legal actions.",
            )
        )
    for state_hash in sorted(set(traps) - set(softlocks)):
        findings.append(
            _finding(
                "nonterminal_terminal_trap",
                subject_kind="state",
                subject_id=state_hash,
                state_hash=state_hash,
                message="A reachable nonterminal state cannot reach an authored ending.",
            )
        )

    recovery_invalid = False
    for state_hash, classification in sorted(failure_states.items()):
        if not classification.recovery_action_ids:
            recovery_invalid = True
            findings.append(
                _finding(
                    "failure_recovery_empty_intersection",
                    subject_kind="state",
                    subject_id=state_hash,
                    state_hash=state_hash,
                    message="Active failures have no common recovery action.",
                )
            )
            continue
        if state_hash not in expanded_states:
            continue
        legal_actions = {item.action.action_id for item in legal_by_state.get(state_hash, ())}
        if not set(classification.recovery_action_ids).issubset(legal_actions):
            recovery_invalid = True
            findings.append(
                _finding(
                    "failure_recovery_unavailable",
                    subject_kind="state",
                    subject_id=state_hash,
                    state_hash=state_hash,
                    message="A declared common recovery action is not executable.",
                )
            )

    profile = str(requirement["profile"])
    reached_units: set[str] = set()
    reached_options: set[tuple[str, str]] = set()
    reached_unit_states: dict[str, str] = {}
    reached_option_states: dict[str, str] = {}
    reached_option_traces: dict[str, list[dict[str, object]]] = {}
    narrative_units: set[str] = set()
    narrative_options: dict[tuple[str, str], str] = {}
    narrative_ending_ids: set[str] = set()
    if profile == "branching_narrative":
        narrative_units, narrative_options, narrative_ending_ids = _narrative_contract(gamepack)
        cursor = logic["narrative_cursor"]
        assert isinstance(cursor, Mapping)
        cursor_id = str(cursor["id"])
        transition_by_action = {
            str(item["action_id"]): item
            for item in logic["narrative_transitions"]  # type: ignore[index]
        }
        for state_hash, state in states.items():
            cursor_value = state[cursor_id]
            if isinstance(cursor_value, str):
                reached_units.add(cursor_value)
                reached_unit_states.setdefault(cursor_value, state_hash)
            for result in legal_by_state.get(state_hash, ()):
                if result.post_state_hash not in states:
                    continue
                transition_contract = transition_by_action.get(result.action.action_id)
                if transition_contract is not None:
                    option_key = (
                        str(transition_contract["source_unit_id"]),
                        str(transition_contract["option_id"]),
                    )
                    reached_options.add(option_key)
                    reached_option_states.setdefault(
                        f"{option_key[0]}:{option_key[1]}",
                        result.post_state_hash,
                    )
                    reached_option_traces.setdefault(
                        f"{option_key[0]}:{option_key[1]}",
                        [
                            *_trace(state_hash, parents),
                            canonical_trace_step(result),
                        ],
                    )
        if proof_graph_closed:
            for unit_id in sorted(
                narrative_units - reached_units,
                key=lambda item: item.encode("utf-8"),
            ):
                findings.append(
                    _finding(
                        "narrative_unit_unreachable",
                        subject_kind="narrative_unit",
                        subject_id=unit_id,
                        message="The authored narrative unit is not cursor-reachable.",
                    )
                )
            for unit_id, option_id in sorted(
                set(narrative_options) - reached_options,
                key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8")),
            ):
                findings.append(
                    _finding(
                        "narrative_option_unreachable",
                        subject_kind="narrative_option",
                        subject_id=f"{unit_id}:{option_id}",
                        message="The authored narrative option is never executable.",
                    )
                )
            if len(set(reached_endings) & narrative_ending_ids) < 2:
                findings.append(
                    _finding(
                        "distinct_endings_insufficient",
                        subject_kind="game",
                        subject_id=str(gamepack["game"]["id"]),  # type: ignore[index]
                        message=(
                            "Branching narrative analysis requires at least two reachable endings."
                        ),
                    )
                )

    restart_invalid = False
    if profile == "abstract_puzzle":
        effects = {str(item["id"]): item for item in logic["effects"]}  # type: ignore[index]
        rules = {str(item["id"]): item for item in logic["rules"]}  # type: ignore[index]
        actions = {str(item["id"]): item for item in logic["actions"]}  # type: ignore[index]
        for classification in failure_states.values():
            for action_id in classification.recovery_action_ids:
                action = actions[action_id]
                reset_effects = [
                    effects[str(effect_id)]
                    for rule_id in action["rule_ids"]
                    for effect_id in rules[str(rule_id)]["effect_ids"]
                    if effects[str(effect_id)]["operation"] == "reset"
                ]
                if not reset_effects:
                    restart_invalid = True
        if restart_invalid:
            findings.append(
                _finding(
                    "recovery_reset_not_declared",
                    subject_kind="game",
                    subject_id=str(gamepack["game"]["id"]),  # type: ignore[index]
                    message="A puzzle recovery action has no explicit reset effect.",
                )
            )

    incomplete_reason = bound_reason or "execution_integrity_failed"

    def proof_check(
        identifier: str,
        proved: bool,
        failure_reason: str,
    ) -> dict[str, object]:
        if proved:
            return _check(identifier, "passed")
        if proof_graph_closed:
            return _check(identifier, "failed", failure_reason)
        return _check(identifier, "inconclusive", incomplete_reason)

    checks: list[dict[str, object]] = [
        _check(
            "frontier_closed",
            "passed" if frontier_closed else "inconclusive",
            *(() if frontier_closed else (bound_reason or "analysis_bound_reached",)),
        )
    ]
    checks.append(
        _check(
            "execution_integrity",
            "passed" if not integrity_findings else "failed",
            *(str(item["reason_code"]) for item in integrity_findings),
        )
    )
    checks.append(
        proof_check(
            "authored_goals_reachable",
            authored_goals.issubset(reached_goals),
            "authored_goal_unreachable",
        )
    )
    checks.append(
        proof_check(
            "authored_endings_reachable",
            authored_endings.issubset(reached_endings),
            "authored_ending_unreachable",
        )
    )
    if recovery_invalid:
        checks.append(_check("failure_recovery", "failed", "failure_recovery_invalid"))
    elif proof_graph_closed:
        checks.append(_check("failure_recovery", "passed"))
    else:
        checks.append(_check("failure_recovery", "inconclusive", incomplete_reason))
    if softlocks:
        checks.append(_check("nonterminal_softlocks", "failed", "nonterminal_softlock"))
    elif proof_graph_closed:
        checks.append(_check("nonterminal_softlocks", "passed"))
    else:
        checks.append(_check("nonterminal_softlocks", "inconclusive", incomplete_reason))
    checks.append(
        proof_check(
            "terminal_reachability",
            not traps and proof_graph_closed,
            "nonterminal_terminal_trap",
        )
    )
    if profile == "abstract_puzzle":
        if start_hash in can_reach_success:
            initial_check = _check("initial_solvable", "passed")
        elif proof_graph_closed:
            initial_check = _check(
                "initial_solvable",
                "failed",
                "initial_state_unsolvable",
            )
        else:
            initial_check = _check("initial_solvable", "inconclusive", incomplete_reason)
        if restart_invalid:
            recovery_check = _check(
                "recovery_restoration",
                "failed",
                "recovery_reset_not_declared",
            )
        elif proof_graph_closed:
            recovery_check = _check("recovery_restoration", "passed")
        else:
            recovery_check = _check(
                "recovery_restoration",
                "inconclusive",
                incomplete_reason,
            )
        checks.extend(
            [
                initial_check,
                recovery_check,
            ]
        )
    else:
        cursor_diverged = any(
            item["reason_code"] == "cursor_divergence" for item in integrity_findings
        )
        if cursor_diverged:
            cursor_check = _check("cursor_consistency", "failed", "cursor_divergence")
        elif proof_graph_closed:
            cursor_check = _check("cursor_consistency", "passed")
        else:
            cursor_check = _check("cursor_consistency", "inconclusive", incomplete_reason)
        checks.extend(
            [
                proof_check(
                    "narrative_units_reachable",
                    narrative_units.issubset(reached_units),
                    "narrative_unit_unreachable",
                ),
                proof_check(
                    "narrative_options_executable",
                    set(narrative_options).issubset(reached_options),
                    "narrative_option_unreachable",
                ),
                proof_check(
                    "distinct_endings",
                    len(set(reached_endings) & narrative_ending_ids) >= 2,
                    "distinct_endings_insufficient",
                ),
                cursor_check,
            ]
        )

    failure_targets: dict[str, str] = {}
    for state_hash, classification in failure_states.items():
        for failure_id in classification.failure_ids:
            failure_targets.setdefault(failure_id, state_hash)
    targets = [
        *(("ending", target_id, state_hash) for target_id, state_hash in reached_endings.items()),
        *(("failure", target_id, state_hash) for target_id, state_hash in failure_targets.items()),
        *(("goal", target_id, state_hash) for target_id, state_hash in reached_goals.items()),
        *(
            ("narrative_unit", target_id, state_hash)
            for target_id, state_hash in reached_unit_states.items()
        ),
        *(
            ("narrative_option", target_id, state_hash)
            for target_id, state_hash in reached_option_states.items()
        ),
        *(
            ("state", str(item["state_hash"]), str(item["state_hash"]))
            for item in findings
            if item["state_hash"] is not None
        ),
    ]
    witnesses, witness_ids, witness_bounded = _build_witnesses(
        targets,
        parents,
        explicit_traces={
            ("narrative_option", target_id): trace
            for target_id, trace in reached_option_traces.items()
        },
    )
    evidence_bound_reason = "witness_bound_reached" if witness_bounded else None
    checks.append(
        _check(
            "witness_evidence_complete",
            "inconclusive" if witness_bounded else "passed",
            *((evidence_bound_reason,) if evidence_bound_reason is not None else ()),
        )
    )
    for finding in findings:
        subject_key = (str(finding["subject_kind"]), str(finding["subject_id"]))
        if subject_key in witness_ids:
            finding["witness_id"] = witness_ids[subject_key]
        elif finding["state_hash"] is not None:
            finding["witness_id"] = witness_ids.get(("state", str(finding["state_hash"])))

    findings = _deduplicate_findings(findings)
    findings.sort(
        key=lambda item: (
            str(item["reason_code"]).encode("utf-8"),
            str(item["subject_kind"]).encode("utf-8"),
            str(item["subject_id"]).encode("utf-8"),
            str(item["state_hash"] or ""),
        )
    )
    checks.sort(key=lambda item: str(item["id"]).encode("utf-8"))
    failed_checks = sum(item["status"] == "failed" for item in checks)
    inconclusive_checks = sum(item["status"] == "inconclusive" for item in checks)
    if bound_reason is not None or evidence_bound_reason is not None:
        status = "inconclusive"
    elif findings or failed_checks:
        status = "failed"
    else:
        status = "passed"
    reason_codes = sorted(
        {
            *(str(item["reason_code"]) for item in findings),
            *(
                str(code)
                for item in checks
                if item["status"] != "passed"
                for code in item["reason_codes"]
            ),
        },
        key=lambda item: item.encode("utf-8"),
    )
    analyzer_id, analyzer_version = ANALYZERS[profile]
    report: dict[str, object] = {
        "format": ANALYSIS_FORMAT,
        "format_version": ANALYSIS_VERSION,
        "analysis_id": _analysis_id(gamepack),
        "gamepack": _identity(gamepack),
        "requirement": copy.deepcopy(requirement),
        "analyzer": {
            "id": analyzer_id,
            "version": analyzer_version,
            "profile": profile,
        },
        "status": status,
        "reason_codes": reason_codes,
        "method": copy.deepcopy(_METHOD),
        "assumptions": copy.deepcopy(_ASSUMPTIONS),
        "false_positive_risks": copy.deepcopy(_FALSE_POSITIVE_RISKS),
        "false_negative_risks": copy.deepcopy(_FALSE_NEGATIVE_RISKS),
        "out_of_scope_claims": copy.deepcopy(_OUT_OF_SCOPE),
        "summary": {
            "checks": len(checks),
            "passed": sum(item["status"] == "passed" for item in checks),
            "failed": failed_checks,
            "inconclusive": inconclusive_checks,
            "findings": len(findings),
            "witnesses": len(witnesses),
        },
        "metrics": {
            "candidate_evaluations": candidate_evaluations,
            "states": len(states),
            "transitions": transitions,
            "max_depth": max_depth,
            "largest_state_bytes": largest_state_bytes,
            "total_state_bytes": total_state_bytes,
            "goals_reached": len(reached_goals),
            "endings_reached": len(reached_endings),
            "narrative_units_reached": len(reached_units),
            "narrative_options_reached": len(reached_options),
            "frontier_closed": frontier_closed,
        },
        "checks": checks,
        "findings": findings,
        "witnesses": witnesses,
    }
    report["content_hash"] = _canonical_hash(report)
    return validate_game_analysis_structure(report)


def _string_array(
    value: object,
    context: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or len(value) > 256
        or not all(isinstance(item, str) and item for item in value)
        or any(len(item) > 256 for item in value)
    ):
        _fail("game_analysis_invalid", f"{context} must be a string array")
    return list(value)


def _validate_parameter_value(value: object, context: str) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int) and -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
        return
    if isinstance(value, str) and 1 <= len(value) <= 256:
        return
    if (
        isinstance(value, list)
        and len(value) <= 256
        and all(isinstance(item, str) and 1 <= len(item) <= 256 for item in value)
        and len(set(value)) == len(value)
    ):
        return
    _fail("game_analysis_invalid", f"{context} is not a bounded parameter value")


def validate_game_analysis_structure(value: object) -> dict[str, object]:
    """Validate only the report contract and internal canonical structure."""

    try:
        report = _object(value, "game analysis")
        _exact_keys(report, _ANALYSIS_FIELDS, "game analysis")
        if report.get("format") != ANALYSIS_FORMAT:
            _fail("game_analysis_format_invalid", f"format must be {ANALYSIS_FORMAT}")
        if report.get("format_version") != ANALYSIS_VERSION or isinstance(
            report.get("format_version"),
            bool,
        ):
            _fail("game_analysis_version_unsupported", "format_version must be 1")
        _identifier(report.get("analysis_id"), "game analysis.analysis_id")
        identity = _object(report.get("gamepack"), "game analysis.gamepack")
        _exact_keys(identity, _IDENTITY_FIELDS, "game analysis.gamepack")
        if (
            identity.get("format") != GAMEPACK_FORMAT
            or identity.get("format_version") != GAMEPACK_VERSION
            or isinstance(identity.get("format_version"), bool)
        ):
            _fail("game_analysis_invalid", "gamepack identity format/version is unsupported")
        _identifier(identity.get("id"), "game analysis.gamepack.id")
        _sha256(identity.get("content_hash"), "game analysis.gamepack.content_hash")
        requirement = _object(report.get("requirement"), "game analysis.requirement")
        _exact_keys(requirement, _REQUIREMENT_FIELDS, "game analysis.requirement")
        if requirement.get("content_hash") != _canonical_hash(requirement):
            _fail("game_analysis_invalid", "requirement hash does not match")
        profile = requirement.get("profile")
        if profile not in ANALYZERS:
            _fail("game_analysis_invalid", "requirement profile is unsupported")
        analyzer_id, analyzer_version = ANALYZERS[str(profile)]
        if (
            requirement.get("analyzer_id") != analyzer_id
            or requirement.get("analyzer_version") != analyzer_version
            or isinstance(requirement.get("analyzer_version"), bool)
        ):
            _fail("game_analysis_invalid", "requirement analyzer is not frozen")
        expected_reason = "analysis_profile_unsupported" if profile == "unsupported" else None
        if requirement.get("reason_code") != expected_reason:
            _fail("game_analysis_invalid", "requirement reason is inconsistent")
        limits = _object(requirement.get("limits"), "game analysis.requirement.limits")
        _exact_keys(limits, frozenset(ANALYSIS_LIMITS), "game analysis.requirement.limits")
        if limits != ANALYSIS_LIMITS:
            _fail("game_analysis_invalid", "requirement limits are not exact")
        analyzer = _object(report.get("analyzer"), "game analysis.analyzer")
        _exact_keys(analyzer, _ANALYZER_FIELDS, "game analysis.analyzer")
        expected_analyzer = ANALYZERS[str(profile)]
        if (
            (
                analyzer.get("id"),
                analyzer.get("version"),
            )
            != expected_analyzer
            or analyzer.get("profile") != profile
            or isinstance(
                analyzer.get("version"),
                bool,
            )
        ):
            _fail("game_analysis_invalid", "analyzer does not match the exact requirement")
        status = report.get("status")
        if status not in _STATUS:
            _fail("game_analysis_invalid", "status is unsupported")
        reason_codes = _string_array(report.get("reason_codes"), "game analysis.reason_codes")
        if reason_codes != sorted(set(reason_codes), key=lambda item: item.encode("utf-8")):
            _fail("game_analysis_invalid", "reason_codes is not canonical")
        if status in {"failed", "inconclusive", "unsupported"} and not reason_codes:
            _fail("game_analysis_invalid", f"{status} status requires reason codes")
        method = _object(report.get("method"), "game analysis.method")
        _exact_keys(method, _METHOD_FIELDS, "game analysis.method")
        if method != _METHOD:
            _fail("game_analysis_invalid", "method is not the exact built-in policy")
        for field, expected in (
            ("assumptions", _ASSUMPTIONS),
            ("false_positive_risks", _FALSE_POSITIVE_RISKS),
            ("false_negative_risks", _FALSE_NEGATIVE_RISKS),
            ("out_of_scope_claims", _OUT_OF_SCOPE),
        ):
            if _string_array(report.get(field), f"game analysis.{field}") != expected:
                _fail("game_analysis_invalid", f"{field} is not the exact v1 disclosure")
        summary = _object(report.get("summary"), "game analysis.summary")
        _exact_keys(summary, _SUMMARY_FIELDS, "game analysis.summary")
        for field in _SUMMARY_FIELDS:
            checked_summary = _integer(
                summary.get(field),
                f"game analysis.summary.{field}",
            )
            if checked_summary > _SUMMARY_MAXIMA[field]:
                _fail(
                    "game_analysis_invalid",
                    f"summary.{field} exceeds its schema maximum",
                )
        metrics = _object(report.get("metrics"), "game analysis.metrics")
        _exact_keys(metrics, _METRIC_FIELDS, "game analysis.metrics")
        for field in _METRIC_FIELDS - {"frontier_closed"}:
            checked_metric = _integer(
                metrics.get(field),
                f"game analysis.metrics.{field}",
            )
            if checked_metric > _METRIC_MAXIMA[field]:
                _fail(
                    "game_analysis_invalid",
                    f"metrics.{field} exceeds its schema maximum",
                )
        if not isinstance(metrics.get("frontier_closed"), bool):
            _fail("game_analysis_invalid", "metrics.frontier_closed must be boolean")
        checks = report.get("checks")
        if not isinstance(checks, list) or not checks or len(checks) > 32:
            _fail("game_analysis_invalid", "checks must be a non-empty array")
        check_ids: list[str] = []
        for index, raw in enumerate(checks):
            item = _object(raw, f"game analysis.checks/{index}")
            _exact_keys(item, _CHECK_FIELDS, f"game analysis.checks/{index}")
            check_ids.append(_identifier(item.get("id"), f"game analysis.checks/{index}.id"))
            if item.get("status") not in _CHECK_STATUS:
                _fail("game_analysis_invalid", "check status is unsupported")
            codes = _string_array(
                item.get("reason_codes"),
                f"game analysis.checks/{index}.reason_codes",
            )
            if codes != sorted(set(codes), key=lambda value: value.encode("utf-8")):
                _fail("game_analysis_invalid", "check reason codes are not canonical")
            if item["status"] == "passed" and codes:
                _fail("game_analysis_invalid", "passed checks cannot retain reason codes")
            if item["status"] in {"failed", "inconclusive"} and not codes:
                _fail("game_analysis_invalid", "failed checks require reason codes")
        if check_ids != sorted(set(check_ids), key=lambda item: item.encode("utf-8")):
            _fail("game_analysis_invalid", "checks are not canonical")
        findings = report.get("findings")
        if not isinstance(findings, list) or len(findings) > 16_384:
            _fail("game_analysis_invalid", "findings must be an array")
        finding_keys: list[tuple[bytes, bytes, bytes, str]] = []
        for index, raw in enumerate(findings):
            item = _object(raw, f"game analysis.findings/{index}")
            _exact_keys(item, _FINDING_FIELDS, f"game analysis.findings/{index}")
            reason_code = _non_empty_string(
                item.get("reason_code"),
                "finding.reason_code",
            )
            if len(reason_code) > 128:
                _fail("game_analysis_invalid", "finding.reason_code is too long")
            if item.get("severity") not in _SEVERITY:
                _fail("game_analysis_invalid", "finding severity is unsupported")
            for field, maximum in (
                ("subject_kind", 64),
                ("subject_id", 256),
                ("message", 1_024),
            ):
                field_value = _non_empty_string(item.get(field), f"finding.{field}")
                if len(field_value) > maximum:
                    _fail("game_analysis_invalid", f"finding.{field} is too long")
            for field in ("state_hash", "witness_id"):
                field_value = item.get(field)
                if field_value is not None:
                    if field == "state_hash":
                        _sha256(field_value, "finding.state_hash")
                    else:
                        _identifier(field_value, "finding.witness_id")
            finding_keys.append(
                (
                    str(item["reason_code"]).encode("utf-8"),
                    str(item["subject_kind"]).encode("utf-8"),
                    str(item["subject_id"]).encode("utf-8"),
                    str(item["state_hash"] or ""),
                )
            )
        if finding_keys != sorted(set(finding_keys)):
            _fail("game_analysis_invalid", "findings are not canonical or deduplicated")
        witnesses = report.get("witnesses")
        if not isinstance(witnesses, list):
            _fail("game_analysis_invalid", "witnesses must be an array")
        if len(witnesses) > ANALYSIS_LIMITS["witness_traces"]:
            _fail("game_analysis_invalid", "witness trace bound is exceeded")
        witness_ids: list[str] = []
        total_steps = 0
        witness_targets: set[tuple[str, str]] = set()
        for index, raw in enumerate(witnesses):
            witness = _object(raw, f"game analysis.witnesses/{index}")
            _exact_keys(witness, _WITNESS_FIELDS, f"game analysis.witnesses/{index}")
            witness_ids.append(_identifier(witness.get("id"), "witness.id"))
            if witness.get("kind") not in _WITNESS_KINDS:
                _fail("game_analysis_invalid", "witness kind is unsupported")
            target_id = _non_empty_string(witness.get("target_id"), "witness.target_id")
            if len(target_id) > 256:
                _fail("game_analysis_invalid", "witness.target_id is too long")
            target_key = (str(witness["kind"]), target_id)
            if target_key in witness_targets:
                _fail("game_analysis_invalid", "witness targets must be deduplicated")
            witness_targets.add(target_key)
            steps = witness.get("steps")
            if not isinstance(steps, list):
                _fail("game_analysis_invalid", "witness.steps must be an array")
            total_steps += len(steps)
            prior_post_hash: str | None = None
            for raw_step in steps:
                step = _object(raw_step, "witness.step")
                _exact_keys(step, _STEP_FIELDS, "witness.step")
                _identifier(step.get("action_id"), "witness.step.action_id")
                parameters = step.get("parameters")
                if not isinstance(parameters, dict) or len(parameters) > 16:
                    _fail("game_analysis_invalid", "witness parameters must be an object")
                for parameter_id, parameter_value in parameters.items():
                    _identifier(parameter_id, "witness.step.parameters key")
                    _validate_parameter_value(
                        parameter_value,
                        f"witness.step.parameters.{parameter_id}",
                    )
                _sha256(step.get("pre_state_hash"), "witness.step.pre_state_hash")
                _sha256(step.get("post_state_hash"), "witness.step.post_state_hash")
                events = _string_array(step.get("events"), "witness.step.events")
                for event_id in events:
                    _identifier(event_id, "witness.step.events item")
                if prior_post_hash is not None and step.get("pre_state_hash") != prior_post_hash:
                    _fail("game_analysis_invalid", "witness state hashes are not contiguous")
                prior_post_hash = str(step["post_state_hash"])
        if witness_ids != [f"witness_{index:04d}" for index in range(1, len(witness_ids) + 1)]:
            _fail("game_analysis_invalid", "witness IDs are not canonical")
        if total_steps > ANALYSIS_LIMITS["total_witness_steps"]:
            _fail("game_analysis_invalid", "witness step bound is exceeded")
        known_witness_ids = set(witness_ids)
        if any(
            item["witness_id"] is not None and item["witness_id"] not in known_witness_ids
            for item in findings
        ):
            _fail("game_analysis_invalid", "finding references an unknown witness")
        if summary["checks"] != len(checks):
            _fail("game_analysis_invalid", "summary check count does not match")
        if summary["findings"] != len(findings) or summary["witnesses"] != len(witnesses):
            _fail("game_analysis_invalid", "summary evidence counts do not match")
        if summary["passed"] != sum(item["status"] == "passed" for item in checks):
            _fail("game_analysis_invalid", "summary passed count does not match")
        if summary["failed"] != sum(item["status"] == "failed" for item in checks):
            _fail("game_analysis_invalid", "summary failed count does not match")
        if summary["inconclusive"] != sum(item["status"] == "inconclusive" for item in checks):
            _fail("game_analysis_invalid", "summary inconclusive count does not match")
        expected_reason_codes = {str(item["reason_code"]) for item in findings}
        expected_reason_codes.update(
            str(code)
            for item in checks
            if item["status"] != "passed"
            for code in item["reason_codes"]
        )
        if reason_codes != sorted(
            expected_reason_codes,
            key=lambda item: item.encode("utf-8"),
        ):
            _fail("game_analysis_invalid", "top-level reason_codes do not match evidence")
        if status == "passed" and (
            profile == "unsupported"
            or findings
            or summary["failed"]
            or summary["inconclusive"]
            or any(item["status"] != "passed" for item in checks)
            or reason_codes
            or metrics["frontier_closed"] is not True
        ):
            _fail("game_analysis_invalid", "passed status overclaims the report evidence")
        if status == "failed" and not (findings or summary["failed"]):
            _fail("game_analysis_invalid", "failed status has no failure evidence")
        if status == "inconclusive" and not summary["inconclusive"]:
            _fail("game_analysis_invalid", "inconclusive status has no bound evidence")
        if (status == "unsupported") != (profile == "unsupported"):
            _fail(
                "game_analysis_invalid",
                "unsupported status must exactly match an unsupported profile",
            )
        if status == "unsupported" and (
            findings
            or summary["failed"]
            or summary["inconclusive"]
            or any(item["status"] != "not_applicable" for item in checks)
            or metrics["frontier_closed"] is not False
        ):
            _fail("game_analysis_invalid", "unsupported status contains proof claims")
        _sha256(report.get("content_hash"), "game analysis.content_hash")
        if report.get("content_hash") != _canonical_hash(report):
            _fail("game_analysis_hash_mismatch", "content_hash does not match")
    except GameAnalysisError:
        raise
    except (CreationContractError, TypeError, ValueError) as exc:
        _fail("game_analysis_invalid", str(exc))
    return copy.deepcopy(report)


def validate_game_analysis(
    value: object,
    gamepack: object,
) -> dict[str, object]:
    """Rerun the exact analyzer and compare the canonical report bytes."""

    checked_gamepack = validate_gamepack_document(gamepack)
    checked = validate_game_analysis_structure(value)
    if checked["gamepack"] != _identity(checked_gamepack):
        _fail("gamepack_identity_mismatch", "report targets a different exact gamepack")
    if checked["requirement"] != checked_gamepack["analysis_requirements"]:
        _fail("analysis_requirement_mismatch", "report requirement differs from gamepack")
    expected = analyze_gamepack(checked_gamepack)
    if canonical_json_bytes(expected) != canonical_json_bytes(checked):
        _fail(
            "analysis_rerun_mismatch",
            "report is not byte-identical to the deterministic analyzer rerun",
        )
    return checked


def serialize_game_analysis(value: object) -> bytes:
    return canonical_json_bytes(validate_game_analysis_structure(value))


def publish_game_analysis(
    path: str | os.PathLike[str],
    value: object,
    *,
    gamepack: object,
) -> PublishedGameArtifact:
    document = validate_game_analysis(value, gamepack)
    try:
        destination = preflight_game_artifact_output(path)
    except GamepackError as exc:
        _fail(exc.reason_code, exc.detail)
    try:
        write_json_atomic(destination, document, durable_parent=True)
    except AssetContractError as exc:
        reason = "output_exists" if "overwrite" in str(exc).casefold() else "output_publish_failed"
        _fail(reason, str(exc))
    return _published_artifact(destination, document)


def load_game_analysis(
    path: str | os.PathLike[str],
    *,
    gamepack: object | None = None,
) -> dict[str, object]:
    try:
        value = read_creation_object(path)
    except (CreationContractError, OSError) as exc:
        _fail("invalid_json", str(exc))
    if gamepack is None:
        return validate_game_analysis_structure(value)
    return validate_game_analysis(value, gamepack)


BUILTIN_ANALYZER_REGISTRY = MappingProxyType(
    {
        analyzer_id: MappingProxyType(
            {
                "version": version,
                "profile": profile,
                "entrypoint": "worldforge.game_analysis:analyze_gamepack",
            }
        )
        for profile, (analyzer_id, version) in ANALYZERS.items()
    }
)
