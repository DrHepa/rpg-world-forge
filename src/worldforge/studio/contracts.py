from __future__ import annotations

import base64
import hmac
import math
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Any

from isoworld.content.portability import portable_relative_path
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.creation_vocabulary import (
    CREATION_CONTENT_MODES as SCAFFOLD_CONTENT_MODES,
)
from worldforge.creation_vocabulary import (
    CREATION_PROJECT_KINDS as SCAFFOLD_PROJECT_KINDS,
)
from worldforge.creation_vocabulary import (
    GAMEPLAY_FAMILIES as SCAFFOLD_GAMEPLAY_FAMILIES,
)
from worldforge.creation_vocabulary import (
    NARRATIVE_AUTHORSHIP_MODES as SCAFFOLD_NARRATIVE_AUTHORSHIP_MODES,
)
from worldforge.creation_vocabulary import (
    NARRATIVE_REQUIREMENTS as SCAFFOLD_NARRATIVE_REQUIREMENTS,
)
from worldforge.creation_vocabulary import (
    NARRATIVE_TOPOLOGIES as SCAFFOLD_NARRATIVE_TOPOLOGIES,
)
from worldforge.creation_vocabulary import (
    PRESENTATION_MODES as SCAFFOLD_PRESENTATION_MODES,
)
from worldforge.creation_vocabulary import (
    RUNTIME_SUPPORT_INTENTS as SCAFFOLD_RUNTIME_SUPPORT_INTENTS,
)
from worldforge.creation_vocabulary import (
    WORLD_PRESENCES as SCAFFOLD_WORLD_PRESENCES,
)
from worldforge.creation_vocabulary import (
    is_creation_identifier,
)
from worldforge.generic_asset_limits import MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
from worldforge.integrity import canonical_json_bytes, canonical_payload_hash
from worldforge.studio.changeset_review import ReviewDiffError, compute_review_sha256
from worldforge.studio.errors import ERROR_CODES, LEGACY_ERROR_CODES, StudioContractError

WORKSPACE_FORMAT = "rpg-world-forge.forge_workspace"
CHANGESET_FORMAT = "rpg-world-forge.studio_changeset"
JOB_FORMAT = "rpg-world-forge.studio_job"
EXTERNAL_GRANT_FORMAT = "rpg-world-forge.studio_external_grant"
CREATION_ROOT_GRANT_FORMAT = "world-forge.studio_creation_root_grant"
CREATION_OUTPUT_GRANT_FORMAT = "world-forge.studio_creation_output_grant"
CREATION_WORKSPACE_FORMAT = "world-forge.studio_creation_workspace"
CREATION_CHANGESET_FORMAT = "world-forge.studio_creation_changeset"
CREATION_ARTIFACT_FORMAT = "world-forge.studio_creation_artifact"
CREATION_EVIDENCE_FORMAT = "world-forge.studio_creation_evidence"
CREATION_PREVIEW_FORMAT = "world-forge.studio_creation_preview"
CREATION_JOB_FORMAT = "world-forge.studio_creation_job"
CREATION_WORKER_FORMAT = "world-forge.studio_creation_worker"
PROTOCOL_FORMAT = "rpg-world-forge.studio_protocol"
STUDIO_VERSION = 1
STUDIO_PROTOCOL_V2 = 2
STUDIO_PROTOCOL_V3 = 3
STUDIO_PROTOCOL_V4 = 4
STUDIO_PROTOCOL_V5 = 5
MAX_CHANGE_FILE_BYTES = 16 * 1024 * 1024
MAX_CHANGESET_BYTES = 64 * 1024 * 1024
MAX_CHANGESET_OPERATIONS = 256
MAX_CREATION_GAME_PACKAGE_BYTES = 264 * 1024 * 1024
PORTABLE_SOURCE_PATH_FORMAT = "rpg-world-forge-portable-source-path"

WORKSPACE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
OPERATION_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
PUBLIC_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ASSET_ENTRY_ID_PATTERN = re.compile(r"^asset_[0-9a-f]{64}$")
ASSET_PREVIEW_HANDLE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)

CHANGESET_STATES = frozenset({"staged", "approved", "applying", "rejected", "applied"})
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
        "asset.catalog.list",
        "asset.catalog.inspect",
        "asset.preview.open",
        "asset.preview.read",
        "asset.preview.close",
        "world.validate",
        "world.analyze",
        "events.list",
        "changeset.create",
        "changeset.get",
        "changeset.list",
        "changeset.diff",
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
EXACT_ASSET_CATALOG_METHODS = frozenset({"asset.catalog.list", "asset.catalog.inspect"})
EXACT_ASSET_PREVIEW_METHODS = frozenset(
    {"asset.preview.open", "asset.preview.read", "asset.preview.close"}
)
EXACT_CHANGESET_METHODS = frozenset(
    {
        "changeset.create",
        "changeset.get",
        "changeset.list",
        "changeset.diff",
        "changeset.approve",
        "changeset.reject",
        "changeset.apply",
    }
)
CHANGESET_ACTION_METHODS = frozenset({"changeset.approve", "changeset.reject", "changeset.apply"})
LEGACY_METHODS = (
    METHODS
    - AUTHORING_METHODS
    - EXACT_JOB_METHODS
    - EXACT_CHANGESET_METHODS
    - EXACT_ASSET_CATALOG_METHODS
    - EXACT_ASSET_PREVIEW_METHODS
)
MAX_STUDIO_SOURCE_DEPTH = 8
MAX_STUDIO_SOURCE_BYTES = 256 * 1024
MAX_STUDIO_SOURCE_DOCUMENTS = 1024
MAX_ASSET_CATALOG_PAGE = 64
MAX_ASSET_INLINE_BYTES = 256 * 1024
MAX_ASSET_CATALOG_PATH_DEPTH = 32
MAX_ASSET_CATALOG_PATH_LENGTH = 4096
ASSET_PREVIEW_CHUNK_BYTES = 64 * 1024
MAX_ASSET_PREVIEW_BYTES = 512 * 1024 * 1024
MAX_ASSET_PREVIEW_SEQUENCE = 8191
MAX_ASSET_PREVIEW_BASE64_LENGTH = 87_384
ASSET_PREVIEW_MEDIA_TYPES = frozenset({"audio/wav", "image/png"})
ASSET_CATALOG_CATEGORIES = frozenset(
    {
        "manifest",
        "target",
        "visual_bible",
        "audio_bible",
        "inventory",
        "specification",
        "production_receipt",
        "production_request",
        "production_output",
        "processing_receipt",
        "processing_recipe",
        "processing_output",
        "license",
        "qa",
        "runtime_output",
    }
)
MAX_STUDIO_DIAGNOSTICS = 512
MAX_STUDIO_JOB_PATH_DEPTH = 16
MAX_STUDIO_RECEIPT_ISSUES = 256
MAX_RUNTIME_TICKS = 1_000_000
LEGACY_JOB_VERSION = 1
MANAGED_JOB_VERSION = 2
EXTERNAL_JOB_VERSION = 3
MANAGED_JOB_OPERATIONS = frozenset(
    {
        "asset.receipt.validate",
        "assetpack.verify",
        "runtime.headless",
        "runtime.replay",
    }
)
EXTERNAL_JOB_OPERATIONS = frozenset(
    {
        "game.materialize",
        "game.package",
        "game.package.extract",
    }
)
EXTERNAL_JOB_STATES = frozenset(
    {"queued", "running", "succeeded", "failed", "canceled", "orphaned"}
)
EXTERNAL_GRANT_ROLES = frozenset({"source", "target"})
EXTERNAL_GRANT_STATES = frozenset({"ready", "reserved", "recovery_required", "consumed", "revoked"})
EXTERNAL_ARTIFACT_KINDS = frozenset(
    {"game_materialization_bundle", "standalone_game", "game_package"}
)
EXTERNAL_OPERATION_KINDS = {
    "game.materialize": {
        "source": "game_materialization_bundle",
        "target": "standalone_game",
    },
    "game.package": {
        "source": "standalone_game",
        "target": "game_package",
    },
    "game.package.extract": {
        "source": "game_package",
        "target": "standalone_game",
    },
}
EXTERNAL_METHODS = frozenset(
    {
        "external_grant.create",
        "external_grant.get",
        "external_grant.revoke",
        "job.recover",
    }
)
METHODS_V2 = frozenset(
    {
        "service.initialize",
        "external_grant.create",
        "external_grant.get",
        "external_grant.revoke",
        "job.create",
        "job.get",
        "job.list",
        "job.cancel",
        "job.recover",
    }
)
METHODS_V3 = frozenset(
    {
        "service.initialize",
        "creation_root_grant.create",
        "creation_root_grant.get",
        "creation_root_grant.revoke",
        "creation_workspace.create",
        "creation_workspace.recover",
        "creation_workspace.register",
        "creation_workspace.get",
        "creation_workspace.list",
        "creation_workspace.open",
        "creation_document.list",
        "creation_document.read",
        "creation_changeset.create",
        "creation_changeset.get",
        "creation_changeset.list",
        "creation_changeset.diff",
        "creation_changeset.approve",
        "creation_changeset.reject",
        "creation_changeset.apply",
        "creation_changeset.recover",
        "creation_workflow.get",
        "creation_workflow.reconcile",
        "creation_phase.read",
        "creation_phase.validate",
        "creation_phase.complete",
        "creation_phase.reopen",
        "creation_readiness.inspect",
    }
)
METHODS_V4 = frozenset(
    {
        "service.initialize",
        "creation_output_grant.create",
        "creation_output_grant.get",
        "creation_output_grant.list",
        "creation_output_grant.revoke",
        "creation_artifact.list",
        "creation_artifact.inspect",
        "creation_evidence.inspect",
        "creation_job.create",
        "creation_job.get",
        "creation_job.list",
        "creation_job.cancel",
        "creation_job.recover",
        "creation_event.list",
        "creation_preview.open",
        "creation_preview.read",
        "creation_preview.close",
    }
)
METHODS_V5 = METHODS_V4 | {"creation_workspace.create"}
MAX_CREATION_ARTIFACT_PAGE = 64
MAX_CREATION_OUTPUT_GRANT_PAGE = 8
MAX_CREATION_ARTIFACTS = 4096
MAX_CREATION_EVIDENCE_BYTES = 1024 * 1024
CREATION_PREVIEW_CHUNK_BYTES = 64 * 1024
MAX_CREATION_PREVIEW_BYTES = 64 * 1024 * 1024
MAX_CREATION_PREVIEW_SEQUENCE = 1023
MAX_CREATION_PREVIEW_BASE64_LENGTH = 87_384
MAX_CREATION_ADMISSION_DOCUMENT_BYTES = 768 * 1024
CREATION_ARTIFACT_LIFECYCLES = frozenset({"active", "invalidated", "historical", "candidate"})
CREATION_CONTENT_MODES = frozenset(SCAFFOLD_CONTENT_MODES)
CREATION_PROJECT_KINDS = frozenset(SCAFFOLD_PROJECT_KINDS)
CREATION_GAMEPLAY_FAMILIES = frozenset(SCAFFOLD_GAMEPLAY_FAMILIES)
CREATION_WORLD_PRESENCES = frozenset(SCAFFOLD_WORLD_PRESENCES)
CREATION_NARRATIVE_REQUIREMENTS = frozenset(SCAFFOLD_NARRATIVE_REQUIREMENTS)
CREATION_NARRATIVE_AUTHORSHIP = frozenset(SCAFFOLD_NARRATIVE_AUTHORSHIP_MODES)
CREATION_NARRATIVE_TOPOLOGIES = frozenset(SCAFFOLD_NARRATIVE_TOPOLOGIES)
CREATION_PRESENTATION_MODES = frozenset(SCAFFOLD_PRESENTATION_MODES)
CREATION_RUNTIME_SUPPORT_INTENTS = frozenset(SCAFFOLD_RUNTIME_SUPPORT_INTENTS)
CREATION_ROOT_GRANT_ROLES = frozenset({"existing_root", "new_target"})
CREATION_ROOT_GRANT_STATES = frozenset(
    {"ready", "reserved", "recovery_required", "consumed", "revoked"}
)
CREATION_OUTPUT_GRANT_KINDS = frozenset(
    {
        "generic_assetpack_directory",
        "game_runtime_bundle_directory",
        "game_materialization_bundle_directory",
        "standalone_game_directory",
        "game_package_file",
    }
)
CREATION_OUTPUT_GRANT_KINDS_V6 = CREATION_OUTPUT_GRANT_KINDS | {"headless_evidence_directory"}
CREATION_OUTPUT_GRANT_STATES = frozenset(
    {"ready", "reserved", "published", "recovery_required", "revoked"}
)
CREATION_CHANGESET_STATES = frozenset(
    {"staged", "approved", "applying", "applied", "rejected", "recovery_required"}
)
CREATION_READINESS_STATES = frozenset(
    {"missing", "not_started", "invalid", "authoring_ready", "implementation_ready", "blocked"}
)
CREATION_JOB_OPERATIONS_V1 = frozenset({"artifact.admit", "creation.compile"})
CREATION_JOB_OPERATIONS_V2 = frozenset({"asset.process"})
CREATION_JOB_OPERATIONS_V3 = frozenset({"asset.release.seal"})
CREATION_JOB_OPERATIONS_V4 = frozenset({"runtime.compose"})
CREATION_JOB_OPERATIONS_V5 = frozenset({"runtime.bundle.build"})
CREATION_JOB_OPERATIONS_V6 = frozenset({"game.materialization.bundle.build"})
CREATION_JOB_OPERATIONS_V7 = frozenset({"game.materialize"})
CREATION_JOB_OPERATIONS_V8 = frozenset({"game.package"})
CREATION_JOB_OPERATIONS_V9 = frozenset({"game.package.extract"})
CREATION_JOB_OPERATIONS_V10 = frozenset({"asset.qa.review"})
CREATION_JOB_OPERATIONS_V11 = frozenset({"asset.release.authorize"})
CREATION_JOB_OPERATIONS_V12 = frozenset({"runtime.headless.verify"})
RUNTIME_HEADLESS_PLATFORM_IDS = frozenset({"platform:linux_x86_64", "platform:windows_x86_64"})
CREATION_JOB_OPERATIONS = (
    CREATION_JOB_OPERATIONS_V1
    | CREATION_JOB_OPERATIONS_V2
    | CREATION_JOB_OPERATIONS_V3
    | CREATION_JOB_OPERATIONS_V4
    | CREATION_JOB_OPERATIONS_V5
    | CREATION_JOB_OPERATIONS_V6
    | CREATION_JOB_OPERATIONS_V7
    | CREATION_JOB_OPERATIONS_V8
    | CREATION_JOB_OPERATIONS_V9
    | CREATION_JOB_OPERATIONS_V10
    | CREATION_JOB_OPERATIONS_V11
    | CREATION_JOB_OPERATIONS_V12
)
CREATION_JOB_STATES = frozenset(
    {"queued", "running", "succeeded", "failed", "canceled", "orphaned"}
)
CREATION_JOB_PROGRESS = frozenset(
    {
        "queued",
        "reserved",
        "worker_started",
        "output_published",
        "registry_committing",
        "committed",
        "cleanup_pending",
        "failed",
        "canceled",
        "orphaned",
    }
)
CREATION_JOB_ERROR_CODES = frozenset(
    {
        "authority_changed",
        "canceled",
        "input_changed",
        "internal_error",
        "invalid_artifact",
        "invalid_project",
        "recovery_ambiguous",
        "recovery_required",
        "service_restart",
        "timeout",
        "worker_crashed",
        "worker_protocol",
    }
)
CREATION_ANALYSIS_STATUSES = frozenset(
    {"passed", "failed", "inconclusive", "unsupported", "not_applicable"}
)
MAX_CREATION_JOB_INPUTS = 128
MAX_CREATION_JOB_OUTPUTS = 16
MAX_CREATION_JOB_PAGE = 8
MAX_CREATION_EVENT_PAGE = 256
JOB_ERROR_CODES = frozenset(
    {
        "execution_failed",
        "invalid_workspace",
        "timeout",
        "worker_crashed",
        "worker_protocol",
    }
)
EXTERNAL_JOB_ERROR_CODES = JOB_ERROR_CODES | frozenset(
    {
        "recovery_ambiguous",
        "recovery_failed",
        "recovery_required",
        "source_changed",
        "target_changed",
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


def _integer(
    value: object,
    context: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise StudioContractError(f"{context} must be an integer of at least {minimum}")
    if maximum is not None and value > maximum:
        raise StudioContractError(f"{context} must be an integer of at most {maximum}")
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


def _validate_asset_catalog_list_params(value: object, context: str) -> None:
    params = _object(value, context)
    allowed = {"workspace_id", "offset", "limit", "expected_manifest_revision"}
    missing = {"workspace_id"} - set(params)
    unknown = set(params) - allowed
    if missing or unknown:
        fields = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(fields))}")
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    offset = 0
    if "offset" in params:
        offset = _integer(params["offset"], f"{context}/offset")
    if "limit" in params:
        limit = _integer(params["limit"], f"{context}/limit", minimum=1)
        if limit > MAX_ASSET_CATALOG_PAGE:
            raise StudioContractError(f"{context}/limit must be at most {MAX_ASSET_CATALOG_PAGE}")
    expected = params.get("expected_manifest_revision")
    if expected is not None:
        _sha256(expected, f"{context}/expected_manifest_revision")
    if offset > 0 and expected is None:
        raise StudioContractError(
            f"{context}/expected_manifest_revision is required after page one"
        )


def _validate_asset_catalog_inspect_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(
        params,
        {"workspace_id", "entry_id", "expected_manifest_revision"},
        context,
    )
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _identifier(params["entry_id"], f"{context}/entry_id", ASSET_ENTRY_ID_PATTERN)
    _sha256(
        params["expected_manifest_revision"],
        f"{context}/expected_manifest_revision",
    )


def _validate_asset_preview_params(method: str, value: object, context: str) -> None:
    params = _object(value, context)
    if method == "asset.preview.open":
        _closed(params, {"workspace_id", "manifest_revision", "entry_id"}, context)
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
        _sha256(params["manifest_revision"], f"{context}/manifest_revision")
        _identifier(params["entry_id"], f"{context}/entry_id", ASSET_ENTRY_ID_PATTERN)
        return

    if method == "asset.preview.read":
        _closed(params, {"handle", "sequence"}, context)
        _identifier(params["handle"], f"{context}/handle", ASSET_PREVIEW_HANDLE_PATTERN)
        sequence = _integer(params["sequence"], f"{context}/sequence")
        if sequence > MAX_ASSET_PREVIEW_SEQUENCE:
            raise StudioContractError(
                f"{context}/sequence must be at most {MAX_ASSET_PREVIEW_SEQUENCE}"
            )
        return

    _closed(params, {"handle"}, context)
    _identifier(params["handle"], f"{context}/handle", ASSET_PREVIEW_HANDLE_PATTERN)


def _validate_changeset_create_params(value: object, context: str) -> None:
    params = _object(value, context)
    allowed = {"changeset_id", "workspace_id", "operations"}
    missing = {"workspace_id", "operations"} - set(params)
    unknown = set(params) - allowed
    if missing or unknown:
        fields = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(fields))}")
    if "changeset_id" in params:
        _identifier(params["changeset_id"], f"{context}/changeset_id", ENTITY_ID_PATTERN)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    operations = params["operations"]
    if (
        not isinstance(operations, list)
        or not operations
        or len(operations) > MAX_CHANGESET_OPERATIONS
    ):
        raise StudioContractError(
            f"{context}/operations must contain 1 to {MAX_CHANGESET_OPERATIONS} entries"
        )
    seen: set[tuple[str, ...]] = set()
    total_bytes = 0
    for index, value in enumerate(operations):
        operation_context = f"{context}/operations/{index}"
        operation = _object(value, operation_context)
        kind = operation.get("operation")
        if not isinstance(kind, str) or kind not in {"create", "replace", "delete"}:
            raise StudioContractError(f"{operation_context}/operation is unknown")
        required = {"path", "operation"}
        allowed_operation = set(required)
        if kind != "delete":
            required.add("content")
            allowed_operation.add("content")
        if kind != "create":
            required.add("expected_base_sha256")
            allowed_operation.add("expected_base_sha256")
        missing_operation = required - set(operation)
        unknown_operation = set(operation) - allowed_operation
        if missing_operation or unknown_operation:
            fields = missing_operation or unknown_operation
            raise StudioContractError(
                f"{operation_context} has invalid fields: {', '.join(sorted(fields))}"
            )
        relative = _studio_source_contract_path(operation["path"], f"{operation_context}/path")
        key = tuple(unicodedata.normalize("NFC", part).casefold() for part in relative.parts)
        if key in seen:
            raise StudioContractError(f"{context}/operations contain an NFC/casefold collision")
        seen.add(key)
        if "expected_base_sha256" in operation:
            _sha256(
                operation["expected_base_sha256"],
                f"{operation_context}/expected_base_sha256",
            )
        if kind != "delete":
            content = _plain_string(operation["content"], f"{operation_context}/content")
            try:
                content_size = len(content.encode("utf-8", errors="strict"))
            except UnicodeEncodeError as exc:
                raise StudioContractError(
                    f"{operation_context}/content must be valid UTF-8 text"
                ) from exc
            if content_size > MAX_STUDIO_SOURCE_BYTES:
                raise StudioContractError(
                    f"{operation_context}/content must be at most "
                    f"{MAX_STUDIO_SOURCE_BYTES} UTF-8 bytes"
                )
            total_bytes += content_size
            if total_bytes > MAX_CHANGESET_BYTES:
                raise StudioContractError(
                    f"{context}/operations retain at most {MAX_CHANGESET_BYTES} UTF-8 bytes"
                )


def _validate_changeset_id_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(params, {"changeset_id"}, context)
    _identifier(params["changeset_id"], f"{context}/changeset_id", ENTITY_ID_PATTERN)


def _validate_changeset_list_params(value: object, context: str) -> None:
    params = _object(value, context)
    allowed = {"workspace_id", "status", "limit"}
    unknown = set(params) - allowed
    if unknown:
        raise StudioContractError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    if "workspace_id" in params:
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    if "status" in params and (
        not isinstance(params["status"], str) or params["status"] not in CHANGESET_STATES
    ):
        raise StudioContractError(f"{context}/status is unknown")
    if "limit" in params:
        limit = _integer(params["limit"], f"{context}/limit", minimum=1)
        if limit > 1000:
            raise StudioContractError(f"{context}/limit must be at most 1000")


def _validate_changeset_action_params(value: object, context: str) -> None:
    params = _object(value, context)
    allowed = {"changeset_id", "expected_review_sha256"}
    missing = {"changeset_id"} - set(params)
    unknown = set(params) - allowed
    if missing or unknown:
        fields = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(fields))}")
    _identifier(params["changeset_id"], f"{context}/changeset_id", ENTITY_ID_PATTERN)
    if "expected_review_sha256" in params:
        _sha256(
            params["expected_review_sha256"],
            f"{context}/expected_review_sha256",
        )


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
        "asset_catalog_inspection": True,
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


def _asset_catalog_path(value: object, context: str, *, nullable: bool) -> PurePosixPath | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > MAX_ASSET_CATALOG_PATH_LENGTH:
        raise StudioContractError(f"{context} must be a bounded portable path")
    try:
        relative = portable_relative_path(value)
    except UnicodeError as exc:
        raise StudioContractError(f"{context} must be a bounded portable path") from exc
    if relative is None or len(relative.parts) > MAX_ASSET_CATALOG_PATH_DEPTH:
        raise StudioContractError(f"{context} must be a bounded portable path")
    return relative


def _validate_asset_catalog_entry(value: object, context: str) -> dict[str, Any]:
    entry = _object(value, context)
    _closed(
        entry,
        {
            "entry_id",
            "asset_id",
            "category",
            "role",
            "path",
            "sha256",
            "media_type",
            "selected",
            "inspectable",
        },
        context,
    )
    _identifier(entry["entry_id"], f"{context}/entry_id", ASSET_ENTRY_ID_PATTERN)
    if entry["asset_id"] is not None:
        _plain_string(entry["asset_id"], f"{context}/asset_id", max_length=128)
    category = entry["category"]
    if not isinstance(category, str) or category not in ASSET_CATALOG_CATEGORIES:
        raise StudioContractError(f"{context}/category is unknown")
    if entry["role"] is not None:
        _plain_string(entry["role"], f"{context}/role", max_length=128)
    path = _asset_catalog_path(entry["path"], f"{context}/path", nullable=True)
    _sha256(entry["sha256"], f"{context}/sha256")
    if entry["media_type"] is not None:
        _plain_string(entry["media_type"], f"{context}/media_type", max_length=128)
    _boolean(entry["selected"], f"{context}/selected")
    _boolean(entry["inspectable"], f"{context}/inspectable")
    if entry["selected"] and category != "production_output":
        raise StudioContractError(f"{context}/selected is limited to production outputs")
    if path is None and (
        category != "processing_recipe"
        or entry["inspectable"] is not False
        or entry["selected"] is not False
    ):
        raise StudioContractError(f"{context} has an invalid identity-only entry")
    return entry


