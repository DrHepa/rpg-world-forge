from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.portability import portable_path_key, portable_relative_path
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.narrative_analysis import analyze_project
from worldforge.project import (
    SourceProject,
    SourceProjectError,
    source_manifest_documents,
    source_project_from_documents,
)
from worldforge.studio.changesets import (
    read_source_file_snapshot,
    read_workspace_file_snapshot,
)
from worldforge.studio.contracts import studio_source_path
from worldforge.studio.errors import StudioError, conflict, invalid_request, not_found
from worldforge.studio.workspaces import WorkspaceManager
from worldforge.validation import ValidationIssue, validate_project
from worldforge.workflow import WorkflowError
from worldforge.world_lifecycle import inspect_world_project_snapshot

MAX_SOURCE_DOCUMENTS = 1024
MAX_SOURCE_DEPTH = 8
MAX_SOURCE_DOCUMENT_BYTES = 256 * 1024
MAX_SOURCE_TOTAL_BYTES = 32 * 1024 * 1024
MAX_AUTHORING_RESULT_BYTES = 900 * 1024
MAX_DIAGNOSTICS = 512
MAX_DIAGNOSTIC_TEXT = 512
MAX_CONTROL_DOCUMENT_BYTES = 4 * 1024 * 1024

_MANIFEST_PATH = PurePosixPath("source/manifest.json")
_PROJECT_CONTROL_PATH = PurePosixPath(".worldforge/project.json")
_STATUS_CONTROL_PATH = PurePosixPath(".worldforge/status.json")


@dataclass(frozen=True, slots=True)
class _SourceEntry:
    path: PurePosixPath
    kind: str


@dataclass(frozen=True, slots=True)
class _SourceCatalog:
    manifest: dict[str, Any]
    manifest_payload: bytes
    entries: tuple[_SourceEntry, ...]
    world_path: PurePosixPath
    collections: dict[str, tuple[PurePosixPath, ...]]


class _SourceProblem(ValueError):
    def __init__(self, path: PurePosixPath, message: str) -> None:
        super().__init__(message)
        self.path = path
        self.message = message


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _trim(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) <= MAX_DIAGNOSTIC_TEXT:
        return text
    return text[: MAX_DIAGNOSTIC_TEXT - 1] + "…"


def _safe_snapshot_message(message: str) -> str:
    folded = message.casefold()
    if "hard link" in folded:
        return "must not be a hard link"
    if "utf-8" in folded:
        return "must be valid UTF-8"
    if "exceeds" in folded:
        return f"exceeds the {MAX_SOURCE_DOCUMENT_BYTES}-byte source-document limit"
    if "collision" in folded:
        return "has an NFC/casefold filesystem collision"
    if "changed" in folded:
        return "changed while it was being read"
    return "is unavailable or violates the registered source boundary"


