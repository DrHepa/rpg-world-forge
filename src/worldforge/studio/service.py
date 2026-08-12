from __future__ import annotations

import base64
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from worldforge.studio.asset_previews import AssetPreviewManager
from worldforge.studio.assets import AssetCatalogManager
from worldforge.studio.authoring import AuthoringManager
from worldforge.studio.changesets import ChangesetManager
from worldforge.studio.contracts import (
    ASSET_PREVIEW_CHUNK_BYTES,
    METHODS,
    METHODS_V2,
    METHODS_V3,
    METHODS_V4,
    METHODS_V5,
    PROTOCOL_FORMAT,
    STUDIO_PROTOCOL_V2,
    STUDIO_PROTOCOL_V3,
    STUDIO_PROTOCOL_V4,
    STUDIO_PROTOCOL_V5,
    STUDIO_VERSION,
    validate_studio_protocol_envelope,
)
from worldforge.studio.creation_artifacts import CreationArtifactRegistry
from worldforge.studio.creation_authoring import CreationAuthoringManager
from worldforge.studio.creation_evidence import CreationEvidenceManager
from worldforge.studio.creation_grants import CreationRootGrantManager
from worldforge.studio.creation_jobs import (
    CreationJobCoordinator,
    CreationJobManager,
    CreationJobScheduler,
)
from worldforge.studio.creation_output_grants import CreationOutputGrantManager
from worldforge.studio.creation_previews import (
    CreationPreviewAuthorityResolver,
    CreationPreviewManager,
)
from worldforge.studio.creation_workspaces import CreationWorkspaceManager
from worldforge.studio.errors import (
    StudioContractError,
    StudioError,
    invalid_request,
    invalid_state,
    not_found,
)
from worldforge.studio.executor import JobScheduler
from worldforge.studio.external_grants import ExternalGrantManager
from worldforge.studio.jobs import JobManager
from worldforge.studio.jsonio import (
    decode_ndjson_object,
    encode_ndjson_object,
    read_ndjson_line,
)
from worldforge.studio.storage import StudioStore
from worldforge.studio.workspaces import WorkspaceManager


def _closed_params(
    params: dict[str, Any],
    *,
    allowed: set[str],
    required: set[str] = frozenset(),
) -> None:
    unknown = set(params) - allowed
    missing = required - set(params)
    if unknown or missing:
        fields = unknown or missing
        raise invalid_request(f"Method params have invalid fields: {', '.join(sorted(fields))}")


