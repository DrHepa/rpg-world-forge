from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import tempfile
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.file_stat import FileStat, path_file_stat
from isoworld.content.media import (
    MAX_MEDIA_BYTES,
    MediaValidationError,
    read_resource_snapshot,
    read_validated_resource,
)
from isoworld.content.portability import portable_relative_path
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.asset_formats.gltf import GLBError, inspect_glb
from worldforge.asset_io import MAX_CONTRACT_BYTES
from worldforge.assets import AssetManifestError, validate_asset_manifest_object
from worldforge.studio.changesets import read_workspace_file_snapshot
from worldforge.studio.errors import (
    StudioError,
    conflict,
    invalid_request,
    invalid_state,
    not_found,
)
from worldforge.studio.workspaces import WorkspaceManager
from worldforge.workflow import WorkflowError
from worldforge.world_lifecycle import inspect_world_project_snapshot

MAX_ASSET_CATALOG_PAGE = 64
MAX_ASSET_INLINE_BYTES = 256 * 1024
MAX_ASSET_CATALOG_RESULT_BYTES = 900 * 1024
_CONTROL_BYTES = 4 * 1024 * 1024
_ENTRY_PREFIX = "asset_"
_SUPPORTED_INSPECTIONS = frozenset(
    {
        "application/json",
        "audio/wav",
        "font/otf",
        "font/ttf",
        "image/png",
        "model/gltf-binary",
        "text/x-glsl",
    }
)
_VALIDATED_UNSUPPORTED_MEDIA = frozenset(
    {
        "audio/mpeg",
        "audio/ogg",
        "image/jpeg",
        "image/webp",
    }
)
_PROJECT = PurePosixPath(".worldforge/project.json")
_STATUS = PurePosixPath(".worldforge/status.json")
_WORLD = PurePosixPath("source/world.json")
_PREVIEW_MEDIA_TYPES = frozenset({"audio/wav", "image/png"})
_PREVIEW_OUTPUT_CATEGORIES = frozenset({"processing_output", "production_output", "runtime_output"})


@dataclass(frozen=True, slots=True)
class _StableFileState:
    device: int
    inode: int
    mode: int
    link_count: int
    size: int
    modified_ns: int
    changed_ns: int
    attributes: int


@dataclass(frozen=True, slots=True, repr=False)
class AssetRevisionGuard:
    workspace_id: str
    world_root: Path
    world_identity: tuple[int, int]
    world_id: str
    workflow_revision: int
    status_sha256: str
    status_state: _StableFileState
    manifest_relative: PurePosixPath
    manifest_sha256: str
    manifest_content_hash: str
    manifest_state: _StableFileState
    manifest_revision: str
    source_relative: PurePosixPath
    source_sha256: str
    source_state: _StableFileState
    media_type: str


@dataclass(frozen=True, slots=True, repr=False)
class ResolvedPreviewAuthority:
    guard: AssetRevisionGuard
    entry_id: str
    world_root: Path
    relative: PurePosixPath
    media_type: str
    byte_length: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _Authority:
    workspace_id: str
    world_root: Path
    world_identity: tuple[int, int]
    world_id: str
    workflow_revision: int
    status_payload: bytes
    status_state: _StableFileState
    manifest_relative: PurePosixPath
    asset_root_relative: PurePosixPath
    manifest_payload: bytes
    manifest_state: _StableFileState
    manifest: dict[str, Any]
    manifest_revision: str


@dataclass(frozen=True, slots=True)
class _Entry:
    entry_id: str
    asset_id: str | None
    category: str
    role: str | None
    relative: PurePosixPath | None
    sha256: str
    media_type: str | None
    selected: bool
    inspectable: bool
    identity_only: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "asset_id": self.asset_id,
            "category": self.category,
            "role": self.role,
            "path": None if self.relative is None else self.relative.as_posix(),
            "sha256": self.sha256,
            "media_type": self.media_type,
            "selected": self.selected,
            "inspectable": self.inspectable,
        }


def _strict_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_file_state(path: Path) -> _StableFileState:
    info: FileStat = path_file_stat(path)
    attributes = int(getattr(info, "st_file_attributes", 0))
    if (
        stat.S_ISLNK(info.st_mode)
        or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_size < 0
    ):
        raise OSError("not an independent regular file")
    return _StableFileState(
        device=info.st_dev,
        inode=info.st_ino,
        mode=info.st_mode,
        link_count=info.st_nlink,
        size=info.st_size,
        modified_ns=info.st_mtime_ns,
        changed_ns=info.st_ctime_ns,
        attributes=attributes,
    )


