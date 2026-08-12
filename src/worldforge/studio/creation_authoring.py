from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
import stat
import unicodedata
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.file_stat import path_file_stat
from isoworld.content.portability import portable_relative_path
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.creation_contracts import (
    MAX_CREATION_CONTRACT_BYTES,
    CreationContractError,
    LoadedCreationProject,
    validate_creation_documents,
)
from worldforge.directory_publish import (
    DirectoryPublishError,
    DirectoryPublishIndeterminateError,
    append_append_only_journal,
    create_append_only_journal,
    fsync_directory,
    read_append_only_journal_history_state,
    remove_append_only_journal,
)
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.phase_report_v3 import PhaseReportV3Error, document_identity
from worldforge.studio.changesets import (
    _identity,
    _is_link_or_reparse,
    _open_pinned_parent,
    _path_info,
    _PinnedParent,
    _reject_pinned_collision,
    _reject_sibling_collisions,
    _safe_directory_info,
    _safe_entry_snapshot,
    _safe_file_snapshot,
    _verify_pinned_parents,
    read_workspace_file_snapshot,
)
from worldforge.studio.contracts import (
    CREATION_CHANGESET_FORMAT,
    CREATION_CHANGESET_STATES,
    ENTITY_ID_PATTERN,
    MAX_CHANGE_FILE_BYTES,
    MAX_CHANGESET_BYTES,
    MAX_CHANGESET_OPERATIONS,
    SHA256_PATTERN,
    WORKSPACE_ID_PATTERN,
    creation_changeset_record_hash,
    validate_studio_creation_changeset,
    validate_studio_creation_workspace,
)
from worldforge.studio.creation_workspaces import CreationWorkspaceManager, _module_paths
from worldforge.studio.errors import (
    StudioContractError,
    StudioError,
    conflict,
    invalid_request,
    invalid_state,
    not_found,
)
from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now
from worldforge.world_lock import exclusive_world_lifecycle

_COLLECTIONS = (
    "world_modules",
    "activity_modules",
    "narrative_modules",
    "system_modules",
    "logic_modules",
)
_RESERVED_PARTS = frozenset({".worldforge", "artifacts", "assets", "runtime", "output", "outputs"})
_JOURNAL_FORMAT = "world-forge.studio_creation_apply_journal"
_JOURNAL_VERSION = 1
_MAX_JOURNAL_RECORD_BYTES = 2 * 1024 * 1024
_MAX_JOURNAL_FILE_BYTES = 512 * 1024 * 1024


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _identifier(value: object, *, field: str, workspace: bool = False) -> str:
    pattern = WORKSPACE_ID_PATTERN if workspace else ENTITY_ID_PATTERN
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise invalid_request(f"{field} is not a valid identifier")
    return value


def _digest(value: object, *, field: str, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise invalid_request(f"{field} must be a lowercase SHA-256 digest")
    return value


def _generation(value: object, *, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > 9_007_199_254_740_991
    ):
        raise invalid_request(f"{field} must be an integer from 0 to 9007199254740991")
    return value


def _portable_creation_path(value: object, *, context: str) -> PurePosixPath:
    try:
        relative = portable_relative_path(value)
    except UnicodeError as exc:
        raise invalid_request(f"{context} must be a portable relative path") from exc
    if relative is None or unicodedata.normalize("NFC", relative.as_posix()) != relative.as_posix():
        raise invalid_request(f"{context} must be an NFC portable relative path")
    if any(part.casefold() in _RESERVED_PARTS for part in relative.parts):
        raise invalid_request(f"{context} is outside the creation source graph")
    return relative


def _safe_creation_target(root: Path, relative: PurePosixPath) -> Path:
    current_path = root
    for part in relative.parts[:-1]:
        _reject_sibling_collisions(
            current_path,
            part,
            context=f"creation changeset path component {part}",
        )
        current_path /= part
        try:
            info = path_file_stat(current_path)
        except OSError as exc:
            raise invalid_request(f"Creation changeset parent is missing: {relative}") from exc
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise invalid_request(f"Creation changeset parent is unsafe: {relative}")
    _reject_sibling_collisions(
        current_path,
        relative.name,
        context=f"creation changeset target {relative}",
    )
    return current_path / relative.name


@contextmanager
def _pinned_creation_parents(
    journal: Mapping[str, Any], root: Path
) -> Iterator[list[_PinnedParent]]:
    root_path = root
    encoded_root_identity = journal.get("root_identity")
    if (
        not isinstance(encoded_root_identity, list)
        or len(encoded_root_identity) != 2
        or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in encoded_root_identity
        )
    ):
        raise StudioError("internal_error", "Creation changeset root identity is invalid")
    root_identity = (encoded_root_identity[0], encoded_root_identity[1])
    cache: dict[object, tuple[tuple[int, int], _PinnedParent]] = {}
    parents: list[_PinnedParent] = []
    with ExitStack() as stack:
        for operation in journal["operations"]:
            relative = _portable_creation_path(
                operation.get("path"),
                context="creation changeset journal path",
            )
            parent_path = root_path.joinpath(*relative.parts[:-1])
            encoded_parent = operation.get("parent_identity")
            if (
                not isinstance(encoded_parent, list)
                or len(encoded_parent) != 2
                or any(
                    isinstance(item, bool) or not isinstance(item, int) or item < 0
                    for item in encoded_parent
                )
            ):
                raise StudioError("internal_error", "Creation changeset parent identity is invalid")
            expected = (encoded_parent[0], encoded_parent[1])
            cached = cache.get(parent_path)
            if cached is not None:
                if cached[0] != expected:
                    raise StudioError(
                        "internal_error",
                        "Creation changeset parent identities are inconsistent",
                    )
                parent = cached[1]
            else:
                parent = stack.enter_context(
                    _open_pinned_parent(
                        root_path,
                        relative,
                        world_identity=root_identity,
                        parent_identity=expected,
                    )
                )
                cache[parent_path] = (expected, parent)
            _reject_pinned_collision(
                parent,
                relative.name,
                context=f"creation changeset target {relative}",
            )
            parents.append(parent)
        _verify_pinned_parents(parents)
        yield parents


def _operation_commit_count(phase: str, operation_count: int) -> int:
    if phase in {"before_staging", "stages_prepared"}:
        return 0
    if phase in {"files_committed", "database_committed"}:
        return operation_count
    prefix = "operation_"
    suffix = "_committed"
    if phase.startswith(prefix) and phase.endswith(suffix):
        encoded = phase[len(prefix) : -len(suffix)]
        if encoded.isdigit():
            count = int(encoded)
            if 1 <= count <= operation_count:
                return count
    raise StudioError("internal_error", "Creation changeset journal phase is invalid")


def _journal_phases(operation_count: int) -> tuple[str, ...]:
    return (
        "before_staging",
        "stages_prepared",
        *(f"operation_{index}_committed" for index in range(1, operation_count + 1)),
        "files_committed",
        "database_committed",
    )


def _journal_payload(
    base: Mapping[str, Any],
    *,
    phase: str,
    stage_identities: tuple[tuple[int, int] | None, ...],
) -> bytes:
    operations = base["operations"]
    if not isinstance(operations, list) or len(stage_identities) != len(operations):
        raise StudioError("internal_error", "Creation changeset journal operation mismatch")
    phases = _journal_phases(len(operations))
    if phase not in phases:
        raise StudioError("internal_error", "Creation changeset journal phase is invalid")
    committed = _operation_commit_count(phase, len(operations))
    include_stages = phases.index(phase) >= phases.index("stages_prepared")
    journal_operations = []
    for index, operation in enumerate(operations):
        identity = stage_identities[index]
        if operation["operation"] == "delete":
            if identity is not None:
                raise StudioError(
                    "internal_error", "Creation delete operation has a stage identity"
                )
        elif include_stages and identity is None:
            raise StudioError("internal_error", "Creation changeset stage identity is missing")
        journal_operations.append(
            {
                **operation,
                "stage_identity": (
                    None if not include_stages or identity is None else [identity[0], identity[1]]
                ),
                "applied": index < committed,
            }
        )
    return canonical_json_bytes({**base, "phase": phase, "operations": journal_operations})


def _expected_journal_history(
    base: Mapping[str, Any],
    *,
    through_phase: str,
    stage_identities: tuple[tuple[int, int] | None, ...],
) -> tuple[bytes, ...]:
    phases = _journal_phases(len(base["operations"]))
    try:
        end = phases.index(through_phase)
    except ValueError as exc:
        raise StudioError("internal_error", "Creation changeset journal phase is invalid") from exc
    return tuple(
        _journal_payload(base, phase=phase, stage_identities=stage_identities)
        for phase in phases[: end + 1]
    )


def _reference_path(value: object, *, context: str) -> PurePosixPath:
    if not isinstance(value, Mapping):
        raise invalid_request(f"{context} must be a document reference")
    return _portable_creation_path(value.get("path"), context=f"{context}/path")


def _validated_project_from_documents(
    documents: Mapping[PurePosixPath, dict[str, Any]],
) -> LoadedCreationProject:
    project_path = PurePosixPath("project.json")
    project = documents.get(project_path)
    if project is None:
        raise invalid_request("Proposed creation graph must contain project.json")
    profile_path = _reference_path(project.get("profile"), context="project/profile")
    manifest_path = _reference_path(
        project.get("source_manifest"),
        context="project/source_manifest",
    )
    profile = documents.get(profile_path)
    manifest = documents.get(manifest_path)
    if profile is None or manifest is None:
        raise invalid_request("Proposed creation graph is missing a referenced root document")
    modules = manifest.get("modules")
    if not isinstance(modules, Mapping):
        raise invalid_request("Proposed source manifest modules must be an object")
    manifest_root = manifest_path.parent
    supplied: dict[str, list[dict[str, Any]]] = {name: [] for name in _COLLECTIONS}
    expected_paths = {project_path, profile_path, manifest_path}
    for collection in _COLLECTIONS:
        references = modules.get(collection)
        if not isinstance(references, list):
            raise invalid_request(f"Proposed source manifest {collection} must be an array")
        for index, reference in enumerate(references):
            relative = _reference_path(
                reference,
                context=f"source manifest/{collection}/{index}",
            )
            module_path = _portable_creation_path(
                (manifest_root / relative).as_posix(),
                context=f"source manifest/{collection}/{index}/resolved_path",
            )
            module = documents.get(module_path)
            if module is None:
                raise invalid_request(f"Proposed creation graph is missing {module_path}")
            expected_paths.add(module_path)
            supplied[collection].append(module)
    if set(documents) != expected_paths:
        extras = sorted(
            (path.as_posix() for path in set(documents) - expected_paths),
            key=lambda item: item.encode("utf-8"),
        )
        raise invalid_request(
            "Proposed creation graph contains undeclared documents"
            + (" " + ", ".join(extras) if extras else "")
        )
    try:
        return validate_creation_documents(
            project,
            profile,
            manifest,
            supplied["world_modules"],
            supplied["activity_modules"],
            supplied["narrative_modules"],
            supplied["system_modules"],
            supplied["logic_modules"],
        )
    except CreationContractError as exc:
        raise invalid_request(f"Proposed creation graph is invalid: {exc}") from exc


def _source_revision(
    project: LoadedCreationProject,
    payloads: Mapping[PurePosixPath, bytes],
) -> str:
    summaries: list[dict[str, Any]] = []
    try:
        for relative, document in _module_paths(project):
            payload = payloads[relative]
            identity = document_identity(document)
            summaries.append(
                {
                    "path": relative.as_posix(),
                    "format": document["format"],
                    "format_version": document["format_version"],
                    "id": identity["id"],
                    "content_hash": document["content_hash"],
                    "file_sha256": _hash(payload),
                }
            )
    except (KeyError, PhaseReportV3Error) as exc:
        raise invalid_request(
            "Proposed creation graph cannot produce an integral revision"
        ) from exc
    revision_payload = encode_json({"documents": summaries}).encode("utf-8")
    return _hash(revision_payload)


