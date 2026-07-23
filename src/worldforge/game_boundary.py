from __future__ import annotations

import ast
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from worldforge.game_boundary_policy import (
    DEFAULT_IGNORED_TOP_LEVEL,
    JSONPolicyError,
    load_strict_json_object,
    scan_python_capabilities,
    validate_dependency_provenance,
    validate_json_objects,
    validate_lexical_directory_root,
    validate_regular_tree,
)
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
        "bibles",
        "phase_reports",
        "receipts",
        "requests",
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
        "rpg-world-forge.asset_inventory",
        "rpg-world-forge.asset_license_record",
        "rpg-world-forge.asset_processing_receipt",
        "rpg-world-forge.asset_processing_recipe",
        "rpg-world-forge.asset_production_receipt",
        "rpg-world-forge.asset_production_request",
        "rpg-world-forge.asset_qa_report",
        "rpg-world-forge.asset_spec",
        "rpg-world-forge.asset_target",
        "rpg-world-forge.audio_bible",
        "rpg-world-forge.narrative_analysis",
        "rpg-world-forge.phase_catalog",
        "rpg-world-forge.phase_report",
        "rpg-world-forge.project",
        "rpg-world-forge.reopen_log",
        "rpg-world-forge.task_claim",
        "rpg-world-forge.workflow_status",
        "rpg-world-forge.visual_bible",
    }
)
FORBIDDEN_GAME_IMPORT_ROOTS = BANNED_IMPORT_ROOTS | {
    "blender_mcp",
    "bpy",
    "modly",
    "modly_cli_mcp",
    "worldforge",
}
FORBIDDEN_GAME_DISTRIBUTIONS = frozenset(
    {
        "anthropic",
        "blender-mcp",
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
        "modly-cli-mcp",
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
FORBIDDEN_GAME_JSON_KEYS = frozenset(
    {
        "api_key",
        "auth_token",
        "authorization",
        "credential",
        "executor",
        "mcp",
        "mcp_endpoint",
        "mcp_server",
        "mcp_servers",
        "orchestrator",
        "provider",
        "provider_id",
        "provider_name",
        "providers",
        "secret",
        "token",
        "weights",
        "weights_file",
        "weights_hash",
        "workflow",
        "workflow_file",
        "workflow_hash",
        "workflow_id",
    }
)
_CREDENTIAL_JSON_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "auth_token",
        "authorization",
        "bearer_token",
        "client_secret",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
_AUTHORING_JSON_VALUE_KEYS = frozenset(
    {
        "adapter",
        "backend",
        "client",
        "engine",
        "executor",
        "generator",
        "integration",
        "model",
        "orchestrator",
        "provider",
        "service",
        "tool",
        "transport",
    }
)
FORBIDDEN_GAME_WEIGHT_SUFFIXES = frozenset({".ckpt", ".gguf", ".pt", ".pth", ".safetensors"})

_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_NPM_ALIAS_NAME = re.compile(
    r"^npm:(?P<name>(?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*)(?:@.*)?$",
    re.IGNORECASE,
)
_INDIRECT_DEPENDENCY_PREFIXES = (
    "file:",
    "git:",
    "git+",
    "github:",
    "http:",
    "https:",
    "link:",
    "path:",
    "ssh:",
    "workspace:",
)
_AUTHORING_VALUE_PATTERN = re.compile(
    r"(?:^|[^a-z0-9])(?:"
    r"anthropic|blender[-_]mcp|cohere|diffusers|google[-_]genai|"
    r"huggingface|langchain|litellm|modly(?:[-_]cli[-_]mcp)?|"
    r"ollama|openai|sentence[-_]transformers|transformers|vertexai"
    r")(?:$|[^a-z0-9])",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{12,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
)
_CONTROL_DIRECTORIES_CASEFOLD = frozenset(
    value.casefold() for value in FORBIDDEN_GAME_CONTROL_DIRECTORIES
)
_ROOT_DIRECTORIES_CASEFOLD = frozenset(
    value.casefold() for value in FORBIDDEN_GAME_ROOT_DIRECTORIES
)
_FILENAMES_CASEFOLD = frozenset(value.casefold() for value in FORBIDDEN_GAME_FILENAMES)
_ALLOWED_PYRAY_SOURCE_PATHS = frozenset(
    {
        Path("isoworld/core/app.py"),
        Path("isoworld/render/pyray_3d.py"),
    }
)


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
    stripped = requirement.strip()
    npm_alias = _NPM_ALIAS_NAME.fullmatch(stripped)
    if npm_alias is not None:
        return re.sub(r"[-_.]+", "-", npm_alias.group("name")).lower()
    match = _REQUIREMENT_NAME.match(stripped)
    if match is None:
        return None
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _indirect_dependency(requirement: str) -> bool:
    normalized = requirement.strip().casefold()
    return (
        normalized.startswith(_INDIRECT_DEPENDENCY_PREFIXES)
        or normalized.startswith(("./", "../", "/"))
        or " @ " in normalized
    )


def _forbidden_command(value: str) -> bool:
    normalized = value.casefold().replace("\\", "/")
    return bool(
        _AUTHORING_VALUE_PATTERN.search(normalized)
        or "mcp://" in normalized
        or re.search(r"(?:^|[\s;&|])(?:npx|npm|pnpm|yarn)\s+[^\n]*(?:mcp|modly)", normalized)
    )


def _is_forbidden_import(module: str) -> bool:
    return any(
        module == root or module.startswith(f"{root}.") for root in FORBIDDEN_GAME_IMPORT_ROOTS
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

        if relative.suffix.casefold() == ".blend":
            reason = "Blender authoring file"
        elif relative.name.casefold() in _FILENAMES_CASEFOLD:
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
        relative = path.relative_to(base)
        try:
            source_relative = relative.relative_to("src")
        except ValueError:
            source_relative = None
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules = imported_modules(node)
            for module in modules:
                if _is_forbidden_import(module):
                    findings.append(
                        GameBoundaryFinding(
                            path=relative,
                            line=node.lineno,
                            rule="forbidden_game_import",
                            detail=module,
                        )
                    )
                    break
            if (
                source_relative is not None
                and source_relative not in _ALLOWED_PYRAY_SOURCE_PATHS
                and any(module.split(".", 1)[0] == "pyray" for module in modules)
            ):
                findings.append(
                    GameBoundaryFinding(
                        path=relative,
                        line=node.lineno,
                        rule="forbidden_game_import",
                        detail=(
                            "pyray import outside a game presentation adapter: "
                            f"{source_relative.as_posix()}"
                        ),
                    )
                )
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

    package_json = base / "package.json"
    script_findings: list[GameBoundaryFinding] = []
    if package_json.is_file():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GameBoundaryError(
                f"cannot parse game dependency manifest {package_json}: {exc}"
            ) from exc
        if isinstance(package, dict):
            for group in (
                "dependencies",
                "devDependencies",
                "optionalDependencies",
                "peerDependencies",
            ):
                values = package.get(group, {})
                if isinstance(values, dict):
                    for name, version in values.items():
                        if isinstance(name, str):
                            requirements.append((Path("package.json"), name))
                        if isinstance(version, str):
                            requirements.append((Path("package.json"), version))
            scripts = package.get("scripts", {})
            if isinstance(scripts, dict):
                for name, command in scripts.items():
                    if isinstance(command, str) and _forbidden_command(command):
                        script_findings.append(
                            GameBoundaryFinding(
                                path=Path("package.json"),
                                rule="forbidden_game_script",
                                detail=f"{name}: {command}",
                            )
                        )

    requirement_files = set(base.glob("requirements*.txt"))
    requirement_files.update(base.glob("requirements*.in"))
    requirement_files.update(base.glob("requirements*.lock"))
    requirements_directory = base / "requirements"
    if requirements_directory.is_dir():
        requirement_files.update(requirements_directory.glob("*.txt"))
        requirement_files.update(requirements_directory.glob("*.in"))
        requirement_files.update(requirements_directory.glob("*.lock"))
    pending_requirement_files = list(sorted(requirement_files))
    visited_requirement_files: set[Path] = set()
    while pending_requirement_files:
        requirement_path = pending_requirement_files.pop(0)
        try:
            resolved_requirement_path = requirement_path.resolve(strict=True)
            resolved_requirement_path.relative_to(base.resolve())
        except (OSError, ValueError):
            requirements.append((Path("requirements"), f"unsafe include: {requirement_path}"))
            continue
        if resolved_requirement_path in visited_requirement_files:
            continue
        visited_requirement_files.add(resolved_requirement_path)
        relative = requirement_path.relative_to(base)
        for raw_line in requirement_path.read_text(encoding="utf-8").splitlines():
            requirement = raw_line.split("#", 1)[0].strip()
            include_match = re.fullmatch(
                r"(?:-r|--requirement|-c|--constraint)(?:\s+|=)(.+)",
                requirement,
            )
            editable_match = re.fullmatch(
                r"(?:-e|--editable)(?:\s+|=)(.+)",
                requirement,
            )
            if include_match is not None:
                include = (requirement_path.parent / include_match.group(1).strip()).resolve()
                try:
                    include.relative_to(base.resolve())
                except ValueError:
                    requirements.append((relative, f"unsafe include: {requirement}"))
                else:
                    pending_requirement_files.append(include)
            elif editable_match is not None:
                requirements.append((relative, editable_match.group(1).strip()))
            elif requirement and not requirement.startswith("-"):
                requirements.append((relative, requirement))

    platform_lock = base / "platform.lock.json"
    if platform_lock.is_file():
        try:
            platform = json.loads(platform_lock.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GameBoundaryError(
                f"cannot parse game platform dependency lock {platform_lock}: {exc}"
            ) from exc
        locked = platform.get("locked_requirements", []) if isinstance(platform, dict) else []
        if isinstance(locked, list):
            requirements.extend(
                (Path("platform.lock.json"), requirement)
                for requirement in locked
                if isinstance(requirement, str)
            )

    findings: list[GameBoundaryFinding] = list(script_findings)
    seen: set[tuple[Path, str]] = set()
    for manifest_path, requirement in requirements:
        distribution = _normalized_distribution(requirement)
        key = (manifest_path, distribution or requirement)
        if (
            distribution in FORBIDDEN_GAME_DISTRIBUTIONS
            or _indirect_dependency(requirement)
            or _forbidden_command(requirement)
            or requirement.startswith("unsafe include:")
        ) and key not in seen:
            seen.add(key)
            findings.append(
                GameBoundaryFinding(
                    path=manifest_path,
                    rule="forbidden_game_dependency",
                    detail=str(requirement),
                )
            )
    return findings


def authoring_metadata_detail(value: object, *, parent_key: str | None = None) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in FORBIDDEN_GAME_JSON_KEYS:
                return f"authoring-only JSON field {key!r}"
            if normalized in _CREDENTIAL_JSON_KEYS or normalized.endswith(
                ("_api_key", "_authorization", "_password", "_private_key")
            ):
                return f"credential-like JSON field {key!r}"
            detail = authoring_metadata_detail(child, parent_key=normalized)
            if detail is not None:
                return detail
    elif isinstance(value, list):
        for child in value:
            detail = authoring_metadata_detail(child, parent_key=parent_key)
            if detail is not None:
                return detail
    elif isinstance(value, str):
        normalized = value.casefold().replace("\\", "/")
        path_parts = set(normalized.split("/"))
        if normalized.endswith(".blend") or normalized.startswith("mcp://"):
            return "authoring-only JSON value"
        if Path(normalized).suffix in FORBIDDEN_GAME_WEIGHT_SUFFIXES or "weights" in path_parts:
            return "model-weights JSON value"
        if path_parts & {"workflow", "workflows"}:
            return "authoring-workflow JSON value"
        if (
            parent_key in _AUTHORING_JSON_VALUE_KEYS and _AUTHORING_VALUE_PATTERN.search(normalized)
        ) or "mcp://" in normalized:
            return "provider or authoring-tool JSON value"
        if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
            return "credential-like JSON value"
    return None


def _authoring_format_findings(base: Path, blocked_roots: set[Path]) -> list[GameBoundaryFinding]:
    findings: list[GameBoundaryFinding] = []
    for path in sorted(base.rglob("*.json")):
        if not path.is_file() or any(blocked in path.parents for blocked in blocked_roots):
            continue
        relative = path.relative_to(base)
        if relative == Path("package.json") or relative == Path("platform.lock.json"):
            continue
        if "node_modules" in {part.casefold() for part in relative.parts}:
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
                    path=relative,
                    rule="forbidden_authoring_format",
                    detail=str(format_name),
                )
            )
            continue
        detail = authoring_metadata_detail(document)
        if detail is not None:
            findings.append(
                GameBoundaryFinding(
                    path=relative,
                    rule="forbidden_authoring_metadata",
                    detail=detail,
                )
            )
    return findings


def _selected_policy_json(base: Path) -> tuple[Path, ...]:
    selected = set(base.glob("*.json"))
    game_data = base / "game_data"
    if game_data.is_dir():
        selected.update(game_data.rglob("*.json"))
    return tuple(sorted(path for path in selected if path.is_file() or path.is_symlink()))


def _canonical_dependency_issues(base: Path) -> tuple[str, ...]:
    requirements = base / "requirements.lock"
    platform_path = base / "platform.lock.json"
    if not requirements.is_file() or not platform_path.is_file():
        return ()
    try:
        platform = load_strict_json_object(platform_path)
        requirement_bytes = requirements.read_bytes()
    except (JSONPolicyError, OSError):
        return ()
    locked = platform.get("locked_requirements")
    if not isinstance(locked, list) or not all(isinstance(item, str) for item in locked):
        return ()
    lock_bytes = ("\n".join(locked) + "\n").encode("utf-8")
    return validate_dependency_provenance(
        requirement_bytes,
        lock_bytes,
        exact=True,
    )


def _policy_finding(issue: str) -> GameBoundaryFinding:
    code, _, detail = issue.partition(":")
    if code.startswith("FS_"):
        relative = detail.split(":", 1)[0]
        return GameBoundaryFinding(Path(relative), "unsafe_game_path", issue)
    if code.startswith("JSON_"):
        return GameBoundaryFinding(Path(detail), "malformed_game_json", code)
    if code.startswith("DEPENDENCY_"):
        return GameBoundaryFinding(
            Path("requirements.lock"),
            "invalid_game_dependency_provenance",
            issue,
        )
    if code.startswith("PY_"):
        parts = issue.split(":", 4)
        relative = Path(parts[1]) if len(parts) > 1 else Path("src/game")
        line = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        rule = (
            "forbidden_game_capability"
            if code in {"PY_FORBIDDEN_IMPORT", "PY_FORBIDDEN_CALL", "PY_DYNAMIC_ESCAPE"}
            else "invalid_game_source"
        )
        return GameBoundaryFinding(relative, rule, issue, line)
    return GameBoundaryFinding(Path("."), "invalid_game_boundary", issue)


def _canonical_policy_findings(
    base: Path,
    *,
    suppress_dependency_issues: bool,
    regular_tree_issues: tuple[str, ...] | None = None,
) -> list[GameBoundaryFinding]:
    issues = list(
        regular_tree_issues
        if regular_tree_issues is not None
        else validate_regular_tree(
            base,
            ignored_top_level=DEFAULT_IGNORED_TOP_LEVEL,
        )
    )
    selected_json = _selected_policy_json(base)
    if selected_json:
        issues.extend(validate_json_objects(selected_json, base=base))
    dependency_issues = _canonical_dependency_issues(base)
    if not suppress_dependency_issues:
        issues.extend(dependency_issues)
    editable = base / "src/game"
    if editable.is_dir() or editable.is_symlink():
        issues.extend(scan_python_capabilities(editable, base=base))
    return [_policy_finding(issue) for issue in issues]


def audit_game_repository(root: str | Path) -> list[GameBoundaryFinding]:
    """Report authoring-control and AI dependencies that leaked into a game repository."""

    requested_base = Path(root)
    lexical_issues = validate_lexical_directory_root(requested_base)
    if lexical_issues:
        if lexical_issues == ("FS_SYMLINK:.",):
            return [_policy_finding(lexical_issues[0])]
        raise GameBoundaryError(f"game repository root is not a directory: {requested_base}")
    lexical_base = requested_base if requested_base.is_absolute() else Path.cwd() / requested_base
    try:
        base = lexical_base.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise GameBoundaryError(
            f"game repository root is not a stable directory: {requested_base}: {exc}"
        ) from exc

    regular_tree_issues = validate_regular_tree(
        base,
        ignored_top_level=DEFAULT_IGNORED_TOP_LEVEL,
    )
    link_issues = tuple(issue for issue in regular_tree_issues if issue.startswith("FS_SYMLINK:"))
    if link_issues:
        return sorted(
            (_policy_finding(issue) for issue in link_issues),
            key=lambda finding: (str(finding.path), finding.rule, finding.detail),
        )

    findings, blocked_roots = _structure_findings(base)
    findings.extend(_python_import_findings(base, blocked_roots))
    dependency_findings = _dependency_findings(base)
    findings.extend(dependency_findings)
    findings.extend(_authoring_format_findings(base, blocked_roots))
    findings.extend(
        _canonical_policy_findings(
            base,
            suppress_dependency_issues=bool(dependency_findings),
            regular_tree_issues=regular_tree_issues,
        )
    )
    unique = {
        (finding.path, finding.rule, finding.detail, finding.line): finding for finding in findings
    }
    return sorted(
        unique.values(),
        key=lambda finding: (str(finding.path), finding.line, finding.rule, finding.detail),
    )
