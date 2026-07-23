from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from isoworld.content.media import media_signature_matches
from worldforge.asset_contracts import (
    EXECUTORS,
    OUTPUT_ROLE_MEDIA,
    PRODUCTION_OPERATIONS,
    ROUTES,
    ContractIssue,
    _base_contract_issues,
    _issue,
    _scan_sensitive,
    _valid_hash,
    validate_asset_spec,
)
from worldforge.asset_formats.gltf import GLBError, inspect_glb
from worldforge.asset_image_inspection import inspect_image_file
from worldforge.asset_io import (
    AssetContractError,
    artifact_reference,
    bind_content_hash,
    read_json_object,
    require_content_hash,
    verify_artifact_reference,
    write_json_atomic,
)
from worldforge.validation import ID_PATTERN

_EXECUTOR_ROUTES = {
    "openai_image": {"openai"},
    "blender_mcp": {"openai", "modly"},
    "modly_cli_mcp": {"modly"},
    "human": {"openai", "modly"},
    "procedural": {"openai", "modly"},
}
_MEDIA_TYPES = {
    "application/json",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "font/otf",
    "font/ttf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "model/gltf-binary",
    "application/x-blender",
    "text/x-glsl",
}
_EXECUTOR_OPERATIONS = {
    "openai_image": {"image_generate", "image_edit", "concept_reference"},
    "blender_mcp": {
        "model_from_reference",
        "retopology",
        "uv_unwrap",
        "material_bake",
        "rig",
        "animate",
        "collision",
        "export_glb",
        "refine",
    },
    "modly_cli_mcp": {"capability_execute", "workflow_run", "process_run"},
    "human": PRODUCTION_OPERATIONS,
    "procedural": PRODUCTION_OPERATIONS,
}
_REQUEST_KEYS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "asset_id",
        "specification",
        "target_id",
        "target_hash",
        "orchestrator",
        "route",
        "executor",
        "operation",
        "inputs",
        "parameters",
        "expected_outputs",
        "parent_receipt_hashes",
        "content_hash",
    }
)
_RECEIPT_KEYS = frozenset(
    {
        "format",
        "format_version",
        "id",
        "request",
        "asset_id",
        "route",
        "executor",
        "operation",
        "status",
        "started_at",
        "completed_at",
        "parent_receipt_hashes",
        "toolchain",
        "replayability",
        "outputs",
        "content_hash",
    }
)
_OPENAI_TOOLCHAIN_KEYS = frozenset(
    {"surface", "requested_model", "resolved_model", "version_resolution"}
)
_BLENDER_TOOLCHAIN_KEYS = frozenset(
    {"blender_version", "blender_mcp_version", "addon_revision", "telemetry_disabled"}
)
_MODLY_TOOLCHAIN_KEYS = frozenset(
    {
        "modly_cli_mcp_version",
        "modly_version",
        "canonical_surface",
        "capability_id",
        "run_id",
        "support_state",
        "capability_discovery_hash",
        "extension",
        "model",
        "setup_reviewed",
    }
)
_MODLY_PARAMETER_KEYS = frozenset(
    {
        "modly_cli_mcp_version",
        "modly_version",
        "canonical_surface",
        "capability_id",
        "support_state",
        "capability_discovery",
        "extension",
        "model",
        "setup_reviewed",
        "arguments",
    }
)
_MODLY_DISCOVERY_KEYS = frozenset(
    {
        "format",
        "format_version",
        "modly_cli_mcp_version",
        "modly_version",
        "canonical_surface",
        "capability_id",
        "support_state",
        "extension",
        "model",
        "content_hash",
    }
)
_CANDIDATE_ROLE_MEDIA = {
    **{role: frozenset(media_types) for role, media_types in OUTPUT_ROLE_MEDIA.items()},
    "authoring_source": frozenset({"application/x-blender"}),
}
_IMAGE_CANDIDATE_MEDIA = frozenset({"image/jpeg", "image/png", "image/webp"})
_OPERATION_OUTPUT_ROLES = {
    "image_generate": frozenset({"preview", "texture"}),
    "image_edit": frozenset({"preview", "texture"}),
    "concept_reference": frozenset({"preview"}),
    "model_from_reference": frozenset({"authoring_source", "model", "preview"}),
    "retopology": frozenset({"authoring_source", "model"}),
    "uv_unwrap": frozenset({"authoring_source", "model"}),
    "material_bake": frozenset({"authoring_source", "material_metadata", "model", "texture"}),
    "rig": frozenset({"authoring_source", "model", "skeleton"}),
    "animate": frozenset({"animation", "authoring_source", "model"}),
    "collision": frozenset({"authoring_source", "collision", "model"}),
    "export_glb": frozenset({"animation", "authoring_source", "collision", "model", "skeleton"}),
    "refine": frozenset(
        {
            "animation",
            "authoring_source",
            "collision",
            "material_metadata",
            "model",
            "skeleton",
            "texture",
        }
    ),
}
_BLENDER_REQUIRED_INPUT_ROLES = {
    "model_from_reference": frozenset({"reference"}),
    "retopology": frozenset({"model"}),
    "uv_unwrap": frozenset({"model"}),
    "material_bake": frozenset({"model"}),
    "rig": frozenset({"model"}),
    "animate": frozenset({"model", "skeleton"}),
    "collision": frozenset({"model"}),
    "refine": frozenset({"model"}),
    "export_glb": frozenset({"model"}),
}
_BLENDER_ALLOWED_PARENT_OPERATIONS = {
    "model_from_reference": frozenset({"concept_reference"}),
    "retopology": frozenset({"model_from_reference", "refine", "retopology"}),
    "uv_unwrap": frozenset({"model_from_reference", "refine", "retopology", "uv_unwrap"}),
    "material_bake": frozenset(
        {"material_bake", "model_from_reference", "refine", "retopology", "uv_unwrap"}
    ),
    "rig": frozenset(
        {"material_bake", "model_from_reference", "refine", "retopology", "uv_unwrap"}
    ),
    "animate": frozenset({"animate", "refine", "rig"}),
    "collision": frozenset(
        {
            "animate",
            "collision",
            "material_bake",
            "model_from_reference",
            "refine",
            "retopology",
            "rig",
            "uv_unwrap",
        }
    ),
    "refine": frozenset(
        {
            "animate",
            "capability_execute",
            "collision",
            "export_glb",
            "material_bake",
            "model_from_reference",
            "process_run",
            "refine",
            "retopology",
            "rig",
            "uv_unwrap",
            "workflow_run",
        }
    ),
    "export_glb": frozenset(
        {
            "animate",
            "collision",
            "material_bake",
            "model_from_reference",
            "refine",
            "retopology",
            "rig",
            "uv_unwrap",
        }
    ),
}
_BLENDER_PARENT_OUTPUT_ROLES = {
    "reference": frozenset({"preview", "texture"}),
    "model": frozenset({"authoring_source", "model"}),
    "skeleton": frozenset({"authoring_source", "model", "skeleton"}),
}