def _bounded(result: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = _strict_bytes(result)
    except (TypeError, ValueError) as exc:
        raise StudioError("internal_error", "Asset catalog result is not strict JSON") from exc
    if len(encoded) > MAX_ASSET_CATALOG_RESULT_BYTES:
        raise invalid_request(
            f"Asset catalog result exceeds the {MAX_ASSET_CATALOG_RESULT_BYTES}-byte response limit"
        )
    return result


def _portable(value: object, *, context: str) -> PurePosixPath:
    relative = portable_relative_path(value)
    if relative is None:
        raise conflict(f"Canonical asset authority has an invalid {context}")
    return relative


def _joined(parent: PurePosixPath, child: object, *, context: str) -> PurePosixPath:
    relative = _portable(child, context=context)
    joined = parent / relative
    if portable_relative_path(joined.as_posix()) != joined:
        raise conflict(f"Canonical asset authority has an invalid {context}")
    return joined


def _entry_id(
    authority: _Authority,
    *,
    asset_id: str | None,
    category: str,
    role: str | None,
    relative: PurePosixPath | None,
    sha256: str,
    media_type: str | None,
    parent: str | None,
) -> str:
    identity = {
        "workspace_id": authority.workspace_id,
        "world_id": authority.world_id,
        "manifest_path": authority.manifest_relative.as_posix(),
        "asset_id": asset_id,
        "category": category,
        "role": role,
        "path": None if relative is None else relative.as_posix(),
        "sha256": sha256,
        "media_type": media_type,
        "parent": parent,
    }
    return _ENTRY_PREFIX + _sha256(_strict_bytes(identity))


def _entry(
    authority: _Authority,
    *,
    asset_id: str | None,
    category: str,
    role: str | None,
    relative: PurePosixPath | None,
    sha256: str,
    media_type: str | None,
    parent: str | None,
    selected: bool = False,
    identity_only: bool = False,
) -> _Entry:
    return _Entry(
        entry_id=_entry_id(
            authority,
            asset_id=asset_id,
            category=category,
            role=role,
            relative=relative,
            sha256=sha256,
            media_type=media_type,
            parent=parent,
        ),
        asset_id=asset_id,
        category=category,
        role=role,
        relative=relative,
        sha256=sha256,
        media_type=media_type,
        selected=selected,
        inspectable=not identity_only and media_type in _SUPPORTED_INSPECTIONS,
        identity_only=identity_only,
    )


class AssetCatalogManager:
    """Read-only inspection of the exact asset graph authorized by workflow status."""

    def __init__(self, workspaces: WorkspaceManager) -> None:
        self.workspaces = workspaces

    def list(
        self,
        workspace_id: object,
        *,
        offset: object = 0,
        limit: object = MAX_ASSET_CATALOG_PAGE,
        expected_manifest_revision: object = None,
    ) -> dict[str, Any]:
        normalized_offset, normalized_limit = self._page(offset, limit)
        if normalized_offset > 0 and expected_manifest_revision is None:
            raise invalid_request(
                "asset.catalog.list expected_manifest_revision is required after page one"
            )
        expected = self._expected_revision(expected_manifest_revision, required=False)
        authority = self._authority(workspace_id)
        self._require_revision(authority, expected)

        retained: list[_Entry] = []
        try:
            for index, entry in enumerate(self._entries(authority)):
                if index < normalized_offset:
                    continue
                retained.append(entry)
                if len(retained) > normalized_limit:
                    break
        except StudioError:
            raise
        except (AssetManifestError, KeyError, RuntimeIOError, TypeError, ValueError) as exc:
            raise conflict("Canonical asset authority changed while building the catalog") from exc
        has_more = len(retained) > normalized_limit
        page = retained[:normalized_limit]
        result = {
            "manifest_revision": authority.manifest_revision,
            "offset": normalized_offset,
            "limit": normalized_limit,
            "entries": [entry.public() for entry in page],
            "next_offset": normalized_offset + len(page) if has_more else None,
        }
        self._recheck(authority)
        return _bounded(result)

    def inspect(
        self,
        workspace_id: object,
        *,
        entry_id: object,
        expected_manifest_revision: object,
    ) -> dict[str, Any]:
        requested = self._requested_entry_id(entry_id)
        expected = self._expected_revision(expected_manifest_revision, required=True)
        assert expected is not None
        authority = self._authority(workspace_id)
        self._require_revision(authority, expected)

        entry = next(
            (item for item in self._entries(authority) if item.entry_id == requested), None
        )
        if entry is None:
            self._recheck(authority)
            raise not_found("Asset catalog entry was not found")
        inspection = self._inspection(authority, entry)
        result = {
            "manifest_revision": authority.manifest_revision,
            "entry": entry.public(),
            "inspection": inspection,
        }
        self._recheck(authority)
        return _bounded(result)

    def resolve_preview_authority(
        self,
        workspace_id: object,
        manifest_revision: object,
        entry_id: object,
    ) -> ResolvedPreviewAuthority:
        requested = self._requested_entry_id(entry_id)
        expected = self._expected_revision(manifest_revision, required=True)
        assert expected is not None
        authority = self._authority(workspace_id)
        self._require_revision(authority, expected)
        entry = next(
            (item for item in self._entries(authority) if item.entry_id == requested),
            None,
        )
        if entry is None:
            self._recheck(authority)
            raise not_found("Asset catalog entry was not found")
        if (
            entry.category not in _PREVIEW_OUTPUT_CATEGORIES
            or entry.media_type not in _PREVIEW_MEDIA_TYPES
            or entry.relative is None
            or entry.identity_only
        ):
            self._recheck(authority)
            raise invalid_request("Asset catalog entry is not previewable")

        target = authority.world_root.joinpath(*entry.relative.parts)
        try:
            before = _stable_file_state(target)
            resource = read_validated_resource(
                authority.world_root,
                entry.relative,
                entry.media_type,
                limit=MAX_MEDIA_BYTES,
            )
            after = _stable_file_state(target)
        except (MediaValidationError, OSError) as exc:
            raise conflict("Preview asset authority changed or is invalid") from exc
        if before != after or before.size <= 0:
            raise conflict("Preview asset authority changed while it was resolved")
        self._require_resource_hash(resource.sha256, entry.sha256)
        self._recheck(authority)

        manifest_content_hash = authority.manifest.get("content_hash")
        if not isinstance(manifest_content_hash, str):
            raise conflict("Canonical asset manifest has no content hash")
        guard = AssetRevisionGuard(
            workspace_id=authority.workspace_id,
            world_root=authority.world_root,
            world_identity=authority.world_identity,
            world_id=authority.world_id,
            workflow_revision=authority.workflow_revision,
            status_sha256=_sha256(authority.status_payload),
            status_state=authority.status_state,
            manifest_relative=authority.manifest_relative,
            manifest_sha256=_sha256(authority.manifest_payload),
            manifest_content_hash=manifest_content_hash,
            manifest_state=authority.manifest_state,
            manifest_revision=authority.manifest_revision,
            source_relative=entry.relative,
            source_sha256=entry.sha256,
            source_state=before,
            media_type=entry.media_type,
        )
        resolved = ResolvedPreviewAuthority(
            guard=guard,
            entry_id=entry.entry_id,
            world_root=authority.world_root,
            relative=entry.relative,
            media_type=entry.media_type,
            byte_length=before.size,
            sha256=entry.sha256,
        )
        self.assert_current(guard)
        return resolved

    def assert_current(self, guard: AssetRevisionGuard) -> None:
        if not isinstance(guard, AssetRevisionGuard):
            raise StudioError("internal_error", "Asset preview revision guard is invalid")
        verified = self.workspaces.verified_root(guard.workspace_id, "world_root")
        if verified is None or verified != (guard.world_root, guard.world_identity):
            raise conflict("Asset preview authority changed")
        try:
            status, status_state = self._read_stable(
                guard.world_root,
                guard.world_identity,
                _STATUS,
                limit=_CONTROL_BYTES,
            )
            manifest, manifest_state = self._read_stable(
                guard.world_root,
                guard.world_identity,
                guard.manifest_relative,
                limit=MAX_CONTRACT_BYTES,
            )
            source_state = _stable_file_state(
                guard.world_root.joinpath(*guard.source_relative.parts)
            )
        except (OSError, StudioError) as exc:
            raise conflict("Asset preview authority changed") from exc
        if (
            _sha256(status) != guard.status_sha256
            or status_state != guard.status_state
            or _sha256(manifest) != guard.manifest_sha256
            or manifest_state != guard.manifest_state
            or source_state != guard.source_state
        ):
            raise conflict("Asset preview authority changed")
        verified = self.workspaces.verified_root(guard.workspace_id, "world_root")
        if verified is None or verified != (guard.world_root, guard.world_identity):
            raise conflict("Asset preview authority changed")

    @staticmethod
    def _page(offset: object, limit: object) -> tuple[int, int]:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise invalid_request("asset.catalog.list offset must be a non-negative integer")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_ASSET_CATALOG_PAGE
        ):
            raise invalid_request(
                f"asset.catalog.list limit must be an integer from 1 to {MAX_ASSET_CATALOG_PAGE}"
            )
        return offset, limit

    @staticmethod
    def _requested_entry_id(value: object) -> str:
        if (
            not isinstance(value, str)
            or len(value) != len(_ENTRY_PREFIX) + 64
            or not value.startswith(_ENTRY_PREFIX)
            or any(character not in "0123456789abcdef" for character in value[len(_ENTRY_PREFIX) :])
        ):
            raise invalid_request("asset.catalog.inspect entry_id is invalid")
        return value

    @staticmethod
    def _expected_revision(value: object, *, required: bool) -> str | None:
        if value is None and not required:
            return None
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise invalid_request("expected_manifest_revision must be a lowercase SHA-256")
        return value

    @staticmethod
    def _require_revision(authority: _Authority, expected: str | None) -> None:
        if expected is not None and expected != authority.manifest_revision:
            raise conflict("Asset manifest revision changed")

    def _authority(self, workspace_id: object) -> _Authority:
        workspace = self.workspaces.get(workspace_id)
        verified = self.workspaces.verified_root(workspace["workspace_id"], "world_root")
        assert verified is not None
        world_root, world_identity = verified
        try:
            project_payload = self._read(world_root, world_identity, _PROJECT, limit=_CONTROL_BYTES)
            status_payload, status_state = self._read_stable(
                world_root,
                world_identity,
                _STATUS,
                limit=_CONTROL_BYTES,
            )
            world_payload = self._read(world_root, world_identity, _WORLD, limit=_CONTROL_BYTES)
            project = decode_json_object(project_payload, source=_PROJECT.as_posix())
            status = decode_json_object(status_payload, source=_STATUS.as_posix())
            world = decode_json_object(world_payload, source=_WORLD.as_posix())
            inspection = inspect_world_project_snapshot(
                world_root,
                project,
                world,
                status,
                error_type=WorkflowError,
            )
        except (RuntimeIOError, StudioError, WorkflowError) as exc:
            raise conflict("Registered world project is no longer canonical") from exc

        manifest_value = status.get("asset_manifest")
        if manifest_value is None:
            raise invalid_state("Registered world has no canonical asset manifest")
        manifest_relative = _portable(manifest_value, context="asset manifest path")
        asset_root_relative = manifest_relative.parent
        manifest_payload, manifest_state = self._read_stable(
            world_root,
            world_identity,
            manifest_relative,
            limit=MAX_CONTRACT_BYTES,
        )
        try:
            manifest = decode_json_object(
                manifest_payload,
                source=manifest_relative.as_posix(),
            )
        except RuntimeIOError as exc:
            raise conflict("Canonical asset manifest is not strict JSON") from exc

        worldpack_value = status.get("worldpack_path")
        if worldpack_value is None:
            raise conflict("Canonical asset manifest has no canon-locked worldpack")
        worldpack_relative = _portable(worldpack_value, context="worldpack path")
        phase = manifest.get("phase")
        profile = "release" if phase == "release" else "draft"
        try:
            issues = validate_asset_manifest_object(
                manifest,
                root=world_root.joinpath(*asset_root_relative.parts),
                profile=profile,
                worldpack_path=world_root.joinpath(*worldpack_relative.parts),
            )
        except (AssetManifestError, OSError, ValueError) as exc:
            raise conflict("Canonical asset manifest validation failed") from exc
        if issues:
            raise conflict("Canonical asset manifest validation failed")

        self._require_status_globals(status, manifest, asset_root_relative)
        repeated_manifest, repeated_manifest_state = self._read_stable(
            world_root,
            world_identity,
            manifest_relative,
            limit=MAX_CONTRACT_BYTES,
        )
        if repeated_manifest != manifest_payload or repeated_manifest_state != manifest_state:
            raise conflict("Canonical asset manifest changed while it was validated")
        manifest_content_hash = manifest.get("content_hash")
        if not isinstance(manifest_content_hash, str):
            raise conflict("Canonical asset manifest has no content hash")
        revision = _sha256(
            _strict_bytes(
                {
                    "workspace_id": workspace["workspace_id"],
                    "world_id": inspection.world_id,
                    "workflow_revision": inspection.revision,
                    "manifest_path": manifest_relative.as_posix(),
                    "manifest_sha256": _sha256(manifest_payload),
                    "manifest_content_hash": manifest_content_hash,
                }
            )
        )
        authority = _Authority(
            workspace_id=workspace["workspace_id"],
            world_root=world_root,
            world_identity=world_identity,
            world_id=inspection.world_id,
            workflow_revision=inspection.revision,
            status_payload=status_payload,
            status_state=status_state,
            manifest_relative=manifest_relative,
            asset_root_relative=asset_root_relative,
            manifest_payload=manifest_payload,
            manifest_state=manifest_state,
            manifest=manifest,
            manifest_revision=revision,
        )
        self._recheck(authority)
        return authority

    @staticmethod
    def _require_status_globals(
        status: dict[str, Any],
        manifest: dict[str, Any],
        asset_root: PurePosixPath,
    ) -> None:
        refs = {
            "asset_target": manifest.get("target"),
            "visual_bible": manifest.get("bibles", {}).get("visual")
            if isinstance(manifest.get("bibles"), dict)
            else None,
            "audio_bible": manifest.get("bibles", {}).get("audio")
            if isinstance(manifest.get("bibles"), dict)
            else None,
            "asset_inventory": manifest.get("inventory"),
        }
        for status_field, reference in refs.items():
            if not isinstance(reference, dict):
                raise conflict("Canonical asset manifest is missing required global documents")
            expected = _joined(
                asset_root,
                reference.get("file"),
                context=f"{status_field} path",
            ).as_posix()
            if status.get(status_field) != expected:
                raise conflict("Workflow status and asset manifest global documents disagree")

    def _recheck(self, authority: _Authority) -> None:
        verified = self.workspaces.verified_root(authority.workspace_id, "world_root")
        if verified is None or verified != (authority.world_root, authority.world_identity):
            raise conflict("Registered world root identity changed")
        status, status_state = self._read_stable(
            authority.world_root,
            authority.world_identity,
            _STATUS,
            limit=_CONTROL_BYTES,
        )
        manifest, manifest_state = self._read_stable(
            authority.world_root,
            authority.world_identity,
            authority.manifest_relative,
            limit=MAX_CONTRACT_BYTES,
        )
        if (
            status != authority.status_payload
            or status_state != authority.status_state
            or manifest != authority.manifest_payload
            or manifest_state != authority.manifest_state
        ):
            raise conflict("Asset catalog authority changed during the request")
        verified = self.workspaces.verified_root(authority.workspace_id, "world_root")
        if verified is None or verified != (authority.world_root, authority.world_identity):
            raise conflict("Registered world root identity changed")

    @staticmethod
    def _read(
        world_root: Path,
        world_identity: tuple[int, int],
        relative: PurePosixPath,
        *,
        limit: int,
    ) -> bytes:
        try:
            return read_workspace_file_snapshot(
                world_root,
                relative,
                world_identity=world_identity,
                context=f"asset authority {relative.as_posix()}",
                limit=limit,
            )
        except StudioError as exc:
            raise conflict("Asset authority file is unavailable or changed") from exc

    @classmethod
    def _read_stable(
        cls,
        world_root: Path,
        world_identity: tuple[int, int],
        relative: PurePosixPath,
        *,
        limit: int,
    ) -> tuple[bytes, _StableFileState]:
        target = world_root.joinpath(*relative.parts)
        try:
            before = _stable_file_state(target)
            payload = cls._read(
                world_root,
                world_identity,
                relative,
                limit=limit,
            )
            after = _stable_file_state(target)
        except (OSError, StudioError) as exc:
            raise conflict("Asset authority changed while it was read") from exc
        if before != after or before.size != len(payload):
            raise conflict("Asset authority changed while it was read")
        return payload, before

    def _json_reference(
        self,
        authority: _Authority,
        relative: PurePosixPath,
        expected_sha256: object,
    ) -> dict[str, Any]:
        payload = self._read(
            authority.world_root,
            authority.world_identity,
            relative,
            limit=MAX_CONTRACT_BYTES,
        )
        if not isinstance(expected_sha256, str) or _sha256(payload) != expected_sha256:
            raise conflict("Authorized asset JSON hash changed")
        try:
            return decode_json_object(payload, source=relative.as_posix())
        except RuntimeIOError as exc:
            raise conflict("Authorized asset JSON is invalid") from exc

    def _entries(self, authority: _Authority) -> Iterator[_Entry]:
        manifest = authority.manifest
        manifest_hash = _sha256(authority.manifest_payload)
        yield _entry(
            authority,
            asset_id=None,
            category="manifest",
            role=None,
            relative=authority.manifest_relative,
            sha256=manifest_hash,
            media_type="application/json",
            parent=str(manifest.get("content_hash")),
        )
        global_refs = (
            ("target", "target", manifest["target"]),
            ("visual_bible", "visual", manifest["bibles"]["visual"]),
            ("audio_bible", "audio", manifest["bibles"]["audio"]),
            ("inventory", "inventory", manifest["inventory"]),
        )
        for category, role, reference in global_refs:
            relative = _joined(
                authority.asset_root_relative,
                reference["file"],
                context=f"{category} path",
            )
            yield _entry(
                authority,
                asset_id=None,
                category=category,
                role=role,
                relative=relative,
                sha256=reference["sha256"],
                media_type="application/json",
                parent=str(manifest.get("content_hash")),
            )

        for asset in sorted(manifest["assets"], key=lambda item: item["id"]):
            asset_id = asset["id"]
            specification = asset["specification"]
            specification_relative = _joined(
                authority.asset_root_relative,
                specification["file"],
                context="asset specification path",
            )
            yield _entry(
                authority,
                asset_id=asset_id,
                category="specification",
                role="specification",
                relative=specification_relative,
                sha256=specification["sha256"],
                media_type="application/json",
                parent=str(manifest.get("content_hash")),
            )

            selected = {
                (candidate["file"], candidate["sha256"])
                for candidate in asset.get("selected_candidates", [])
            }
            receipts = sorted(
                asset["production_receipts"],
                key=lambda item: (item["file"], item["sha256"]),
            )
            for receipt_ref in receipts:
                receipt_relative = _joined(
                    authority.asset_root_relative,
                    receipt_ref["file"],
                    context="production receipt path",
                )
                receipt = self._json_reference(
                    authority,
                    receipt_relative,
                    receipt_ref["sha256"],
                )
                receipt_content_hash = receipt["content_hash"]
                yield _entry(
                    authority,
                    asset_id=asset_id,
                    category="production_receipt",
                    role="receipt",
                    relative=receipt_relative,
                    sha256=receipt_ref["sha256"],
                    media_type="application/json",
                    parent=receipt_content_hash,
                )
                request_ref = receipt["request"]
                request_relative = _joined(
                    authority.asset_root_relative,
                    request_ref["file"],
                    context="production request path",
                )
                yield _entry(
                    authority,
                    asset_id=asset_id,
                    category="production_request",
                    role="request",
                    relative=request_relative,
                    sha256=request_ref["sha256"],
                    media_type="application/json",
                    parent=receipt_content_hash,
                )
                for output in sorted(
                    receipt["outputs"],
                    key=lambda item: (item["role"], item["file"], item["sha256"]),
                ):
                    output_relative = _joined(
                        authority.asset_root_relative,
                        output["file"],
                        context="production output path",
                    )
                    yield _entry(
                        authority,
                        asset_id=asset_id,
                        category="production_output",
                        role=output["role"],
                        relative=output_relative,
                        sha256=output["sha256"],
                        media_type=output["media_type"],
                        parent=receipt_content_hash,
                        selected=(output["file"], output["sha256"]) in selected,
                    )

            processing_hash: str | None = None
            processing_ref = asset.get("processing_receipt")
            if processing_ref is not None:
                processing_relative = _joined(
                    authority.asset_root_relative,
                    processing_ref["file"],
                    context="processing receipt path",
                )
                processing = self._json_reference(
                    authority,
                    processing_relative,
                    processing_ref["sha256"],
                )
                processing_hash = processing["content_hash"]
                yield _entry(
                    authority,
                    asset_id=asset_id,
                    category="processing_receipt",
                    role="receipt",
                    relative=processing_relative,
                    sha256=processing_ref["sha256"],
                    media_type="application/json",
                    parent=processing_hash,
                )
                if processing["format_version"] == 1:
                    recipe = processing["recipe"]
                    yield _entry(
                        authority,
                        asset_id=asset_id,
                        category="processing_recipe",
                        role="recipe",
                        relative=None,
                        sha256=recipe["sha256"],
                        media_type="application/json",
                        parent=recipe["content_hash"],
                        identity_only=True,
                    )
                else:
                    recipe = processing["recipe_ref"]
                    recipe_relative = _joined(
                        authority.asset_root_relative,
                        recipe["file"],
                        context="processing recipe path",
                    )
                    yield _entry(
                        authority,
                        asset_id=asset_id,
                        category="processing_recipe",
                        role="recipe",
                        relative=recipe_relative,
                        sha256=recipe["sha256"],
                        media_type="application/json",
                        parent=recipe["content_hash"],
                    )
                for output in sorted(
                    processing["outputs"],
                    key=lambda item: (
                        item["role"],
                        item["artifact"]["file"],
                        item["artifact"]["sha256"],
                    ),
                ):
                    artifact = output["artifact"]
                    output_relative = _joined(
                        processing_relative.parent,
                        artifact["file"],
                        context="processing output path",
                    )
                    yield _entry(
                        authority,
                        asset_id=asset_id,
                        category="processing_output",
                        role=output["role"],
                        relative=output_relative,
                        sha256=artifact["sha256"],
                        media_type=output["media_type"],
                        parent=processing_hash,
                    )

            license_ref = asset.get("license")
            if license_ref is not None:
                yield _entry(
                    authority,
                    asset_id=asset_id,
                    category="license",
                    role="license",
                    relative=_joined(
                        authority.asset_root_relative,
                        license_ref["file"],
                        context="asset license path",
                    ),
                    sha256=license_ref["sha256"],
                    media_type="application/json",
                    parent=str(manifest.get("content_hash")),
                )
            qa_ref = asset.get("qa")
            if qa_ref is not None:
                yield _entry(
                    authority,
                    asset_id=asset_id,
                    category="qa",
                    role="qa",
                    relative=_joined(
                        authority.asset_root_relative,
                        qa_ref["file"],
                        context="asset QA path",
                    ),
                    sha256=qa_ref["sha256"],
                    media_type="application/json",
                    parent=str(manifest.get("content_hash")),
                )
            for output in sorted(
                asset["outputs"],
                key=lambda item: (item["role"], item["runtime_file"], item["sha256"]),
            ):
                yield _entry(
                    authority,
                    asset_id=asset_id,
                    category="runtime_output",
                    role=output["role"],
                    relative=_joined(
                        authority.asset_root_relative,
                        output["runtime_file"],
                        context="runtime output path",
                    ),
                    sha256=output["sha256"],
                    media_type=output["media_type"],
                    parent=processing_hash,
                )

    def _inspection(self, authority: _Authority, entry: _Entry) -> dict[str, Any]:
        if entry.identity_only:
            return {"kind": "unavailable", "reason": "identity_only"}
        assert entry.relative is not None
        media_type = entry.media_type
        if media_type in {"application/json", "text/x-glsl"}:
            try:
                resource = read_validated_resource(
                    authority.world_root,
                    entry.relative,
                    media_type,
                    limit=MAX_ASSET_INLINE_BYTES,
                )
            except MediaValidationError as exc:
                if "exceeds" in str(exc):
                    raise invalid_request(
                        f"Inline asset inspection exceeds {MAX_ASSET_INLINE_BYTES} bytes"
                    ) from exc
                raise conflict("Authorized asset changed or is invalid") from exc
            self._require_resource_hash(resource.sha256, entry.sha256)
            assert resource.payload is not None
            content = resource.payload.decode("utf-8")
            if media_type == "text/x-glsl":
                return {"kind": "glsl", "encoding": "utf-8", "content": content}
            try:
                value = decode_json_object(resource.payload, source=entry.relative.as_posix())
            except RuntimeIOError as exc:  # pragma: no cover - media validation enforces this
                raise conflict("Authorized JSON asset changed or is invalid") from exc
            return {
                "kind": "json",
                "encoding": "utf-8",
                "content": content,
                "value": value,
            }

        if media_type in {"image/png", "audio/wav", "font/ttf", "font/otf"}:
            return self._validated_metadata(authority, entry)
        if media_type == "model/gltf-binary":
            return self._glb_metadata(authority, entry)

        try:
            if media_type in _VALIDATED_UNSUPPORTED_MEDIA:
                resource = read_validated_resource(
                    authority.world_root,
                    entry.relative,
                    media_type,
                    limit=MAX_MEDIA_BYTES,
                )
            else:
                resource = read_resource_snapshot(
                    authority.world_root,
                    entry.relative,
                    limit=MAX_MEDIA_BYTES,
                )
        except MediaValidationError as exc:
            raise conflict("Authorized asset changed or is invalid") from exc
        self._require_resource_hash(resource.sha256, entry.sha256)
        return {"kind": "unavailable", "reason": "unsupported_media_type"}

    def _validated_metadata(self, authority: _Authority, entry: _Entry) -> dict[str, Any]:
        descriptor, name = tempfile.mkstemp(prefix=".worldforge-asset-inspect-")
        temporary = Path(name)
        try:
            try:
                resource = read_validated_resource(
                    authority.world_root,
                    entry.relative,
                    entry.media_type,
                    limit=MAX_MEDIA_BYTES,
                    materialize_descriptor=descriptor,
                )
            finally:
                os.close(descriptor)
            self._require_resource_hash(resource.sha256, entry.sha256)
            if entry.media_type == "image/png":
                with temporary.open("rb") as source:
                    header = source.read(33)
                width, height, bit_depth, color_type, _, _, interlaced = struct.unpack_from(
                    ">IIBBBBB", header, 16
                )
                return {
                    "kind": "png",
                    "width": width,
                    "height": height,
                    "bit_depth": bit_depth,
                    "color_type": color_type,
                    "interlaced": bool(interlaced),
                }
            if entry.media_type == "audio/wav":
                with wave.open(str(temporary), "rb") as source:
                    channels = source.getnchannels()
                    sample_rate = source.getframerate()
                    sample_width_bits = source.getsampwidth() * 8
                    frame_count = source.getnframes()
                return {
                    "kind": "wav",
                    "channels": channels,
                    "sample_rate": sample_rate,
                    "sample_width_bits": sample_width_bits,
                    "frame_count": frame_count,
                    "duration_ms": (frame_count * 1000) // sample_rate,
                }
            with temporary.open("rb") as source:
                header = source.read(12)
            flavor, table_count = struct.unpack_from(">4sH", header)
            return {
                "kind": "font",
                "flavor": "opentype" if flavor == b"OTTO" else "truetype",
                "table_count": table_count,
            }
        except (MediaValidationError, OSError, struct.error, wave.Error) as exc:
            raise conflict("Authorized asset changed or has invalid metadata") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _glb_metadata(self, authority: _Authority, entry: _Entry) -> dict[str, Any]:
        descriptor, name = tempfile.mkstemp(prefix=".worldforge-asset-inspect-")
        temporary = Path(name)
        try:
            try:
                resource = read_resource_snapshot(
                    authority.world_root,
                    entry.relative,
                    limit=MAX_MEDIA_BYTES,
                    materialize_descriptor=descriptor,
                )
            finally:
                os.close(descriptor)
            self._require_resource_hash(resource.sha256, entry.sha256)
            metadata = inspect_glb(temporary)
            return {"kind": "glb", **metadata}
        except (GLBError, MediaValidationError, OSError) as exc:
            raise conflict("Authorized GLB changed or has invalid metadata") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _require_resource_hash(actual: str, expected: str) -> None:
        if actual != expected:
            raise conflict("Authorized asset hash changed")
