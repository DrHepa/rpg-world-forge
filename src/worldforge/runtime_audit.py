from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

BANNED_IMPORT_ROOTS = {
    "anthropic",
    "google.generativeai",
    "langchain",
    "litellm",
    "llama_cpp",
    "ollama",
    "openai",
    "torch",
    "transformers",
}


@dataclass(frozen=True, slots=True)
class AuditFinding:
    path: Path
    line: int
    module: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: forbidden runtime AI import: {self.module}"


def _is_banned(module: str) -> bool:
    return any(
        module == banned or module.startswith(f"{banned}.") for banned in BANNED_IMPORT_ROOTS
    )


def audit_runtime(root: str | Path) -> list[AuditFinding]:
    base = Path(root)
    findings: list[AuditFinding] = []
    for path in sorted(base.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if _is_banned(module):
                    findings.append(AuditFinding(path=path, line=node.lineno, module=module))
    return findings