@dataclass(frozen=True)
class ResolvedProductionReceipt:
    manifest_index: int
    path: Path
    content_hash: str


@dataclass(frozen=True)
class _ProductionReceiptAuthority:
    path: Path
    content_hash: str
    reference_items: tuple[tuple[str, object], ...]


class ProductionReceiptIndex:
    """Closed authority over production receipts referenced by one asset."""

    __slots__ = ("_by_hash", "_by_path", "_root")

    def __init__(
        self,
        root: Path,
        by_hash: dict[str, _ProductionReceiptAuthority],
        by_path: dict[Path, _ProductionReceiptAuthority],
    ) -> None:
        self._root = root
        self._by_hash = MappingProxyType(dict(by_hash))
        self._by_path = MappingProxyType(dict(by_path))

    @classmethod
    def from_manifest_references(
        cls,
        asset_root: str | Path,
        references: list[object],
        *,
        context: str = "production_receipts",
    ) -> tuple[
        ProductionReceiptIndex,
        tuple[ResolvedProductionReceipt, ...],
        list[ContractIssue],
    ]:
        root = Path(asset_root).resolve()
        by_hash: dict[str, _ProductionReceiptAuthority] = {}
        by_path: dict[Path, _ProductionReceiptAuthority] = {}
        resolved: list[ResolvedProductionReceipt] = []
        issues: list[ContractIssue] = []
        for manifest_index, reference in enumerate(references):
            issue_path = str(manifest_index)
            try:
                path = verify_artifact_reference(
                    root,
                    reference,
                    context=f"{context}/{manifest_index}",
                )
                receipt = read_json_object(path)
                require_content_hash(receipt, context="production receipt")
            except AssetContractError as exc:
                issues.append(_issue(issue_path, str(exc)))
                continue
            content_hash = receipt["content_hash"]
            assert isinstance(content_hash, str)
            assert isinstance(reference, dict)
            authority = _ProductionReceiptAuthority(
                path=path,
                content_hash=content_hash,
                reference_items=tuple(sorted(reference.items())),
            )
            prior_path = by_path.get(path)
            if prior_path is not None:
                if prior_path.reference_items == authority.reference_items:
                    message = "duplicate production receipt reference"
                else:
                    message = "conflicting production receipt references for the same path"
                issues.append(_issue(issue_path, message))
                continue
            if content_hash in by_hash:
                issues.append(
                    _issue(
                        issue_path,
                        "duplicate receipt content hash across conflicting production "
                        "receipt references",
                    )
                )
                continue
            by_hash[content_hash] = authority
            by_path[path] = authority
            resolved.append(
                ResolvedProductionReceipt(
                    manifest_index=manifest_index,
                    path=path,
                    content_hash=content_hash,
                )
            )
        return cls(root, by_hash, by_path), tuple(resolved), issues

    def authorizes(self, content_hash: str) -> bool:
        return content_hash in self._by_hash

    def read(self, content_hash: str) -> dict[str, Any]:
        authority = self._by_hash.get(content_hash)
        if authority is None:
            raise AssetContractError(
                f"Production receipt content hash is not authorized: {content_hash}"
            )
        return self._read_authority(authority)

    def read_path(self, path: str | Path) -> tuple[str, dict[str, Any]]:
        candidate = Path(path)
        authority = self._by_path.get(candidate)
        if authority is None:
            raise AssetContractError(f"Production receipt path is not authorized: {candidate}")
        return authority.content_hash, self._read_authority(authority)

    def _read_authority(
        self,
        authority: _ProductionReceiptAuthority,
    ) -> dict[str, Any]:
        path = verify_artifact_reference(
            self._root,
            dict(authority.reference_items),
            context="authorized production receipt",
        )
        if path != authority.path:
            raise AssetContractError("Authorized production receipt path was rebound")
        receipt = read_json_object(path)
        require_content_hash(receipt, context="authorized production receipt")
        if receipt.get("content_hash") != authority.content_hash:
            raise AssetContractError(
                "Authorized production receipt content hash does not match its index key"
            )
        return receipt


def _sorted_unique_hashes(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(_valid_hash(item) for item in value)
        and value == sorted(set(value))
    )


def _exact_key_issues(
    raw: dict[str, Any], expected: frozenset[str], context: str
) -> list[ContractIssue]:
    missing = sorted(expected - set(raw))
    unknown = sorted(set(raw) - expected)
    issues = [_issue(context, f"missing fields: {', '.join(missing)}")] if missing else []
    if unknown:
        issues.append(_issue(context, f"unknown fields: {', '.join(unknown)}"))
    return issues


def _bounded_text_issue(value: object, *, path: str, maximum: int) -> ContractIssue | None:
    if not isinstance(value, str) or not value:
        return _issue(path, "must be a non-empty string")
    if len(value) > maximum:
        return _issue(path, f"must contain at most {maximum} characters")
    return None


def _modly_capability_discovery_issues(raw: dict[str, Any]) -> list[ContractIssue]:
    issues = _base_contract_issues(
        raw,
        expected_format="rpg-world-forge.modly_capability_discovery",
    )
    issues.extend(_exact_key_issues(raw, _MODLY_DISCOVERY_KEYS, "discovery"))
    for field, maximum in (
        ("modly_cli_mcp_version", 128),
        ("modly_version", 128),
        ("capability_id", 512),
    ):
        issue = _bounded_text_issue(raw.get(field), path=field, maximum=maximum)
        if issue is not None:
            issues.append(issue)
    if raw.get("canonical_surface") not in {"process_run", "workflow_run"}:
        issues.append(_issue("canonical_surface", "must be process_run or workflow_run"))
    if raw.get("support_state") != "supported":
        issues.append(_issue("support_state", "must report supported before execution"))

    extension = raw.get("extension")
    if not isinstance(extension, dict):
        issues.append(_issue("extension", "must be a versioned extension identity"))
    else:
        issues.extend(
            _exact_key_issues(
                extension,
                frozenset({"id", "version", "revision", "manifest_hash", "workflow_hash"}),
                "extension",
            )
        )
        for field, maximum in (("id", 256), ("version", 128), ("revision", 256)):
            issue = _bounded_text_issue(
                extension.get(field),
                path=f"extension/{field}",
                maximum=maximum,
            )
            if issue is not None:
                issues.append(issue)
        for field in ("manifest_hash", "workflow_hash"):
            if not _valid_hash(extension.get(field)):
                issues.append(_issue(f"extension/{field}", "invalid SHA-256"))

    model = raw.get("model")
    if not isinstance(model, dict):
        issues.append(_issue("model", "must be a versioned model and weights identity"))
    else:
        issues.extend(
            _exact_key_issues(
                model,
                frozenset({"id", "version", "weights_hash"}),
                "model",
            )
        )
        for field, maximum in (("id", 256), ("version", 128)):
            issue = _bounded_text_issue(
                model.get(field),
                path=f"model/{field}",
                maximum=maximum,
            )
            if issue is not None:
                issues.append(issue)
        if not _valid_hash(model.get("weights_hash")):
            issues.append(_issue("model/weights_hash", "invalid SHA-256"))
    issues.extend(_scan_sensitive(raw))
    return issues


