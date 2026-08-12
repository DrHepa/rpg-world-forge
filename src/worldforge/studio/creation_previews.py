from __future__ import annotations

import copy
import hashlib
import os
import secrets
import stat
import string
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.file_stat import FileStat, path_file_stat
from isoworld.content.portability import is_portable_path_component
from isoworld.content.resource_snapshot import (
    ResourceSnapshotError,
    ResourceSnapshotOwner,
    ResourceSnapshotReader,
)
from worldforge.generic_asset_processing import (
    GenericAssetProcessingError,
    validate_asset_qa_report_document,
)
from worldforge.generic_assetpack import (
    MAX_GENERIC_ASSETPACK_FILES,
    GenericAssetpackError,
    verify_generic_assetpack,
)
from worldforge.studio.creation_artifacts import CreationArtifactRegistry
from worldforge.studio.creation_evidence import CreationEvidenceManager
from worldforge.studio.creation_output_grants import CreationOutputGrantManager
from worldforge.studio.errors import (
    StudioError,
    conflict,
    invalid_request,
    invalid_state,
    not_found,
)

_CREATION_PREVIEW_CHUNK_BYTES = 64 * 1024
_MAX_CREATION_PREVIEW_BYTES = 64 * 1024 * 1024
_MAX_CREATION_PREVIEW_SEQUENCE = 1023
_HANDLE_LENGTH = 43
_HANDLE_ALPHABET = frozenset(string.ascii_letters + string.digits + "_-")
_SUPPORTED_MEDIA = frozenset({"audio/wav", "image/png"})
_MAX_TREE_ENTRIES = MAX_GENERIC_ASSETPACK_FILES * 2 + 2
_QA_REVIEW_PREVIEW_FIELDS = frozenset(
    {
        "source_kind",
        "workspace_id",
        "expected_root_generation",
        "expected_source_revision",
        "expected_workflow_status_hash",
        "expected_artifact_snapshot_hash",
        "qa_report_artifact_id",
        "asset_id",
        "output_role",
    }
)


def _random_handle() -> str:
    return secrets.token_urlsafe(32)


def _valid_handle(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HANDLE_LENGTH
        and all(character in _HANDLE_ALPHABET for character in value)
    )


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _entry_state(info: FileStat) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_nlink),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
        int(getattr(info, "st_file_attributes", 0)),
    )


def _capture_tree_guard(root: Path) -> tuple[tuple[str, str, tuple[int, ...]], ...]:
    """Capture one bounded exact tree without exposing its native path."""

    absolute = root.absolute()
    try:
        root_before = path_file_stat(absolute)
    except OSError as exc:
        raise conflict("Published creation assetpack is unavailable") from exc
    if _is_link_or_reparse(root_before) or not stat.S_ISDIR(root_before.st_mode):
        raise conflict("Published creation assetpack root is unsafe")

    pending: list[tuple[Path, PurePosixPath | None]] = [(absolute, None)]
    entries: list[tuple[str, str, tuple[int, ...]]] = [("", "directory", _entry_state(root_before))]
    portable_keys: set[tuple[str, ...]] = {()}
    while pending:
        directory, relative_directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name.encode("utf-8"))
        except (OSError, UnicodeError) as exc:
            raise conflict("Published creation assetpack tree is unavailable") from exc
        for child in children:
            name = child.name
            if unicodedata.normalize("NFC", name) != name or not is_portable_path_component(name):
                raise conflict("Published creation assetpack path is not portable")
            relative = (
                PurePosixPath(name) if relative_directory is None else relative_directory / name
            )
            key = tuple(unicodedata.normalize("NFC", part).casefold() for part in relative.parts)
            if key in portable_keys:
                raise conflict("Published creation assetpack has an NFC/casefold alias")
            portable_keys.add(key)
            try:
                info = path_file_stat(directory / name)
            except OSError as exc:
                raise conflict("Published creation assetpack tree changed") from exc
            if _is_link_or_reparse(info):
                raise conflict("Published creation assetpack contains a link or reparse point")
            if stat.S_ISDIR(info.st_mode):
                kind = "directory"
                pending.append((directory / name, relative))
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                kind = "file"
            else:
                raise conflict("Published creation assetpack contains an unsafe entry")
            entries.append((relative.as_posix(), kind, _entry_state(info)))
            if len(entries) > _MAX_TREE_ENTRIES:
                raise invalid_state("Published creation assetpack tree exceeds Studio limits")

    try:
        root_after = path_file_stat(absolute)
    except OSError as exc:
        raise conflict("Published creation assetpack root changed") from exc
    if _entry_state(root_after) != _entry_state(root_before):
        raise conflict("Published creation assetpack root changed")
    return tuple(sorted(entries, key=lambda item: item[0].encode("utf-8")))


@dataclass(frozen=True, slots=True)
class ResolvedCreationPreviewAuthority:
    workspace_id: str
    authority: Mapping[str, object]
    artifact_snapshot_hash: str
    assetpack_artifact_id: str
    output_grant_id: str
    output_grant_generation: int
    asset_id: str
    assetpack_root: Path
    runtime_path: PurePosixPath
    role: str
    media_type: str
    byte_length: int
    sha256: str
    metadata: Mapping[str, object]
    private_guard: object


