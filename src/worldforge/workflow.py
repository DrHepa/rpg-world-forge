from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.portability import is_portable_path_component
from worldforge.world_lock import exclusive_world_lifecycle


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
PHASE_ID_PATTERN = re.compile(r"^p[0-9]{2}_[a-z0-9_]+$")
MAX_CONTROL_BYTES = 4 * 1024 * 1024
MAX_WORLDPACK_BYTES = 64 * 1024 * 1024
PHASE_REPORT_REQUIRED_KEYS = frozenset(
    {
        "blockers",
        "decisions",
        "deliverables",
        "format",
        "format_version",
        "phase",
        "reviewed_by",
        "status",
        "summary",
        "validations",
    }
)
PHASE_REPORT_ALLOWED_KEYS = PHASE_REPORT_REQUIRED_KEYS | {
    "asset_manifest_path",
    "handoff_path",
    "renderpack_path",
    "worldpack_hash",
    "worldpack_path",
}
PHASE_REPORT_PATH_KEYS = (
    "asset_manifest_path",
    "handoff_path",
    "renderpack_path",
    "worldpack_path",
)


class WorkflowError(ValueError):
    """Raised when a workflow transition or report is invalid."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _read_object(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_CONTROL_BYTES:
            raise OSError("not a safe standalone control file")
        value = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WorkflowError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError(f"{path} must contain an object")
    return value


_replace_file = os.replace


def _encoded_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _stage_bytes(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _prepare_transaction_target(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise WorkflowError(f"Transaction target escapes the world project: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise WorkflowError(f"Transaction target is not normalized: {path}")

    parent = root
    for part in relative.parts[:-1]:
        parent = parent / part
        if parent.is_symlink():
            raise WorkflowError(f"Transaction parent cannot be a symbolic link: {parent}")
        if parent.exists():
            if not parent.is_dir():
                raise WorkflowError(f"Transaction parent is not a directory: {parent}")
        else:
            parent.mkdir()
    if path.is_symlink():
        raise WorkflowError(f"Transaction target cannot be a symbolic link: {path}")


def _commit_json_transaction(root: Path, updates: dict[Path, object]) -> None:
    """Publish related controls together and restore all observed failures."""

    root = root.resolve()
    originals: dict[Path, bytes | None] = {}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path in updates:
            _prepare_transaction_target(root, path)
        for path, value in updates.items():
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise WorkflowError(f"Transaction target is not a regular file: {path}")
                originals[path] = path.read_bytes()
            else:
                originals[path] = None
            staged[path] = _stage_bytes(path, _encoded_json(value))
        for path, temporary in staged.items():
            _replace_file(temporary, path)
            replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            original = originals[path]
            if original is None:
                path.unlink(missing_ok=True)
            else:
                restore = _stage_bytes(path, original)
                os.replace(restore, path)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


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


def validate_workflow_status(
    status: dict[str, Any],
    *,
    expected_world_id: str | None = None,
) -> None:
    """Validate the phase machine and its release evidence without doing I/O."""

    status_version = status.get("format_version")
    if (
        status.get("format") != "rpg-world-forge.workflow_status"
        or isinstance(status_version, bool)
        or not isinstance(status_version, int)
        or status_version != 1
    ):
        raise WorkflowError("Unsupported workflow status")

    world_id = status.get("world_id")
    if not isinstance(world_id, str) or not world_id.strip():
        raise WorkflowError("Workflow status has an invalid world_id")
    if expected_world_id is not None and world_id != expected_world_id:
        raise WorkflowError("Project, world, and workflow status IDs do not match")

    completed = status.get("completed_phases")
    current = status.get("current_phase")
    if not isinstance(completed, list) or not all(
        isinstance(item, str) and item in PHASE_INDEX for item in completed
    ):
        raise WorkflowError("Workflow status has invalid completed phases")
    if current is not None and (not isinstance(current, str) or current not in PHASE_INDEX):
        raise WorkflowError("Workflow status has an invalid current phase")
    expected_completed = [phase.id for phase in PHASES[: len(completed)]]
    if completed != expected_completed:
        raise WorkflowError("Workflow completed phases must be a unique ordered prefix")
    expected_current = PHASES[len(completed)].id if len(completed) < len(PHASES) else None
    if current != expected_current:
        raise WorkflowError("Workflow current phase must follow the completed phase prefix")

    revision = status.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise WorkflowError("Workflow status revision must be a non-negative integer")
    if not isinstance(status.get("canon_locked"), bool):
        raise WorkflowError("Workflow status canon_locked must be boolean")

    worldpack_hash = status.get("worldpack_hash")
    worldpack_path = status.get("worldpack_path")
    if (worldpack_hash is None) != (worldpack_path is None):
        raise WorkflowError("Worldpack release metadata must be complete or empty")
    if worldpack_hash is not None and (
        not isinstance(worldpack_hash, str) or SHA256_PATTERN.fullmatch(worldpack_hash) is None
    ):
        raise WorkflowError("Workflow status has an invalid worldpack hash")
    if worldpack_path is not None and (
        not isinstance(worldpack_path, str) or not worldpack_path.strip()
    ):
        raise WorkflowError("Workflow status has an invalid worldpack path")
    if status["canon_locked"] and worldpack_hash is None:
        raise WorkflowError("Canon-locked status requires complete worldpack metadata")
    canon_completed = "p10_canon_lock" in completed
    if status["canon_locked"] != canon_completed or (worldpack_hash is not None) != canon_completed:
        raise WorkflowError("P10 completion, canon lock, and worldpack metadata must agree")

    for first, second, label in (
        ("asset_manifest", "renderpack", "asset release"),
        ("release_hash", "release_package", "world bundle"),
    ):
        left, right = status.get(first), status.get(second)
        if (left is None) != (right is None):
            raise WorkflowError(f"{label} metadata must be complete or empty")
        for field, value in ((first, left), (second, right)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise WorkflowError(f"Workflow status has an invalid {field}")
    asset_completed = "p13_asset_production" in completed
    if (status.get("asset_manifest") is not None) != asset_completed:
        raise WorkflowError("P13 completion and asset release metadata must agree")
    release_hash = status.get("release_hash")
    if release_hash is not None and SHA256_PATTERN.fullmatch(release_hash) is None:
        raise WorkflowError("Workflow status has an invalid release hash")
    compatibility = status.get("compatibility_report")
    if compatibility is not None and (
        not isinstance(compatibility, str) or not compatibility.strip()
    ):
        raise WorkflowError("Workflow status has an invalid compatibility report")


def _load_status_unlocked(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root)
    status = _read_object(root / ".worldforge/status.json")
    from worldforge.world_lifecycle import _load_canonical_status_unlocked

    return _load_canonical_status_unlocked(root, status)


def load_status(project_root: str | Path) -> dict[str, Any]:
    with exclusive_world_lifecycle(project_root, error_type=WorkflowError) as root:
        return _load_status_unlocked(root)


def describe_status(project_root: str | Path) -> str:
    status = load_status(project_root)
    current = status.get("current_phase")
    title = "complete" if current is None else PHASES[PHASE_INDEX[current]].title
    return (
        f"world={status['world_id']} phase={current or 'complete'} "
        f"title={title!r} completed={len(status.get('completed_phases', []))}/{len(PHASES)} "
        f"revision={status.get('revision', 0)}"
    )


def _normalized_project_path(relative: Any) -> PurePosixPath | None:
    if not isinstance(relative, str) or not relative:
        return None
    if "\\" in relative:
        return None
    normalized = PurePosixPath(relative)
    if (
        normalized.is_absolute()
        or normalized.as_posix() != relative
        or not normalized.parts
        or any(not is_portable_path_component(part) for part in normalized.parts)
    ):
        return None
    return normalized


def _safe_deliverable(root: Path, relative: Any) -> Path | None:
    normalized = _normalized_project_path(relative)
    if normalized is None:
        return None
    current = root
    for part in normalized.parts[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            return None
        if not stat.S_ISDIR(info.st_mode) or current.is_symlink():
            return None
    target = current / normalized.parts[-1]
    try:
        info = target.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or target.is_symlink() or info.st_nlink != 1:
        return None
    return target


def _validate_phase_report_contract(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    keys = set(report)
    missing = sorted(PHASE_REPORT_REQUIRED_KEYS - keys)
    unknown = sorted(keys - PHASE_REPORT_ALLOWED_KEYS)
    if missing:
        errors.append(f"phase report is missing fields: {', '.join(missing)}")
    if unknown:
        errors.append(f"phase report contains unknown fields: {', '.join(unknown)}")

    if report.get("format") != "rpg-world-forge.phase_report":
        errors.append("unknown phase-report format")
    report_version = report.get("format_version")
    if (
        isinstance(report_version, bool)
        or not isinstance(report_version, int)
        or report_version != 1
    ):
        errors.append("unsupported phase-report version")
    phase = report.get("phase")
    if not isinstance(phase, str) or PHASE_ID_PATTERN.fullmatch(phase) is None:
        errors.append("phase must use the published phase ID format")
    if report.get("status") != "ready":
        errors.append("status must be ready")
    if not isinstance(report.get("summary"), str) or not report["summary"].strip():
        errors.append("summary is required")

    deliverables = report.get("deliverables")
    if not isinstance(deliverables, list) or not deliverables:
        errors.append("deliverables must contain at least one file")
    elif not all(_normalized_project_path(item) is not None for item in deliverables):
        errors.append("deliverables must use normalized relative POSIX project paths")

    decisions = report.get("decisions")
    if not isinstance(decisions, list) or not all(isinstance(item, str) for item in decisions):
        errors.append("decisions must be a list of strings")
    blockers = report.get("blockers")
    if not isinstance(blockers, list):
        errors.append("blockers must be a list")
    elif blockers:
        errors.append("the phase still contains blockers")

    validations = report.get("validations")
    if not isinstance(validations, list) or not validations:
        errors.append("validations must contain at least one check")
    else:
        for index, validation in enumerate(validations):
            if not isinstance(validation, dict):
                errors.append(f"validation {index} must be an object")
                continue
            unknown_validation_keys = sorted(set(validation) - {"evidence", "name", "passed"})
            if unknown_validation_keys:
                errors.append(
                    f"validation {index} contains unknown fields: "
                    f"{', '.join(unknown_validation_keys)}"
                )
            if not isinstance(validation.get("name"), str) or not validation["name"].strip():
                errors.append(f"validation {index} requires a name")
            if validation.get("passed") is not True:
                errors.append(f"validation {index} must declare passed=true")
            if (
                not isinstance(validation.get("evidence"), str)
                or not validation["evidence"].strip()
            ):
                errors.append(f"validation {index} requires evidence")

    if not isinstance(report.get("reviewed_by"), str) or not report["reviewed_by"].strip():
        errors.append("reviewed_by is required")
    for key in PHASE_REPORT_PATH_KEYS:
        if key in report and _normalized_project_path(report[key]) is None:
            errors.append(f"{key} must be a normalized relative POSIX project path")
    reported_hash = report.get("worldpack_hash")
    if "worldpack_hash" in report and (
        not isinstance(reported_hash, str) or SHA256_PATTERN.fullmatch(reported_hash) is None
    ):
        errors.append("worldpack_hash must be a lowercase SHA-256 digest")
    return errors


def _load_worldpack_snapshot(path: Path):
    """Load one immutable snapshot through the complete runtime worldpack validator."""

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > MAX_WORLDPACK_BYTES
        ):
            raise OSError("not a safe standalone worldpack file")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            payload = source.read(MAX_WORLDPACK_BYTES + 1)
        if len(payload) > MAX_WORLDPACK_BYTES:
            raise OSError("worldpack exceeds the 64 MiB limit")
    except OSError as exc:
        raise WorkflowError(f"Could not read {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        with tempfile.TemporaryDirectory(prefix="worldforge-worldpack-") as directory:
            snapshot = Path(directory) / "worldpack.json"
            snapshot.write_bytes(payload)
            from isoworld.content.loader import load_worldpack

            pack = load_worldpack(snapshot)
            raw = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WorkflowError(f"Worldpack validation failed: {exc}") from exc
    if not isinstance(raw, dict):
        raise WorkflowError("Worldpack validation failed: root must be an object")
    return raw, pack


def _worldpack_binding_errors(
    raw: dict[str, Any],
    pack: Any,
    status: dict[str, Any],
    *,
    phase: str,
) -> list[str]:
    errors: list[str] = []
    if pack.world_id != status["world_id"]:
        errors.append(f"{phase} worldpack world_id does not match the canonical world project")
    world = raw.get("world")
    pack_version = world.get("version") if isinstance(world, dict) else None
    if pack_version != status.get("world_version"):
        errors.append(f"{phase} worldpack version does not match the canonical world project")
    return errors


def validate_phase_report(
    project_root: str | Path,
    report_path: str | Path,
    *,
    _status: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    root = Path(project_root).resolve()
    status = load_status(root) if _status is None else _status
    report = _read_object(Path(report_path))
    errors = _validate_phase_report_contract(report)
    current = status.get("current_phase")
    if report.get("phase") != current:
        errors.append(f"report targets {report.get('phase')} but current phase is {current}")

    deliverables = report.get("deliverables")
    if isinstance(deliverables, list):
        for relative in deliverables:
            target = _safe_deliverable(root, relative)
            if target is None:
                errors.append(f"deliverable has an unsafe path: {relative}")
            elif not target.is_file() or target.stat().st_size == 0:
                errors.append(f"deliverable is missing or empty: {relative}")

    if current == "p10_canon_lock":
        worldpack = _safe_deliverable(root, report.get("worldpack_path"))
        reported_hash = report.get("worldpack_hash")
        if worldpack is None or not worldpack.is_file():
            errors.append("P10 requires an existing worldpack_path inside the project")
        else:
            try:
                payload, loaded_pack = _load_worldpack_snapshot(worldpack)
            except WorkflowError as exc:
                errors.append(f"P10 {exc}")
            else:
                errors.extend(
                    _worldpack_binding_errors(
                        payload,
                        loaded_pack,
                        status,
                        phase="P10",
                    )
                )
                if loaded_pack.content_hash != reported_hash:
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
                try:
                    worldpack_raw, locked_pack = _load_worldpack_snapshot(worldpack_path)
                except WorkflowError as exc:
                    errors.append(f"P13 {exc}")
                else:
                    errors.extend(
                        _worldpack_binding_errors(
                            worldpack_raw,
                            locked_pack,
                            status,
                            phase="P13",
                        )
                    )
                    if locked_pack.content_hash != status.get("worldpack_hash"):
                        errors.append("P13 worldpack content hash does not match workflow status")
                    else:
                        asset_issues = validate_asset_manifest(
                            asset_manifest,
                            profile="release",
                            worldpack_path=worldpack_path,
                        )
                        errors.extend(f"asset release: {issue}" for issue in asset_issues)
                        renderpack_path = _safe_deliverable(root, report.get("renderpack_path"))
                        if renderpack_path is None or not renderpack_path.is_file():
                            errors.append(
                                "P13 requires an existing renderpack_path inside the project"
                            )
                        else:
                            try:
                                from isoworld.content.renderpack import load_renderpack

                                load_renderpack(renderpack_path, locked_pack)
                            except ValueError as exc:
                                errors.append(f"P13 renderpack is invalid: {exc}")

    if current == "p14_handoff":
        handoff = _safe_deliverable(root, report.get("handoff_path"))
        if handoff is None or not handoff.is_file() or handoff.stat().st_size == 0:
            errors.append("P14 requires an existing, non-empty handoff_path")
    return report, errors


def complete_phase(project_root: str | Path, report_path: str | Path) -> dict[str, Any]:
    with exclusive_world_lifecycle(project_root, error_type=WorkflowError) as root:
        return _complete_phase_locked(root, report_path)


def _complete_phase_locked(root: Path, report_path: str | Path) -> dict[str, Any]:
    status_path = root / ".worldforge/status.json"
    status = _load_status_unlocked(root)
    if status.get("current_phase") is None:
        raise WorkflowError("The workflow is already complete")
    report, errors = validate_phase_report(root, report_path, _status=status)
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
    _commit_json_transaction(root, {report_target: report, status_path: status})
    return status


def reopen_phase(
    project_root: str | Path,
    phase_id: str,
    *,
    reason: str,
    approved_by: str,
) -> dict[str, Any]:
    with exclusive_world_lifecycle(project_root, error_type=WorkflowError) as root:
        return _reopen_phase_locked(
            root,
            phase_id,
            reason=reason,
            approved_by=approved_by,
        )


def _reopen_phase_locked(
    root: Path,
    phase_id: str,
    *,
    reason: str,
    approved_by: str,
) -> dict[str, Any]:
    status_path = root / ".worldforge/status.json"
    status = _load_status_unlocked(root)
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
    for field in ("compatibility_report", "release_hash", "release_package"):
        status[field] = None

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
    _commit_json_transaction(root, {log_path: log, status_path: status})
    return status