def validate_modly_capability_discovery(path: str | Path) -> list[ContractIssue]:
    """Validate one sanitized, hash-bound pre-execution Modly discovery snapshot."""

    try:
        raw = read_json_object(path)
    except AssetContractError as exc:
        return [_issue("discovery", str(exc))]
    return _modly_capability_discovery_issues(raw)


def _modly_parameter_issues(
    value: object,
    *,
    operation: object,
    root: Path | None,
) -> list[ContractIssue]:
    if not isinstance(value, dict):
        return [_issue("parameters", "Modly parameters must be a strict object")]
    issues = _exact_key_issues(value, _MODLY_PARAMETER_KEYS, "parameters")
    for field in ("modly_cli_mcp_version", "modly_version", "capability_id"):
        if not isinstance(value.get(field), str) or not value[field]:
            issues.append(_issue(f"parameters/{field}", "is required before Modly execution"))
    surface = value.get("canonical_surface")
    if surface not in {"process_run", "workflow_run"}:
        issues.append(
            _issue(
                "parameters/canonical_surface",
                "must be process_run or workflow_run",
            )
        )
    if operation in {"process_run", "workflow_run"} and surface != operation:
        issues.append(
            _issue(
                "parameters/canonical_surface",
                "does not match the requested operation",
            )
        )
    if value.get("support_state") != "supported":
        issues.append(
            _issue(
                "parameters/support_state",
                "live discovery must report supported before execution",
            )
        )
    discovery = value.get("capability_discovery")
    if root is None:
        if not isinstance(discovery, dict):
            issues.append(
                _issue(
                    "parameters/capability_discovery",
                    "a hash-bound discovery snapshot is required",
                )
            )
        else:
            issues.extend(
                _exact_key_issues(
                    discovery,
                    frozenset({"file", "sha256"})
                    if "size" not in discovery
                    else frozenset({"file", "sha256", "size"}),
                    "parameters/capability_discovery",
                )
            )
            if not isinstance(discovery.get("file"), str) or not discovery["file"]:
                issues.append(_issue("parameters/capability_discovery/file", "is required"))
            if not _valid_hash(discovery.get("sha256")):
                issues.append(_issue("parameters/capability_discovery/sha256", "invalid SHA-256"))
    else:
        try:
            discovery_path = verify_artifact_reference(
                root,
                discovery,
                context="parameters/capability_discovery",
            )
        except AssetContractError as exc:
            issues.append(_issue("parameters/capability_discovery", str(exc)))
        else:
            try:
                snapshot = read_json_object(discovery_path)
            except AssetContractError as exc:
                issues.append(_issue("parameters/capability_discovery", str(exc)))
            else:
                snapshot_issues = _modly_capability_discovery_issues(snapshot)
                for item in snapshot_issues:
                    issues.append(
                        _issue(
                            f"parameters/capability_discovery/{item.path}",
                            item.message,
                        )
                    )
                for field in (
                    "modly_cli_mcp_version",
                    "modly_version",
                    "canonical_surface",
                    "capability_id",
                    "support_state",
                    "extension",
                    "model",
                ):
                    if snapshot.get(field) != value.get(field):
                        issues.append(
                            _issue(
                                f"parameters/capability_discovery/{field}",
                                "does not match the approved Modly request",
                            )
                        )
    extension = value.get("extension")
    if not isinstance(extension, dict):
        issues.append(_issue("parameters/extension", "versioned extension identity is required"))
    else:
        issues.extend(
            _exact_key_issues(
                extension,
                frozenset({"id", "version", "revision", "manifest_hash", "workflow_hash"}),
                "parameters/extension",
            )
        )
        for field in ("id", "version", "revision", "manifest_hash", "workflow_hash"):
            item = extension.get(field)
            if field.endswith("hash"):
                if not _valid_hash(item):
                    issues.append(_issue(f"parameters/extension/{field}", "invalid SHA-256"))
            elif not isinstance(item, str) or not item:
                issues.append(_issue(f"parameters/extension/{field}", "is required"))
    model = value.get("model")
    if not isinstance(model, dict):
        issues.append(_issue("parameters/model", "model and weights identity is required"))
    else:
        issues.extend(
            _exact_key_issues(
                model,
                frozenset({"id", "version", "weights_hash"}),
                "parameters/model",
            )
        )
        for field in ("id", "version", "weights_hash"):
            item = model.get(field)
            if field == "weights_hash":
                if not _valid_hash(item):
                    issues.append(_issue(f"parameters/model/{field}", "invalid SHA-256"))
            elif not isinstance(item, str) or not item:
                issues.append(_issue(f"parameters/model/{field}", "is required"))
    if value.get("setup_reviewed") is not True:
        issues.append(_issue("parameters/setup_reviewed", "must be true before execution"))
    if not isinstance(value.get("arguments"), dict):
        issues.append(_issue("parameters/arguments", "must be an object"))
    return issues


def _openai_parameter_issues(value: object) -> list[ContractIssue]:
    if not isinstance(value, dict):
        return []
    model = value.get("model")
    if value.get("background") == "transparent" and isinstance(model, str):
        if model.startswith("gpt-image-2"):
            return [
                _issue(
                    "parameters/background",
                    "gpt-image-2 does not support transparent output",
                )
            ]
    return []


def _blender_dependency_issues(
    *,
    executor: object,
    operation: object,
    route: object,
    inputs: object,
    parents: object,
) -> list[ContractIssue]:
    if executor != "blender_mcp" or not isinstance(operation, str):
        return []
    required_roles = _BLENDER_REQUIRED_INPUT_ROLES.get(operation)
    if required_roles is None:
        return []
    issues: list[ContractIssue] = []
    if not isinstance(inputs, list) or not inputs:
        issues.append(_issue("inputs", f"{operation} requires parent-produced inputs"))
        input_roles: set[str] = set()
    else:
        input_roles = {
            item.get("role")
            for item in inputs
            if isinstance(item, dict) and isinstance(item.get("role"), str)
        }
    missing_roles = sorted(required_roles - input_roles)
    if missing_roles:
        issues.append(
            _issue(
                "inputs",
                f"{operation} requires input roles: {', '.join(missing_roles)}",
            )
        )
    if not isinstance(parents, list) or not parents:
        issues.append(
            _issue(
                "parent_receipt_hashes",
                f"{operation} requires at least one parent receipt",
            )
        )
    if route == "modly" and operation == "model_from_reference":
        issues.append(
            _issue(
                "route",
                "model_from_reference belongs to the OpenAI reference-to-model chain",
            )
        )
    return issues


