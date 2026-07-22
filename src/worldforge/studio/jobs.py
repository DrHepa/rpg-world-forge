from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from worldforge.studio.contracts import JOB_STATES, validate_studio_job
from worldforge.studio.errors import (
    StudioContractError,
    StudioError,
    invalid_request,
    invalid_state,
    not_found,
)
from worldforge.studio.storage import StudioStore, decode_object, encode_json, utc_now
from worldforge.studio.workspaces import WorkspaceManager

_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "canceled"}),
    "running": frozenset(
        {
            "awaiting_approval",
            "awaiting_user",
            "paused",
            "succeeded",
            "failed",
            "canceled",
        }
    ),
    "awaiting_approval": frozenset({"queued", "running", "failed", "canceled"}),
    "awaiting_user": frozenset({"queued", "running", "failed", "canceled"}),
    "paused": frozenset({"queued", "running", "canceled"}),
    "orphaned": frozenset({"queued", "failed", "canceled"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
}


class JobManager:
    def __init__(self, store: StudioStore) -> None:
        self.store = store

    def create(self, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("job.create params must be an object")
        allowed = {"job_id", "workspace_id", "operation", "input"}
        unknown = set(params) - allowed
        missing = {"workspace_id", "operation", "input"} - set(params)
        if unknown or missing:
            detail = "unknown" if unknown else "missing"
            fields = unknown or missing
            raise invalid_request(f"job.create has {detail} fields: {', '.join(sorted(fields))}")
        workspace_id = params["workspace_id"]
        WorkspaceManager(self.store).get(workspace_id)
        timestamp = utc_now()
        record = {
            "format": "rpg-world-forge.studio_job",
            "format_version": 1,
            "job_id": params.get("job_id") or uuid.uuid4().hex,
            "workspace_id": workspace_id,
            "operation": params["operation"],
            "state": "queued",
            "input": params["input"],
            "result": None,
            "error": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        try:
            validate_studio_job(record)
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        try:
            with self.store.connection:
                self.store.connection.execute(
                    "INSERT INTO jobs (job_id, workspace_id, state, record_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        record["job_id"],
                        record["workspace_id"],
                        record["state"],
                        encode_json(record),
                    ),
                )
                self.store.record_event(
                    workspace_id=workspace_id,
                    topic="job.created",
                    entity_type="job",
                    entity_id=record["job_id"],
                    payload={"operation": record["operation"], "state": "queued"},
                    created_at=timestamp,
                )
        except sqlite3.IntegrityError as exc:
            raise invalid_request(f"Job {record['job_id']} already exists") from exc
        return record

    def get(self, job_id: object) -> dict[str, Any]:
        if not isinstance(job_id, str):
            raise invalid_request("job_id must be a string")
        row = self.store.connection.execute(
            "SELECT record_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise not_found(f"Job {job_id} was not found")
        record = decode_object(row["record_json"], context="job")
        try:
            return validate_studio_job(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored job is invalid") from exc

    def list(
        self,
        *,
        workspace_id: object = None,
        state: object = None,
        limit: object = 100,
    ) -> list[dict[str, Any]]:
        if workspace_id is not None:
            WorkspaceManager(self.store).get(workspace_id)
        if state is not None and (not isinstance(state, str) or state not in JOB_STATES):
            raise invalid_request("job state filter is unknown")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise invalid_request("job list limit must be an integer from 1 to 1000")
        clauses: list[str] = []
        values: list[object] = []
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            values.append(workspace_id)
        if state is not None:
            clauses.append("state = ?")
            values.append(state)
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        rows = self.store.connection.execute(
            f"SELECT record_json FROM jobs{where} ORDER BY job_id LIMIT ?",  # noqa: S608
            (*values, limit),
        ).fetchall()
        return [self._validated_row(row) for row in rows]

    def transition(self, job_id: object, params: object) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("job.transition params must be an object")
        allowed = {"state", "result", "error"}
        unknown = set(params) - allowed
        missing = {"state"} - set(params)
        if unknown or missing:
            fields = unknown or missing
            raise invalid_request(f"job.transition has invalid fields: {', '.join(sorted(fields))}")
        record = self.get(job_id)
        next_state = params["state"]
        if (
            not isinstance(next_state, str)
            or next_state not in JOB_STATES
            or next_state == "orphaned"
        ):
            raise invalid_request("Requested job state is unknown or service-reserved")
        current = record["state"]
        if next_state not in _TRANSITIONS[current]:
            raise invalid_state(f"Job transition {current} -> {next_state} is not allowed")
        result = params.get("result")
        error = params.get("error")
        if next_state == "succeeded":
            if result is None or error is not None:
                raise invalid_request("A succeeded job requires result and forbids error")
        elif next_state == "failed":
            if error is None or result is not None:
                raise invalid_request("A failed job requires error and forbids result")
        elif result is not None or error is not None:
            raise invalid_request("Only terminal succeeded/failed jobs may carry result or error")
        updated = {
            **record,
            "state": next_state,
            "result": result,
            "error": error,
            "updated_at": utc_now(),
        }
        try:
            validate_studio_job(updated)
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        with self.store.connection:
            cursor = self.store.connection.execute(
                "UPDATE jobs SET state = ?, record_json = ? WHERE job_id = ? AND state = ?",
                (next_state, encode_json(updated), job_id, current),
            )
            if cursor.rowcount != 1:
                raise StudioError("conflict", "Job state changed concurrently")
            self.store.record_event(
                workspace_id=record["workspace_id"],
                topic="job.transitioned",
                entity_type="job",
                entity_id=record["job_id"],
                payload={"previous_state": current, "state": next_state},
                created_at=updated["updated_at"],
            )
        return updated

    def cancel(self, job_id: object) -> dict[str, Any]:
        record = self.get(job_id)
        if record["state"] == "canceled":
            return record
        return self.transition(job_id, {"state": "canceled"})

    @staticmethod
    def _validated_row(row: sqlite3.Row) -> dict[str, Any]:
        record = decode_object(row["record_json"], context="job")
        try:
            return validate_studio_job(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored job is invalid") from exc
