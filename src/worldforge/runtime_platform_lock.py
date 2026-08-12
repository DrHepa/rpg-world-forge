from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from worldforge.creation_contracts import (
    CreationContractError,
    _decode_creation_object,
    _exact_keys,
    _identifier,
    _integer,
    _object,
    _sha256,
    _validate_json_structure,
    canonical_creation_hash,
    read_creation_object,
)
from worldforge.integrity import canonical_json_bytes

RUNTIME_PLATFORM_LOCK_FORMAT = "world-forge.runtime_platform_lock"
RUNTIME_PLATFORM_LOCK_VERSION = 1

_LOCK_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "lock_id",
        "platform",
        "python",
        "dependency",
        "content_hash",
    }
)
_PLATFORM_FIELDS = frozenset({"os", "architecture", "backend", "renderer"})
_PYTHON_FIELDS = frozenset({"implementation", "minor", "abi", "requires_python"})
_DEPENDENCY_FIELDS = frozenset(
    {
        "distribution",
        "version",
        "pin",
        "import_module",
        "native_api",
        "artifact",
    }
)
_ARTIFACT_FIELDS = frozenset({"filename", "size_bytes", "url", "sha256"})

_PROJECT_FILES_URL = "https://pypi.org/project/raylib/6.0.1.0/#files"
_ARTIFACTS = (
    (
        "linux",
        "3.11",
        "cp311",
        ("raylib-6.0.1.0-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"),
        2_302_782,
        "6b126a8b9e9a0d36dc796fb0ae1bd7473464a4b126315e332079e5eca7215116",
    ),
    (
        "linux",
        "3.12",
        "cp312",
        ("raylib-6.0.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"),
        2_320_911,
        "bcd224e184c5d64fb6d57bbdabc07124a6f64455ec711d748a0c148b3b26b914",
    ),
    (
        "windows",
        "3.11",
        "cp311",
        "raylib-6.0.1.0-cp311-cp311-win_amd64.whl",
        2_297_998,
        "a665bd824128396f70435f959399d76c2bb460ce1867fb9d19b41490b70a0d2a",
    ),
    (
        "windows",
        "3.12",
        "cp312",
        "raylib-6.0.1.0-cp312-cp312-win_amd64.whl",
        2_300_464,
        "64ee5407b3e222045a2b4e6c41ede77a7be05c90335e0679c4765d0e5bcf3ba6",
    ),
)