def _parent_receipts_by_hash(
    root: Path,
    hashes: list[str],
    *,
    receipt_index: ProductionReceiptIndex | None = None,
    lineage_stack: frozenset[str] = frozenset(),
) -> tuple[dict[str, dict[str, Any]], list[ContractIssue]]:
    wanted = set(hashes)
    found: dict[str, dict[str, Any]] = {}
    issues: list[ContractIssue] = []
    if receipt_index is not None:
        for content_hash in sorted(wanted):
            if content_hash in lineage_stack or not receipt_index.authorizes(content_hash):
                continue
            try:
                candidate = receipt_index.read(content_hash)
            except AssetContractError as exc:
                issues.append(
                    _issue(
                        "parent_receipt_hashes",
                        f"authorized parent receipt {content_hash} is invalid: {exc}",
                    )
                )
                continue
            if (
                candidate.get("format") != "rpg-world-forge.asset_production_receipt"
                or candidate.get("format_version") != 1
            ):
                issues.append(
                    _issue(
                        "parent_receipt_hashes",
                        f"authorized parent receipt {content_hash} has an invalid contract",
                    )
                )
                continue
            found[content_hash] = candidate
        return found, issues
    receipts_root = root / "receipts"
    if not wanted or not receipts_root.is_dir() or receipts_root.is_symlink():
        return found, issues
    for path in sorted(receipts_root.rglob("*.json")):
        if path.is_symlink():
            continue
        try:
            candidate = read_json_object(path)
            require_content_hash(candidate, context="parent production receipt")
        except AssetContractError:
            continue
        content_hash = candidate.get("content_hash")
        if (
            isinstance(content_hash, str)
            and content_hash in wanted
            and content_hash not in lineage_stack
            and candidate.get("format") == "rpg-world-forge.asset_production_receipt"
            and candidate.get("format_version") == 1
        ):
            if validate_production_receipt(
                path,
                asset_root=root,
                _lineage_stack=lineage_stack,
            ):
                continue
            found[content_hash] = candidate
    return found, issues


def _blender_parent_lineage_issues(
    request: dict[str, Any],
    parent_receipts: dict[str, dict[str, Any]],
) -> list[ContractIssue]:
    if request.get("executor") != "blender_mcp":
        return []
    operation = request.get("operation")
    if not isinstance(operation, str):
        return []
    parent_hashes = request.get("parent_receipt_hashes")
    if not isinstance(parent_hashes, list):
        return []
    issues: list[ContractIssue] = []
    valid_parent_hashes = [item for item in parent_hashes if isinstance(item, str)]
    missing = [item for item in valid_parent_hashes if item not in parent_receipts]
    for item in missing:
        issues.append(_issue("parent_receipt_hashes", f"cannot resolve parent receipt {item}"))
    parents = [parent_receipts[item] for item in valid_parent_hashes if item in parent_receipts]
    allowed_operations = _BLENDER_ALLOWED_PARENT_OPERATIONS.get(operation, frozenset())
    for index, parent in enumerate(parents):
        context = f"parent_receipt_hashes/{index}"
        if parent.get("asset_id") != request.get("asset_id"):
            issues.append(_issue(context, "parent asset does not match the request"))
        if parent.get("route") != request.get("route"):
            issues.append(_issue(context, "parent route does not match the request"))
        if parent.get("status") != "succeeded":
            issues.append(_issue(context, "parent receipt did not succeed"))
        if parent.get("operation") not in allowed_operations:
            issues.append(
                _issue(
                    context,
                    f"operation {parent.get('operation')!r} cannot parent {operation}",
                )
            )
    modly_parents = [parent for parent in parents if parent.get("executor") == "modly_cli_mcp"]
    if request.get("route") == "modly" and operation == "refine" and parents:
        if not modly_parents:
            issues.append(
                _issue(
                    "parent_receipt_hashes",
                    "Modly refinement requires a direct modly_cli_mcp parent",
                )
            )
    parent_outputs: list[dict[str, Any]] = []
    for parent in parents:
        outputs = parent.get("outputs")
        if isinstance(outputs, list):
            parent_outputs.extend(output for output in outputs if isinstance(output, dict))
    inputs = request.get("inputs")
    if isinstance(inputs, list):
        for role in sorted(_BLENDER_REQUIRED_INPUT_ROLES.get(operation, frozenset())):
            matching_inputs = [
                item for item in inputs if isinstance(item, dict) and item.get("role") == role
            ]
            accepted_output_roles = _BLENDER_PARENT_OUTPUT_ROLES[role]
            if matching_inputs and not any(
                parent_output.get("role") in accepted_output_roles
                and parent_output.get("file") == item.get("file")
                and parent_output.get("sha256") == item.get("sha256")
                for item in matching_inputs
                for parent_output in parent_outputs
            ):
                issues.append(
                    _issue(
                        "inputs",
                        f"{role} input is not an exact output of an approved parent receipt",
                    )
                )
        if request.get("route") == "modly" and operation == "refine" and modly_parents:
            model_inputs = [
                item for item in inputs if isinstance(item, dict) and item.get("role") == "model"
            ]
            modly_outputs: list[dict[str, Any]] = []
            for parent in modly_parents:
                outputs = parent.get("outputs")
                if isinstance(outputs, list):
                    modly_outputs.extend(output for output in outputs if isinstance(output, dict))
            if len(model_inputs) != 1 or not any(
                output.get("role") == "model"
                and output.get("file") == model_inputs[0].get("file")
                and output.get("sha256") == model_inputs[0].get("sha256")
                for output in modly_outputs
            ):
                issues.append(
                    _issue(
                        "inputs",
                        "Modly refinement model must be the exact output of one direct "
                        "modly_cli_mcp parent",
                    )
                )
    return issues


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None


def _expected_output_issues(
    value: object,
    *,
    executor: object,
    operation: object,
) -> list[ContractIssue]:
    if not isinstance(value, list) or not value or len(value) > 256:
        return [_issue("expected_outputs", "must contain 1..256 output contracts")]
    issues: list[ContractIssue] = []
    pairs: list[tuple[str, str]] = []
    allowed_roles = (
        _OPERATION_OUTPUT_ROLES.get(operation)
        if isinstance(executor, str)
        and executor in {"openai_image", "blender_mcp"}
        and isinstance(operation, str)
        else None
    )
    for index, item in enumerate(value):
        context = f"expected_outputs/{index}"
        if not isinstance(item, dict):
            issues.append(_issue(context, "must be an object"))
            continue
        issues.extend(
            _exact_key_issues(
                item,
                frozenset({"role", "media_type"}),
                context,
            )
        )
        role = item.get("role")
        media_type = item.get("media_type")
        if not isinstance(role, str) or role not in _CANDIDATE_ROLE_MEDIA:
            issues.append(_issue(f"{context}/role", "unknown candidate role"))
            continue
        if not isinstance(media_type, str) or media_type not in _CANDIDATE_ROLE_MEDIA[role]:
            issues.append(_issue(f"{context}/media_type", "is incompatible with the role"))
            continue
        if executor == "openai_image" and media_type not in _IMAGE_CANDIDATE_MEDIA:
            issues.append(_issue(f"{context}/media_type", "OpenAI Image must return an image"))
        if allowed_roles is not None and role not in allowed_roles:
            issues.append(
                _issue(
                    f"{context}/role",
                    f"is incompatible with operation {operation}",
                )
            )
        pairs.append((role, media_type))
    if len(pairs) != len(set(pairs)):
        issues.append(_issue("expected_outputs", "must use unique role/media pairs"))
    return issues


