from __future__ import annotations

import copy
import hashlib
import hmac
import os
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from gamepack_runtime.game_package import (
    MAX_GAME_PACKAGE_ARCHIVE_BYTES,
    GamePackageError,
    verify_game_package_bytes,
)
from isoworld.content.file_stat import path_file_stat
from isoworld.runtime_io import RuntimeIOError, decode_json_object
from worldforge.asset_io import AssetContractError, write_bytes_atomic
from worldforge.directory_publish import directory_identity, fsync_directory
from worldforge.generic_asset_processing import (
    GenericAssetProcessingError,
    validate_asset_processing_receipt_document,
)
from worldforge.generic_asset_production import (
    GenericAssetProductionError,
    read_verified_artifact_bytes,
)
from worldforge.generic_headless import GenericHeadlessError, verify_headless_evidence_set
from worldforge.integrity import canonical_json_bytes
from worldforge.phase_report_v3 import PhaseReportV3Error, document_identity
from worldforge.studio.contracts import (
    validate_studio_creation_worker_envelope,
    validate_studio_recovery_evidence,
)
from worldforge.studio.creation_job_protocol import (
    MAX_PRIVATE_CREATION_REQUEST_BYTES,
    validate_private_creation_request,
)
from worldforge.studio.creation_process import creation_process_identity
from worldforge.studio.errors import StudioContractError, StudioError
from worldforge.studio.executor import (
    MAX_WORKER_STDERR_BYTES,
    _BoundedCapture,
    _terminate_and_reap,
    _WindowsJob,
)
from worldforge.studio.jsonio import decode_ndjson_object, encode_ndjson_object

MAX_CREATION_WORKER_RESPONSE_BYTES = 1024 * 1024
_POLL_SECONDS = 0.025


class CreationWorkerExecutionError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        recovery_evidence: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.recovery_evidence: dict[str, object] = {}
        if recovery_evidence:
            checked = validate_studio_recovery_evidence(
                dict(recovery_evidence),
                "creation worker recovery evidence",
            )
            self.recovery_evidence = copy.deepcopy(checked)
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class VerifiedCreationOutput:
    locator: str
    subject: dict[str, Any]
    payload: bytes
    size: int
    sha256: str
    file_identity: tuple[int, int]


@dataclass(frozen=True)
class VerifiedCreationBinaryOutput:
    locator: str
    payload: bytes
    size: int
    sha256: str
    file_identity: tuple[int, int]


@dataclass(frozen=True)
class CreationWorkerExecution:
    response: dict[str, Any]
    outputs: tuple[VerifiedCreationOutput, ...]
    binary_outputs: tuple[VerifiedCreationBinaryOutput, ...] = ()


def worker_command() -> tuple[str, ...]:
    source_root = Path(__file__).resolve().parents[2]
    parent_guard = ""
    if sys.platform.startswith("linux") and os.name == "posix":
        parent_guard = (
            "import ctypes,os;"
            "_wf_parent=int(os.environ.pop('WORLD_FORGE_STUDIO_PARENT_PID'));"
            "_wf_libc=ctypes.CDLL(None,use_errno=True);"
            "((_wf_libc.prctl(1,9,0,0,0)==0 and os.getppid()==_wf_parent) "
            "or os._exit(70));"
        )
    bootstrap = (
        parent_guard
        + "import runpy,sys;"
        + f"sys.path.insert(0,{str(source_root)!r});"
        + "runpy.run_module('worldforge.studio.creation_worker',run_name='__main__')"
    )
    return (sys.executable, "-I", "-u", "-c", bootstrap)


def worker_environment() -> dict[str, str]:
    environment = {"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    if sys.platform.startswith("linux") and os.name == "posix":
        environment["WORLD_FORGE_STUDIO_PARENT_PID"] = str(os.getpid())
    if os.name == "nt":
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
            value = os.environ.get(key)
            if value:
                environment[key] = value
    return environment


def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _directory_proof(path: Path) -> tuple[int, int]:
    try:
        return directory_identity(path, context="creation worker directory")
    except Exception as exc:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Creation worker directory is unsafe"
        ) from exc