def _bounded_text(value: object, context: str) -> str:
    text = _plain_string(value, context)
    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise StudioContractError(f"{context} must be valid UTF-8") from exc
    if len(encoded) > MAX_ASSET_INLINE_BYTES:
        raise StudioContractError(f"{context} must be at most {MAX_ASSET_INLINE_BYTES} UTF-8 bytes")
    return text


def _bounded_string_array(value: object, context: str) -> None:
    if not isinstance(value, list) or len(value) > 64:
        raise StudioContractError(f"{context} must contain at most 64 strings")
    for index, item in enumerate(value):
        _plain_string(item, f"{context}/{index}", max_length=256)


def _validate_asset_inspection(value: object, context: str) -> dict[str, Any]:
    inspection = _object(value, context)
    kind = inspection.get("kind")
    if kind == "json":
        _closed(inspection, {"kind", "encoding", "content", "value"}, context)
        if inspection["encoding"] != "utf-8":
            raise StudioContractError(f"{context}/encoding must be utf-8")
        content = _bounded_text(inspection["content"], f"{context}/content")
        parsed = _object(inspection["value"], f"{context}/value")
        _strict_json_value(parsed, f"{context}/value")
        try:
            decoded = decode_json_object(
                content.encode("utf-8"),
                source="asset catalog inspection",
            )
        except RuntimeIOError as exc:
            raise StudioContractError(f"{context}/content must be a strict JSON object") from exc
        if decoded != parsed:
            raise StudioContractError(f"{context}/value does not match content")
    elif kind == "glsl":
        _closed(inspection, {"kind", "encoding", "content"}, context)
        if inspection["encoding"] != "utf-8":
            raise StudioContractError(f"{context}/encoding must be utf-8")
        _bounded_text(inspection["content"], f"{context}/content")
    elif kind == "png":
        _closed(
            inspection,
            {"kind", "width", "height", "bit_depth", "color_type", "interlaced"},
            context,
        )
        for field in ("width", "height", "bit_depth"):
            _integer(inspection[field], f"{context}/{field}", minimum=1)
        _integer(inspection["color_type"], f"{context}/color_type")
        _boolean(inspection["interlaced"], f"{context}/interlaced")
    elif kind == "wav":
        _closed(
            inspection,
            {
                "kind",
                "channels",
                "sample_rate",
                "sample_width_bits",
                "frame_count",
                "duration_ms",
            },
            context,
        )
        for field in ("channels", "sample_rate", "sample_width_bits"):
            _integer(inspection[field], f"{context}/{field}", minimum=1)
        for field in ("frame_count", "duration_ms"):
            _integer(inspection[field], f"{context}/{field}")
    elif kind == "font":
        _closed(inspection, {"kind", "flavor", "table_count"}, context)
        if inspection["flavor"] not in {"truetype", "opentype"}:
            raise StudioContractError(f"{context}/flavor is unknown")
        _integer(inspection["table_count"], f"{context}/table_count", minimum=1)
    elif kind == "glb":
        _closed(
            inspection,
            {
                "kind",
                "byte_length",
                "json_chunk_bytes",
                "bin_chunk_bytes",
                "extensions_used",
                "extensions_required",
                "external_uris",
                "embedded_uris",
                "max_texture_dimension",
                "metrics",
            },
            context,
        )
        for field in (
            "byte_length",
            "json_chunk_bytes",
            "bin_chunk_bytes",
            "embedded_uris",
            "max_texture_dimension",
        ):
            _integer(inspection[field], f"{context}/{field}")
        for field in ("extensions_used", "extensions_required", "external_uris"):
            _bounded_string_array(inspection[field], f"{context}/{field}")
        metrics = _object(inspection["metrics"], f"{context}/metrics")
        metric_fields = {
            "nodes",
            "meshes",
            "materials",
            "textures",
            "skins",
            "bones",
            "influences",
            "animations",
            "vertices",
            "triangles",
            "external_uris",
        }
        _closed(metrics, metric_fields, f"{context}/metrics")
        for field in metric_fields:
            _integer(metrics[field], f"{context}/metrics/{field}")
    elif kind == "unavailable":
        _closed(inspection, {"kind", "reason"}, context)
        if inspection["reason"] not in {"identity_only", "unsupported_media_type"}:
            raise StudioContractError(f"{context}/reason is unknown")
    else:
        raise StudioContractError(f"{context}/kind is unknown")
    return inspection


def _validate_asset_catalog_result(method: str, value: object, context: str) -> None:
    result = _object(value, context)
    if method == "asset.catalog.list":
        _closed(
            result,
            {"manifest_revision", "offset", "limit", "entries", "next_offset"},
            context,
        )
        _sha256(result["manifest_revision"], f"{context}/manifest_revision")
        offset = _integer(result["offset"], f"{context}/offset")
        limit = _integer(result["limit"], f"{context}/limit", minimum=1)
        if limit > MAX_ASSET_CATALOG_PAGE:
            raise StudioContractError(f"{context}/limit must be at most {MAX_ASSET_CATALOG_PAGE}")
        entries = result["entries"]
        if not isinstance(entries, list) or len(entries) > limit:
            raise StudioContractError(f"{context}/entries exceeds the requested page")
        seen: set[str] = set()
        for index, entry in enumerate(entries):
            validated = _validate_asset_catalog_entry(entry, f"{context}/entries/{index}")
            if validated["entry_id"] in seen:
                raise StudioContractError(f"{context}/entries contains duplicate entry IDs")
            seen.add(validated["entry_id"])
        next_offset = result["next_offset"]
        if next_offset is not None:
            expected = offset + len(entries)
            if (
                isinstance(next_offset, bool)
                or not isinstance(next_offset, int)
                or next_offset != expected
                or len(entries) != limit
            ):
                raise StudioContractError(f"{context}/next_offset is inconsistent")
        return

    _closed(result, {"manifest_revision", "entry", "inspection"}, context)
    _sha256(result["manifest_revision"], f"{context}/manifest_revision")
    entry = _validate_asset_catalog_entry(result["entry"], f"{context}/entry")
    inspection = _validate_asset_inspection(result["inspection"], f"{context}/inspection")
    if inspection["kind"] == "unavailable":
        if entry["inspectable"] is not False:
            raise StudioContractError(f"{context}/entry cannot inspect unavailable media")
        if inspection["reason"] == "identity_only" and entry["path"] is not None:
            raise StudioContractError(f"{context}/inspection identity is inconsistent")
    elif entry["inspectable"] is not True:
        raise StudioContractError(f"{context}/entry must be inspectable")


def _validate_asset_preview_result(method: str, value: object, context: str) -> None:
    result = _object(value, context)
    if method == "asset.preview.open":
        _closed(
            result,
            {
                "handle",
                "manifest_revision",
                "entry_id",
                "media_type",
                "byte_length",
                "sha256",
                "chunk_bytes",
            },
            context,
        )
        _identifier(result["handle"], f"{context}/handle", ASSET_PREVIEW_HANDLE_PATTERN)
        _sha256(result["manifest_revision"], f"{context}/manifest_revision")
        _identifier(result["entry_id"], f"{context}/entry_id", ASSET_ENTRY_ID_PATTERN)
        if result["media_type"] not in ASSET_PREVIEW_MEDIA_TYPES:
            raise StudioContractError(f"{context}/media_type is not previewable")
        byte_length = _integer(result["byte_length"], f"{context}/byte_length", minimum=1)
        if byte_length > MAX_ASSET_PREVIEW_BYTES:
            raise StudioContractError(
                f"{context}/byte_length must be at most {MAX_ASSET_PREVIEW_BYTES}"
            )
        _sha256(result["sha256"], f"{context}/sha256")
        if (
            type(result["chunk_bytes"]) is not int
            or result["chunk_bytes"] != ASSET_PREVIEW_CHUNK_BYTES
        ):
            raise StudioContractError(f"{context}/chunk_bytes must be {ASSET_PREVIEW_CHUNK_BYTES}")
        return

    if method == "asset.preview.close":
        _closed(result, {"handle", "closed"}, context)
        _identifier(result["handle"], f"{context}/handle", ASSET_PREVIEW_HANDLE_PATTERN)
        if result["closed"] is not True:
            raise StudioContractError(f"{context}/closed must be true")
        return

    _closed(
        result,
        {
            "handle",
            "sequence",
            "data_base64",
            "byte_length",
            "cumulative_bytes",
            "cumulative_sha256",
            "eof",
        },
        context,
    )
    _identifier(result["handle"], f"{context}/handle", ASSET_PREVIEW_HANDLE_PATTERN)
    sequence = _integer(result["sequence"], f"{context}/sequence")
    if sequence > MAX_ASSET_PREVIEW_SEQUENCE:
        raise StudioContractError(
            f"{context}/sequence must be at most {MAX_ASSET_PREVIEW_SEQUENCE}"
        )
    encoded = _plain_string(
        result["data_base64"],
        f"{context}/data_base64",
        max_length=MAX_ASSET_PREVIEW_BASE64_LENGTH,
    )
    if not encoded:
        raise StudioContractError(f"{context}/data_base64 must be non-empty")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise StudioContractError(f"{context}/data_base64 must be canonical base64") from exc
    if base64.b64encode(decoded).decode("ascii") != encoded:
        raise StudioContractError(f"{context}/data_base64 must be canonical base64")
    byte_length = _integer(result["byte_length"], f"{context}/byte_length", minimum=1)
    if byte_length > ASSET_PREVIEW_CHUNK_BYTES or len(decoded) != byte_length:
        raise StudioContractError(f"{context}/byte_length does not match preview data")
    cumulative_bytes = _integer(
        result["cumulative_bytes"],
        f"{context}/cumulative_bytes",
        minimum=1,
    )
    expected_cumulative = sequence * ASSET_PREVIEW_CHUNK_BYTES + byte_length
    if cumulative_bytes != expected_cumulative or cumulative_bytes > MAX_ASSET_PREVIEW_BYTES:
        raise StudioContractError(f"{context}/cumulative_bytes is inconsistent")
    _sha256(result["cumulative_sha256"], f"{context}/cumulative_sha256")
    eof = _boolean(result["eof"], f"{context}/eof")
    if not eof and byte_length != ASSET_PREVIEW_CHUNK_BYTES:
        raise StudioContractError(f"{context}/byte_length must fill non-final chunks")


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


def _validate_external_job_input(operation: str, value: object, context: str) -> None:
    job_input = _object(value, context)
    hash_field = {
        "game.materialize": "expected_materialization_hash",
        "game.package": "expected_game_hash",
        "game.package.extract": "expected_package_hash",
    }[operation]
    fields = {"source_grant_id", "target_grant_id", hash_field}
    missing = fields - set(job_input)
    unknown = set(job_input) - fields
    if missing or unknown:
        invalid = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(invalid))}")
    source = _identifier(
        job_input["source_grant_id"],
        f"{context}/source_grant_id",
        ENTITY_ID_PATTERN,
    )
    target = _identifier(
        job_input["target_grant_id"],
        f"{context}/target_grant_id",
        ENTITY_ID_PATTERN,
    )
    if source == target:
        raise StudioContractError(f"{context} source and target grants must differ")
    _sha256(job_input[hash_field], f"{context}/{hash_field}")


def _validate_external_job_result(
    operation: str,
    value: object,
    context: str,
) -> None:
    result = _object(value, context)
    fields = {
        "game.materialize": {
            "operation",
            "game_id",
            "standalone_hash",
            "payload_lock_hash",
            "runtime_bundle_hash",
            "target_grant_id",
        },
        "game.package": {
            "operation",
            "package_id",
            "content_hash",
            "archive_sha256",
            "game_id",
            "game_hash",
            "target_grant_id",
        },
        "game.package.extract": {
            "operation",
            "package_id",
            "package_hash",
            "archive_sha256",
            "game_id",
            "game_hash",
            "payload_lock_hash",
            "target_grant_id",
        },
    }[operation]
    missing = fields - set(result)
    unknown = set(result) - fields
    if missing or unknown:
        invalid = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(invalid))}")
    if result["operation"] != operation:
        raise StudioContractError(f"{context}/operation is invalid")
    for field in fields - {"operation", "target_grant_id"}:
        if field.endswith("_id") or field == "game_id":
            _identifier(result[field], f"{context}/{field}", ENTITY_ID_PATTERN)
        else:
            _sha256(result[field], f"{context}/{field}")
    _identifier(
        result["target_grant_id"],
        f"{context}/target_grant_id",
        ENTITY_ID_PATTERN,
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


def validate_studio_recovery_evidence(value: object, context: str) -> dict[str, Any]:
    evidence = _object(value, context)
    if not evidence or not set(evidence) <= {"stage", "journal"}:
        raise StudioContractError(f"{context} must identify a stage or journal")
    for kind, raw in evidence.items():
        item = _object(raw, f"{context}/{kind}")
        _closed(item, {"locator", "identity", "retention"}, f"{context}/{kind}")
        locator = _plain_string(
            item["locator"],
            f"{context}/{kind}/locator",
            max_length=255,
        )
        if (
            not locator
            or locator in {".", ".."}
            or "/" in locator
            or "\\" in locator
            or "\x00" in locator
            or unicodedata.normalize("NFC", locator) != locator
        ):
            raise StudioContractError(f"{context}/{kind}/locator is not a private basename")
        identity = item["identity"]
        if identity is not None:
            if not isinstance(identity, list) or len(identity) != 2:
                raise StudioContractError(f"{context}/{kind}/identity is invalid")
            for index, number in enumerate(identity):
                _integer(
                    number,
                    f"{context}/{kind}/identity/{index}",
                    maximum=9_007_199_254_740_991,
                )
        if item["retention"] != "active":
            raise StudioContractError(f"{context}/{kind}/retention is invalid")
    return evidence


def _validate_job_error(
    value: object,
    context: str,
    *,
    codes: frozenset[str] = JOB_ERROR_CODES,
) -> None:
    error = _object(value, context)
    fields = {"code", "message"}
    if "recovery_evidence" in error:
        fields.add("recovery_evidence")
    _closed(error, fields, context)
    if error["code"] not in codes:
        raise StudioContractError(f"{context}/code is unknown")
    message = _string(error["message"], f"{context}/message")
    assert message is not None
    if len(message) > 512:
        raise StudioContractError(f"{context}/message must contain at most 512 characters")
    if "recovery_evidence" in error:
        validate_studio_recovery_evidence(
            error["recovery_evidence"],
            f"{context}/recovery_evidence",
        )


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
    executable_operations = MANAGED_JOB_OPERATIONS | EXTERNAL_JOB_OPERATIONS
    if not isinstance(operation, str) or operation not in executable_operations:
        raise StudioContractError("job.create params/operation is not an executable operation")
    if operation in MANAGED_JOB_OPERATIONS:
        _validate_job_input(operation, params["input"], "job.create params/input")
    else:
        _validate_external_job_input(operation, params["input"], "job.create params/input")
    return params


def validate_studio_external_grant(value: object) -> dict[str, Any]:
    grant = _object(value, "external grant")
    fields = {
        "format",
        "format_version",
        "grant_id",
        "workspace_id",
        "operation",
        "role",
        "artifact_kind",
        "display_name",
        "state",
        "expected_content_hash",
        "created_at",
        "updated_at",
    }
    _closed(grant, fields, "external grant")
    if grant["format"] != EXTERNAL_GRANT_FORMAT:
        raise StudioContractError("external grant format is unsupported")
    if type(grant["format_version"]) is not int or grant["format_version"] != 1:
        raise StudioContractError("external grant format_version must be 1")
    _identifier(grant["grant_id"], "external grant/grant_id", ENTITY_ID_PATTERN)
    _identifier(
        grant["workspace_id"],
        "external grant/workspace_id",
        WORKSPACE_ID_PATTERN,
    )
    operation = grant["operation"]
    if not isinstance(operation, str) or operation not in EXTERNAL_JOB_OPERATIONS:
        raise StudioContractError("external grant/operation is unknown")
    role = grant["role"]
    if not isinstance(role, str) or role not in EXTERNAL_GRANT_ROLES:
        raise StudioContractError("external grant/role is unknown")
    artifact_kind = grant["artifact_kind"]
    if (
        not isinstance(artifact_kind, str)
        or artifact_kind not in EXTERNAL_ARTIFACT_KINDS
        or EXTERNAL_OPERATION_KINDS[operation][role] != artifact_kind
    ):
        raise StudioContractError("external grant/artifact_kind is invalid for its operation")
    display_name = _plain_string(
        grant["display_name"],
        "external grant/display_name",
        max_length=128,
    )
    if (
        not display_name
        or unicodedata.normalize("NFC", display_name) != display_name
        or any(character in display_name for character in "/\\\x00\r\n")
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in display_name)
    ):
        raise StudioContractError("external grant/display_name is not a safe label")
    if not isinstance(grant["state"], str) or grant["state"] not in EXTERNAL_GRANT_STATES:
        raise StudioContractError("external grant/state is unknown")
    _sha256(
        grant["expected_content_hash"],
        "external grant/expected_content_hash",
        nullable=True,
    )
    _timestamp(grant["created_at"], "external grant/created_at")
    _timestamp(grant["updated_at"], "external grant/updated_at")
    return grant


def _validate_creation_project_identity(value: object, context: str) -> dict[str, Any]:
    identity = _object(value, context)
    _closed(identity, {"format", "format_version", "id", "content_hash"}, context)
    if identity["format"] != "world-forge.project":
        raise StudioContractError(f"{context}/format must be world-forge.project")
    if type(identity["format_version"]) is not int or identity["format_version"] != 1:
        raise StudioContractError(f"{context}/format_version must be 1")
    _identifier(identity["id"], f"{context}/id", ENTITY_ID_PATTERN)
    _sha256(identity["content_hash"], f"{context}/content_hash")
    return identity


def validate_studio_creation_root_grant(value: object) -> dict[str, Any]:
    grant = _object(value, "creation root grant")
    fields = {
        "format",
        "format_version",
        "grant_id",
        "role",
        "display_name",
        "state",
        "expected_target_state",
        "expected_project",
        "generation",
        "created_at",
        "updated_at",
    }
    _closed(grant, fields, "creation root grant")
    if grant["format"] != CREATION_ROOT_GRANT_FORMAT:
        raise StudioContractError("creation root grant format is unsupported")
    if type(grant["format_version"]) is not int or grant["format_version"] != 1:
        raise StudioContractError("creation root grant format_version must be 1")
    _identifier(grant["grant_id"], "creation root grant/grant_id", ENTITY_ID_PATTERN)
    role = grant["role"]
    if not isinstance(role, str) or role not in CREATION_ROOT_GRANT_ROLES:
        raise StudioContractError("creation root grant/role is unknown")
    display_name = _plain_string(
        grant["display_name"],
        "creation root grant/display_name",
        max_length=128,
    )
    if (
        not display_name
        or unicodedata.normalize("NFC", display_name) != display_name
        or any(character in display_name for character in "/\\\x00\r\n")
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in display_name)
    ):
        raise StudioContractError("creation root grant/display_name is not a safe label")
    if not isinstance(grant["state"], str) or grant["state"] not in CREATION_ROOT_GRANT_STATES:
        raise StudioContractError("creation root grant/state is unknown")
    target_state = grant["expected_target_state"]
    expected_project = grant["expected_project"]
    if role == "existing_root":
        if target_state != "existing_project":
            raise StudioContractError(
                "creation root grant target state must be existing_project for existing_root"
            )
        if expected_project is None:
            raise StudioContractError(
                "creation root grant expected_project is required for existing_root"
            )
        _validate_creation_project_identity(
            expected_project,
            "creation root grant/expected_project",
        )
    else:
        if target_state != "absent":
            raise StudioContractError(
                "creation root grant target state must be absent for new_target"
            )
        if expected_project is not None:
            raise StudioContractError(
                "creation root grant expected_project must be null for new_target"
            )
    _integer(grant["generation"], "creation root grant/generation")
    _timestamp(grant["created_at"], "creation root grant/created_at")
    _timestamp(grant["updated_at"], "creation root grant/updated_at")
    return grant


def _validate_creation_output_publication(
    value: object,
    context: str,
    *,
    grant_version: int = 1,
) -> dict[str, Any]:
    publication = _object(value, context)
    fields = {"format", "format_version", "id", "content_hash"}
    if grant_version == 1:
        fields.add("inventory_hash")
    elif grant_version in {2, 3, 4, 6}:
        fields.add("tree_hash")
    else:
        fields.update({"archive_sha256", "size_bytes"})
    _closed(publication, fields, context)
    expected_format = {
        1: "world-forge.assetpack",
        2: "world-forge.game_runtime_bundle",
        3: "world-forge.game_materialization_bundle",
        4: "world-forge.standalone_game",
        5: "world-forge.game_package",
        6: "world-forge.headless_evidence_set",
    }[grant_version]
    if publication["format"] != expected_format:
        raise StudioContractError(f"{context}/format must be {expected_format}")
    if type(publication["format_version"]) is not int or publication["format_version"] != 1:
        raise StudioContractError(f"{context}/format_version must be 1")
    _identifier(publication["id"], f"{context}/id", ENTITY_ID_PATTERN)
    _sha256(publication["content_hash"], f"{context}/content_hash")
    if grant_version == 5:
        _sha256(publication["archive_sha256"], f"{context}/archive_sha256")
        _integer(
            publication["size_bytes"],
            f"{context}/size_bytes",
            minimum=1,
            maximum=MAX_CREATION_GAME_PACKAGE_BYTES,
        )
    else:
        digest_field = "inventory_hash" if grant_version == 1 else "tree_hash"
        _sha256(publication[digest_field], f"{context}/{digest_field}")
    return publication


def _validate_studio_creation_output_grant(
    value: object,
    *,
    allow_v6: bool,
) -> dict[str, Any]:
    grant = _object(value, "creation output grant")
    fields = {
        "format",
        "format_version",
        "grant_id",
        "workspace_id",
        "kind",
        "display_name",
        "state",
        "generation",
        "publication",
        "created_at",
        "updated_at",
    }
    _closed(grant, fields, "creation output grant")
    if grant["format"] != CREATION_OUTPUT_GRANT_FORMAT:
        raise StudioContractError("creation output grant format is unsupported")
    version = grant["format_version"]
    supported_versions = {1, 2, 3, 4, 5, 6} if allow_v6 else {1, 2, 3, 4, 5}
    if type(version) is not int or version not in supported_versions:
        maximum = "1, 2, 3, 4, 5, or 6" if allow_v6 else "1, 2, 3, 4, or 5"
        raise StudioContractError(f"creation output grant format_version must be {maximum}")
    _identifier(grant["grant_id"], "creation output grant/grant_id", ENTITY_ID_PATTERN)
    _identifier(
        grant["workspace_id"],
        "creation output grant/workspace_id",
        WORKSPACE_ID_PATTERN,
    )
    expected_kind = {
        1: "generic_assetpack_directory",
        2: "game_runtime_bundle_directory",
        3: "game_materialization_bundle_directory",
        4: "standalone_game_directory",
        5: "game_package_file",
        6: "headless_evidence_directory",
    }[version]
    if grant["kind"] != expected_kind:
        raise StudioContractError("creation output grant/kind is unknown for its version")
    display_name = _plain_string(
        grant["display_name"],
        "creation output grant/display_name",
        max_length=128,
    )
    if (
        not display_name
        or unicodedata.normalize("NFC", display_name) != display_name
        or any(character in display_name for character in "/\\\x00\r\n")
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in display_name)
    ):
        raise StudioContractError("creation output grant/display_name is not a safe label")
    state = grant["state"]
    if state not in CREATION_OUTPUT_GRANT_STATES:
        raise StudioContractError("creation output grant/state is unknown")
    _integer(grant["generation"], "creation output grant/generation")
    publication = grant["publication"]
    if state == "published":
        if publication is None:
            raise StudioContractError("published creation output grant requires publication")
        _validate_creation_output_publication(
            publication,
            "creation output grant/publication",
            grant_version=version,
        )
    elif publication is not None:
        raise StudioContractError("unpublished creation output grant cannot cite publication")
    created_at = _timestamp(grant["created_at"], "creation output grant/created_at")
    updated_at = _timestamp(grant["updated_at"], "creation output grant/updated_at")
    if updated_at < created_at:
        raise StudioContractError("creation output grant/updated_at precedes created_at")
    return grant


def validate_studio_creation_output_grant(value: object) -> dict[str, Any]:
    """Validate the published v1-v5 output-grant reader contract."""

    return _validate_studio_creation_output_grant(value, allow_v6=False)


def validate_studio_creation_output_grant_v6(value: object) -> dict[str, Any]:
    """Validate the additive v1-v6 output-grant contract used by protocol v5."""

    return _validate_studio_creation_output_grant(value, allow_v6=True)