def _reference_with_role(root: Path, role: str, relative: str) -> dict[str, Any]:
    if not isinstance(role, str) or not role:
        raise AssetContractError("Every production input requires a role")
    reference = artifact_reference(root, relative)
    return {"role": role, **reference}


def create_production_request(
    asset_root: str | Path,
    specification_file: str,
    output_path: str | Path,
    *,
    request_id: str,
    route: str,
    executor: str,
    operation: str,
    inputs: list[tuple[str, str]] | None = None,
    parameters: dict[str, Any] | None = None,
    expected_outputs: list[dict[str, str]] | None = None,
    parent_receipt_hashes: list[str] | None = None,
    reviewed_script_file: str | None = None,
) -> dict[str, Any]:
    """Create an immutable, provider-external production request.

    This function never calls OpenAI, Blender, or Modly. An agent executes the
    reviewed request outside Forge and returns a sanitized receipt.
    """

    root = Path(asset_root).resolve()
    specification_path = root / specification_file
    spec_issues = validate_asset_spec(specification_path)
    if spec_issues:
        raise AssetContractError("; ".join(str(issue) for issue in spec_issues))
    spec = read_json_object(specification_path)
    if spec.get("format_version") != 2:
        raise AssetContractError("Production requests require asset-spec version 2")
    if not isinstance(route, str) or route not in ROUTES:
        raise AssetContractError("route must be openai or modly")
    if (
        not isinstance(executor, str)
        or executor not in EXECUTORS
        or route not in _EXECUTOR_ROUTES.get(executor, set())
    ):
        raise AssetContractError(f"executor {executor!r} is incompatible with route {route!r}")
    if not isinstance(operation, str) or operation not in PRODUCTION_OPERATIONS:
        raise AssetContractError(f"unsupported production operation: {operation}")
    production = spec.get("production", {})
    if route not in production.get("allowed_routes", []):
        raise AssetContractError(f"The specification does not permit route {route}")
    if executor not in production.get("allowed_executors", []):
        raise AssetContractError(f"The specification does not permit executor {executor}")
    if operation not in _EXECUTOR_OPERATIONS[executor]:
        raise AssetContractError(
            f"operation {operation!r} is incompatible with executor {executor!r}"
        )
    if not isinstance(request_id, str) or ID_PATTERN.fullmatch(request_id) is None:
        raise AssetContractError("request_id must be a portable canonical ID")
    parent_hashes = [] if parent_receipt_hashes is None else list(parent_receipt_hashes)
    if not _sorted_unique_hashes(parent_hashes):
        raise AssetContractError("parent receipt hashes must be sorted unique SHA-256 digests")
    normalized_parameters = {} if parameters is None else parameters
    if not isinstance(normalized_parameters, dict):
        raise AssetContractError("parameters must be an object")
    if executor == "modly_cli_mcp":
        modly_issues = _modly_parameter_issues(
            normalized_parameters,
            operation=operation,
            root=root,
        )
        if modly_issues:
            raise AssetContractError("; ".join(str(issue) for issue in modly_issues))
    if executor == "openai_image":
        openai_issues = _openai_parameter_issues(normalized_parameters)
        if openai_issues:
            raise AssetContractError("; ".join(str(issue) for issue in openai_issues))
    sensitive = _scan_sensitive(normalized_parameters, "parameters")
    if sensitive:
        raise AssetContractError("; ".join(str(issue) for issue in sensitive))
    normalized_outputs = spec["expected_outputs"] if expected_outputs is None else expected_outputs
    output_issues = _expected_output_issues(
        normalized_outputs,
        executor=executor,
        operation=operation,
    )
    if output_issues:
        raise AssetContractError("; ".join(str(issue) for issue in output_issues))
    bound_inputs = [
        _reference_with_role(root, role, relative)
        for role, relative in sorted(inputs or [], key=lambda item: (item[0], item[1]))
    ]
    dependency_issues = _blender_dependency_issues(
        executor=executor,
        operation=operation,
        route=route,
        inputs=bound_inputs,
        parents=parent_hashes,
    )
    if dependency_issues:
        raise AssetContractError("; ".join(str(issue) for issue in dependency_issues))
    body: dict[str, Any] = {
        "format": "rpg-world-forge.asset_production_request",
        "format_version": 1,
        "id": request_id,
        "asset_id": spec["id"],
        "specification": artifact_reference(root, specification_file),
        "target_id": spec["target_id"],
        "target_hash": spec["target_hash"],
        "orchestrator": "gpt",
        "route": route,
        "executor": executor,
        "operation": operation,
        "inputs": bound_inputs,
        "parameters": normalized_parameters,
        "expected_outputs": normalized_outputs,
        "parent_receipt_hashes": parent_hashes,
    }
    if executor == "blender_mcp":
        if reviewed_script_file is None:
            raise AssetContractError("Blender MCP requests require a pre-reviewed script")
        if not reviewed_script_file.casefold().endswith(".py"):
            raise AssetContractError("The reviewed Blender script must use the .py extension")
        body["reviewed_script"] = artifact_reference(root, reviewed_script_file)
        body["approval_mode"] = "explicit"
    elif reviewed_script_file is not None:
        raise AssetContractError("reviewed_script_file is only valid for Blender MCP")
    if executor == "blender_mcp":
        parent_receipts, resolution_issues = _parent_receipts_by_hash(root, parent_hashes)
        lineage_issues = resolution_issues + _blender_parent_lineage_issues(
            body,
            parent_receipts,
        )
        if lineage_issues:
            raise AssetContractError("; ".join(str(issue) for issue in lineage_issues))
    request = bind_content_hash(body)
    write_json_atomic(output_path, request)
    return request