def _file_proof(path: Path, *, hash_bytes: bool) -> tuple[object, ...]:
    resolved = path.resolve(strict=True)
    info = path_file_stat(resolved)
    if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
        raise CreationWorkerExecutionError("worker_protocol", "Worker runtime file is unsafe")
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest() if hash_bytes else None
    return (
        str(resolved),
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        digest,
    )


def _worker_identity_proof() -> tuple[tuple[object, ...], tuple[object, ...], tuple[int, int]]:
    runtime = _file_proof(Path(sys.executable), hash_bytes=False)
    module = _file_proof(Path(__file__).with_name("creation_worker.py"), hash_bytes=True)
    source_root = _directory_proof(Path(__file__).resolve().parents[2])
    return runtime, module, source_root


def create_creation_stage(
    parent: Path,
    _job_id: str,
    *,
    locator: str | None = None,
) -> tuple[Path, tuple[int, int]]:
    if not parent.is_absolute():
        parent = Path(os.path.abspath(parent))
    parent_identity = _directory_proof(parent)
    attempts = 1 if locator is not None else 8
    if locator is not None and (
        not locator.startswith("stage_")
        or len(locator) != len("stage_") + 32
        or any(character not in "0123456789abcdef" for character in locator[6:])
    ):
        raise CreationWorkerExecutionError("worker_protocol", "Creation stage locator is invalid")
    for _attempt in range(attempts):
        selected_locator = locator or f"stage_{uuid.uuid4().hex}"
        stage = parent / selected_locator
        try:
            stage.mkdir(mode=0o700)
        except FileExistsError:
            continue
        identity = _directory_proof(stage)
        fsync_directory(parent, context="creation worker stage parent")
        if _directory_proof(parent) != parent_identity or _directory_proof(stage) != identity:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker stage binding changed"
            )
        return stage, identity
    raise CreationWorkerExecutionError("internal_error", "Could not reserve creation stage")


def _write_exclusive(path: Path, payload: bytes) -> tuple[int, int]:
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
    identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        opened = os.fstat(descriptor)
        identity = (int(opened.st_dev), int(opened.st_ino))
        if _is_link_or_reparse(opened) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise CreationWorkerExecutionError("worker_protocol", "Private request is unsafe")
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short private request write")
            view = view[written:]
        os.fsync(descriptor)
        named = path_file_stat(path)
        confirmed = os.fstat(descriptor)
        if (
            (int(named.st_dev), int(named.st_ino)) != identity
            or (int(confirmed.st_dev), int(confirmed.st_ino)) != identity
            or confirmed.st_size != len(payload)
            or named.st_nlink != 1
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol", "Private request changed during publication"
            )
        return identity
    finally:
        if descriptor is not None:
            os.close(descriptor)


def write_private_request(
    stage: Path,
    request: object,
    *,
    locator: str | None = None,
) -> tuple[str, str]:
    checked = validate_private_creation_request(request)
    payload = canonical_json_bytes(checked)
    if len(payload) > MAX_PRIVATE_CREATION_REQUEST_BYTES:
        raise CreationWorkerExecutionError("worker_protocol", "Private request is too large")
    stage_identity = _directory_proof(stage)
    selected_locator = locator or f"request_{uuid.uuid4().hex}"
    if (
        not selected_locator.startswith("request_")
        or len(selected_locator) != len("request_") + 32
        or any(character not in "0123456789abcdef" for character in selected_locator[8:])
    ):
        raise CreationWorkerExecutionError("worker_protocol", "Private request locator is invalid")
    _write_exclusive(stage / f"{selected_locator}.json", payload)
    fsync_directory(stage, context="creation worker request stage")
    if _directory_proof(stage) != stage_identity:
        raise CreationWorkerExecutionError("worker_protocol", "Private request stage changed")
    return selected_locator, hashlib.sha256(payload).hexdigest()