def validate_studio_creation_preview(value: object) -> dict[str, Any]:
    preview = _object(value, "creation preview")
    fields = {
        "format",
        "format_version",
        "handle",
        "workspace_id",
        "assetpack_artifact_id",
        "output_grant_id",
        "output_grant_generation",
        "asset_id",
        "media_type",
        "byte_length",
        "sha256",
        "chunk_bytes",
        "metadata",
    }
    _closed(preview, fields, "creation preview")
    if preview["format"] != CREATION_PREVIEW_FORMAT:
        raise StudioContractError("creation preview format is unsupported")
    if type(preview["format_version"]) is not int or preview["format_version"] != 1:
        raise StudioContractError("creation preview format_version must be 1")
    _identifier(preview["handle"], "creation preview/handle", ASSET_PREVIEW_HANDLE_PATTERN)
    _identifier(
        preview["workspace_id"],
        "creation preview/workspace_id",
        WORKSPACE_ID_PATTERN,
    )
    for field in ("assetpack_artifact_id", "output_grant_id", "asset_id"):
        _identifier(preview[field], f"creation preview/{field}", ENTITY_ID_PATTERN)
    _integer(
        preview["output_grant_generation"],
        "creation preview/output_grant_generation",
    )
    media_type = preview["media_type"]
    if media_type not in {"audio/wav", "image/png"}:
        raise StudioContractError("creation preview/media_type must be PNG or WAV")
    _integer(
        preview["byte_length"],
        "creation preview/byte_length",
        minimum=1,
        maximum=MAX_CREATION_PREVIEW_BYTES,
    )
    _sha256(preview["sha256"], "creation preview/sha256")
    if preview["chunk_bytes"] != CREATION_PREVIEW_CHUNK_BYTES:
        raise StudioContractError(
            f"creation preview/chunk_bytes must be {CREATION_PREVIEW_CHUNK_BYTES}"
        )
    metadata = _object(preview["metadata"], "creation preview/metadata")
    if media_type == "image/png":
        _closed(metadata, {"kind", "width", "height", "mode"}, "creation preview/metadata")
        if metadata["kind"] != "png" or metadata["mode"] not in {
            "rgba8",
            "rgb8",
            "grayscale8",
        }:
            raise StudioContractError("creation preview PNG metadata is invalid")
        _integer(metadata["width"], "creation preview/metadata/width", minimum=1, maximum=16384)
        _integer(
            metadata["height"],
            "creation preview/metadata/height",
            minimum=1,
            maximum=16384,
        )
    else:
        _closed(
            metadata,
            {"kind", "channels", "sample_rate", "frames", "sample_width"},
            "creation preview/metadata",
        )
        if (
            metadata["kind"] != "wav_pcm16"
            or metadata["channels"] not in {1, 2}
            or metadata["sample_width"] != 2
        ):
            raise StudioContractError("creation preview WAV metadata is invalid")
        _integer(
            metadata["sample_rate"],
            "creation preview/metadata/sample_rate",
            minimum=8000,
            maximum=192000,
        )
        _integer(
            metadata["frames"],
            "creation preview/metadata/frames",
            minimum=1,
            maximum=192000000,
        )
    return preview


def validate_studio_creation_preview_v2(value: object) -> dict[str, Any]:
    """Validate the additive pathless QA-review candidate preview contract."""

    preview = _object(value, "creation preview v2")
    fields = {
        "format",
        "format_version",
        "handle",
        "workspace_id",
        "source",
        "media_type",
        "byte_length",
        "sha256",
        "chunk_bytes",
        "metadata",
    }
    _closed(preview, fields, "creation preview v2")
    if preview["format"] != CREATION_PREVIEW_FORMAT:
        raise StudioContractError("creation preview v2 format is unsupported")
    if type(preview["format_version"]) is not int or preview["format_version"] != 2:
        raise StudioContractError("creation preview v2 format_version must be 2")
    _identifier(preview["handle"], "creation preview v2/handle", ASSET_PREVIEW_HANDLE_PATTERN)
    _identifier(
        preview["workspace_id"],
        "creation preview v2/workspace_id",
        WORKSPACE_ID_PATTERN,
    )
    source = _object(preview["source"], "creation preview v2/source")
    _closed(
        source,
        {"kind", "qa_report_artifact_id", "asset_id", "output_role"},
        "creation preview v2/source",
    )
    if source["kind"] != "qa_review_candidate":
        raise StudioContractError("creation preview v2/source kind is unsupported")
    for field in ("qa_report_artifact_id", "asset_id", "output_role"):
        _identifier(source[field], f"creation preview v2/source/{field}", ENTITY_ID_PATTERN)
    media_type = preview["media_type"]
    if media_type not in {"audio/wav", "image/png"}:
        raise StudioContractError("creation preview v2/media_type must be PNG or WAV")
    _integer(
        preview["byte_length"],
        "creation preview v2/byte_length",
        minimum=1,
        maximum=MAX_CREATION_PREVIEW_BYTES,
    )
    _sha256(preview["sha256"], "creation preview v2/sha256")
    if preview["chunk_bytes"] != CREATION_PREVIEW_CHUNK_BYTES:
        raise StudioContractError(
            f"creation preview v2/chunk_bytes must be {CREATION_PREVIEW_CHUNK_BYTES}"
        )
    metadata = _object(preview["metadata"], "creation preview v2/metadata")
    if media_type == "image/png":
        _closed(metadata, {"kind", "width", "height", "mode"}, "creation preview v2/metadata")
        if metadata["kind"] != "png" or metadata["mode"] not in {
            "rgba8",
            "rgb8",
            "grayscale8",
        }:
            raise StudioContractError("creation preview v2 PNG metadata is invalid")
        _integer(
            metadata["width"],
            "creation preview v2/metadata/width",
            minimum=1,
            maximum=16384,
        )
        _integer(
            metadata["height"],
            "creation preview v2/metadata/height",
            minimum=1,
            maximum=16384,
        )
    else:
        _closed(
            metadata,
            {"kind", "channels", "sample_rate", "frames", "sample_width"},
            "creation preview v2/metadata",
        )
        if (
            metadata["kind"] != "wav_pcm16"
            or metadata["channels"] not in {1, 2}
            or metadata["sample_width"] != 2
        ):
            raise StudioContractError("creation preview v2 WAV metadata is invalid")
        _integer(
            metadata["sample_rate"],
            "creation preview v2/metadata/sample_rate",
            minimum=8000,
            maximum=192000,
        )
        _integer(
            metadata["frames"],
            "creation preview v2/metadata/frames",
            minimum=1,
            maximum=192000000,
        )
    return preview


def validate_studio_creation_workspace(value: object) -> dict[str, Any]:
    workspace = _object(value, "creation workspace")
    fields = {
        "format",
        "format_version",
        "workspace_id",
        "project",
        "project_kind",
        "source_revision",
        "workflow_status_hash",
        "root_generation",
        "created_at",
        "updated_at",
    }
    _closed(workspace, fields, "creation workspace")
    if workspace["format"] != CREATION_WORKSPACE_FORMAT:
        raise StudioContractError("creation workspace format is unsupported")
    if type(workspace["format_version"]) is not int or workspace["format_version"] != 1:
        raise StudioContractError("creation workspace format_version must be 1")
    _identifier(
        workspace["workspace_id"],
        "creation workspace/workspace_id",
        WORKSPACE_ID_PATTERN,
    )
    _validate_creation_project_identity(workspace["project"], "creation workspace/project")
    if workspace["project_kind"] not in CREATION_PROJECT_KINDS:
        raise StudioContractError("creation workspace/project_kind is unsupported")
    _sha256(workspace["source_revision"], "creation workspace/source_revision")
    _sha256(
        workspace["workflow_status_hash"],
        "creation workspace/workflow_status_hash",
        nullable=True,
    )
    _integer(workspace["root_generation"], "creation workspace/root_generation")
    _timestamp(workspace["created_at"], "creation workspace/created_at")
    _timestamp(workspace["updated_at"], "creation workspace/updated_at")
    return workspace


def _validate_creation_artifact_identity(value: object, context: str) -> dict[str, Any]:
    identity = _object(value, context)
    _closed(identity, {"format", "format_version", "id", "content_hash"}, context)
    _identifier(identity["format"], f"{context}/format", OPERATION_PATTERN)
    if type(identity["format_version"]) is not int or identity["format_version"] != 1:
        raise StudioContractError(f"{context}/format_version must be 1")
    _identifier(identity["id"], f"{context}/id", ENTITY_ID_PATTERN)
    _sha256(identity["content_hash"], f"{context}/content_hash")
    return identity


