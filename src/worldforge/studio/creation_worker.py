from __future__ import annotations

import hashlib
import hmac
import os
import stat
import sys
from pathlib import Path
from typing import Any, BinaryIO

from isoworld.content.file_stat import path_file_stat
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.directory_publish import fsync_directory
from worldforge.studio.contracts import (
    ENTITY_ID_PATTERN,
    validate_studio_creation_worker_envelope,
)
from worldforge.studio.creation_job_protocol import (
    MAX_PRIVATE_CREATION_REQUEST_BYTES,
    CreationWorkerProtocolError,
    execute_private_creation_request,
)
from worldforge.studio.errors import StudioContractError, StudioError
from worldforge.studio.jsonio import (
    decode_ndjson_object,
    encode_ndjson_object,
    read_ndjson_line,
)

MAX_CREATION_WORKER_RESPONSE_BYTES = 1024 * 1024
_DENIED_AUDIT_EVENTS = frozenset(
    {
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
        "os.system",
        "pty.spawn",
        "subprocess.Popen",
    }
)


def _worker_audit_hook(event: str, _arguments: tuple[object, ...]) -> None:
    if event.startswith("socket.") or event.startswith("os.exec") or event in _DENIED_AUDIT_EVENTS:
        raise PermissionError(f"creation worker capability denied: {event}")


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _stage_identity(stage: Path) -> tuple[int, int]:
    info = path_file_stat(stage)
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise CreationWorkerProtocolError("creation worker stage is unsafe")
    return int(info.st_dev), int(info.st_ino)


def _locator_path(stage: Path, locator: object) -> Path:
    if not isinstance(locator, str) or ENTITY_ID_PATTERN.fullmatch(locator) is None:
        raise CreationWorkerProtocolError("creation worker locator is invalid")
    return stage / f"{locator}.json"


