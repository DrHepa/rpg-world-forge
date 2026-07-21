"""Portable stdlib-only boundary checks shared with generated games."""

from __future__ import annotations

import ast
import json
import math
import os
import re
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

POLICY_API_VERSION = "1"
MAX_JSON_BYTES = 16 * 1024 * 1024
DEFAULT_IGNORED_TOP_LEVEL = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "dist",
        "package_output",
        "saves",
        "screenshots",
    }
)
PathLike = str | os.PathLike[str]
DependencyData = str | bytes


class JSONPolicyError(ValueError):
    """Strict JSON failure with a stable machine-readable code."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}:{detail}")


class _JSONSignal(ValueError):
    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail


def _ordered(issues: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(issues)))


def _selected_paths(value: PathLike | Iterable[PathLike]) -> tuple[Path, ...]:
    if isinstance(value, (str, os.PathLike)):
        return (Path(value),)
    return tuple(Path(item) for item in value)


def _display(path: Path, base: PathLike | None) -> str:
    if base is None:
        return path.as_posix()
    try:
        return path.absolute().relative_to(Path(base).absolute()).as_posix()
    except ValueError:
        return path.as_posix()


def _file_kind(mode: int) -> str:
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character-device"
    if stat.S_ISBLK(mode):
        return "block-device"
    return "other"


def validate_regular_tree(
    root: PathLike,
    *,
    ignored_top_level: Iterable[str] = (),
) -> tuple[str, ...]:
    """Reject links, hardlinked files, and entries other than directories/files."""

    root_path = Path(root)
    ignored = frozenset(ignored_top_level)
    try:
        root_info = root_path.lstat()
    except FileNotFoundError:
        return ("FS_MISSING:.",)
    except OSError:
        return ("FS_UNREADABLE:.",)
    if stat.S_ISLNK(root_info.st_mode):
        return ("FS_SYMLINK:.",)
    if not stat.S_ISDIR(root_info.st_mode):
        return ("FS_NOT_DIRECTORY:.",)

    issues: list[str] = []

    def visit(directory: Path, prefix: str) -> None:
        try:
            with os.scandir(directory) as stream:
                entries = sorted(stream, key=lambda entry: entry.name)
        except OSError:
            issues.append(f"FS_UNREADABLE:{prefix or '.'}")
            return
        for entry in entries:
            if not prefix and entry.name in ignored:
                continue
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                issues.append(f"FS_UNREADABLE:{relative}")
                continue
            if stat.S_ISLNK(info.st_mode):
                issues.append(f"FS_SYMLINK:{relative}")
            elif stat.S_ISDIR(info.st_mode):
                visit(Path(entry.path), relative)
            elif not stat.S_ISREG(info.st_mode):
                issues.append(f"FS_NON_REGULAR:{relative}:{_file_kind(info.st_mode)}")
            elif info.st_nlink != 1:
                issues.append(f"FS_HARDLINK:{relative}")

    visit(root_path, "")
    return _ordered(issues)


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JSONSignal("JSON_DUPLICATE_KEY", key)
        result[key] = value
    return result


def _json_float(token: str) -> float:
    try:
        value = float(token)
    except (OverflowError, ValueError):
        raise _JSONSignal("JSON_NUMBER_OVERFLOW", token) from None
    if not math.isfinite(value):
        raise _JSONSignal("JSON_NUMBER_OVERFLOW", token)
    return value


def _json_int(token: str) -> int:
    try:
        return int(token)
    except (OverflowError, ValueError):
        raise _JSONSignal("JSON_NUMBER_OVERFLOW", token) from None


def _json_constant(token: str) -> object:
    raise _JSONSignal("JSON_NONFINITE", token)


def load_strict_json_object(
    path: PathLike,
    *,
    limit: int = MAX_JSON_BYTES,
) -> dict[str, Any]:
    """Read a bounded standalone UTF-8 JSON object without following links."""

    source = Path(path)
    descriptor: int | None = None
    try:
        before = source.lstat()
        if stat.S_ISLNK(before.st_mode):
            raise JSONPolicyError("JSON_SYMLINK")
        if not stat.S_ISREG(before.st_mode):
            raise JSONPolicyError("JSON_NOT_REGULAR")
        if before.st_nlink != 1:
            raise JSONPolicyError("JSON_HARDLINK")
        if before.st_size > limit:
            raise JSONPolicyError("JSON_TOO_LARGE")
        descriptor = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size > limit
        ):
            raise JSONPolicyError("JSON_CHANGED")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(limit + 1)
        if len(payload) > limit:
            raise JSONPolicyError("JSON_TOO_LARGE")
    except JSONPolicyError:
        raise
    except FileNotFoundError:
        raise JSONPolicyError("JSON_MISSING") from None
    except OSError:
        raise JSONPolicyError("JSON_IO_ERROR") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)

    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise JSONPolicyError("JSON_NOT_UTF8") from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_json_object,
            parse_constant=_json_constant,
            parse_float=_json_float,
            parse_int=_json_int,
        )
    except _JSONSignal as error:
        raise JSONPolicyError(error.code, error.detail) from None
    except (json.JSONDecodeError, RecursionError):
        raise JSONPolicyError("JSON_INVALID") from None
    if not isinstance(value, dict):
        raise JSONPolicyError("JSON_NOT_OBJECT")
    return value


def validate_json_objects(
    paths: PathLike | Iterable[PathLike],
    *,
    base: PathLike | None = None,
) -> tuple[str, ...]:
    selected = sorted(
        ((_display(path, base), path) for path in _selected_paths(paths)),
        key=lambda item: item[0],
    )
    issues: list[str] = []
    for label, path in selected:
        try:
            load_strict_json_object(path)
        except JSONPolicyError as error:
            issues.append(f"{error.code}:{label}")
    return _ordered(issues)


_PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[[A-Za-z0-9._,-]+\])?==(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)


def _dependency_bytes(data: DependencyData) -> bytes:
    return data if isinstance(data, bytes) else data.encode("utf-8")


def _dependency_text(data: DependencyData, source: str, issues: list[str]) -> str | None:
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        issues.append(f"DEPENDENCY_NOT_UTF8:{source}")
        return None


def _parse_pins(text: str | None, source: str, issues: list[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    if text is None:
        return pins
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN_RE.fullmatch(line)
        if match is None:
            issues.append(f"DEPENDENCY_UNPINNED:{source}:{line_number}")
            continue
        name = re.sub(r"[-_.]+", "-", match.group("name")).lower()
        if name in pins:
            issues.append(f"DEPENDENCY_DUPLICATE:{source}:{name}")
            continue
        pins[name] = match.group("version")
    return pins


def validate_dependency_provenance(
    requirements: DependencyData,
    lock: DependencyData,
    *,
    expected_requirements: DependencyData | None = None,
    expected_lock: DependencyData | None = None,
    exact: bool = False,
) -> tuple[str, ...]:
    """Validate exact snapshots and requirements-style offline dependency pins."""

    issues: list[str] = []
    if expected_requirements is not None and _dependency_bytes(requirements) != _dependency_bytes(
        expected_requirements
    ):
        issues.append("DEPENDENCY_REQUIREMENTS_PROVENANCE_MISMATCH")
    if expected_lock is not None and _dependency_bytes(lock) != _dependency_bytes(expected_lock):
        issues.append("DEPENDENCY_LOCK_PROVENANCE_MISMATCH")
    required = _parse_pins(
        _dependency_text(requirements, "requirements", issues), "requirements", issues
    )
    locked = _parse_pins(_dependency_text(lock, "lock", issues), "lock", issues)
    for name, version in sorted(required.items()):
        locked_version = locked.get(name)
        if locked_version is None:
            issues.append(f"DEPENDENCY_MISSING_FROM_LOCK:{name}")
        elif locked_version != version:
            issues.append(
                f"DEPENDENCY_PIN_MISMATCH:{name}:required={version}:locked={locked_version}"
            )
    if exact:
        for name in sorted(locked.keys() - required.keys()):
            issues.append(f"DEPENDENCY_MISSING_FROM_REQUIREMENTS:{name}")
    return _ordered(issues)


_FORBIDDEN_MODULES = frozenset(
    {
        "_posixsubprocess",
        "_socket",
        "aiohttp",
        "ctypes",
        "ftplib",
        "http",
        "multiprocessing",
        "nt",
        "posix",
        "requests",
        "smtplib",
        "socket",
        "subprocess",
        "urllib",
        "webbrowser",
    }
)
_DYNAMIC_TARGETS = frozenset(
    {
        "builtins.__import__",
        "builtins.compile",
        "builtins.eval",
        "builtins.exec",
        "importlib.import_module",
    }
)
_DIRECT_BUILTINS = {
    "__builtins__": "builtins",
    "__import__": "builtins.__import__",
    "compile": "builtins.compile",
    "eval": "builtins.eval",
    "exec": "builtins.exec",
    "getattr": "builtins.getattr",
}


def _forbidden_module(target: str) -> bool:
    return target.split(".", 1)[0] in _FORBIDDEN_MODULES


def _forbidden_call(target: str) -> bool:
    if _forbidden_module(target):
        return True
    if target in {"asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}:
        return True
    if not target.startswith("os."):
        return False
    member = target.rsplit(".", 1)[-1]
    return (
        member
        in {
            "fork",
            "forkpty",
            "popen",
            "posix_spawn",
            "posix_spawnp",
            "startfile",
            "system",
        }
        or member.startswith("exec")
        or member.startswith("spawn")
    )


def _forbidden_import(target: str) -> bool:
    return _forbidden_module(target) or _forbidden_call(target) or target in _DYNAMIC_TARGETS


class _CapabilityVisitor(ast.NodeVisitor):
    def __init__(self, label: str) -> None:
        self.label = label
        self.aliases: dict[str, str] = {}
        self.issues: list[str] = []

    def _add(self, code: str, node: ast.AST, target: str) -> None:
        line = getattr(node, "lineno", 1)
        column = getattr(node, "col_offset", 0) + 1
        self.issues.append(f"{code}:{self.label}:{line}:{column}:{target}")

    def _resolve(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, _DIRECT_BUILTINS.get(node.id))
        if isinstance(node, ast.Attribute):
            base = self._resolve(node.value)
            return f"{base}.{node.attr}" if base else None
        if isinstance(node, ast.Subscript):
            base = self._resolve(node.value)
            key = node.slice
            if base == "builtins" and isinstance(key, ast.Constant) and isinstance(key.value, str):
                return f"builtins.{key.value}"
            return None
        if isinstance(node, ast.Call):
            called = self._resolve(node.func)
            if called in {"builtins.__import__", "importlib.import_module"}:
                if node.args and isinstance(node.args[0], ast.Constant):
                    module = node.args[0].value
                    if isinstance(module, str):
                        return module
            if called == "builtins.getattr" and len(node.args) >= 2:
                base = self._resolve(node.args[0])
                member = node.args[1]
                if base and isinstance(member, ast.Constant) and isinstance(member.value, str):
                    return f"{base}.{member.value}"
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for imported in node.names:
            self.aliases[imported.asname or imported.name.split(".", 1)[0]] = imported.name
            if _forbidden_import(imported.name):
                self._add("PY_FORBIDDEN_IMPORT", node, imported.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or not node.module:
            return
        for imported in node.names:
            target = f"{node.module}.{imported.name}"
            self.aliases[imported.asname or imported.name] = target
            if _forbidden_import(target):
                self._add("PY_FORBIDDEN_IMPORT", node, target)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        resolved = self._resolve(node.value)
        if resolved:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.aliases[target.id] = resolved

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            resolved = self._resolve(node.value)
            if resolved and isinstance(node.target, ast.Name):
                self.aliases[node.target.id] = resolved
        self.visit(node.annotation)

    def visit_Call(self, node: ast.Call) -> None:
        target = self._resolve(node.func)
        if target in _DYNAMIC_TARGETS:
            self._add("PY_DYNAMIC_ESCAPE", node, target)
        elif target and _forbidden_call(target):
            self._add("PY_FORBIDDEN_CALL", node, target)
        elif isinstance(node.func, ast.Call):
            getter = self._resolve(node.func.func)
            if getter == "builtins.getattr" and len(node.func.args) >= 2:
                base = self._resolve(node.func.args[0])
                member = node.func.args[1]
                if (
                    base
                    and not (isinstance(member, ast.Constant) and isinstance(member.value, str))
                    and (
                        base in {"asyncio", "builtins", "importlib", "os"}
                        or _forbidden_module(base)
                    )
                ):
                    self._add("PY_DYNAMIC_ESCAPE", node, f"getattr({base})")
        self.generic_visit(node)


def _python_files(roots: tuple[Path, ...], base: PathLike | None, issues: list[str]) -> list[Path]:
    files: list[Path] = []

    def visit(path: Path, *, selected_root: bool = False) -> None:
        label = _display(path, base)
        try:
            info = path.lstat()
        except FileNotFoundError:
            if selected_root:
                issues.append(f"PY_ROOT_MISSING:{label}")
            return
        except OSError:
            issues.append(f"PY_UNREADABLE:{label}")
            return
        if stat.S_ISLNK(info.st_mode):
            issues.append(f"PY_ROOT_NON_REGULAR:{label}")
            return
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                issues.append(f"PY_HARDLINK:{label}")
            elif path.suffix == ".py":
                files.append(path)
            return
        if not stat.S_ISDIR(info.st_mode):
            if selected_root:
                issues.append(f"PY_ROOT_NON_REGULAR:{label}")
            return
        try:
            with os.scandir(path) as stream:
                children = sorted(stream, key=lambda entry: entry.name)
        except OSError:
            issues.append(f"PY_UNREADABLE:{label}")
            return
        for child in children:
            if child.name.endswith(".py") or child.is_dir(follow_symlinks=False):
                visit(Path(child.path))

    for root in roots:
        visit(root, selected_root=True)
    return sorted(set(files), key=lambda path: _display(path, base))


def scan_python_capabilities(
    editable_roots: PathLike | Iterable[PathLike],
    *,
    base: PathLike | None = None,
) -> tuple[str, ...]:
    """AST-scan only explicitly selected editable roots; narrative text is ignored."""

    issues: list[str] = []
    for path in _python_files(_selected_paths(editable_roots), base, issues):
        label = _display(path, base)
        try:
            source = path.read_bytes().decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            issues.append(f"PY_NOT_UTF8:{label}")
            continue
        except OSError:
            issues.append(f"PY_UNREADABLE:{label}")
            continue
        try:
            tree = ast.parse(source, filename=label, type_comments=True)
        except (SyntaxError, ValueError) as error:
            issues.append(f"PY_INVALID_SYNTAX:{label}:{getattr(error, 'lineno', None) or 1}")
            continue
        visitor = _CapabilityVisitor(label)
        visitor.visit(tree)
        issues.extend(visitor.issues)
    return _ordered(issues)