def _validate_creation_artifact_authority(value: object, context: str) -> dict[str, Any]:
    authority = _object(value, context)
    _closed(
        authority,
        {"workspace_id", "root_generation", "source_revision", "workflow_status_hash"},
        context,
    )
    _identifier(authority["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _integer(authority["root_generation"], f"{context}/root_generation")
    _sha256(authority["source_revision"], f"{context}/source_revision")
    _sha256(
        authority["workflow_status_hash"],
        f"{context}/workflow_status_hash",
        nullable=True,
    )
    return authority


def _validate_creation_job_authority(value: object, context: str) -> dict[str, Any]:
    authority = _object(value, context)
    _closed(
        authority,
        {
            "root_generation",
            "source_revision",
            "workflow_status_hash",
            "artifact_snapshot_hash",
        },
        context,
    )
    _integer(authority["root_generation"], f"{context}/root_generation")
    _sha256(authority["source_revision"], f"{context}/source_revision")
    _sha256(
        authority["workflow_status_hash"],
        f"{context}/workflow_status_hash",
        nullable=True,
    )
    _sha256(authority["artifact_snapshot_hash"], f"{context}/artifact_snapshot_hash")
    return authority


def _validate_creation_job_input(value: object, context: str) -> dict[str, Any]:
    item = _object(value, context)
    _closed(item, {"artifact_id", "subject"}, context)
    _identifier(item["artifact_id"], f"{context}/artifact_id", ENTITY_ID_PATTERN)
    _validate_creation_artifact_identity(item["subject"], f"{context}/subject")
    return item


def _validate_asset_process_operation_params(value: object, context: str) -> dict[str, Any]:
    params = _object(value, context)
    _closed(
        params,
        {
            "license_artifact_ids",
            "recipe_id",
            "processing_receipt_id",
            "qa_report_id",
            "acceptance_results",
        },
        context,
    )
    licenses = params["license_artifact_ids"]
    if not isinstance(licenses, list) or not 1 <= len(licenses) <= 4:
        raise StudioContractError(f"{context}/license_artifact_ids must contain 1 to 4 entries")
    checked_licenses = [
        _identifier(item, f"{context}/license_artifact_ids/{index}", ENTITY_ID_PATTERN)
        for index, item in enumerate(licenses)
    ]
    if checked_licenses != sorted(set(checked_licenses), key=lambda item: item.encode("utf-8")):
        raise StudioContractError(f"{context}/license_artifact_ids must be unique and canonical")
    for field in ("recipe_id", "processing_receipt_id", "qa_report_id"):
        _identifier(params[field], f"{context}/{field}", ENTITY_ID_PATTERN)
    acceptance = params["acceptance_results"]
    if (
        not isinstance(acceptance, list)
        or not 1 <= len(acceptance) <= MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
    ):
        raise StudioContractError(f"{context}/acceptance_results is invalid")
    criterion_hashes: list[str] = []
    for index, raw in enumerate(acceptance):
        item = _object(raw, f"{context}/acceptance_results/{index}")
        _closed(
            item,
            {"criterion_index", "criterion_sha256", "status", "evidence_hashes"},
            f"{context}/acceptance_results/{index}",
        )
        if item["criterion_index"] != index:
            raise StudioContractError(
                f"{context}/acceptance_results/{index}/criterion_index is not canonical"
            )
        criterion_hashes.append(
            _sha256(
                item["criterion_sha256"],
                f"{context}/acceptance_results/{index}/criterion_sha256",
            )
        )
        if item["status"] not in {"passed", "failed"}:
            raise StudioContractError(f"{context}/acceptance_results/{index}/status is unknown")
        evidence = item["evidence_hashes"]
        if (
            not isinstance(evidence, list)
            or not 1 <= len(evidence) <= MAX_GENERIC_ASSET_ACCEPTANCE_ITEMS
        ):
            raise StudioContractError(
                f"{context}/acceptance_results/{index}/evidence_hashes is invalid"
            )
        checked_evidence = [
            _sha256(
                digest,
                f"{context}/acceptance_results/{index}/evidence_hashes/{evidence_index}",
            )
            for evidence_index, digest in enumerate(evidence)
        ]
        if checked_evidence != sorted(set(checked_evidence)):
            raise StudioContractError(
                f"{context}/acceptance_results/{index}/evidence_hashes must be canonical"
            )
    if len(criterion_hashes) != len(set(criterion_hashes)):
        raise StudioContractError(f"{context}/acceptance_results criterion hashes repeat")
    return params


def _validate_asset_release_seal_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    _closed(
        params,
        {
            "qa_report_artifact_ids",
            "manifest_id",
            "target_grant_id",
            "target_grant_generation",
        },
        context,
    )
    qa_ids = params["qa_report_artifact_ids"]
    if not isinstance(qa_ids, list) or not 1 <= len(qa_ids) <= MAX_CREATION_JOB_INPUTS:
        raise StudioContractError(
            f"{context}/qa_report_artifact_ids must contain 1 to {MAX_CREATION_JOB_INPUTS} entries"
        )
    checked = [
        _identifier(item, f"{context}/qa_report_artifact_ids/{index}", ENTITY_ID_PATTERN)
        for index, item in enumerate(qa_ids)
    ]
    if checked != sorted(set(checked), key=lambda item: item.encode("utf-8")):
        raise StudioContractError(f"{context}/qa_report_artifact_ids must be unique and canonical")
    _identifier(params["manifest_id"], f"{context}/manifest_id", ENTITY_ID_PATTERN)
    _identifier(
        params["target_grant_id"],
        f"{context}/target_grant_id",
        ENTITY_ID_PATTERN,
    )
    _integer(params["target_grant_generation"], f"{context}/target_grant_generation")
    return params


def _validate_runtime_compose_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    _closed(
        params,
        {
            "gamepack_artifact_id",
            "asset_inventory_artifact_id",
            "assetpack_artifact_id",
            "target_grant_id",
            "target_grant_generation",
        },
        context,
    )
    for field in (
        "gamepack_artifact_id",
        "asset_inventory_artifact_id",
        "assetpack_artifact_id",
        "target_grant_id",
    ):
        _identifier(params[field], f"{context}/{field}", ENTITY_ID_PATTERN)
    if (
        len(
            {
                params["gamepack_artifact_id"],
                params["asset_inventory_artifact_id"],
                params["assetpack_artifact_id"],
            }
        )
        != 3
    ):
        raise StudioContractError(f"{context} artifact IDs must be distinct")
    _integer(params["target_grant_generation"], f"{context}/target_grant_generation")
    return params


def _validate_runtime_bundle_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    artifact_fields = (
        "gamepack_artifact_id",
        "asset_inventory_artifact_id",
        "assetpack_artifact_id",
        "runtime_snapshot_artifact_id",
        "runtime_adapter_registry_artifact_id",
        "runtime_composition_artifact_id",
        "runtime_support_report_artifact_id",
    )
    _closed(
        params,
        {
            *artifact_fields,
            "source_grant_id",
            "source_grant_generation",
            "target_grant_id",
            "target_grant_generation",
        },
        context,
    )
    artifact_ids = [
        _identifier(params[field], f"{context}/{field}", ENTITY_ID_PATTERN)
        for field in artifact_fields
    ]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise StudioContractError(f"{context} artifact IDs must be distinct")
    source_grant_id = _identifier(
        params["source_grant_id"],
        f"{context}/source_grant_id",
        ENTITY_ID_PATTERN,
    )
    target_grant_id = _identifier(
        params["target_grant_id"],
        f"{context}/target_grant_id",
        ENTITY_ID_PATTERN,
    )
    if source_grant_id == target_grant_id:
        raise StudioContractError(f"{context} source and target grants must be distinct")
    _integer(params["source_grant_generation"], f"{context}/source_grant_generation")
    _integer(params["target_grant_generation"], f"{context}/target_grant_generation")
    return params


def _validate_materialization_bundle_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    _closed(
        params,
        {
            "runtime_bundle_artifact_id",
            "source_grant_id",
            "source_grant_generation",
            "target_grant_id",
            "target_grant_generation",
        },
        context,
    )
    _identifier(
        params["runtime_bundle_artifact_id"],
        f"{context}/runtime_bundle_artifact_id",
        ENTITY_ID_PATTERN,
    )
    source_grant_id = _identifier(
        params["source_grant_id"],
        f"{context}/source_grant_id",
        ENTITY_ID_PATTERN,
    )
    target_grant_id = _identifier(
        params["target_grant_id"],
        f"{context}/target_grant_id",
        ENTITY_ID_PATTERN,
    )
    if source_grant_id == target_grant_id:
        raise StudioContractError(f"{context} source and target grants must be distinct")
    _integer(params["source_grant_generation"], f"{context}/source_grant_generation")
    _integer(params["target_grant_generation"], f"{context}/target_grant_generation")
    return params


def _validate_game_materialize_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    _closed(
        params,
        {
            "materialization_bundle_artifact_id",
            "source_grant_id",
            "source_grant_generation",
            "target_grant_id",
            "target_grant_generation",
        },
        context,
    )
    _identifier(
        params["materialization_bundle_artifact_id"],
        f"{context}/materialization_bundle_artifact_id",
        ENTITY_ID_PATTERN,
    )
    source_grant_id = _identifier(
        params["source_grant_id"],
        f"{context}/source_grant_id",
        ENTITY_ID_PATTERN,
    )
    target_grant_id = _identifier(
        params["target_grant_id"],
        f"{context}/target_grant_id",
        ENTITY_ID_PATTERN,
    )
    if source_grant_id == target_grant_id:
        raise StudioContractError(f"{context} source and target grants must be distinct")
    _integer(params["source_grant_generation"], f"{context}/source_grant_generation")
    _integer(params["target_grant_generation"], f"{context}/target_grant_generation")
    return params


def _validate_game_package_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    _closed(
        params,
        {
            "standalone_game_artifact_id",
            "source_grant_id",
            "source_grant_generation",
            "target_grant_id",
            "target_grant_generation",
        },
        context,
    )
    _identifier(
        params["standalone_game_artifact_id"],
        f"{context}/standalone_game_artifact_id",
        ENTITY_ID_PATTERN,
    )
    source_grant_id = _identifier(
        params["source_grant_id"],
        f"{context}/source_grant_id",
        ENTITY_ID_PATTERN,
    )
    target_grant_id = _identifier(
        params["target_grant_id"],
        f"{context}/target_grant_id",
        ENTITY_ID_PATTERN,
    )
    if source_grant_id == target_grant_id:
        raise StudioContractError(f"{context} source and target grants must be distinct")
    _integer(params["source_grant_generation"], f"{context}/source_grant_generation")
    _integer(params["target_grant_generation"], f"{context}/target_grant_generation")
    return params


def _validate_game_package_extract_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    _closed(
        params,
        {
            "game_package_artifact_id",
            "source_grant_id",
            "source_grant_generation",
            "target_grant_id",
            "target_grant_generation",
        },
        context,
    )
    _identifier(
        params["game_package_artifact_id"],
        f"{context}/game_package_artifact_id",
        ENTITY_ID_PATTERN,
    )
    source_grant_id = _identifier(
        params["source_grant_id"],
        f"{context}/source_grant_id",
        ENTITY_ID_PATTERN,
    )
    target_grant_id = _identifier(
        params["target_grant_id"],
        f"{context}/target_grant_id",
        ENTITY_ID_PATTERN,
    )
    if source_grant_id == target_grant_id:
        raise StudioContractError(f"{context} source and target grants must be distinct")
    _integer(params["source_grant_generation"], f"{context}/source_grant_generation")
    _integer(params["target_grant_generation"], f"{context}/target_grant_generation")
    return params


def _validate_asset_qa_review_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    _closed(
        params,
        {
            "qa_report_artifact_id",
            "output_role",
            "review_receipt_id",
            "decisions",
            "blockers",
        },
        context,
    )
    for field in ("qa_report_artifact_id", "output_role", "review_receipt_id"):
        _identifier(params[field], f"{context}/{field}", ENTITY_ID_PATTERN)
    decisions = params["decisions"]
    if not isinstance(decisions, list) or not 1 <= len(decisions) <= 64:
        raise StudioContractError(f"{context}/decisions must contain 1 to 64 entries")
    for index, decision in enumerate(decisions):
        if decision not in {"approved", "rejected"}:
            raise StudioContractError(f"{context}/decisions/{index} is unknown")
    _validate_canonical_public_token_array(
        params["blockers"],
        f"{context}/blockers",
        maximum=64,
    )
    return params


def _validate_asset_release_authorize_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    _closed(
        params,
        {
            "review_receipt_artifact_ids",
            "manifest_id",
            "assetpack_id",
            "release_authority_id",
            "blockers",
            "target_grant_id",
            "target_grant_generation",
        },
        context,
    )
    review_ids = params["review_receipt_artifact_ids"]
    if not isinstance(review_ids, list) or not 1 <= len(review_ids) <= MAX_CREATION_JOB_INPUTS:
        raise StudioContractError(
            f"{context}/review_receipt_artifact_ids must contain "
            f"1 to {MAX_CREATION_JOB_INPUTS} entries"
        )
    checked_ids = [
        _identifier(item, f"{context}/review_receipt_artifact_ids/{index}", ENTITY_ID_PATTERN)
        for index, item in enumerate(review_ids)
    ]
    if checked_ids != sorted(set(checked_ids), key=lambda item: item.encode("utf-8")):
        raise StudioContractError(
            f"{context}/review_receipt_artifact_ids must be unique and canonical"
        )
    for field in ("manifest_id", "assetpack_id", "release_authority_id"):
        _identifier(params[field], f"{context}/{field}", ENTITY_ID_PATTERN)
    _identifier(params["target_grant_id"], f"{context}/target_grant_id", ENTITY_ID_PATTERN)
    _integer(params["target_grant_generation"], f"{context}/target_grant_generation")
    _validate_canonical_public_token_array(
        params["blockers"],
        f"{context}/blockers",
        maximum=64,
    )
    return params


def _validate_runtime_headless_verify_operation_params(
    value: object,
    context: str,
) -> dict[str, Any]:
    params = _object(value, context)
    artifact_fields = (
        "gamepack_artifact_id",
        "asset_inventory_artifact_id",
        "assetpack_artifact_id",
        "asset_release_authority_artifact_id",
        "runtime_snapshot_artifact_id",
        "runtime_adapter_registry_artifact_id",
        "runtime_composition_artifact_id",
        "runtime_bundle_artifact_id",
        "headless_script_artifact_id",
    )
    fields = {
        *artifact_fields,
        "source_grant_id",
        "expected_source_grant_generation",
        "target_grant_id",
        "expected_target_grant_generation",
        "platform_id",
    }
    missing = fields - set(params)
    unknown = set(params) - fields
    if missing or unknown:
        raise StudioContractError(
            f"{context} has invalid fields: {', '.join(sorted(missing | unknown))}"
        )
    artifact_ids = [
        _identifier(params[field], f"{context}/{field}", ENTITY_ID_PATTERN)
        for field in artifact_fields
    ]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise StudioContractError(f"{context} artifact IDs must be distinct")
    for field in ("source_grant_id", "target_grant_id"):
        _identifier(params[field], f"{context}/{field}", ENTITY_ID_PATTERN)
    if params["platform_id"] not in RUNTIME_HEADLESS_PLATFORM_IDS:
        raise StudioContractError(f"{context}/platform_id is unsupported")
    if params["source_grant_id"] == params["target_grant_id"]:
        raise StudioContractError(f"{context} source and target grants must be distinct")
    for field in (
        "expected_source_grant_generation",
        "expected_target_grant_generation",
    ):
        _integer(params[field], f"{context}/{field}")
    return params


def _validate_creation_job_publication(
    value: object,
    context: str,
    *,
    job_version: int,
) -> dict[str, Any]:
    publication = _object(value, context)
    payload_field = {
        3: "assetpack",
        5: "runtime_bundle",
        6: "materialization_bundle",
        7: "standalone_game",
        8: "game_package",
        9: "standalone_game",
        11: "assetpack",
        12: "headless_evidence_set",
    }[job_version]
    _closed(
        publication,
        {"grant_id", "grant_generation", "kind", "state", payload_field},
        context,
    )
    _identifier(publication["grant_id"], f"{context}/grant_id", ENTITY_ID_PATTERN)
    _integer(publication["grant_generation"], f"{context}/grant_generation")
    expected_kind = {
        3: "generic_assetpack_directory",
        5: "game_runtime_bundle_directory",
        6: "game_materialization_bundle_directory",
        7: "standalone_game_directory",
        8: "game_package_file",
        9: "standalone_game_directory",
        11: "generic_assetpack_directory",
        12: "headless_evidence_directory",
    }[job_version]
    if publication["kind"] != expected_kind:
        raise StudioContractError(f"{context}/kind is unknown")
    if publication["state"] != "published":
        raise StudioContractError(f"{context}/state must be published")
    _validate_creation_output_publication(
        publication[payload_field],
        f"{context}/{payload_field}",
        grant_version={3: 1, 5: 2, 6: 3, 7: 4, 8: 5, 9: 4, 11: 1, 12: 6}[job_version],
    )
    return publication


def _validate_creation_job_result(
    value: object,
    context: str,
    *,
    job_version: int,
) -> dict[str, Any]:
    result = _object(value, context)
    fields = {
        "output_artifact_ids",
        "artifact_snapshot_hash",
        "analysis_status",
        "reason_codes",
        "cleanup_pending",
    }
    if job_version in {3, 5, 6, 7, 8, 9}:
        fields.add("publication")
    elif job_version == 10:
        fields.update({"review_receipt", "review_status"})
    elif job_version == 11:
        fields.update(
            {
                "asset_manifest",
                "assetpack",
                "asset_release_authority",
                "release_status",
                "publication",
            }
        )
    elif job_version == 12:
        fields.update(
            {
                "runtime_support_authority",
                "runtime_evidence",
                "runtime_support_report",
                "release_status",
                "native_status",
                "supported",
                "publication",
            }
        )
    _closed(
        result,
        fields,
        context,
    )
    output_ids = result["output_artifact_ids"]
    if not isinstance(output_ids, list) or not 1 <= len(output_ids) <= MAX_CREATION_JOB_OUTPUTS:
        raise StudioContractError(
            f"{context}/output_artifact_ids must contain 1 to {MAX_CREATION_JOB_OUTPUTS} entries"
        )
    checked_ids = [
        _identifier(item, f"{context}/output_artifact_ids/{index}", ENTITY_ID_PATTERN)
        for index, item in enumerate(output_ids)
    ]
    if len(checked_ids) != len(set(checked_ids)):
        raise StudioContractError(f"{context}/output_artifact_ids must be unique")
    _sha256(result["artifact_snapshot_hash"], f"{context}/artifact_snapshot_hash")
    if result["analysis_status"] not in CREATION_ANALYSIS_STATUSES:
        raise StudioContractError(f"{context}/analysis_status is unknown")
    _validate_canonical_public_token_array(
        result["reason_codes"],
        f"{context}/reason_codes",
        maximum=128,
    )
    _boolean(result["cleanup_pending"], f"{context}/cleanup_pending")
    if job_version in {3, 5, 6, 7, 8, 9}:
        _validate_creation_job_publication(
            result["publication"],
            f"{context}/publication",
            job_version=job_version,
        )
    elif job_version == 10:
        receipt = _object(result["review_receipt"], f"{context}/review_receipt")
        _closed(
            receipt,
            {"format", "format_version", "review_receipt_id", "content_hash"},
            f"{context}/review_receipt",
        )
        if (
            receipt["format"] != "world-forge.asset_qa_review_receipt"
            or receipt["format_version"] != 1
        ):
            raise StudioContractError(f"{context}/review_receipt format is unsupported")
        _identifier(
            receipt["review_receipt_id"],
            f"{context}/review_receipt/review_receipt_id",
            ENTITY_ID_PATTERN,
        )
        _sha256(receipt["content_hash"], f"{context}/review_receipt/content_hash")
        if result["review_status"] not in {"approved", "rejected"}:
            raise StudioContractError(f"{context}/review_status is unknown")
    elif job_version == 11:
        if len(checked_ids) != 3:
            raise StudioContractError(
                f"{context}/output_artifact_ids must contain exactly three entries"
            )
        for field, identifier_field in (
            ("asset_manifest", "manifest_id"),
            ("assetpack", "assetpack_id"),
        ):
            identity = _object(result[field], f"{context}/{field}")
            _closed(identity, {identifier_field, "content_hash"}, f"{context}/{field}")
            _identifier(
                identity[identifier_field],
                f"{context}/{field}/{identifier_field}",
                ENTITY_ID_PATTERN,
            )
            _sha256(identity["content_hash"], f"{context}/{field}/content_hash")
        authority = _object(
            result["asset_release_authority"],
            f"{context}/asset_release_authority",
        )
        _closed(
            authority,
            {"format", "format_version", "release_authority_id", "content_hash"},
            f"{context}/asset_release_authority",
        )
        if (
            authority["format"] != "world-forge.asset_release_authority"
            or authority["format_version"] != 1
        ):
            raise StudioContractError(f"{context}/asset_release_authority format is unsupported")
        _identifier(
            authority["release_authority_id"],
            f"{context}/asset_release_authority/release_authority_id",
            ENTITY_ID_PATTERN,
        )
        _sha256(
            authority["content_hash"],
            f"{context}/asset_release_authority/content_hash",
        )
        if result["release_status"] not in {"authorized", "blocked"}:
            raise StudioContractError(f"{context}/release_status is unknown")
        publication = result["publication"]
        if result["release_status"] == "authorized":
            if result["analysis_status"] != "passed" or result["reason_codes"]:
                raise StudioContractError(
                    f"{context} authorized release analysis fields are inconsistent"
                )
            if publication is None:
                raise StudioContractError(
                    f"{context}/publication is required for authorized release"
                )
            _validate_creation_job_publication(
                publication,
                f"{context}/publication",
                job_version=11,
            )
        else:
            if result["analysis_status"] != "failed" or not result["reason_codes"]:
                raise StudioContractError(
                    f"{context} blocked release analysis fields are inconsistent"
                )
            if publication is not None:
                raise StudioContractError(f"{context}/publication must be null for blocked release")
    elif job_version == 12:
        if len(checked_ids) != 3:
            raise StudioContractError(
                f"{context}/output_artifact_ids must contain exactly three entries"
            )
        for field, expected_format in (
            ("runtime_support_authority", "world-forge.runtime_support_authority"),
            ("runtime_evidence", "world-forge.runtime_evidence"),
            ("runtime_support_report", "world-forge.runtime_support_report"),
        ):
            identity = _object(result[field], f"{context}/{field}")
            _closed(
                identity,
                {"format", "format_version", "id", "content_hash"},
                f"{context}/{field}",
            )
            if identity["format"] != expected_format or identity["format_version"] != 1:
                raise StudioContractError(f"{context}/{field} is unsupported")
            _identifier(identity["id"], f"{context}/{field}/id", ENTITY_ID_PATTERN)
            _sha256(identity["content_hash"], f"{context}/{field}/content_hash")
        _validate_creation_job_publication(
            result["publication"],
            f"{context}/publication",
            job_version=12,
        )
        if (
            result["release_status"] != "blocked"
            or result["native_status"] != "unavailable"
            or result["supported"] is not False
        ):
            raise StudioContractError(f"{context} overclaims runtime authority")
    return result


def _validate_creation_job_error(value: object, context: str) -> dict[str, Any]:
    error = _object(value, context)
    fields = {"code", "message", "retryable"}
    if "recovery_evidence" in error:
        fields.add("recovery_evidence")
    _closed(error, fields, context)
    if error["code"] not in CREATION_JOB_ERROR_CODES:
        raise StudioContractError(f"{context}/code is unknown")
    message = _plain_string(error["message"], f"{context}/message", max_length=512)
    if not message:
        raise StudioContractError(f"{context}/message must be non-empty")
    _boolean(error["retryable"], f"{context}/retryable")
    if "recovery_evidence" in error:
        validate_studio_recovery_evidence(
            error["recovery_evidence"],
            f"{context}/recovery_evidence",
        )
    return error


def creation_job_record_hash(value: object) -> str:
    return canonical_payload_hash(_object(value, "creation job"), hash_field="record_hash")


def validate_studio_creation_job(value: object) -> dict[str, Any]:
    job = _object(value, "creation job")
    fields = {
        "format",
        "format_version",
        "job_id",
        "workspace_id",
        "operation",
        "state",
        "generation",
        "authority",
        "inputs",
        "progress",
        "result",
        "error",
        "created_at",
        "started_at",
        "finished_at",
        "updated_at",
        "record_hash",
    }
    version = job.get("format_version")
    if version in {2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}:
        fields.add("operation_params")
    missing = fields - set(job)
    unknown = set(job) - fields
    if missing or unknown:
        invalid = sorted(missing | unknown)
        raise StudioContractError(f"creation job has invalid fields: {', '.join(invalid)}")
    if job["format"] != CREATION_JOB_FORMAT:
        raise StudioContractError("creation job format is unsupported")
    if type(version) is not int or version not in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}:
        raise StudioContractError("creation job format_version is unsupported")
    _identifier(job["job_id"], "creation job/job_id", ENTITY_ID_PATTERN)
    _identifier(job["workspace_id"], "creation job/workspace_id", WORKSPACE_ID_PATTERN)
    expected_operations = (
        CREATION_JOB_OPERATIONS_V1
        if version == 1
        else CREATION_JOB_OPERATIONS_V2
        if version == 2
        else CREATION_JOB_OPERATIONS_V3
        if version == 3
        else CREATION_JOB_OPERATIONS_V4
        if version == 4
        else CREATION_JOB_OPERATIONS_V5
        if version == 5
        else CREATION_JOB_OPERATIONS_V6
        if version == 6
        else CREATION_JOB_OPERATIONS_V7
        if version == 7
        else CREATION_JOB_OPERATIONS_V8
        if version == 8
        else CREATION_JOB_OPERATIONS_V9
        if version == 9
        else CREATION_JOB_OPERATIONS_V10
        if version == 10
        else CREATION_JOB_OPERATIONS_V11
        if version == 11
        else CREATION_JOB_OPERATIONS_V12
    )
    if job["operation"] not in expected_operations:
        raise StudioContractError("creation job/operation is unknown for its version")
    if version == 2:
        _validate_asset_process_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 3:
        _validate_asset_release_seal_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 4:
        _validate_runtime_compose_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 5:
        _validate_runtime_bundle_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 6:
        _validate_materialization_bundle_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 7:
        _validate_game_materialize_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 8:
        _validate_game_package_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 9:
        _validate_game_package_extract_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 10:
        _validate_asset_qa_review_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 11:
        _validate_asset_release_authorize_operation_params(
            job["operation_params"], "creation job/operation_params"
        )
    elif version == 12:
        _validate_runtime_headless_verify_operation_params(
            job["operation_params"],
            "creation job/operation_params",
        )
    state = job["state"]
    if state not in CREATION_JOB_STATES:
        raise StudioContractError("creation job/state is unknown")
    _integer(job["generation"], "creation job/generation")
    _validate_creation_job_authority(job["authority"], "creation job/authority")
    inputs = job["inputs"]
    if not isinstance(inputs, list) or len(inputs) > MAX_CREATION_JOB_INPUTS:
        raise StudioContractError(
            f"creation job/inputs must contain at most {MAX_CREATION_JOB_INPUTS} entries"
        )
    artifact_ids: list[str] = []
    for index, raw in enumerate(inputs):
        item = _validate_creation_job_input(raw, f"creation job/inputs/{index}")
        artifact_ids.append(item["artifact_id"])
    if len(artifact_ids) != len(set(artifact_ids)):
        raise StudioContractError("creation job/inputs must reference unique artifacts")
    progress = job["progress"]
    if progress not in CREATION_JOB_PROGRESS:
        raise StudioContractError("creation job/progress is unknown")

    created_at = _timestamp(job["created_at"], "creation job/created_at")
    updated_at = _timestamp(job["updated_at"], "creation job/updated_at")
    started_at = job["started_at"]
    finished_at = job["finished_at"]
    if started_at is not None:
        _timestamp(started_at, "creation job/started_at")
    if finished_at is not None:
        _timestamp(finished_at, "creation job/finished_at")
    if updated_at < created_at:
        raise StudioContractError("creation job/updated_at precedes created_at")

    if state == "queued":
        if progress != "queued" or any(
            item is not None for item in (job["result"], job["error"], started_at, finished_at)
        ):
            raise StudioContractError("queued creation job fields are inconsistent")
    elif state == "running":
        if (
            progress
            not in {
                "reserved",
                "worker_started",
                "output_published",
                "registry_committing",
            }
            or job["result"] is not None
            or job["error"] is not None
            or started_at is None
            or finished_at is not None
        ):
            raise StudioContractError("running creation job fields are inconsistent")
    elif state == "succeeded":
        if (
            progress not in {"committed", "cleanup_pending"}
            or job["result"] is None
            or job["error"] is not None
            or started_at is None
            or finished_at is None
        ):
            raise StudioContractError("succeeded creation job fields are inconsistent")
        result = _validate_creation_job_result(
            job["result"],
            "creation job/result",
            job_version=version,
        )
        if (progress == "cleanup_pending") != result["cleanup_pending"]:
            raise StudioContractError("creation job cleanup state does not match result")
    elif state in {"failed", "orphaned"}:
        if (
            progress != state
            or job["result"] is not None
            or job["error"] is None
            or finished_at is None
        ):
            raise StudioContractError(f"{state} creation job fields are inconsistent")
        _validate_creation_job_error(job["error"], "creation job/error")
    else:
        if (
            progress != "canceled"
            or job["result"] is not None
            or job["error"] is not None
            or finished_at is None
        ):
            raise StudioContractError("canceled creation job fields are inconsistent")

    _sha256(job["record_hash"], "creation job/record_hash")
    expected_hash = creation_job_record_hash(job)
    if not hmac.compare_digest(job["record_hash"], expected_hash):
        raise StudioContractError("creation job/record_hash does not match the record")
    return job


def _validate_creation_worker_metadata(value: object, context: str) -> dict[str, Any]:
    metadata = _object(value, context)
    missing = {"analysis_status"} - set(metadata)
    unknown = set(metadata) - {"analysis_status", "reason_codes"}
    if missing or unknown:
        raise StudioContractError(
            f"{context} has invalid fields: {', '.join(sorted(missing | unknown))}"
        )
    if metadata["analysis_status"] not in CREATION_ANALYSIS_STATUSES:
        raise StudioContractError(f"{context}/analysis_status is unknown")
    if "reason_codes" in metadata:
        _validate_canonical_public_token_array(
            metadata["reason_codes"], f"{context}/reason_codes", maximum=128
        )
    return metadata


def validate_studio_creation_worker_envelope(value: object) -> dict[str, Any]:
    envelope = _object(value, "creation worker envelope")
    common = {"format", "format_version", "kind", "job_id", "operation"}
    kind = envelope.get("kind")
    fields = (
        common | {"request_locator", "request_sha256"}
        if kind == "request"
        else common | {"ok", "outputs", "metadata"}
        if kind == "response" and envelope.get("ok") is True
        else common | {"ok", "error"}
        if kind == "response" and envelope.get("ok") is False
        else common
    )
    missing = fields - set(envelope)
    unknown = set(envelope) - fields
    if missing or unknown:
        invalid = sorted(missing | unknown)
        raise StudioContractError(
            f"creation worker envelope has invalid fields: {', '.join(invalid)}"
        )
    if envelope.get("format") != CREATION_WORKER_FORMAT:
        raise StudioContractError("creation worker envelope format is unsupported")
    version = envelope.get("format_version")
    if type(version) is not int or version not in {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}:
        raise StudioContractError("creation worker envelope format_version is unsupported")
    if kind not in {"request", "response"}:
        raise StudioContractError("creation worker envelope/kind is unknown")
    _identifier(envelope["job_id"], "creation worker envelope/job_id", ENTITY_ID_PATTERN)
    expected_operations = (
        CREATION_JOB_OPERATIONS_V1
        if version == 1
        else CREATION_JOB_OPERATIONS_V2
        if version == 2
        else CREATION_JOB_OPERATIONS_V3
        if version == 3
        else CREATION_JOB_OPERATIONS_V4
        if version == 4
        else CREATION_JOB_OPERATIONS_V5
        if version == 5
        else CREATION_JOB_OPERATIONS_V6
        if version == 6
        else CREATION_JOB_OPERATIONS_V7
        if version == 7
        else CREATION_JOB_OPERATIONS_V8
        if version == 8
        else CREATION_JOB_OPERATIONS_V9
        if version == 9
        else CREATION_JOB_OPERATIONS_V10
        if version == 10
        else CREATION_JOB_OPERATIONS_V11
        if version == 11
        else CREATION_JOB_OPERATIONS_V12
    )
    if envelope["operation"] not in expected_operations:
        raise StudioContractError("creation worker envelope/operation is unknown for its version")
    if kind == "request":
        _identifier(
            envelope["request_locator"],
            "creation worker envelope/request_locator",
            ENTITY_ID_PATTERN,
        )
        _sha256(envelope["request_sha256"], "creation worker envelope/request_sha256")
        return envelope
    if not isinstance(envelope["ok"], bool):
        raise StudioContractError("creation worker envelope/ok must be a boolean")
    if not envelope["ok"]:
        _validate_creation_job_error(envelope["error"], "creation worker envelope/error")
        return envelope
    outputs = envelope["outputs"]
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_CREATION_JOB_OUTPUTS:
        raise StudioContractError(
            f"creation worker envelope/outputs must contain 1 to {MAX_CREATION_JOB_OUTPUTS} entries"
        )
    locators: list[str] = []
    for index, raw in enumerate(outputs):
        output = _object(raw, f"creation worker envelope/outputs/{index}")
        _closed(
            output,
            {"locator", "subject", "size", "sha256"},
            f"creation worker envelope/outputs/{index}",
        )
        locators.append(
            _identifier(
                output["locator"],
                f"creation worker envelope/outputs/{index}/locator",
                ENTITY_ID_PATTERN,
            )
        )
        _validate_creation_artifact_identity(
            output["subject"], f"creation worker envelope/outputs/{index}/subject"
        )
        _integer(
            output["size"],
            f"creation worker envelope/outputs/{index}/size",
            minimum=1,
            maximum=MAX_CHANGESET_BYTES,
        )
        _sha256(output["sha256"], f"creation worker envelope/outputs/{index}/sha256")
    if len(locators) != len(set(locators)):
        raise StudioContractError("creation worker envelope/outputs locators must be unique")
    _validate_creation_worker_metadata(envelope["metadata"], "creation worker envelope/metadata")
    return envelope


def _validate_canonical_identifier_array(
    value: object,
    context: str,
    *,
    maximum: int = 128,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise StudioContractError(f"{context} must be an array with at most {maximum} entries")
    checked = [
        _identifier(item, f"{context}/{index}", OPERATION_PATTERN)
        for index, item in enumerate(value)
    ]
    if checked != sorted(set(checked), key=lambda item: item.encode("utf-8")):
        raise StudioContractError(f"{context} must be unique and canonically ordered")
    return checked


def _validate_canonical_public_token_array(
    value: object,
    context: str,
    *,
    maximum: int,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise StudioContractError(f"{context} must be an array with at most {maximum} entries")
    checked = [
        _identifier(item, f"{context}/{index}", PUBLIC_TOKEN_PATTERN)
        for index, item in enumerate(value)
    ]
    if checked != sorted(set(checked), key=lambda item: item.encode("utf-8")):
        raise StudioContractError(f"{context} must be unique and canonically ordered")
    return checked


def validate_studio_creation_artifact(value: object) -> dict[str, Any]:
    artifact = _object(value, "creation artifact")
    fields = {
        "format",
        "format_version",
        "artifact_id",
        "subject",
        "lifecycle",
        "roles",
        "producer",
        "references",
        "authority",
        "record_hash",
    }
    _closed(artifact, fields, "creation artifact")
    if artifact["format"] != CREATION_ARTIFACT_FORMAT:
        raise StudioContractError("creation artifact format is unsupported")
    if type(artifact["format_version"]) is not int or artifact["format_version"] != 1:
        raise StudioContractError("creation artifact format_version must be 1")
    _identifier(artifact["artifact_id"], "creation artifact/artifact_id", ENTITY_ID_PATTERN)
    _validate_creation_artifact_identity(artifact["subject"], "creation artifact/subject")
    if artifact["lifecycle"] not in CREATION_ARTIFACT_LIFECYCLES:
        raise StudioContractError("creation artifact/lifecycle is unknown")
    roles = _validate_canonical_identifier_array(artifact["roles"], "creation artifact/roles")
    if not roles:
        raise StudioContractError("creation artifact/roles must be non-empty")
    producer = _object(artifact["producer"], "creation artifact/producer")
    _closed(producer, {"kind", "phase_id", "reference_id"}, "creation artifact/producer")
    if producer["kind"] not in {
        "source_snapshot",
        "active_phase_report",
        "invalidated_phase_report",
        "future_candidate",
    }:
        raise StudioContractError("creation artifact/producer/kind is unknown")
    phase_id = _string(
        producer["phase_id"],
        "creation artifact/producer/phase_id",
        nullable=True,
    )
    if phase_id is not None:
        _identifier(phase_id, "creation artifact/producer/phase_id", OPERATION_PATTERN)
    _identifier(
        producer["reference_id"],
        "creation artifact/producer/reference_id",
        OPERATION_PATTERN,
    )
    if producer["kind"] == "source_snapshot" and phase_id is not None:
        raise StudioContractError("source snapshot producer cannot name a phase")
    if producer["kind"] in {"active_phase_report", "invalidated_phase_report"} and phase_id is None:
        raise StudioContractError("phase report producer requires a phase")
    references = _object(artifact["references"], "creation artifact/references")
    _closed(
        references,
        {"dependency_count", "dependent_count"},
        "creation artifact/references",
    )
    _integer(
        references["dependency_count"],
        "creation artifact/references/dependency_count",
        maximum=MAX_CREATION_ARTIFACTS,
    )
    _integer(
        references["dependent_count"],
        "creation artifact/references/dependent_count",
        maximum=MAX_CREATION_ARTIFACTS,
    )
    _validate_creation_artifact_authority(artifact["authority"], "creation artifact/authority")
    _sha256(artifact["record_hash"], "creation artifact/record_hash")
    if canonical_payload_hash(artifact, hash_field="record_hash") != artifact["record_hash"]:
        raise StudioContractError("creation artifact record_hash does not match")
    return artifact


def _validate_creation_execution_rows(value: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 32:
        raise StudioContractError(f"{context} must be an array with at most 32 entries")
    platforms: list[str] = []
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        row = _object(raw, f"{context}/{index}")
        _closed(row, {"platform", "status", "evidence_ids"}, f"{context}/{index}")
        platform = _identifier(row["platform"], f"{context}/{index}/platform", PUBLIC_TOKEN_PATTERN)
        if row["status"] not in {
            "untested",
            "headless_verified",
            "native_verified",
            "failed",
        }:
            raise StudioContractError(f"{context}/{index}/status is unknown")
        _validate_canonical_identifier_array(
            row["evidence_ids"], f"{context}/{index}/evidence_ids", maximum=64
        )
        platforms.append(platform)
        rows.append(row)
    if platforms != sorted(set(platforms), key=lambda item: item.encode("utf-8")):
        raise StudioContractError(f"{context} platforms must be unique and canonically ordered")
    return rows


def validate_studio_creation_evidence(value: object) -> dict[str, Any]:
    evidence = _object(value, "creation evidence")
    fields = {
        "format",
        "format_version",
        "evidence_id",
        "authority",
        "artifact_snapshot_hash",
        "artifact_counts",
        "dimensions",
        "blocker_reason_codes",
        "mechanics",
        "runtime",
        "assets",
        "materialization",
        "readiness",
        "handoff",
        "content_hash",
    }
    _closed(evidence, fields, "creation evidence")
    if evidence["format"] != CREATION_EVIDENCE_FORMAT:
        raise StudioContractError("creation evidence format is unsupported")
    if type(evidence["format_version"]) is not int or evidence["format_version"] != 1:
        raise StudioContractError("creation evidence format_version must be 1")
    _identifier(evidence["evidence_id"], "creation evidence/evidence_id", ENTITY_ID_PATTERN)
    _validate_creation_artifact_authority(evidence["authority"], "creation evidence/authority")
    _sha256(evidence["artifact_snapshot_hash"], "creation evidence/artifact_snapshot_hash")
    counts = _object(evidence["artifact_counts"], "creation evidence/artifact_counts")
    _closed(
        counts,
        {"active", "invalidated", "historical", "candidate", "ignored"},
        "creation evidence/artifact_counts",
    )
    for field in counts:
        _integer(counts[field], f"creation evidence/artifact_counts/{field}", maximum=100_000)
    dimensions = _object(evidence["dimensions"], "creation evidence/dimensions")
    _closed(
        dimensions,
        {"authoring", "compilation", "assets", "adapter", "execution", "packaging", "release"},
        "creation evidence/dimensions",
    )
    allowed_dimensions = {
        "authoring": {"valid", "invalid"},
        "compilation": {"not_requested", "compiled", "unsupported", "failed"},
        "assets": {"unplanned", "planned", "produced", "processed", "sealed", "failed"},
        "adapter": {"absent", "declared", "verified"},
        "packaging": {"unverified", "verified", "failed"},
        "release": {"blocked", "ready"},
    }
    for field, allowed in allowed_dimensions.items():
        if dimensions[field] not in allowed:
            raise StudioContractError(f"creation evidence/dimensions/{field} is unknown")
    execution = _validate_creation_execution_rows(
        dimensions["execution"], "creation evidence/dimensions/execution"
    )
    blockers = _validate_canonical_identifier_array(
        evidence["blocker_reason_codes"],
        "creation evidence/blocker_reason_codes",
        maximum=128,
    )
    if (dimensions["release"] == "ready") != (not blockers):
        raise StudioContractError("creation evidence release state is inconsistent")

    mechanics = _object(evidence["mechanics"], "creation evidence/mechanics")
    _closed(
        mechanics,
        {"artifact_id", "total", "status_counts", "required_features", "missing_features"},
        "creation evidence/mechanics",
    )
    if mechanics["artifact_id"] is not None:
        _identifier(
            mechanics["artifact_id"], "creation evidence/mechanics/artifact_id", ENTITY_ID_PATTERN
        )
    _integer(mechanics["total"], "creation evidence/mechanics/total", maximum=4096)
    status_counts = _object(mechanics["status_counts"], "creation evidence/mechanics/status_counts")
    _closed(
        status_counts,
        {"supported_current", "game_extension_verified", "authoring_only", "blocked"},
        "creation evidence/mechanics/status_counts",
    )
    for field in status_counts:
        _integer(
            status_counts[field],
            f"creation evidence/mechanics/status_counts/{field}",
            maximum=4096,
        )
    if sum(status_counts.values()) != mechanics["total"]:
        raise StudioContractError("creation evidence mechanic status counts do not match total")
    for field in ("required_features", "missing_features"):
        _validate_canonical_public_token_array(
            mechanics[field], f"creation evidence/mechanics/{field}", maximum=4096
        )

    runtime = _object(evidence["runtime"], "creation evidence/runtime")
    _closed(
        runtime,
        {
            "requested_adapter",
            "resolved_adapter",
            "required_features",
            "missing_features",
            "platforms",
        },
        "creation evidence/runtime",
    )
    for field in ("requested_adapter", "resolved_adapter"):
        if runtime[field] is not None:
            _identifier(runtime[field], f"creation evidence/runtime/{field}", ENTITY_ID_PATTERN)
    for field in ("required_features", "missing_features"):
        _validate_canonical_public_token_array(
            runtime[field], f"creation evidence/runtime/{field}", maximum=4096
        )
    if runtime["platforms"] != execution:
        raise StudioContractError("creation evidence runtime platforms do not match dimensions")

    assets = _object(evidence["assets"], "creation evidence/assets")
    asset_fields = {
        "subject_artifact_id",
        "target_artifact_id",
        "style_artifact_id",
        "inventory_artifact_id",
        "assetpack_artifact_id",
        "inventory_assets",
        "lineage_complete",
        "lineage_partial",
        "qa_passed",
        "qa_failed",
        "licensed",
    }
    _closed(assets, asset_fields, "creation evidence/assets")
    for field in (
        "subject_artifact_id",
        "target_artifact_id",
        "style_artifact_id",
        "inventory_artifact_id",
        "assetpack_artifact_id",
    ):
        if assets[field] is not None:
            _identifier(assets[field], f"creation evidence/assets/{field}", ENTITY_ID_PATTERN)
    for field in asset_fields - {
        "subject_artifact_id",
        "target_artifact_id",
        "style_artifact_id",
        "inventory_artifact_id",
        "assetpack_artifact_id",
    }:
        _integer(assets[field], f"creation evidence/assets/{field}", maximum=100_000)

    materialization = _object(evidence["materialization"], "creation evidence/materialization")
    _closed(
        materialization,
        {"enabled", "state", "prerequisites"},
        "creation evidence/materialization",
    )
    if materialization["enabled"] is not False or materialization["state"] != "blocked":
        raise StudioContractError("creation evidence materialization must remain inspect-only")
    prerequisites = materialization["prerequisites"]
    if not isinstance(prerequisites, list) or not 1 <= len(prerequisites) <= 64:
        raise StudioContractError("creation evidence materialization prerequisites are invalid")
    codes: list[str] = []
    for index, raw in enumerate(prerequisites):
        item = _object(raw, f"creation evidence/materialization/prerequisites/{index}")
        _closed(
            item,
            {"code", "satisfied", "message"},
            f"creation evidence/materialization/prerequisites/{index}",
        )
        codes.append(
            _identifier(
                item["code"],
                f"creation evidence/materialization/prerequisites/{index}/code",
                OPERATION_PATTERN,
            )
        )
        _boolean(
            item["satisfied"],
            f"creation evidence/materialization/prerequisites/{index}/satisfied",
        )
        message = _plain_string(
            item["message"],
            f"creation evidence/materialization/prerequisites/{index}/message",
            max_length=256,
        )
        if not message:
            raise StudioContractError("creation evidence prerequisite message must be non-empty")
    if codes != sorted(set(codes), key=lambda item: item.encode("utf-8")):
        raise StudioContractError("creation evidence prerequisites are not canonical")

    for field, expected_format in (
        ("readiness", "world-forge.creation_readiness"),
        ("handoff", "world-forge.creation_handoff"),
    ):
        identity = _validate_creation_artifact_identity(
            evidence[field], f"creation evidence/{field}"
        )
        if identity["format"] != expected_format:
            raise StudioContractError(f"creation evidence/{field} format is unsupported")
    _sha256(evidence["content_hash"], "creation evidence/content_hash")
    if canonical_payload_hash(evidence) != evidence["content_hash"]:
        raise StudioContractError("creation evidence content_hash does not match")
    return evidence


def creation_changeset_record_hash(value: object) -> str:
    record = _object(value, "creation changeset")
    return canonical_payload_hash(record, hash_field="record_hash")


def _validate_creation_changeset_operation(value: object, context: str) -> dict[str, Any]:
    operation = _object(value, context)
    _closed(
        operation,
        {
            "operation",
            "path",
            "expected_base_file_sha256",
            "expected_base_size",
            "proposed_file_sha256",
            "proposed_size",
        },
        context,
    )
    kind = operation["operation"]
    if kind not in {"create", "replace", "delete"}:
        raise StudioContractError(f"{context}/operation is unknown")
    try:
        relative = portable_relative_path(operation["path"])
    except UnicodeError as exc:
        raise StudioContractError(f"{context}/path must be a portable relative path") from exc
    rendered = None if relative is None else relative.as_posix()
    if relative is None or unicodedata.normalize("NFC", rendered) != rendered:
        raise StudioContractError(f"{context}/path must be an NFC portable relative path")
    if len(rendered) > 1024:
        raise StudioContractError(f"{context}/path must contain at most 1024 characters")
    reserved = {".worldforge", "artifacts", "assets", "runtime", "output", "outputs"}
    if any(part.casefold() in reserved for part in relative.parts):
        raise StudioContractError(f"{context}/path is outside the creation source graph")
    base_hash = _sha256(
        operation["expected_base_file_sha256"],
        f"{context}/expected_base_file_sha256",
        nullable=True,
    )
    proposed_hash = _sha256(
        operation["proposed_file_sha256"],
        f"{context}/proposed_file_sha256",
        nullable=True,
    )
    base_size = operation["expected_base_size"]
    proposed_size = operation["proposed_size"]
    if base_size is not None:
        _integer(base_size, f"{context}/expected_base_size")
        if base_size > MAX_CHANGE_FILE_BYTES:
            raise StudioContractError(f"{context}/expected_base_size exceeds the file limit")
    if proposed_size is not None:
        _integer(proposed_size, f"{context}/proposed_size")
        if proposed_size > MAX_CHANGE_FILE_BYTES:
            raise StudioContractError(f"{context}/proposed_size exceeds the file limit")
    if kind == "create" and (base_hash is not None or base_size is not None):
        raise StudioContractError(f"{context} create operation requires an absent base")
    if kind in {"replace", "delete"} and (base_hash is None or base_size is None):
        raise StudioContractError(f"{context} operation requires an exact base")
    if kind == "delete" and (proposed_hash is not None or proposed_size is not None):
        raise StudioContractError(f"{context} delete operation cannot contain proposed bytes")
    if kind in {"create", "replace"} and (proposed_hash is None or proposed_size is None):
        raise StudioContractError(f"{context} operation requires exact proposed bytes")
    if kind == "replace" and base_hash == proposed_hash:
        raise StudioContractError(f"{context} replace operation must change file bytes")
    return operation


def validate_studio_creation_changeset(value: object) -> dict[str, Any]:
    changeset = _object(value, "creation changeset")
    fields = {
        "format",
        "format_version",
        "changeset_id",
        "workspace_id",
        "status",
        "expected_root_generation",
        "expected_source_revision",
        "proposed_source_revision",
        "expected_workflow_status_hash",
        "review_sha256",
        "operations",
        "created_at",
        "updated_at",
        "record_hash",
    }
    _closed(changeset, fields, "creation changeset")
    if changeset["format"] != CREATION_CHANGESET_FORMAT:
        raise StudioContractError("creation changeset format is unsupported")
    if changeset["format_version"] != 1 or isinstance(changeset["format_version"], bool):
        raise StudioContractError("creation changeset format_version must be 1")
    _identifier(changeset["changeset_id"], "creation changeset/changeset_id", ENTITY_ID_PATTERN)
    _identifier(
        changeset["workspace_id"],
        "creation changeset/workspace_id",
        WORKSPACE_ID_PATTERN,
    )
    if changeset["status"] not in CREATION_CHANGESET_STATES:
        raise StudioContractError("creation changeset/status is unknown")
    _integer(
        changeset["expected_root_generation"],
        "creation changeset/expected_root_generation",
        maximum=9_007_199_254_740_991,
    )
    expected_revision = _sha256(
        changeset["expected_source_revision"],
        "creation changeset/expected_source_revision",
    )
    proposed_revision = _sha256(
        changeset["proposed_source_revision"],
        "creation changeset/proposed_source_revision",
    )
    if expected_revision == proposed_revision:
        raise StudioContractError("creation changeset source revisions must differ")
    _sha256(
        changeset["expected_workflow_status_hash"],
        "creation changeset/expected_workflow_status_hash",
        nullable=True,
    )
    _sha256(changeset["review_sha256"], "creation changeset/review_sha256")
    operations = changeset["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_CHANGESET_OPERATIONS:
        raise StudioContractError("creation changeset/operations is invalid")
    paths: set[tuple[str, ...]] = set()
    rendered_paths: list[str] = []
    total_retained = 0
    for index, raw_operation in enumerate(operations):
        operation = _validate_creation_changeset_operation(
            raw_operation,
            f"creation changeset/operations/{index}",
        )
        relative = PurePosixPath(operation["path"])
        key = tuple(part.casefold() for part in relative.parts)
        if key in paths:
            raise StudioContractError("creation changeset operation paths collide")
        paths.add(key)
        rendered_paths.append(relative.as_posix())
        total_retained += (operation["expected_base_size"] or 0) + (operation["proposed_size"] or 0)
    if rendered_paths != sorted(rendered_paths, key=lambda path: path.encode("utf-8")):
        raise StudioContractError("creation changeset operations must be canonically ordered")
    if total_retained > MAX_CHANGESET_BYTES:
        raise StudioContractError("creation changeset retained bytes exceed the aggregate limit")
    _timestamp(changeset["created_at"], "creation changeset/created_at")
    _timestamp(changeset["updated_at"], "creation changeset/updated_at")
    record_hash = _sha256(changeset["record_hash"], "creation changeset/record_hash")
    expected_hash = creation_changeset_record_hash(changeset)
    assert record_hash is not None
    if not hmac.compare_digest(record_hash, expected_hash):
        raise StudioContractError("creation changeset/record_hash does not match the record")
    return changeset


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
    common = {
        "format",
        "format_version",
        "changeset_id",
        "workspace_id",
        "status",
        "operations",
        "created_at",
        "updated_at",
    }
    version = changeset.get("format_version")
    if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2}:
        raise StudioContractError("changeset format_version must be 1 or 2")
    required = common | ({"review_sha256"} if version == 2 else set())
    _closed(changeset, required, "changeset")
    if changeset["format"] != CHANGESET_FORMAT:
        raise StudioContractError("changeset format is unsupported")
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
        operation_fields = {
            "path",
            "operation",
            "base_sha256",
            "proposed_sha256",
            "size",
        }
        if version == 2:
            operation_fields.add("base_size")
        _closed(
            operation,
            operation_fields,
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
        base_size = operation.get("base_size", 0)
        if version == 2 and (
            isinstance(base_size, bool)
            or not isinstance(base_size, int)
            or not 0 <= base_size <= MAX_CHANGE_FILE_BYTES
        ):
            raise StudioContractError(
                f"changeset/operations/{index}/base_size must be from 0 to {MAX_CHANGE_FILE_BYTES}"
            )
        if kind == "create":
            if base is not None:
                raise StudioContractError(
                    f"changeset/operations/{index}/base_sha256 must be null for create"
                )
            if version == 2 and base_size != 0:
                raise StudioContractError(
                    f"changeset/operations/{index}/base_size must be zero for create"
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
    if version == 2:
        review_sha256 = _sha256(changeset["review_sha256"], "changeset/review_sha256")
        try:
            expected_review_sha256 = compute_review_sha256(operations)
        except ReviewDiffError as exc:  # pragma: no cover - operations were validated above
            raise StudioContractError("changeset/review_sha256 could not be computed") from exc
        if review_sha256 != expected_review_sha256:
            raise StudioContractError("changeset/review_sha256 does not match operations")
    _timestamp(changeset["created_at"], "changeset/created_at")
    _timestamp(changeset["updated_at"], "changeset/updated_at")
    return changeset


def _validate_changeset_diff_line(value: object, context: str) -> None:
    line = _object(value, context)
    _closed(line, {"kind", "text"}, context)
    if not isinstance(line["kind"], str) or line["kind"] not in {
        "context",
        "remove",
        "add",
    }:
        raise StudioContractError(f"{context}/kind is unknown")
    _plain_string(line["text"], f"{context}/text")


def _validate_changeset_text_hunk(value: object, context: str) -> None:
    hunk = _object(value, context)
    _closed(
        hunk,
        {"base_start", "base_count", "proposed_start", "proposed_count", "lines"},
        context,
    )
    _integer(hunk["base_start"], f"{context}/base_start", minimum=1)
    _integer(hunk["base_count"], f"{context}/base_count")
    _integer(hunk["proposed_start"], f"{context}/proposed_start", minimum=1)
    _integer(hunk["proposed_count"], f"{context}/proposed_count")
    lines = hunk["lines"]
    if not isinstance(lines, list) or not lines or len(lines) > 40_000:
        raise StudioContractError(f"{context}/lines must contain 1 to 40000 entries")
    for index, line in enumerate(lines):
        _validate_changeset_diff_line(line, f"{context}/lines/{index}")


def _validate_changeset_json_change(value: object, context: str) -> None:
    change = _object(value, context)
    kind = change.get("operation")
    fields = {
        "add": {"operation", "pointer", "value"},
        "remove": {"operation", "pointer", "old_value"},
        "replace": {"operation", "pointer", "old_value", "value"},
    }
    if not isinstance(kind, str) or kind not in fields:
        raise StudioContractError(f"{context}/operation is unknown")
    _closed(change, fields[kind], context)
    _plain_string(change["pointer"], f"{context}/pointer")
    for field in fields[kind] - {"operation", "pointer"}:
        _strict_json_value(change[field], f"{context}/{field}")


def _validate_changeset_diff(value: object, context: str) -> None:
    diff = _object(value, context)
    required = {
        "changeset_id",
        "changeset_format_version",
        "available",
        "unavailable_reason",
        "review_sha256",
        "operations",
    }
    _closed(diff, required, context)
    _identifier(diff["changeset_id"], f"{context}/changeset_id", ENTITY_ID_PATTERN)
    version = diff["changeset_format_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2}:
        raise StudioContractError(f"{context}/changeset_format_version must be 1 or 2")
    _boolean(diff["available"], f"{context}/available")
    operations = diff["operations"]
    if version == 1:
        if (
            diff["available"] is not False
            or diff["unavailable_reason"] != "legacy_base_bytes_not_retained"
            or diff["review_sha256"] is not None
            or operations != []
        ):
            raise StudioContractError(f"{context} is not a valid unavailable v1 diff")
        return
    if (
        diff["available"] is not True
        or diff["unavailable_reason"] is not None
        or not isinstance(operations, list)
        or not operations
        or len(operations) > MAX_CHANGESET_OPERATIONS
    ):
        raise StudioContractError(f"{context} is not a valid available v2 diff")
    review_sha256 = _sha256(diff["review_sha256"], f"{context}/review_sha256")
    public_operations: list[dict[str, Any]] = []
    for index, value in enumerate(operations):
        operation_context = f"{context}/operations/{index}"
        operation = _object(value, operation_context)
        fields = {
            "path",
            "operation",
            "base_sha256",
            "base_size",
            "proposed_sha256",
            "size",
            "text_hunks",
            "json_pointer_changes",
        }
        _closed(operation, fields, operation_context)
        hunks = operation["text_hunks"]
        if not isinstance(hunks, list) or len(hunks) > 20_000:
            raise StudioContractError(
                f"{operation_context}/text_hunks must contain at most 20000 entries"
            )
        for hunk_index, hunk in enumerate(hunks):
            _validate_changeset_text_hunk(hunk, f"{operation_context}/text_hunks/{hunk_index}")
        json_changes = operation["json_pointer_changes"]
        if json_changes is not None:
            if not isinstance(json_changes, list) or len(json_changes) > 100_000:
                raise StudioContractError(
                    f"{operation_context}/json_pointer_changes must contain at most 100000 entries"
                )
            for change_index, change in enumerate(json_changes):
                _validate_changeset_json_change(
                    change,
                    f"{operation_context}/json_pointer_changes/{change_index}",
                )
        public_operations.append(
            {
                field: operation[field]
                for field in (
                    "path",
                    "operation",
                    "base_sha256",
                    "base_size",
                    "proposed_sha256",
                    "size",
                )
            }
        )
    validate_studio_changeset(
        {
            "format": CHANGESET_FORMAT,
            "format_version": 2,
            "changeset_id": diff["changeset_id"],
            "workspace_id": "review_validation",
            "status": "staged",
            "operations": public_operations,
            "review_sha256": review_sha256,
            "created_at": "1970-01-01T00:00:00Z",
            "updated_at": "1970-01-01T00:00:00Z",
        }
    )


def _validate_changeset_result(method: str, value: object, context: str) -> None:
    result = _object(value, context)
    if method == "changeset.list":
        _closed(result, {"changesets"}, context)
        changesets = result["changesets"]
        if not isinstance(changesets, list) or len(changesets) > 1000:
            raise StudioContractError(f"{context}/changesets must contain at most 1000 entries")
        for index, changeset in enumerate(changesets):
            try:
                validate_studio_changeset(changeset)
            except StudioContractError as exc:
                raise StudioContractError(f"{context}/changesets/{index}: {exc}") from exc
        return
    if method == "changeset.diff":
        _closed(result, {"diff"}, context)
        _validate_changeset_diff(result["diff"], f"{context}/diff")
        return
    _closed(result, {"changeset"}, context)
    validate_studio_changeset(result["changeset"])


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
        or version not in {LEGACY_JOB_VERSION, MANAGED_JOB_VERSION, EXTERNAL_JOB_VERSION}
    ):
        raise StudioContractError("job format_version must be 1, 2 or 3")
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
    elif version == MANAGED_JOB_VERSION:
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
    else:
        if not isinstance(operation, str) or operation not in EXTERNAL_JOB_OPERATIONS:
            raise StudioContractError("job/operation is not an external executable operation")
        if job["state"] not in EXTERNAL_JOB_STATES:
            raise StudioContractError("job/state is not valid for an external job")
        _validate_external_job_input(operation, job["input"], "job/input")
        state = job["state"]
        if state == "succeeded":
            if job["result"] is None or job["error"] is not None:
                raise StudioContractError("a succeeded job requires result and forbids error")
            _validate_external_job_result(operation, job["result"], "job/result")
        elif state in {"failed", "orphaned"}:
            if job["result"] is not None or job["error"] is None:
                raise StudioContractError(
                    "a failed or orphaned job requires error and forbids result"
                )
            _validate_job_error(
                job["error"],
                "job/error",
                codes=EXTERNAL_JOB_ERROR_CODES,
            )
            if state == "orphaned" and job["error"]["code"] != "recovery_required":
                raise StudioContractError(
                    "an orphaned external job requires recovery_required error"
                )
        elif job["result"] is not None or job["error"] is not None:
            raise StudioContractError("only succeeded/failed jobs may carry result or error")
    _timestamp(job["created_at"], "job/created_at")
    _timestamp(job["updated_at"], "job/updated_at")
    return job


def _validate_external_grant_create_params(value: object, context: str) -> None:
    params = _object(value, context)
    allowed = {
        "grant_id",
        "workspace_id",
        "operation",
        "role",
        "artifact_kind",
        "display_name",
        "path",
        "expected_content_hash",
    }
    required = allowed - {"grant_id"}
    missing = required - set(params)
    unknown = set(params) - allowed
    if missing or unknown:
        fields = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(fields))}")
    if "grant_id" in params:
        _identifier(params["grant_id"], f"{context}/grant_id", ENTITY_ID_PATTERN)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    operation = params["operation"]
    if not isinstance(operation, str) or operation not in EXTERNAL_JOB_OPERATIONS:
        raise StudioContractError(f"{context}/operation is unknown")
    role = params["role"]
    if not isinstance(role, str) or role not in EXTERNAL_GRANT_ROLES:
        raise StudioContractError(f"{context}/role is unknown")
    if params["artifact_kind"] != EXTERNAL_OPERATION_KINDS[operation][role]:
        raise StudioContractError(f"{context}/artifact_kind is invalid")
    _plain_string(params["display_name"], f"{context}/display_name", max_length=128)
    path = _string(params["path"], f"{context}/path")
    assert path is not None
    if len(path) > 32_767 or "\x00" in path or unicodedata.normalize("NFC", path) != path:
        raise StudioContractError(f"{context}/path is invalid")
    _sha256(
        params["expected_content_hash"],
        f"{context}/expected_content_hash",
        nullable=True,
    )


def _validate_external_grant_id_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(params, {"grant_id"}, context)
    _identifier(params["grant_id"], f"{context}/grant_id", ENTITY_ID_PATTERN)


def _validate_job_recover_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(params, {"job_id", "action"}, context)
    _identifier(params["job_id"], f"{context}/job_id", ENTITY_ID_PATTERN)
    if params["action"] not in {"resume", "rollback"}:
        raise StudioContractError(f"{context}/action must be resume or rollback")


def _validate_job_id_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(params, {"job_id"}, context)
    _identifier(params["job_id"], f"{context}/job_id", ENTITY_ID_PATTERN)


def _validate_external_job_list_params(value: object, context: str) -> None:
    params = _object(value, context)
    unknown = set(params) - {"workspace_id", "state", "limit"}
    if unknown:
        raise StudioContractError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    if "workspace_id" in params:
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    if "state" in params:
        state = params["state"]
        if not isinstance(state, str) or state not in EXTERNAL_JOB_STATES:
            raise StudioContractError(f"{context}/state is unknown")
    if "limit" in params:
        limit = params["limit"]
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise StudioContractError(f"{context}/limit must be an integer from 1 to 1000")


def _validate_external_job_list_result(value: object, context: str) -> None:
    result = _object(value, context)
    _closed(result, {"jobs"}, context)
    jobs = result["jobs"]
    if not isinstance(jobs, list) or len(jobs) > 1000:
        raise StudioContractError(f"{context}/jobs must be an array with at most 1000 entries")
    for index, value in enumerate(jobs):
        job = validate_studio_job(value)
        if job["format_version"] != EXTERNAL_JOB_VERSION:
            raise StudioContractError(
                f"{context}/jobs/{index} v2 responses require an external v3 job version"
            )


def _validate_creation_root_grant_create_params(value: object, context: str) -> None:
    params = _object(value, context)
    allowed = {
        "grant_id",
        "role",
        "display_name",
        "path",
        "expected_project_hash",
    }
    required = allowed - {"grant_id"}
    missing = required - set(params)
    unknown = set(params) - allowed
    if missing or unknown:
        fields = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(fields))}")
    if "grant_id" in params:
        _identifier(params["grant_id"], f"{context}/grant_id", ENTITY_ID_PATTERN)
    role = params["role"]
    if not isinstance(role, str) or role not in CREATION_ROOT_GRANT_ROLES:
        raise StudioContractError(f"{context}/role is unknown")
    _plain_string(params["display_name"], f"{context}/display_name", max_length=128)
    path = _string(params["path"], f"{context}/path")
    assert path is not None
    if len(path) > 32_767 or "\x00" in path or unicodedata.normalize("NFC", path) != path:
        raise StudioContractError(f"{context}/path is invalid")
    expected_hash = _sha256(
        params["expected_project_hash"],
        f"{context}/expected_project_hash",
        nullable=True,
    )
    if role == "existing_root" and expected_hash is None:
        raise StudioContractError(f"{context}/expected_project_hash is required for existing_root")
    if role == "new_target" and expected_hash is not None:
        raise StudioContractError(f"{context}/expected_project_hash must be null for new_target")


def _validate_creation_root_grant_id_params(
    value: object,
    context: str,
    *,
    mutation: bool,
) -> None:
    params = _object(value, context)
    fields = {"grant_id", "expected_generation"} if mutation else {"grant_id"}
    _closed(params, fields, context)
    _identifier(params["grant_id"], f"{context}/grant_id", ENTITY_ID_PATTERN)
    if mutation:
        _integer(params["expected_generation"], f"{context}/expected_generation")


def _validate_creation_output_grant_create_params(
    value: object,
    context: str,
    *,
    allow_v6: bool = False,
) -> None:
    params = _object(value, context)
    fields = {"grant_id", "workspace_id", "kind", "display_name", "path"}
    required = fields - {"grant_id"}
    invalid = (required - set(params)) | (set(params) - fields)
    if invalid:
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(invalid))}")
    if "grant_id" in params:
        _identifier(params["grant_id"], f"{context}/grant_id", ENTITY_ID_PATTERN)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    allowed_kinds = CREATION_OUTPUT_GRANT_KINDS_V6 if allow_v6 else CREATION_OUTPUT_GRANT_KINDS
    if params["kind"] not in allowed_kinds:
        raise StudioContractError(f"{context}/kind is unknown")
    _plain_string(params["display_name"], f"{context}/display_name", max_length=128)
    path = _string(params["path"], f"{context}/path")
    assert path is not None
    if len(path) > 32_767 or "\x00" in path or unicodedata.normalize("NFC", path) != path:
        raise StudioContractError(f"{context}/path is invalid")