class RuntimePlatformLockError(ValueError):
    """Raised when a runtime platform lock is not the audited lock."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise RuntimePlatformLockError(reason_code, detail)


def _canonical_hash(document: Mapping[str, object]) -> str:
    try:
        return canonical_creation_hash(document)
    except CreationContractError as exc:
        _fail("runtime_platform_lock_invalid", str(exc))


def _build_lock(
    *,
    os_name: str,
    python_minor: str,
    abi: str,
    filename: str,
    size_bytes: int,
    sha256: str,
) -> dict[str, Any]:
    seed: dict[str, Any] = {
        "format": RUNTIME_PLATFORM_LOCK_FORMAT,
        "format_version": RUNTIME_PLATFORM_LOCK_VERSION,
        "platform": {
            "os": os_name,
            "architecture": "x86_64",
            "backend": "backend:raylib",
            "renderer": "raylib",
        },
        "python": {
            "implementation": "cpython",
            "minor": python_minor,
            "abi": abi,
            "requires_python": ">=3.11,<3.13",
        },
        "dependency": {
            "distribution": "raylib",
            "version": "6.0.1.0",
            "pin": "raylib==6.0.1.0",
            "import_module": "pyray",
            "native_api": "raylib-5.5",
            "artifact": {
                "filename": filename,
                "size_bytes": size_bytes,
                "url": _PROJECT_FILES_URL,
                "sha256": sha256,
            },
        },
    }
    document = {
        **seed,
        "lock_id": "runtime_platform_lock_" + _canonical_hash(seed)[:40],
        "content_hash": "",
    }
    document["content_hash"] = _canonical_hash(document)
    return document


_BUILTIN_LOCKS = tuple(
    _build_lock(
        os_name=os_name,
        python_minor=minor,
        abi=abi,
        filename=filename,
        size_bytes=size_bytes,
        sha256=sha256,
    )
    for os_name, minor, abi, filename, size_bytes, sha256 in _ARTIFACTS
)
_BUILTIN_BY_ID = {item["lock_id"]: item for item in _BUILTIN_LOCKS}


def build_builtin_runtime_platform_locks() -> tuple[dict[str, Any], ...]:
    return tuple(copy.deepcopy(item) for item in _BUILTIN_LOCKS)


def validate_runtime_platform_lock_document(value: object) -> dict[str, Any]:
    try:
        _validate_json_structure(value, context="runtime platform lock")
        document = _object(value, "runtime platform lock")
        _exact_keys(document, _LOCK_FIELDS, "runtime platform lock")
        if document.get("format") != RUNTIME_PLATFORM_LOCK_FORMAT:
            _fail(
                "runtime_platform_lock_format_mismatch",
                f"format must be {RUNTIME_PLATFORM_LOCK_FORMAT}",
            )
        if document.get("format_version") != RUNTIME_PLATFORM_LOCK_VERSION:
            _fail(
                "runtime_platform_lock_version_mismatch",
                "format_version must be 1",
            )
        lock_id = _identifier(
            document.get("lock_id"),
            "runtime platform lock.lock_id",
        )
        _sha256(
            document.get("content_hash"),
            "runtime platform lock.content_hash",
        )

        platform = _object(
            document.get("platform"),
            "runtime platform lock.platform",
        )
        _exact_keys(platform, _PLATFORM_FIELDS, "runtime platform lock.platform")
        if platform.get("os") not in {"linux", "windows"}:
            _fail("runtime_platform_lock_platform_invalid", "os is not supported")
        if platform.get("architecture") != "x86_64":
            _fail(
                "runtime_platform_lock_platform_invalid",
                "architecture must be x86_64",
            )
        if platform.get("backend") != "backend:raylib":
            _fail(
                "runtime_platform_lock_platform_invalid",
                "backend must be backend:raylib",
            )
        if platform.get("renderer") != "raylib":
            _fail(
                "runtime_platform_lock_platform_invalid",
                "renderer must be raylib",
            )

        python = _object(document.get("python"), "runtime platform lock.python")
        _exact_keys(python, _PYTHON_FIELDS, "runtime platform lock.python")
        if python.get("implementation") != "cpython":
            _fail(
                "runtime_platform_lock_python_invalid",
                "implementation must be cpython",
            )
        if python.get("minor") not in {"3.11", "3.12"}:
            _fail(
                "runtime_platform_lock_python_invalid",
                "minor must be 3.11 or 3.12",
            )
        if python.get("abi") not in {"cp311", "cp312"}:
            _fail(
                "runtime_platform_lock_python_invalid",
                "ABI must be cp311 or cp312",
            )
        if python.get("requires_python") != ">=3.11,<3.13":
            _fail(
                "runtime_platform_lock_python_invalid",
                "requires_python must be >=3.11,<3.13",
            )

        dependency = _object(
            document.get("dependency"),
            "runtime platform lock.dependency",
        )
        _exact_keys(
            dependency,
            _DEPENDENCY_FIELDS,
            "runtime platform lock.dependency",
        )
        expected_dependency = {
            "distribution": "raylib",
            "version": "6.0.1.0",
            "pin": "raylib==6.0.1.0",
            "import_module": "pyray",
            "native_api": "raylib-5.5",
        }
        for field, expected in expected_dependency.items():
            if dependency.get(field) != expected:
                _fail(
                    "runtime_platform_lock_dependency_invalid",
                    f"{field} must be {expected}",
                )
        artifact = _object(
            dependency.get("artifact"),
            "runtime platform lock.dependency.artifact",
        )
        _exact_keys(
            artifact,
            _ARTIFACT_FIELDS,
            "runtime platform lock.dependency.artifact",
        )
        if (
            not isinstance(artifact.get("filename"), str)
            or not artifact["filename"].endswith(".whl")
            or "/" in artifact["filename"]
            or "\\" in artifact["filename"]
        ):
            _fail(
                "runtime_platform_lock_artifact_invalid",
                "filename must identify one wheel basename",
            )
        _integer(
            artifact.get("size_bytes"),
            "runtime platform lock.dependency.artifact.size_bytes",
            minimum=1,
        )
        if artifact.get("url") != _PROJECT_FILES_URL:
            _fail(
                "runtime_platform_lock_artifact_invalid",
                "url must be the audited official PyPI project files URL",
            )
        _sha256(
            artifact.get("sha256"),
            "runtime platform lock.dependency.artifact.sha256",
        )

        seed = {
            key: item for key, item in document.items() if key not in {"lock_id", "content_hash"}
        }
        expected_id = "runtime_platform_lock_" + _canonical_hash(seed)[:40]
        if lock_id != expected_id:
            _fail(
                "runtime_platform_lock_id_mismatch",
                "lock_id is not derived from canonical content",
            )
        if document["content_hash"] != _canonical_hash(document):
            _fail(
                "runtime_platform_lock_content_hash_mismatch",
                "content_hash is not canonical",
            )
        expected = _BUILTIN_BY_ID.get(lock_id)
        if expected is None or document != expected:
            _fail(
                "runtime_platform_lock_not_audited",
                "lock does not match an exact audited raylib artifact",
            )
        return copy.deepcopy(document)
    except RuntimePlatformLockError:
        raise
    except CreationContractError as exc:
        _fail("runtime_platform_lock_invalid", str(exc))


def serialize_runtime_platform_lock(value: object) -> bytes:
    return canonical_json_bytes(validate_runtime_platform_lock_document(value))


def load_runtime_platform_lock(source: object) -> dict[str, Any]:
    try:
        if isinstance(source, (bytes, bytearray)):
            document = _decode_creation_object(
                bytes(source),
                "runtime platform lock",
            )
        elif isinstance(source, str) and source.lstrip().startswith("{"):
            document = _decode_creation_object(
                source.encode("utf-8"),
                "runtime platform lock",
            )
        else:
            document = read_creation_object(source)  # type: ignore[arg-type]
        return validate_runtime_platform_lock_document(document)
    except RuntimePlatformLockError:
        raise
    except (CreationContractError, OSError, TypeError) as exc:
        _fail("runtime_platform_lock_invalid", str(exc))
