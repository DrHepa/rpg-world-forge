from __future__ import annotations

import ast
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from worldforge.runtime_audit import BANNED_IMPORT_ROOTS, imported_modules

FORBIDDEN_GAME_CONTROL_DIRECTORIES = frozenset(
    {
        ".agents",
        ".claude",
        ".codex",
        ".cursor",
        ".gemini",
        ".worldforge",
        "authoring",
        "phase_reports",
        "prompts",
    }
)
FORBIDDEN_GAME_ROOT_DIRECTORIES = frozenset({"source"})
FORBIDDEN_GAME_FILENAMES = frozenset(
    {
        ".cursorrules",
        "AGENTS.md",
        "AGENTS.override.md",
        "CLAUDE.md",
        "copilot-instructions.md",
        "GEMINI.md",
        "SKILL.md",
    }
)
FORBIDDEN_GAME_JSON_FORMATS = frozenset(
    {
        "isoworld.source_manifest",
        "rpg-world-forge.asset_manifest",
        "rpg-world-forge.asset_spec",
        "rpg-world-forge.narrative_analysis",
        "rpg-world-forge.phase_catalog",
        "rpg-world-forge.phase_report",
        "rpg-world-forge.project",
        "rpg-world-forge.reopen_log",
        "rpg-world-forge.task_claim",
        "rpg-world-forge.workflow_status",
    }
)
FORBIDDEN_GAME_IMPORT_ROOTS = BANNED_IMPORT_ROOTS | {"modly", "worldforge"}
FORBIDDEN_GAME_DISTRIBUTIONS = frozenset(
    {
        "anthropic",
        "cohere",
        "diffusers",
        "google-genai",
        "google-generativeai",
        "groq",
        "huggingface-hub",
        "langchain",
        "litellm",
        "llama-cpp-python",
        "mistralai",
        "modly",
        "mlx",
        "ollama",
        "openai",
        "rpg-world-forge",
        "sentence-transformers",
        "tensorflow",
        "torch",
        "transformers",
        "vertexai",
        "worldforge",
    }
)

_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_CONTROL_DIRECTORIES_CASEFOLD = frozenset(
    value.casefold() for value in FORBIDDEN_GAME_CONTROL_DIRECTORIES
)
_ROOT_DIRECTORIES_CASEFOLD = frozenset(
    value.casefold() for value in FORBIDDEN_GAME_ROOT_DIRECTORIES
)
_FILENAMES_CASEFOLD = frozenset(value.casefold() for value in FORBIDDEN_GAME_FILENAMES)


class GameBoundaryError(ValueError):
    """Raised when a game repository cannot be audited safely."""


