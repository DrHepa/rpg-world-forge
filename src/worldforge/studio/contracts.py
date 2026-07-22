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
OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
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
        "workspace.overview",
        "source.list",
        "source.read",
        "world.validate",
        "world.analyze",
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
WORKSPACE_AUTHORING_METHODS = frozenset(
    {"workspace.overview", "source.list", "world.validate", "world.analyze"}
)
AUTHORING_METHODS = WORKSPACE_AUTHORING_METHODS | {"source.read"}
EXACT_JOB_METHODS = frozenset({"job.create", "job.cancel"})
LEGACY_METHODS = METHODS - AUTHORING_METHODS - EXACT_JOB_METHODS
MAX_STUDIO_SOURCE_DEPTH = 8
MAX_STUDIO_SOURCE_BYTES = 256 * 1024
MAX_STUDIO_SOURCE_DOCUMENTS = 1024
MAX_STUDIO_DIAGNOSTICS = 512
MAX_STUDIO_JOB_PATH_DEPTH = 16
MAX_STUDIO_RECEIPT_ISSUES = 256
MAX_RUNTIME_TICKS = 1_000_000
LEGACY_JOB_VERSION = 1
MANAGED_JOB_VERSION = 2
MANAGED_JOB_OPERATIONS = frozenset(
    {
        "asset.receipt.validate",
        "assetpack.verify",
        "runtime.headless",
        "runtime.replay",
    }
)
JOB_ERROR_CODES = frozenset(
    {
        "execution_failed",
        "invalid_workspace",
        "timeout",
        "worker_crashed",
        "worker_protocol",
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


def _plain_string(value: object, context: str, *, max_length: int | None = None) -> str:
    if not isinstance(value, str):
        raise StudioContractError(f"{context} must be a string")
    if max_length is not None and len(value) > max_length:
        raise StudioContractError(f"{context} must contain at most {max_length} characters")
    return value


def _boolean(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise StudioContractError(f"{context} must be a boolean")
    return value


def _integer(value: object, context: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise StudioContractError(f"{context} must be an integer of at least {minimum}")
    return value


def studio_source_path(value: object) -> PurePosixPath | None:
    """Return a canonical portable path rooted below ``source/``."""

    try:
        relative = portable_relative_path(value)
    except UnicodeError:
        return None
    if relative is None or len(relative.parts) < 2 or relative.parts[0] != "source":
        return None
    return relative


def _studio_source_contract_path(value: object, context: str) -> PurePosixPath:
    relative = studio_source_path(value)
    if relative is None or len(relative.parts) > MAX_STUDIO_SOURCE_DEPTH:
        raise StudioContractError(
            f"{context} must be a portable source path of at most "
            f"{MAX_STUDIO_SOURCE_DEPTH} components"
        )
    return relative


def _validate_workspace_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(params, {"workspace_id"}, context)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)


def _validate_source_read_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(params, {"workspace_id", "path"}, context)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _studio_source_contract_path(params["path"], f"{context}/path")


def _validate_source_document_summary(value: object, context: str) -> None:
    document = _object(value, context)
    _closed(document, {"path", "kind", "size", "sha256"}, context)
    _studio_source_contract_path(document["path"], f"{context}/path")
    kind = _string(document["kind"], f"{context}/kind")
    assert kind is not None
    if len(kind) > 128:
        raise StudioContractError(f"{context}/kind must contain at most 128 characters")
    size = _integer(document["size"], f"{context}/size")
    if size > MAX_STUDIO_SOURCE_BYTES:
        raise StudioContractError(f"{context}/size must be at most {MAX_STUDIO_SOURCE_BYTES}")
    _sha256(document["sha256"], f"{context}/sha256")


def _validate_source_document(value: object, context: str) -> None:
    document = _object(value, context)
    _closed(
        document,
        {"path", "kind", "size", "sha256", "encoding", "content", "json"},
        context,
    )
    _validate_source_document_summary(
        {field: document[field] for field in ("path", "kind", "size", "sha256")},
        context,
    )
    if document["encoding"] != "utf-8":
        raise StudioContractError(f"{context}/encoding must be utf-8")
    _plain_string(document["content"], f"{context}/content")
    parsed = _object(document["json"], f"{context}/json")
    _strict_json_value(parsed, f"{context}/json")


def _validate_diagnostic(value: object, context: str) -> None:
    diagnostic = _object(value, context)
    _closed(diagnostic, {"severity", "code", "path", "message"}, context)
    if diagnostic["severity"] != "error":
        raise StudioContractError(f"{context}/severity must be error")
    if diagnostic["code"] not in {"source_error", "validation_error"}:
        raise StudioContractError(f"{context}/code is unknown")
    _plain_string(diagnostic["path"], f"{context}/path")
    _plain_string(diagnostic["message"], f"{context}/message", max_length=512)


def _validate_world_validation(value: object, context: str) -> None:
    validation = _object(value, context)
    _closed(
        validation,
        {
            "valid",
            "profile",
            "world_id",
            "object_count",
            "diagnostics",
            "diagnostics_truncated",
        },
        context,
    )
    _boolean(validation["valid"], f"{context}/valid")
    if validation["profile"] != "release":
        raise StudioContractError(f"{context}/profile must be release")
    world_id = validation["world_id"]
    if world_id is not None:
        _plain_string(world_id, f"{context}/world_id")
    _integer(validation["object_count"], f"{context}/object_count")
    diagnostics = validation["diagnostics"]
    if not isinstance(diagnostics, list) or len(diagnostics) > MAX_STUDIO_DIAGNOSTICS:
        raise StudioContractError(
            f"{context}/diagnostics must contain at most {MAX_STUDIO_DIAGNOSTICS} entries"
        )
    for index, diagnostic in enumerate(diagnostics):
        _validate_diagnostic(diagnostic, f"{context}/diagnostics/{index}")
    _boolean(validation["diagnostics_truncated"], f"{context}/diagnostics_truncated")


def _validate_narrative_analysis(value: object, context: str) -> None:
    analysis = _object(value, context)
    _closed(
        analysis,
        {"format", "format_version", "world_id", "summary", "findings"},
        context,
    )
    if analysis["format"] != "rpg-world-forge.narrative_analysis":
        raise StudioContractError(f"{context}/format is unsupported")
    if isinstance(analysis["format_version"], bool) or analysis["format_version"] != 1:
        raise StudioContractError(f"{context}/format_version must be 1")
    _plain_string(analysis["world_id"], f"{context}/world_id")
    summary = _object(analysis["summary"], f"{context}/summary")
    _strict_json_value(summary, f"{context}/summary")
    findings = analysis["findings"]
    if not isinstance(findings, list):
        raise StudioContractError(f"{context}/findings must be an array")
    for index, value in enumerate(findings):
        finding = _object(value, f"{context}/findings/{index}")
        _closed(
            finding,
            {"severity", "code", "path", "message"},
            f"{context}/findings/{index}",
        )
        if finding["severity"] not in {"error", "warning", "info"}:
            raise StudioContractError(f"{context}/findings/{index}/severity is unknown")
        for field in ("code", "path", "message"):
            _plain_string(finding[field], f"{context}/findings/{index}/{field}")


def _validate_workspace_overview(value: object, context: str) -> None:
    overview = _object(value, context)
    _closed(
        overview,
        {"workspace_id", "project", "status", "repositories", "capabilities"},
        context,
    )
    _identifier(overview["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    project = _object(overview["project"], f"{context}/project")
    _closed(project, {"world_id", "title", "world_version"}, f"{context}/project")
    _string(project["world_id"], f"{context}/project/world_id")
    _string(project["title"], f"{context}/project/title")
    _string(project["world_version"], f"{context}/project/world_version", nullable=True)
    status = _object(overview["status"], f"{context}/status")
    _closed(
        status,
        {"current_phase", "revision", "canon_locked", "worldpack_hash"},
        f"{context}/status",
    )
    _string(status["current_phase"], f"{context}/status/current_phase", nullable=True)
    _integer(status["revision"], f"{context}/status/revision")
    _boolean(status["canon_locked"], f"{context}/status/canon_locked")
    _sha256(status["worldpack_hash"], f"{context}/status/worldpack_hash", nullable=True)
    repositories = _object(overview["repositories"], f"{context}/repositories")
    _closed(
        repositories,
        {"game_registered", "bundle_registered"},
        f"{context}/repositories",
    )
    _boolean(repositories["game_registered"], f"{context}/repositories/game_registered")
    _boolean(repositories["bundle_registered"], f"{context}/repositories/bundle_registered")
    capabilities = _object(overview["capabilities"], f"{context}/capabilities")
    expected_capabilities = {
        "providers": False,
        "source_inspection": True,
        "world_validation": True,
        "narrative_analysis": True,
        "staged_changesets": True,
    }
    _closed(capabilities, set(expected_capabilities), f"{context}/capabilities")
    for field, expected in expected_capabilities.items():
        if capabilities[field] is not expected:
            raise StudioContractError(f"{context}/capabilities/{field} is invalid")


def _validate_authoring_result(method: str, value: object, context: str) -> None:
    result = _object(value, context)
    if method == "workspace.overview":
        _closed(result, {"overview"}, context)
        _validate_workspace_overview(result["overview"], f"{context}/overview")
    elif method == "source.list":
        _closed(result, {"documents"}, context)
        documents = result["documents"]
        if not isinstance(documents, list) or len(documents) > MAX_STUDIO_SOURCE_DOCUMENTS:
            raise StudioContractError(
                f"{context}/documents must contain at most {MAX_STUDIO_SOURCE_DOCUMENTS} entries"
            )
        for index, document in enumerate(documents):
            _validate_source_document_summary(document, f"{context}/documents/{index}")
    elif method == "source.read":
        _closed(result, {"document"}, context)
        _validate_source_document(result["document"], f"{context}/document")
    elif method == "world.validate":
        _closed(result, {"validation"}, context)
        _validate_world_validation(result["validation"], f"{context}/validation")
    elif method == "world.analyze":
        _closed(result, {"validation", "analysis"}, context)
        _validate_world_validation(result["validation"], f"{context}/validation")
        if result["analysis"] is not None:
            _validate_narrative_analysis(result["analysis"], f"{context}/analysis")
    else:  # pragma: no cover - callers discriminate the method first
        raise StudioContractError("envelope/method is unknown")


def studio_job_path(value: object) -> PurePosixPath | None:
    """Return one bounded portable path relative to a registered workspace root."""

    try:
        relative = portable_relative_path(value)
    except UnicodeError:
        return None
    if relative is None or len(relative.parts) > MAX_STUDIO_JOB_PATH_DEPTH:
        return None
    return relative


def _validate_job_input(operation: str, value: object, context: str) -> None:
    job_input = _object(value, context)
    fields = {
        "asset.receipt.validate": {"receipt"},
        "assetpack.verify": {"assetpack", "worldpack"},
        "runtime.headless": {"worldpack", "ticks"},
        "runtime.replay": {"worldpack", "replay"},
    }[operation]
    _closed(job_input, fields, context)
    for field in fields - {"ticks"}:
        if studio_job_path(job_input[field]) is None:
            raise StudioContractError(
                f"{context}/{field} must be a portable path of at most "
                f"{MAX_STUDIO_JOB_PATH_DEPTH} components"
            )
    if operation == "runtime.headless":
        ticks = job_input["ticks"]
        if (
            isinstance(ticks, bool)
            or not isinstance(ticks, int)
            or not 0 <= ticks <= MAX_RUNTIME_TICKS
        ):
            raise StudioContractError(
                f"{context}/ticks must be an integer from 0 to {MAX_RUNTIME_TICKS}"
            )


def _validate_receipt_result(result: dict[str, Any], context: str) -> None:
    _closed(
        result,
        {"operation", "valid", "issue_count", "issues_truncated", "issues"},
        context,
    )
    if result["operation"] != "asset.receipt.validate":
        raise StudioContractError(f"{context}/operation is invalid")
    _boolean(result["valid"], f"{context}/valid")
    issue_count = _integer(result["issue_count"], f"{context}/issue_count")
    _boolean(result["issues_truncated"], f"{context}/issues_truncated")
    issues = result["issues"]
    if not isinstance(issues, list) or len(issues) > MAX_STUDIO_RECEIPT_ISSUES:
        raise StudioContractError(
            f"{context}/issues must contain at most {MAX_STUDIO_RECEIPT_ISSUES} entries"
        )
    if issue_count < len(issues):
        raise StudioContractError(f"{context}/issue_count cannot be smaller than issues")
    for index, value in enumerate(issues):
        issue = _object(value, f"{context}/issues/{index}")
        _closed(issue, {"path", "message"}, f"{context}/issues/{index}")
        _plain_string(issue["path"], f"{context}/issues/{index}/path", max_length=512)
        _plain_string(issue["message"], f"{context}/issues/{index}/message", max_length=512)


def _validate_assetpack_result(result: dict[str, Any], context: str) -> None:
    _closed(
        result,
        {
            "operation",
            "valid",
            "world_id",
            "world_content_hash",
            "target_id",
            "target_hash",
            "content_hash",
            "asset_count",
            "file_count",
            "binding_count",
        },
        context,
    )
    if result["operation"] != "assetpack.verify" or result["valid"] is not True:
        raise StudioContractError(f"{context} is not an assetpack verification result")
    _string(result["world_id"], f"{context}/world_id")
    _sha256(result["world_content_hash"], f"{context}/world_content_hash")
    _string(result["target_id"], f"{context}/target_id")
    _sha256(result["target_hash"], f"{context}/target_hash")
    _sha256(result["content_hash"], f"{context}/content_hash")
    for field in ("asset_count", "file_count", "binding_count"):
        _integer(result[field], f"{context}/{field}")


def _validate_runtime_result(operation: str, result: dict[str, Any], context: str) -> None:
    count_field = "ticks" if operation == "runtime.headless" else "action_count"
    _closed(
        result,
        {
            "operation",
            "world_id",
            "world_content_hash",
            count_field,
            "state_tick",
            "absolute_minute",
            "state_digest",
        },
        context,
    )
    if result["operation"] != operation:
        raise StudioContractError(f"{context}/operation is invalid")
    _string(result["world_id"], f"{context}/world_id")
    _sha256(result["world_content_hash"], f"{context}/world_content_hash")
    count = _integer(result[count_field], f"{context}/{count_field}")
    if count > MAX_RUNTIME_TICKS:
        raise StudioContractError(f"{context}/{count_field} exceeds {MAX_RUNTIME_TICKS}")
    _integer(result["state_tick"], f"{context}/state_tick")
    _integer(result["absolute_minute"], f"{context}/absolute_minute")
    _sha256(result["state_digest"], f"{context}/state_digest")


def _validate_job_result(operation: str, value: object, context: str) -> None:
    result = _object(value, context)
    if operation == "asset.receipt.validate":
        _validate_receipt_result(result, context)
    elif operation == "assetpack.verify":
        _validate_assetpack_result(result, context)
    else:
        _validate_runtime_result(operation, result, context)


def _validate_job_error(value: object, context: str) -> None:
    error = _object(value, context)
    _closed(error, {"code", "message"}, context)
    if error["code"] not in JOB_ERROR_CODES:
        raise StudioContractError(f"{context}/code is unknown")
    message = _string(error["message"], f"{context}/message")
    assert message is not None
    if len(message) > 512:
        raise StudioContractError(f"{context}/message must contain at most 512 characters")


def validate_job_create_params(value: object) -> dict[str, Any]:
    params = _object(value, "job.create params")
    allowed = {"job_id", "workspace_id", "operation", "input"}
    missing = {"workspace_id", "operation", "input"} - set(params)
    unknown = set(params) - allowed
    if missing or unknown:
        fields = missing or unknown
        raise StudioContractError(
            f"job.create params have invalid fields: {', '.join(sorted(fields))}"
        )
    if "job_id" in params:
        _identifier(params["job_id"], "job.create params/job_id", ENTITY_ID_PATTERN)
    _identifier(
        params["workspace_id"],
        "job.create params/workspace_id",
        WORKSPACE_ID_PATTERN,
    )
    operation = params["operation"]
    if not isinstance(operation, str) or operation not in MANAGED_JOB_OPERATIONS:
        raise StudioContractError("job.create params/operation is not an executable operation")
    _validate_job_input(operation, params["input"], "job.create params/input")
    return params


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
    version = job["format_version"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version not in {LEGACY_JOB_VERSION, MANAGED_JOB_VERSION}
    ):
        raise StudioContractError("job format_version must be 1 or 2")
    _identifier(job["job_id"], "job/job_id", ENTITY_ID_PATTERN)
    _identifier(job["workspace_id"], "job/workspace_id", WORKSPACE_ID_PATTERN)
    operation = job["operation"]
    if not isinstance(job["state"], str) or job["state"] not in JOB_STATES:
        raise StudioContractError("job/state is unknown")
    if version == LEGACY_JOB_VERSION:
        _identifier(operation, "job/operation", OPERATION_PATTERN)
        for field in ("input", "result", "error"):
            item = job[field]
            if field != "input" and item is None:
                continue
            _object(item, f"job/{field}")
            _strict_json_value(item, f"job/{field}")
    else:
        if not isinstance(operation, str) or operation not in MANAGED_JOB_OPERATIONS:
            raise StudioContractError("job/operation is not an executable operation")
        _validate_job_input(operation, job["input"], "job/input")
        state = job["state"]
        if state == "succeeded":
            if job["result"] is None or job["error"] is not None:
                raise StudioContractError("a succeeded job requires result and forbids error")
            _validate_job_result(operation, job["result"], "job/result")
        elif state == "failed":
            if job["result"] is not None or job["error"] is None:
                raise StudioContractError("a failed job requires error and forbids result")
            _validate_job_error(job["error"], "job/error")
        elif job["result"] is not None or job["error"] is not None:
            raise StudioContractError("only succeeded/failed jobs may carry result or error")
    _timestamp(job["created_at"], "job/created_at")
    _timestamp(job["updated_at"], "job/updated_at")
    return job


def validate_studio_protocol_envelope(value: object) -> dict[str, Any]:
    envelope = _object(value, "envelope")
    common = {"protocol", "protocol_version", "kind", "request_id"}
    kind = envelope.get("kind")
    additions = {
        "request": {"method", "params"},
        "response": {"method", "result"},
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
        method = envelope["method"]
        if not isinstance(method, str) or method not in METHODS:
            raise StudioContractError("envelope/method is unknown")
        if method in WORKSPACE_AUTHORING_METHODS:
            _validate_workspace_params(envelope["params"], "envelope/params")
        elif method == "source.read":
            _validate_source_read_params(envelope["params"], "envelope/params")
        elif method == "job.create":
            validate_job_create_params(envelope["params"])
        elif method == "job.cancel":
            params = _object(envelope["params"], "envelope/params")
            _closed(params, {"job_id"}, "envelope/params")
            _identifier(params["job_id"], "envelope/params/job_id", ENTITY_ID_PATTERN)
        else:
            params = _object(envelope["params"], "envelope/params")
            _strict_json_value(params, "envelope/params")
    elif kind == "response":
        method = envelope["method"]
        if not isinstance(method, str) or method not in METHODS:
            raise StudioContractError("envelope/method is unknown")
        if method in AUTHORING_METHODS:
            _validate_authoring_result(method, envelope["result"], "envelope/result")
        elif method in EXACT_JOB_METHODS:
            result = _object(envelope["result"], "envelope/result")
            _closed(result, {"job"}, "envelope/result")
            job = validate_studio_job(result["job"])
            if method == "job.create" and job["format_version"] != MANAGED_JOB_VERSION:
                raise StudioContractError("job.create responses require a managed v2 job")
        elif method in LEGACY_METHODS:
            result = _object(envelope["result"], "envelope/result")
            _strict_json_value(result, "envelope/result")
        else:  # pragma: no cover - METHODS is partitioned above
            raise StudioContractError("envelope/method is unknown")
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
