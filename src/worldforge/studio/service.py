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
    PROTOCOL_FORMAT,
    STUDIO_VERSION,
    validate_studio_protocol_envelope,
)
from worldforge.studio.errors import (
    StudioContractError,
    StudioError,
    invalid_request,
    invalid_state,
)
from worldforge.studio.executor import JobScheduler
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
    def __init__(self, store: StudioStore, scheduler: JobScheduler | None = None) -> None:
        self.store = store
        self.scheduler = scheduler
        self._closed = False
        self._preview_shutdown = False
        self.workspaces = WorkspaceManager(store)
        self.assets = AssetCatalogManager(self.workspaces)
        preview_manager: AssetPreviewManager | None = None
        try:
            preview_manager = AssetPreviewManager(self.assets)
            self.asset_previews = preview_manager
            self.authoring = AuthoringManager(self.workspaces)
            self.changesets = ChangesetManager(store)
            self.jobs = JobManager(store)
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
            }
        except BaseException:
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
        result = self._methods[request["method"]](request["params"])
        response = {
            "protocol": PROTOCOL_FORMAT,
            "protocol_version": STUDIO_VERSION,
            "kind": "response",
            "request_id": request["request_id"],
            "method": request["method"],
            "result": result,
        }
        try:
            return validate_studio_protocol_envelope(response)
        except StudioContractError as exc:
            raise StudioError(
                "internal_error", "Studio method produced an invalid response"
            ) from exc

    def close(self) -> None:
        self._closed = True
        if self._preview_shutdown:
            return
        self.asset_previews.shutdown()
        self._preview_shutdown = True

    @staticmethod
    def _initialize(params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed=set())
        return {
            "service": "rpg-world-forge.studio",
            "service_version": 1,
            "protocol": PROTOCOL_FORMAT,
            "protocol_version": STUDIO_VERSION,
            "methods": sorted(METHODS),
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

    def _job_get(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"job_id"}, required={"job_id"})
        return {"job": self.jobs.get(params["job_id"])}

    def _job_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"workspace_id", "state", "limit"})
        return {
            "jobs": self.jobs.list(
                workspace_id=params.get("workspace_id"),
                state=params.get("state"),
                limit=params.get("limit", 100),
            )
        }

    def _job_transition(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(
            params,
            allowed={"job_id", "state", "result", "error"},
            required={"job_id", "state"},
        )
        transition = {key: value for key, value in params.items() if key != "job_id"}
        return {"job": self.jobs.transition(params["job_id"], transition)}

    def _job_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        _closed_params(params, allowed={"job_id"}, required={"job_id"})
        job = self.jobs.cancel(params["job_id"])
        if self.scheduler is not None:
            self.scheduler.notify()
        return {"job": job}


def _error_envelope(request_id: str | None, error: StudioError) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_FORMAT,
        "protocol_version": STUDIO_VERSION,
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
) -> StudioError | None:
    first_error: StudioError | None = None
    stages = (
        (service, "close"),
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
    service: StudioService | None = None
    try:
        store = StudioStore(data_dir)
        scheduler = JobScheduler(data_dir)
        scheduler.start()
        service = StudioService(store, scheduler)
    except BaseException as exc:
        startup_error = _sanitized_error(exc, "Studio service could not start")
        _close_runtime(service, scheduler, store)
        _write(output_stream, _error_envelope(None, startup_error))
        return 1
    assert service is not None
    shutdown_error: StudioError | None = None
    try:
        while True:
            request_id: str | None = None
            try:
                line = read_ndjson_line(input_stream)
                if line is None:
                    break
                request = decode_ndjson_object(line)
                candidate = request.get("request_id")
                request_id = candidate if isinstance(candidate, str) and candidate else None
                response = service.handle(request)
            except StudioError as exc:
                response = _error_envelope(request_id, exc)
            except Exception:
                response = _error_envelope(
                    request_id,
                    StudioError("internal_error", "Internal Studio service error"),
                )
            _write(output_stream, response)
    finally:
        shutdown_error = _close_runtime(service, scheduler, store)
    if shutdown_error is not None:
        _write(output_stream, _error_envelope(None, shutdown_error))
        return 1
    return 0