@dataclass(frozen=True, slots=True)
class GameBoundaryFinding:
    path: Path
    rule: str
    detail: str
    line: int = 0

    def __str__(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else str(self.path)
        return f"{location}: {self.rule}: {self.detail}"


def _normalized_distribution(requirement: str) -> str | None:
    match = _REQUIREMENT_NAME.match(requirement.strip())
    if match is None:
        return None
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _is_forbidden_import(module: str) -> bool:
    return any(
        module == root or module.startswith(f"{root}.")
        for root in FORBIDDEN_GAME_IMPORT_ROOTS
    )


def _structure_findings(base: Path) -> tuple[list[GameBoundaryFinding], set[Path]]:
    findings: list[GameBoundaryFinding] = []
    blocked_roots: set[Path] = set()

    for path in sorted(base.rglob("*")):
        if any(blocked == path or blocked in path.parents for blocked in blocked_roots):
            continue
        relative = path.relative_to(base)
        reason: str | None = None
        blocked = path

        if relative.name.casefold() in _FILENAMES_CASEFOLD:
            reason = "agent control file"
        elif relative.parts and relative.parts[0].casefold() in _ROOT_DIRECTORIES_CASEFOLD:
            blocked = base / relative.parts[0]
            reason = "world-authoring source root"
        else:
            for index, component in enumerate(relative.parts):
                if component.casefold() in _CONTROL_DIRECTORIES_CASEFOLD:
                    blocked = base.joinpath(*relative.parts[: index + 1])
                    reason = "forge/world-authoring control directory"
                    break

        if reason is None:
            continue
        blocked_roots.add(blocked)
        findings.append(
            GameBoundaryFinding(
                path=blocked.relative_to(base),
                rule="forbidden_game_path",
                detail=reason,
            )
        )

    return findings, blocked_roots


def _python_import_findings(base: Path, blocked_roots: set[Path]) -> list[GameBoundaryFinding]:
    findings: list[GameBoundaryFinding] = []
    for path in sorted(base.rglob("*.py")):
        if not path.is_file() or any(blocked in path.parents for blocked in blocked_roots):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for module in imported_modules(node):
                if _is_forbidden_import(module):
                    findings.append(
                        GameBoundaryFinding(
                            path=path.relative_to(base),
                            line=node.lineno,
                            rule="forbidden_game_import",
                            detail=module,
                        )
                    )
                    break
    return findings


def _dependency_findings(base: Path) -> list[GameBoundaryFinding]:
    path = base / "pyproject.toml"
    requirements: list[tuple[Path, str]] = []
    if path.is_file():
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise GameBoundaryError(f"cannot parse game dependency manifest {path}: {exc}") from exc

        project = document.get("project", {})
        if isinstance(project, dict):
            for requirement in project.get("dependencies", []):
                if isinstance(requirement, str):
                    requirements.append((Path("pyproject.toml"), requirement))
            optional = project.get("optional-dependencies", {})
            if isinstance(optional, dict):
                for values in optional.values():
                    if isinstance(values, list):
                        requirements.extend(
                            (Path("pyproject.toml"), value)
                            for value in values
                            if isinstance(value, str)
                        )

        groups = document.get("dependency-groups", {})
        if isinstance(groups, dict):
            for values in groups.values():
                if isinstance(values, list):
                    requirements.extend(
                        (Path("pyproject.toml"), value)
                        for value in values
                        if isinstance(value, str)
                    )

        tool = document.get("tool", {})
        if isinstance(tool, dict):
            poetry = tool.get("poetry", {})
            if isinstance(poetry, dict):
                poetry_dependencies = poetry.get("dependencies", {})
                if isinstance(poetry_dependencies, dict):
                    requirements.extend(
                        (Path("pyproject.toml"), name)
                        for name in poetry_dependencies
                        if name != "python"
                    )
                poetry_groups = poetry.get("group", {})
                if isinstance(poetry_groups, dict):
                    for group in poetry_groups.values():
                        if not isinstance(group, dict):
                            continue
                        dependencies = group.get("dependencies", {})
                        if isinstance(dependencies, dict):
                            requirements.extend(
                                (Path("pyproject.toml"), name) for name in dependencies
                            )
            for tool_name in ("pdm", "uv"):
                tool_config = tool.get(tool_name, {})
                if not isinstance(tool_config, dict):
                    continue
                for key in ("dev-dependencies", "dependencies"):
                    values = tool_config.get(key, [])
                    if isinstance(values, list):
                        requirements.extend(
                            (Path("pyproject.toml"), value)
                            for value in values
                            if isinstance(value, str)
                        )

    requirement_files = set(base.glob("requirements*.txt"))
    requirement_files.update(base.glob("requirements*.in"))
    requirements_directory = base / "requirements"
    if requirements_directory.is_dir():
        requirement_files.update(requirements_directory.glob("*.txt"))
        requirement_files.update(requirements_directory.glob("*.in"))
    for requirement_path in sorted(requirement_files):
        relative = requirement_path.relative_to(base)
        for raw_line in requirement_path.read_text(encoding="utf-8").splitlines():
            requirement = raw_line.split("#", 1)[0].strip()
            if requirement and not requirement.startswith("-"):
                requirements.append((relative, requirement))

    findings: list[GameBoundaryFinding] = []
    seen: set[tuple[Path, str]] = set()
    for manifest_path, requirement in requirements:
        distribution = _normalized_distribution(requirement)
        key = (manifest_path, distribution or requirement)
        if distribution in FORBIDDEN_GAME_DISTRIBUTIONS and key not in seen:
            seen.add(key)
            findings.append(
                GameBoundaryFinding(
                    path=manifest_path,
                    rule="forbidden_game_dependency",
                    detail=str(requirement),
                )
            )
    return findings


def _authoring_format_findings(
    base: Path, blocked_roots: set[Path]
) -> list[GameBoundaryFinding]:
    findings: list[GameBoundaryFinding] = []
    for path in sorted(base.rglob("*.json")):
        if not path.is_file() or any(blocked in path.parents for blocked in blocked_roots):
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(document, dict):
            continue
        format_name = document.get("format")
        if format_name in FORBIDDEN_GAME_JSON_FORMATS:
            findings.append(
                GameBoundaryFinding(
                    path=path.relative_to(base),
                    rule="forbidden_authoring_format",
                    detail=str(format_name),
                )
            )
    return findings


def audit_game_repository(root: str | Path) -> list[GameBoundaryFinding]:
    """Report authoring-control and AI dependencies that leaked into a game repository."""

    base = Path(root)
    if not base.is_dir():
        raise GameBoundaryError(f"game repository root is not a directory: {base}")

    findings, blocked_roots = _structure_findings(base)
    findings.extend(_python_import_findings(base, blocked_roots))
    findings.extend(_dependency_findings(base))
    findings.extend(_authoring_format_findings(base, blocked_roots))
    return sorted(findings, key=lambda finding: (str(finding.path), finding.line, finding.rule))