def stage_private_asset_inputs(
    stage: Path,
    request: object,
    payloads: Sequence[tuple[str, bytes]],
) -> None:
    """Publish exact source bytes into one private asset-processing stage."""

    checked = validate_private_creation_request(request)
    if checked["operation"] not in {
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
    }:
        if payloads:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation job received unexpected staged binary inputs"
            )
        return
    staged_inputs = checked["staged_inputs"]
    if len(payloads) != len(staged_inputs):
        raise CreationWorkerExecutionError(
            "input_changed", "Asset processing staged input count changed"
        )
    stage_identity = _directory_proof(stage)
    artifact_root = stage / "artifact_root"
    try:
        for expected, staged in zip(staged_inputs, payloads, strict=True):
            locator, payload = staged
            if locator != expected["source_locator"] or not isinstance(payload, bytes):
                raise CreationWorkerExecutionError(
                    "input_changed", "Asset processing staged input binding changed"
                )
            if (
                len(payload) != expected["size_bytes"]
                or hashlib.sha256(payload).hexdigest() != expected["sha256"]
            ):
                raise CreationWorkerExecutionError(
                    "input_changed", "Asset processing staged input bytes changed"
                )
            destination = artifact_root.joinpath(*PurePosixPath(locator).parts)
            write_bytes_atomic(destination, payload, durable_parent=True)
            confirmed = read_verified_artifact_bytes(
                artifact_root,
                locator,
                expected_sha256=expected["sha256"],
                expected_size_bytes=expected["size_bytes"],
                limit=(
                    MAX_GAME_PACKAGE_ARCHIVE_BYTES
                    if checked["operation"] == "game.package.extract"
                    else 16 * 1024 * 1024
                ),
            )
            if confirmed != payload:
                raise CreationWorkerExecutionError(
                    "input_changed", "Asset processing staged input verification changed"
                )
    except CreationWorkerExecutionError:
        raise
    except (AssetContractError, GenericAssetProductionError, OSError, ValueError) as exc:
        raise CreationWorkerExecutionError(
            "input_changed", "Asset processing inputs could not be staged safely"
        ) from exc
    if _directory_proof(stage) != stage_identity:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Asset processing stage identity changed"
        )


def _read_bound_file(
    path: Path,
    *,
    limit: int,
) -> tuple[bytes, tuple[int, int]]:
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
        identity = (int(opened.st_dev), int(opened.st_ino))
        if (
            _is_link_or_reparse(opened)
            or _is_link_or_reparse(named)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1
            or named.st_nlink != 1
            or (int(named.st_dev), int(named.st_ino)) != identity
        ):
            raise CreationWorkerExecutionError("worker_protocol", "Worker file is unsafe")
        payload = bytearray()
        while len(payload) <= limit:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        confirmed = os.fstat(descriptor)
        if len(payload) > limit or confirmed.st_size != len(payload):
            raise CreationWorkerExecutionError(
                "worker_protocol", "Worker file changed or exceeded its bound"
            )
        return bytes(payload), identity
    finally:
        os.close(descriptor)


def _verified_outputs(
    stage: Path,
    response: dict[str, Any],
) -> tuple[VerifiedCreationOutput, ...]:
    outputs: list[VerifiedCreationOutput] = []
    for raw in response["outputs"]:
        locator = raw["locator"]
        payload, identity = _read_bound_file(
            stage / f"{locator}.json",
            limit=int(raw["size"]),
        )
        digest = hashlib.sha256(payload).hexdigest()
        if len(payload) != raw["size"] or not hmac.compare_digest(digest, raw["sha256"]):
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker output hash or size changed"
            )
        try:
            document = decode_json_object(payload, source="creation worker output")
            subject = document_identity(document)
        except (RuntimeIOError, PhaseReportV3Error, TypeError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker output is not a typed JSON artifact"
            ) from exc
        if canonical_json_bytes(document) != payload or subject != raw["subject"]:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker output identity or bytes changed"
            )
        outputs.append(
            VerifiedCreationOutput(
                locator=locator,
                subject=subject,
                payload=payload,
                size=len(payload),
                sha256=digest,
                file_identity=identity,
            )
        )
    return tuple(outputs)