@dataclass(frozen=True, slots=True)
class ResolvedQaReviewCandidatePreviewAuthority:
    workspace_id: str
    authority: Mapping[str, object]
    artifact_snapshot_hash: str
    qa_report_artifact_id: str
    asset_id: str
    output_role: str
    blob_root: Path
    blob_path: PurePosixPath
    role: str
    media_type: str
    byte_length: int
    sha256: str
    metadata: Mapping[str, object]
    private_guard: object


ResolvedPreviewAuthority = (
    ResolvedCreationPreviewAuthority | ResolvedQaReviewCandidatePreviewAuthority
)


@dataclass(frozen=True, slots=True)
class _CreationPreviewPolicy:
    max_artifact_bytes: int = _MAX_CREATION_PREVIEW_BYTES
    max_workspace_handles: int = 4
    max_workspace_bytes: int = 128 * 1024 * 1024
    max_global_handles: int = 16
    max_global_bytes: int = 256 * 1024 * 1024
    idle_seconds: float = 60.0
    lifetime_seconds: float = 300.0
    reaper_seconds: float = 5.0
    shutdown_wait_seconds: float = 5.0

    def __post_init__(self) -> None:
        integers = (
            self.max_artifact_bytes,
            self.max_workspace_handles,
            self.max_workspace_bytes,
            self.max_global_handles,
            self.max_global_bytes,
        )
        durations = (
            self.idle_seconds,
            self.lifetime_seconds,
            self.reaper_seconds,
            self.shutdown_wait_seconds,
        )
        if any(type(value) is not int or value <= 0 for value in integers):
            raise ValueError("Creation preview quota values must be positive integers")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
            for value in durations
        ):
            raise ValueError("Creation preview durations must be positive numbers")
        if self.max_artifact_bytes > _MAX_CREATION_PREVIEW_BYTES:
            raise ValueError("Creation preview artifacts cannot exceed 64 MiB")


@dataclass(frozen=True, slots=True)
class _CachedRead:
    handle: str
    sequence: int
    payload: bytes
    cumulative_bytes: int
    cumulative_sha256: str
    eof: bool

    def public(self) -> dict[str, object]:
        return {
            "handle": self.handle,
            "sequence": self.sequence,
            "payload": self.payload,
            "cumulative_bytes": self.cumulative_bytes,
            "cumulative_sha256": self.cumulative_sha256,
            "eof": self.eof,
        }


@dataclass(slots=True)
class _Lease:
    handle: str
    workspace_id: str
    authority: ResolvedPreviewAuthority
    reserved_bytes: int
    created_at: float
    last_access: float
    state: str = "opening"
    owner: ResourceSnapshotOwner | Any | None = None
    reader: ResourceSnapshotReader | Any | None = None
    next_sequence: int = 0
    cumulative_bytes: int = 0
    digest: Any = field(default_factory=hashlib.sha256)
    previous: _CachedRead | None = None
    in_flight: bool = True
    cleanup_in_progress: bool = False
    reader_close_attempted: bool = False


