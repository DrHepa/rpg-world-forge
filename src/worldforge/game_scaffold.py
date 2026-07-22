from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from importlib.resources import files
from importlib.util import find_spec
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld import __version__ as runtime_version
from isoworld.content.models import RUNTIME_API_VERSION, SUPPORTED_RUNTIME_FEATURES
from isoworld.content.portability import is_portable_path_component
from worldforge.game_boundary import audit_game_repository
from worldforge.game_lock import GameMutationLockError, exclusive_game_mutation
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.repository_boundary import (
    assert_new_repository_target,
    require_standalone_game_root,
)
from worldforge.validation import ID_PATTERN


class GameScaffoldError(ValueError):
    """Raised when a standalone game cannot be materialized safely."""


RUNTIME_VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


_TEMPLATE_OUTPUTS = {
    "README.md.tmpl": "README.md",
    "pyproject.toml.tmpl": "pyproject.toml",
    "gitignore.tmpl": ".gitignore",
    "ci.yml.tmpl": ".github/workflows/ci.yml",
    "game_init.py.tmpl": "src/game/__init__.py",
    "game_catalog_wrapper.py.tmpl": "src/game/catalog.py",
    "game_main.py.tmpl": "src/game/__main__.py",
    "run_game.py.tmpl": "run_game.py",
    "verify_game.py.tmpl": "scripts/verify_game.py",
    "offline_smoke.py.tmpl": "scripts/offline_smoke.py",
    "native_smoke.py.tmpl": "scripts/native_smoke.py",
    "benchmark_scene.py.tmpl": "scripts/benchmark_scene.py",
    "package_game.py.tmpl": "scripts/package_game.py",
    "lock_shared_assets.py.tmpl": "scripts/lock_shared_assets.py",
    "test_game_shell.py.tmpl": "tests/test_game_shell.py",
    "worlds.lock.json.tmpl": "game_data/worlds.lock.json",
    "shared.lock.json.tmpl": "game_data/shared.lock.json",
    "platform.lock.json.tmpl": "platform.lock.json",
    "requirements.lock.tmpl": "requirements.lock",
    "LICENSE.tmpl": "LICENSE",
    "THIRD_PARTY_NOTICES.md.tmpl": "THIRD_PARTY_NOTICES.md",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(canonical_json_bytes(payload))
    os.replace(temporary, path)


def verify_game_runtime_snapshot(root: Path) -> dict[str, Any]:
    manifest_path = root / "runtime.lock.json"
    try:
        info = manifest_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("not a standalone regular file")
        data = manifest_path.read_bytes()
        manifest = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GameScaffoldError(f"Could not verify runtime.lock.json: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != {
        "format",
        "format_version",
        "runtime_version",
        "runtime_api_version",
        "supported_runtime_features",
        "source_revision",
        "files",
        "content_hash",
    }:
        raise GameScaffoldError("The runtime lock has an invalid shape")
    if manifest["format"] != "isoworld.runtime_snapshot" or manifest["format_version"] != 1:
        raise GameScaffoldError("The runtime lock has an unknown format")
    if not all(
        isinstance(manifest[field], str) and manifest[field]
        for field in (
            "runtime_version",
            "runtime_api_version",
            "source_revision",
            "content_hash",
        )
    ):
        raise GameScaffoldError("The runtime lock identity is invalid")
    if (
        RUNTIME_VERSION_PATTERN.fullmatch(manifest["runtime_version"]) is None
        or RUNTIME_VERSION_PATTERN.fullmatch(manifest["runtime_api_version"]) is None
    ):
        raise GameScaffoldError("The runtime lock versions are invalid")
    features = manifest["supported_runtime_features"]
    if (
        not isinstance(features, list)
        or not all(
            isinstance(feature, str) and ID_PATTERN.fullmatch(feature) for feature in features
        )
        or features != sorted(set(features))
    ):
        raise GameScaffoldError("The runtime lock feature inventory is invalid")
    if manifest["content_hash"] != canonical_payload_hash(manifest):
        raise GameScaffoldError("The runtime lock content hash does not verify")
    expected_bytes = canonical_json_bytes(manifest)
    if data != expected_bytes:
        raise GameScaffoldError("The runtime lock is not canonically serialized")
    records = manifest["files"]
    if not isinstance(records, list) or not records or len(records) > 10_000:
        raise GameScaffoldError("The runtime lock has an invalid file inventory")
    paths: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
            raise GameScaffoldError(f"Runtime lock file record {index} is invalid")
        if (
            not isinstance(record["sha256"], str)
            or len(record["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in record["sha256"])
            or isinstance(record["size"], bool)
            or not isinstance(record["size"], int)
            or record["size"] < 0
        ):
            raise GameScaffoldError(f"Runtime lock file record {index} metadata is invalid")
        relative = record["path"]
        if not isinstance(relative, str) or "\\" in relative:
            raise GameScaffoldError(f"Runtime lock file path {index} is invalid")
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or pure.as_posix() != relative
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.parts[:2] != ("src", "isoworld")
            or pure.suffix != ".py"
        ):
            raise GameScaffoldError(f"Runtime lock file path {index} is unsafe")
        target = root.joinpath(*pure.parts)
        try:
            file_info = target.lstat()
        except OSError as exc:
            raise GameScaffoldError(f"Runtime snapshot file is missing: {relative}") from exc
        if (
            not stat.S_ISREG(file_info.st_mode)
            or file_info.st_nlink != 1
            or file_info.st_size != record["size"]
            or _sha256(target.read_bytes()) != record["sha256"]
        ):
            raise GameScaffoldError(f"Runtime snapshot file failed verification: {relative}")
        paths.append(relative)
    if paths != sorted(set(paths)):
        raise GameScaffoldError("Runtime lock file paths must be unique and sorted")
    runtime_root = root / "src/isoworld"
    try:
        runtime_info = runtime_root.lstat()
    except OSError as exc:
        raise GameScaffoldError("The vendored runtime root is missing") from exc
    if not stat.S_ISDIR(runtime_info.st_mode) or runtime_root.is_symlink():
        raise GameScaffoldError("The vendored runtime root is unsafe")
    actual: list[str] = []
    actual_directories: set[str] = set()
    for current, directory_names, file_names in os.walk(
        runtime_root,
        topdown=True,
        followlinks=False,
    ):
        current_path = Path(current)
        directory_names.sort()
        directory_names[:] = [name for name in directory_names if name != "__pycache__"]
        file_names.sort()
        for name in directory_names:
            directory = current_path / name
            directory_info = directory.lstat()
            if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(directory_info.st_mode):
                raise GameScaffoldError("The vendored runtime contains an unsafe directory")
            actual_directories.add(directory.relative_to(root).as_posix())
        for name in file_names:
            file = current_path / name
            file_info = file.lstat()
            if not stat.S_ISREG(file_info.st_mode) or file_info.st_nlink != 1:
                raise GameScaffoldError("The vendored runtime contains an unsafe file")
            actual.append(file.relative_to(root).as_posix())
    actual.sort()
    expected_directories = {
        parent.as_posix()
        for relative in paths
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() not in {".", "src", "src/isoworld"}
    }
    if actual != paths:
        raise GameScaffoldError("The vendored runtime tree differs from its lock")
    if actual_directories != expected_directories:
        raise GameScaffoldError("The vendored runtime directory tree differs from its lock")
    return manifest


def _runtime_source_root() -> Path:
    specification = find_spec("isoworld")
    locations = specification.submodule_search_locations if specification is not None else None
    if not locations:
        raise GameScaffoldError("The reference isoworld runtime package is unavailable")
    root = Path(next(iter(locations))).resolve()
    if not root.is_dir():
        raise GameScaffoldError("The reference isoworld runtime package is not a directory")
    return root


def _materialize_runtime(root: Path, *, source_revision: str | None) -> dict[str, Any]:
    source = _runtime_source_root()
    destination = root / "src/isoworld"
    records: list[dict[str, Any]] = []
    for path in sorted(source.rglob("*.py")):
        if path.is_symlink():
            raise GameScaffoldError(f"Runtime snapshot source contains a symlink: {path}")
        relative = path.relative_to(source)
        data = path.read_bytes()
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
        records.append(
            {
                "path": (Path("src/isoworld") / relative).as_posix(),
                "sha256": _sha256(data),
                "size": len(data),
            }
        )
    catalog_source = files("worldforge").joinpath(
        "templates",
        "pyray_game",
        "game_catalog.py.tmpl",
    )
    catalog_relative = Path("content/catalog.py")
    catalog_output = destination / catalog_relative
    if catalog_output.exists():
        raise GameScaffoldError("The runtime snapshot has a duplicate catalog verifier")
    try:
        catalog_data = catalog_source.read_bytes()
    except (FileNotFoundError, OSError) as exc:
        raise GameScaffoldError(f"Missing locked catalog verifier template: {exc}") from exc
    catalog_output.parent.mkdir(parents=True, exist_ok=True)
    catalog_output.write_bytes(catalog_data)
    records.append(
        {
            "path": (Path("src/isoworld") / catalog_relative).as_posix(),
            "sha256": _sha256(catalog_data),
            "size": len(catalog_data),
        }
    )
    records.sort(key=lambda record: record["path"])
    if not records:
        raise GameScaffoldError("The reference runtime snapshot is empty")
    manifest: dict[str, Any] = {
        "format": "isoworld.runtime_snapshot",
        "format_version": 1,
        "runtime_version": runtime_version,
        "runtime_api_version": RUNTIME_API_VERSION,
        "supported_runtime_features": sorted(SUPPORTED_RUNTIME_FEATURES),
        "source_revision": source_revision or "unavailable",
        "files": records,
    }
    manifest["content_hash"] = canonical_payload_hash(manifest)
    _atomic_json(root / "runtime.lock.json", manifest)
    return manifest


def _assert_external_target(target: Path) -> None:
    try:
        assert_new_repository_target(target, repository_type="game")
    except ValueError as exc:
        raise GameScaffoldError(str(exc)) from exc


def _render_templates(root: Path, *, game_id: str, title: str) -> None:
    template_root = files("worldforge").joinpath("templates", "pyray_game")
    replacements = {
        "__GAME_ID__": game_id,
        "__GAME_DISTRIBUTION__": game_id.replace("_", "-"),
        "__GAME_TITLE__": title,
        "__GAME_TITLE_PY__": json.dumps(title, ensure_ascii=False),
    }
    for template_name, relative_output in _TEMPLATE_OUTPUTS.items():
        template = template_root.joinpath(template_name)
        try:
            content = template.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            raise GameScaffoldError(f"Missing game template {template_name}: {exc}") from exc
        for token, value in replacements.items():
            content = content.replace(token, value)
        if "__GAME_" in content:
            raise GameScaffoldError(f"Unresolved game template token in {template_name}")
        output = root / relative_output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")


def _materialize_boundary_policy(root: Path) -> None:
    source = files("worldforge").joinpath("game_boundary_policy.py")
    try:
        data = source.read_bytes()
    except (FileNotFoundError, OSError) as exc:
        raise GameScaffoldError(f"Missing canonical game boundary policy: {exc}") from exc
    output = root / "scripts/game_boundary_policy.py"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)


def create_game_project(
    target: str | Path,
    *,
    game_id: str,
    title: str,
    source_revision: str | None = None,
) -> Path:
    """Materialize a clean standalone pyray/raylib game and vendored runtime snapshot."""

    if not ID_PATTERN.fullmatch(game_id) or not is_portable_path_component(game_id):
        raise GameScaffoldError("game_id must be portable 2..64-character ASCII snake_case")
    normalized_title = title.strip()
    if not normalized_title:
        raise GameScaffoldError("title cannot be empty")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized_title):
        raise GameScaffoldError("title must be a single-line printable string")
    destination = Path(target)
    if destination.exists() or destination.is_symlink():
        raise GameScaffoldError(f"The target already exists: {destination}")
    _assert_external_target(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent))
    try:
        _render_templates(staging, game_id=game_id, title=normalized_title)
        _materialize_boundary_policy(staging)
        _materialize_runtime(staging, source_revision=source_revision)
        verify_game_runtime_snapshot(staging)
        findings = audit_game_repository(staging)
        if findings:
            rendered = "; ".join(str(finding) for finding in findings)
            raise GameScaffoldError(f"Generated game violates the clean boundary: {rendered}")
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def update_game_runtime_snapshot(
    game_root: str | Path,
    *,
    expected_content_hash: str,
    source_revision: str | None = None,
) -> dict[str, Any]:
    """Replace a materialized game's vendored runtime with rollback on any failure."""

    try:
        root = require_standalone_game_root(game_root)
    except (OSError, ValueError) as exc:
        raise GameScaffoldError(str(exc)) from exc
    if (
        not isinstance(expected_content_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_content_hash) is None
    ):
        raise GameScaffoldError("expected_content_hash must be a lowercase SHA-256 digest")
    try:
        with exclusive_game_mutation(root, "runtime-update"):
            return _update_game_runtime_snapshot_locked(
                root,
                expected_content_hash=expected_content_hash,
                source_revision=source_revision,
            )
    except GameMutationLockError as exc:
        raise GameScaffoldError(str(exc)) from exc


