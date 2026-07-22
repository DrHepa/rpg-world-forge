from __future__ import annotations

import json
import os
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

from isoworld.content.loader import load_worldpack
from isoworld.core.app import GameApp
from isoworld.persistence import load_replay, state_digest
from worldforge.asset_production import validate_production_receipt
from worldforge.assetpack import verify_assetpack
from worldforge.studio.contracts import (
    MANAGED_JOB_OPERATIONS,
    StudioContractError,
    studio_job_path,
    validate_job_create_params,
)
from worldforge.studio.job_paths import (
    JobFileProof,
    JobPathError,
    proof_matches,
    verify_root,
    verify_workspace_file,
)
from worldforge.studio.job_protocol import (
    MAX_WORKER_REQUEST_BYTES,
    MAX_WORKER_RESPONSE_BYTES,
    WORKER_PROTOCOL,
    WORKER_VERSION,
)
from worldforge.studio.jsonio import decode_ndjson_object

MAX_RESULT_TEXT = 512


class WorkerProtocolError(ValueError):
    pass


def _closed(value: object, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise WorkerProtocolError(f"{context} has an invalid shape")
    return value


def _identity(value: object) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
    ):
        raise WorkerProtocolError("world identity is invalid")
    return value[0], value[1]


def _root(value: object) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise WorkerProtocolError("world root is invalid")
    root = Path(value)
    if not root.is_absolute() or str(root) != os.path.abspath(root):
        raise WorkerProtocolError("world root must be an absolute normalized path")
    return root


def _operation_paths(operation: str, job_input: dict[str, Any]) -> dict[str, PurePosixPath]:
    fields = {
        "asset.receipt.validate": ("receipt",),
        "assetpack.verify": ("assetpack", "worldpack"),
        "runtime.headless": ("worldpack",),
        "runtime.replay": ("worldpack", "replay"),
    }[operation]
    result: dict[str, PurePosixPath] = {}
    for field in fields:
        relative = studio_job_path(job_input[field])
        if relative is None:  # validate_job_create_params already enforces this
            raise WorkerProtocolError("managed job path is invalid")
        result[field] = (
            PurePosixPath("assets").joinpath(relative)
            if operation == "asset.receipt.validate"
            else relative
        )
    return result


def _verify_files(
    root: Path,
    root_identity: tuple[int, int],
    paths: dict[str, PurePosixPath],
    expected: object,
) -> dict[str, JobFileProof]:
    proofs = _closed(expected, set(paths), "worker files")
    verified: dict[str, JobFileProof] = {}
    for field, relative in paths.items():
        proof = verify_workspace_file(root, relative, world_identity=root_identity)
        if not proof_matches(proof, proofs[field]):
            raise JobPathError("managed job input identity or content changed")
        verified[field] = proof
    return verified


def _bounded_text(value: object, *, redact: Path | None = None) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    if redact is not None:
        text = text.replace(str(redact), "<assets>")
    return text[:MAX_RESULT_TEXT]


def _receipt_result(path: Path, asset_root: Path) -> dict[str, Any]:
    issues = validate_production_receipt(path, asset_root=asset_root)
    projected = [
        {
            "path": _bounded_text(issue.path, redact=asset_root),
            "message": _bounded_text(issue.message, redact=asset_root),
        }
        for issue in issues[:256]
    ]
    return {
        "operation": "asset.receipt.validate",
        "valid": not issues,
        "issue_count": len(issues),
        "issues_truncated": len(issues) > len(projected),
        "issues": projected,
    }


def _assetpack_result(assetpack_path: Path, worldpack_path: Path) -> dict[str, Any]:
    payload = verify_assetpack(assetpack_path, worldpack_path)
    assets = payload["assets"]
    bindings = payload["bindings"]
    return {
        "operation": "assetpack.verify",
        "valid": True,
        "world_id": payload["world_id"],
        "world_content_hash": payload["world_content_hash"],
        "target_id": payload["target_id"],
        "target_hash": payload["target_hash"],
        "content_hash": payload["content_hash"],
        "asset_count": len(assets),
        "file_count": sum(len(asset["files"]) for asset in assets),
        "binding_count": len(bindings),
    }