def _verified_binary_outputs(
    stage: Path,
    response: Mapping[str, Any],
    outputs: Sequence[VerifiedCreationOutput],
    request: Mapping[str, Any],
) -> tuple[VerifiedCreationBinaryOutput, ...]:
    if response["operation"] == "game.package":
        if (
            request.get("operation") != "game.package"
            or len(outputs) != 1
            or outputs[0].subject.get("format") != "world-forge.game_package"
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol", "Game package worker output set is not exact"
            )
        archive = request.get("archive_output")
        if not isinstance(archive, dict) or archive.get("locator") != "game_package_archive":
            raise CreationWorkerExecutionError(
                "worker_protocol", "Game package worker archive binding is invalid"
            )
        try:
            payload, identity = _read_bound_file(
                stage / "game_package_archive.wfgame",
                limit=MAX_GAME_PACKAGE_ARCHIVE_BYTES,
            )
            verified = verify_game_package_bytes(payload)
            output_document = decode_json_object(
                outputs[0].payload,
                source="game package worker manifest",
            )
        except (GamePackageError, OSError, RuntimeIOError, TypeError, ValueError) as exc:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Game package worker archive is not integral"
            ) from exc
        digest = hashlib.sha256(payload).hexdigest()
        if (
            verified.manifest != output_document
            or digest != archive.get("sha256")
            or len(payload) != archive.get("size_bytes")
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol", "Game package worker archive identity changed"
            )
        return (
            VerifiedCreationBinaryOutput(
                locator="game_package_archive",
                payload=payload,
                size=len(payload),
                sha256=digest,
                file_identity=identity,
            ),
        )
    if response["operation"] != "asset.process":
        return ()
    receipts = [
        output
        for output in outputs
        if output.subject.get("format") == "world-forge.asset_processing_receipt"
    ]
    if len(receipts) != 1:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Asset processing worker receipt set is not exact"
        )
    try:
        receipt = validate_asset_processing_receipt_document(
            decode_json_object(receipts[0].payload, source="asset processing worker receipt")
        )
    except (RuntimeIOError, GenericAssetProcessingError, TypeError, ValueError) as exc:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Asset processing worker receipt is invalid"
        ) from exc
    if receipt["status"] == "completed":
        records = receipt["outputs"]
    else:
        recovery = receipt["recovery"]
        records = recovery["retained_artifacts"]
    artifact_root = stage / "artifact_root"
    verified: list[VerifiedCreationBinaryOutput] = []
    try:
        for record in records:
            locator = str(record["locator"])
            payload = read_verified_artifact_bytes(
                artifact_root,
                locator,
                expected_sha256=record["sha256"],
                expected_size_bytes=record["size_bytes"],
                limit=16 * 1024 * 1024,
            )
            path = artifact_root.joinpath(*PurePosixPath(locator).parts)
            info = path_file_stat(path)
            if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise CreationWorkerExecutionError(
                    "worker_protocol", "Processed asset output is unsafe"
                )
            verified.append(
                VerifiedCreationBinaryOutput(
                    locator=locator,
                    payload=payload,
                    size=len(payload),
                    sha256=str(record["sha256"]),
                    file_identity=(int(info.st_dev), int(info.st_ino)),
                )
            )
    except CreationWorkerExecutionError:
        raise
    except (GenericAssetProductionError, OSError, ValueError) as exc:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Processed asset output bytes are not integral"
        ) from exc
    return tuple(verified)