def _bounded(result: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.dumps(
            result,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StudioError("internal_error", "Authoring result is not strict JSON") from exc
    if len(payload) > MAX_AUTHORING_RESULT_BYTES:
        raise invalid_request(
            f"Authoring result exceeds the {MAX_AUTHORING_RESULT_BYTES}-byte response limit"
        )
    return result


def _source_relative(value: object) -> PurePosixPath:
    relative = studio_source_path(value)
    if relative is None:
        raise invalid_request("path must be a portable file beneath source/")
    if len(relative.parts) > MAX_SOURCE_DEPTH:
        raise invalid_request(f"path exceeds the {MAX_SOURCE_DEPTH}-component depth limit")
    return relative


def _manifest_relative(value: object, *, context: str) -> PurePosixPath:
    relative = portable_relative_path(value)
    if relative is None:
        raise SourceProjectError(f"{context} must be a portable relative path")
    source_relative = _source_relative(f"source/{relative.as_posix()}")
    return source_relative


def _diagnostic(
    path: str,
    message: object,
    *,
    code: str,
) -> dict[str, str]:
    return {
        "severity": "error",
        "code": code,
        "path": path,
        "message": _trim(message),
    }


def _validation_diagnostics(issues: list[ValidationIssue]) -> list[dict[str, str]]:
    return [
        _diagnostic(issue.path, issue.message, code="validation_error")
        for issue in issues[:MAX_DIAGNOSTICS]
    ]


class AuthoringManager:
    """Read-only Studio access to one registered world's canonical source graph."""

    def __init__(self, workspaces: WorkspaceManager) -> None:
        self.workspaces = workspaces

    def overview(self, workspace_id: object) -> dict[str, Any]:
        workspace, world_root, world_identity = self._world(workspace_id)
        try:
            catalog = self._catalog(world_root, world_identity)
            project = self._decode(
                self._read_control(
                    world_root,
                    world_identity,
                    _PROJECT_CONTROL_PATH,
                ),
                _PROJECT_CONTROL_PATH,
            )
            status = self._decode(
                self._read_control(
                    world_root,
                    world_identity,
                    _STATUS_CONTROL_PATH,
                ),
                _STATUS_CONTROL_PATH,
            )
            world = self._decode(
                self._read(world_root, world_identity, catalog.world_path),
                catalog.world_path,
            )
            inspection = inspect_world_project_snapshot(
                world_root,
                project,
                world,
                status,
                error_type=WorkflowError,
            )
        except (_SourceProblem, StudioError, WorkflowError) as exc:
            raise conflict("Registered world project is no longer canonical") from exc
        self.workspaces.verified_root(workspace["workspace_id"], "world_root")
        return _bounded(
            {
                "overview": {
                    "workspace_id": workspace["workspace_id"],
                    "project": {
                        "world_id": inspection.world_id,
                        "title": inspection.title,
                        "world_version": inspection.world_version,
                    },
                    "status": {
                        "current_phase": inspection.current_phase,
                        "revision": inspection.revision,
                        "canon_locked": inspection.canon_locked,
                        "worldpack_hash": inspection.worldpack_hash,
                    },
                    "repositories": {
                        "game_registered": workspace["game_root"] is not None,
                        "bundle_registered": workspace["bundle_root"] is not None,
                    },
                    "capabilities": {
                        "providers": False,
                        "source_inspection": True,
                        "world_validation": True,
                        "narrative_analysis": True,
                        "staged_changesets": True,
                        "asset_catalog_inspection": True,
                    },
                }
            }
        )

    def list_sources(self, workspace_id: object) -> dict[str, Any]:
        workspace, world_root, world_identity = self._world(workspace_id)
        try:
            catalog = self._catalog(world_root, world_identity)
            documents: list[dict[str, Any]] = []
            total = 0
            for entry in sorted(catalog.entries, key=lambda item: item.path.as_posix()):
                payload = (
                    catalog.manifest_payload
                    if entry.path == _MANIFEST_PATH
                    else self._read(world_root, world_identity, entry.path)
                )
                total += len(payload)
                if total > MAX_SOURCE_TOTAL_BYTES:
                    raise _SourceProblem(
                        entry.path,
                        f"source project exceeds the {MAX_SOURCE_TOTAL_BYTES}-byte limit",
                    )
                documents.append(
                    {
                        "path": entry.path.as_posix(),
                        "kind": entry.kind,
                        "size": len(payload),
                        "sha256": _sha256(payload),
                    }
                )
        except _SourceProblem as exc:
            raise invalid_request(f"{exc.path.as_posix()}: {exc.message}") from exc
        self.workspaces.verified_root(workspace["workspace_id"], "world_root")
        return _bounded({"documents": documents})

    def read_source(self, workspace_id: object, path: object) -> dict[str, Any]:
        workspace, world_root, world_identity = self._world(workspace_id)
        requested = _source_relative(path)
        try:
            catalog = self._catalog(world_root, world_identity)
            by_path = {entry.path: entry for entry in catalog.entries}
            entry = by_path.get(requested)
            if entry is None:
                raise not_found("Source document is not declared by the registered world project")
            payload = (
                catalog.manifest_payload
                if requested == _MANIFEST_PATH
                else self._read(world_root, world_identity, requested)
            )
            parsed = self._decode(payload, requested)
        except _SourceProblem as exc:
            raise invalid_request(f"{exc.path.as_posix()}: {exc.message}") from exc
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as exc:  # pragma: no cover - enforced by the snapshot helper
            raise invalid_request("Source document must be valid UTF-8") from exc
        self.workspaces.verified_root(workspace["workspace_id"], "world_root")
        return _bounded(
            {
                "document": {
                    "path": requested.as_posix(),
                    "kind": entry.kind,
                    "encoding": "utf-8",
                    "size": len(payload),
                    "sha256": _sha256(payload),
                    "content": content,
                    "json": parsed,
                }
            }
        )

    def validate_world(self, workspace_id: object) -> dict[str, Any]:
        workspace, world_root, world_identity = self._world(workspace_id)
        project, problem = self._project(world_root, world_identity)
        if problem is not None:
            validation = {
                "valid": False,
                "profile": "release",
                "world_id": None,
                "object_count": 0,
                "diagnostics": [
                    _diagnostic(problem.path.as_posix(), problem.message, code="source_error")
                ],
                "diagnostics_truncated": False,
            }
        else:
            assert project is not None
            issues = validate_project(project, profile="release")
            validation = {
                "valid": not issues,
                "profile": "release",
                "world_id": project.world.get("id")
                if isinstance(project.world.get("id"), str)
                else None,
                "object_count": sum(len(items) for items in project.collections.values()),
                "diagnostics": _validation_diagnostics(issues),
                "diagnostics_truncated": len(issues) > MAX_DIAGNOSTICS,
            }
        self.workspaces.verified_root(workspace["workspace_id"], "world_root")
        return _bounded({"validation": validation})

    def analyze_world(self, workspace_id: object) -> dict[str, Any]:
        workspace, world_root, world_identity = self._world(workspace_id)
        project, problem = self._project(world_root, world_identity)
        if problem is not None:
            validation = {
                "valid": False,
                "profile": "release",
                "world_id": None,
                "object_count": 0,
                "diagnostics": [
                    _diagnostic(problem.path.as_posix(), problem.message, code="source_error")
                ],
                "diagnostics_truncated": False,
            }
            analysis: dict[str, Any] | None = None
        else:
            assert project is not None
            issues = validate_project(project, profile="release")
            validation = {
                "valid": not issues,
                "profile": "release",
                "world_id": project.world.get("id")
                if isinstance(project.world.get("id"), str)
                else None,
                "object_count": sum(len(items) for items in project.collections.values()),
                "diagnostics": _validation_diagnostics(issues),
                "diagnostics_truncated": len(issues) > MAX_DIAGNOSTICS,
            }
            analysis = analyze_project(project) if not issues else None
        self.workspaces.verified_root(workspace["workspace_id"], "world_root")
        return _bounded({"validation": validation, "analysis": analysis})

    def _world(self, workspace_id: object) -> tuple[dict[str, Any], Path, tuple[int, int]]:
        workspace = self.workspaces.get(workspace_id)
        verified = self.workspaces.verified_root(workspace["workspace_id"], "world_root")
        assert verified is not None
        return workspace, *verified

    def _read(
        self,
        world_root: Path,
        world_identity: tuple[int, int],
        path: PurePosixPath,
    ) -> bytes:
        try:
            return read_source_file_snapshot(
                world_root,
                path,
                world_identity=world_identity,
                context=f"source document {path.as_posix()}",
                limit=MAX_SOURCE_DOCUMENT_BYTES,
            )
        except StudioError as exc:
            raise _SourceProblem(path, _safe_snapshot_message(exc.message)) from exc

    @staticmethod
    def _read_control(
        world_root: Path,
        world_identity: tuple[int, int],
        path: PurePosixPath,
    ) -> bytes:
        return read_workspace_file_snapshot(
            world_root,
            path,
            world_identity=world_identity,
            context=f"world control {path.as_posix()}",
            limit=MAX_CONTROL_DOCUMENT_BYTES,
        )

    @staticmethod
    def _decode(payload: bytes, path: PurePosixPath) -> dict[str, Any]:
        try:
            return decode_json_object(payload, source=path.as_posix())
        except RuntimeIOError as exc:
            raise _SourceProblem(path, _trim(exc)) from exc

    def _catalog(
        self,
        world_root: Path,
        world_identity: tuple[int, int],
    ) -> _SourceCatalog:
        manifest_payload = self._read(world_root, world_identity, _MANIFEST_PATH)
        manifest = self._decode(manifest_payload, _MANIFEST_PATH)
        try:
            world_value, raw_collections = source_manifest_documents(manifest)
            world_path = _manifest_relative(world_value, context="manifest/world")
            if world_path != PurePosixPath("source/world.json"):
                raise SourceProjectError("manifest/world must reference world.json")
            collections = {
                collection: tuple(
                    _manifest_relative(path, context=f"manifest/collections/{collection}")
                    for path in paths
                )
                for collection, paths in raw_collections.items()
            }
        except (SourceProjectError, StudioError) as exc:
            raise _SourceProblem(_MANIFEST_PATH, _trim(exc)) from exc

        entries = [_SourceEntry(_MANIFEST_PATH, "manifest"), _SourceEntry(world_path, "world")]
        for collection, paths in collections.items():
            if unicodedata.normalize("NFC", collection) != collection or len(collection) > 128:
                raise _SourceProblem(
                    _MANIFEST_PATH,
                    "collection names must be NFC strings of at most 128 characters",
                )
            entries.extend(_SourceEntry(path, collection) for path in paths)
        if len(entries) > MAX_SOURCE_DOCUMENTS:
            raise _SourceProblem(
                _MANIFEST_PATH,
                f"source manifest exceeds the {MAX_SOURCE_DOCUMENTS}-document limit",
            )
        seen: dict[tuple[str, ...], PurePosixPath] = {}
        for entry in entries:
            key = portable_path_key(entry.path)
            if key in seen:
                raise _SourceProblem(
                    _MANIFEST_PATH,
                    "source manifest contains a duplicate or NFC/casefold path collision",
                )
            seen[key] = entry.path
        return _SourceCatalog(
            manifest=manifest,
            manifest_payload=manifest_payload,
            entries=tuple(entries),
            world_path=world_path,
            collections=collections,
        )

    def _project(
        self,
        world_root: Path,
        world_identity: tuple[int, int],
    ) -> tuple[SourceProject | None, _SourceProblem | None]:
        try:
            catalog = self._catalog(world_root, world_identity)
            payloads: dict[PurePosixPath, bytes] = {_MANIFEST_PATH: catalog.manifest_payload}
            total = len(catalog.manifest_payload)
            for entry in catalog.entries:
                if entry.path == _MANIFEST_PATH:
                    continue
                payload = self._read(world_root, world_identity, entry.path)
                total += len(payload)
                if total > MAX_SOURCE_TOTAL_BYTES:
                    raise _SourceProblem(
                        entry.path,
                        f"source project exceeds the {MAX_SOURCE_TOTAL_BYTES}-byte limit",
                    )
                payloads[entry.path] = payload
            documents = {
                entry.path.relative_to("source").as_posix(): self._decode(
                    payloads[entry.path], entry.path
                )
                for entry in catalog.entries
                if entry.path != _MANIFEST_PATH
            }
            project = source_project_from_documents(
                world_root / _MANIFEST_PATH,
                catalog.manifest,
                documents,
            )
            return project, None
        except SourceProjectError as exc:
            return None, _SourceProblem(_MANIFEST_PATH, _trim(exc))
        except _SourceProblem as exc:
            return None, exc


__all__ = [
    "AuthoringManager",
    "MAX_AUTHORING_RESULT_BYTES",
    "MAX_SOURCE_DEPTH",
    "MAX_SOURCE_DOCUMENT_BYTES",
    "MAX_SOURCE_DOCUMENTS",
    "MAX_SOURCE_TOTAL_BYTES",
]