def validate_production_request(
    path: str | Path,
    *,
    asset_root: str | Path | None = None,
    receipt_index: ProductionReceiptIndex | None = None,
    _lineage_stack: frozenset[str] = frozenset(),
) -> list[ContractIssue]:
    try:
        raw = read_json_object(path)
    except AssetContractError as exc:
        return [_issue("request", str(exc))]
    issues = _base_contract_issues(
        raw,
        expected_format="rpg-world-forge.asset_production_request",
    )
    expected_keys = _REQUEST_KEYS
    if raw.get("executor") == "blender_mcp":
        expected_keys |= {"reviewed_script", "approval_mode"}
    issues.extend(_exact_key_issues(raw, expected_keys, "request"))
    for field in ("id", "asset_id", "target_id"):
        if not isinstance(raw.get(field), str) or ID_PATTERN.fullmatch(raw[field]) is None:
            issues.append(_issue(field, "must be a portable canonical ID"))
    if not _valid_hash(raw.get("target_hash")):
        issues.append(_issue("target_hash", "invalid SHA-256"))
    if raw.get("orchestrator") != "gpt":
        issues.append(_issue("orchestrator", "GPT must remain the orchestrator"))
    route = raw.get("route")
    executor = raw.get("executor")
    operation = raw.get("operation")
    if not isinstance(route, str) or route not in ROUTES:
        issues.append(_issue("route", "unknown production route"))
    if not isinstance(executor, str) or executor not in EXECUTORS:
        issues.append(_issue("executor", "unknown executor"))
    elif not isinstance(route, str) or route not in _EXECUTOR_ROUTES[executor]:
        issues.append(_issue("executor", "executor is incompatible with the route"))
    if not isinstance(operation, str) or operation not in PRODUCTION_OPERATIONS:
        issues.append(_issue("operation", "unknown production operation"))
    elif (
        isinstance(executor, str)
        and executor in _EXECUTOR_OPERATIONS
        and operation not in _EXECUTOR_OPERATIONS[executor]
    ):
        issues.append(_issue("operation", "operation is incompatible with the executor"))
    parents = raw.get("parent_receipt_hashes")
    if not _sorted_unique_hashes(parents):
        issues.append(_issue("parent_receipt_hashes", "must be sorted unique SHA-256 digests"))
    issues.extend(
        _blender_dependency_issues(
            executor=executor,
            operation=operation,
            route=route,
            inputs=raw.get("inputs"),
            parents=parents,
        )
    )
    issues.extend(
        _expected_output_issues(
            raw.get("expected_outputs"),
            executor=executor,
            operation=operation,
        )
    )
    if asset_root is not None:
        root = Path(asset_root).resolve()
        try:
            spec_path = verify_artifact_reference(
                root, raw.get("specification"), context="specification"
            )
        except AssetContractError as exc:
            issues.append(_issue("specification", str(exc)))
        else:
            spec_issues = validate_asset_spec(
                spec_path,
                expected_id=raw.get("asset_id") if isinstance(raw.get("asset_id"), str) else None,
                target_hash=raw.get("target_hash")
                if isinstance(raw.get("target_hash"), str)
                else None,
            )
            issues.extend(
                _issue(f"specification/{item.path}", item.message) for item in spec_issues
            )
            spec = read_json_object(spec_path)
            if raw.get("target_id") != spec.get("target_id"):
                issues.append(_issue("target_id", "does not match the specification"))
            if raw.get("target_hash") != spec.get("target_hash"):
                issues.append(_issue("target_hash", "does not match the specification"))
            production = spec.get("production")
            if isinstance(production, dict):
                if route not in production.get("allowed_routes", []):
                    issues.append(_issue("route", "is not permitted by the specification"))
                if executor not in production.get("allowed_executors", []):
                    issues.append(_issue("executor", "is not permitted by the specification"))
        inputs = raw.get("inputs")
        if not isinstance(inputs, list):
            issues.append(_issue("inputs", "must be a list"))
        else:
            canonical_keys: list[tuple[str, str]] = []
            input_shapes_valid = True
            for reference in inputs:
                if not isinstance(reference, dict):
                    input_shapes_valid = False
                    continue
                role_value = reference.get("role")
                file_value = reference.get("file")
                if not isinstance(role_value, str) or not isinstance(file_value, str):
                    input_shapes_valid = False
                    continue
                canonical_keys.append((role_value, file_value))
            if not input_shapes_valid or canonical_keys != sorted(canonical_keys):
                issues.append(_issue("inputs", "must use canonical role/file order"))
            for index, reference in enumerate(inputs):
                try:
                    verify_artifact_reference(
                        root,
                        reference,
                        context=f"inputs/{index}",
                        allowed_extra=frozenset({"role"}),
                    )
                except AssetContractError as exc:
                    issues.append(_issue(f"inputs/{index}", str(exc)))
                if (
                    not isinstance(reference, dict)
                    or not isinstance(reference.get("role"), str)
                    or not reference["role"]
                ):
                    issues.append(_issue(f"inputs/{index}/role", "is required"))
        if executor == "blender_mcp":
            try:
                script_path = verify_artifact_reference(
                    root,
                    raw.get("reviewed_script"),
                    context="reviewed_script",
                )
            except AssetContractError as exc:
                issues.append(_issue("reviewed_script", str(exc)))
            else:
                if script_path.suffix.casefold() != ".py":
                    issues.append(_issue("reviewed_script", "must be a Python script"))
            if raw.get("approval_mode") != "explicit":
                issues.append(_issue("approval_mode", "must be explicit before Blender execution"))
            parent_hashes = (
                [item for item in parents if isinstance(item, str)]
                if isinstance(parents, list)
                else []
            )
            parent_receipts, resolution_issues = _parent_receipts_by_hash(
                root,
                parent_hashes,
                receipt_index=receipt_index,
                lineage_stack=_lineage_stack,
            )
            issues.extend(resolution_issues)
            issues.extend(_blender_parent_lineage_issues(raw, parent_receipts))
    parameters = raw.get("parameters")
    if not isinstance(parameters, dict):
        issues.append(_issue("parameters", "must be an object"))
    elif executor == "modly_cli_mcp":
        issues.extend(
            _modly_parameter_issues(
                parameters,
                operation=operation,
                root=Path(asset_root).resolve() if asset_root is not None else None,
            )
        )
    elif executor == "openai_image":
        issues.extend(_openai_parameter_issues(parameters))
    issues.extend(_scan_sensitive(raw))
    return issues