def verify_creation_stage_outputs(
    stage: Path,
    expected_stage_identity: tuple[int, int],
    request_locator: str,
    request_sha256: str,
    outputs: Sequence[VerifiedCreationOutput],
    binary_outputs: Sequence[VerifiedCreationBinaryOutput],
) -> None:
    """Require the complete private worker tree to match its declared outputs."""

    if _directory_proof(stage) != expected_stage_identity:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Creation worker stage identity changed"
        )
    request_name = f"{request_locator}.json"
    try:
        request_payload, request_identity = _read_bound_file(
            stage / request_name,
            limit=MAX_PRIVATE_CREATION_REQUEST_BYTES,
        )
        if not hmac.compare_digest(hashlib.sha256(request_payload).hexdigest(), request_sha256):
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker request digest changed"
            )
        request = validate_private_creation_request(
            decode_json_object(request_payload, source="creation worker retained request")
        )
    except CreationWorkerExecutionError:
        raise
    except (OSError, RuntimeIOError, TypeError, ValueError) as exc:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Creation worker retained request is invalid"
        ) from exc

    expected_files: dict[str, tuple[str, int, tuple[int, int] | None]] = {
        request_name: (request_sha256, len(request_payload), request_identity)
    }

    def bind_file(
        locator: str,
        sha256: str,
        size: int,
        identity: tuple[int, int] | None,
    ) -> None:
        if locator in expected_files:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker stage output locators overlap"
            )
        expected_files[locator] = (sha256, size, identity)

    for output in outputs:
        bind_file(
            f"{output.locator}.json",
            output.sha256,
            output.size,
            output.file_identity,
        )
    if request["operation"] in {
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
    }:
        for staged_input in request["staged_inputs"]:
            bind_file(
                f"artifact_root/{staged_input['source_locator']}",
                str(staged_input["sha256"]),
                int(staged_input["size_bytes"]),
                None,
            )
        if request["operation"] == "asset.process":
            for output in binary_outputs:
                bind_file(
                    f"artifact_root/{output.locator}",
                    output.sha256,
                    output.size,
                    output.file_identity,
                )
        elif request["operation"] == "game.package":
            archive = request["archive_output"]
            if len(binary_outputs) != 1:
                raise CreationWorkerExecutionError(
                    "worker_protocol",
                    "Creation worker game package archive set changed",
                )
            output = binary_outputs[0]
            bind_file(
                "game_package_archive.wfgame",
                str(archive["sha256"]),
                int(archive["size_bytes"]),
                output.file_identity,
            )
        elif request["operation"] == "runtime.headless.verify":
            try:
                verified = verify_headless_evidence_set(
                    stage / "artifact_root" / "headless-evidence",
                    bundle_root=stage / "artifact_root" / "runtime-bundle",
                )
                try:
                    if len(outputs) != 3:
                        raise CreationWorkerExecutionError(
                            "worker_protocol",
                            "Runtime headless worker output set is not exact",
                        )
                    evidence = decode_json_object(
                        verified.read_bytes("runtime/evidence.json"),
                        source="runtime headless worker evidence",
                    )
                    support = decode_json_object(
                        verified.read_bytes("runtime/support-report.json"),
                        source="runtime headless worker support report",
                    )
                    output_evidence = decode_json_object(
                        outputs[1].payload,
                        source="runtime headless worker output evidence",
                    )
                    output_support = decode_json_object(
                        outputs[2].payload,
                        source="runtime headless worker output support report",
                    )
                    if (
                        output_evidence != evidence
                        or output_support != support
                        or evidence["platform"]["platform_id"] != request["platform_id"]
                    ):
                        raise CreationWorkerExecutionError(
                            "worker_protocol",
                            "Runtime headless worker evidence outputs changed",
                        )
                    for relative, payload in verified.files.items():
                        bind_file(
                            f"artifact_root/headless-evidence/{relative}",
                            hashlib.sha256(payload).hexdigest(),
                            len(payload),
                            None,
                        )
                finally:
                    verified.close()
            except CreationWorkerExecutionError:
                raise
            except (GenericHeadlessError, OSError, RuntimeIOError, TypeError, ValueError) as exc:
                raise CreationWorkerExecutionError(
                    "worker_protocol",
                    "Runtime headless worker evidence tree is not integral",
                ) from exc
        elif binary_outputs:
            raise CreationWorkerExecutionError(
                "worker_protocol",
                "Creation worker returned unexpected binary outputs",
            )
    elif binary_outputs:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Non-asset creation worker returned binary outputs"
        )

    expected_directories = {"."}
    for locator in expected_files:
        parent = PurePosixPath(locator).parent
        while parent.as_posix() != ".":
            expected_directories.add(parent.as_posix())
            parent = parent.parent

    actual_files: set[str] = set()
    actual_directories = {"."}
    pending = [stage]
    try:
        while pending:
            current = pending.pop()
            for entry in current.iterdir():
                relative = entry.relative_to(stage).as_posix()
                info = path_file_stat(entry)
                if _is_link_or_reparse(info):
                    raise CreationWorkerExecutionError(
                        "worker_protocol", "Creation worker stage contains a link"
                    )
                if stat.S_ISDIR(info.st_mode):
                    if relative in actual_directories or relative not in expected_directories:
                        raise CreationWorkerExecutionError(
                            "worker_protocol",
                            "Creation worker stage output tree is not exact",
                        )
                    actual_directories.add(relative)
                    pending.append(entry)
                    continue
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or relative in actual_files
                    or relative not in expected_files
                ):
                    raise CreationWorkerExecutionError(
                        "worker_protocol", "Creation worker stage output tree is not exact"
                    )
                digest, size, expected_identity = expected_files[relative]
                payload, identity = _read_bound_file(entry, limit=size)
                if (
                    len(payload) != size
                    or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), digest)
                    or (expected_identity is not None and identity != expected_identity)
                ):
                    raise CreationWorkerExecutionError(
                        "worker_protocol", "Creation worker stage output bytes changed"
                    )
                actual_files.add(relative)
    except CreationWorkerExecutionError:
        raise
    except OSError as exc:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Creation worker stage output tree could not be inspected"
        ) from exc
    if actual_files != set(expected_files) or actual_directories != expected_directories:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Creation worker stage output tree is not exact"
        )
    if _directory_proof(stage) != expected_stage_identity:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Creation worker stage identity changed"
        )