def _validate_creation_output_grant_id_params(
    value: object,
    context: str,
    *,
    mutation: bool,
) -> None:
    params = _object(value, context)
    fields = {"grant_id", "expected_generation"} if mutation else {"grant_id"}
    _closed(params, fields, context)
    _identifier(params["grant_id"], f"{context}/grant_id", ENTITY_ID_PATTERN)
    if mutation:
        _integer(params["expected_generation"], f"{context}/expected_generation")


def _validate_creation_output_grant_list_params(value: object, context: str) -> None:
    params = _object(value, context)
    fields = {
        "workspace_id",
        "expected_root_generation",
        "expected_source_revision",
        "expected_workflow_status_hash",
        "expected_artifact_snapshot_hash",
        "cursor",
        "limit",
    }
    _closed(params, fields, context)
    _validate_creation_authority_params(params, context)
    _sha256(
        params["expected_artifact_snapshot_hash"],
        f"{context}/expected_artifact_snapshot_hash",
    )
    if params["cursor"] is not None:
        _identifier(params["cursor"], f"{context}/cursor", ENTITY_ID_PATTERN)
    _integer(
        params["limit"],
        f"{context}/limit",
        minimum=1,
        maximum=MAX_CREATION_OUTPUT_GRANT_PAGE,
    )


