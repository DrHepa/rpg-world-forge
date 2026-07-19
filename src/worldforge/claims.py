from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from worldforge.validation import ID_PATTERN

ACTIVE_STATUSES = {"claimed", "blocked", "ready_for_integration"}
KNOWN_STATUSES = ACTIVE_STATUSES | {"integrated", "cancelled"}


@dataclass(frozen=True, slots=True)
class ClaimIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def _read_claim(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _normalize_owned_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = PurePosixPath(value.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        return None
    return str(normalized)


def _paths_overlap(first: str, second: str) -> bool:
    left = PurePosixPath(first).parts
    right = PurePosixPath(second).parts
    common = min(len(left), len(right))
    return left[:common] == right[:common]


def validate_claims(project_root: str | Path) -> list[ClaimIssue]:
    claims_root = Path(project_root) / ".worldforge/claims"
    issues: list[ClaimIssue] = []
    ownership: list[tuple[str, str, Path]] = []
    for path in sorted(claims_root.glob("*.json")):
        claim = _read_claim(path)
        relative = str(path.relative_to(Path(project_root)))
        if claim is None:
            issues.append(ClaimIssue(relative, "invalid JSON"))
            continue
        if claim.get("format") != "rpg-world-forge.task_claim":
            issues.append(ClaimIssue(relative, "unknown format"))
        if claim.get("format_version") != 1:
            issues.append(ClaimIssue(relative, "unsupported version"))
        task_id = claim.get("task_id")
        if not isinstance(task_id, str) or not ID_PATTERN.fullmatch(task_id):
            issues.append(ClaimIssue(f"{relative}/task_id", "invalid ID"))
            task_id = relative
        status = claim.get("status")
        if status not in KNOWN_STATUSES:
            issues.append(ClaimIssue(f"{relative}/status", "unknown status"))
        for field in ("owner", "role", "objective", "handoff_path"):
            if not isinstance(claim.get(field), str) or not claim[field].strip():
                issues.append(ClaimIssue(f"{relative}/{field}", "value is required"))
        for field in ("non_goals", "owned_paths", "read_inputs", "dependencies", "validation"):
            if not isinstance(claim.get(field), list):
                issues.append(ClaimIssue(f"{relative}/{field}", "must be a list"))
        if status in ACTIVE_STATUSES and isinstance(claim.get("owned_paths"), list):
            if not claim["owned_paths"]:
                issues.append(ClaimIssue(f"{relative}/owned_paths", "cannot be empty"))
            for raw_owned in claim["owned_paths"]:
                owned = _normalize_owned_path(raw_owned)
                if owned is None:
                    issues.append(
                        ClaimIssue(f"{relative}/owned_paths", f"unsafe path: {raw_owned}")
                    )
                else:
                    ownership.append((task_id, owned, path))

    for index, (task_id, owned, path) in enumerate(ownership):
        for other_task, other_owned, other_path in ownership[index + 1 :]:
            if task_id != other_task and _paths_overlap(owned, other_owned):
                issues.append(
                    ClaimIssue(
                        str(path.relative_to(Path(project_root))),
                        f"ownership overlaps {other_path.name}: {owned} <> {other_owned}",
                    )
                )
    return issues