def _executor_receipt_issues(
    raw: dict[str, Any],
    request: dict[str, Any],
    *,
    root: Path,
) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    toolchain = raw.get("toolchain")
    executor = raw.get("executor")
    if not isinstance(toolchain, dict):
        return [_issue("toolchain", "must be an object")]
    if executor == "openai_image":
        issues.extend(_exact_key_issues(toolchain, _OPENAI_TOOLCHAIN_KEYS, "toolchain"))
        for field in ("surface", "requested_model", "version_resolution"):
            if not isinstance(toolchain.get(field), str) or not toolchain[field]:
                issues.append(
                    _issue(f"toolchain/{field}", "is required for OpenAI image production")
                )
        if toolchain.get("surface") not in {"images_api", "responses_api"}:
            issues.append(_issue("toolchain/surface", "must be images_api or responses_api"))
        resolution = toolchain.get("version_resolution")
        resolved = toolchain.get("resolved_model")
        if resolution == "exact_snapshot" and (not isinstance(resolved, str) or not resolved):
            issues.append(_issue("toolchain/resolved_model", "exact snapshots must be recorded"))
        if resolution == "unavailable_from_surface" and resolved is not None:
            issues.append(
                _issue("toolchain/resolved_model", "must be null when the surface hides it")
            )
        parameters = request.get("parameters")
        if isinstance(parameters, dict) and isinstance(parameters.get("model"), str):
            if toolchain.get("requested_model") != parameters["model"]:
                issues.append(
                    _issue(
                        "toolchain/requested_model",
                        "does not match the model approved in the request",
                    )
                )
        if (
            isinstance(parameters, dict)
            and parameters.get("background") == "transparent"
            and str(toolchain.get("requested_model", "")).startswith("gpt-image-2")
        ):
            issues.append(_issue("toolchain", "gpt-image-2 does not support transparent output"))
        if raw.get("replayability") != "traceable_not_bit_reproducible":
            issues.append(
                _issue("replayability", "OpenAI generation must not claim bit reproducibility")
            )
    elif executor == "blender_mcp":
        issues.extend(_exact_key_issues(toolchain, _BLENDER_TOOLCHAIN_KEYS, "toolchain"))
        for field in ("blender_version", "blender_mcp_version", "addon_revision"):
            if not isinstance(toolchain.get(field), str) or not toolchain[field]:
                issues.append(_issue(f"toolchain/{field}", "is required for Blender MCP"))
        if toolchain.get("telemetry_disabled") is not True:
            issues.append(_issue("toolchain/telemetry_disabled", "must be true"))
        if raw.get("reviewed_script") != request.get("reviewed_script"):
            issues.append(
                _issue("reviewed_script", "does not match the pre-approved request script")
            )
        if raw.get("approval_mode") != request.get("approval_mode"):
            issues.append(_issue("approval_mode", "does not match the pre-approved request"))
    elif executor == "modly_cli_mcp":
        issues.extend(_exact_key_issues(toolchain, _MODLY_TOOLCHAIN_KEYS, "toolchain"))
        for field in (
            "modly_cli_mcp_version",
            "modly_version",
            "canonical_surface",
            "capability_id",
            "run_id",
        ):
            if not isinstance(toolchain.get(field), str) or not toolchain[field]:
                issues.append(_issue(f"toolchain/{field}", "is required for Modly"))
        if toolchain.get("canonical_surface") not in {"workflow_run", "process_run"}:
            issues.append(
                _issue("toolchain/canonical_surface", "must be workflow_run or process_run")
            )
        if toolchain.get("support_state") != "supported":
            issues.append(
                _issue("toolchain/support_state", "live capability discovery must report supported")
            )
        if not _valid_hash(toolchain.get("capability_discovery_hash")):
            issues.append(
                _issue(
                    "toolchain/capability_discovery_hash", "a discovery snapshot hash is required"
                )
            )
        extension = toolchain.get("extension")
        if not isinstance(extension, dict):
            issues.append(_issue("toolchain/extension", "versioned extension identity is required"))
        else:
            issues.extend(
                _exact_key_issues(
                    extension,
                    frozenset({"id", "version", "revision", "manifest_hash", "workflow_hash"}),
                    "toolchain/extension",
                )
            )
            for field in ("id", "version", "revision", "manifest_hash", "workflow_hash"):
                value = extension.get(field)
                if field.endswith("hash"):
                    if not _valid_hash(value):
                        issues.append(_issue(f"toolchain/extension/{field}", "invalid SHA-256"))
                elif not isinstance(value, str) or not value:
                    issues.append(_issue(f"toolchain/extension/{field}", "is required"))
        model = toolchain.get("model")
        if not isinstance(model, dict):
            issues.append(_issue("toolchain/model", "model and weights identity is required"))
        else:
            issues.extend(
                _exact_key_issues(
                    model,
                    frozenset({"id", "version", "weights_hash"}),
                    "toolchain/model",
                )
            )
            for field in ("id", "version", "weights_hash"):
                value = model.get(field)
                if field == "weights_hash":
                    if not _valid_hash(value):
                        issues.append(_issue(f"toolchain/model/{field}", "invalid SHA-256"))
                elif not isinstance(value, str) or not value:
                    issues.append(_issue(f"toolchain/model/{field}", "is required"))
        if toolchain.get("setup_reviewed") is not True:
            issues.append(
                _issue("toolchain/setup_reviewed", "extension setup code must be reviewed")
            )
        parameters = request.get("parameters")
        if isinstance(parameters, dict):
            for field in (
                "modly_cli_mcp_version",
                "modly_version",
                "canonical_surface",
                "capability_id",
                "support_state",
                "extension",
                "model",
                "setup_reviewed",
            ):
                if toolchain.get(field) != parameters.get(field):
                    issues.append(
                        _issue(
                            f"toolchain/{field}",
                            "does not match the value approved before execution",
                        )
                    )
            discovery = parameters.get("capability_discovery")
            expected_discovery_hash = (
                discovery.get("sha256") if isinstance(discovery, dict) else None
            )
            if toolchain.get("capability_discovery_hash") != expected_discovery_hash:
                issues.append(
                    _issue(
                        "toolchain/capability_discovery_hash",
                        "does not match the approved discovery snapshot",
                    )
                )
        if raw.get("replayability") not in {
            "deterministic_seeded",
            "traceable_not_bit_reproducible",
        }:
            issues.append(_issue("replayability", "must state actual Modly replayability"))
    return issues


