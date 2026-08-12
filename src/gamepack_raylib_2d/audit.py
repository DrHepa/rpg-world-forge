"""Static fail-closed boundary audit for the bounded adapter package."""

from __future__ import annotations

import ast
import os
from pathlib import Path

_ALLOWED_ROOTS = frozenset(
    {
        "__future__",
        "ast",
        "collections",
        "copy",
        "ctypes",
        "dataclasses",
        "gamepack_raylib_2d",
        "gamepack_runtime",
        "hashlib",
        "importlib",
        "json",
        "math",
        "os",
        "pathlib",
        "stat",
        "struct",
        "sys",
        "types",
        "typing",
        "unicodedata",
    }
)
_DYNAMIC_IMPORT_NAME = "import_module"
_NATIVE_BINDING = "py" + "ray"
_FORBIDDEN_CALLS = frozenset(
    {
        "__import__",
        "compile",
        "eval",
        "exec",
        "open",
        "system",
    }
)


class AdapterBoundaryError(ValueError):
    """Raised when the adapter source tree cannot be audited exactly."""


def _violation(path: Path, line: int, reason: str) -> dict[str, object]:
    return {
        "path": path.name,
        "line": line,
        "reason": reason,
    }


def audit_adapter_boundary(root: str | Path) -> dict[str, object]:
    """Return deterministic violations for one exact flat Python package."""

    package_root = Path(os.path.abspath(os.fspath(root)))
    if package_root.name != "gamepack_raylib_2d" or not package_root.is_dir():
        raise AdapterBoundaryError("adapter package root has an unexpected identity")
    files = sorted(package_root.glob("*.py"), key=lambda item: item.name.encode("utf-8"))
    if not files or any(path.is_symlink() or not path.is_file() for path in files):
        raise AdapterBoundaryError("adapter package contains an unsafe Python source")
    violations: list[dict[str, object]] = []
    for path in files:
        try:
            source = path.read_text(encoding="utf-8", errors="strict")
            tree = ast.parse(source, filename=os.fspath(path))
        except (OSError, SyntaxError, UnicodeError) as exc:
            raise AdapterBoundaryError(f"could not inspect {path.name}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_name = alias.name.split(".", 1)[0]
                    if root_name not in _ALLOWED_ROOTS:
                        violations.append(
                            _violation(path, node.lineno, f"forbidden import {alias.name}")
                        )
            elif isinstance(node, ast.ImportFrom):
                root_name = (node.module or "").split(".", 1)[0]
                if node.level or root_name not in _ALLOWED_ROOTS:
                    violations.append(
                        _violation(
                            path,
                            node.lineno,
                            f"forbidden import {node.module or '<relative>'}",
                        )
                    )
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                    violations.append(
                        _violation(path, node.lineno, f"forbidden call {node.func.id}")
                    )
                if isinstance(node.func, ast.Attribute) and node.func.attr == _DYNAMIC_IMPORT_NAME:
                    exact_native_import = (
                        path.name == "backend.py"
                        and len(node.args) == 1
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == _NATIVE_BINDING
                        and not node.keywords
                    )
                    if not exact_native_import:
                        violations.append(_violation(path, node.lineno, "forbidden dynamic import"))
    violations.sort(key=lambda item: (str(item["path"]).encode("utf-8"), int(item["line"])))
    return {
        "package": package_root.name,
        "files": [path.name for path in files],
        "violations": violations,
    }


__all__ = ["AdapterBoundaryError", "audit_adapter_boundary"]