def _verify_catalog_for_runtime(
    root: Path,
    runtime_api_version: str,
    runtime_features: list[str],
) -> None:
    from worldforge.bundle import verify_game_catalog_compatibility

    try:
        verify_game_catalog_compatibility(
            root,
            runtime_api_version,
            runtime_features,
        )
    except ValueError as exc:
        raise GameScaffoldError(
            f"Runtime update is incompatible with the installed catalog: {exc}"
        ) from exc


def _update_game_runtime_snapshot_locked(
    root: Path,
    *,
    expected_content_hash: str,
    source_revision: str | None,
) -> dict[str, Any]:
    manifest_path = root / "runtime.lock.json"
    runtime_path = root / "src/isoworld"
    findings = audit_game_repository(root)
    if findings:
        raise GameScaffoldError(f"Refusing to update a boundary-invalid game: {findings[0]}")
    current = verify_game_runtime_snapshot(root)
    if current.get("content_hash") != expected_content_hash:
        raise GameScaffoldError("The current runtime snapshot hash differs from the expected hash")

    staging = Path(tempfile.mkdtemp(prefix=".runtime-update-", dir=root))
    backup = root / "src/.isoworld-backup"
    previous_manifest = manifest_path.read_bytes()
    if backup.exists() or backup.is_symlink():
        shutil.rmtree(staging, ignore_errors=True)
        raise GameScaffoldError("A previous runtime update backup still exists")
    try:
        updated = _materialize_runtime(staging, source_revision=source_revision)
        _verify_catalog_for_runtime(
            root,
            updated["runtime_api_version"],
            updated["supported_runtime_features"],
        )
        os.replace(runtime_path, backup)
        os.replace(staging / "src/isoworld", runtime_path)
        os.replace(staging / "runtime.lock.json", manifest_path)
        verify_game_runtime_snapshot(root)
        _verify_catalog_for_runtime(
            root,
            updated["runtime_api_version"],
            updated["supported_runtime_features"],
        )
        post_findings = audit_game_repository(root)
        if post_findings:
            raise GameScaffoldError(f"Updated game violates the clean boundary: {post_findings[0]}")
    except Exception:
        if backup.is_dir():
            if runtime_path.exists():
                shutil.rmtree(runtime_path)
            os.replace(backup, runtime_path)
        manifest_path.write_bytes(previous_manifest)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup)
    return updated