def _validate_creation_workspace_create_params(
    value: object,
    context: str,
    *,
    allow_asset_content_mode: bool = False,
) -> None:
    params = _object(value, context)
    base_fields = {
        "workspace_id",
        "grant_id",
        "expected_grant_generation",
        "project_kind",
        "project_id",
        "title",
        "default_locale",
        "project_version",
    }
    facet_fields = {
        "gameplay_family",
        "initial_core_verb",
        "initial_core_loop",
        "world_presence",
        "narrative_requirement",
        "narrative_authorship",
        "narrative_topology",
        "presentation_mode",
        "runtime_support_intent",
        "asset_content_mode",
    }
    optional_game_fields = {"asset_content_mode"}
    required_facet_fields = facet_fields - optional_game_fields
    allowed = base_fields | facet_fields
    if not allow_asset_content_mode:
        allowed = allowed - optional_game_fields
    required = base_fields - {"workspace_id"}
    missing = required - set(params)
    unknown = set(params) - allowed
    if missing or unknown:
        fields = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(fields))}")
    if "workspace_id" in params:
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _identifier(params["grant_id"], f"{context}/grant_id", ENTITY_ID_PATTERN)
    _integer(
        params["expected_grant_generation"],
        f"{context}/expected_grant_generation",
    )
    if params["project_kind"] not in CREATION_PROJECT_KINDS:
        raise StudioContractError(f"{context}/project_kind is unsupported")
    _identifier(params["project_id"], f"{context}/project_id", ENTITY_ID_PATTERN)
    _plain_string(params["title"], f"{context}/title", max_length=256)
    _plain_string(params["default_locale"], f"{context}/default_locale", max_length=64)
    _plain_string(params["project_version"], f"{context}/project_version", max_length=64)
    if params["project_kind"] != "game":
        supplied = facet_fields.intersection(params)
        if supplied:
            raise StudioContractError(
                f"{context} library projects cannot include game facets: "
                + ", ".join(sorted(supplied))
            )
        return
    missing_facets = required_facet_fields - set(params)
    if missing_facets:
        raise StudioContractError(
            f"{context} game project is missing facets: " + ", ".join(sorted(missing_facets))
        )
    if params["gameplay_family"] not in CREATION_GAMEPLAY_FAMILIES:
        raise StudioContractError(f"{context}/gameplay_family is unsupported")
    if not is_creation_identifier(params["initial_core_verb"]):
        raise StudioContractError(f"{context}/initial_core_verb is invalid")
    initial_core_loop = _plain_string(
        params["initial_core_loop"], f"{context}/initial_core_loop"
    ).strip()
    if not initial_core_loop or len(initial_core_loop) > 512:
        raise StudioContractError(f"{context}/initial_core_loop is invalid")
    if params["world_presence"] not in CREATION_WORLD_PRESENCES:
        raise StudioContractError(f"{context}/world_presence is unsupported")
    requirement = params["narrative_requirement"]
    authorship = params["narrative_authorship"]
    topology = params["narrative_topology"]
    if requirement not in CREATION_NARRATIVE_REQUIREMENTS:
        raise StudioContractError(f"{context}/narrative_requirement is unsupported")
    if authorship not in CREATION_NARRATIVE_AUTHORSHIP:
        raise StudioContractError(f"{context}/narrative_authorship is unsupported")
    if topology not in CREATION_NARRATIVE_TOPOLOGIES:
        raise StudioContractError(f"{context}/narrative_topology is unsupported")
    if requirement == "none" and (authorship != "none" or topology != "none"):
        raise StudioContractError(f"{context} narrative:none has incompatible facets")
    if requirement != "none" and (authorship == "none" or topology == "none"):
        raise StudioContractError(f"{context} narrative facets must be explicit")
    if params["presentation_mode"] not in CREATION_PRESENTATION_MODES:
        raise StudioContractError(f"{context}/presentation_mode is unsupported")
    if params["runtime_support_intent"] not in CREATION_RUNTIME_SUPPORT_INTENTS:
        raise StudioContractError(f"{context}/runtime_support_intent is unsupported")
    if (
        "asset_content_mode" in params
        and params["asset_content_mode"] not in CREATION_CONTENT_MODES
    ):
        raise StudioContractError(f"{context}/asset_content_mode is unsupported")


def _validate_creation_workspace_register_params(value: object, context: str) -> None:
    params = _object(value, context)
    allowed = {
        "workspace_id",
        "grant_id",
        "expected_grant_generation",
        "expected_project_hash",
    }
    required = allowed - {"workspace_id"}
    missing = required - set(params)
    unknown = set(params) - allowed
    if missing or unknown:
        fields = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(fields))}")
    if "workspace_id" in params:
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _identifier(params["grant_id"], f"{context}/grant_id", ENTITY_ID_PATTERN)
    _integer(
        params["expected_grant_generation"],
        f"{context}/expected_grant_generation",
    )
    _sha256(params["expected_project_hash"], f"{context}/expected_project_hash")


def _validate_creation_workspace_id_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(params, {"workspace_id"}, context)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)


def _validate_creation_evidence_authority_params(
    value: object,
    context: str,
    *,
    method: str,
) -> None:
    params = _object(value, context)
    common = {
        "workspace_id",
        "expected_root_generation",
        "expected_source_revision",
        "expected_workflow_status_hash",
        "expected_artifact_snapshot_hash",
    }
    fields = (
        common | {"lifecycle", "cursor", "limit"}
        if method == "creation_artifact.list"
        else common | {"artifact_id"}
        if method == "creation_artifact.inspect"
        else common
    )
    _closed(params, fields, context)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _integer(params["expected_root_generation"], f"{context}/expected_root_generation")
    _sha256(params["expected_source_revision"], f"{context}/expected_source_revision")
    _sha256(
        params["expected_workflow_status_hash"],
        f"{context}/expected_workflow_status_hash",
        nullable=True,
    )
    _sha256(
        params["expected_artifact_snapshot_hash"],
        f"{context}/expected_artifact_snapshot_hash",
        nullable=True,
    )
    if method == "creation_artifact.list":
        lifecycle = params["lifecycle"]
        if lifecycle is not None and lifecycle not in CREATION_ARTIFACT_LIFECYCLES:
            raise StudioContractError(f"{context}/lifecycle is unknown")
        cursor = params["cursor"]
        if cursor is not None:
            _identifier(cursor, f"{context}/cursor", ENTITY_ID_PATTERN)
            if params["expected_artifact_snapshot_hash"] is None:
                raise StudioContractError(
                    f"{context}/expected_artifact_snapshot_hash is required after page one"
                )
        _integer(params["limit"], f"{context}/limit", minimum=1, maximum=MAX_CREATION_ARTIFACT_PAGE)
    elif method == "creation_artifact.inspect":
        _identifier(params["artifact_id"], f"{context}/artifact_id", ENTITY_ID_PATTERN)
        if params["expected_artifact_snapshot_hash"] is None:
            raise StudioContractError(
                f"{context}/expected_artifact_snapshot_hash is required for inspection"
            )


def _validate_creation_preview_params(
    method: str,
    value: object,
    context: str,
    *,
    allow_pre_release: bool = False,
) -> None:
    params = _object(value, context)
    if method == "creation_preview.open":
        if allow_pre_release and params.get("source_kind") == "qa_review_candidate":
            fields = {
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
            _closed(params, fields, context)
            _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
            _integer(params["expected_root_generation"], f"{context}/expected_root_generation")
            _sha256(params["expected_source_revision"], f"{context}/expected_source_revision")
            _sha256(
                params["expected_workflow_status_hash"],
                f"{context}/expected_workflow_status_hash",
                nullable=True,
            )
            _sha256(
                params["expected_artifact_snapshot_hash"],
                f"{context}/expected_artifact_snapshot_hash",
            )
            for field in ("qa_report_artifact_id", "asset_id", "output_role"):
                _identifier(params[field], f"{context}/{field}", ENTITY_ID_PATTERN)
            return
        fields = {
            "workspace_id",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
            "assetpack_artifact_id",
            "output_grant_id",
            "expected_output_grant_generation",
            "asset_id",
        }
        _closed(params, fields, context)
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
        _integer(params["expected_root_generation"], f"{context}/expected_root_generation")
        _sha256(params["expected_source_revision"], f"{context}/expected_source_revision")
        _sha256(
            params["expected_workflow_status_hash"],
            f"{context}/expected_workflow_status_hash",
            nullable=True,
        )
        _sha256(
            params["expected_artifact_snapshot_hash"],
            f"{context}/expected_artifact_snapshot_hash",
        )
        for field in ("assetpack_artifact_id", "output_grant_id", "asset_id"):
            _identifier(params[field], f"{context}/{field}", ENTITY_ID_PATTERN)
        _integer(
            params["expected_output_grant_generation"],
            f"{context}/expected_output_grant_generation",
        )
        return
    if method == "creation_preview.read":
        _closed(params, {"handle", "sequence"}, context)
        _identifier(params["handle"], f"{context}/handle", ASSET_PREVIEW_HANDLE_PATTERN)
        _integer(
            params["sequence"],
            f"{context}/sequence",
            maximum=MAX_CREATION_PREVIEW_SEQUENCE,
        )
        return
    _closed(params, {"handle"}, context)
    _identifier(params["handle"], f"{context}/handle", ASSET_PREVIEW_HANDLE_PATTERN)


def _validate_creation_job_v4_params(method: str, value: object, context: str) -> None:
    params = _object(value, context)
    if method == "creation_job.create":
        common = {
            "job_id",
            "workspace_id",
            "operation",
            "expected_root_generation",
            "expected_source_revision",
            "expected_workflow_status_hash",
            "expected_artifact_snapshot_hash",
        }
        operation = params.get("operation")
        fields = (
            common
            if operation == "creation.compile"
            else common | {"document", "dependency_artifact_ids"}
            if operation == "artifact.admit"
            else common
            | {
                "license_artifact_ids",
                "recipe_id",
                "processing_receipt_id",
                "qa_report_id",
                "acceptance_results",
            }
            if operation == "asset.process"
            else common
            | {
                "qa_report_artifact_ids",
                "manifest_id",
                "target_grant_id",
                "expected_target_grant_generation",
            }
            if operation == "asset.release.seal"
            else common
            | {
                "gamepack_artifact_id",
                "asset_inventory_artifact_id",
                "assetpack_artifact_id",
                "target_grant_id",
                "expected_target_grant_generation",
            }
            if operation == "runtime.compose"
            else common
            | {
                "gamepack_artifact_id",
                "asset_inventory_artifact_id",
                "assetpack_artifact_id",
                "runtime_snapshot_artifact_id",
                "runtime_adapter_registry_artifact_id",
                "runtime_composition_artifact_id",
                "runtime_support_report_artifact_id",
                "source_grant_id",
                "expected_source_grant_generation",
                "target_grant_id",
                "expected_target_grant_generation",
            }
            if operation == "runtime.bundle.build"
            else common
            | {
                "runtime_bundle_artifact_id",
                "source_grant_id",
                "expected_source_grant_generation",
                "target_grant_id",
                "expected_target_grant_generation",
            }
            if operation == "game.materialization.bundle.build"
            else common
            | {
                "materialization_bundle_artifact_id",
                "source_grant_id",
                "expected_source_grant_generation",
                "target_grant_id",
                "expected_target_grant_generation",
            }
            if operation == "game.materialize"
            else common
            | {
                "standalone_game_artifact_id",
                "source_grant_id",
                "expected_source_grant_generation",
                "target_grant_id",
                "expected_target_grant_generation",
            }
            if operation == "game.package"
            else common
            | {
                "game_package_artifact_id",
                "source_grant_id",
                "expected_source_grant_generation",
                "target_grant_id",
                "expected_target_grant_generation",
            }
            if operation == "game.package.extract"
            else common
        )
        required = fields - {"job_id"}
        invalid = (required - set(params)) | (set(params) - fields)
        if invalid:
            raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(invalid))}")
        if "job_id" in params:
            _identifier(params["job_id"], f"{context}/job_id", ENTITY_ID_PATTERN)
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
        if operation not in CREATION_JOB_OPERATIONS - (
            CREATION_JOB_OPERATIONS_V10 | CREATION_JOB_OPERATIONS_V11 | CREATION_JOB_OPERATIONS_V12
        ):
            raise StudioContractError(f"{context}/operation is unknown")
        _integer(params["expected_root_generation"], f"{context}/expected_root_generation")
        _sha256(params["expected_source_revision"], f"{context}/expected_source_revision")
        _sha256(
            params["expected_workflow_status_hash"],
            f"{context}/expected_workflow_status_hash",
            nullable=True,
        )
        _sha256(
            params["expected_artifact_snapshot_hash"],
            f"{context}/expected_artifact_snapshot_hash",
        )
        if operation == "artifact.admit":
            document = _object(params["document"], f"{context}/document")
            _strict_json_value(document, f"{context}/document")
            if len(canonical_json_bytes(document)) > MAX_CREATION_ADMISSION_DOCUMENT_BYTES:
                raise StudioContractError(f"{context}/document exceeds its byte limit")
            dependencies = params["dependency_artifact_ids"]
            if not isinstance(dependencies, list) or len(dependencies) > MAX_CREATION_JOB_INPUTS:
                raise StudioContractError(f"{context}/dependency_artifact_ids is invalid")
            checked = [
                _identifier(item, f"{context}/dependency_artifact_ids/{index}", ENTITY_ID_PATTERN)
                for index, item in enumerate(dependencies)
            ]
            if checked != sorted(set(checked), key=lambda item: item.encode("utf-8")):
                raise StudioContractError(
                    f"{context}/dependency_artifact_ids must be unique and canonical"
                )
        elif operation == "asset.process":
            _validate_asset_process_operation_params(
                {
                    field: params[field]
                    for field in (
                        "license_artifact_ids",
                        "recipe_id",
                        "processing_receipt_id",
                        "qa_report_id",
                        "acceptance_results",
                    )
                },
                context,
            )
        elif operation == "asset.release.seal":
            _validate_asset_release_seal_operation_params(
                {
                    "qa_report_artifact_ids": params["qa_report_artifact_ids"],
                    "manifest_id": params["manifest_id"],
                    "target_grant_id": params["target_grant_id"],
                    "target_grant_generation": params["expected_target_grant_generation"],
                },
                context,
            )
        elif operation == "runtime.compose":
            _validate_runtime_compose_operation_params(
                {
                    "gamepack_artifact_id": params["gamepack_artifact_id"],
                    "asset_inventory_artifact_id": params["asset_inventory_artifact_id"],
                    "assetpack_artifact_id": params["assetpack_artifact_id"],
                    "target_grant_id": params["target_grant_id"],
                    "target_grant_generation": params["expected_target_grant_generation"],
                },
                context,
            )
        elif operation == "runtime.bundle.build":
            _validate_runtime_bundle_operation_params(
                {
                    "gamepack_artifact_id": params["gamepack_artifact_id"],
                    "asset_inventory_artifact_id": params["asset_inventory_artifact_id"],
                    "assetpack_artifact_id": params["assetpack_artifact_id"],
                    "runtime_snapshot_artifact_id": params["runtime_snapshot_artifact_id"],
                    "runtime_adapter_registry_artifact_id": params[
                        "runtime_adapter_registry_artifact_id"
                    ],
                    "runtime_composition_artifact_id": params["runtime_composition_artifact_id"],
                    "runtime_support_report_artifact_id": params[
                        "runtime_support_report_artifact_id"
                    ],
                    "source_grant_id": params["source_grant_id"],
                    "source_grant_generation": params["expected_source_grant_generation"],
                    "target_grant_id": params["target_grant_id"],
                    "target_grant_generation": params["expected_target_grant_generation"],
                },
                context,
            )
        elif operation == "game.materialization.bundle.build":
            _validate_materialization_bundle_operation_params(
                {
                    "runtime_bundle_artifact_id": params["runtime_bundle_artifact_id"],
                    "source_grant_id": params["source_grant_id"],
                    "source_grant_generation": params["expected_source_grant_generation"],
                    "target_grant_id": params["target_grant_id"],
                    "target_grant_generation": params["expected_target_grant_generation"],
                },
                context,
            )
        elif operation == "game.materialize":
            _validate_game_materialize_operation_params(
                {
                    "materialization_bundle_artifact_id": params[
                        "materialization_bundle_artifact_id"
                    ],
                    "source_grant_id": params["source_grant_id"],
                    "source_grant_generation": params["expected_source_grant_generation"],
                    "target_grant_id": params["target_grant_id"],
                    "target_grant_generation": params["expected_target_grant_generation"],
                },
                context,
            )
        elif operation == "game.package":
            _validate_game_package_operation_params(
                {
                    "standalone_game_artifact_id": params["standalone_game_artifact_id"],
                    "source_grant_id": params["source_grant_id"],
                    "source_grant_generation": params["expected_source_grant_generation"],
                    "target_grant_id": params["target_grant_id"],
                    "target_grant_generation": params["expected_target_grant_generation"],
                },
                context,
            )
        elif operation == "game.package.extract":
            _validate_game_package_extract_operation_params(
                {
                    "game_package_artifact_id": params["game_package_artifact_id"],
                    "source_grant_id": params["source_grant_id"],
                    "source_grant_generation": params["expected_source_grant_generation"],
                    "target_grant_id": params["target_grant_id"],
                    "target_grant_generation": params["expected_target_grant_generation"],
                },
                context,
            )
        return
    if method == "creation_job.get":
        _closed(params, {"job_id"}, context)
        _identifier(params["job_id"], f"{context}/job_id", ENTITY_ID_PATTERN)
        return
    if method == "creation_job.list":
        _closed(params, {"workspace_id", "state", "after_sequence", "limit"}, context)
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
        if params["state"] is not None and params["state"] not in CREATION_JOB_STATES:
            raise StudioContractError(f"{context}/state is unknown")
        _integer(params["after_sequence"], f"{context}/after_sequence")
        _integer(
            params["limit"],
            f"{context}/limit",
            minimum=1,
            maximum=MAX_CREATION_JOB_PAGE,
        )
        return
    if method in {"creation_job.cancel", "creation_job.recover"}:
        fields = {"job_id", "expected_generation", "expected_record_hash"}
        if method == "creation_job.recover":
            fields.add("mode")
        _closed(params, fields, context)
        _identifier(params["job_id"], f"{context}/job_id", ENTITY_ID_PATTERN)
        _integer(params["expected_generation"], f"{context}/expected_generation")
        _sha256(params["expected_record_hash"], f"{context}/expected_record_hash")
        if method == "creation_job.recover" and params["mode"] not in {
            "resume",
            "rollback",
            "cleanup",
        }:
            raise StudioContractError(f"{context}/mode is unknown")
        return
    if method == "creation_event.list":
        _closed(params, {"workspace_id", "after_id", "limit"}, context)
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
        _integer(params["after_id"], f"{context}/after_id")
        _integer(
            params["limit"],
            f"{context}/limit",
            minimum=1,
            maximum=MAX_CREATION_EVENT_PAGE,
        )
        return
    raise StudioContractError("envelope/method is unknown")


def _validate_creation_job_v5_params(method: str, value: object, context: str) -> None:
    params = _object(value, context)
    if method != "creation_job.create":
        _validate_creation_job_v4_params(method, params, context)
        return
    operation = params.get("operation")
    if operation in CREATION_JOB_OPERATIONS - (
        CREATION_JOB_OPERATIONS_V10 | CREATION_JOB_OPERATIONS_V11 | CREATION_JOB_OPERATIONS_V12
    ):
        _validate_creation_job_v4_params(method, params, context)
        return
    common = {
        "job_id",
        "workspace_id",
        "operation",
        "expected_root_generation",
        "expected_source_revision",
        "expected_workflow_status_hash",
        "expected_artifact_snapshot_hash",
    }
    operation_fields = (
        {
            "qa_report_artifact_id",
            "output_role",
            "review_receipt_id",
            "decisions",
            "blockers",
        }
        if operation == "asset.qa.review"
        else {
            "review_receipt_artifact_ids",
            "manifest_id",
            "assetpack_id",
            "release_authority_id",
            "blockers",
            "target_grant_id",
            "expected_target_grant_generation",
        }
        if operation == "asset.release.authorize"
        else {
            "gamepack_artifact_id",
            "asset_inventory_artifact_id",
            "assetpack_artifact_id",
            "asset_release_authority_artifact_id",
            "runtime_snapshot_artifact_id",
            "runtime_adapter_registry_artifact_id",
            "runtime_composition_artifact_id",
            "runtime_bundle_artifact_id",
            "headless_script_artifact_id",
            "source_grant_id",
            "expected_source_grant_generation",
            "target_grant_id",
            "expected_target_grant_generation",
            "platform_id",
        }
        if operation == "runtime.headless.verify"
        else set()
    )
    if not operation_fields:
        raise StudioContractError(f"{context}/operation is unknown")
    fields = common | operation_fields
    required = fields - {"job_id"}
    invalid = (required - set(params)) | (set(params) - fields)
    if invalid:
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(invalid))}")
    if "job_id" in params:
        _identifier(params["job_id"], f"{context}/job_id", ENTITY_ID_PATTERN)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _integer(params["expected_root_generation"], f"{context}/expected_root_generation")
    _sha256(params["expected_source_revision"], f"{context}/expected_source_revision")
    _sha256(
        params["expected_workflow_status_hash"],
        f"{context}/expected_workflow_status_hash",
        nullable=True,
    )
    _sha256(
        params["expected_artifact_snapshot_hash"],
        f"{context}/expected_artifact_snapshot_hash",
    )
    specific = {field: params[field] for field in operation_fields if field in params}
    if operation == "asset.qa.review":
        _validate_asset_qa_review_operation_params(specific, context)
    elif operation == "asset.release.authorize":
        _validate_asset_release_authorize_operation_params(
            {
                **{
                    field: specific[field]
                    for field in (
                        "review_receipt_artifact_ids",
                        "manifest_id",
                        "assetpack_id",
                        "release_authority_id",
                        "blockers",
                        "target_grant_id",
                    )
                },
                "target_grant_generation": specific["expected_target_grant_generation"],
            },
            context,
        )
    else:
        _validate_runtime_headless_verify_operation_params(
            specific,
            context,
        )