def _headless_result(worldpack_path: Path, ticks: int) -> dict[str, Any]:
    pack = load_worldpack(worldpack_path)
    state = GameApp(pack).run_headless(ticks)
    return {
        "operation": "runtime.headless",
        "world_id": pack.world_id,
        "world_content_hash": pack.content_hash,
        "ticks": ticks,
        "state_tick": state.tick,
        "absolute_minute": state.absolute_minute,
        "state_digest": state_digest(state),
    }


def _replay_result(worldpack_path: Path, replay_path: Path) -> dict[str, Any]:
    pack = load_worldpack(worldpack_path)
    actions, state = load_replay(replay_path, pack)
    return {
        "operation": "runtime.replay",
        "world_id": pack.world_id,
        "world_content_hash": pack.content_hash,
        "action_count": len(actions),
        "state_tick": state.tick,
        "absolute_minute": state.absolute_minute,
        "state_digest": state_digest(state),
    }


def execute(request: object) -> dict[str, Any]:
    payload = _closed(
        request,
        {
            "protocol",
            "protocol_version",
            "operation",
            "input",
            "world_root",
            "world_identity",
            "files",
        },
        "worker request",
    )
    if payload["protocol"] != WORKER_PROTOCOL or payload["protocol_version"] != WORKER_VERSION:
        raise WorkerProtocolError("worker protocol is unsupported")
    operation = payload["operation"]
    if not isinstance(operation, str) or operation not in MANAGED_JOB_OPERATIONS:
        raise WorkerProtocolError("worker operation is not allowed")
    try:
        validated = validate_job_create_params(
            {"workspace_id": "worker_01", "operation": operation, "input": payload["input"]}
        )
    except StudioContractError as exc:
        raise WorkerProtocolError("worker operation input is invalid") from exc
    job_input = validated["input"]
    root = _root(payload["world_root"])
    root_identity = _identity(payload["world_identity"])
    verify_root(root, root_identity)
    paths = _operation_paths(operation, job_input)
    _verify_files(root, root_identity, paths, payload["files"])
    absolute = {field: root.joinpath(*relative.parts) for field, relative in paths.items()}
    if operation == "asset.receipt.validate":
        result = _receipt_result(absolute["receipt"], root / "assets")
    elif operation == "assetpack.verify":
        result = _assetpack_result(absolute["assetpack"], absolute["worldpack"])
    elif operation == "runtime.headless":
        result = _headless_result(absolute["worldpack"], job_input["ticks"])
    else:
        result = _replay_result(absolute["worldpack"], absolute["replay"])
    _verify_files(root, root_identity, paths, payload["files"])
    return result


def _response(value: dict[str, Any]) -> bytes:
    encoded = json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > MAX_WORKER_RESPONSE_BYTES:
        return (
            b'{"error":{"code":"worker_protocol","message":'
            b'"Worker response exceeds the bound"},"ok":false}\n'
        )
    return encoded + b"\n"


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_WORKER_REQUEST_BYTES + 1)
    if len(raw) > MAX_WORKER_REQUEST_BYTES:
        response = {
            "ok": False,
            "error": {"code": "worker_protocol", "message": "Worker request exceeds the bound"},
        }
    else:
        try:
            request = decode_ndjson_object(raw)
            response = {"ok": True, "result": execute(request)}
        except WorkerProtocolError:
            response = {
                "ok": False,
                "error": {"code": "worker_protocol", "message": "Worker request is invalid"},
            }
        except Exception:
            response = {
                "ok": False,
                "error": {"code": "execution_failed", "message": "Managed job execution failed"},
            }
    sys.stdout.buffer.write(_response(response))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
