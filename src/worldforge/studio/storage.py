from __future__ import annotations

import json
import math
import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from isoworld.content.file_stat import FileStat, path_file_stat
from worldforge.studio.contracts import validate_studio_job
from worldforge.studio.errors import StudioContractError, StudioError

SCHEMA_VERSION = 1
DATABASE_NAME = "studio.sqlite3"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def encode_json(value: object) -> str:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def decode_object(value: str, *, context: str) -> dict[str, Any]:
    try:
        decoded = json.loads(
            value,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise StudioError("internal_error", f"Stored {context} is invalid") from exc
    if not isinstance(decoded, dict):
        raise StudioError("internal_error", f"Stored {context} is not an object")
    return decoded


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(Path(path)))


def _is_link_or_reparse(info: FileStat) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _ensure_safe_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StudioError(
            "internal_error", f"Could not create Studio data directory: {exc}"
        ) from exc
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = path_file_stat(current)
        except OSError as exc:
            raise StudioError(
                "internal_error", f"Could not inspect Studio data directory: {exc}"
            ) from exc
        if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise StudioError(
                "invalid_request", f"Studio data path is not a safe directory: {current}"
            )


def _safe_database_file(path: Path) -> tuple[int, int] | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StudioError("internal_error", f"Could not inspect Studio database: {exc}") from exc
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StudioError("invalid_request", "Studio database must be a standalone regular file")
    return info.st_dev, info.st_ino


class StudioStore:
    """Durable Studio registry. Repository contents never enter this database."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = _absolute(data_dir)
        _ensure_safe_directory(self.data_dir)
        self.blobs_dir = self.data_dir / "blobs/sha256"
        self.journals_dir = self.data_dir / "journals"
        _ensure_safe_directory(self.blobs_dir)
        _ensure_safe_directory(self.journals_dir)
        self.database_path = self.data_dir / DATABASE_NAME
        before = _safe_database_file(self.database_path)
        try:
            self.connection = sqlite3.connect(self.database_path, timeout=5.0)
        except sqlite3.Error as exc:
            raise StudioError("internal_error", f"Could not open Studio database: {exc}") from exc
        self.connection.row_factory = sqlite3.Row
        after = _safe_database_file(self.database_path)
        if after is None or (before is not None and before != after):
            self.connection.close()
            raise StudioError("conflict", "Studio database identity changed while opening")
        try:
            self._configure()
            self._migrate()
            self._orphan_running_jobs()
        except Exception:
            self.connection.close()
            raise

    def __enter__(self) -> StudioStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def blob_path(self, digest: str) -> Path:
        return self.blobs_dir / digest[:2] / digest

    def _configure(self) -> None:
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            mode = self.connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            self.connection.execute("PRAGMA synchronous = FULL")
        except sqlite3.Error as exc:
            raise StudioError(
                "internal_error", f"Could not configure Studio database: {exc}"
            ) from exc
        if str(mode).casefold() != "wal":
            raise StudioError("internal_error", "Studio database could not enable WAL mode")

    def _migrate(self) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_meta ("
                    "key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)"
                )
                row = self.connection.execute(
                    "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                ).fetchone()
                version = 0 if row is None else int(row["value"])
                if version > SCHEMA_VERSION:
                    raise StudioError(
                        "invalid_state",
                        f"Studio database uses newer schema version {version}",
                    )
                if version < 1:
                    self._create_v1_schema()
                    self.connection.execute(
                        "INSERT OR REPLACE INTO schema_meta (key, value) "
                        "VALUES ('schema_version', ?)",
                        (str(SCHEMA_VERSION),),
                    )
        except StudioError:
            raise
        except (sqlite3.Error, ValueError) as exc:
            raise StudioError(
                "internal_error", f"Could not migrate Studio database: {exc}"
            ) from exc

    def _create_v1_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                workspace_id TEXT PRIMARY KEY NOT NULL,
                record_json TEXT NOT NULL,
                forge_dev TEXT NOT NULL,
                forge_ino TEXT NOT NULL,
                world_dev TEXT NOT NULL,
                world_ino TEXT NOT NULL,
                game_dev TEXT,
                game_ino TEXT,
                bundle_dev TEXT,
                bundle_ino TEXT
            );
            CREATE TABLE IF NOT EXISTS changesets (
                changeset_id TEXT PRIMARY KEY NOT NULL,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                status TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS changesets_workspace_idx
                ON changesets(workspace_id, changeset_id);
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY NOT NULL,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                state TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_workspace_idx ON jobs(workspace_id, job_id);
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id TEXT REFERENCES workspaces(workspace_id),
                topic TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_workspace_idx ON events(workspace_id, event_id);
            """
        )

    def record_event(
        self,
        *,
        workspace_id: str | None,
        topic: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> int:
        timestamp = created_at or utc_now()
        cursor = self.connection.execute(
            "INSERT INTO events "
            "(workspace_id, topic, entity_type, entity_id, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (workspace_id, topic, entity_type, entity_id, encode_json(payload or {}), timestamp),
        )
        return int(cursor.lastrowid)

    def list_events(
        self,
        *,
        workspace_id: str | None = None,
        after_id: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if workspace_id is None:
            rows = self.connection.execute(
                "SELECT * FROM events WHERE event_id > ? ORDER BY event_id LIMIT ?",
                (after_id, limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM events WHERE workspace_id = ? AND event_id > ? "
                "ORDER BY event_id LIMIT ?",
                (workspace_id, after_id, limit),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "workspace_id": row["workspace_id"],
                "topic": row["topic"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "payload": decode_object(row["payload_json"], context="event payload"),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def _orphan_running_jobs(self) -> None:
        rows = self.connection.execute(
            "SELECT job_id, workspace_id, record_json FROM jobs WHERE state = 'running'"
        ).fetchall()
        if not rows:
            return
        timestamp = utc_now()
        try:
            with self.connection:
                for row in rows:
                    record = decode_object(row["record_json"], context="job")
                    record["state"] = "orphaned"
                    record["updated_at"] = timestamp
                    try:
                        validate_studio_job(record)
                    except StudioContractError as exc:
                        raise StudioError(
                            "internal_error", "Stored running job is invalid"
                        ) from exc
                    self.connection.execute(
                        "UPDATE jobs SET state = 'orphaned', record_json = ? WHERE job_id = ?",
                        (encode_json(record), row["job_id"]),
                    )
                    self.record_event(
                        workspace_id=row["workspace_id"],
                        topic="job.orphaned",
                        entity_type="job",
                        entity_id=row["job_id"],
                        payload={"previous_state": "running", "reason": "service_restart"},
                        created_at=timestamp,
                    )
        except sqlite3.Error as exc:
            raise StudioError(
                "internal_error", f"Could not recover running Studio jobs: {exc}"
            ) from exc