class CreationAuthoringManager:
    """Reviewable aggregate authoring for generic creation workspaces only."""

    def __init__(
        self,
        store: StudioStore,
        *,
        workspaces: CreationWorkspaceManager | None = None,
        mutation_hook: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self.store = store
        self.workspaces = workspaces or CreationWorkspaceManager(store)
        self._mutation_hook = mutation_hook

    def _notify(self, phase: str, **details: object) -> None:
        if self._mutation_hook is not None:
            self._mutation_hook(phase, dict(details))

    def create(self, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("creation_changeset.create params must be an object")
        allowed = {
            "changeset_id",
            "workspace_id",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "operations",
        }
        required = allowed - {"changeset_id"}
        missing = required - set(params)
        unknown = set(params) - allowed
        if missing or unknown:
            fields = missing or unknown
            raise invalid_request(
                "creation_changeset.create has invalid fields: " + ", ".join(sorted(fields))
            )
        changeset_id = _identifier(
            params.get("changeset_id") or uuid.uuid4().hex,
            field="changeset_id",
        )
        workspace_id = _identifier(
            params["workspace_id"],
            field="workspace_id",
            workspace=True,
        )
        expected_generation = _generation(
            params["expected_root_generation"],
            field="expected_root_generation",
        )
        expected_revision = _digest(
            params["expected_source_revision"],
            field="expected_source_revision",
        )
        expected_workflow_hash = _digest(
            params["expected_workflow_status_hash"],
            field="expected_workflow_status_hash",
            nullable=True,
        )
        operations = params["operations"]
        if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_CHANGESET_OPERATIONS:
            raise invalid_request(
                "creation changeset operations must contain 1 to "
                f"{MAX_CHANGESET_OPERATIONS} entries"
            )
        if self.store.connection.execute(
            "SELECT 1 FROM creation_changesets WHERE changeset_id = ?",
            (changeset_id,),
        ).fetchone():
            raise conflict(f"Creation changeset {changeset_id} already exists")

        row = self.workspaces._row(workspace_id)
        root, root_identity = self.workspaces._verified_root(row)
        try:
            with exclusive_world_lifecycle(root, error_type=ValueError):
                record, current_project, summaries, current_revision, workflow = (
                    self.workspaces._refresh_snapshot(workspace_id)
                )
                del summaries
                if record["root_generation"] != expected_generation:
                    raise conflict("Creation workspace generation changed")
                if not hmac.compare_digest(current_revision, expected_revision):
                    raise conflict("Creation workspace source revision changed")
                if workflow["status_hash"] != expected_workflow_hash:
                    raise conflict("Creation workspace workflow status changed")
                current_root, current_identity = self.workspaces._verified_root(
                    self.workspaces._row(workspace_id)
                )
                if current_root != root or current_identity != root_identity:
                    raise conflict("Creation workspace root identity changed")
                current_documents = {
                    relative: document for relative, document in _module_paths(current_project)
                }
                current_payloads = {
                    relative: read_workspace_file_snapshot(
                        root,
                        relative,
                        world_identity=root_identity,
                        context=f"creation authoring base {relative.as_posix()}",
                        limit=MAX_CHANGE_FILE_BYTES,
                    )
                    for relative in current_documents
                }
                self._notify("staging_snapshot_captured", workspace_id=workspace_id)
                if not hmac.compare_digest(
                    _source_revision(current_project, current_payloads), current_revision
                ):
                    raise conflict("Creation workspace changed while staging changeset")
                confirmed = self.workspaces._scan_integral_snapshot(
                    workspace_id,
                    root,
                    root_identity,
                    root_generation=record["root_generation"],
                    notify=False,
                )
                if confirmed is None or confirmed[2] != current_revision:
                    raise conflict("Creation workspace changed while staging changeset")
        except ValueError as exc:
            raise conflict("Creation workspace lifecycle authority changed") from exc
        proposed_documents = dict(current_documents)
        proposed_payloads = dict(current_payloads)
        captured: list[tuple[dict[str, Any], bytes | None, bytes | None]] = []
        seen: set[tuple[str, ...]] = set()
        for index, raw in enumerate(operations):
            public, base_payload, proposed_payload, proposed_document = self._capture_operation(
                raw,
                index=index,
                current_documents=current_documents,
                current_payloads=current_payloads,
            )
            relative = PurePosixPath(public["path"])
            key = tuple(part.casefold() for part in relative.parts)
            if key in seen:
                raise invalid_request("Creation changeset operation paths collide")
            seen.add(key)
            if public["operation"] == "delete":
                proposed_documents.pop(relative, None)
                proposed_payloads.pop(relative, None)
            else:
                assert proposed_document is not None and proposed_payload is not None
                proposed_documents[relative] = proposed_document
                proposed_payloads[relative] = proposed_payload
            captured.append((public, base_payload, proposed_payload))

        proposed_project = _validated_project_from_documents(proposed_documents)
        final_paths = {relative for relative, _document in _module_paths(proposed_project)}
        if final_paths != set(proposed_documents):
            raise invalid_request("Proposed creation graph path closure is inconsistent")
        changed_paths = {
            path
            for path in set(current_payloads) | set(proposed_payloads)
            if current_payloads.get(path) != proposed_payloads.get(path)
        }
        operation_paths = {PurePosixPath(item[0]["path"]) for item in captured}
        if changed_paths != operation_paths:
            raise invalid_request("Creation changeset operations do not exactly describe the graph")
        proposed_revision = _source_revision(proposed_project, proposed_payloads)
        assert expected_revision is not None
        if hmac.compare_digest(expected_revision, proposed_revision):
            raise invalid_request("Creation changeset does not change the source revision")

        public_operations = sorted(
            (item[0] for item in captured),
            key=lambda operation: operation["path"].encode("utf-8"),
        )
        review_sha256 = canonical_payload_hash(
            {
                "workspace_id": workspace_id,
                "expected_root_generation": expected_generation,
                "expected_source_revision": expected_revision,
                "proposed_source_revision": proposed_revision,
                "expected_workflow_status_hash": expected_workflow_hash,
                "operations": public_operations,
            },
            hash_field="_unused",
        )
        timestamp = utc_now()
        changeset: dict[str, Any] = {
            "format": CREATION_CHANGESET_FORMAT,
            "format_version": 1,
            "changeset_id": changeset_id,
            "workspace_id": workspace_id,
            "status": "staged",
            "expected_root_generation": expected_generation,
            "expected_source_revision": expected_revision,
            "proposed_source_revision": proposed_revision,
            "expected_workflow_status_hash": expected_workflow_hash,
            "review_sha256": review_sha256,
            "operations": public_operations,
            "created_at": timestamp,
            "updated_at": timestamp,
            "record_hash": "",
        }
        changeset["record_hash"] = creation_changeset_record_hash(changeset)
        try:
            validate_studio_creation_changeset(changeset)
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc

        total = sum(
            (0 if base is None else len(base)) + (0 if proposed is None else len(proposed))
            for _public, base, proposed in captured
        )
        if total > MAX_CHANGESET_BYTES:
            raise invalid_request("Creation changeset retained bytes exceed the aggregate limit")
        for public, base_payload, proposed_payload in captured:
            if base_payload is not None:
                self._store_blob(base_payload, public["expected_base_file_sha256"])
            if proposed_payload is not None:
                self._store_blob(proposed_payload, public["proposed_file_sha256"])
        try:
            with self.store.connection:
                self.store.connection.execute(
                    "INSERT INTO creation_changesets "
                    "(changeset_id, workspace_id, status, record_json, generation) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (changeset_id, workspace_id, "staged", encode_json(changeset)),
                )
                self.store.connection.executemany(
                    "INSERT INTO creation_changeset_operations "
                    "(changeset_id, path, operation, base_blob_sha256, base_size, "
                    "proposed_blob_sha256, proposed_size) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            changeset_id,
                            public["path"],
                            public["operation"],
                            public["expected_base_file_sha256"],
                            public["expected_base_size"],
                            public["proposed_file_sha256"],
                            public["proposed_size"],
                        )
                        for public in public_operations
                    ],
                )
                self.store.record_creation_event(
                    workspace_id=workspace_id,
                    topic="creation_changeset.created",
                    entity_type="creation_changeset",
                    entity_id=changeset_id,
                    payload={
                        "operations": len(public_operations),
                        "proposed_source_revision": proposed_revision,
                    },
                    created_at=timestamp,
                )
        except sqlite3.IntegrityError as exc:
            raise conflict(f"Creation changeset {changeset_id} already exists") from exc
        return changeset

    def _capture_operation(
        self,
        raw: object,
        *,
        index: int,
        current_documents: Mapping[PurePosixPath, dict[str, Any]],
        current_payloads: Mapping[PurePosixPath, bytes],
    ) -> tuple[dict[str, Any], bytes | None, bytes | None, dict[str, Any] | None]:
        if not isinstance(raw, dict):
            raise invalid_request(f"creation changeset operation {index} must be an object")
        kind = raw.get("operation")
        if kind not in {"create", "replace", "delete"}:
            raise invalid_request(f"creation changeset operation {index} is unknown")
        fields = {
            "operation",
            "path",
            "expected_base_file_sha256",
            "expected_base_size",
            "proposed_file_sha256",
            "proposed_size",
        }
        if kind != "delete":
            fields.add("document")
        if set(raw) != fields:
            raise invalid_request(f"creation changeset operation {index} has invalid fields")
        relative = _portable_creation_path(raw["path"], context=f"operations/{index}/path")
        base_payload = current_payloads.get(relative)
        if kind == "create":
            if relative in current_documents or base_payload is not None:
                raise conflict(f"Creation target already exists: {relative}")
            if (
                raw["expected_base_file_sha256"] is not None
                or raw["expected_base_size"] is not None
            ):
                raise invalid_request(f"creation operation {index} must declare an absent base")
        else:
            if relative not in current_documents or base_payload is None:
                raise conflict(f"Creation changeset base is absent: {relative}")
            expected_base_hash = _digest(
                raw["expected_base_file_sha256"],
                field=f"operations/{index}/expected_base_file_sha256",
            )
            expected_base_size = _generation(
                raw["expected_base_size"],
                field=f"operations/{index}/expected_base_size",
            )
            if expected_base_size != len(base_payload) or not hmac.compare_digest(
                expected_base_hash,
                _hash(base_payload),
            ):
                raise conflict(f"Creation changeset base changed before staging: {relative}")
        proposed_payload: bytes | None = None
        proposed_document: dict[str, Any] | None = None
        if kind == "delete":
            if raw["proposed_file_sha256"] is not None or raw["proposed_size"] is not None:
                raise invalid_request(f"creation operation {index} delete must not propose bytes")
        else:
            document = raw["document"]
            if not isinstance(document, dict):
                raise invalid_request(f"creation operation {index} document must be an object")
            proposed_document = document
            try:
                proposed_payload = canonical_json_bytes(document)
            except (TypeError, ValueError) as exc:
                raise invalid_request(
                    f"creation operation {index} document is not strict JSON"
                ) from exc
            if len(proposed_payload) > min(MAX_CHANGE_FILE_BYTES, MAX_CREATION_CONTRACT_BYTES):
                raise invalid_request(f"creation operation {index} exceeds the file limit")
            proposed_hash = _digest(
                raw["proposed_file_sha256"],
                field=f"operations/{index}/proposed_file_sha256",
            )
            proposed_size = _generation(
                raw["proposed_size"],
                field=f"operations/{index}/proposed_size",
            )
            if proposed_size != len(proposed_payload) or not hmac.compare_digest(
                proposed_hash,
                _hash(proposed_payload),
            ):
                raise invalid_request(
                    f"creation operation {index} proposed hash or size does not match bytes"
                )
            if kind == "replace" and base_payload == proposed_payload:
                raise invalid_request(f"creation operation {index} is a no-op replacement")
        public = {
            "operation": kind,
            "path": relative.as_posix(),
            "expected_base_file_sha256": (None if base_payload is None else _hash(base_payload)),
            "expected_base_size": None if base_payload is None else len(base_payload),
            "proposed_file_sha256": (None if proposed_payload is None else _hash(proposed_payload)),
            "proposed_size": None if proposed_payload is None else len(proposed_payload),
        }
        return public, base_payload, proposed_payload, proposed_document

    @contextmanager
    def _pinned_blob_parent(self, digest: str) -> Iterator[tuple[_PinnedParent, str]]:
        target = self.store.blob_path(digest)
        root_info = _safe_directory_info(
            self.store.blobs_dir,
            context="creation changeset blob root",
        )
        root_identity = _identity(root_info)
        shard_name = target.parent.name
        with _open_pinned_parent(
            self.store.blobs_dir,
            PurePosixPath(shard_name),
            world_identity=root_identity,
            parent_identity=root_identity,
        ) as root_parent:
            _reject_pinned_collision(
                root_parent,
                shard_name,
                context="creation changeset blob shard",
            )
            shard_info = root_parent.entry_info(shard_name)
            if shard_info is None:
                try:
                    if root_parent.descriptor is not None:
                        if os.mkdir not in os.supports_dir_fd:
                            raise StudioError(
                                "internal_error",
                                "Secure blob shard creation is unavailable",
                            )
                        os.mkdir(shard_name, 0o700, dir_fd=root_parent.descriptor)
                    else:
                        root_parent.verify_visible()
                        (root_parent.path / shard_name).mkdir(mode=0o700)
                        root_parent.verify_visible()
                    root_parent.flush()
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise StudioError(
                        "internal_error", "Could not create creation changeset blob shard"
                    ) from exc
                shard_info = root_parent.entry_info(shard_name)
            if (
                shard_info is None
                or _is_link_or_reparse(shard_info)
                or not stat.S_ISDIR(shard_info.st_mode)
            ):
                raise conflict("Creation changeset blob shard is unsafe")
            shard_identity = _identity(shard_info)
        parent_info = _safe_directory_info(
            target.parent,
            context="creation changeset blob parent",
        )
        if _identity(parent_info) != shard_identity:
            raise conflict("Creation changeset blob shard identity changed")
        relative = PurePosixPath(*target.relative_to(self.store.blobs_dir).parts)
        with _open_pinned_parent(
            self.store.blobs_dir,
            relative,
            world_identity=root_identity,
            parent_identity=shard_identity,
        ) as parent:
            _reject_pinned_collision(
                parent,
                relative.name,
                context="creation changeset blob target",
            )
            yield parent, relative.name

    @staticmethod
    def _parent_names(parent: _PinnedParent) -> list[str]:
        if parent.descriptor is not None:
            try:
                return os.listdir(parent.descriptor)
            except OSError as exc:
                raise conflict("Could not enumerate creation changeset blobs") from exc
        parent.verify_visible()
        try:
            return [entry.name for entry in parent.path.iterdir()]
        except OSError as exc:
            raise conflict("Could not enumerate creation changeset blobs") from exc

    def _read_or_reconcile_blob(
        self,
        parent: _PinnedParent,
        target_name: str,
        digest: str,
    ) -> bytes | None:
        info = parent.entry_info(target_name)
        if info is None:
            return None
        if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
            raise conflict("Creation changeset blob target is unsafe")
        if info.st_nlink == 2:
            prefix = f".{digest}."
            target_payload, target_identity = _safe_entry_snapshot(
                parent,
                target_name,
                context="linked creation changeset blob",
                require_standalone=False,
                require_utf8=False,
            )
            candidates: list[str] = []
            for name in self._parent_names(parent):
                if not name.startswith(prefix) or not name.endswith(".tmp"):
                    continue
                nonce = name[len(prefix) : -4]
                if len(nonce) != 32 or any(
                    character not in "0123456789abcdef" for character in nonce
                ):
                    continue
                candidate_info = parent.entry_info(name)
                if (
                    candidate_info is not None
                    and not _is_link_or_reparse(candidate_info)
                    and stat.S_ISREG(candidate_info.st_mode)
                    and candidate_info.st_nlink == 2
                    and _identity(candidate_info) == target_identity
                ):
                    candidates.append(name)
            if len(candidates) != 1:
                raise conflict("Creation changeset blob has an ambiguous publication link")
            temporary_name = candidates[0]
            temporary_info = parent.entry_info(temporary_name)
            temporary_payload, temporary_identity = _safe_entry_snapshot(
                parent,
                temporary_name,
                context="linked creation changeset blob temporary",
                require_standalone=False,
                require_utf8=False,
            )
            if (
                temporary_info is None
                or temporary_info.st_nlink != 2
                or target_identity != temporary_identity
                or target_payload != temporary_payload
                or not hmac.compare_digest(_hash(target_payload), digest)
            ):
                raise conflict("Creation changeset blob publication link changed")
            parent.unlink(temporary_name)
            parent.flush()
            info = parent.entry_info(target_name)
        if info is None or info.st_nlink != 1:
            raise conflict("Creation changeset blob is not standalone")
        payload, _target_identity = _safe_entry_snapshot(
            parent,
            target_name,
            context="retained creation changeset blob",
            require_standalone=True,
            require_utf8=False,
        )
        if not hmac.compare_digest(_hash(payload), digest):
            raise conflict("Creation changeset blob does not match its digest")
        return payload

    def _store_blob(self, payload: bytes, digest: object) -> None:
        if not isinstance(digest, str) or not hmac.compare_digest(_hash(payload), digest):
            raise StudioError("internal_error", "Creation changeset blob digest is inconsistent")
        with self._pinned_blob_parent(digest) as (parent, target_name):
            current = self._read_or_reconcile_blob(parent, target_name, digest)
            if current is not None:
                if current != payload:
                    raise conflict("Creation changeset blob path contains other bytes")
                try:
                    parent.flush()
                except OSError as exc:
                    raise StudioError(
                        "internal_error",
                        "Could not durably confirm creation changeset blob",
                    ) from exc
                return
            temporary_name = f".{digest}.{uuid.uuid4().hex}.tmp"
            descriptor: int | None = None
            temporary_identity: tuple[int, int] | None = None
            try:
                descriptor = parent.open_entry(
                    temporary_name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                opened = os.fstat(descriptor)
                if _is_link_or_reparse(opened) or not stat.S_ISREG(opened.st_mode):
                    raise OSError("blob temporary is not a regular file")
                temporary_identity = _identity(opened)
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = None
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                parent.flush()
                staged, staged_identity = _safe_entry_snapshot(
                    parent,
                    temporary_name,
                    context="creation changeset blob temporary",
                    require_standalone=True,
                    require_utf8=False,
                )
                if staged_identity != temporary_identity or staged != payload:
                    raise conflict("Creation changeset blob temporary changed")
                self._notify("blob_staged", digest=digest)
                parent.link(temporary_name, target_name)
                linked = parent.entry_info(target_name)
                temporary = parent.entry_info(temporary_name)
                if (
                    linked is None
                    or temporary is None
                    or linked.st_nlink != 2
                    or temporary.st_nlink != 2
                    or _identity(linked) != temporary_identity
                    or _identity(temporary) != temporary_identity
                ):
                    raise conflict("Creation changeset blob publication link changed")
                self._notify("blob_linked", digest=digest)
                parent.unlink(temporary_name)
                parent.flush()
                temporary_identity = None
                current = self._read_or_reconcile_blob(parent, target_name, digest)
                if current != payload:
                    raise conflict("Creation changeset blob changed during publication")
            except BaseException as exc:
                if descriptor is not None:
                    os.close(descriptor)
                if isinstance(exc, BaseException) and not isinstance(exc, Exception):
                    raise
                if temporary_identity is not None:
                    try:
                        current = self._read_or_reconcile_blob(parent, target_name, digest)
                    except StudioError:
                        current = None
                    info = parent.entry_info(temporary_name)
                    owned_standalone = (
                        info is not None
                        and not _is_link_or_reparse(info)
                        and stat.S_ISREG(info.st_mode)
                        and info.st_nlink == 1
                        and _identity(info) == temporary_identity
                    )
                    if current == payload:
                        try:
                            if owned_standalone:
                                parent.unlink(temporary_name)
                            parent.flush()
                        except OSError as retry_exc:
                            raise StudioError(
                                "internal_error",
                                "Could not durably converge creation changeset blob",
                            ) from retry_exc
                        temporary_identity = None
                        return
                    if current is None and owned_standalone:
                        parent.unlink(temporary_name)
                        parent.flush()
                if isinstance(exc, StudioError):
                    raise exc
                if isinstance(exc, OSError):
                    raise StudioError(
                        "internal_error",
                        "Could not persist creation changeset blob",
                    ) from exc
                raise

    def _read_blob(self, digest: str, size: int) -> bytes:
        try:
            with self._pinned_blob_parent(digest) as (parent, target_name):
                payload = self._read_or_reconcile_blob(parent, target_name, digest)
        except (OSError, StudioError) as exc:
            raise conflict("Could not read retained creation changeset blob") from exc
        if payload is None:
            raise conflict("Retained creation changeset blob is missing")
        if len(payload) != size or not hmac.compare_digest(_hash(payload), digest):
            raise conflict("Retained creation changeset blob does not match its digest and size")
        return payload

    def get(self, changeset_id: object) -> dict[str, Any]:
        identifier = _identifier(changeset_id, field="changeset_id")
        row = self.store.connection.execute(
            "SELECT * FROM creation_changesets WHERE changeset_id = ?",
            (identifier,),
        ).fetchone()
        if row is None:
            raise not_found(f"Creation changeset {identifier} was not found")
        return self._validated_row(row)

    def list(
        self,
        *,
        workspace_id: object = None,
        status: object = None,
        limit: object = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[object] = []
        if workspace_id is not None:
            identifier = _identifier(workspace_id, field="workspace_id", workspace=True)
            self.workspaces.get(identifier)
            clauses.append("workspace_id = ?")
            values.append(identifier)
        if status is not None:
            if not isinstance(status, str) or status not in CREATION_CHANGESET_STATES:
                raise invalid_request("Creation changeset status filter is unknown")
            clauses.append("status = ?")
            values.append(status)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise invalid_request("Creation changeset limit must be from 1 to 1000")
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        rows = self.store.connection.execute(
            f"SELECT * FROM creation_changesets{where} "  # noqa: S608
            "ORDER BY changeset_id LIMIT ?",
            (*values, limit),
        ).fetchall()
        return [self._validated_row(row) for row in rows]

    def diff(self, changeset_id: object) -> dict[str, Any]:
        record = self.get(changeset_id)
        self._verify_retained_evidence(record)
        operations = [
            {
                **operation,
                "size_delta": (operation["proposed_size"] or 0)
                - (operation["expected_base_size"] or 0),
            }
            for operation in record["operations"]
        ]
        return {
            "changeset_id": record["changeset_id"],
            "workspace_id": record["workspace_id"],
            "expected_source_revision": record["expected_source_revision"],
            "proposed_source_revision": record["proposed_source_revision"],
            "review_sha256": record["review_sha256"],
            "operations": operations,
        }

    def approve(
        self,
        changeset_id: object,
        *,
        expected_record_hash: object,
        expected_review_sha256: object,
    ) -> dict[str, Any]:
        record = self.get(changeset_id)
        self._verify_action_hashes(
            record,
            expected_record_hash=expected_record_hash,
            expected_review_sha256=expected_review_sha256,
        )
        self._verify_retained_evidence(record)
        return self._transition(
            changeset_id,
            allowed={"staged"},
            status="approved",
            expected_record_hash=expected_record_hash,
            expected_review_sha256=expected_review_sha256,
        )

    def _verify_retained_evidence(self, record: Mapping[str, Any]) -> None:
        for operation in record["operations"]:
            if operation["expected_base_file_sha256"] is not None:
                self._read_blob(
                    operation["expected_base_file_sha256"],
                    operation["expected_base_size"],
                )
            if operation["proposed_file_sha256"] is not None:
                self._read_blob(
                    operation["proposed_file_sha256"],
                    operation["proposed_size"],
                )

    def reject(
        self,
        changeset_id: object,
        *,
        expected_record_hash: object,
        expected_review_sha256: object,
    ) -> dict[str, Any]:
        return self._transition(
            changeset_id,
            allowed={"staged", "approved"},
            status="rejected",
            expected_record_hash=expected_record_hash,
            expected_review_sha256=expected_review_sha256,
        )

    @staticmethod
    def _verify_action_hashes(
        record: Mapping[str, Any],
        *,
        expected_record_hash: object,
        expected_review_sha256: object,
    ) -> None:
        expected_record = _digest(expected_record_hash, field="expected_record_hash")
        expected_review = _digest(
            expected_review_sha256,
            field="expected_review_sha256",
        )
        if not hmac.compare_digest(record["record_hash"], expected_record):
            raise conflict("Creation changeset record changed")
        if not hmac.compare_digest(record["review_sha256"], expected_review):
            raise conflict("Creation changeset review changed")

    def _projected_graph(
        self,
        record: Mapping[str, Any],
        root: Path,
        root_identity: tuple[int, int],
        current_project: LoadedCreationProject,
    ) -> tuple[LoadedCreationProject, dict[PurePosixPath, bytes]]:
        current_documents = {
            relative: document for relative, document in _module_paths(current_project)
        }
        current_payloads = {
            relative: read_workspace_file_snapshot(
                root,
                relative,
                world_identity=root_identity,
                context=f"creation changeset base {relative.as_posix()}",
                limit=MAX_CHANGE_FILE_BYTES,
            )
            for relative in current_documents
        }
        proposed_documents = dict(current_documents)
        proposed_payloads = dict(current_payloads)
        for operation in record["operations"]:
            relative = _portable_creation_path(
                operation["path"],
                context="creation changeset operation path",
            )
            current = current_payloads.get(relative)
            if operation["operation"] == "create":
                if current is not None:
                    raise conflict(f"Creation changeset base is no longer absent: {relative}")
            else:
                if current is None:
                    raise conflict(f"Creation changeset base is now absent: {relative}")
                if len(current) != operation["expected_base_size"] or not hmac.compare_digest(
                    _hash(current), operation["expected_base_file_sha256"]
                ):
                    raise conflict(f"Creation changeset base changed: {relative}")
            if operation["operation"] == "delete":
                proposed_documents.pop(relative, None)
                proposed_payloads.pop(relative, None)
                continue
            proposed = self._read_blob(
                operation["proposed_file_sha256"], operation["proposed_size"]
            )
            try:
                document = decode_json_object(
                    proposed,
                    source=f"creation changeset proposed {relative.as_posix()}",
                )
            except RuntimeIOError as exc:
                raise conflict("Retained creation changeset document is invalid") from exc
            if canonical_json_bytes(document) != proposed:
                raise conflict("Retained creation changeset document is not canonical")
            proposed_documents[relative] = document
            proposed_payloads[relative] = proposed
        proposed_project = _validated_project_from_documents(proposed_documents)
        proposed_revision = _source_revision(proposed_project, proposed_payloads)
        if not hmac.compare_digest(proposed_revision, record["proposed_source_revision"]):
            raise conflict("Retained creation changeset projection changed")
        return proposed_project, proposed_payloads

    def _claim_apply(
        self,
        record: dict[str, Any],
        *,
        claimed: dict[str, Any],
        root_identity: tuple[int, int],
        journal_name: str,
        journal_identity: tuple[int, int],
    ) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT * FROM creation_changesets WHERE changeset_id = ?",
            (record["changeset_id"],),
        ).fetchone()
        if row is None:
            raise not_found(f"Creation changeset {record['changeset_id']} was not found")
        current = self._validated_row(row)
        if current != record or current["status"] != "approved":
            raise conflict("Creation changeset changed before apply claim")
        timestamp = claimed["updated_at"]
        expected_claim = {
            **record,
            "status": "applying",
            "updated_at": timestamp,
            "record_hash": "",
        }
        expected_claim["record_hash"] = creation_changeset_record_hash(expected_claim)
        if (
            not isinstance(timestamp, str)
            or claimed != expected_claim
            or claimed.get("record_hash") != creation_changeset_record_hash(claimed)
        ):
            raise StudioError("internal_error", "Creation changeset apply claim is inconsistent")
        validate_studio_creation_changeset(claimed)
        try:
            with self.store.connection:
                cursor = self.store.connection.execute(
                    "UPDATE creation_changesets SET status = 'applying', record_json = ?, "
                    "generation = generation + 1 WHERE changeset_id = ? AND generation = ? "
                    "AND status = 'approved'",
                    (
                        encode_json(claimed),
                        record["changeset_id"],
                        row["generation"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise conflict("Creation changeset changed before apply claim")
                self.store.connection.execute(
                    "INSERT INTO creation_changeset_attempts "
                    "(changeset_id, phase, journal_name, journal_dev, journal_ino, "
                    "root_dev, root_ino, generation, created_at, updated_at) "
                    "VALUES (?, 'before_staging', ?, ?, ?, ?, ?, 0, ?, ?)",
                    (
                        record["changeset_id"],
                        journal_name,
                        str(journal_identity[0]),
                        str(journal_identity[1]),
                        str(root_identity[0]),
                        str(root_identity[1]),
                        timestamp,
                        timestamp,
                    ),
                )
                self.store.record_creation_event(
                    workspace_id=record["workspace_id"],
                    topic="creation_changeset.applying",
                    entity_type="creation_changeset",
                    entity_id=record["changeset_id"],
                    payload={"previous_status": "approved"},
                    created_at=timestamp,
                )
        except sqlite3.IntegrityError as exc:
            raise conflict("Creation changeset already has an apply attempt") from exc
        return claimed

    def _update_attempt(
        self,
        changeset_id: str,
        *,
        phase: str,
        journal_identity: tuple[int, int] | None = None,
    ) -> None:
        timestamp = utc_now()
        assignments = "phase = ?, updated_at = ?, generation = generation + 1"
        values: list[object] = [phase, timestamp]
        if journal_identity is not None:
            assignments += ", journal_dev = ?, journal_ino = ?"
            values.extend((str(journal_identity[0]), str(journal_identity[1])))
        values.append(changeset_id)
        with self.store.connection:
            cursor = self.store.connection.execute(
                f"UPDATE creation_changeset_attempts SET {assignments} "  # noqa: S608
                "WHERE changeset_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise conflict("Creation changeset apply attempt changed")

    def _prepare_journal_base(
        self,
        record: Mapping[str, Any],
        *,
        approved_record_hash: str,
        root: Path,
        root_identity: tuple[int, int],
        attempt_nonce: str,
    ) -> dict[str, Any]:
        operations: list[dict[str, Any]] = []
        for index, public in enumerate(record["operations"]):
            relative = _portable_creation_path(
                public["path"], context="creation changeset operation path"
            )
            target = _safe_creation_target(root, relative)
            parent_info = _safe_directory_info(
                target.parent,
                context=f"creation changeset parent {relative.parent}",
            )
            info = _path_info(target)
            base_identity: tuple[int, int] | None = None
            if public["operation"] == "create":
                if info is not None:
                    raise conflict(f"Creation changeset base is no longer absent: {relative}")
            else:
                if info is None:
                    raise conflict(f"Creation changeset base is now absent: {relative}")
                payload, base_identity = _safe_file_snapshot(
                    target,
                    context=f"creation changeset base {relative}",
                    require_standalone=True,
                )
                if len(payload) != public["expected_base_size"] or not hmac.compare_digest(
                    _hash(payload), public["expected_base_file_sha256"]
                ):
                    raise conflict(f"Creation changeset base changed: {relative}")
            stage_name = (
                None
                if public["operation"] == "delete"
                else (
                    f".worldforge-creation-{record['changeset_id']}-{index}-"
                    f"{uuid.uuid4().hex}.stage"
                )
            )
            rollback_name = (
                None
                if public["operation"] == "create"
                else (
                    f".worldforge-creation-{record['changeset_id']}-{index}-"
                    f"{uuid.uuid4().hex}.rollback"
                )
            )
            for reserved in (stage_name, rollback_name):
                if reserved is not None and _path_info(target.parent / reserved) is not None:
                    raise conflict("Creation changeset temporary namespace is occupied")
            operations.append(
                {
                    **public,
                    "parent_identity": list(_identity(parent_info)),
                    "base_identity": (
                        None if base_identity is None else [base_identity[0], base_identity[1]]
                    ),
                    "stage_name": stage_name,
                    "rollback_name": rollback_name,
                }
            )
        return {
            "format": _JOURNAL_FORMAT,
            "format_version": _JOURNAL_VERSION,
            "attempt_nonce": attempt_nonce,
            "changeset_id": record["changeset_id"],
            "workspace_id": record["workspace_id"],
            "approved_record_hash": approved_record_hash,
            "applying_record_hash": record["record_hash"],
            "review_sha256": record["review_sha256"],
            "expected_root_generation": record["expected_root_generation"],
            "expected_source_revision": record["expected_source_revision"],
            "proposed_source_revision": record["proposed_source_revision"],
            "expected_workflow_status_hash": record["expected_workflow_status_hash"],
            "root": str(root),
            "root_identity": [root_identity[0], root_identity[1]],
            "operations": operations,
        }

    def _create_journal(
        self,
        path: Path,
        base: Mapping[str, Any],
    ) -> tuple[tuple[bytes, ...], tuple[int, int]]:
        stage_identities = tuple(None for _operation in base["operations"])
        history = _expected_journal_history(
            base,
            through_phase="before_staging",
            stage_identities=stage_identities,
        )
        try:
            identity = create_append_only_journal(
                path,
                history[0],
                max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
            )
            fsync_directory(path.parent, context="Studio creation changeset journal directory")
        except FileExistsError:
            raise StudioError(
                "recovery_ambiguous",
                "Creation changeset journal namespace was occupied before binding",
            ) from None
        except DirectoryPublishError as exc:
            raise StudioError(
                "recovery_failed", "Could not publish creation changeset journal"
            ) from exc
        self._notify("journal_created_unbound", changeset_id=base["changeset_id"])
        return history, identity

    def _advance_journal(
        self,
        path: Path,
        base: Mapping[str, Any],
        *,
        identity: tuple[int, int],
        current_phase: str,
        updated_phase: str,
        stage_identities: tuple[tuple[int, int] | None, ...],
    ) -> tuple[bytes, ...]:
        phases = _journal_phases(len(base["operations"]))
        if phases.index(updated_phase) != phases.index(current_phase) + 1:
            raise StudioError(
                "internal_error", "Creation changeset journal transition is not contiguous"
            )
        history = _expected_journal_history(
            base,
            through_phase=current_phase,
            stage_identities=stage_identities,
        )
        updated = _expected_journal_history(
            base,
            through_phase=updated_phase,
            stage_identities=stage_identities,
        )
        try:
            append_append_only_journal(
                path,
                expected_identity=identity,
                expected_payload=history[-1],
                expected_history=history,
                updated_payload=updated[-1],
                max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
                repair_partial_tail=True,
            )
        except DirectoryPublishError as exc:
            raise StudioError(
                "recovery_ambiguous", "Creation changeset journal transition failed"
            ) from exc
        self._update_attempt(base["changeset_id"], phase=updated_phase)
        self._notify(updated_phase, changeset_id=base["changeset_id"])
        return updated

    @staticmethod
    def _journal_base_from_document(document: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "format",
            "format_version",
            "attempt_nonce",
            "changeset_id",
            "workspace_id",
            "approved_record_hash",
            "applying_record_hash",
            "review_sha256",
            "expected_root_generation",
            "expected_source_revision",
            "proposed_source_revision",
            "expected_workflow_status_hash",
            "root",
            "root_identity",
            "phase",
            "operations",
        }
        if set(document) != required:
            raise StudioError("recovery_ambiguous", "Creation changeset journal fields are invalid")
        operations = document["operations"]
        if not isinstance(operations, list) or not operations:
            raise StudioError(
                "recovery_ambiguous", "Creation changeset journal operations are invalid"
            )
        base_operations = []
        operation_fields = {
            "operation",
            "path",
            "expected_base_file_sha256",
            "expected_base_size",
            "proposed_file_sha256",
            "proposed_size",
            "parent_identity",
            "base_identity",
            "stage_name",
            "rollback_name",
            "stage_identity",
            "applied",
        }
        for operation in operations:
            if not isinstance(operation, dict) or set(operation) != operation_fields:
                raise StudioError(
                    "recovery_ambiguous",
                    "Creation changeset journal operation fields are invalid",
                )
            base_operations.append(
                {
                    key: value
                    for key, value in operation.items()
                    if key not in {"stage_identity", "applied"}
                }
            )
        return {
            key: value for key, value in document.items() if key not in {"phase", "operations"}
        } | {"operations": base_operations}

    @staticmethod
    def _validated_attempt_journal_name(
        attempt: sqlite3.Row,
        record: Mapping[str, Any],
    ) -> str:
        name = attempt["journal_name"]
        prefix = f"{record['changeset_id']}."
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or unicodedata.normalize("NFC", name) != name
            or not name.startswith(prefix)
            or not name.endswith(".json")
        ):
            raise StudioError("recovery_ambiguous", "Creation changeset journal name is invalid")
        nonce = name[len(prefix) : -5]
        if len(nonce) != 32 or any(character not in "0123456789abcdef" for character in nonce):
            raise StudioError("recovery_ambiguous", "Creation changeset journal name is invalid")
        return name

    def _read_journal(
        self,
        path: Path,
        attempt: sqlite3.Row,
        record: Mapping[str, Any],
        root: Path,
        root_identity: tuple[int, int],
    ) -> tuple[
        dict[str, Any],
        str,
        tuple[tuple[int, int] | None, ...],
        tuple[bytes, ...],
        tuple[int, int],
    ]:
        try:
            loaded = read_append_only_journal_history_state(
                path,
                max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
            )
        except DirectoryPublishError as exc:
            raise StudioError(
                "recovery_ambiguous", "Creation changeset journal is unavailable"
            ) from exc
        if loaded is None:
            raise StudioError("recovery_ambiguous", "Creation changeset journal is missing")
        history, identity, partial_tail = loaded
        expected_identity = (
            None
            if attempt["journal_dev"] is None or attempt["journal_ino"] is None
            else (int(attempt["journal_dev"]), int(attempt["journal_ino"]))
        )
        if not history or expected_identity is None or identity != expected_identity:
            raise StudioError(
                "recovery_ambiguous", "Creation changeset journal identity is not bound"
            )
        try:
            document = decode_json_object(history[-1], source="Studio creation changeset journal")
            if canonical_json_bytes(document) != history[-1]:
                raise ValueError("journal is not canonical")
            base = self._journal_base_from_document(document)
            phase = document["phase"]
            stage_identities = tuple(
                None
                if operation["stage_identity"] is None
                else (
                    int(operation["stage_identity"][0]),
                    int(operation["stage_identity"][1]),
                )
                for operation in document["operations"]
            )
            expected_history = _expected_journal_history(
                base,
                through_phase=phase,
                stage_identities=stage_identities,
            )
        except (KeyError, TypeError, ValueError, RuntimeIOError, StudioError) as exc:
            raise StudioError(
                "recovery_ambiguous", "Creation changeset journal is invalid"
            ) from exc
        nonce = base["attempt_nonce"]
        expected_name = f"{record['changeset_id']}.{nonce}.json"
        public_operation_fields = {
            "operation",
            "path",
            "expected_base_file_sha256",
            "expected_base_size",
            "proposed_file_sha256",
            "proposed_size",
        }
        journal_public_operations = [
            {key: operation[key] for key in public_operation_fields}
            for operation in base["operations"]
        ]
        if (
            history != expected_history
            or not isinstance(nonce, str)
            or len(nonce) != 32
            or any(character not in "0123456789abcdef" for character in nonce)
            or attempt["journal_name"] != expected_name
            or path.name != expected_name
            or base["format"] != _JOURNAL_FORMAT
            or base["format_version"] != _JOURNAL_VERSION
            or base["changeset_id"] != record["changeset_id"]
            or base["workspace_id"] != record["workspace_id"]
            or base["review_sha256"] != record["review_sha256"]
            or base["expected_root_generation"] != record["expected_root_generation"]
            or base["expected_source_revision"] != record["expected_source_revision"]
            or base["proposed_source_revision"] != record["proposed_source_revision"]
            or base["expected_workflow_status_hash"] != record["expected_workflow_status_hash"]
            or base["root"] != str(root)
            or base["root_identity"] != [root_identity[0], root_identity[1]]
            or (int(attempt["root_dev"]), int(attempt["root_ino"])) != root_identity
            or journal_public_operations != record["operations"]
            or (
                record["status"] == "applying"
                and base["applying_record_hash"] != record["record_hash"]
            )
        ):
            raise StudioError(
                "recovery_ambiguous", "Creation changeset journal does not match its attempt"
            )
        if partial_tail:
            phases = _journal_phases(len(base["operations"]))
            phase_index = phases.index(phase)
            if phase_index + 1 >= len(phases):
                raise StudioError(
                    "recovery_ambiguous",
                    "Creation changeset journal has no valid transition after its partial tail",
                )
            updated_phase = phases[phase_index + 1]
            repaired_stage_identities = stage_identities
            if phase == "before_staging":
                repaired_stage_identities = self._prepared_stage_identities_for_repair(
                    base,
                    root,
                )
            updated_history = _expected_journal_history(
                base,
                through_phase=updated_phase,
                stage_identities=repaired_stage_identities,
            )
            try:
                append_append_only_journal(
                    path,
                    expected_identity=identity,
                    expected_payload=history[-1],
                    expected_history=history,
                    updated_payload=updated_history[-1],
                    max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                    max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
                    repair_partial_tail=True,
                )
            except DirectoryPublishError as exc:
                raise StudioError(
                    "recovery_ambiguous",
                    "Creation changeset journal partial transition is not exact",
                ) from exc
            self._update_attempt(base["changeset_id"], phase=updated_phase)
            phase = updated_phase
            stage_identities = repaired_stage_identities
            history = updated_history
        return base, phase, stage_identities, history, identity

    def _prepared_stage_identities_for_repair(
        self,
        base: Mapping[str, Any],
        root: Path,
    ) -> tuple[tuple[int, int] | None, ...]:
        identities: list[tuple[int, int] | None] = []
        with _pinned_creation_parents(base, root) as parents:
            for operation, parent in zip(base["operations"], parents, strict=True):
                stage_name = operation["stage_name"]
                if stage_name is None:
                    identities.append(None)
                    continue
                payload, identity = _safe_entry_snapshot(
                    parent,
                    stage_name,
                    context="creation changeset partial-journal stage",
                    require_standalone=True,
                    require_utf8=False,
                )
                if len(payload) != operation["proposed_size"] or not hmac.compare_digest(
                    _hash(payload), operation["proposed_file_sha256"]
                ):
                    raise StudioError(
                        "recovery_ambiguous",
                        "Creation changeset partial-journal stage changed",
                    )
                identities.append(identity)
            _verify_pinned_parents(parents)
        return tuple(identities)

    @staticmethod
    def _remove_journal(
        path: Path,
        *,
        history: tuple[bytes, ...],
        identity: tuple[int, int],
    ) -> None:
        try:
            remove_append_only_journal(
                path,
                expected_identity=identity,
                expected_payload=history[-1],
                expected_history=history,
                max_record_bytes=_MAX_JOURNAL_RECORD_BYTES,
                max_file_bytes=_MAX_JOURNAL_FILE_BYTES,
            )
        except DirectoryPublishIndeterminateError as exc:
            raise StudioError(
                "recovery_ambiguous", "Creation changeset journal cleanup is indeterminate"
            ) from exc
        except DirectoryPublishError as exc:
            raise StudioError(
                "recovery_failed", "Creation changeset journal cleanup failed"
            ) from exc

    def _prepare_stages(
        self,
        base: Mapping[str, Any],
        parents: list[_PinnedParent],
        *,
        allow_existing: bool,
    ) -> tuple[tuple[int, int] | None, ...]:
        identities: list[tuple[int, int] | None] = []
        for operation, parent in zip(base["operations"], parents, strict=True):
            stage_name = operation["stage_name"]
            if stage_name is None:
                identities.append(None)
                continue
            payload = self._read_blob(operation["proposed_file_sha256"], operation["proposed_size"])
            _reject_pinned_collision(
                parent,
                stage_name,
                context="creation changeset stage",
            )
            existing = parent.entry_info(stage_name)
            if existing is not None:
                if not allow_existing:
                    raise conflict("Creation changeset stage namespace is occupied")
                staged, identity = _safe_entry_snapshot(
                    parent,
                    stage_name,
                    context="creation changeset stage",
                    require_standalone=True,
                    require_utf8=False,
                )
                if staged != payload or not hmac.compare_digest(_hash(staged), _hash(payload)):
                    raise conflict("Creation changeset stage contains unowned bytes")
                identities.append(identity)
                continue
            descriptor: int | None = None
            try:
                descriptor = parent.open_entry(
                    stage_name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = None
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                parent.flush()
                staged, identity = _safe_entry_snapshot(
                    parent,
                    stage_name,
                    context="creation changeset stage",
                    require_standalone=True,
                    require_utf8=False,
                )
                if staged != payload or not hmac.compare_digest(_hash(staged), _hash(payload)):
                    raise conflict("Creation changeset stage changed during preparation")
                identities.append(identity)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        _verify_pinned_parents(parents)
        return tuple(identities)

    def _move_noreplace(
        self,
        parent: _PinnedParent,
        source_name: str,
        destination_name: str,
        identity: tuple[int, int],
        digest: str,
    ) -> None:
        if parent.entry_info(destination_name) is not None:
            raise conflict("Creation changeset rollback reservation already exists")
        parent.link(source_name, destination_name)
        linked, linked_identity = _safe_entry_snapshot(
            parent,
            destination_name,
            context="creation changeset rollback",
            require_standalone=False,
        )
        current, current_identity = _safe_entry_snapshot(
            parent,
            source_name,
            context="creation changeset source",
            require_standalone=False,
        )
        if (
            linked_identity != identity
            or current_identity != identity
            or not hmac.compare_digest(_hash(linked), digest)
            or linked != current
        ):
            parent.unlink(destination_name)
            parent.flush()
            raise conflict("Creation changeset source changed while reserving rollback")
        self._notify(
            "rollback_linked",
            source_name=source_name,
            rollback_name=destination_name,
        )
        parent.unlink(source_name)
        parent.flush()
        restored, restored_identity = _safe_entry_snapshot(
            parent,
            destination_name,
            context="creation changeset rollback",
            require_standalone=True,
        )
        if restored_identity != identity or not hmac.compare_digest(_hash(restored), digest):
            raise conflict("Creation changeset rollback changed after reservation")

    def _publish_stage(
        self,
        parent: _PinnedParent,
        stage_name: str,
        target_name: str,
        identity: tuple[int, int],
        digest: str,
    ) -> None:
        if parent.entry_info(target_name) is not None:
            raise conflict("Creation changeset target is no longer absent")
        parent.link(stage_name, target_name)
        published, published_identity = _safe_entry_snapshot(
            parent,
            target_name,
            context="published creation changeset file",
            require_standalone=False,
        )
        staged, staged_identity = _safe_entry_snapshot(
            parent,
            stage_name,
            context="creation changeset stage",
            require_standalone=False,
        )
        if (
            published_identity != identity
            or staged_identity != identity
            or not hmac.compare_digest(_hash(published), digest)
            or staged != published
        ):
            if published_identity == identity:
                parent.unlink(target_name)
                parent.flush()
            raise conflict("Creation changeset stage changed during publication")
        self._notify(
            "stage_linked",
            stage_name=stage_name,
            target_name=target_name,
        )
        parent.unlink(stage_name)
        parent.flush()
        final, final_identity = _safe_entry_snapshot(
            parent,
            target_name,
            context="published creation changeset file",
            require_standalone=True,
        )
        if final_identity != identity or not hmac.compare_digest(_hash(final), digest):
            raise conflict("Published creation changeset file changed")

    def _apply_operation(
        self,
        operation: Mapping[str, Any],
        parent: _PinnedParent,
        stage_identity: tuple[int, int] | None,
    ) -> None:
        relative = _portable_creation_path(
            operation["path"], context="creation changeset journal path"
        )
        target_name = relative.name
        if operation["operation"] != "create":
            payload, identity = _safe_entry_snapshot(
                parent,
                target_name,
                context="creation changeset base",
                require_standalone=True,
            )
            encoded_base_identity = operation["base_identity"]
            if encoded_base_identity != [identity[0], identity[1]] or not hmac.compare_digest(
                _hash(payload), operation["expected_base_file_sha256"]
            ):
                raise conflict(f"Creation changeset base changed during apply: {relative}")
            self._move_noreplace(
                parent,
                target_name,
                operation["rollback_name"],
                identity,
                operation["expected_base_file_sha256"],
            )
        if operation["operation"] != "delete":
            if stage_identity is None:
                raise StudioError("internal_error", "Creation changeset stage identity is missing")
            self._publish_stage(
                parent,
                operation["stage_name"],
                target_name,
                stage_identity,
                operation["proposed_file_sha256"],
            )

    def _validate_committed(
        self,
        base: Mapping[str, Any],
        parents: list[_PinnedParent],
        stage_identities: tuple[tuple[int, int] | None, ...],
    ) -> None:
        for operation, parent, stage_identity in zip(
            base["operations"], parents, stage_identities, strict=True
        ):
            relative = _portable_creation_path(
                operation["path"], context="creation changeset journal path"
            )
            if operation["operation"] == "delete":
                if parent.entry_info(relative.name) is not None:
                    raise conflict("Committed creation deletion target reappeared")
                continue
            payload, identity = _safe_entry_snapshot(
                parent,
                relative.name,
                context="committed creation changeset target",
                require_standalone=True,
            )
            if identity != stage_identity or not hmac.compare_digest(
                _hash(payload), operation["proposed_file_sha256"]
            ):
                raise conflict("Committed creation changeset target changed")

    def _rollback_journal(
        self,
        base: Mapping[str, Any],
        parents: list[_PinnedParent],
        stage_identities: tuple[tuple[int, int] | None, ...],
    ) -> None:
        pairs = list(zip(base["operations"], parents, stage_identities, strict=True))
        for operation, parent, stage_identity in reversed(pairs):
            relative = _portable_creation_path(
                operation["path"], context="creation changeset journal path"
            )
            target_name = relative.name
            info = parent.entry_info(target_name)
            if info is not None:
                payload, target_identity = _safe_entry_snapshot(
                    parent,
                    target_name,
                    context="creation changeset rollback target",
                    require_standalone=False,
                )
                encoded_base = operation["base_identity"]
                base_identity = None if encoded_base is None else (encoded_base[0], encoded_base[1])
                if (
                    stage_identity is not None
                    and target_identity == stage_identity
                    and hmac.compare_digest(_hash(payload), operation["proposed_file_sha256"])
                ):
                    parent.unlink(target_name)
                    parent.flush()
                elif (
                    base_identity is not None
                    and target_identity == base_identity
                    and hmac.compare_digest(_hash(payload), operation["expected_base_file_sha256"])
                ):
                    pass
                else:
                    raise conflict("Creation changeset rollback target contains unowned bytes")
            rollback_name = operation["rollback_name"]
            if rollback_name is not None:
                rollback_info = parent.entry_info(rollback_name)
                if rollback_info is not None:
                    rollback_payload, rollback_identity = _safe_entry_snapshot(
                        parent,
                        rollback_name,
                        context="creation changeset rollback source",
                        require_standalone=False,
                    )
                    encoded_base = operation["base_identity"]
                    if encoded_base != [
                        rollback_identity[0],
                        rollback_identity[1],
                    ] or not hmac.compare_digest(
                        _hash(rollback_payload),
                        operation["expected_base_file_sha256"],
                    ):
                        raise conflict("Creation changeset rollback source changed")
                    if parent.entry_info(target_name) is not None:
                        current, current_identity = _safe_entry_snapshot(
                            parent,
                            target_name,
                            context="creation changeset restored base",
                            require_standalone=False,
                        )
                        if current_identity != rollback_identity or current != rollback_payload:
                            raise conflict("Creation changeset rollback refuses an existing target")
                        parent.unlink(rollback_name)
                        parent.flush()
                        _safe_entry_snapshot(
                            parent,
                            target_name,
                            context="restored creation changeset base",
                            require_standalone=True,
                        )
                    else:
                        self._publish_stage(
                            parent,
                            rollback_name,
                            target_name,
                            rollback_identity,
                            operation["expected_base_file_sha256"],
                        )
            stage_name = operation["stage_name"]
            if stage_name is not None and parent.entry_info(stage_name) is not None:
                staged, identity = _safe_entry_snapshot(
                    parent,
                    stage_name,
                    context="creation changeset unused stage",
                    require_standalone=True,
                    require_utf8=False,
                )
                if not hmac.compare_digest(_hash(staged), operation["proposed_file_sha256"]) or (
                    stage_identity is not None and identity != stage_identity
                ):
                    raise conflict("Creation changeset stage changed before rollback cleanup")
                parent.unlink(stage_name)
                parent.flush()

    @staticmethod
    def _cleanup_rollbacks(
        base: Mapping[str, Any],
        parents: list[_PinnedParent],
    ) -> None:
        for operation, parent in zip(base["operations"], parents, strict=True):
            rollback_name = operation["rollback_name"]
            if rollback_name is None or parent.entry_info(rollback_name) is None:
                continue
            payload, identity = _safe_entry_snapshot(
                parent,
                rollback_name,
                context="committed creation changeset rollback",
                require_standalone=True,
            )
            encoded = operation["base_identity"]
            if encoded != [identity[0], identity[1]] or not hmac.compare_digest(
                _hash(payload), operation["expected_base_file_sha256"]
            ):
                raise conflict("Committed creation changeset rollback changed")
            parent.unlink(rollback_name)
            parent.flush()

    def _set_status_and_remove_attempt(
        self,
        record: Mapping[str, Any],
        *,
        status: str,
        topic: str,
    ) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT * FROM creation_changesets WHERE changeset_id = ?",
            (record["changeset_id"],),
        ).fetchone()
        if row is None:
            raise not_found(f"Creation changeset {record['changeset_id']} was not found")
        current = self._validated_row(row)
        if current["status"] == status:
            return current
        if current["status"] not in {"applying", "recovery_required"}:
            raise conflict("Creation changeset state changed during recovery")
        updated = {
            **current,
            "status": status,
            "updated_at": utc_now(),
            "record_hash": "",
        }
        updated["record_hash"] = creation_changeset_record_hash(updated)
        validate_studio_creation_changeset(updated)
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE creation_changesets SET status = ?, record_json = ?, "
                "generation = generation + 1 WHERE changeset_id = ? AND generation = ?",
                (
                    status,
                    encode_json(updated),
                    current["changeset_id"],
                    row["generation"],
                ),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation changeset state changed during recovery")
            self.store.connection.execute(
                "DELETE FROM creation_changeset_attempts WHERE changeset_id = ?",
                (current["changeset_id"],),
            )
            self.store.record_creation_event(
                workspace_id=current["workspace_id"],
                topic=topic,
                entity_type="creation_changeset",
                entity_id=current["changeset_id"],
                payload={},
                created_at=updated["updated_at"],
            )
        return updated

    def _mark_recovery_required(self, changeset_id: str, *, reason: str) -> dict[str, Any]:
        row = self.store.connection.execute(
            "SELECT * FROM creation_changesets WHERE changeset_id = ?",
            (changeset_id,),
        ).fetchone()
        if row is None:
            raise not_found(f"Creation changeset {changeset_id} was not found")
        current = self._validated_row(row)
        if current["status"] == "recovery_required":
            return current
        if current["status"] != "applying":
            return current
        updated = {
            **current,
            "status": "recovery_required",
            "updated_at": utc_now(),
            "record_hash": "",
        }
        updated["record_hash"] = creation_changeset_record_hash(updated)
        validate_studio_creation_changeset(updated)
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE creation_changesets SET status = 'recovery_required', "
                "record_json = ?, generation = generation + 1 "
                "WHERE changeset_id = ? AND generation = ? AND status = 'applying'",
                (encode_json(updated), changeset_id, row["generation"]),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation changeset state changed while marking recovery")
            self.store.record_creation_event(
                workspace_id=current["workspace_id"],
                topic="creation_changeset.recovery_required",
                entity_type="creation_changeset",
                entity_id=changeset_id,
                payload={"reason": reason},
                created_at=updated["updated_at"],
            )
        return updated

    def _commit_database(
        self,
        record: Mapping[str, Any],
        *,
        root: Path,
        root_identity: tuple[int, int],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        workspace_row = self.workspaces._row(record["workspace_id"])
        workspace_record = self.workspaces._validated_row(workspace_row)
        snapshot = self.workspaces._scan_integral_snapshot(
            record["workspace_id"],
            root,
            root_identity,
            root_generation=workspace_record["root_generation"],
            notify=False,
        )
        if snapshot is None:
            raise conflict("Published creation graph changed during final recensus")
        project, _summaries, source_revision, workflow = snapshot
        if not hmac.compare_digest(source_revision, record["proposed_source_revision"]):
            raise conflict("Published creation graph does not match the reviewed revision")
        current_changeset_row = self.store.connection.execute(
            "SELECT * FROM creation_changesets WHERE changeset_id = ?",
            (record["changeset_id"],),
        ).fetchone()
        if current_changeset_row is None:
            raise not_found(f"Creation changeset {record['changeset_id']} was not found")
        current_changeset = self._validated_row(current_changeset_row)
        if current_changeset["status"] == "applied":
            if not hmac.compare_digest(
                workspace_record["source_revision"], record["proposed_source_revision"]
            ):
                raise conflict("Applied creation changeset workspace state diverged")
            return current_changeset, workspace_record, workflow
        if current_changeset["status"] not in {"applying", "recovery_required"}:
            raise conflict("Creation changeset state changed before database commit")
        if (
            workspace_record["root_generation"] != record["expected_root_generation"]
            or not hmac.compare_digest(
                workspace_record["source_revision"], record["expected_source_revision"]
            )
            or workspace_record["workflow_status_hash"] != record["expected_workflow_status_hash"]
        ):
            raise conflict("Creation workspace state changed before database commit")
        timestamp = utc_now()
        updated_workspace = {
            **workspace_record,
            "project": document_identity(project.project),
            "project_kind": project.project["project_kind"],
            "source_revision": source_revision,
            "workflow_status_hash": workflow["status_hash"],
            "root_generation": workspace_record["root_generation"] + 1,
            "updated_at": timestamp,
        }
        validate_studio_creation_workspace(updated_workspace)
        applied = {
            **current_changeset,
            "status": "applied",
            "updated_at": timestamp,
            "record_hash": "",
        }
        applied["record_hash"] = creation_changeset_record_hash(applied)
        validate_studio_creation_changeset(applied)
        self.store.connection.execute("BEGIN IMMEDIATE")
        try:
            workspace_cursor = self.store.connection.execute(
                "UPDATE creation_workspaces SET record_json = ?, generation = ? "
                "WHERE workspace_id = ? AND generation = ?",
                (
                    encode_json(updated_workspace),
                    updated_workspace["root_generation"],
                    record["workspace_id"],
                    workspace_record["root_generation"],
                ),
            )
            changeset_cursor = self.store.connection.execute(
                "UPDATE creation_changesets SET status = 'applied', record_json = ?, "
                "generation = generation + 1 WHERE changeset_id = ? AND generation = ? "
                "AND status IN ('applying', 'recovery_required')",
                (
                    encode_json(applied),
                    record["changeset_id"],
                    current_changeset_row["generation"],
                ),
            )
            if workspace_cursor.rowcount != 1 or changeset_cursor.rowcount != 1:
                raise conflict("Creation changeset database commit lost its CAS")
            self.store.connection.execute(
                "UPDATE creation_changeset_attempts SET phase = 'database_committing', "
                "updated_at = ?, generation = generation + 1 WHERE changeset_id = ?",
                (timestamp, record["changeset_id"]),
            )
            self.store.record_creation_event(
                workspace_id=record["workspace_id"],
                topic="creation_changeset.applied",
                entity_type="creation_changeset",
                entity_id=record["changeset_id"],
                payload={"source_revision": source_revision},
                created_at=timestamp,
            )
            self.store.connection.commit()
        except BaseException:
            self.store.connection.rollback()
            raise
        return applied, updated_workspace, workflow

    def _finish_committed(
        self,
        record: Mapping[str, Any],
        *,
        base: Mapping[str, Any],
        phase: str,
        stage_identities: tuple[tuple[int, int] | None, ...],
        history: tuple[bytes, ...],
        journal_identity: tuple[int, int],
        journal_path: Path,
        root: Path,
        root_identity: tuple[int, int],
        parents: list[_PinnedParent],
    ) -> dict[str, Any]:
        self._validate_committed(base, parents, stage_identities)
        applied, workspace, workflow = self._commit_database(
            record,
            root=root,
            root_identity=root_identity,
        )
        try:
            if phase != "database_committed":
                history = self._advance_journal(
                    journal_path,
                    base,
                    identity=journal_identity,
                    current_phase=phase,
                    updated_phase="database_committed",
                    stage_identities=stage_identities,
                )
            self._notify("database_committed", changeset_id=record["changeset_id"])
            self._cleanup_rollbacks(base, parents)
            _verify_pinned_parents(parents)
            self._remove_journal(
                journal_path,
                history=history,
                identity=journal_identity,
            )
            with self.store.connection:
                self.store.connection.execute(
                    "DELETE FROM creation_changeset_attempts WHERE changeset_id = ?",
                    (record["changeset_id"],),
                )
        except Exception:
            # The reviewed bytes and both database records are already committed.
            # Preserve the bound attempt so targeted recovery can finish cleanup;
            # never turn a durable success into a reported apply failure.
            pass
        return {"changeset": applied, "workspace": workspace, "workflow": workflow}

    def apply(
        self,
        changeset_id: object,
        *,
        expected_record_hash: object,
        expected_review_sha256: object,
        expected_root_generation: object,
    ) -> dict[str, Any]:
        record = self.get(changeset_id)
        self._verify_action_hashes(
            record,
            expected_record_hash=expected_record_hash,
            expected_review_sha256=expected_review_sha256,
        )
        expected_generation = _generation(
            expected_root_generation,
            field="expected_root_generation",
        )
        if record["status"] != "approved":
            raise invalid_state("Only an approved creation changeset can be applied")
        if expected_generation != record["expected_root_generation"]:
            raise conflict("Creation changeset root generation changed")
        workspace_row = self.workspaces._row(record["workspace_id"])
        root, root_identity = self.workspaces._verified_root(workspace_row)
        attempt_nonce = uuid.uuid4().hex
        journal_name = f"{record['changeset_id']}.{attempt_nonce}.json"
        journal_path = self.store.creation_changeset_journals_dir / journal_name
        claimed: dict[str, Any] | None = None
        base: dict[str, Any] | None = None
        history: tuple[bytes, ...] | None = None
        journal_identity: tuple[int, int] | None = None
        phase: str | None = None
        try:
            with exclusive_world_lifecycle(root, error_type=ValueError):
                current_workspace, current_project, _summaries, source_revision, workflow = (
                    self.workspaces._refresh_snapshot(record["workspace_id"])
                )
                current_root, current_identity = self.workspaces._verified_root(
                    self.workspaces._row(record["workspace_id"])
                )
                if current_root != root or current_identity != root_identity:
                    raise conflict("Creation workspace root identity changed")
                if (
                    current_workspace["root_generation"] != expected_generation
                    or not hmac.compare_digest(source_revision, record["expected_source_revision"])
                    or workflow["status_hash"] != record["expected_workflow_status_hash"]
                ):
                    raise conflict("Creation workspace changed before changeset apply")
                self._projected_graph(
                    record,
                    root,
                    root_identity,
                    current_project,
                )
                claim_timestamp = utc_now()
                prospective_claim = {
                    **record,
                    "status": "applying",
                    "updated_at": claim_timestamp,
                    "record_hash": "",
                }
                prospective_claim["record_hash"] = creation_changeset_record_hash(prospective_claim)
                validate_studio_creation_changeset(prospective_claim)
                base = self._prepare_journal_base(
                    prospective_claim,
                    approved_record_hash=record["record_hash"],
                    root=root,
                    root_identity=root_identity,
                    attempt_nonce=attempt_nonce,
                )
                history, journal_identity = self._create_journal(journal_path, base)
                try:
                    claimed = self._claim_apply(
                        record,
                        claimed=prospective_claim,
                        root_identity=root_identity,
                        journal_name=journal_name,
                        journal_identity=journal_identity,
                    )
                except Exception:
                    self._remove_journal(
                        journal_path,
                        history=history,
                        identity=journal_identity,
                    )
                    raise
                phase = "before_staging"
                self._notify("journal_published", changeset_id=record["changeset_id"])
                with _pinned_creation_parents(base, root) as parents:
                    try:
                        stage_identities = self._prepare_stages(
                            base,
                            parents,
                            allow_existing=False,
                        )
                        history = self._advance_journal(
                            journal_path,
                            base,
                            identity=journal_identity,
                            current_phase=phase,
                            updated_phase="stages_prepared",
                            stage_identities=stage_identities,
                        )
                        phase = "stages_prepared"
                        for index, (operation, parent, stage_identity) in enumerate(
                            zip(
                                base["operations"],
                                parents,
                                stage_identities,
                                strict=True,
                            ),
                            start=1,
                        ):
                            self._apply_operation(operation, parent, stage_identity)
                            self._notify(
                                "operation_published",
                                changeset_id=record["changeset_id"],
                                operation_index=index - 1,
                            )
                            updated_phase = f"operation_{index}_committed"
                            history = self._advance_journal(
                                journal_path,
                                base,
                                identity=journal_identity,
                                current_phase=phase,
                                updated_phase=updated_phase,
                                stage_identities=stage_identities,
                            )
                            phase = updated_phase
                            self._notify(
                                "operation_applied",
                                changeset_id=record["changeset_id"],
                                operation_index=index - 1,
                            )
                        history = self._advance_journal(
                            journal_path,
                            base,
                            identity=journal_identity,
                            current_phase=phase,
                            updated_phase="files_committed",
                            stage_identities=stage_identities,
                        )
                        phase = "files_committed"
                        self._notify("files_committed", changeset_id=record["changeset_id"])
                        return self._finish_committed(
                            claimed,
                            base=base,
                            phase=phase,
                            stage_identities=stage_identities,
                            history=history,
                            journal_identity=journal_identity,
                            journal_path=journal_path,
                            root=root,
                            root_identity=root_identity,
                            parents=parents,
                        )
                    except Exception as exc:
                        if phase != "files_committed":
                            try:
                                stage_identities = (
                                    tuple(None for _operation in base["operations"])
                                    if phase == "before_staging"
                                    else stage_identities
                                )
                                self._rollback_journal(base, parents, stage_identities)
                                if history is not None and journal_identity is not None:
                                    self._remove_journal(
                                        journal_path,
                                        history=history,
                                        identity=journal_identity,
                                    )
                                self._set_status_and_remove_attempt(
                                    claimed,
                                    status="approved",
                                    topic="creation_changeset.apply_rolled_back",
                                )
                            except Exception as rollback_exc:
                                self._mark_recovery_required(
                                    record["changeset_id"],
                                    reason="rollback_failed",
                                )
                                raise StudioError(
                                    "recovery_failed",
                                    "Creation changeset apply failed and recovery is required",
                                ) from rollback_exc
                        else:
                            self._mark_recovery_required(
                                record["changeset_id"],
                                reason="files_committed",
                            )
                            raise StudioError(
                                "recovery_failed",
                                "Creation changeset files committed and recovery is required",
                            ) from exc
                        if isinstance(exc, StudioError):
                            raise exc
                        raise StudioError(
                            "internal_error", "Creation changeset apply failed and rolled back"
                        ) from exc
        except ValueError as exc:
            raise conflict("Creation changeset apply lifecycle authority changed") from exc
        except StudioError:
            if claimed is not None and not (journal_path.exists() or journal_path.is_symlink()):
                current = self.get(record["changeset_id"])
                if current["status"] in {"applying", "recovery_required"}:
                    self._set_status_and_remove_attempt(
                        current,
                        status="approved",
                        topic="creation_changeset.apply_claim_released",
                    )
            raise

    def _resume_operation(
        self,
        operation: Mapping[str, Any],
        parent: _PinnedParent,
        stage_identity: tuple[int, int] | None,
    ) -> None:
        relative = _portable_creation_path(
            operation["path"], context="creation changeset journal path"
        )
        target_name = relative.name
        target = parent.entry_info(target_name)
        rollback_name = operation["rollback_name"]
        rollback = None if rollback_name is None else parent.entry_info(rollback_name)
        stage_name = operation["stage_name"]
        stage = None if stage_name is None else parent.entry_info(stage_name)
        if target is not None and stage is not None and stage_identity is not None:
            target_payload, target_identity = _safe_entry_snapshot(
                parent,
                target_name,
                context="resumed creation changeset linked target",
                require_standalone=False,
            )
            stage_payload, current_stage_identity = _safe_entry_snapshot(
                parent,
                stage_name,
                context="resumed creation changeset linked stage",
                require_standalone=False,
            )
            if (
                target.st_nlink == 2
                and stage.st_nlink == 2
                and target_identity == stage_identity
                and current_stage_identity == stage_identity
                and target_payload == stage_payload
                and hmac.compare_digest(_hash(target_payload), operation["proposed_file_sha256"])
            ):
                parent.unlink(stage_name)
                parent.flush()
                _safe_entry_snapshot(
                    parent,
                    target_name,
                    context="resumed published creation changeset target",
                    require_standalone=True,
                )
                stage = None
                target = parent.entry_info(target_name)
        if operation["operation"] != "create" and target is not None and rollback is not None:
            target_payload, target_identity = _safe_entry_snapshot(
                parent,
                target_name,
                context="resumed creation changeset linked base",
                require_standalone=False,
            )
            rollback_payload, rollback_identity = _safe_entry_snapshot(
                parent,
                rollback_name,
                context="resumed creation changeset linked rollback",
                require_standalone=False,
            )
            encoded_base = operation["base_identity"]
            linked_base = (
                target.st_nlink == 2
                and rollback.st_nlink == 2
                and encoded_base == [target_identity[0], target_identity[1]]
                and rollback_identity == target_identity
                and target_payload == rollback_payload
                and hmac.compare_digest(
                    _hash(target_payload), operation["expected_base_file_sha256"]
                )
            )
            if linked_base:
                parent.unlink(target_name)
                parent.flush()
                _safe_entry_snapshot(
                    parent,
                    rollback_name,
                    context="resumed creation changeset rollback",
                    require_standalone=True,
                )
                target = None
                if operation["operation"] == "delete":
                    return
                if stage is None or stage_identity is None:
                    raise conflict("Creation changeset replacement stage is missing")
                self._publish_stage(
                    parent,
                    stage_name,
                    target_name,
                    stage_identity,
                    operation["proposed_file_sha256"],
                )
                return
        if operation["operation"] == "delete":
            if target is None and rollback is not None:
                return
            self._apply_operation(operation, parent, stage_identity)
            return
        if target is not None:
            payload, identity = _safe_entry_snapshot(
                parent,
                target_name,
                context="resumed creation changeset target",
                require_standalone=True,
            )
            if identity == stage_identity and hmac.compare_digest(
                _hash(payload), operation["proposed_file_sha256"]
            ):
                return
        if operation["operation"] == "replace" and target is None and rollback is not None:
            if stage is None or stage_identity is None:
                raise conflict("Creation changeset replacement stage is missing")
            self._publish_stage(
                parent,
                stage_name,
                target_name,
                stage_identity,
                operation["proposed_file_sha256"],
            )
            return
        self._apply_operation(operation, parent, stage_identity)

    def recover(
        self,
        changeset_id: object,
        *,
        mode: object,
        expected_record_hash: object,
        expected_review_sha256: object,
        expected_root_generation: object,
    ) -> dict[str, Any]:
        if mode not in {"resume", "rollback"}:
            raise invalid_request("Creation changeset recovery mode must be resume or rollback")
        record = self.get(changeset_id)
        self._verify_action_hashes(
            record,
            expected_record_hash=expected_record_hash,
            expected_review_sha256=expected_review_sha256,
        )
        expected_generation = _generation(
            expected_root_generation,
            field="expected_root_generation",
        )
        workspace_row = self.workspaces._row(record["workspace_id"])
        root, root_identity = self.workspaces._verified_root(workspace_row)
        attempt = self.store.connection.execute(
            "SELECT * FROM creation_changeset_attempts WHERE changeset_id = ?",
            (record["changeset_id"],),
        ).fetchone()
        if attempt is None:
            workspace, _project, _summaries, _revision, workflow = (
                self.workspaces._refresh_snapshot(record["workspace_id"])
            )
            if workspace["root_generation"] != expected_generation:
                raise conflict("Creation workspace generation changed")
            if record["status"] not in {"approved", "applied"}:
                raise StudioError(
                    "recovery_ambiguous", "Creation changeset has no bound recovery attempt"
                )
            return {
                "changeset": record,
                "workspace": workspace,
                "workflow": workflow,
                "outcome": "not_needed",
            }
        journal_name = self._validated_attempt_journal_name(attempt, record)
        journal_path = self.store.creation_changeset_journals_dir / journal_name
        try:
            with exclusive_world_lifecycle(root, error_type=ValueError):
                current_root, current_identity = self.workspaces._verified_root(
                    self.workspaces._row(record["workspace_id"])
                )
                if current_root != root or current_identity != root_identity:
                    raise conflict("Creation workspace root identity changed")
                current_workspace = self.workspaces._validated_row(
                    self.workspaces._row(record["workspace_id"])
                )
                if current_workspace["root_generation"] != expected_generation:
                    raise conflict("Creation workspace generation changed")
                if (
                    record["status"] != "applied"
                    and current_workspace["root_generation"] != record["expected_root_generation"]
                ):
                    raise conflict("Creation changeset recovery generation changed")
                if not journal_path.exists() and not journal_path.is_symlink():
                    workspace, _project, _summaries, revision, workflow = (
                        self.workspaces._refresh_snapshot(record["workspace_id"])
                    )
                    if record["status"] == "applied":
                        with self.store.connection:
                            self.store.connection.execute(
                                "DELETE FROM creation_changeset_attempts WHERE changeset_id = ?",
                                (record["changeset_id"],),
                            )
                        return {
                            "changeset": record,
                            "workspace": workspace,
                            "workflow": workflow,
                            "outcome": "committed",
                        }
                    if not hmac.compare_digest(revision, record["expected_source_revision"]):
                        raise StudioError(
                            "recovery_ambiguous",
                            "Missing creation changeset journal has ambiguous files",
                        )
                    restored = self._set_status_and_remove_attempt(
                        record,
                        status="approved",
                        topic="creation_changeset.recovered_orphan_claim",
                    )
                    return {
                        "changeset": restored,
                        "workspace": workspace,
                        "workflow": workflow,
                        "outcome": "rolled_back",
                    }
                base, phase, stage_identities, history, journal_identity = self._read_journal(
                    journal_path,
                    attempt,
                    record,
                    root,
                    root_identity,
                )
                with _pinned_creation_parents(base, root) as parents:
                    if mode == "rollback" and record["status"] != "applied":
                        self._rollback_journal(base, parents, stage_identities)
                        self._remove_journal(
                            journal_path,
                            history=history,
                            identity=journal_identity,
                        )
                        restored = self._set_status_and_remove_attempt(
                            record,
                            status="approved",
                            topic="creation_changeset.recovered_rollback",
                        )
                        workspace, _project, _summaries, _revision, workflow = (
                            self.workspaces._refresh_snapshot(record["workspace_id"])
                        )
                        return {
                            "changeset": restored,
                            "workspace": workspace,
                            "workflow": workflow,
                            "outcome": "rolled_back",
                        }
                    if phase == "before_staging":
                        stage_identities = self._prepare_stages(
                            base,
                            parents,
                            allow_existing=True,
                        )
                        history = self._advance_journal(
                            journal_path,
                            base,
                            identity=journal_identity,
                            current_phase=phase,
                            updated_phase="stages_prepared",
                            stage_identities=stage_identities,
                        )
                        phase = "stages_prepared"
                    committed = _operation_commit_count(phase, len(base["operations"]))
                    if phase not in {"files_committed", "database_committed"}:
                        for index in range(committed, len(base["operations"])):
                            self._resume_operation(
                                base["operations"][index],
                                parents[index],
                                stage_identities[index],
                            )
                            updated_phase = f"operation_{index + 1}_committed"
                            history = self._advance_journal(
                                journal_path,
                                base,
                                identity=journal_identity,
                                current_phase=phase,
                                updated_phase=updated_phase,
                                stage_identities=stage_identities,
                            )
                            phase = updated_phase
                        history = self._advance_journal(
                            journal_path,
                            base,
                            identity=journal_identity,
                            current_phase=phase,
                            updated_phase="files_committed",
                            stage_identities=stage_identities,
                        )
                        phase = "files_committed"
                    result = self._finish_committed(
                        record,
                        base=base,
                        phase=phase,
                        stage_identities=stage_identities,
                        history=history,
                        journal_identity=journal_identity,
                        journal_path=journal_path,
                        root=root,
                        root_identity=root_identity,
                        parents=parents,
                    )
                    return {**result, "outcome": "committed"}
        except ValueError as exc:
            raise conflict("Creation changeset recovery lifecycle authority changed") from exc
        except StudioError:
            self._mark_recovery_required(record["changeset_id"], reason="recovery_failed")
            raise

    def _transition(
        self,
        changeset_id: object,
        *,
        allowed: set[str],
        status: str,
        expected_record_hash: object,
        expected_review_sha256: object,
    ) -> dict[str, Any]:
        identifier = _identifier(changeset_id, field="changeset_id")
        expected_record = _digest(expected_record_hash, field="expected_record_hash")
        expected_review = _digest(
            expected_review_sha256,
            field="expected_review_sha256",
        )
        row = self.store.connection.execute(
            "SELECT * FROM creation_changesets WHERE changeset_id = ?",
            (identifier,),
        ).fetchone()
        if row is None:
            raise not_found(f"Creation changeset {identifier} was not found")
        record = self._validated_row(row)
        if not hmac.compare_digest(record["record_hash"], expected_record):
            raise conflict("Creation changeset record changed")
        if not hmac.compare_digest(record["review_sha256"], expected_review):
            raise conflict("Creation changeset review changed")
        if record["status"] not in allowed:
            raise invalid_state(f"Creation changeset cannot transition from {record['status']}")
        updated = {**record, "status": status, "updated_at": utc_now(), "record_hash": ""}
        updated["record_hash"] = creation_changeset_record_hash(updated)
        validate_studio_creation_changeset(updated)
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE creation_changesets SET status = ?, record_json = ?, "
                "generation = generation + 1 WHERE changeset_id = ? AND generation = ?",
                (status, encode_json(updated), identifier, row["generation"]),
            )
            if cursor.rowcount != 1:
                raise conflict("Creation changeset changed concurrently")
            self.store.record_creation_event(
                workspace_id=updated["workspace_id"],
                topic=f"creation_changeset.{status}",
                entity_type="creation_changeset",
                entity_id=identifier,
                payload={"record_hash": updated["record_hash"]},
                created_at=updated["updated_at"],
            )
        return updated

    def _validated_row(self, row: sqlite3.Row) -> dict[str, Any]:
        record = decode_object(row["record_json"], context="creation changeset")
        try:
            checked = validate_studio_creation_changeset(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored creation changeset is invalid") from exc
        if checked["status"] != row["status"]:
            raise StudioError("internal_error", "Stored creation changeset state diverged")
        operation_rows = self.store.connection.execute(
            "SELECT path, operation, base_blob_sha256, base_size, "
            "proposed_blob_sha256, proposed_size "
            "FROM creation_changeset_operations WHERE changeset_id = ? ORDER BY path",
            (checked["changeset_id"],),
        ).fetchall()
        expected = [
            (
                operation["path"],
                operation["operation"],
                operation["expected_base_file_sha256"],
                operation["expected_base_size"],
                operation["proposed_file_sha256"],
                operation["proposed_size"],
            )
            for operation in checked["operations"]
        ]
        retained = [
            (
                operation["path"],
                operation["operation"],
                operation["base_blob_sha256"],
                operation["base_size"],
                operation["proposed_blob_sha256"],
                operation["proposed_size"],
            )
            for operation in operation_rows
        ]
        if retained != expected:
            raise StudioError(
                "internal_error", "Stored creation changeset operation projection diverged"
            )
        return checked
