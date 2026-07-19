from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

BANNED_IMPORT_ROOTS = {
    "anthropic",
    "cohere",
    "diffusers",
    "google.genai",
    "google.generativeai",
    "groq",
    "huggingface_hub",
    "langchain",
    "litellm",
    "llama_cpp",
    "mistralai",
    "mlx",
    "modly",
    "ollama",
    "openai",
    "sentence_transformers",
    "tensorflow",
    "torch",
    "transformers",
    "vertexai",
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


def imported_modules(node: ast.AST) -> list[str]:
    """Return statically identifiable modules imported by one AST node."""

    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom) and node.module:
        return [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
    if not isinstance(node, ast.Call) or not node.args:
        return []

    function = node.func
    is_dynamic_import = (
        isinstance(function, ast.Name) and function.id in {"__import__", "import_module"}
    ) or (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "importlib"
        and function.attr == "import_module"
    )
    first_argument = node.args[0]
    if is_dynamic_import and isinstance(first_argument, ast.Constant):
        if isinstance(first_argument.value, str):
            return [first_argument.value]
    return []


def audit_runtime(root: str | Path) -> list[AuditFinding]:
    base = Path(root)
    findings: list[AuditFinding] = []
    for path in sorted(base.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for module in imported_modules(node):
                if _is_banned(module):
                    findings.append(AuditFinding(path=path, line=node.lineno, module=module))
                    break
    return findings
