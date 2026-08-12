from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from worldforge.studio.contracts import (
    EXTERNAL_JOB_OPERATIONS,
    EXTERNAL_JOB_VERSION,
    JOB_STATES,
    MANAGED_JOB_OPERATIONS,
    MANAGED_JOB_VERSION,
    validate_job_create_params,
    validate_studio_job,
)
from worldforge.studio.errors import (
    StudioContractError,
    StudioError,
    invalid_request,
    invalid_state,
    not_found,
)
from worldforge.studio.external_grants import ExternalGrantManager
from worldforge.studio.external_jobs import (
    ExternalJobExecutionError,
    execute_external_operation,
    rollback_external_operation,
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
        try:
            validated = validate_job_create_params(params)
        except StudioContractError as exc:
            raise invalid_request(str(exc)) from exc
        params = validated
        workspace_id = params["workspace_id"]
        WorkspaceManager(self.store).get(workspace_id)
        external = params["operation"] in EXTERNAL_JOB_OPERATIONS
        timestamp = utc_now()
        record = {
            "format": "rpg-world-forge.studio_job",
            "format_version": EXTERNAL_JOB_VERSION if external else MANAGED_JOB_VERSION,
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
            self.store.connection.execute("BEGIN IMMEDIATE")
            if external:
                ExternalGrantManager(self.store).reserve_for_job(
                    job_id=record["job_id"],
                    workspace_id=workspace_id,
                    operation=record["operation"],
                    job_input=record["input"],
                )
            self.store.connection.execute(
                "INSERT INTO jobs (job_id, workspace_id, state, record_json) VALUES (?, ?, ?, ?)",
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
            self.store.connection.commit()
        except sqlite3.IntegrityError as exc:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            raise invalid_request(f"Job {record['job_id']} already exists") from exc
        except Exception:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            raise
        return record

    def get(
        self,
        job_id: object,
        *,
        format_versions: frozenset[int] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(job_id, str):
            raise invalid_request("job_id must be a string")
        row = self.store.connection.execute(
            "SELECT record_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise not_found(f"Job {job_id} was not found")
        record = self._validated_row(row)
        self._require_format_version(record, format_versions)
        return record

    def list(
        self,
        *,
        workspace_id: object = None,
        state: object = None,
        limit: object = 100,
        format_versions: frozenset[int] | None = None,
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
            f"SELECT record_json FROM jobs{where} ORDER BY job_id",  # noqa: S608
            values,
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            record = self._validated_row(row)
            if format_versions is not None and record["format_version"] not in format_versions:
                continue
            result.append(record)
            if len(result) == limit:
                break
        return result

    def transition(
        self,
        job_id: object,
        params: object,
        *,
        format_versions: frozenset[int] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise invalid_request("job.transition params must be an object")
        allowed = {"state", "result", "error"}
        unknown = set(params) - allowed
        missing = {"state"} - set(params)
        if unknown or missing:
            fields = unknown or missing
            raise invalid_request(f"job.transition has invalid fields: {', '.join(sorted(fields))}")
        record = self.get(job_id, format_versions=format_versions)
        next_state = params["state"]
        if (
            not isinstance(next_state, str)
            or next_state not in JOB_STATES
            or next_state == "orphaned"
        ):
            raise invalid_request("Requested job state is unknown or service-reserved")
        if self._is_managed(record):
            raise invalid_state("Executable job transitions are owned by the Studio executor")
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

    def cancel(
        self,
        job_id: object,
        *,
        format_versions: frozenset[int] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(job_id, str):
            raise invalid_request("job_id must be a string")
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            row = self.store.connection.execute(
                "SELECT record_json FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise not_found(f"Job {job_id} was not found")
            record = self._validated_row(row)
            self._require_format_version(record, format_versions)
            current = record["state"]
            if current in {"succeeded", "failed", "canceled"}:
                self.store.connection.commit()
                return record
            if record["format_version"] == EXTERNAL_JOB_VERSION and current == "orphaned":
                raise invalid_state("Orphaned external jobs require explicit recovery")
            timestamp = utc_now()
            already_requested = self.store.connection.execute(
                "SELECT 1 FROM events WHERE entity_type = 'job' AND entity_id = ? "
                "AND topic = 'job.cancel_requested' LIMIT 1",
                (job_id,),
            ).fetchone()
            if already_requested is None:
                self.store.record_event(
                    workspace_id=record["workspace_id"],
                    topic="job.cancel_requested",
                    entity_type="job",
                    entity_id=job_id,
                    payload={"state": current},
                    created_at=timestamp,
                )
            if current == "running" and self._is_managed(record):
                self.store.connection.commit()
                return record
            canceled = {
                **record,
                "state": "canceled",
                "result": None,
                "error": None,
                "updated_at": timestamp,
            }
            validate_studio_job(canceled)
            cursor = self.store.connection.execute(
                "UPDATE jobs SET state = 'canceled', record_json = ? "
                "WHERE job_id = ? AND state = ?",
                (encode_json(canceled), job_id, current),
            )
            if cursor.rowcount != 1:
                raise StudioError("conflict", "Job state changed concurrently")
            if record["format_version"] == EXTERNAL_JOB_VERSION:
                ExternalGrantManager(self.store).release_target_for_job(job_id)
            self.store.record_event(
                workspace_id=record["workspace_id"],
                topic="job.transitioned",
                entity_type="job",
                entity_id=job_id,
                payload={"previous_state": current, "state": "canceled"},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return canceled
        except Exception:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            raise

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically claim the oldest queued job if no executor owns a running job."""

        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            running_rows = self.store.connection.execute(
                "SELECT record_json FROM jobs WHERE state = 'running' ORDER BY rowid"
            )
            for running_row in running_rows:
                if self._is_managed(self._validated_row(running_row)):
                    self.store.connection.commit()
                    return None
            queued_rows = self.store.connection.execute(
                "SELECT record_json FROM jobs WHERE state = 'queued' ORDER BY rowid"
            )
            record = None
            for queued_row in queued_rows:
                candidate = self._validated_row(queued_row)
                if self._is_managed(candidate):
                    record = candidate
                    break
            if record is None:
                self.store.connection.commit()
                return None
            timestamp = utc_now()
            running_record = {**record, "state": "running", "updated_at": timestamp}
            validate_studio_job(running_record)
            cursor = self.store.connection.execute(
                "UPDATE jobs SET state = 'running', record_json = ? "
                "WHERE job_id = ? AND state = 'queued'",
                (encode_json(running_record), record["job_id"]),
            )
            if cursor.rowcount != 1:
                self.store.connection.rollback()
                return None
            self.store.record_event(
                workspace_id=record["workspace_id"],
                topic="job.transitioned",
                entity_type="job",
                entity_id=record["job_id"],
                payload={"previous_state": "queued", "state": "running"},
                created_at=timestamp,
            )
            self.store.record_event(
                workspace_id=record["workspace_id"],
                topic="job.progress",
                entity_type="job",
                entity_id=record["job_id"],
                payload={"progress": 0, "stage": "claimed"},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return running_record
        except Exception:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            raise

    def cancellation_requested(self, job_id: str) -> bool:
        return (
            self.store.connection.execute(
                "SELECT 1 FROM events WHERE entity_type = 'job' AND entity_id = ? "
                "AND topic = 'job.cancel_requested' LIMIT 1",
                (job_id,),
            ).fetchone()
            is not None
        )

    def progress(self, job_id: str, value: int, stage: str) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ValueError("job progress must be an integer from 0 to 100")
        if not isinstance(stage, str) or not stage or len(stage) > 64:
            raise ValueError("job progress stage must be a short non-empty string")
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            row = self.store.connection.execute(
                "SELECT record_json FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise not_found(f"Job {job_id} was not found")
            record = self._validated_row(row)
            if not self._is_managed(record):
                raise invalid_state("Progress is owned by the managed v2 Studio executor")
            if record["state"] != "running":
                raise invalid_state("Progress may be recorded only for a running job")
            prior_rows = self.store.connection.execute(
                "SELECT payload_json FROM events WHERE entity_type = 'job' AND entity_id = ? "
                "AND topic = 'job.progress' ORDER BY event_id DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if prior_rows is not None:
                prior = decode_object(prior_rows["payload_json"], context="job progress")
                prior_value = prior.get("progress")
                if isinstance(prior_value, bool) or not isinstance(prior_value, int):
                    raise StudioError("internal_error", "Stored job progress is invalid")
                if value <= prior_value:
                    raise invalid_state("Job progress must increase monotonically")
            self.store.record_event(
                workspace_id=record["workspace_id"],
                topic="job.progress",
                entity_type="job",
                entity_id=job_id,
                payload={"progress": value, "stage": stage},
            )
            self.store.connection.commit()
        except Exception:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            raise

    def finish(
        self,
        job_id: str,
        state: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if state not in {"succeeded", "failed", "canceled", "orphaned"}:
            raise ValueError("executor terminal state is invalid")
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            row = self.store.connection.execute(
                "SELECT record_json FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise not_found(f"Job {job_id} was not found")
            record = self._validated_row(row)
            if not self._is_managed(record):
                raise invalid_state("Only a managed v2 job may be completed by the executor")
            if record["state"] != "running":
                raise invalid_state("Only a running job may be completed by the executor")
            timestamp = utc_now()
            if (
                record["format_version"] == EXTERNAL_JOB_VERSION
                and state == "orphaned"
                and error is None
            ):
                error = {
                    "code": "recovery_required",
                    "message": "External job requires explicit retained-evidence recovery",
                }
            updated = {
                **record,
                "state": state,
                "result": result,
                "error": error,
                "updated_at": timestamp,
            }
            validate_studio_job(updated)
            cursor = self.store.connection.execute(
                "UPDATE jobs SET state = ?, record_json = ? WHERE job_id = ? AND state = 'running'",
                (state, encode_json(updated), job_id),
            )
            if cursor.rowcount != 1:
                raise StudioError("conflict", "Job state changed concurrently")
            if record["format_version"] == EXTERNAL_JOB_VERSION:
                grants = ExternalGrantManager(self.store)
                if state == "succeeded":
                    grants.consume_source_for_job(record)
                    grants.set_target_state_for_job(job_id, "consumed")
                elif state == "orphaned":
                    grants.set_target_state_for_job(job_id, "recovery_required")
                else:
                    grants.release_target_for_job(job_id)
            payload: dict[str, Any] = {"previous_state": "running", "state": state}
            if reason is not None:
                payload["reason"] = reason
            topic = "job.orphaned" if state == "orphaned" else "job.transitioned"
            self.store.record_event(
                workspace_id=record["workspace_id"],
                topic=topic,
                entity_type="job",
                entity_id=job_id,
                payload=payload,
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except Exception:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            raise

    def recover(self, job_id: object, action: object) -> dict[str, Any]:
        if not isinstance(job_id, str):
            raise invalid_request("job_id must be a string")
        if action not in {"resume", "rollback"}:
            raise invalid_request("job recovery action must be resume or rollback")
        try:
            self.store.connection.execute("BEGIN IMMEDIATE")
            row = self.store.connection.execute(
                "SELECT record_json FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise not_found(f"Job {job_id} was not found")
            record = self._validated_row(row)
            if record["format_version"] != EXTERNAL_JOB_VERSION:
                raise invalid_state("Only an external v3 job supports recovery")
            if record["state"] != "orphaned":
                raise invalid_state("Only an orphaned external job supports recovery")
            grants = ExternalGrantManager(self.store)
            binding = grants.binding_for_job(record)
            if action == "resume":
                hash_field = {
                    "game.materialize": "expected_materialization_hash",
                    "game.package": "expected_game_hash",
                    "game.package.extract": "expected_package_hash",
                }[record["operation"]]
                result = execute_external_operation(
                    operation=record["operation"],
                    source=binding["source_path"],
                    target=binding["target_path"],
                    expected_hash=record["input"][hash_field],
                    target_grant_id=record["input"]["target_grant_id"],
                    expected_source_identity=binding["source_identity"],
                    expected_parent_identity=binding["parent_identity"],
                )
                state = "succeeded"
                grants.consume_source_for_job(record)
                grants.set_target_state_for_job(job_id, "consumed")
            else:
                rollback_external_operation(
                    operation=record["operation"],
                    target=binding["target_path"],
                    expected_parent_identity=binding["parent_identity"],
                )
                result = None
                state = "canceled"
                grants.release_target_for_job(job_id)
            timestamp = utc_now()
            updated = {
                **record,
                "state": state,
                "result": result,
                "error": None,
                "updated_at": timestamp,
            }
            validate_studio_job(updated)
            cursor = self.store.connection.execute(
                "UPDATE jobs SET state = ?, record_json = ? "
                "WHERE job_id = ? AND state = 'orphaned'",
                (state, encode_json(updated), job_id),
            )
            if cursor.rowcount != 1:
                raise StudioError("conflict", "Job state changed concurrently")
            self.store.record_event(
                workspace_id=record["workspace_id"],
                topic="job.recovered",
                entity_type="job",
                entity_id=job_id,
                payload={"action": action, "state": state},
                created_at=timestamp,
            )
            self.store.connection.commit()
            return updated
        except ExternalJobExecutionError as exc:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            code = "recovery_failed"
            if exc.code in {"recovery_ambiguous", "source_changed", "target_changed"}:
                code = "recovery_ambiguous"
            raise StudioError(
                code,
                "External job recovery could not prove safe ownership",
                details={
                    "reason_code": exc.code,
                    "recovery_evidence": exc.recovery_evidence,
                },
            ) from None
        except Exception:
            if self.store.connection.in_transaction:
                self.store.connection.rollback()
            raise

    def resolve_forced_cancel(self, job_id: str) -> dict[str, Any]:
        record = self.get(job_id)
        if record["format_version"] != EXTERNAL_JOB_VERSION:
            return self.finish(job_id, "canceled")
        if record["state"] != "running":
            raise invalid_state("Forced cancellation requires a running external job")
        grants = ExternalGrantManager(self.store)
        try:
            binding = grants.binding_for_job(record)
            hash_field = {
                "game.materialize": "expected_materialization_hash",
                "game.package": "expected_game_hash",
                "game.package.extract": "expected_package_hash",
            }[record["operation"]]
            if binding["target_path"].exists() or binding["target_path"].is_symlink():
                result = execute_external_operation(
                    operation=record["operation"],
                    source=binding["source_path"],
                    target=binding["target_path"],
                    expected_hash=record["input"][hash_field],
                    target_grant_id=record["input"]["target_grant_id"],
                    expected_source_identity=binding["source_identity"],
                    expected_parent_identity=binding["parent_identity"],
                )
                return self.finish(job_id, "succeeded", result=result)
            rollback_external_operation(
                operation=record["operation"],
                target=binding["target_path"],
                expected_parent_identity=binding["parent_identity"],
            )
            return self.finish(job_id, "canceled")
        except ExternalJobExecutionError as exc:
            error: dict[str, Any] = {
                "code": "recovery_required",
                "message": exc.message,
            }
            if exc.recovery_evidence:
                error["recovery_evidence"] = exc.recovery_evidence
            return self.finish(
                job_id,
                "orphaned",
                error=error,
                reason="cancel_recovery_required",
            )
        except StudioError:
            return self.finish(job_id, "orphaned", reason="cancel_recovery_required")

    @staticmethod
    def _is_managed(record: dict[str, Any]) -> bool:
        return (
            record["format_version"] == MANAGED_JOB_VERSION
            and record["operation"] in MANAGED_JOB_OPERATIONS
        ) or (
            record["format_version"] == EXTERNAL_JOB_VERSION
            and record["operation"] in EXTERNAL_JOB_OPERATIONS
        )

    @staticmethod
    def _validated_row(row: sqlite3.Row) -> dict[str, Any]:
        record = decode_object(row["record_json"], context="job")
        try:
            return validate_studio_job(record)
        except StudioContractError as exc:
            raise StudioError("internal_error", "Stored job is invalid") from exc

    @staticmethod
    def _require_format_version(
        record: dict[str, Any],
        format_versions: frozenset[int] | None,
    ) -> None:
        if format_versions is not None and record["format_version"] not in format_versions:
            raise not_found(f"Job {record['job_id']} was not found")