def run_isolated_creation_worker(
    stage: Path,
    expected_stage_identity: tuple[int, int],
    envelope: object,
    *,
    timeout_seconds: float = 60.0,
    cancel_requested: Callable[[], bool] | None = None,
    process_started: Callable[[int, dict[str, Any]], None] | None = None,
    process_stopped: Callable[[int, dict[str, Any]], None] | None = None,
) -> CreationWorkerExecution:
    if not 0.05 <= float(timeout_seconds) <= 3600.0:
        raise ValueError("creation worker timeout is outside its fixed bounds")
    try:
        checked_envelope = validate_studio_creation_worker_envelope(envelope)
    except StudioContractError as exc:
        raise CreationWorkerExecutionError("worker_protocol", "Worker envelope is invalid") from exc
    if checked_envelope["kind"] != "request":
        raise CreationWorkerExecutionError("worker_protocol", "Worker request kind is invalid")
    if _directory_proof(stage) != expected_stage_identity:
        raise CreationWorkerExecutionError("worker_protocol", "Worker stage identity changed")
    request_path = stage / f"{checked_envelope['request_locator']}.json"
    request_payload, request_identity = _read_bound_file(
        request_path,
        limit=MAX_PRIVATE_CREATION_REQUEST_BYTES,
    )
    if not hmac.compare_digest(
        hashlib.sha256(request_payload).hexdigest(),
        checked_envelope["request_sha256"],
    ):
        raise CreationWorkerExecutionError("worker_protocol", "Worker request digest changed")
    try:
        request_document = validate_private_creation_request(
            decode_json_object(request_payload, source="creation worker retained request")
        )
    except (RuntimeIOError, TypeError, ValueError) as exc:
        raise CreationWorkerExecutionError(
            "worker_protocol", "Worker request is not a closed private request"
        ) from exc
    runtime_proof = _worker_identity_proof()
    process: subprocess.Popen[bytes] | None = None
    tree = _WindowsJob()
    stdout_capture: _BoundedCapture | None = None
    stderr_capture: _BoundedCapture | None = None
    registered_process: tuple[int, dict[str, Any]] | None = None
    try:
        process = subprocess.Popen(
            worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=stage,
            env=worker_environment(),
            shell=False,
            start_new_session=os.name == "posix",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            ),
        )
        tree.assign(process)
        process_identity = creation_process_identity(process.pid)
        if process_started is not None:
            process_started(process.pid, process_identity)
        registered_process = (process.pid, process_identity)
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_capture = _BoundedCapture(process.stdout, MAX_CREATION_WORKER_RESPONSE_BYTES)
        stderr_capture = _BoundedCapture(process.stderr, MAX_WORKER_STDERR_BYTES)
        stdout_capture.start()
        stderr_capture.start()
        process.stdin.write(encode_ndjson_object(checked_envelope))
        process.stdin.close()
        deadline = time.monotonic() + float(timeout_seconds)
        stop_reason: str | None = None
        while process.poll() is None:
            if cancel_requested is not None and cancel_requested():
                stop_reason = "canceled"
                break
            if time.monotonic() >= deadline:
                stop_reason = "timeout"
                break
            time.sleep(_POLL_SECONDS)
        if stop_reason is not None:
            _terminate_and_reap(process, tree)
            raise CreationWorkerExecutionError(stop_reason, f"Creation worker {stop_reason}")
        return_code = process.wait()
        stdout_capture.join()
        stderr_capture.join()
        if stdout_capture.overflow or stderr_capture.overflow:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker output exceeded its bound"
            )
        if return_code != 0 or stderr_capture.payload:
            raise CreationWorkerExecutionError("worker_crashed", "Creation worker crashed")
        try:
            response = decode_ndjson_object(bytes(stdout_capture.payload))
            checked_response = validate_studio_creation_worker_envelope(response)
        except (StudioError, StudioContractError) as exc:
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker response is invalid"
            ) from exc
        if (
            checked_response["kind"] != "response"
            or checked_response["job_id"] != checked_envelope["job_id"]
            or checked_response["operation"] != checked_envelope["operation"]
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker response authority differs"
            )
        outputs = _verified_outputs(stage, checked_response) if checked_response["ok"] else ()
        binary_outputs = (
            _verified_binary_outputs(stage, checked_response, outputs, request_document)
            if checked_response["ok"]
            else ()
        )
        after_request, after_request_identity = _read_bound_file(
            request_path,
            limit=MAX_PRIVATE_CREATION_REQUEST_BYTES,
        )
        if (
            after_request_identity != request_identity
            or after_request != request_payload
            or _directory_proof(stage) != expected_stage_identity
            or _worker_identity_proof() != runtime_proof
        ):
            raise CreationWorkerExecutionError(
                "worker_protocol", "Creation worker authority changed during execution"
            )
        verify_creation_stage_outputs(
            stage,
            expected_stage_identity,
            checked_envelope["request_locator"],
            checked_envelope["request_sha256"],
            outputs,
            binary_outputs,
        )
        return CreationWorkerExecution(checked_response, outputs, binary_outputs)
    except (OSError, subprocess.SubprocessError) as exc:
        if process is not None:
            _terminate_and_reap(process, tree)
        raise CreationWorkerExecutionError(
            "worker_crashed", "Creation worker could not run"
        ) from exc
    except BaseException:
        if process is not None:
            _terminate_and_reap(process, tree)
        raise
    finally:
        if stdout_capture is not None:
            stdout_capture.close()
        if stderr_capture is not None:
            stderr_capture.close()
        try:
            if registered_process is not None and process_stopped is not None:
                process_stopped(*registered_process)
        finally:
            tree.close()