def _validate_creation_workspace_recover_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(params, {"workspace_id", "expected_root_generation"}, context)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _integer(params["expected_root_generation"], f"{context}/expected_root_generation")


def _validate_creation_revision_params(
    value: object,
    context: str,
    *,
    include_path: bool,
) -> None:
    params = _object(value, context)
    fields = {"workspace_id", "expected_source_revision"}
    if include_path:
        fields.add("path")
    _closed(params, fields, context)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _sha256(
        params["expected_source_revision"],
        f"{context}/expected_source_revision",
    )
    if include_path:
        relative = portable_relative_path(params["path"])
        if relative is None:
            raise StudioContractError(f"{context}/path must be a portable relative path")


def _validate_creation_changeset_id_params(value: object, context: str) -> None:
    params = _object(value, context)
    _closed(params, {"changeset_id"}, context)
    _identifier(params["changeset_id"], f"{context}/changeset_id", ENTITY_ID_PATTERN)


def _validate_creation_changeset_input_operation(value: object, context: str) -> None:
    operation = _object(value, context)
    common = {
        "operation",
        "path",
        "expected_base_file_sha256",
        "expected_base_size",
        "proposed_file_sha256",
        "proposed_size",
    }
    kind = operation.get("operation")
    if kind not in {"create", "replace", "delete"}:
        raise StudioContractError(f"{context}/operation is unknown")
    fields = common if kind == "delete" else common | {"document"}
    _closed(operation, fields, context)
    relative = portable_relative_path(operation["path"])
    if relative is None or len(str(operation["path"])) > 1024:
        raise StudioContractError(f"{context}/path must be a portable relative path")
    base_hash = _sha256(
        operation["expected_base_file_sha256"],
        f"{context}/expected_base_file_sha256",
        nullable=True,
    )
    proposed_hash = _sha256(
        operation["proposed_file_sha256"],
        f"{context}/proposed_file_sha256",
        nullable=True,
    )
    base_size = operation["expected_base_size"]
    proposed_size = operation["proposed_size"]
    if base_size is not None:
        _integer(base_size, f"{context}/expected_base_size", maximum=MAX_CHANGE_FILE_BYTES)
    if proposed_size is not None:
        _integer(proposed_size, f"{context}/proposed_size", maximum=MAX_CHANGE_FILE_BYTES)
    if kind == "create" and (base_hash is not None or base_size is not None):
        raise StudioContractError(f"{context} create operation cannot have a base")
    if kind != "create" and (base_hash is None or base_size is None):
        raise StudioContractError(f"{context} {kind} operation requires an exact base")
    if kind == "delete":
        if proposed_hash is not None or proposed_size is not None:
            raise StudioContractError(f"{context} delete operation cannot have a proposal")
    else:
        if proposed_hash is None or proposed_size is None:
            raise StudioContractError(f"{context} {kind} operation requires an exact proposal")
        document = _object(operation["document"], f"{context}/document")
        _strict_json_value(document, f"{context}/document")


