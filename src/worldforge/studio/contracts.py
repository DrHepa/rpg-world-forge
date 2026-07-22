from __future__ import annotations

import math
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any

from isoworld.content.portability import portable_relative_path
from worldforge.studio.errors import ERROR_CODES, StudioContractError

WORKSPACE_FORMAT = "rpg-world-forge.forge_workspace"
CHANGESET_FORMAT = "rpg-world-forge.studio_changeset"
JOB_FORMAT = "rpg-world-forge.studio_job"
PROTOCOL_FORMAT = "rpg-world-forge.studio_protocol"
STUDIO_VERSION = 1
MAX_CHANGE_FILE_BYTES = 16 * 1024 * 1024
MAX_CHANGESET_OPERATIONS = 256
PORTABLE_SOURCE_PATH_FORMAT = "rpg-world-forge-portable-source-path"

WORKSPACE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)

CHANGESET_STATES = frozenset({"staged", "approved", "rejected", "applied"})
JOB_STATES = frozenset(
    {
        "queued",
        "running",
        "awaiting_approval",
        "awaiting_user",
        "paused",
        "succeeded",
        "failed",
        "canceled",
        "orphaned",
    }
)
METHODS = frozenset(
    {
        "service.initialize",
        "workspace.register",
        "workspace.list",
        "workspace.get",
        "events.list",
        "changeset.create",
        "changeset.get",
        "changeset.list",
        "changeset.approve",
        "changeset.reject",
        "changeset.apply",
        "job.create",
        "job.get",
        "job.list",
        "job.transition",
        "job.cancel",
    }
)


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StudioContractError(f"{context} must be an object")
    return value