def validate_production_receipt(
    path: str | Path,
    *,
    asset_root: str | Path,
    receipt_index: ProductionReceiptIndex | None = None,
    _lineage_stack: frozenset[str] = frozenset(),
) -> list[ContractIssue]:
    if receipt_index is None:
        try:
            raw = read_json_object(path)
        except AssetContractError as exc:
            return [_issue("receipt", str(exc))]
    else:
        try:
            _, raw = receipt_index.read_path(path)
        except AssetContractError as exc:
            return [_issue("receipt", str(exc))]
    issues = _base_contract_issues(
        raw,
        expected_format="rpg-world-forge.asset_production_receipt",
    )
    expected_keys = _RECEIPT_KEYS
    if raw.get("executor") == "blender_mcp":
        expected_keys |= {"reviewed_script", "approval_mode"}
    issues.extend(_exact_key_issues(raw, expected_keys, "receipt"))
    for field in ("id", "asset_id"):
        if not isinstance(raw.get(field), str) or ID_PATTERN.fullmatch(raw[field]) is None:
            issues.append(_issue(field, "must be a portable canonical ID"))
    started = _timestamp(raw.get("started_at"))
    completed = _timestamp(raw.get("completed_at"))
    if started is None:
        issues.append(_issue("started_at", "must be an RFC 3339 UTC timestamp"))
    if completed is None:
        issues.append(_issue("completed_at", "must be an RFC 3339 UTC timestamp"))
    if started is not None and completed is not None and completed < started:
        issues.append(_issue("completed_at", "must not precede started_at"))
    root = Path(asset_root).resolve()
    try:
        request_path = verify_artifact_reference(root, raw.get("request"), context="request")
        request = read_json_object(request_path)
        require_content_hash(request, context="production request")
    except AssetContractError as exc:
        issues.append(_issue("request", str(exc)))
        request = {}
    else:
        receipt_hash = raw.get("content_hash")
        current_lineage = (
            _lineage_stack | {receipt_hash} if isinstance(receipt_hash, str) else _lineage_stack
        )
        request_issues = validate_production_request(
            request_path,
            asset_root=root,
            receipt_index=receipt_index,
            _lineage_stack=current_lineage,
        )
        issues.extend(_issue(f"request/{item.path}", item.message) for item in request_issues)
    for field in ("asset_id", "route", "executor", "operation"):
        if raw.get(field) != request.get(field):
            issues.append(_issue(field, "does not match the production request"))
    if raw.get("status") != "succeeded":
        issues.append(_issue("status", "only succeeded receipts can enter asset lineage"))
    parents = raw.get("parent_receipt_hashes")
    if parents != request.get("parent_receipt_hashes"):
        issues.append(_issue("parent_receipt_hashes", "does not match the production request"))
    outputs = raw.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        issues.append(_issue("outputs", "at least one candidate is required"))
    else:
        seen_paths: set[str] = set()
        for index, output in enumerate(outputs):
            if not isinstance(output, dict):
                issues.append(_issue(f"outputs/{index}", "must be an object"))
                continue
            output_path: Path | None = None
            try:
                output_path = verify_artifact_reference(
                    root,
                    output,
                    context=f"outputs/{index}",
                    allowed_extra=frozenset({"role", "media_type", "width", "height"}),
                )
            except AssetContractError as exc:
                issues.append(_issue(f"outputs/{index}", str(exc)))
            role = output.get("role")
            media_type = output.get("media_type")
            if not isinstance(role, str) or role not in _CANDIDATE_ROLE_MEDIA:
                issues.append(_issue(f"outputs/{index}/role", "unknown candidate role"))
            elif not isinstance(media_type, str) or media_type not in _CANDIDATE_ROLE_MEDIA[role]:
                issues.append(
                    _issue(
                        f"outputs/{index}/media_type",
                        "is incompatible with the candidate role",
                    )
                )
            else:
                allowed_roles = (
                    _OPERATION_OUTPUT_ROLES.get(request.get("operation"))
                    if isinstance(request.get("executor"), str)
                    and request.get("executor") in {"openai_image", "blender_mcp"}
                    and isinstance(request.get("operation"), str)
                    else None
                )
                if allowed_roles is not None and role not in allowed_roles:
                    issues.append(
                        _issue(
                            f"outputs/{index}/role",
                            "is incompatible with the requested operation",
                        )
                    )
                if (
                    request.get("executor") == "openai_image"
                    and media_type not in _IMAGE_CANDIDATE_MEDIA
                ):
                    issues.append(
                        _issue(
                            f"outputs/{index}/media_type",
                            "OpenAI Image must return an image",
                        )
                    )
            if isinstance(media_type, str) and media_type not in _MEDIA_TYPES:
                issues.append(_issue(f"outputs/{index}/media_type", "unknown candidate media type"))
            elif output_path is not None and isinstance(media_type, str):
                if media_type == "model/gltf-binary":
                    try:
                        inspect_glb(output_path, allow_external_uris=False)
                    except GLBError as exc:
                        issues.append(_issue(f"outputs/{index}/media_type", f"invalid GLB: {exc}"))
                elif media_type == "application/x-blender":
                    try:
                        with output_path.open("rb") as source:
                            blender_header = source.read(7)
                    except OSError as exc:
                        issues.append(_issue(f"outputs/{index}/media_type", str(exc)))
                    else:
                        if blender_header != b"BLENDER":
                            issues.append(
                                _issue(
                                    f"outputs/{index}/media_type", "does not match Blender bytes"
                                )
                            )
                elif media_type in _IMAGE_CANDIDATE_MEDIA:
                    try:
                        inspection = inspect_image_file(
                            output_path,
                            f"outputs/{index}",
                        )
                    except AssetContractError as exc:
                        issues.append(_issue(f"outputs/{index}/media_type", str(exc)))
                    else:
                        if inspection.media_type != media_type:
                            issues.append(
                                _issue(
                                    f"outputs/{index}/media_type",
                                    "does not match the decoded image format",
                                )
                            )
                        if (
                            "width" in output
                            and "height" in output
                            and (
                                output["width"] != inspection.width
                                or output["height"] != inspection.height
                            )
                        ):
                            issues.append(
                                _issue(
                                    f"outputs/{index}",
                                    "declared dimensions do not match decoded image bytes",
                                )
                            )
                elif not media_signature_matches(output_path, media_type):
                    issues.append(
                        _issue(f"outputs/{index}/media_type", "does not match candidate bytes")
                    )
            if ("width" in output) != ("height" in output):
                issues.append(
                    _issue(
                        f"outputs/{index}",
                        "width and height must be declared together",
                    )
                )
            if (
                "width" in output or "height" in output
            ) and media_type not in _IMAGE_CANDIDATE_MEDIA:
                issues.append(
                    _issue(
                        f"outputs/{index}",
                        "dimensions are only valid for decoded image outputs",
                    )
                )
            for dimension in ("width", "height"):
                if dimension in output and (
                    isinstance(output[dimension], bool)
                    or not isinstance(output[dimension], int)
                    or output[dimension] <= 0
                ):
                    issues.append(
                        _issue(f"outputs/{index}/{dimension}", "must be a positive integer")
                    )
            file_value = output.get("file")
            if isinstance(file_value, str):
                if file_value in seen_paths:
                    issues.append(_issue(f"outputs/{index}/file", "duplicate candidate path"))
                seen_paths.add(file_value)
        expected_outputs = request.get("expected_outputs")
        if isinstance(expected_outputs, list):
            expected_pairs = sorted(
                (item["role"], item["media_type"])
                for item in expected_outputs
                if isinstance(item, dict)
                and isinstance(item.get("role"), str)
                and isinstance(item.get("media_type"), str)
            )
            actual_pairs = sorted(
                (item["role"], item["media_type"])
                for item in outputs
                if isinstance(item, dict)
                and isinstance(item.get("role"), str)
                and isinstance(item.get("media_type"), str)
            )
            if expected_pairs != actual_pairs:
                issues.append(
                    _issue("outputs", "do not exactly match the request expected outputs")
                )
    if request:
        issues.extend(_executor_receipt_issues(raw, request, root=root))
    issues.extend(_scan_sensitive(raw))
    return issues