def _validate_creation_changeset_create_params(value: object, context: str) -> None:
    params = _object(value, context)
    required = {
        "workspace_id",
        "expected_root_generation",
        "expected_source_revision",
        "expected_workflow_status_hash",
        "operations",
    }
    allowed = required | {"changeset_id"}
    missing = required - set(params)
    unknown = set(params) - allowed
    if missing or unknown:
        fields = missing or unknown
        raise StudioContractError(f"{context} has invalid fields: {', '.join(sorted(fields))}")
    if "changeset_id" in params:
        _identifier(params["changeset_id"], f"{context}/changeset_id", ENTITY_ID_PATTERN)
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _integer(params["expected_root_generation"], f"{context}/expected_root_generation")
    _sha256(params["expected_source_revision"], f"{context}/expected_source_revision")
    _sha256(
        params["expected_workflow_status_hash"],
        f"{context}/expected_workflow_status_hash",
        nullable=True,
    )
    operations = params["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_CHANGESET_OPERATIONS:
        raise StudioContractError(
            f"{context}/operations must contain 1 to {MAX_CHANGESET_OPERATIONS} entries"
        )
    for index, operation in enumerate(operations):
        _validate_creation_changeset_input_operation(operation, f"{context}/operations/{index}")


def _validate_creation_changeset_list_params(value: object, context: str) -> None:
    params = _object(value, context)
    allowed = {"workspace_id", "status", "limit"}
    unknown = set(params) - allowed
    if unknown:
        raise StudioContractError(
            f"{context} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    if "workspace_id" in params:
        _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    if "status" in params and params["status"] not in CREATION_CHANGESET_STATES:
        raise StudioContractError(f"{context}/status is unknown")
    if "limit" in params:
        _integer(params["limit"], f"{context}/limit", minimum=1, maximum=1000)


def _validate_creation_changeset_action_params(
    value: object,
    context: str,
    *,
    include_generation: bool,
    include_mode: bool,
) -> None:
    params = _object(value, context)
    fields = {"changeset_id", "expected_record_hash", "expected_review_sha256"}
    if include_generation:
        fields.add("expected_root_generation")
    if include_mode:
        fields.add("mode")
    _closed(params, fields, context)
    _identifier(params["changeset_id"], f"{context}/changeset_id", ENTITY_ID_PATTERN)
    _sha256(params["expected_record_hash"], f"{context}/expected_record_hash")
    _sha256(params["expected_review_sha256"], f"{context}/expected_review_sha256")
    if include_generation:
        _integer(params["expected_root_generation"], f"{context}/expected_root_generation")
    if include_mode and params["mode"] not in {"resume", "rollback"}:
        raise StudioContractError(f"{context}/mode must be resume or rollback")


def _validate_creation_authority_params(
    params: dict[str, Any],
    context: str,
) -> None:
    _identifier(params["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _integer(params["expected_root_generation"], f"{context}/expected_root_generation")
    _sha256(params["expected_source_revision"], f"{context}/expected_source_revision")
    _sha256(
        params["expected_workflow_status_hash"],
        f"{context}/expected_workflow_status_hash",
        nullable=True,
    )


def _validate_inline_artifact_registry(value: object, context: str) -> None:
    if not isinstance(value, list) or len(value) > 1024:
        raise StudioContractError(f"{context} must be an array with at most 1024 entries")
    for index, document in enumerate(value):
        _object(document, f"{context}/{index}")
        _strict_json_value(document, f"{context}/{index}")


def _validate_creation_phase_params(method: str, value: object, context: str) -> None:
    params = _object(value, context)
    authority = {
        "workspace_id",
        "expected_root_generation",
        "expected_source_revision",
        "expected_workflow_status_hash",
    }
    if method == "creation_workflow.reconcile":
        fields = authority | {"artifact_registry"}
    elif method == "creation_phase.read":
        fields = authority | {"phase_id"}
    elif method in {"creation_phase.validate", "creation_phase.complete"}:
        fields = authority | {"report", "artifact_registry"}
    elif method == "creation_phase.reopen":
        fields = authority | {"phase_id", "reason", "approved_by"}
    else:  # pragma: no cover - callers discriminate the method first
        raise StudioContractError("envelope/method is unknown")
    _closed(params, fields, context)
    _validate_creation_authority_params(params, context)
    if "artifact_registry" in fields:
        _validate_inline_artifact_registry(
            params["artifact_registry"],
            f"{context}/artifact_registry",
        )
    if "report" in fields:
        report = _object(params["report"], f"{context}/report")
        _strict_json_value(report, f"{context}/report")
    if method in {"creation_phase.read", "creation_phase.complete", "creation_phase.reopen"}:
        _sha256(
            params["expected_workflow_status_hash"],
            f"{context}/expected_workflow_status_hash",
        )
    if method in {"creation_phase.read", "creation_phase.reopen"}:
        _identifier(params["phase_id"], f"{context}/phase_id", OPERATION_PATTERN)
    if method == "creation_phase.reopen":
        reason = _plain_string(params["reason"], f"{context}/reason", max_length=512)
        if not reason.strip():
            raise StudioContractError(f"{context}/reason cannot be blank")
        _identifier(params["approved_by"], f"{context}/approved_by", ENTITY_ID_PATTERN)


def _validate_creation_document_summary(value: object, context: str) -> None:
    summary = _object(value, context)
    _closed(
        summary,
        {"path", "format", "format_version", "id", "content_hash", "file_sha256"},
        context,
    )
    if portable_relative_path(summary["path"]) is None:
        raise StudioContractError(f"{context}/path must be a portable relative path")
    _plain_string(summary["format"], f"{context}/format", max_length=128)
    _integer(summary["format_version"], f"{context}/format_version", minimum=1)
    _identifier(summary["id"], f"{context}/id", ENTITY_ID_PATTERN)
    _sha256(summary["content_hash"], f"{context}/content_hash")
    _sha256(summary["file_sha256"], f"{context}/file_sha256")


def _validate_creation_workspace_open_result(value: object, context: str) -> None:
    result = _object(value, context)
    _closed(
        result,
        {
            "workspace",
            "route",
            "project_kind",
            "source_revision",
            "workflow_status_hash",
            "current_phase",
        },
        context,
    )
    workspace = validate_studio_creation_workspace(result["workspace"])
    if result["route"] != "generic":
        raise StudioContractError(f"{context}/route must be generic")
    if result["project_kind"] != workspace["project_kind"]:
        raise StudioContractError(f"{context}/project_kind does not match workspace")
    if result["source_revision"] != workspace["source_revision"]:
        raise StudioContractError(f"{context}/source_revision does not match workspace")
    if result["workflow_status_hash"] != workspace["workflow_status_hash"]:
        raise StudioContractError(f"{context}/workflow_status_hash does not match workspace")
    _string(result["current_phase"], f"{context}/current_phase", nullable=True)


def _validate_creation_workflow_result(value: object, context: str) -> None:
    result = _object(value, context)
    _closed(result, {"workflow"}, context)
    workflow = _object(result["workflow"], f"{context}/workflow")
    _closed(
        workflow,
        {
            "state",
            "source_revision",
            "status_hash",
            "current_phase",
            "revision",
            "status",
        },
        f"{context}/workflow",
    )
    if workflow["state"] not in {"missing", "not_started", "active", "complete", "invalid"}:
        raise StudioContractError(f"{context}/workflow/state is unknown")
    _sha256(workflow["source_revision"], f"{context}/workflow/source_revision")
    _sha256(
        workflow["status_hash"],
        f"{context}/workflow/status_hash",
        nullable=True,
    )
    _string(workflow["current_phase"], f"{context}/workflow/current_phase", nullable=True)
    revision = workflow["revision"]
    if revision is not None:
        _integer(revision, f"{context}/workflow/revision")
    status = workflow["status"]
    if status is not None:
        _object(status, f"{context}/workflow/status")
        _strict_json_value(status, f"{context}/workflow/status")


def _validate_creation_readiness_result(value: object, context: str) -> None:
    result = _object(value, context)
    _closed(result, {"readiness"}, context)
    readiness = _object(result["readiness"], f"{context}/readiness")
    _closed(
        readiness,
        {
            "state",
            "source_revision",
            "workflow_status_hash",
            "current_phase",
            "release",
            "blocker_reason_codes",
            "report",
        },
        f"{context}/readiness",
    )
    if readiness["state"] not in CREATION_READINESS_STATES:
        raise StudioContractError(f"{context}/readiness/state is unknown")
    _sha256(readiness["source_revision"], f"{context}/readiness/source_revision")
    _sha256(
        readiness["workflow_status_hash"],
        f"{context}/readiness/workflow_status_hash",
        nullable=True,
    )
    _string(readiness["current_phase"], f"{context}/readiness/current_phase", nullable=True)
    if readiness["release"] not in {"blocked", "ready"}:
        raise StudioContractError(f"{context}/readiness/release is unknown")
    blockers = readiness["blocker_reason_codes"]
    if not isinstance(blockers, list) or len(blockers) > 128:
        raise StudioContractError(f"{context}/readiness/blocker_reason_codes is invalid")
    for index, blocker in enumerate(blockers):
        _identifier(
            blocker,
            f"{context}/readiness/blocker_reason_codes/{index}",
            OPERATION_PATTERN,
        )
    report = readiness["report"]
    if report is not None:
        _object(report, f"{context}/readiness/report")
        _strict_json_value(report, f"{context}/readiness/report")


def _validate_creation_changeset_diff(value: object, context: str) -> None:
    diff = _object(value, context)
    _closed(
        diff,
        {
            "changeset_id",
            "workspace_id",
            "expected_source_revision",
            "proposed_source_revision",
            "review_sha256",
            "operations",
        },
        context,
    )
    _identifier(diff["changeset_id"], f"{context}/changeset_id", ENTITY_ID_PATTERN)
    _identifier(diff["workspace_id"], f"{context}/workspace_id", WORKSPACE_ID_PATTERN)
    _sha256(diff["expected_source_revision"], f"{context}/expected_source_revision")
    _sha256(diff["proposed_source_revision"], f"{context}/proposed_source_revision")
    _sha256(diff["review_sha256"], f"{context}/review_sha256")
    operations = diff["operations"]
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_CHANGESET_OPERATIONS:
        raise StudioContractError(f"{context}/operations is invalid")
    for index, item in enumerate(operations):
        operation = _object(item, f"{context}/operations/{index}")
        _closed(
            operation,
            {
                "operation",
                "path",
                "expected_base_file_sha256",
                "expected_base_size",
                "proposed_file_sha256",
                "proposed_size",
                "size_delta",
            },
            f"{context}/operations/{index}",
        )
        _validate_creation_changeset_operation(
            {key: value for key, value in operation.items() if key != "size_delta"},
            f"{context}/operations/{index}",
        )
        size_delta = operation["size_delta"]
        if (
            isinstance(size_delta, bool)
            or not isinstance(size_delta, int)
            or not -MAX_CHANGE_FILE_BYTES <= size_delta <= MAX_CHANGE_FILE_BYTES
        ):
            raise StudioContractError(f"{context}/operations/{index}/size_delta is invalid")


def _validate_creation_workspace_workflow_result(
    value: object,
    context: str,
    *,
    extra_fields: set[str] = frozenset(),
) -> dict[str, Any]:
    result = _object(value, context)
    _closed(result, {"workspace", "workflow"} | extra_fields, context)
    workspace = validate_studio_creation_workspace(result["workspace"])
    _validate_creation_workflow_result({"workflow": result["workflow"]}, context)
    workflow = _object(result["workflow"], f"{context}/workflow")
    if workflow["source_revision"] != workspace["source_revision"]:
        raise StudioContractError(f"{context}/workflow source revision does not match workspace")
    if workflow["status_hash"] != workspace["workflow_status_hash"]:
        raise StudioContractError(f"{context}/workflow status hash does not match workspace")
    return result


def _validate_creation_response(method: str, value: object, context: str) -> None:
    result = _object(value, context)
    if method == "service.initialize":
        _closed(
            result,
            {
                "service",
                "service_version",
                "protocol",
                "protocol_version",
                "methods",
                "capabilities",
            },
            context,
        )
        if (
            result["service"] != "world-forge.studio"
            or result["service_version"] != STUDIO_PROTOCOL_V3
            or result["protocol"] != PROTOCOL_FORMAT
            or result["protocol_version"] != STUDIO_PROTOCOL_V3
            or result["methods"] != sorted(METHODS_V3)
        ):
            raise StudioContractError(f"{context} does not describe Studio protocol v3")
        capabilities = _object(result["capabilities"], f"{context}/capabilities")
        _closed(
            capabilities,
            {
                "generic_creation",
                "safe_project_creation",
                "read_only_documents",
                "profile_editing",
                "generic_jobs",
                "reviewed_changesets",
                "workflow_mutations",
                "inline_phase_reports",
            },
            f"{context}/capabilities",
        )
        expected_capabilities = {
            "generic_creation": True,
            "safe_project_creation": True,
            "read_only_documents": True,
            "profile_editing": True,
            "generic_jobs": False,
            "reviewed_changesets": True,
            "workflow_mutations": True,
            "inline_phase_reports": True,
        }
        if capabilities != expected_capabilities:
            raise StudioContractError(
                f"{context}/capabilities does not describe Studio protocol v3"
            )
    elif method.startswith("creation_root_grant."):
        _closed(result, {"grant"}, context)
        validate_studio_creation_root_grant(result["grant"])
    elif method in {
        "creation_workspace.create",
        "creation_workspace.register",
        "creation_workspace.get",
    }:
        _closed(result, {"workspace"}, context)
        validate_studio_creation_workspace(result["workspace"])
    elif method == "creation_workspace.recover":
        _closed(result, {"workspace", "state"}, context)
        validate_studio_creation_workspace(result["workspace"])
        if result["state"] not in {"complete", "cleanup_pending"}:
            raise StudioContractError(f"{context}/state is unknown")
    elif method == "creation_workspace.list":
        _closed(result, {"workspaces"}, context)
        workspaces = result["workspaces"]
        if not isinstance(workspaces, list) or len(workspaces) > 1000:
            raise StudioContractError(f"{context}/workspaces is invalid")
        for workspace in workspaces:
            validate_studio_creation_workspace(workspace)
    elif method == "creation_workspace.open":
        _validate_creation_workspace_open_result(result, context)
    elif method == "creation_document.list":
        _closed(result, {"documents", "source_revision"}, context)
        _sha256(result["source_revision"], f"{context}/source_revision")
        documents = result["documents"]
        if not isinstance(documents, list) or len(documents) > 1024:
            raise StudioContractError(f"{context}/documents is invalid")
        for index, document in enumerate(documents):
            _validate_creation_document_summary(document, f"{context}/documents/{index}")
    elif method == "creation_document.read":
        _closed(result, {"document", "source_revision"}, context)
        _sha256(result["source_revision"], f"{context}/source_revision")
        document = _object(result["document"], f"{context}/document")
        summary = {key: value for key, value in document.items() if key != "document"}
        _closed(document, set(summary) | {"document"}, f"{context}/document")
        _validate_creation_document_summary(summary, f"{context}/document")
        canonical = _object(document["document"], f"{context}/document/document")
        _strict_json_value(canonical, f"{context}/document/document")
        if (
            canonical.get("format") != summary["format"]
            or canonical.get("format_version") != summary["format_version"]
            or canonical.get("content_hash") != summary["content_hash"]
        ):
            raise StudioContractError(f"{context}/document canonical identity does not match")
    elif method in {
        "creation_changeset.create",
        "creation_changeset.get",
        "creation_changeset.approve",
        "creation_changeset.reject",
    }:
        _closed(result, {"changeset"}, context)
        validate_studio_creation_changeset(result["changeset"])
    elif method == "creation_changeset.list":
        _closed(result, {"changesets"}, context)
        changesets = result["changesets"]
        if not isinstance(changesets, list) or len(changesets) > 1000:
            raise StudioContractError(f"{context}/changesets is invalid")
        for changeset in changesets:
            validate_studio_creation_changeset(changeset)
    elif method == "creation_changeset.diff":
        _closed(result, {"diff"}, context)
        _validate_creation_changeset_diff(result["diff"], f"{context}/diff")
    elif method == "creation_changeset.apply":
        checked = _validate_creation_workspace_workflow_result(
            result,
            context,
            extra_fields={"changeset"},
        )
        validate_studio_creation_changeset(checked["changeset"])
    elif method == "creation_changeset.recover":
        checked = _validate_creation_workspace_workflow_result(
            result,
            context,
            extra_fields={"changeset", "outcome"},
        )
        validate_studio_creation_changeset(checked["changeset"])
        if checked["outcome"] not in {"not_needed", "rolled_back", "committed"}:
            raise StudioContractError(f"{context}/outcome is unknown")
    elif method == "creation_workflow.get":
        _validate_creation_workflow_result(result, context)
    elif method == "creation_phase.read":
        checked = _validate_creation_workspace_workflow_result(
            result,
            context,
            extra_fields={"reference", "report"},
        )
        reference = _object(checked["reference"], f"{context}/reference")
        _closed(
            reference,
            {"phase", "status", "content_hash", "invalidation_dependencies"},
            f"{context}/reference",
        )
        _identifier(reference["phase"], f"{context}/reference/phase", OPERATION_PATTERN)
        if reference["status"] not in {"ready", "not_applicable"}:
            raise StudioContractError(f"{context}/reference/status is unsupported")
        _sha256(reference["content_hash"], f"{context}/reference/content_hash")
        dependencies = reference["invalidation_dependencies"]
        if not isinstance(dependencies, list) or not dependencies:
            raise StudioContractError(
                f"{context}/reference/invalidation_dependencies must be non-empty"
            )
        for index, dependency in enumerate(dependencies):
            _strict_json_value(
                _object(dependency, f"{context}/reference/invalidation_dependencies/{index}"),
                f"{context}/reference/invalidation_dependencies/{index}",
            )
        report = _object(checked["report"], f"{context}/report")
        _strict_json_value(report, f"{context}/report")
    elif method in {
        "creation_workflow.reconcile",
        "creation_phase.complete",
        "creation_phase.reopen",
    }:
        _validate_creation_workspace_workflow_result(result, context)
    elif method == "creation_phase.validate":
        checked = _validate_creation_workspace_workflow_result(
            result,
            context,
            extra_fields={"report"},
        )
        report = _object(checked["report"], f"{context}/report")
        _strict_json_value(report, f"{context}/report")
    elif method == "creation_readiness.inspect":
        _validate_creation_readiness_result(result, context)
    else:  # pragma: no cover - METHODS_V3 is partitioned above
        raise StudioContractError("envelope/method is unknown")


def _validate_creation_artifact_counts(value: object, context: str) -> dict[str, Any]:
    counts = _object(value, context)
    _closed(counts, {"active", "invalidated", "historical", "candidate", "ignored"}, context)
    for field in counts:
        _integer(counts[field], f"{context}/{field}", maximum=100_000)
    return counts


def _validate_creation_artifact_projection(value: object, context: str) -> None:
    projection = _object(value, context)
    _closed(projection, {"projection_kind", "title", "status", "facts", "lineage"}, context)
    _identifier(projection["projection_kind"], f"{context}/projection_kind", OPERATION_PATTERN)
    title = _plain_string(projection["title"], f"{context}/title", max_length=256)
    if not title:
        raise StudioContractError(f"{context}/title must be non-empty")
    status = projection["status"]
    if status is not None:
        _plain_string(status, f"{context}/status", max_length=128)
    facts = projection["facts"]
    if not isinstance(facts, list) or len(facts) > 128:
        raise StudioContractError(f"{context}/facts must have at most 128 entries")
    for index, raw in enumerate(facts):
        fact = _object(raw, f"{context}/facts/{index}")
        _closed(fact, {"key", "value"}, f"{context}/facts/{index}")
        _identifier(fact["key"], f"{context}/facts/{index}/key", OPERATION_PATTERN)
        fact_value = fact["value"]
        if isinstance(fact_value, list):
            if len(fact_value) > 128:
                raise StudioContractError(f"{context}/facts/{index}/value is too large")
            for value_index, item in enumerate(fact_value):
                _plain_string(
                    item,
                    f"{context}/facts/{index}/value/{value_index}",
                    max_length=256,
                )
        elif fact_value is not None and not isinstance(fact_value, str | bool | int):
            raise StudioContractError(f"{context}/facts/{index}/value is unsupported")
        elif isinstance(fact_value, str):
            _plain_string(fact_value, f"{context}/facts/{index}/value", max_length=1024)
    lineage = projection["lineage"]
    if not isinstance(lineage, list) or len(lineage) > 128:
        raise StudioContractError(f"{context}/lineage must have at most 128 entries")
    artifact_ids: list[str] = []
    for index, raw in enumerate(lineage):
        item = _object(raw, f"{context}/lineage/{index}")
        _closed(item, {"relation", "artifact_id", "lifecycle"}, f"{context}/lineage/{index}")
        if item["relation"] != "depends_on":
            raise StudioContractError(f"{context}/lineage/{index}/relation is unsupported")
        artifact_ids.append(
            _identifier(
                item["artifact_id"],
                f"{context}/lineage/{index}/artifact_id",
                ENTITY_ID_PATTERN,
            )
        )
        if item["lifecycle"] not in CREATION_ARTIFACT_LIFECYCLES:
            raise StudioContractError(f"{context}/lineage/{index}/lifecycle is unknown")
    if artifact_ids != sorted(set(artifact_ids), key=lambda item: item.encode("utf-8")):
        raise StudioContractError(f"{context}/lineage must be unique and canonical")


def _validate_creation_preview_response(
    method: str,
    value: object,
    context: str,
    *,
    allow_pre_release: bool = False,
) -> None:
    result = _object(value, context)
    if method == "creation_preview.open":
        _closed(result, {"preview"}, context)
        preview = _object(result["preview"], f"{context}/preview")
        if allow_pre_release and preview.get("format_version") == 2:
            validate_studio_creation_preview_v2(preview)
        else:
            validate_studio_creation_preview(preview)
        return
    if method == "creation_preview.close":
        _closed(result, {"handle", "closed"}, context)
        _identifier(result["handle"], f"{context}/handle", ASSET_PREVIEW_HANDLE_PATTERN)
        if result["closed"] is not True:
            raise StudioContractError(f"{context}/closed must be true")
        return

    _closed(
        result,
        {
            "handle",
            "sequence",
            "data_base64",
            "byte_length",
            "cumulative_bytes",
            "cumulative_sha256",
            "eof",
        },
        context,
    )
    _identifier(result["handle"], f"{context}/handle", ASSET_PREVIEW_HANDLE_PATTERN)
    sequence = _integer(
        result["sequence"],
        f"{context}/sequence",
        maximum=MAX_CREATION_PREVIEW_SEQUENCE,
    )
    encoded = _plain_string(
        result["data_base64"],
        f"{context}/data_base64",
        max_length=MAX_CREATION_PREVIEW_BASE64_LENGTH,
    )
    if not encoded:
        raise StudioContractError(f"{context}/data_base64 must be non-empty")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise StudioContractError(f"{context}/data_base64 must be canonical base64") from exc
    if base64.b64encode(decoded).decode("ascii") != encoded:
        raise StudioContractError(f"{context}/data_base64 must be canonical base64")
    byte_length = _integer(result["byte_length"], f"{context}/byte_length", minimum=1)
    if byte_length > CREATION_PREVIEW_CHUNK_BYTES or len(decoded) != byte_length:
        raise StudioContractError(f"{context}/byte_length does not match preview data")
    cumulative_bytes = _integer(
        result["cumulative_bytes"],
        f"{context}/cumulative_bytes",
        minimum=1,
    )
    expected_cumulative = sequence * CREATION_PREVIEW_CHUNK_BYTES + byte_length
    if cumulative_bytes != expected_cumulative or cumulative_bytes > MAX_CREATION_PREVIEW_BYTES:
        raise StudioContractError(f"{context}/cumulative_bytes is inconsistent")
    _sha256(result["cumulative_sha256"], f"{context}/cumulative_sha256")
    eof = _boolean(result["eof"], f"{context}/eof")
    if not eof and byte_length != CREATION_PREVIEW_CHUNK_BYTES:
        raise StudioContractError(f"{context}/byte_length must fill non-final chunks")


def _validate_creation_evidence_response(
    method: str,
    value: object,
    context: str,
    *,
    max_job_version: int = 9,
    max_output_grant_version: int = 5,
    allow_pre_release_preview: bool = False,
) -> None:
    if method.startswith("creation_preview."):
        _validate_creation_preview_response(
            method,
            value,
            context,
            allow_pre_release=allow_pre_release_preview,
        )
        return
    result = _object(value, context)
    output_grant_validator = (
        validate_studio_creation_output_grant_v6
        if max_output_grant_version == 6
        else validate_studio_creation_output_grant
    )
    if method == "service.initialize":
        _closed(
            result,
            {
                "service",
                "service_version",
                "protocol",
                "protocol_version",
                "methods",
                "capabilities",
            },
            context,
        )
        if (
            result["service"] != "world-forge.studio"
            or result["service_version"] != STUDIO_PROTOCOL_V4
            or result["protocol"] != PROTOCOL_FORMAT
            or result["protocol_version"] != STUDIO_PROTOCOL_V4
            or result["methods"] != sorted(METHODS_V4)
        ):
            raise StudioContractError(f"{context} does not describe Studio protocol v4")
        capabilities = _object(result["capabilities"], f"{context}/capabilities")
        expected = {
            "creation_evidence_projection": True,
            "creation_jobs": True,
            "creation_output_grants": True,
            "creation_runtime_compose": True,
            "creation_runtime_bundle": True,
            "creation_materialization_bundle": True,
            "creation_asset_previews": True,
            "asset_previews": False,
            "materialization_execution": True,
            "game_packaging": True,
            "game_package_extraction": True,
        }
        _closed(capabilities, set(expected), f"{context}/capabilities")
        if capabilities != expected:
            raise StudioContractError(f"{context}/capabilities does not describe Studio v4")
        return
    if method in {
        "creation_output_grant.create",
        "creation_output_grant.get",
        "creation_output_grant.revoke",
    }:
        _closed(result, {"grant"}, context)
        output_grant_validator(result["grant"])
        return
    if method == "creation_output_grant.list":
        _closed(result, {"authority", "artifact_snapshot_hash", "grants", "next_cursor"}, context)
        _validate_creation_artifact_authority(result["authority"], f"{context}/authority")
        _sha256(result["artifact_snapshot_hash"], f"{context}/artifact_snapshot_hash")
        grants = result["grants"]
        if not isinstance(grants, list) or len(grants) > MAX_CREATION_OUTPUT_GRANT_PAGE:
            raise StudioContractError(f"{context}/grants exceeds one page")
        previous: bytes | None = None
        for grant in grants:
            checked = output_grant_validator(grant)
            current = checked["grant_id"].encode("utf-8")
            if previous is not None and current <= previous:
                raise StudioContractError(f"{context}/grants are not ordered")
            previous = current
        if result["next_cursor"] is not None:
            _identifier(result["next_cursor"], f"{context}/next_cursor", ENTITY_ID_PATTERN)
            if not grants or result["next_cursor"] != grants[-1]["grant_id"]:
                raise StudioContractError(f"{context}/next_cursor does not match the page")
        return
    if method in {
        "creation_job.create",
        "creation_job.get",
        "creation_job.cancel",
        "creation_job.recover",
    }:
        _closed(result, {"job"}, context)
        job = validate_studio_creation_job(result["job"])
        if job["format_version"] > max_job_version:
            raise StudioContractError(
                f"{context}/job format_version is unavailable in this protocol"
            )
        return
    if method == "creation_job.list":
        _closed(result, {"jobs", "next_sequence"}, context)
        jobs = result["jobs"]
        if not isinstance(jobs, list) or len(jobs) > MAX_CREATION_JOB_PAGE:
            raise StudioContractError(f"{context}/jobs exceeds one page")
        for index, job in enumerate(jobs):
            try:
                checked = validate_studio_creation_job(job)
                if checked["format_version"] > max_job_version:
                    raise StudioContractError(
                        "creation job format_version is unavailable in this protocol"
                    )
            except StudioContractError as exc:
                raise StudioContractError(f"{context}/jobs/{index}: {exc}") from exc
        next_sequence = result["next_sequence"]
        if next_sequence is not None:
            _integer(next_sequence, f"{context}/next_sequence", minimum=1)
        return
    if method == "creation_event.list":
        _closed(result, {"events"}, context)
        events = result["events"]
        if not isinstance(events, list) or len(events) > MAX_CREATION_EVENT_PAGE:
            raise StudioContractError(f"{context}/events exceeds one page")
        previous = 0
        for index, raw in enumerate(events):
            event = _object(raw, f"{context}/events/{index}")
            _closed(
                event,
                {
                    "event_id",
                    "workspace_id",
                    "topic",
                    "entity_type",
                    "entity_id",
                    "payload",
                    "created_at",
                },
                f"{context}/events/{index}",
            )
            event_id = _integer(event["event_id"], f"{context}/events/{index}/event_id", minimum=1)
            if event_id <= previous:
                raise StudioContractError(f"{context}/events are not ordered")
            previous = event_id
            _identifier(
                event["workspace_id"],
                f"{context}/events/{index}/workspace_id",
                WORKSPACE_ID_PATTERN,
            )
            _identifier(event["topic"], f"{context}/events/{index}/topic", OPERATION_PATTERN)
            _identifier(
                event["entity_type"],
                f"{context}/events/{index}/entity_type",
                OPERATION_PATTERN,
            )
            _identifier(
                event["entity_id"],
                f"{context}/events/{index}/entity_id",
                ENTITY_ID_PATTERN,
            )
            _strict_json_value(
                _object(event["payload"], f"{context}/events/{index}/payload"),
                f"{context}/events/{index}/payload",
            )
            _timestamp(event["created_at"], f"{context}/events/{index}/created_at")
        return
    common = {"authority", "artifact_snapshot_hash"}
    if method == "creation_artifact.list":
        _closed(result, common | {"artifacts", "next_cursor", "counts"}, context)
        _validate_creation_artifact_authority(result["authority"], f"{context}/authority")
        _sha256(result["artifact_snapshot_hash"], f"{context}/artifact_snapshot_hash")
        artifacts = result["artifacts"]
        if not isinstance(artifacts, list) or len(artifacts) > MAX_CREATION_ARTIFACT_PAGE:
            raise StudioContractError(f"{context}/artifacts exceeds one page")
        artifact_ids: list[str] = []
        for index, artifact in enumerate(artifacts):
            checked = validate_studio_creation_artifact(artifact)
            if checked["authority"] != result["authority"]:
                raise StudioContractError(f"{context}/artifacts/{index} authority differs")
            artifact_ids.append(checked["artifact_id"])
        if artifact_ids != sorted(set(artifact_ids), key=lambda item: item.encode("utf-8")):
            raise StudioContractError(f"{context}/artifacts are not canonical")
        next_cursor = result["next_cursor"]
        if next_cursor is not None:
            _identifier(next_cursor, f"{context}/next_cursor", ENTITY_ID_PATTERN)
        _validate_creation_artifact_counts(result["counts"], f"{context}/counts")
    elif method == "creation_artifact.inspect":
        _closed(result, common | {"artifact", "projection"}, context)
        _validate_creation_artifact_authority(result["authority"], f"{context}/authority")
        _sha256(result["artifact_snapshot_hash"], f"{context}/artifact_snapshot_hash")
        artifact = validate_studio_creation_artifact(result["artifact"])
        if artifact["authority"] != result["authority"]:
            raise StudioContractError(f"{context}/artifact authority differs")
        _validate_creation_artifact_projection(result["projection"], f"{context}/projection")
    elif method == "creation_evidence.inspect":
        _closed(result, common | {"evidence"}, context)
        _validate_creation_artifact_authority(result["authority"], f"{context}/authority")
        _sha256(result["artifact_snapshot_hash"], f"{context}/artifact_snapshot_hash")
        evidence = validate_studio_creation_evidence(result["evidence"])
        if (
            evidence["authority"] != result["authority"]
            or evidence["artifact_snapshot_hash"] != result["artifact_snapshot_hash"]
        ):
            raise StudioContractError(f"{context}/evidence authority differs")
    else:  # pragma: no cover - METHODS_V4 is partitioned above
        raise StudioContractError("envelope/method is unknown")


def _validate_creation_authority_response_v5(
    method: str,
    value: object,
    context: str,
) -> None:
    if method == "creation_workspace.create":
        _validate_creation_response(method, value, context)
        return
    if method != "service.initialize":
        _validate_creation_evidence_response(
            method,
            value,
            context,
            max_job_version=12,
            max_output_grant_version=6,
            allow_pre_release_preview=True,
        )
        return
    result = _object(value, context)
    _closed(
        result,
        {
            "service",
            "service_version",
            "protocol",
            "protocol_version",
            "methods",
            "capabilities",
        },
        context,
    )
    if (
        result["service"] != "world-forge.studio"
        or result["service_version"] != STUDIO_PROTOCOL_V5
        or result["protocol"] != PROTOCOL_FORMAT
        or result["protocol_version"] != STUDIO_PROTOCOL_V5
        or result["methods"] != sorted(METHODS_V5)
    ):
        raise StudioContractError(f"{context} does not describe Studio protocol v5")
    capabilities = _object(result["capabilities"], f"{context}/capabilities")
    expected = {
        "creation_evidence_projection": True,
        "creation_jobs": True,
        "creation_output_grants": True,
        "creation_runtime_compose": True,
        "creation_runtime_bundle": True,
        "creation_materialization_bundle": True,
        "creation_asset_previews": True,
        "asset_previews": False,
        "materialization_execution": True,
        "game_packaging": True,
        "game_package_extraction": True,
        "asset_authority_reviews": True,
        "asset_release_authority": True,
        "runtime_headless_authority": True,
        "creation_preview_pre_release": True,
    }
    _closed(capabilities, set(expected), f"{context}/capabilities")
    if capabilities != expected:
        raise StudioContractError(f"{context}/capabilities does not describe Studio v5")


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
    protocol_version = envelope["protocol_version"]
    if (
        isinstance(protocol_version, bool)
        or not isinstance(protocol_version, int)
        or protocol_version
        not in {
            STUDIO_VERSION,
            STUDIO_PROTOCOL_V2,
            STUDIO_PROTOCOL_V3,
            STUDIO_PROTOCOL_V4,
            STUDIO_PROTOCOL_V5,
        }
    ):
        raise StudioContractError("envelope/protocol_version must be 1, 2, 3, 4 or 5")
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
        allowed_methods = (
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
        if not isinstance(method, str) or method not in allowed_methods:
            if (
                isinstance(method, str)
                and method in METHODS | EXTERNAL_METHODS | METHODS_V3 | METHODS_V4 | METHODS_V5
            ):
                raise StudioContractError(
                    f"envelope/method {method} is not available in protocol v{protocol_version}"
                )
            raise StudioContractError("envelope/method is unknown")
        if protocol_version == STUDIO_PROTOCOL_V5:
            if method == "service.initialize":
                params = _object(envelope["params"], "envelope/params")
                _closed(params, set(), "envelope/params")
            elif method in {
                "creation_job.create",
                "creation_job.get",
                "creation_job.list",
                "creation_job.cancel",
                "creation_job.recover",
                "creation_event.list",
            }:
                _validate_creation_job_v5_params(method, envelope["params"], "envelope/params")
            elif method == "creation_workspace.create":
                _validate_creation_workspace_create_params(
                    envelope["params"],
                    "envelope/params",
                    allow_asset_content_mode=True,
                )
            elif method == "creation_output_grant.create":
                _validate_creation_output_grant_create_params(
                    envelope["params"],
                    "envelope/params",
                    allow_v6=True,
                )
            elif method == "creation_output_grant.get":
                _validate_creation_output_grant_id_params(
                    envelope["params"],
                    "envelope/params",
                    mutation=False,
                )
            elif method == "creation_output_grant.list":
                _validate_creation_output_grant_list_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method == "creation_output_grant.revoke":
                _validate_creation_output_grant_id_params(
                    envelope["params"],
                    "envelope/params",
                    mutation=True,
                )
            elif method.startswith("creation_preview."):
                _validate_creation_preview_params(
                    method,
                    envelope["params"],
                    "envelope/params",
                    allow_pre_release=True,
                )
            else:
                _validate_creation_evidence_authority_params(
                    envelope["params"],
                    "envelope/params",
                    method=method,
                )
        elif protocol_version == STUDIO_PROTOCOL_V4:
            if method == "service.initialize":
                params = _object(envelope["params"], "envelope/params")
                _closed(params, set(), "envelope/params")
            elif method == "creation_output_grant.create":
                _validate_creation_output_grant_create_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method == "creation_output_grant.get":
                _validate_creation_output_grant_id_params(
                    envelope["params"],
                    "envelope/params",
                    mutation=False,
                )
            elif method == "creation_output_grant.list":
                _validate_creation_output_grant_list_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method == "creation_output_grant.revoke":
                _validate_creation_output_grant_id_params(
                    envelope["params"],
                    "envelope/params",
                    mutation=True,
                )
            elif method in {
                "creation_job.create",
                "creation_job.get",
                "creation_job.list",
                "creation_job.cancel",
                "creation_job.recover",
                "creation_event.list",
            }:
                _validate_creation_job_v4_params(method, envelope["params"], "envelope/params")
            elif method.startswith("creation_preview."):
                _validate_creation_preview_params(method, envelope["params"], "envelope/params")
            else:
                _validate_creation_evidence_authority_params(
                    envelope["params"],
                    "envelope/params",
                    method=method,
                )
        elif protocol_version == STUDIO_PROTOCOL_V3:
            if method == "service.initialize" or method == "creation_workspace.list":
                params = _object(envelope["params"], "envelope/params")
                _closed(params, set(), "envelope/params")
            elif method == "creation_root_grant.create":
                _validate_creation_root_grant_create_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method == "creation_root_grant.get":
                _validate_creation_root_grant_id_params(
                    envelope["params"],
                    "envelope/params",
                    mutation=False,
                )
            elif method == "creation_root_grant.revoke":
                _validate_creation_root_grant_id_params(
                    envelope["params"],
                    "envelope/params",
                    mutation=True,
                )
            elif method == "creation_workspace.create":
                _validate_creation_workspace_create_params(
                    envelope["params"],
                    "envelope/params",
                    allow_asset_content_mode=False,
                )
            elif method == "creation_workspace.recover":
                _validate_creation_workspace_recover_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method == "creation_workspace.register":
                _validate_creation_workspace_register_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method in {"creation_workspace.get", "creation_workspace.open"}:
                _validate_creation_workspace_id_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method == "creation_document.list":
                _validate_creation_revision_params(
                    envelope["params"],
                    "envelope/params",
                    include_path=False,
                )
            elif method in {"creation_workflow.get", "creation_readiness.inspect"}:
                _validate_creation_workspace_id_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method == "creation_document.read":
                _validate_creation_revision_params(
                    envelope["params"],
                    "envelope/params",
                    include_path=True,
                )
            elif method == "creation_changeset.create":
                _validate_creation_changeset_create_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method in {"creation_changeset.get", "creation_changeset.diff"}:
                _validate_creation_changeset_id_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method == "creation_changeset.list":
                _validate_creation_changeset_list_params(
                    envelope["params"],
                    "envelope/params",
                )
            elif method in {"creation_changeset.approve", "creation_changeset.reject"}:
                _validate_creation_changeset_action_params(
                    envelope["params"],
                    "envelope/params",
                    include_generation=False,
                    include_mode=False,
                )
            elif method == "creation_changeset.apply":
                _validate_creation_changeset_action_params(
                    envelope["params"],
                    "envelope/params",
                    include_generation=True,
                    include_mode=False,
                )
            elif method == "creation_changeset.recover":
                _validate_creation_changeset_action_params(
                    envelope["params"],
                    "envelope/params",
                    include_generation=True,
                    include_mode=True,
                )
            elif method in {
                "creation_workflow.reconcile",
                "creation_phase.read",
                "creation_phase.validate",
                "creation_phase.complete",
                "creation_phase.reopen",
            }:
                _validate_creation_phase_params(
                    method,
                    envelope["params"],
                    "envelope/params",
                )
            else:  # pragma: no cover - METHODS_V3 is partitioned above
                raise StudioContractError("envelope/method is unknown")
        elif method in WORKSPACE_AUTHORING_METHODS:
            _validate_workspace_params(envelope["params"], "envelope/params")
        elif method == "source.read":
            _validate_source_read_params(envelope["params"], "envelope/params")
        elif method == "asset.catalog.list":
            _validate_asset_catalog_list_params(envelope["params"], "envelope/params")
        elif method == "asset.catalog.inspect":
            _validate_asset_catalog_inspect_params(envelope["params"], "envelope/params")
        elif method in EXACT_ASSET_PREVIEW_METHODS:
            _validate_asset_preview_params(method, envelope["params"], "envelope/params")
        elif method == "changeset.create":
            _validate_changeset_create_params(envelope["params"], "envelope/params")
        elif method in {"changeset.get", "changeset.diff"}:
            _validate_changeset_id_params(envelope["params"], "envelope/params")
        elif method == "changeset.list":
            _validate_changeset_list_params(envelope["params"], "envelope/params")
        elif method in CHANGESET_ACTION_METHODS:
            _validate_changeset_action_params(envelope["params"], "envelope/params")
        elif method == "job.create":
            validate_job_create_params(envelope["params"])
            operation = envelope["params"]["operation"]
            if protocol_version == STUDIO_VERSION and operation in EXTERNAL_JOB_OPERATIONS:
                raise StudioContractError("external job creation is not available in protocol v1")
            if protocol_version == STUDIO_PROTOCOL_V2 and operation in MANAGED_JOB_OPERATIONS:
                raise StudioContractError(
                    "managed job creation is not available in external protocol v2"
                )
        elif method in {"job.get", "job.cancel"}:
            _validate_job_id_params(envelope["params"], "envelope/params")
        elif method == "job.list" and protocol_version == STUDIO_PROTOCOL_V2:
            _validate_external_job_list_params(envelope["params"], "envelope/params")
        elif method == "job.recover":
            _validate_job_recover_params(envelope["params"], "envelope/params")
        elif method == "external_grant.create":
            _validate_external_grant_create_params(
                envelope["params"],
                "envelope/params",
            )
        elif method in {"external_grant.get", "external_grant.revoke"}:
            _validate_external_grant_id_params(envelope["params"], "envelope/params")
        else:
            params = _object(envelope["params"], "envelope/params")
            _strict_json_value(params, "envelope/params")
    elif kind == "response":
        method = envelope["method"]
        allowed_methods = (
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
        if not isinstance(method, str) or method not in allowed_methods:
            if (
                isinstance(method, str)
                and method in METHODS | EXTERNAL_METHODS | METHODS_V3 | METHODS_V4 | METHODS_V5
            ):
                raise StudioContractError(
                    f"envelope/method {method} is not available in protocol v{protocol_version}"
                )
            raise StudioContractError("envelope/method is unknown")
        if protocol_version == STUDIO_PROTOCOL_V5:
            _validate_creation_authority_response_v5(
                method,
                envelope["result"],
                "envelope/result",
            )
        elif protocol_version == STUDIO_PROTOCOL_V4:
            _validate_creation_evidence_response(method, envelope["result"], "envelope/result")
        elif protocol_version == STUDIO_PROTOCOL_V3:
            _validate_creation_response(method, envelope["result"], "envelope/result")
        elif method in AUTHORING_METHODS:
            _validate_authoring_result(method, envelope["result"], "envelope/result")
        elif method in EXACT_ASSET_CATALOG_METHODS:
            _validate_asset_catalog_result(method, envelope["result"], "envelope/result")
        elif method in EXACT_ASSET_PREVIEW_METHODS:
            _validate_asset_preview_result(method, envelope["result"], "envelope/result")
        elif method in EXACT_CHANGESET_METHODS:
            _validate_changeset_result(method, envelope["result"], "envelope/result")
        elif protocol_version == STUDIO_PROTOCOL_V2 and method == "job.list":
            _validate_external_job_list_result(envelope["result"], "envelope/result")
        elif method in EXACT_JOB_METHODS or (
            protocol_version == STUDIO_PROTOCOL_V2 and method in {"job.get", "job.recover"}
        ):
            result = _object(envelope["result"], "envelope/result")
            _closed(result, {"job"}, "envelope/result")
            job = validate_studio_job(result["job"])
            if (
                method == "job.create"
                and protocol_version == STUDIO_VERSION
                and job["format_version"] != MANAGED_JOB_VERSION
            ):
                raise StudioContractError("job.create v1 responses require a managed v2 job")
            if (
                protocol_version == STUDIO_PROTOCOL_V2
                and job["format_version"] != EXTERNAL_JOB_VERSION
            ):
                raise StudioContractError(f"{method} v2 responses require an external v3 job")
        elif method in EXTERNAL_METHODS:
            result = _object(envelope["result"], "envelope/result")
            _closed(result, {"grant"}, "envelope/result")
            validate_studio_external_grant(result["grant"])
        elif method in LEGACY_METHODS:
            result = _object(envelope["result"], "envelope/result")
            _strict_json_value(result, "envelope/result")
        else:  # pragma: no cover - METHODS is partitioned above
            raise StudioContractError("envelope/method is unknown")
    elif kind == "error":
        error = _object(envelope["error"], "envelope/error")
        _closed(error, {"code", "message", "details"}, "envelope/error")
        allowed_error_codes = (
            LEGACY_ERROR_CODES if protocol_version == STUDIO_VERSION else ERROR_CODES
        )
        if not isinstance(error["code"], str) or error["code"] not in allowed_error_codes:
            raise StudioContractError("envelope/error/code is unknown")
        _string(error["message"], "envelope/error/message")
        _object(error["details"], "envelope/error/details")
        _strict_json_value(error["details"], "envelope/error/details")
    else:
        event = _object(envelope["event"], "envelope/event")
        _strict_json_value(event, "envelope/event")
    return envelope