def _closed(value: dict[str, Any], required: set[str], context: str) -> None:
    missing = required - set(value)
    unknown = set(value) - required
    if missing:
        raise StudioContractError(f"{context} is missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise StudioContractError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )


def _string(value: object, context: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise StudioContractError(f"{context} must be a non-empty string")
    return value


def _identifier(value: object, context: str, pattern: re.Pattern[str]) -> str:
    text = _string(value, context)
    assert text is not None
    if pattern.fullmatch(text) is None:
        raise StudioContractError(f"{context} is not a valid identifier")
    return text


def _timestamp(value: object, context: str) -> str:
    text = _string(value, context)
    assert text is not None
    if TIMESTAMP_PATTERN.fullmatch(text) is None:
        raise StudioContractError(f"{context} must be a UTC RFC 3339 timestamp")
    return text


def _sha256(value: object, context: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise StudioContractError(f"{context} must be a lowercase SHA-256 digest")
    return value


def _strict_json_value(value: object, context: str) -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StudioContractError(f"{context} cannot contain non-finite numbers")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _strict_json_value(item, f"{context}/{index}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise StudioContractError(f"{context} object keys must be strings")
            _strict_json_value(item, f"{context}/{key}")
        return
    raise StudioContractError(f"{context} contains a non-JSON value")


def studio_source_path(value: object) -> PurePosixPath | None:
    """Return a canonical portable path rooted below ``source/``."""

    try:
        relative = portable_relative_path(value)
    except UnicodeError:
        return None
    if relative is None or len(relative.parts) < 2 or relative.parts[0] != "source":
        return None
    return relative


def validate_forge_workspace(value: object) -> dict[str, Any]:
    workspace = _object(value, "workspace")
    required = {
        "format",
        "format_version",
        "workspace_id",
        "forge_root",
        "world_root",
        "game_root",
        "bundle_root",
        "created_at",
    }
    _closed(workspace, required, "workspace")
    if workspace["format"] != WORKSPACE_FORMAT:
        raise StudioContractError("workspace format is unsupported")
    if isinstance(workspace["format_version"], bool) or workspace["format_version"] != 1:
        raise StudioContractError("workspace format_version must be 1")
    _identifier(workspace["workspace_id"], "workspace/workspace_id", WORKSPACE_ID_PATTERN)
    _string(workspace["forge_root"], "workspace/forge_root")
    _string(workspace["world_root"], "workspace/world_root")
    _string(workspace["game_root"], "workspace/game_root", nullable=True)
    _string(workspace["bundle_root"], "workspace/bundle_root", nullable=True)
    _timestamp(workspace["created_at"], "workspace/created_at")
    return workspace


def validate_studio_changeset(value: object) -> dict[str, Any]:
    changeset = _object(value, "changeset")
    required = {
        "format",
        "format_version",
        "changeset_id",
        "workspace_id",
        "status",
        "operations",
        "created_at",
        "updated_at",
    }
    _closed(changeset, required, "changeset")
    if changeset["format"] != CHANGESET_FORMAT:
        raise StudioContractError("changeset format is unsupported")
    if isinstance(changeset["format_version"], bool) or changeset["format_version"] != 1:
        raise StudioContractError("changeset format_version must be 1")
    _identifier(changeset["changeset_id"], "changeset/changeset_id", ENTITY_ID_PATTERN)
    _identifier(changeset["workspace_id"], "changeset/workspace_id", WORKSPACE_ID_PATTERN)
    if not isinstance(changeset["status"], str) or changeset["status"] not in CHANGESET_STATES:
        raise StudioContractError("changeset/status is unknown")
    operations = changeset["operations"]
    if (
        not isinstance(operations, list)
        or not operations
        or len(operations) > MAX_CHANGESET_OPERATIONS
    ):
        raise StudioContractError(
            f"changeset/operations must contain 1 to {MAX_CHANGESET_OPERATIONS} entries"
        )
    path_keys: set[tuple[str, ...]] = set()
    for index, item in enumerate(operations):
        operation = _object(item, f"changeset/operations/{index}")
        _closed(
            operation,
            {"path", "operation", "base_sha256", "proposed_sha256", "size"},
            f"changeset/operations/{index}",
        )
        path_value = _string(operation["path"], f"changeset/operations/{index}/path")
        relative = studio_source_path(path_value)
        if relative is None:
            raise StudioContractError(
                f"changeset/operations/{index}/path must be portable and beneath source/"
            )
        path_key = tuple(unicodedata.normalize("NFC", part).casefold() for part in relative.parts)
        if path_key in path_keys:
            raise StudioContractError("changeset/operations contain an NFC/casefold collision")
        path_keys.add(path_key)
        kind = operation["operation"]
        if not isinstance(kind, str) or kind not in {"create", "replace", "delete"}:
            raise StudioContractError(f"changeset/operations/{index}/operation is unknown")
        base = operation["base_sha256"]
        proposed = operation["proposed_sha256"]
        if kind == "create":
            if base is not None:
                raise StudioContractError(
                    f"changeset/operations/{index}/base_sha256 must be null for create"
                )
            _sha256(proposed, f"changeset/operations/{index}/proposed_sha256")
        elif kind == "replace":
            _sha256(base, f"changeset/operations/{index}/base_sha256")
            _sha256(proposed, f"changeset/operations/{index}/proposed_sha256")
        else:
            _sha256(base, f"changeset/operations/{index}/base_sha256")
            if proposed is not None:
                raise StudioContractError(
                    f"changeset/operations/{index}/proposed_sha256 must be null for delete"
                )
        size = operation["size"]
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 <= size <= MAX_CHANGE_FILE_BYTES
        ):
            raise StudioContractError(
                f"changeset/operations/{index}/size must be from 0 to {MAX_CHANGE_FILE_BYTES}"
            )
        if kind == "delete" and size != 0:
            raise StudioContractError(f"changeset/operations/{index}/size must be zero for delete")
    _timestamp(changeset["created_at"], "changeset/created_at")
    _timestamp(changeset["updated_at"], "changeset/updated_at")
    return changeset


def validate_studio_job(value: object) -> dict[str, Any]:
    job = _object(value, "job")
    required = {
        "format",
        "format_version",
        "job_id",
        "workspace_id",
        "operation",
        "state",
        "input",
        "result",
        "error",
        "created_at",
        "updated_at",
    }
    _closed(job, required, "job")
    if job["format"] != JOB_FORMAT:
        raise StudioContractError("job format is unsupported")
    if isinstance(job["format_version"], bool) or job["format_version"] != 1:
        raise StudioContractError("job format_version must be 1")
    _identifier(job["job_id"], "job/job_id", ENTITY_ID_PATTERN)
    _identifier(job["workspace_id"], "job/workspace_id", WORKSPACE_ID_PATTERN)
    _identifier(job["operation"], "job/operation", OPERATION_PATTERN)
    if not isinstance(job["state"], str) or job["state"] not in JOB_STATES:
        raise StudioContractError("job/state is unknown")
    for field in ("input", "result", "error"):
        item = job[field]
        if field != "input" and item is None:
            continue
        _object(item, f"job/{field}")
        _strict_json_value(item, f"job/{field}")
    _timestamp(job["created_at"], "job/created_at")
    _timestamp(job["updated_at"], "job/updated_at")
    return job


def validate_studio_protocol_envelope(value: object) -> dict[str, Any]:
    envelope = _object(value, "envelope")
    common = {"protocol", "protocol_version", "kind", "request_id"}
    kind = envelope.get("kind")
    additions = {
        "request": {"method", "params"},
        "response": {"result"},
        "error": {"error"},
        "event": {"event"},
    }
    if not isinstance(kind, str) or kind not in additions:
        raise StudioContractError("envelope/kind is unknown")
    _closed(envelope, common | additions[kind], "envelope")
    if envelope["protocol"] != PROTOCOL_FORMAT:
        raise StudioContractError("envelope/protocol is unsupported")
    if isinstance(envelope["protocol_version"], bool) or envelope["protocol_version"] != 1:
        raise StudioContractError("envelope/protocol_version must be 1")
    request_id = envelope["request_id"]
    if kind == "event":
        if request_id is not None:
            raise StudioContractError("event request_id must be null")
    elif kind == "error" and request_id is None:
        pass
    else:
        _string(request_id, "envelope/request_id")
    if kind == "request":
        if not isinstance(envelope["method"], str) or envelope["method"] not in METHODS:
            raise StudioContractError("envelope/method is unknown")
        _object(envelope["params"], "envelope/params")
        _strict_json_value(envelope["params"], "envelope/params")
    elif kind == "response":
        _object(envelope["result"], "envelope/result")
        _strict_json_value(envelope["result"], "envelope/result")
    elif kind == "error":
        error = _object(envelope["error"], "envelope/error")
        _closed(error, {"code", "message", "details"}, "envelope/error")
        if not isinstance(error["code"], str) or error["code"] not in ERROR_CODES:
            raise StudioContractError("envelope/error/code is unknown")
        _string(error["message"], "envelope/error/message")
        _object(error["details"], "envelope/error/details")
        _strict_json_value(error["details"], "envelope/error/details")
    else:
        event = _object(envelope["event"], "envelope/event")
        _strict_json_value(event, "envelope/event")
    return envelope