class CreationPreviewAuthorityResolver:
    """Resolve published assetpack or retained QA-candidate preview authority."""

    def __init__(
        self,
        evidence: CreationEvidenceManager,
        artifacts: CreationArtifactRegistry,
        output_grants: CreationOutputGrantManager,
    ) -> None:
        self._evidence = evidence
        self._artifacts = artifacts
        self._output_grants = output_grants

    @staticmethod
    def _evidence_params(params: Mapping[str, object]) -> dict[str, object]:
        return {
            "workspace_id": params["workspace_id"],
            "expected_root_generation": params["expected_root_generation"],
            "expected_source_revision": params["expected_source_revision"],
            "expected_workflow_status_hash": params["expected_workflow_status_hash"],
            "expected_artifact_snapshot_hash": params["expected_artifact_snapshot_hash"],
            "artifact_id": params["assetpack_artifact_id"],
        }

    @staticmethod
    def _qa_evidence_params(params: Mapping[str, object]) -> dict[str, object]:
        return {
            "workspace_id": params["workspace_id"],
            "expected_root_generation": params["expected_root_generation"],
            "expected_source_revision": params["expected_source_revision"],
            "expected_workflow_status_hash": params["expected_workflow_status_hash"],
            "expected_artifact_snapshot_hash": params["expected_artifact_snapshot_hash"],
            "artifact_id": params["qa_report_artifact_id"],
        }

    @staticmethod
    def _select_output(
        manifest: Mapping[str, Any], asset_id: object
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        selected = [asset for asset in manifest["assets"] if asset["asset"]["asset_id"] == asset_id]
        if len(selected) != 1:
            raise invalid_request("Creation preview asset is not in the exact assetpack")
        outputs = [
            output for output in selected[0]["outputs"] if output["media_type"] in _SUPPORTED_MEDIA
        ]
        if len(outputs) != 1 or len(selected[0]["outputs"]) != 1:
            raise invalid_request("Creation preview asset has no unique PNG or WAV output")
        return selected[0], outputs[0]

    def resolve(self, params: object) -> ResolvedPreviewAuthority:
        if not isinstance(params, Mapping):
            raise invalid_request("creation_preview.open params must be an object")
        if params.get("source_kind") == "qa_review_candidate":
            return self._resolve_qa_review_candidate(params)
        if "source_kind" in params:
            raise invalid_request("Creation preview source kind is unsupported")
        return self._resolve_published_assetpack(params)

    def _resolve_published_assetpack(
        self,
        params: Mapping[str, object],
    ) -> ResolvedCreationPreviewAuthority:
        inspection = self._evidence.inspect(self._evidence_params(params))
        record = inspection["artifact"]
        if (
            record["lifecycle"] != "candidate"
            or record["subject"]["format"] != "world-forge.assetpack"
            or record["subject"]["format_version"] != 1
        ):
            raise conflict("Creation preview assetpack candidate is not current")
        workspace_id = str(params["workspace_id"])
        artifact_id = str(params["assetpack_artifact_id"])
        manifest = self._artifacts.get_document(workspace_id, artifact_id)
        grant_id = str(params["output_grant_id"])
        generation = int(params["expected_output_grant_generation"])
        grant = self._output_grants.get(grant_id)
        binding = self._output_grants.published_binding(
            grant_id=grant_id,
            workspace_id=workspace_id,
            expected_generation=generation,
        )
        publication = {
            "format": "world-forge.assetpack",
            "format_version": 1,
            "id": manifest.get("assetpack_id"),
            "content_hash": manifest.get("content_hash"),
            "inventory_hash": manifest.get("inventory", {}).get("content_hash"),
        }
        if (
            record["subject"]
            != {
                "format": "world-forge.assetpack",
                "format_version": 1,
                "id": manifest.get("assetpack_id"),
                "content_hash": manifest.get("content_hash"),
            }
            or grant["workspace_id"] != workspace_id
            or grant["kind"] != "generic_assetpack_directory"
            or grant["state"] != "published"
            or grant["generation"] != generation
            or grant["publication"] != publication
            or binding["expected_tree_hash"] != manifest.get("content_hash")
            or binding["published_identity"] is None
        ):
            raise conflict("Creation preview assetpack publication authority changed")
        root = Path(binding["path"])
        try:
            tree_before = _capture_tree_guard(root)
            with verify_generic_assetpack(
                root,
                expected_content_hash=str(manifest["content_hash"]),
                expected_parent_identity=tuple(binding["parent_identity"]),
            ) as verified:
                verified_manifest = verified.manifest
                root_identity = tuple(verified.root_identity)
            tree_after = _capture_tree_guard(root)
        except GenericAssetpackError as exc:
            raise conflict("Creation preview assetpack verification failed") from exc
        if (
            verified_manifest != manifest
            or root_identity != tuple(binding["published_identity"])
            or tree_before != tree_after
        ):
            raise conflict("Creation preview assetpack changed while opening")
        _asset, output = self._select_output(manifest, params["asset_id"])
        runtime_path = PurePosixPath(output["runtime_path"])
        guard = {
            "params": dict(params),
            "inspection": copy.deepcopy(inspection),
            "manifest": copy.deepcopy(manifest),
            "grant": copy.deepcopy(grant),
            "binding": {
                "path": root,
                "parent_identity": tuple(binding["parent_identity"]),
                "published_identity": tuple(binding["published_identity"]),
                "expected_manifest_hash": binding["expected_manifest_hash"],
                "expected_tree_hash": binding["expected_tree_hash"],
            },
            "tree": tree_after,
        }
        return ResolvedCreationPreviewAuthority(
            workspace_id=workspace_id,
            authority=copy.deepcopy(inspection["authority"]),
            artifact_snapshot_hash=str(inspection["artifact_snapshot_hash"]),
            assetpack_artifact_id=artifact_id,
            output_grant_id=grant_id,
            output_grant_generation=generation,
            asset_id=str(params["asset_id"]),
            assetpack_root=root,
            runtime_path=runtime_path,
            role=str(output["role"]),
            media_type=str(output["media_type"]),
            byte_length=int(output["size_bytes"]),
            sha256=str(output["sha256"]),
            metadata=copy.deepcopy(output["metadata"]),
            private_guard=guard,
        )

    def _resolve_qa_review_candidate(
        self,
        params: Mapping[str, object],
        *,
        include_guard: bool = True,
    ) -> ResolvedQaReviewCandidatePreviewAuthority:
        if (
            set(params) != _QA_REVIEW_PREVIEW_FIELDS
            or params.get("source_kind") != "qa_review_candidate"
        ):
            raise invalid_request("QA review candidate preview params are not exact")
        workspace_id = str(params["workspace_id"])
        artifact_id = str(params["qa_report_artifact_id"])
        try:
            inspection = self._evidence.inspect(self._qa_evidence_params(params))
            row = self._artifacts.store.connection.execute(
                "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
                (workspace_id, artifact_id),
            ).fetchone()
            if row is None:
                raise not_found("Creation preview QA report candidate was not found")
            stored = self._artifacts._validated_row(row)  # noqa: SLF001
            report = validate_asset_qa_report_document(stored.document)
            if (
                inspection["artifact"] != stored.record
                or row["workspace_id"] != workspace_id
                or row["subject_format"] != "world-forge.asset_qa_report"
                or int(row["subject_version"]) != 1
                or row["subject_id"] != report["qa_report_id"]
                or row["content_hash"] != report["content_hash"]
                or row["producer_operation"] != "asset.process"
                or int(row["producer_output_position"]) != 2
                or set(stored.record["roles"]) != {"asset_lineage", "asset_qa"}
                or report["asset"]["asset_id"] != params["asset_id"]
            ):
                raise conflict("Creation preview QA report authority changed")

            dependency_rows: dict[str, Any] = {}
            for dependency_artifact_id, identity in stored.dependencies:
                dependency_row = self._artifacts.store.connection.execute(
                    "SELECT * FROM creation_artifacts WHERE workspace_id = ? AND artifact_id = ?",
                    (workspace_id, dependency_artifact_id),
                ).fetchone()
                if dependency_row is None:
                    raise conflict("Creation preview QA lineage is incomplete")
                dependency = self._artifacts._validated_row(dependency_row)  # noqa: SLF001
                if dependency.record["subject"] != identity:
                    raise conflict("Creation preview QA lineage identity changed")
                dependency_rows[str(identity["format"])] = dependency_row

            recipe_row = dependency_rows.get("world-forge.asset_processing_recipe")
            receipt_row = dependency_rows.get("world-forge.asset_processing_receipt")
            if (
                recipe_row is None
                or receipt_row is None
                or recipe_row["producer_job_id"] != row["producer_job_id"]
                or receipt_row["producer_job_id"] != row["producer_job_id"]
                or recipe_row["producer_operation"] != "asset.process"
                or receipt_row["producer_operation"] != "asset.process"
                or int(recipe_row["producer_output_position"]) != 0
                or int(receipt_row["producer_output_position"]) != 1
            ):
                raise conflict("Creation preview QA process lineage changed")

            outputs = [
                output for output in report["outputs"] if output["role"] == params["output_role"]
            ]
            if len(outputs) != 1:
                raise invalid_request("Creation preview QA output role is unavailable")
            output = outputs[0]
            retention = self._artifacts.load_asset_process_retention(
                workspace_id=workspace_id,
                producer_job_id=str(row["producer_job_id"]),
            )
            expected_retention_authority = {
                "root_generation": int(row["root_generation"]),
                "source_revision": str(row["source_revision"]),
                "workflow_status_hash": row["workflow_status_hash"],
                "artifact_snapshot_hash": str(row["input_artifact_snapshot_hash"]),
            }
            retained_outputs = [
                item for item in retention["outputs"] if item["role"] == output["role"]
            ]
            if retention["authority"] != expected_retention_authority or len(retained_outputs) != 1:
                raise conflict("Creation preview retained process authority changed")
            retained = retained_outputs[0]
            if any(
                retained[field] != output[field]
                for field in (
                    "candidate_artifact_id",
                    "role",
                    "media_type",
                    "runtime_path",
                    "locator",
                    "sha256",
                    "size_bytes",
                )
            ):
                raise conflict("Creation preview retained output identity changed")
            retained_bytes = self._artifacts.read_retained_asset_output(
                retention,
                role=str(output["role"]),
            )
            if (
                len(retained_bytes) != int(output["size_bytes"])
                or hashlib.sha256(retained_bytes).hexdigest() != output["sha256"]
            ):
                raise conflict("Creation preview retained output bytes changed")
            blob_root = self._artifacts.store.blobs_dir
            blob_path = PurePosixPath(str(output["sha256"])[:2]) / str(output["sha256"])
            fingerprint = {
                "inspection": copy.deepcopy(inspection),
                "record": copy.deepcopy(stored.record),
                "document": copy.deepcopy(report),
                "dependencies": copy.deepcopy(stored.dependencies),
                "retention": copy.deepcopy(retention),
            }
            private_guard: object = (
                {
                    "params": copy.deepcopy(dict(params)),
                    "fingerprint": fingerprint,
                }
                if include_guard
                else fingerprint
            )
            return ResolvedQaReviewCandidatePreviewAuthority(
                workspace_id=workspace_id,
                authority=copy.deepcopy(inspection["authority"]),
                artifact_snapshot_hash=str(inspection["artifact_snapshot_hash"]),
                qa_report_artifact_id=artifact_id,
                asset_id=str(report["asset"]["asset_id"]),
                output_role=str(output["role"]),
                blob_root=blob_root,
                blob_path=blob_path,
                role=str(output["role"]),
                media_type=str(output["media_type"]),
                byte_length=int(output["size_bytes"]),
                sha256=str(output["sha256"]),
                metadata=copy.deepcopy(output["metadata"]),
                private_guard=private_guard,
            )
        except StudioError:
            raise
        except (GenericAssetProcessingError, KeyError, TypeError, ValueError) as exc:
            raise conflict("Creation preview QA review candidate is invalid") from exc

    def assert_current(self, authority: ResolvedPreviewAuthority) -> None:
        if isinstance(authority, ResolvedQaReviewCandidatePreviewAuthority):
            guard = authority.private_guard
            if not isinstance(guard, Mapping):
                raise invalid_state("Creation preview QA private authority is unavailable")
            params = guard.get("params")
            if not isinstance(params, Mapping):
                raise invalid_state("Creation preview QA private authority is invalid")
            current = self._resolve_qa_review_candidate(params, include_guard=False)
            if current.private_guard != guard.get("fingerprint"):
                raise conflict("Creation preview QA authority changed")
            return
        guard = authority.private_guard
        if not isinstance(guard, Mapping):
            raise invalid_state("Creation preview private authority is unavailable")
        params = guard["params"]
        if not isinstance(params, Mapping):
            raise invalid_state("Creation preview private authority is invalid")
        inspection = self._evidence.inspect(self._evidence_params(params))
        manifest = self._artifacts.get_document(
            authority.workspace_id,
            authority.assetpack_artifact_id,
        )
        grant = self._output_grants.get(authority.output_grant_id)
        binding = self._output_grants.published_binding(
            grant_id=authority.output_grant_id,
            workspace_id=authority.workspace_id,
            expected_generation=authority.output_grant_generation,
        )
        expected_binding = guard["binding"]
        if not isinstance(expected_binding, Mapping):
            raise invalid_state("Creation preview private binding is invalid")
        if (
            inspection != guard["inspection"]
            or manifest != guard["manifest"]
            or grant != guard["grant"]
            or Path(binding["path"]) != expected_binding["path"]
            or tuple(binding["parent_identity"]) != expected_binding["parent_identity"]
            or tuple(binding["published_identity"] or ()) != expected_binding["published_identity"]
            or binding["expected_manifest_hash"] != expected_binding["expected_manifest_hash"]
            or binding["expected_tree_hash"] != expected_binding["expected_tree_hash"]
            or _capture_tree_guard(authority.assetpack_root) != guard["tree"]
        ):
            raise conflict("Creation preview authority changed")


class CreationPreviewManager:
    """Own bounded ephemeral creation-asset preview snapshots without paths."""

    def __init__(
        self,
        resolver: CreationPreviewAuthorityResolver | Any,
        *,
        _policy: _CreationPreviewPolicy | None = None,
        _clock: Callable[[], float] = time.monotonic,
        _owner_factory: Callable[[], ResourceSnapshotOwner] = ResourceSnapshotOwner,
        _token_factory: Callable[[], str] = _random_handle,
        _registration_hook: Callable[[_Lease], None] | None = None,
        _start_reaper: bool = True,
    ) -> None:
        self._resolver = resolver
        self._policy = _policy or _CreationPreviewPolicy()
        self._clock = _clock
        self._owner_factory = _owner_factory
        self._token_factory = _token_factory
        self._registration_hook = _registration_hook
        self._condition = threading.Condition(threading.RLock())
        self._leases: dict[str, _Lease] = {}
        self._closed_handles: dict[str, float] = {}
        self._shutdown = False
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        if _start_reaper:
            self._reaper = threading.Thread(
                target=self._reaper_loop,
                name="creation-preview-reaper",
                daemon=True,
            )
            try:
                self._reaper.start()
            except BaseException:
                self._shutdown = True
                self._stop.set()
                raise

    def open(self, params: object) -> dict[str, object]:
        with self._condition:
            self._require_running()
        authority = self._resolver.resolve(params)
        self._validate_authority(authority)
        lease = self._reserve(authority)
        try:
            self._resolver.assert_current(authority)
            owner = self._owner_factory()
            with self._condition:
                lease.owner = owner
            source_root, source_path = self._materialization_source(authority)
            captured = owner.materialize(
                source_root,
                source_path,
                authority.media_type,
                limit=authority.byte_length,
            )
            if captured.sha256 != authority.sha256:
                raise ResourceSnapshotError("materialized creation preview SHA-256 changed")
            reader = owner.open_reader(source_path)
            with self._condition:
                lease.reader = reader
            if reader.size != authority.byte_length or reader.sha256 != authority.sha256:
                raise ResourceSnapshotError("creation preview reader identity changed")
            self._resolver.assert_current(authority)
            if self._registration_hook is not None:
                self._registration_hook(lease)
            with self._condition:
                if self._shutdown or lease.state != "opening":
                    raise invalid_state("Creation preview manager is shut down")
                lease.state = "active"
                lease.in_flight = False
                lease.last_access = self._clock()
                self._condition.notify_all()
            if isinstance(authority, ResolvedQaReviewCandidatePreviewAuthority):
                return {
                    "format": "world-forge.studio_creation_preview",
                    "format_version": 2,
                    "handle": lease.handle,
                    "workspace_id": authority.workspace_id,
                    "source": {
                        "kind": "qa_review_candidate",
                        "qa_report_artifact_id": authority.qa_report_artifact_id,
                        "asset_id": authority.asset_id,
                        "output_role": authority.output_role,
                    },
                    "media_type": authority.media_type,
                    "byte_length": authority.byte_length,
                    "sha256": authority.sha256,
                    "chunk_bytes": _CREATION_PREVIEW_CHUNK_BYTES,
                    "metadata": copy.deepcopy(dict(authority.metadata)),
                }
            return {
                "format": "world-forge.studio_creation_preview",
                "format_version": 1,
                "handle": lease.handle,
                "workspace_id": authority.workspace_id,
                "assetpack_artifact_id": authority.assetpack_artifact_id,
                "output_grant_id": authority.output_grant_id,
                "output_grant_generation": authority.output_grant_generation,
                "asset_id": authority.asset_id,
                "media_type": authority.media_type,
                "byte_length": authority.byte_length,
                "sha256": authority.sha256,
                "chunk_bytes": _CREATION_PREVIEW_CHUNK_BYTES,
                "metadata": copy.deepcopy(dict(authority.metadata)),
            }
        except StudioError:
            self._abort(lease, remember=False)
            raise
        except Exception as exc:
            self._abort(lease, remember=False)
            raise conflict("Creation preview changed or failed while opening") from exc
        except BaseException:
            self._abort(lease, remember=False)
            raise

    def read(self, handle: object, sequence: object) -> dict[str, object]:
        normalized_sequence = self._sequence(sequence)
        cleanup_expired = False
        with self._condition:
            self._require_running()
            lease = self._available_lease(handle)
            now = self._clock()
            if self._expired(lease, now):
                lease.state = "closing"
                cleanup_expired = True
            elif lease.in_flight:
                raise conflict("Creation preview read is already in progress")
            if cleanup_expired:
                pass
            elif lease.previous is not None and normalized_sequence == lease.previous.sequence:
                replay = True
            elif lease.state == "active" and normalized_sequence == lease.next_sequence:
                replay = False
            else:
                raise conflict("Creation preview sequence conflict")
            if not cleanup_expired:
                lease.in_flight = True

        if cleanup_expired:
            self._cleanup(lease.handle, remember=True)
            raise not_found("Creation preview handle is unavailable")

        try:
            self._resolver.assert_current(lease.authority)
            if replay:
                cached = lease.previous
                assert cached is not None
                pending = cached
                pending_digest = None
            else:
                reader = lease.reader
                if reader is None:
                    raise ResourceSnapshotError("creation preview reader is unavailable")
                chunk = reader.read_next()
                if not isinstance(chunk.payload, bytes):
                    raise ResourceSnapshotError("creation preview chunk payload is invalid")
                pending_digest = lease.digest.copy()
                pending_digest.update(chunk.payload)
                self._validate_chunk(
                    lease,
                    normalized_sequence,
                    chunk.sequence,
                    chunk.payload,
                    chunk.cumulative_bytes,
                    chunk.cumulative_sha256,
                    chunk.eof,
                    pending_digest.hexdigest(),
                )
                pending = _CachedRead(
                    handle=lease.handle,
                    sequence=chunk.sequence,
                    payload=chunk.payload,
                    cumulative_bytes=chunk.cumulative_bytes,
                    cumulative_sha256=chunk.cumulative_sha256,
                    eof=chunk.eof,
                )
            self._resolver.assert_current(lease.authority)

            cleanup_cancelled = False
            with self._condition:
                now = self._clock()
                if lease.state not in {"active", "eof"} or self._expired(lease, now):
                    lease.state = "closing"
                    cleanup_cancelled = True
                elif replay:
                    if lease.previous != pending:
                        lease.state = "closing"
                        cleanup_cancelled = True
                elif normalized_sequence != lease.next_sequence:
                    lease.state = "closing"
                    cleanup_cancelled = True
                else:
                    assert pending_digest is not None
                    lease.digest = pending_digest
                    lease.cumulative_bytes = pending.cumulative_bytes
                    lease.previous = pending
                    lease.next_sequence += 1
                    lease.state = "eof" if pending.eof else "active"
                lease.last_access = now
                lease.in_flight = False
                self._condition.notify_all()
            if cleanup_cancelled:
                self._cleanup(lease.handle, remember=True)
                raise not_found("Creation preview handle is unavailable")
            return pending.public()
        except StudioError:
            self._abort(lease, remember=True)
            raise
        except Exception as exc:
            self._abort(lease, remember=True)
            raise conflict("Creation preview read failed") from exc
        except BaseException:
            self._abort(lease, remember=True)
            raise

    def close(self, handle: object) -> bool:
        if not _valid_handle(handle):
            raise not_found("Creation preview handle is unavailable")
        assert isinstance(handle, str)
        deadline = time.monotonic() + self._policy.shutdown_wait_seconds
        target: _Lease | None = None
        while True:
            with self._condition:
                self._purge_tombstones(self._clock())
                if handle in self._closed_handles:
                    return True
                current = self._leases.get(handle)
                if target is None:
                    if current is None:
                        raise not_found("Creation preview handle is unavailable")
                    target = current
                elif current is not target:
                    return True
                if target.state != "closed":
                    target.state = "closing"
                if target.in_flight or target.cleanup_in_progress:
                    remaining = max(0.0, deadline - time.monotonic())
                    completed = self._condition.wait_for(
                        lambda lease=target: (
                            self._leases.get(handle) is not lease
                            or not (lease.in_flight or lease.cleanup_in_progress)
                        ),
                        timeout=remaining,
                    )
                    if not completed:
                        raise conflict("Creation preview close timed out")
                    continue

            if self._cleanup(handle, remember=True):
                return True

            with self._condition:
                if handle in self._closed_handles or self._leases.get(handle) is not target:
                    return True
                if target.in_flight or target.cleanup_in_progress:
                    remaining = max(0.0, deadline - time.monotonic())
                    completed = self._condition.wait_for(
                        lambda lease=target: (
                            self._leases.get(handle) is not lease
                            or not (lease.in_flight or lease.cleanup_in_progress)
                        ),
                        timeout=remaining,
                    )
                    if not completed:
                        raise conflict("Creation preview close timed out")
                    continue
                raise StudioError("internal_error", "Creation preview cleanup failed")

    def shutdown(self) -> None:
        deadline = time.monotonic() + self._policy.shutdown_wait_seconds
        with self._condition:
            self._shutdown = True
            self._stop.set()
            for lease in self._leases.values():
                if lease.state != "closed":
                    lease.state = "closing"
            reaper = self._reaper
        if reaper is not None and reaper.is_alive() and reaper is not threading.current_thread():
            reaper.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    not any(
                        lease.in_flight or lease.cleanup_in_progress
                        for lease in self._leases.values()
                    )
                ),
                timeout=max(0.0, deadline - time.monotonic()),
            )
            handles = tuple(self._leases)
        for handle in handles:
            self._cleanup(handle, remember=False)
        with self._condition:
            if self._leases or (
                reaper is not None
                and reaper is not threading.current_thread()
                and reaper.is_alive()
            ):
                raise StudioError("internal_error", "Creation preview shutdown cleanup failed")
            self._closed_handles.clear()

    def _reserve(self, authority: ResolvedPreviewAuthority) -> _Lease:
        now = self._clock()
        with self._condition:
            self._require_running()
            self._purge_tombstones(now)
            workspace_leases = [
                lease
                for lease in self._leases.values()
                if lease.workspace_id == authority.workspace_id
            ]
            if len(workspace_leases) >= self._policy.max_workspace_handles:
                raise invalid_state("workspace creation preview quota exceeded")
            if (
                sum(lease.reserved_bytes for lease in workspace_leases) + authority.byte_length
                > self._policy.max_workspace_bytes
            ):
                raise invalid_state("workspace creation preview quota exceeded")
            if len(self._leases) >= self._policy.max_global_handles:
                raise invalid_state("global creation preview quota exceeded")
            if (
                sum(lease.reserved_bytes for lease in self._leases.values()) + authority.byte_length
                > self._policy.max_global_bytes
            ):
                raise invalid_state("global creation preview quota exceeded")
            handle = ""
            for _ in range(128):
                candidate = self._token_factory()
                if not _valid_handle(candidate):
                    raise StudioError("internal_error", "Creation preview handle generation failed")
                if candidate not in self._leases and candidate not in self._closed_handles:
                    handle = candidate
                    break
            if not handle:
                raise StudioError("internal_error", "Creation preview handle generation failed")
            lease = _Lease(
                handle=handle,
                workspace_id=authority.workspace_id,
                authority=authority,
                reserved_bytes=authority.byte_length,
                created_at=now,
                last_access=now,
            )
            self._leases[handle] = lease
            return lease

    def _cleanup(self, handle: str, *, remember: bool) -> bool:
        with self._condition:
            lease = self._leases.get(handle)
            if lease is None:
                return handle in self._closed_handles or not remember
            if lease.in_flight or lease.cleanup_in_progress:
                return False
            lease.cleanup_in_progress = True
            lease.state = "closing"
            reader = lease.reader
            owner = lease.owner

        if reader is not None and not lease.reader_close_attempted and not reader.closed:
            lease.reader_close_attempted = True
            try:
                reader.close()
            except Exception:
                with self._condition:
                    lease.cleanup_in_progress = False
                    lease.state = "quarantined"
                    self._condition.notify_all()
                return False
        if owner is not None and not owner.closed:
            try:
                owner.close()
            except Exception:
                if not owner.closed:
                    with self._condition:
                        lease.cleanup_in_progress = False
                        lease.state = "quarantined"
                        self._condition.notify_all()
                    return False
        if owner is not None and not owner.closed:
            with self._condition:
                lease.cleanup_in_progress = False
                lease.state = "quarantined"
                self._condition.notify_all()
            return False

        with self._condition:
            current = self._leases.get(handle)
            if current is lease:
                lease.cleanup_in_progress = False
                lease.state = "closed"
                self._leases.pop(handle)
                if remember:
                    self._closed_handles[handle] = self._clock() + self._policy.lifetime_seconds
                    while len(self._closed_handles) > self._policy.max_global_handles * 4:
                        self._closed_handles.pop(next(iter(self._closed_handles)))
            self._condition.notify_all()
        return True

    def _abort(self, lease: _Lease, *, remember: bool) -> None:
        with self._condition:
            lease.state = "closing"
            lease.in_flight = False
            self._condition.notify_all()
        self._cleanup(lease.handle, remember=remember)

    def _available_lease(self, handle: object) -> _Lease:
        if not _valid_handle(handle):
            raise not_found("Creation preview handle is unavailable")
        assert isinstance(handle, str)
        lease = self._leases.get(handle)
        if lease is None or lease.state not in {"active", "eof"}:
            raise not_found("Creation preview handle is unavailable")
        return lease

    @staticmethod
    def _sequence(value: object) -> int:
        if type(value) is not int or not 0 <= value <= _MAX_CREATION_PREVIEW_SEQUENCE:
            raise invalid_request(
                "Creation preview sequence must be an integer from 0 to "
                f"{_MAX_CREATION_PREVIEW_SEQUENCE}"
            )
        return value

    def _expired(self, lease: _Lease, now: float) -> bool:
        return (
            now - lease.last_access >= self._policy.idle_seconds
            or now - lease.created_at >= self._policy.lifetime_seconds
        )

    @staticmethod
    def _materialization_source(authority: ResolvedPreviewAuthority) -> tuple[Path, PurePosixPath]:
        if isinstance(authority, ResolvedQaReviewCandidatePreviewAuthority):
            return authority.blob_root, authority.blob_path
        return authority.assetpack_root, authority.runtime_path

    def _validate_authority(self, authority: ResolvedPreviewAuthority) -> None:
        if (
            type(authority.byte_length) is not int
            or not 1 <= authority.byte_length <= self._policy.max_artifact_bytes
        ):
            raise invalid_request("creation asset preview quota exceeded")
        if authority.media_type not in _SUPPORTED_MEDIA:
            raise invalid_request("Creation preview asset is not PNG or WAV")
        _source_root, source_path = self._materialization_source(authority)
        if (
            not isinstance(source_path, PurePosixPath)
            or source_path.is_absolute()
            or not source_path.parts
            or any(
                part in {"", ".", ".."} or not is_portable_path_component(part)
                for part in source_path.parts
            )
        ):
            raise StudioError("internal_error", "Creation preview authority is invalid")
        if not isinstance(authority.metadata, Mapping):
            raise invalid_request("Creation preview metadata is unsupported")
        expected_kind = "png" if authority.media_type == "image/png" else "wav_pcm16"
        if authority.metadata.get("kind") != expected_kind:
            raise invalid_request("Creation preview metadata is unsupported")

    @staticmethod
    def _validate_chunk(
        lease: _Lease,
        requested_sequence: int,
        actual_sequence: int,
        payload: bytes,
        cumulative_bytes: int,
        cumulative_sha256: str,
        eof: bool,
        computed_sha256: str,
    ) -> None:
        expected_cumulative = lease.cumulative_bytes + len(payload)
        expected_payload_bytes = min(
            _CREATION_PREVIEW_CHUNK_BYTES,
            lease.authority.byte_length - lease.cumulative_bytes,
        )
        if (
            type(actual_sequence) is not int
            or type(cumulative_bytes) is not int
            or not isinstance(cumulative_sha256, str)
            or not isinstance(eof, bool)
            or actual_sequence != requested_sequence
            or actual_sequence != lease.next_sequence
            or not isinstance(payload, bytes)
            or len(payload) != expected_payload_bytes
            or cumulative_bytes != expected_cumulative
            or cumulative_sha256 != computed_sha256
            or cumulative_bytes > lease.authority.byte_length
        ):
            raise ResourceSnapshotError("creation preview chunk integrity changed")
        if eof:
            if (
                cumulative_bytes != lease.authority.byte_length
                or cumulative_sha256 != lease.authority.sha256
            ):
                raise ResourceSnapshotError("creation preview EOF integrity changed")
        elif not payload or cumulative_bytes >= lease.authority.byte_length:
            raise ResourceSnapshotError("creation preview ended at an unauthorized boundary")

    def _purge_tombstones(self, now: float) -> None:
        for handle, expiry in tuple(self._closed_handles.items()):
            if now >= expiry:
                self._closed_handles.pop(handle, None)

    def _reap_once(self) -> None:
        now = self._clock()
        with self._condition:
            self._purge_tombstones(now)
            handles: list[str] = []
            for lease in self._leases.values():
                if (
                    lease.state == "opening"
                    and now - lease.created_at >= self._policy.lifetime_seconds
                ):
                    lease.state = "closing"
                if lease.state in {"active", "eof"} and self._expired(lease, now):
                    lease.state = "closing"
                if lease.state in {"closing", "quarantined"} and not lease.in_flight:
                    handles.append(lease.handle)
        for handle in handles:
            self._cleanup(handle, remember=True)

    def _reaper_loop(self) -> None:
        while not self._stop.wait(self._policy.reaper_seconds):
            self._reap_once()

    def _require_running(self) -> None:
        if self._shutdown:
            raise invalid_state("Creation preview manager is shut down")