class StudioService:
    def __init__(
        self,
        store: StudioStore,
        scheduler: JobScheduler | None = None,
        creation_scheduler: CreationJobScheduler | None = None,
    ) -> None:
        self.store = store
        self.scheduler = scheduler
        self.creation_scheduler = creation_scheduler
        self._closed = False
        self._preview_shutdown = False
        self._creation_preview_shutdown = False
        self.workspaces = WorkspaceManager(store)
        self.assets = AssetCatalogManager(self.workspaces)
        preview_manager: AssetPreviewManager | None = None
        creation_preview_manager: CreationPreviewManager | None = None
        try:
            preview_manager = AssetPreviewManager(self.assets)
            self.asset_previews = preview_manager
            self.authoring = AuthoringManager(self.workspaces)
            self.changesets = ChangesetManager(store)
            self.jobs = JobManager(store)
            self.external_grants = ExternalGrantManager(store)
            self.creation_root_grants = CreationRootGrantManager(store)
            self.creation_output_grants = CreationOutputGrantManager(store)
            self.creation_workspaces = CreationWorkspaceManager(
                store,
                grants=self.creation_root_grants,
            )
            self.creation_authoring = CreationAuthoringManager(
                store,
                workspaces=self.creation_workspaces,
            )
            self.creation_artifacts = CreationArtifactRegistry(
                store,
                workspaces=self.creation_workspaces,
            )
            self.creation_evidence = CreationEvidenceManager(
                self.creation_workspaces,
                candidates=self.creation_artifacts,
            )
            creation_preview_manager = CreationPreviewManager(
                CreationPreviewAuthorityResolver(
                    self.creation_evidence,
                    self.creation_artifacts,
                    self.creation_output_grants,
                )
            )
            self.creation_previews = creation_preview_manager
            self.creation_jobs = CreationJobManager(
                store,
                workspaces=self.creation_workspaces,
                evidence=self.creation_evidence,
                artifacts=self.creation_artifacts,
                output_grants=self.creation_output_grants,
            )
            self.creation_job_coordinator = CreationJobCoordinator(self.creation_jobs)
            self._methods: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
                "service.initialize": self._initialize,
                "workspace.register": self._workspace_register,
                "workspace.list": self._workspace_list,
                "workspace.get": self._workspace_get,
                "workspace.overview": self._workspace_overview,
                "source.list": self._source_list,
                "source.read": self._source_read,
                "asset.catalog.list": self._asset_catalog_list,
                "asset.catalog.inspect": self._asset_catalog_inspect,
                "asset.preview.open": self._asset_preview_open,
                "asset.preview.read": self._asset_preview_read,
                "asset.preview.close": self._asset_preview_close,
                "world.validate": self._world_validate,
                "world.analyze": self._world_analyze,
                "events.list": self._events_list,
                "changeset.create": self._changeset_create,
                "changeset.get": self._changeset_get,
                "changeset.list": self._changeset_list,
                "changeset.diff": self._changeset_diff,
                "changeset.approve": self._changeset_approve,
                "changeset.reject": self._changeset_reject,
                "changeset.apply": self._changeset_apply,
                "job.create": self._job_create,
                "job.get": self._job_get,
                "job.list": self._job_list,
                "job.transition": self._job_transition,
                "job.cancel": self._job_cancel,
                "job.recover": self._job_recover,
                "external_grant.create": self._external_grant_create,
                "external_grant.get": self._external_grant_get,
                "external_grant.revoke": self._external_grant_revoke,
                "creation_root_grant.create": self._creation_root_grant_create,
                "creation_root_grant.get": self._creation_root_grant_get,
                "creation_root_grant.revoke": self._creation_root_grant_revoke,
                "creation_output_grant.create": self._creation_output_grant_create,
                "creation_output_grant.get": self._creation_output_grant_get,
                "creation_output_grant.list": self._creation_output_grant_list,
                "creation_output_grant.revoke": self._creation_output_grant_revoke,
                "creation_workspace.create": self._creation_workspace_create,
                "creation_workspace.recover": self._creation_workspace_recover,
                "creation_workspace.register": self._creation_workspace_register,
                "creation_workspace.get": self._creation_workspace_get,
                "creation_workspace.list": self._creation_workspace_list,
                "creation_workspace.open": self._creation_workspace_open,
                "creation_document.list": self._creation_document_list,
                "creation_document.read": self._creation_document_read,
                "creation_changeset.create": self._creation_changeset_create,
                "creation_changeset.get": self._creation_changeset_get,
                "creation_changeset.list": self._creation_changeset_list,
                "creation_changeset.diff": self._creation_changeset_diff,
                "creation_changeset.approve": self._creation_changeset_approve,
                "creation_changeset.reject": self._creation_changeset_reject,
                "creation_changeset.apply": self._creation_changeset_apply,
                "creation_changeset.recover": self._creation_changeset_recover,
                "creation_workflow.get": self._creation_workflow_get,
                "creation_workflow.reconcile": self._creation_workflow_reconcile,
                "creation_phase.read": self._creation_phase_read,
                "creation_phase.validate": self._creation_phase_validate,
                "creation_phase.complete": self._creation_phase_complete,
                "creation_phase.reopen": self._creation_phase_reopen,
                "creation_readiness.inspect": self._creation_readiness_inspect,
                "creation_artifact.list": self._creation_artifact_list,
                "creation_artifact.inspect": self._creation_artifact_inspect,
                "creation_evidence.inspect": self._creation_evidence_inspect,
                "creation_preview.open": self._creation_preview_open,
                "creation_preview.read": self._creation_preview_read,
                "creation_preview.close": self._creation_preview_close,
                "creation_job.create": self._creation_job_create,
                "creation_job.get": self._creation_job_get,
                "creation_job.list": self._creation_job_list,
                "creation_job.cancel": self._creation_job_cancel,
                "creation_job.recover": self._creation_job_recover,
                "creation_event.list": self._creation_event_list,
            }
        except BaseException:
            if creation_preview_manager is not None:
                try:
                    creation_preview_manager.shutdown()
                except BaseException:
                    pass
            if preview_manager is not None:
                try:
                    preview_manager.shutdown()
                except BaseException:
                    pass
            raise

    def handle(self, envelope: object) -> dict[str, Any]:
        if self._closed:
            raise invalid_state("Studio service is closed")
        try:
            request = validate_studio_protocol_envelope(envelope)
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        if request["kind"] != "request":
            raise invalid_request("Studio service accepts only request envelopes")
        if request["method"] == "service.initialize":
            result = self._initialize(
                request["params"],
                protocol_version=request["protocol_version"],
            )
        elif request["method"] in {"job.get", "job.list", "job.transition", "job.cancel"}:
            result = self._dispatch_versioned_job_method(
                request["method"],
                request["params"],
                protocol_version=request["protocol_version"],
            )
        elif request["method"] in {
            "creation_job.get",
            "creation_job.list",
            "creation_job.cancel",
            "creation_job.recover",
        }:
            result = self._dispatch_versioned_creation_job_method(
                request["method"],
                request["params"],
                protocol_version=request["protocol_version"],
            )
        elif request["method"] in {
            "creation_output_grant.get",
            "creation_output_grant.list",
            "creation_output_grant.revoke",
        }:
            result = self._dispatch_versioned_creation_output_grant_method(
                request["method"],
                request["params"],
                protocol_version=request["protocol_version"],
            )
        elif request["method"] == "creation_artifact.inspect":
            result = self._creation_artifact_inspect(
                request["params"],
                protocol_version=request["protocol_version"],
            )
        else:
            result = self._methods[request["method"]](request["params"])
        response = {
            "protocol": PROTOCOL_FORMAT,
            "protocol_version": request["protocol_version"],
            "kind": "response",
            "request_id": request["request_id"],
            "method": request["method"],
            "result": result,
        }
        try:
            checked = validate_studio_protocol_envelope(response)
            # Prove the response fits the transport while request-local error
            # framing can still replace it with a bounded error envelope.
            encode_ndjson_object(checked)
            return checked
        except StudioContractError as exc:
            raise StudioError(
                "internal_error", "Studio method produced an invalid response"
            ) from exc

    def close(self) -> None:
        self._closed = True
        first_error: BaseException | None = None
        if not self._preview_shutdown:
            try:
                self.asset_previews.shutdown()
                self._preview_shutdown = True
            except BaseException as exc:
                first_error = exc
        if not getattr(self, "_creation_preview_shutdown", False):
            creation_previews = getattr(self, "creation_previews", None)
            if creation_previews is None:
                self._creation_preview_shutdown = True
            else:
                try:
                    creation_previews.shutdown()
                    self._creation_preview_shutdown = True
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error

    @staticmethod
    def _initialize(
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_VERSION,
    ) -> dict[str, Any]:
        _closed_params(params, allowed=set())
        methods = (
            METHODS
            if protocol_version == STUDIO_VERSION
            else METHODS_V2
            if protocol_version == STUDIO_PROTOCOL_V2
            else METHODS_V3
            if protocol_version == STUDIO_PROTOCOL_V3
            else METHODS_V4
            if protocol_version == STUDIO_PROTOCOL_V4
            else METHODS_V5
        )
        if protocol_version == STUDIO_PROTOCOL_V5:
            return {
                "service": "world-forge.studio",
                "service_version": STUDIO_PROTOCOL_V5,
                "protocol": PROTOCOL_FORMAT,
                "protocol_version": STUDIO_PROTOCOL_V5,
                "methods": sorted(METHODS_V5),
                "capabilities": {
                    "creation_evidence_projection": True,
                    "creation_jobs": True,
                    "creation_runtime_compose": True,
                    "creation_runtime_bundle": True,
                    "creation_materialization_bundle": True,
                    "creation_output_grants": True,
                    "creation_asset_previews": True,
                    "asset_previews": False,
                    "materialization_execution": True,
                    "game_packaging": True,
                    "game_package_extraction": True,
                    "asset_authority_reviews": True,
                    "asset_release_authority": True,
                    "runtime_headless_authority": True,
                    "creation_preview_pre_release": True,
                },
            }
        if protocol_version == STUDIO_PROTOCOL_V4:
            return {
                "service": "world-forge.studio",
                "service_version": STUDIO_PROTOCOL_V4,
                "protocol": PROTOCOL_FORMAT,
                "protocol_version": STUDIO_PROTOCOL_V4,
                "methods": sorted(METHODS_V4),
                "capabilities": {
                    "creation_evidence_projection": True,
                    "creation_jobs": True,
                    "creation_runtime_compose": True,
                    "creation_runtime_bundle": True,
                    "creation_materialization_bundle": True,
                    "creation_output_grants": True,
                    "creation_asset_previews": True,
                    "asset_previews": False,
                    "materialization_execution": True,
                    "game_packaging": True,
                    "game_package_extraction": True,
                },
            }
        if protocol_version == STUDIO_PROTOCOL_V3:
            return {
                "service": "world-forge.studio",
                "service_version": STUDIO_PROTOCOL_V3,
                "protocol": PROTOCOL_FORMAT,
                "protocol_version": STUDIO_PROTOCOL_V3,
                "methods": sorted(METHODS_V3),
                "capabilities": {
                    "generic_creation": True,
                    "safe_project_creation": True,
                    "read_only_documents": True,
                    "profile_editing": True,
                    "generic_jobs": False,
                    "reviewed_changesets": True,
                    "workflow_mutations": True,
                    "inline_phase_reports": True,
                },
            }
        result = {
            "service": "rpg-world-forge.studio",
            "service_version": protocol_version,
            "protocol": PROTOCOL_FORMAT,
            "protocol_version": protocol_version,
            "methods": sorted(methods),
            "capabilities": {
                "providers": False,
                "watcher": False,
                "source_inspection": True,
                "world_validation": True,
                "narrative_analysis": True,
                "staged_changesets": True,
                "durable_jobs": True,
                "asset_catalog_inspection": True,
                "asset_previews": True,
            },
        }
        if protocol_version == STUDIO_PROTOCOL_V2:
            result["capabilities"]["external_artifact_jobs"] = True
        return result

    def _workspace_register(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"workspace": self.workspaces.register(params)}

    def _workspace_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed=set())
        return {"workspaces": self.workspaces.list()}

    def _workspace_get(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id"}, required={"workspace_id"})
        return {"workspace": self.workspaces.get(params["workspace_id"])}

    def _workspace_overview(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id"}, required={"workspace_id"})
        return self.authoring.overview(params["workspace_id"])

    def _source_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id"}, required={"workspace_id"})
        return self.authoring.list_sources(params["workspace_id"])

    def _source_read(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id", "path"}, required={"workspace_id", "path"})
        return self.authoring.read_source(params["workspace_id"], params["path"])

    def _asset_catalog_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"workspace_id", "offset", "limit", "expected_manifest_revision"},
            required={"workspace_id"},
        )
        return self.assets.list(
            params["workspace_id"],
            offset=params.get("offset", 0),
            limit=params.get("limit", 64),
            expected_manifest_revision=params.get("expected_manifest_revision"),
        )

    def _asset_catalog_inspect(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"workspace_id", "entry_id", "expected_manifest_revision"},
            required={"workspace_id", "entry_id", "expected_manifest_revision"},
        )
        return self.assets.inspect(
            params["workspace_id"],
            entry_id=params["entry_id"],
            expected_manifest_revision=params["expected_manifest_revision"],
        )

    def _asset_preview_open(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"workspace_id", "manifest_revision", "entry_id"},
            required={"workspace_id", "manifest_revision", "entry_id"},
        )
        opened = self.asset_previews.open(
            params["workspace_id"],
            params["manifest_revision"],
            params["entry_id"],
        )
        return {**opened, "chunk_bytes": ASSET_PREVIEW_CHUNK_BYTES}

    def _asset_preview_read(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"handle", "sequence"},
            required={"handle", "sequence"},
        )
        chunk = self.asset_previews.read(params["handle"], params["sequence"])
        payload = chunk.get("payload")
        if not isinstance(payload, bytes):
            raise StudioError("internal_error", "Asset preview read produced invalid bytes")
        return {
            "handle": chunk.get("handle"),
            "sequence": chunk.get("sequence"),
            "data_base64": base64.b64encode(payload).decode("ascii"),
            "byte_length": len(payload),
            "cumulative_bytes": chunk.get("cumulative_bytes"),
            "cumulative_sha256": chunk.get("cumulative_sha256"),
            "eof": chunk.get("eof"),
        }

    def _asset_preview_close(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"handle"}, required={"handle"})
        handle = params["handle"]
        self.asset_previews.close(handle)
        return {"handle": handle, "closed": True}

    def _world_validate(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id"}, required={"workspace_id"})
        return self.authoring.validate_world(params["workspace_id"])

    def _world_analyze(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id"}, required={"workspace_id"})
        return self.authoring.analyze_world(params["workspace_id"])

    def _events_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id", "after_id", "limit"})
        workspace_id = params.get("workspace_id")
        if workspace_id is not None:
            self.workspaces.get(workspace_id)
        after_id = params.get("after_id", 0)
        limit = params.get("limit", 100)
        if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
            raise invalid_request("events.list after_id must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise invalid_request("events.list limit must be an integer from 1 to 1000")
        events = self.store.list_events(workspace_id=workspace_id, after_id=after_id, limit=limit)
        return {"events": events}

    def _changeset_create(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"changeset": self.changesets.create(params)}

    def _changeset_get(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"changeset_id"}, required={"changeset_id"})
        return {"changeset": self.changesets.get(params["changeset_id"])}

    def _changeset_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id", "status", "limit"})
        return {
            "changesets": self.changesets.list(
                workspace_id=params.get("workspace_id"),
                status=params.get("status"),
                limit=params.get("limit", 100),
            )
        }

    def _changeset_diff(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"changeset_id"}, required={"changeset_id"})
        return {"diff": self.changesets.diff(params["changeset_id"])}

    def _changeset_approve(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"changeset_id", "expected_review_sha256"},
            required={"changeset_id"},
        )
        return {
            "changeset": self.changesets.approve(
                params["changeset_id"],
                expected_review_sha256=params.get("expected_review_sha256"),
            )
        }

    def _changeset_reject(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"changeset_id", "expected_review_sha256"},
            required={"changeset_id"},
        )
        return {
            "changeset": self.changesets.reject(
                params["changeset_id"],
                expected_review_sha256=params.get("expected_review_sha256"),
            )
        }

    def _changeset_apply(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"changeset_id", "expected_review_sha256"},
            required={"changeset_id"},
        )
        return {
            "changeset": self.changesets.apply(
                params["changeset_id"],
                expected_review_sha256=params.get("expected_review_sha256"),
            )
        }

    def _job_create(self, params: dict[str, Any]) -> dict[str, Any]:
        job = self.jobs.create(params)
        if self.scheduler is not None:
            self.scheduler.notify()
        return {"job": job}

    @staticmethod
    def _job_format_versions(protocol_version: int) -> frozenset[int]:
        return frozenset({1, 2}) if protocol_version == STUDIO_VERSION else frozenset({3})

    def _dispatch_versioned_job_method(
        self,
        method: str,
        params: dict[str, Any],
        *,
        protocol_version: int,
    ) -> dict[str, Any]:
        handlers = {
            "job.get": self._job_get,
            "job.list": self._job_list,
            "job.transition": self._job_transition,
            "job.cancel": self._job_cancel,
        }
        return handlers[method](params, protocol_version=protocol_version)

    def _job_get(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_VERSION,
    ) -> dict[str, Any]:
        _closed_params(params, allowed={"job_id"}, required={"job_id"})
        return {
            "job": self.jobs.get(
                params["job_id"],
                format_versions=self._job_format_versions(protocol_version),
            )
        }

    def _job_list(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_VERSION,
    ) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id", "state", "limit"})
        return {
            "jobs": self.jobs.list(
                workspace_id=params.get("workspace_id"),
                state=params.get("state"),
                limit=params.get("limit", 100),
                format_versions=self._job_format_versions(protocol_version),
            )
        }

    def _job_transition(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_VERSION,
    ) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"job_id", "state", "result", "error"},
            required={"job_id", "state"},
        )
        transition = {key: value for key, value in params.items() if key != "job_id"}
        return {
            "job": self.jobs.transition(
                params["job_id"],
                transition,
                format_versions=self._job_format_versions(protocol_version),
            )
        }

    def _job_cancel(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_VERSION,
    ) -> dict[str, Any]:
        _closed_params(params, allowed={"job_id"}, required={"job_id"})
        job = self.jobs.cancel(
            params["job_id"],
            format_versions=self._job_format_versions(protocol_version),
        )
        if self.scheduler is not None:
            self.scheduler.notify()
        return {"job": job}

    def _job_recover(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"job_id", "action"},
            required={"job_id", "action"},
        )
        job = self.jobs.recover(params["job_id"], params["action"])
        if self.scheduler is not None:
            self.scheduler.notify()
        return {"job": job}

    def _external_grant_create(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"grant": self.external_grants.create(params)}

    def _external_grant_get(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"grant_id"}, required={"grant_id"})
        return {"grant": self.external_grants.get(params["grant_id"])}

    def _external_grant_revoke(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"grant_id"}, required={"grant_id"})
        return {"grant": self.external_grants.revoke(params["grant_id"])}

    def _creation_root_grant_create(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"grant": self.creation_root_grants.create(params)}

    def _creation_root_grant_get(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"grant": self.creation_root_grants.get(params["grant_id"])}

    def _creation_root_grant_revoke(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "grant": self.creation_root_grants.revoke(
                params["grant_id"],
                expected_generation=params["expected_generation"],
            )
        }

    def _creation_output_grant_create(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"grant": self.creation_output_grants.create(params)}

    @staticmethod
    def _max_creation_output_grant_version(protocol_version: int) -> int:
        return 6 if protocol_version == STUDIO_PROTOCOL_V5 else 5

    @classmethod
    def _require_visible_creation_output_grant(
        cls,
        grant: dict[str, Any],
        *,
        protocol_version: int,
    ) -> dict[str, Any]:
        version = grant.get("format_version")
        if type(version) is not int or version not in range(
            1,
            cls._max_creation_output_grant_version(protocol_version) + 1,
        ):
            raise not_found("Creation output grant is unavailable in this protocol")
        return grant

    def _dispatch_versioned_creation_output_grant_method(
        self,
        method: str,
        params: dict[str, Any],
        *,
        protocol_version: int,
    ) -> dict[str, Any]:
        handlers = {
            "creation_output_grant.get": self._creation_output_grant_get,
            "creation_output_grant.list": self._creation_output_grant_list,
            "creation_output_grant.revoke": self._creation_output_grant_revoke,
        }
        return handlers[method](params, protocol_version=protocol_version)

    def _creation_output_grant_get(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_PROTOCOL_V4,
    ) -> dict[str, Any]:
        grant = self.creation_output_grants.get(params["grant_id"])
        return {
            "grant": self._require_visible_creation_output_grant(
                grant,
                protocol_version=protocol_version,
            )
        }

    def _creation_output_grant_list(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_PROTOCOL_V4,
    ) -> dict[str, Any]:
        snapshot = self.creation_evidence.list(
            {
                "workspace_id": params["workspace_id"],
                "expected_root_generation": params["expected_root_generation"],
                "expected_source_revision": params["expected_source_revision"],
                "expected_workflow_status_hash": params["expected_workflow_status_hash"],
                "expected_artifact_snapshot_hash": params["expected_artifact_snapshot_hash"],
                "lifecycle": None,
                "cursor": None,
                "limit": 1,
            }
        )
        maximum = self._max_creation_output_grant_version(protocol_version)
        grants: list[dict[str, Any]] = []
        raw_cursor = params["cursor"]
        next_cursor: str | None = None
        while len(grants) < params["limit"]:
            raw_grants, raw_next_cursor = self.creation_output_grants.list(
                workspace_id=params["workspace_id"],
                cursor=raw_cursor,
                limit=params["limit"],
            )
            if not raw_grants and raw_next_cursor is not None:
                raise invalid_state("Creation output grant pagination did not advance")
            for index, grant in enumerate(raw_grants):
                version = grant.get("format_version")
                if type(version) is int and version in range(1, maximum + 1):
                    grants.append(grant)
                    if len(grants) == params["limit"]:
                        has_more = index + 1 < len(raw_grants) or raw_next_cursor is not None
                        next_cursor = grant["grant_id"] if has_more else None
                        break
            if len(grants) == params["limit"] or raw_next_cursor is None:
                break
            if raw_next_cursor == raw_cursor:
                raise invalid_state("Creation output grant pagination did not advance")
            raw_cursor = raw_next_cursor
        return {
            "authority": snapshot["authority"],
            "artifact_snapshot_hash": snapshot["artifact_snapshot_hash"],
            "grants": grants,
            "next_cursor": next_cursor,
        }

    def _creation_output_grant_revoke(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_PROTOCOL_V4,
    ) -> dict[str, Any]:
        self._require_visible_creation_output_grant(
            self.creation_output_grants.get(params["grant_id"]),
            protocol_version=protocol_version,
        )
        grant = self.creation_output_grants.revoke(
            params["grant_id"],
            expected_generation=params["expected_generation"],
        )
        return {
            "grant": self._require_visible_creation_output_grant(
                grant,
                protocol_version=protocol_version,
            )
        }

    def _creation_workspace_create(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"workspace": self.creation_workspaces.create(params)}

    def _creation_workspace_recover(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.creation_workspaces.recover(
            params["workspace_id"],
            expected_root_generation=params["expected_root_generation"],
        )

    def _creation_workspace_register(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"workspace": self.creation_workspaces.register(params)}

    def _creation_workspace_get(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"workspace": self.creation_workspaces.get(params["workspace_id"])}

    def _creation_workspace_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed=set())
        return {"workspaces": self.creation_workspaces.list()}

    def _creation_workspace_open(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.creation_workspaces.open(params["workspace_id"])

    def _creation_document_list(self, params: dict[str, Any]) -> dict[str, Any]:
        documents = self.creation_workspaces.list_documents(
            params["workspace_id"],
            expected_source_revision=params["expected_source_revision"],
        )
        return {
            "documents": documents,
            "source_revision": params["expected_source_revision"],
        }

    def _creation_document_read(self, params: dict[str, Any]) -> dict[str, Any]:
        document = self.creation_workspaces.read_document(
            params["workspace_id"],
            params["path"],
            expected_source_revision=params["expected_source_revision"],
        )
        return {
            "document": document,
            "source_revision": params["expected_source_revision"],
        }

    def _creation_changeset_create(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"changeset": self.creation_authoring.create(params)}

    def _creation_changeset_get(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"changeset": self.creation_authoring.get(params["changeset_id"])}

    def _creation_changeset_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "changesets": self.creation_authoring.list(
                workspace_id=params.get("workspace_id"),
                status=params.get("status"),
                limit=params.get("limit", 100),
            )
        }

    def _creation_changeset_diff(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"diff": self.creation_authoring.diff(params["changeset_id"])}

    def _creation_changeset_approve(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "changeset": self.creation_authoring.approve(
                params["changeset_id"],
                expected_record_hash=params["expected_record_hash"],
                expected_review_sha256=params["expected_review_sha256"],
            )
        }

    def _creation_changeset_reject(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "changeset": self.creation_authoring.reject(
                params["changeset_id"],
                expected_record_hash=params["expected_record_hash"],
                expected_review_sha256=params["expected_review_sha256"],
            )
        }

    @staticmethod
    def _creation_transition_result(result: dict[str, Any]) -> dict[str, Any]:
        return {
            **result,
            "workflow": {
                "source_revision": result["workspace"]["source_revision"],
                **result["workflow"],
            },
        }

    def _creation_changeset_apply(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self.creation_authoring.apply(
            params["changeset_id"],
            expected_record_hash=params["expected_record_hash"],
            expected_review_sha256=params["expected_review_sha256"],
            expected_root_generation=params["expected_root_generation"],
        )
        return self._creation_transition_result(result)

    def _creation_changeset_recover(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self.creation_authoring.recover(
            params["changeset_id"],
            mode=params["mode"],
            expected_record_hash=params["expected_record_hash"],
            expected_review_sha256=params["expected_review_sha256"],
            expected_root_generation=params["expected_root_generation"],
        )
        return self._creation_transition_result(result)

    def _creation_workflow_get(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"workflow": self.creation_workspaces.workflow(params["workspace_id"])}

    def _creation_workflow_reconcile(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.creation_workspaces.reconcile_workflow(
            params["workspace_id"],
            expected_root_generation=params["expected_root_generation"],
            expected_source_revision=params["expected_source_revision"],
            expected_workflow_status_hash=params["expected_workflow_status_hash"],
            artifact_registry=tuple(params["artifact_registry"]),
        )

    def _creation_phase_read(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.creation_workspaces.read_phase_report(
            params["workspace_id"],
            params["phase_id"],
            expected_root_generation=params["expected_root_generation"],
            expected_source_revision=params["expected_source_revision"],
            expected_workflow_status_hash=params["expected_workflow_status_hash"],
        )

    def _creation_phase_validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.creation_workspaces.validate_phase(
            params["workspace_id"],
            expected_root_generation=params["expected_root_generation"],
            expected_source_revision=params["expected_source_revision"],
            expected_workflow_status_hash=params["expected_workflow_status_hash"],
            report=params["report"],
            artifact_registry=tuple(params["artifact_registry"]),
        )

    def _creation_phase_complete(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.creation_workspaces.complete_phase(
            params["workspace_id"],
            expected_root_generation=params["expected_root_generation"],
            expected_source_revision=params["expected_source_revision"],
            expected_workflow_status_hash=params["expected_workflow_status_hash"],
            report=params["report"],
            artifact_registry=tuple(params["artifact_registry"]),
        )

    def _creation_phase_reopen(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.creation_workspaces.reopen_phase(
            params["workspace_id"],
            params["phase_id"],
            reason=params["reason"],
            approved_by=params["approved_by"],
            expected_root_generation=params["expected_root_generation"],
            expected_source_revision=params["expected_source_revision"],
            expected_workflow_status_hash=params["expected_workflow_status_hash"],
        )

    def _creation_readiness_inspect(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"readiness": self.creation_workspaces.readiness(params["workspace_id"])}

    def _creation_artifact_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.creation_evidence.list(params)

    def _creation_artifact_inspect(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_PROTOCOL_V4,
    ) -> dict[str, Any]:
        return self.creation_evidence.inspect(params, protocol_version=protocol_version)

    def _creation_evidence_inspect(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.creation_evidence.evidence(params)

    def _creation_preview_open(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"preview": self.creation_previews.open(params)}

    def _creation_preview_read(self, params: dict[str, Any]) -> dict[str, Any]:
        chunk = self.creation_previews.read(params["handle"], params["sequence"])
        payload = chunk.get("payload")
        if not isinstance(payload, bytes):
            raise StudioError("internal_error", "Creation preview read produced invalid bytes")
        return {
            "handle": chunk.get("handle"),
            "sequence": chunk.get("sequence"),
            "data_base64": base64.b64encode(payload).decode("ascii"),
            "byte_length": len(payload),
            "cumulative_bytes": chunk.get("cumulative_bytes"),
            "cumulative_sha256": chunk.get("cumulative_sha256"),
            "eof": chunk.get("eof"),
        }

    def _creation_preview_close(self, params: dict[str, Any]) -> dict[str, Any]:
        handle = params["handle"]
        self.creation_previews.close(handle)
        return {"handle": handle, "closed": True}

    def _creation_job_create(self, params: dict[str, Any]) -> dict[str, Any]:
        job = self.creation_jobs.create(params)
        if self.creation_scheduler is not None:
            self.creation_scheduler.notify()
        return {"job": job}

    @staticmethod
    def _max_creation_job_version(protocol_version: int) -> int:
        return 12 if protocol_version == STUDIO_PROTOCOL_V5 else 9

    @classmethod
    def _require_visible_creation_job(
        cls,
        job: dict[str, Any],
        *,
        protocol_version: int,
    ) -> dict[str, Any]:
        version = job.get("format_version")
        if type(version) is not int or version not in range(
            1,
            cls._max_creation_job_version(protocol_version) + 1,
        ):
            raise not_found("Creation job is unavailable in this protocol")
        return job

    def _dispatch_versioned_creation_job_method(
        self,
        method: str,
        params: dict[str, Any],
        *,
        protocol_version: int,
    ) -> dict[str, Any]:
        handlers = {
            "creation_job.get": self._creation_job_get,
            "creation_job.list": self._creation_job_list,
            "creation_job.cancel": self._creation_job_cancel,
            "creation_job.recover": self._creation_job_recover,
        }
        return handlers[method](params, protocol_version=protocol_version)

    def _creation_job_get(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_PROTOCOL_V4,
    ) -> dict[str, Any]:
        job = self.creation_jobs.get(params["job_id"])
        return {
            "job": self._require_visible_creation_job(
                job,
                protocol_version=protocol_version,
            )
        }

    def _creation_job_list(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_PROTOCOL_V4,
    ) -> dict[str, Any]:
        jobs, next_sequence = self.creation_jobs.list(
            workspace_id=params["workspace_id"],
            state=params["state"],
            after_sequence=params["after_sequence"],
            limit=params["limit"],
        )
        maximum = self._max_creation_job_version(protocol_version)
        jobs = [
            job
            for job in jobs
            if type(job.get("format_version")) is int
            and job["format_version"] in range(1, maximum + 1)
        ]
        return {"jobs": jobs, "next_sequence": next_sequence}

    def _creation_job_cancel(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_PROTOCOL_V4,
    ) -> dict[str, Any]:
        self._require_visible_creation_job(
            self.creation_jobs.get(params["job_id"]),
            protocol_version=protocol_version,
        )
        job = self.creation_jobs.cancel(
            params["job_id"],
            expected_generation=params["expected_generation"],
            expected_record_hash=params["expected_record_hash"],
        )
        if self.creation_scheduler is not None:
            self.creation_scheduler.notify()
        return {
            "job": self._require_visible_creation_job(
                job,
                protocol_version=protocol_version,
            )
        }

    def _creation_job_recover(
        self,
        params: dict[str, Any],
        *,
        protocol_version: int = STUDIO_PROTOCOL_V4,
    ) -> dict[str, Any]:
        self._require_visible_creation_job(
            self.creation_jobs.get(params["job_id"]),
            protocol_version=protocol_version,
        )
        job = self.creation_jobs.recover(
            params["job_id"],
            mode=params["mode"],
            expected_generation=params["expected_generation"],
            expected_record_hash=params["expected_record_hash"],
        )
        if self.creation_scheduler is not None:
            self.creation_scheduler.notify()
        return {
            "job": self._require_visible_creation_job(
                job,
                protocol_version=protocol_version,
            )
        }

    def _creation_event_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "events": self.store.list_creation_events(
                workspace_id=params["workspace_id"],
                after_id=params["after_id"],
                limit=params["limit"],
            )
        }


def _error_envelope(
    request_id: str | None,
    error: StudioError,
    *,
    protocol_version: int = STUDIO_VERSION,
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_FORMAT,
        "protocol_version": protocol_version,
        "kind": "error",
        "request_id": request_id,
        "error": {"code": error.code, "message": error.message, "details": error.details},
    }


def _write(output: BinaryIO, envelope: dict[str, Any]) -> None:
    output.write(encode_ndjson_object(envelope))
    output.flush()


def _sanitized_error(error: BaseException, fallback: str) -> StudioError:
    if isinstance(error, StudioError):
        return error
    return StudioError("internal_error", fallback)


def _close_runtime(
    service: StudioService | None,
    scheduler: JobScheduler | None,
    store: StudioStore | None,
    creation_scheduler: CreationJobScheduler | None = None,
) -> StudioError | None:
    first_error: StudioError | None = None
    stages = (
        (service, "close"),
        (creation_scheduler, "shutdown"),
        (scheduler, "shutdown"),
        (store, "close"),
    )
    for owner, method_name in stages:
        if owner is None:
            continue
        try:
            getattr(owner, method_name)()
        except BaseException as exc:
            if first_error is None:
                first_error = _sanitized_error(exc, "Studio service shutdown failed")
    return first_error


def serve(input_stream: BinaryIO, output_stream: BinaryIO, *, data_dir: str | Path) -> int:
    store: StudioStore | None = None
    scheduler: JobScheduler | None = None
    creation_scheduler: CreationJobScheduler | None = None
    service: StudioService | None = None
    try:
        store = StudioStore(data_dir)
        scheduler = JobScheduler(data_dir)
        scheduler.start()
        creation_scheduler = CreationJobScheduler(data_dir)
        creation_scheduler.start()
        service = StudioService(store, scheduler)
        service.creation_scheduler = creation_scheduler
    except BaseException as exc:
        startup_error = _sanitized_error(exc, "Studio service could not start")
        _close_runtime(service, scheduler, store, creation_scheduler)
        _write(output_stream, _error_envelope(None, startup_error))
        return 1
    assert service is not None
    shutdown_error: StudioError | None = None
    try:
        while True:
            request_id: str | None = None
            request_protocol_version = STUDIO_VERSION
            try:
                line = read_ndjson_line(input_stream)
                if line is None:
                    break
                request = decode_ndjson_object(line)
                candidate = request.get("request_id")
                request_id = candidate if isinstance(candidate, str) and candidate else None
                version = request.get("protocol_version")
                if version == STUDIO_PROTOCOL_V2:
                    request_protocol_version = STUDIO_PROTOCOL_V2
                elif version == STUDIO_PROTOCOL_V3:
                    request_protocol_version = STUDIO_PROTOCOL_V3
                elif version == STUDIO_PROTOCOL_V4:
                    request_protocol_version = STUDIO_PROTOCOL_V4
                elif version == STUDIO_PROTOCOL_V5:
                    request_protocol_version = STUDIO_PROTOCOL_V5
                response = service.handle(request)
            except StudioError as exc:
                response = _error_envelope(
                    request_id,
                    exc,
                    protocol_version=request_protocol_version,
                )
            except Exception:
                response = _error_envelope(
                    request_id,
                    StudioError("internal_error", "Internal Studio service error"),
                    protocol_version=request_protocol_version,
                )
            _write(output_stream, response)
    finally:
        shutdown_error = _close_runtime(service, scheduler, store, creation_scheduler)
    if shutdown_error is not None:
        _write(output_stream, _error_envelope(None, shutdown_error))
        return 1
    return 0