def _read_bound_file(path: Path, *, limit: int) -> tuple[bytes, tuple[int, int]]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        named = path_file_stat(path)
        if (
            _is_link_or_reparse(opened)
            or _is_link_or_reparse(named)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise CreationWorkerProtocolError("creation worker file is unsafe")
        payload = bytearray()
        while len(payload) <= limit:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        if len(payload) > limit:
            raise CreationWorkerProtocolError("creation worker file exceeds its byte limit")
        confirmed = os.fstat(descriptor)
        if (confirmed.st_dev, confirmed.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ) or confirmed.st_size != len(payload):
            raise CreationWorkerProtocolError("creation worker file changed while reading")
        return bytes(payload), (int(opened.st_dev), int(opened.st_ino))
    finally:
        os.close(descriptor)


def _write_output(
    stage: Path,
    locator: str,
    payload: bytes,
    *,
    binary: bool = False,
) -> None:
    if binary:
        if locator != "game_package_archive":
            raise CreationWorkerProtocolError("creation worker binary locator is invalid")
        path = stage / "game_package_archive.wfgame"
    else:
        path = _locator_path(stage, locator)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        opened = os.fstat(descriptor)
        if _is_link_or_reparse(opened) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise CreationWorkerProtocolError("creation worker output is unsafe")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short creation worker output write")
            view = view[written:]
        os.fsync(descriptor)
        confirmed = os.fstat(descriptor)
        named = path_file_stat(path)
        if (
            confirmed.st_size != len(payload)
            or (confirmed.st_dev, confirmed.st_ino) != (opened.st_dev, opened.st_ino)
            or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            or named.st_nlink != 1
        ):
            raise CreationWorkerProtocolError("creation worker output changed after writing")
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _execute(envelope: object, stage: Path) -> dict[str, Any]:
    try:
        request_envelope = validate_studio_creation_worker_envelope(envelope)
    except StudioContractError as exc:
        raise CreationWorkerProtocolError(str(exc)) from exc
    if request_envelope["kind"] != "request":
        raise CreationWorkerProtocolError("creation worker accepts only request envelopes")
    stage_identity = _stage_identity(stage)
    request_path = _locator_path(stage, request_envelope["request_locator"])
    request_payload, _request_identity = _read_bound_file(
        request_path,
        limit=MAX_PRIVATE_CREATION_REQUEST_BYTES,
    )
    if not hmac.compare_digest(
        hashlib.sha256(request_payload).hexdigest(),
        request_envelope["request_sha256"],
    ):
        raise CreationWorkerProtocolError("creation worker request digest changed")
    try:
        request = decode_json_object(request_payload, source="creation worker request")
    except RuntimeIOError as exc:
        raise CreationWorkerProtocolError("creation worker request is not strict JSON") from exc
    if (
        request.get("job_id") != request_envelope["job_id"]
        or request.get("operation") != request_envelope["operation"]
    ):
        raise CreationWorkerProtocolError("creation worker request authority differs")
    result = execute_private_creation_request(
        request,
        artifact_root=(stage / "artifact_root")
        if request["operation"]
        in {
            "asset.process",
            "asset.release.authorize",
            "asset.release.seal",
            "runtime.compose",
            "runtime.bundle.build",
            "game.materialization.bundle.build",
            "game.materialize",
            "game.package",
            "game.package.extract",
            "asset.qa.review",
            "runtime.headless.verify",
        }
        else None,
    )
    outputs: list[dict[str, Any]] = []
    for output in result.outputs:
        _write_output(stage, output.locator, output.payload)
        outputs.append(
            {
                "locator": output.locator,
                "subject": output.subject,
                "size": len(output.payload),
                "sha256": hashlib.sha256(output.payload).hexdigest(),
            }
        )
    if request["operation"] == "game.package":
        if (
            len(result.binary_outputs) != 1
            or result.binary_outputs[0].locator != request["archive_output"]["locator"]
            or hashlib.sha256(result.binary_outputs[0].payload).hexdigest()
            != request["archive_output"]["sha256"]
            or len(result.binary_outputs[0].payload) != request["archive_output"]["size_bytes"]
        ):
            raise CreationWorkerProtocolError("creation worker game package binary output changed")
        _write_output(
            stage,
            result.binary_outputs[0].locator,
            result.binary_outputs[0].payload,
            binary=True,
        )
    elif result.binary_outputs:
        raise CreationWorkerProtocolError(
            "creation worker produced unexpected private binary outputs"
        )
    fsync_directory(stage, context="creation worker output stage")
    if _stage_identity(stage) != stage_identity:
        raise CreationWorkerProtocolError("creation worker stage identity changed")
    response = {
        "format": "world-forge.studio_creation_worker",
        "format_version": request_envelope["format_version"],
        "kind": "response",
        "job_id": request_envelope["job_id"],
        "operation": request_envelope["operation"],
        "ok": True,
        "outputs": outputs,
        "metadata": {
            "analysis_status": result.analysis_status,
            "reason_codes": list(result.reason_codes),
        },
    }
    try:
        return validate_studio_creation_worker_envelope(response)
    except StudioContractError as exc:
        raise CreationWorkerProtocolError("creation worker produced an invalid response") from exc


def _error_response(envelope: object, error: BaseException) -> dict[str, Any] | None:
    if not isinstance(envelope, dict):
        return None
    job_id = envelope.get("job_id")
    operation = envelope.get("operation")
    if (
        not isinstance(job_id, str)
        or ENTITY_ID_PATTERN.fullmatch(job_id) is None
        or operation
        not in {
            "artifact.admit",
            "asset.process",
            "asset.release.authorize",
            "asset.release.seal",
            "creation.compile",
            "runtime.compose",
            "runtime.bundle.build",
            "game.materialization.bundle.build",
            "game.materialize",
            "game.package",
            "game.package.extract",
            "asset.qa.review",
            "runtime.headless.verify",
        }
    ):
        return None
    code = "worker_protocol" if isinstance(error, CreationWorkerProtocolError) else "internal_error"
    message = (
        "Creation worker request or output failed validation"
        if code == "worker_protocol"
        else "Creation worker failed"
    )
    response = {
        "format": "world-forge.studio_creation_worker",
        "format_version": (
            12
            if operation == "runtime.headless.verify"
            else 11
            if operation == "asset.release.authorize"
            else 10
            if operation == "asset.qa.review"
            else 9
            if operation == "game.package.extract"
            else 8
            if operation == "game.package"
            else 7
            if operation == "game.materialize"
            else 6
            if operation == "game.materialization.bundle.build"
            else 5
            if operation == "runtime.bundle.build"
            else 4
            if operation == "runtime.compose"
            else 3
            if operation == "asset.release.seal"
            else 2
            if operation == "asset.process"
            else 1
        ),
        "kind": "response",
        "job_id": job_id,
        "operation": operation,
        "ok": False,
        "error": {"code": code, "message": message, "retryable": False},
    }
    try:
        return validate_studio_creation_worker_envelope(response)
    except StudioContractError:
        return None


def main(input_stream: BinaryIO | None = None, output_stream: BinaryIO | None = None) -> int:
    source = input_stream or sys.stdin.buffer
    target = output_stream or sys.stdout.buffer
    envelope: object = None
    try:
        sys.addaudithook(_worker_audit_hook)
        line = read_ndjson_line(source)
        if line is None:
            return 2
        envelope = decode_ndjson_object(line)
        response = _execute(envelope, Path.cwd())
    except (CreationWorkerProtocolError, StudioError, OSError, ValueError) as exc:
        response = _error_response(envelope, exc)
        if response is None:
            return 2
    payload = encode_ndjson_object(response)
    if len(payload) > MAX_CREATION_WORKER_RESPONSE_BYTES:
        return 2
    target.write(payload)
    target.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the isolated executor
    raise SystemExit(main())
