from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worldforge.integrity import declared_hash_matches


@dataclass(frozen=True, slots=True)
class Phase:
    id: str
    title: str


PHASES = (
    Phase("p00_brief", "Brief and constraints"),
    Phase("p01_genre_style", "Genre, promise and style"),
    Phase("p02_world_laws", "World laws and canon ontology"),
    Phase("p03_geography", "Geography and environments"),
    Phase("p04_timeline", "History, events and timeline"),
    Phase("p05_societies", "Societies, cultures and factions"),
    Phase("p06_characters", "Characters and personal stories"),
    Phase("p07_systems", "Systems and interaction matrix"),
    Phase("p08_world_arcs", "World arcs and scenario architecture"),
    Phase("p09_narrative_content", "Quests, scenes and dialogue"),
    Phase("p10_canon_lock", "Simulation, continuity and canon lock"),
    Phase("p11_art_audio", "Visual and audio direction"),
    Phase("p12_asset_specs", "Asset inventory and specifications"),
    Phase("p13_asset_production", "Asset production and QA"),
    Phase("p14_handoff", "Implementation handoff"),
)
PHASE_INDEX = {phase.id: index for index, phase in enumerate(PHASES)}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class WorkflowError(ValueError):
    """Raised when a workflow transition or report is invalid."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"{path} must contain an object")
    return value


def _write_object(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def initial_status(world_id: str) -> dict[str, Any]:
    return {
        "format": "rpg-world-forge.workflow_status",
        "format_version": 1,
        "world_id": world_id,
        "lead_agent": "gpt",
        "current_phase": PHASES[0].id,
        "completed_phases": [],
        "revision": 0,
        "canon_locked": False,
        "worldpack_hash": None,
        "worldpack_path": None,
        "asset_manifest": None,
        "renderpack": None,
    }


def phase_catalog() -> list[dict[str, str]]:
    return [{"id": phase.id, "title": phase.title} for phase in PHASES]


def load_status(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root)
    return _read_object(root / ".worldforge/status.json")


def describe_status(project_root: str | Path) -> str:
    status = load_status(project_root)
    current = status.get("current_phase")
    title = "complete" if current is None else PHASES[PHASE_INDEX[current]].title
    return (
        f"world={status['world_id']} phase={current or 'complete'} "
        f"title={title!r} completed={len(status.get('completed_phases', []))}/{len(PHASES)} "
        f"revision={status.get('revision', 0)}"
    )


def _safe_deliverable(root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target


def validate_phase_report(
    project_root: str | Path,
    report_path: str | Path,
) -> tuple[dict[str, Any], list[str]]:
    root = Path(project_root).resolve()
    status = load_status(root)
    report = _read_object(Path(report_path))
    errors: list[str] = []
    if report.get("format") != "rpg-world-forge.phase_report":
        errors.append("unknown phase-report format")
    if report.get("format_version") != 1:
        errors.append("unsupported phase-report version")
    current = status.get("current_phase")
    if report.get("phase") != current:
        errors.append(f"report targets {report.get('phase')} but current phase is {current}")
    if report.get("status") != "ready":
        errors.append("status must be ready")
    blockers = report.get("blockers")
    if not isinstance(blockers, list):
        errors.append("blockers must be a list")
    elif blockers:
        errors.append("the phase still contains blockers")
    if not isinstance(report.get("reviewed_by"), str) or not report["reviewed_by"].strip():
        errors.append("reviewed_by is required")

    deliverables = report.get("deliverables")
    if not isinstance(deliverables, list) or not deliverables:
        errors.append("deliverables must contain at least one file")
    else:
        for relative in deliverables:
            target = _safe_deliverable(root, relative)
            if target is None:
                errors.append(f"deliverable has an unsafe path: {relative}")
            elif not target.is_file() or target.stat().st_size == 0:
                errors.append(f"deliverable is missing or empty: {relative}")

    validations = report.get("validations")
    if not isinstance(validations, list) or not validations:
        errors.append("validations must contain at least one check")
    else:
        for validation in validations:
            if not isinstance(validation, dict) or validation.get("passed") is not True:
                errors.append("every validation must declare passed=true")

    if current == "p10_canon_lock":
        worldpack = _safe_deliverable(root, report.get("worldpack_path"))
        reported_hash = report.get("worldpack_hash")
        if worldpack is None or not worldpack.is_file():
            errors.append("P10 requires an existing worldpack_path inside the project")
        else:
            try:
                payload = _read_object(worldpack)
            except WorkflowError as exc:
                errors.append(str(exc))
            else:
                if payload.get("format") != "isoworld.worldpack":
                    errors.append("P10 requires a compatible worldpack")
                if not declared_hash_matches(payload):
                    errors.append("P10 worldpack content hash does not match its contents")
                if payload.get("content_hash") != reported_hash:
                    errors.append("worldpack_hash does not match the compiled file")
        if not isinstance(reported_hash, str) or not SHA256_PATTERN.fullmatch(reported_hash):
            errors.append("P10 requires a valid SHA-256 worldpack_hash")

    if current == "p13_asset_production":
        asset_manifest = _safe_deliverable(root, report.get("asset_manifest_path"))
        if asset_manifest is None or not asset_manifest.is_file():
            errors.append("P13 requires an existing asset_manifest_path inside the project")
        else:
            from worldforge.assets import validate_asset_manifest

            worldpack_path = _safe_deliverable(root, status.get("worldpack_path"))
            if worldpack_path is None or not worldpack_path.is_file():
                errors.append("P13 requires the canon-locked P10 worldpack")
            else:
                asset_issues = validate_asset_manifest(
                    asset_manifest,
                    profile="release",
                    worldpack_path=worldpack_path,
                )
                errors.extend(f"asset release: {issue}" for issue in asset_issues)
                renderpack_path = _safe_deliverable(root, report.get("renderpack_path"))
                if renderpack_path is None or not renderpack_path.is_file():
                    errors.append("P13 requires an existing renderpack_path inside the project")
                else:
                    try:
                        from isoworld.content.loader import load_worldpack
                        from isoworld.content.renderpack import load_renderpack

                        load_renderpack(renderpack_path, load_worldpack(worldpack_path))
                    except ValueError as exc:
                        errors.append(f"P13 renderpack is invalid: {exc}")

    if current == "p14_handoff":
        handoff = _safe_deliverable(root, report.get("handoff_path"))
        if handoff is None or not handoff.is_file() or handoff.stat().st_size == 0:
            errors.append("P14 requires an existing, non-empty handoff_path")
    return report, errors


def complete_phase(project_root: str | Path, report_path: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    status_path = root / ".worldforge/status.json"
    status = load_status(root)
    if status.get("current_phase") is None:
        raise WorkflowError("The workflow is already complete")
    report, errors = validate_phase_report(root, report_path)
    if errors:
        raise WorkflowError("; ".join(errors))

    current = status["current_phase"]
    index = PHASE_INDEX[current]
    completed = list(status.get("completed_phases", []))
    if current not in completed:
        completed.append(current)
    next_phase = PHASES[index + 1].id if index + 1 < len(PHASES) else None
    status["completed_phases"] = completed
    status["current_phase"] = next_phase
    status["revision"] = int(status.get("revision", 0)) + 1
    if current == "p10_canon_lock":
        status["canon_locked"] = True
        status["worldpack_hash"] = report["worldpack_hash"]
        status["worldpack_path"] = report["worldpack_path"]
    elif current == "p13_asset_production":
        status["asset_manifest"] = report["asset_manifest_path"]
        status["renderpack"] = report["renderpack_path"]

    report_target = root / ".worldforge/phase_reports" / f"{current}.json"
    _write_object(report_target, report)
    _write_object(status_path, status)
    return status


def reopen_phase(
    project_root: str | Path,
    phase_id: str,
    *,
    reason: str,
    approved_by: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    status_path = root / ".worldforge/status.json"
    status = load_status(root)
    if phase_id not in PHASE_INDEX:
        raise WorkflowError(f"Unknown phase: {phase_id}")
    completed = list(status.get("completed_phases", []))
    if phase_id not in completed:
        raise WorkflowError(f"Phase was not completed: {phase_id}")
    if not reason.strip() or not approved_by.strip():
        raise WorkflowError("reason and approved_by are required")

    reopen_index = PHASE_INDEX[phase_id]
    status["completed_phases"] = [item for item in completed if PHASE_INDEX[item] < reopen_index]
    status["current_phase"] = phase_id
    status["revision"] = int(status.get("revision", 0)) + 1
    if reopen_index <= PHASE_INDEX["p10_canon_lock"]:
        status["canon_locked"] = False
        status["worldpack_hash"] = None
        status["worldpack_path"] = None
        status["asset_manifest"] = None
        status["renderpack"] = None
    elif reopen_index <= PHASE_INDEX["p13_asset_production"]:
        status["asset_manifest"] = None
        status["renderpack"] = None

    log_path = root / ".worldforge/reopen_log.json"
    if log_path.exists():
        log = _read_object(log_path)
    else:
        log = {
            "format": "rpg-world-forge.reopen_log",
            "format_version": 1,
            "entries": [],
        }
    entries = log.get("entries")
    if not isinstance(entries, list):
        raise WorkflowError("reopen_log contains invalid entries")
    entries.append(
        {
            "revision": status["revision"],
            "phase": phase_id,
            "reason": reason.strip(),
            "approved_by": approved_by.strip(),
        }
    )
    _write_object(log_path, log)
    _write_object(status_path, status)
    return status
