from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from worldforge.studio.contracts import MANAGED_JOB_OPERATIONS, studio_job_path
from worldforge.studio.errors import StudioError
from worldforge.studio.job_paths import (
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
from worldforge.studio.jobs import JobManager
from worldforge.studio.jsonio import decode_ndjson_object
from worldforge.studio.storage import StudioStore, encode_json
from worldforge.studio.workspaces import WorkspaceManager

MAX_WORKER_STDERR_BYTES = 64 * 1024
DEFAULT_JOB_TIMEOUT_SECONDS = 60.0
_POLL_SECONDS = 0.025
_TERMINATE_GRACE_SECONDS = 2.0


def _worker_command() -> tuple[str, ...]:
    source_root = Path(__file__).resolve().parents[2]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(source_root)!r});"
        "runpy.run_module('worldforge.studio.worker',run_name='__main__')"
    )
    return (sys.executable, "-I", "-u", "-c", bootstrap)


def _worker_cwd() -> Path:
    return Path(__file__).resolve().parents[3]


def _worker_environment() -> dict[str, str]:
    environment = {"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    if os.name == "nt":
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP"):
            value = os.environ.get(key)
            if value:
                environment[key] = value
    return environment


class _BoundedCapture:
    def __init__(self, stream: BinaryIO, limit: int) -> None:
        self.stream = stream
        self.limit = limit
        self.payload = bytearray()
        self.overflow = False
        self.thread = threading.Thread(target=self._read, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def join(self) -> None:
        self.thread.join(timeout=_TERMINATE_GRACE_SECONDS)
        if self.thread.is_alive():
            self.overflow = True

    def close(self) -> None:
        self.join()
        self.stream.close()

    def _read(self) -> None:
        while True:
            chunk = self.stream.read(8192)
            if not chunk:
                return
            remaining = self.limit - len(self.payload)
            if remaining > 0:
                self.payload.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.overflow = True


class _WindowsJob:
    def __init__(self) -> None:
        self.handle: int | None = None

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if os.name != "nt":
            return

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_job = kernel32.CreateJobObjectW
        create_job.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        create_job.restype = ctypes.c_void_p
        set_information = kernel32.SetInformationJobObject
        set_information.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        set_information.restype = ctypes.c_int
        assign = kernel32.AssignProcessToJobObject
        assign.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        assign.restype = ctypes.c_int
        handle = create_job(None, None)
        if not handle:
            raise OSError("Windows process containment is unavailable")
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        if not set_information(handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
            kernel32.CloseHandle(handle)
            raise OSError("Windows process containment could not be configured")
        process_handle = getattr(process, "_handle", None)
        if process_handle is None or not assign(handle, process_handle):
            kernel32.CloseHandle(handle)
            raise OSError("Windows worker could not enter process containment")
        self.handle = int(handle)

    def terminate(self, process: subprocess.Popen[bytes]) -> None:
        if os.name == "nt" and self.handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if not kernel32.TerminateJobObject(ctypes.c_void_p(self.handle), 1):
                process.kill()
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        else:
            process.terminate()

    def kill(self, process: subprocess.Popen[bytes]) -> None:
        if os.name == "nt" and self.handle is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).TerminateJobObject(
                ctypes.c_void_p(self.handle), 1
            )
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        else:
            process.kill()

    def close(self) -> None:
        if os.name == "nt" and self.handle is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None


def _terminate_and_reap(process: subprocess.Popen[bytes], tree: _WindowsJob) -> None:
    if process.poll() is None:
        tree.terminate(process)
        deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
        while time.monotonic() < deadline:
            root_running = process.poll() is None
            group_running = False
            if os.name == "posix":
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    group_running = True
            if not root_running and not group_running:
                break
            time.sleep(_POLL_SECONDS)
        if process.poll() is None or group_running:
            tree.kill(process)
        process.wait(timeout=_TERMINATE_GRACE_SECONDS)
    else:
        process.wait()
    if os.name == "nt":
        tree.close()


def _operation_paths(operation: str, job_input: dict[str, Any]) -> dict[str, PurePosixPath]:
    fields = {
        "asset.receipt.validate": ("receipt",),
        "assetpack.verify": ("assetpack", "worldpack"),
        "runtime.headless": ("worldpack",),
        "runtime.replay": ("worldpack", "replay"),
    }[operation]
    paths: dict[str, PurePosixPath] = {}
    for field in fields:
        relative = studio_job_path(job_input[field])
        if relative is None:
            raise JobPathError("managed job path is invalid")
        paths[field] = (
            PurePosixPath("assets").joinpath(relative)
            if operation == "asset.receipt.validate"
            else relative
        )
    return paths


class JobScheduler:
    """One durable FIFO executor using a thread-owned secondary Studio store."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        timeout_seconds: float = DEFAULT_JOB_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(timeout_seconds, int | float) or isinstance(timeout_seconds, bool):
            raise ValueError("job timeout must be numeric")
        if not 0.05 <= float(timeout_seconds) <= 3600.0:
            raise ValueError("job timeout is outside the fixed scheduler bounds")
        self.data_dir = Path(data_dir)
        self.timeout_seconds = float(timeout_seconds)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="studio-job-scheduler")
        self._startup_error: BaseException | None = None
        self._lifecycle_lock = threading.Lock()
        self._started = False
        self._shutdown = False

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown:
                raise StudioError(
                    "invalid_state", "Studio job scheduler cannot start after shutdown"
                )
            if self._started:
                raise StudioError("invalid_state", "Studio job scheduler can start only once")
            self._started = True
            try:
                self._thread.start()
            except BaseException as exc:
                self._shutdown = True
                raise StudioError("internal_error", "Studio job scheduler could not start") from exc
        startup_error: StudioError | None = None
        if not self._ready.wait(timeout=5.0):
            startup_error = StudioError("internal_error", "Studio job scheduler did not start")
        elif self._startup_error is not None:
            startup_error = StudioError("internal_error", "Studio job scheduler could not start")
        if startup_error is not None:
            try:
                self.shutdown()
            except StudioError as exc:
                raise exc from startup_error
            raise startup_error from self._startup_error

    def notify(self) -> None:
        with self._lifecycle_lock:
            if self._shutdown:
                return
        self._wake.set()

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            self._shutdown = True
            started = self._started
        self._stop.set()
        self._wake.set()
        if started and self._thread.ident is not None:
            self._thread.join(timeout=10.0)
        if self._thread.is_alive():
            raise StudioError("internal_error", "Studio job scheduler did not stop cleanly")

    def _run(self) -> None:
        try:
            store = StudioStore(self.data_dir, mode="secondary")
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            return
        self._ready.set()
        jobs = JobManager(store)
        try:
            while not self._stop.is_set():
                claimed = jobs.claim_next()
                if claimed is None:
                    self._wake.wait(timeout=0.1)
                    self._wake.clear()
                    continue
                self._execute(jobs, store, claimed)
        finally:
            store.close()

    def _prepare_request(
        self, store: StudioStore, job: dict[str, Any]
    ) -> tuple[dict[str, Any], Path, tuple[int, int], dict[str, PurePosixPath]]:
        operation = job["operation"]
        if operation not in MANAGED_JOB_OPERATIONS:
            raise JobPathError("managed job operation is invalid")
        verified = WorkspaceManager(store).verified_root(job["workspace_id"], "world_root")
        if verified is None:
            raise JobPathError("registered world root is unavailable")
        root, root_identity = verified
        paths = _operation_paths(operation, job["input"])
        proofs = {
            field: verify_workspace_file(root, relative, world_identity=root_identity).to_dict()
            for field, relative in paths.items()
        }
        request = {
            "protocol": WORKER_PROTOCOL,
            "protocol_version": WORKER_VERSION,
            "operation": operation,
            "input": job["input"],
            "world_root": str(root),
            "world_identity": [root_identity[0], root_identity[1]],
            "files": proofs,
        }
        encoded = encode_json(request).encode("utf-8") + b"\n"
        if len(encoded) > MAX_WORKER_REQUEST_BYTES:
            raise JobPathError("managed worker request exceeds its bound")
        return request, root, root_identity, paths

    @staticmethod
    def _revalidate_request(
        request: dict[str, Any],
        root: Path,
        root_identity: tuple[int, int],
        paths: dict[str, PurePosixPath],
    ) -> None:
        verify_root(root, root_identity)
        proofs = request["files"]
        for field, relative in paths.items():
            current = verify_workspace_file(root, relative, world_identity=root_identity)
            if not proof_matches(current, proofs[field]):
                raise JobPathError("managed job input changed before worker start")

    def _execute(self, jobs: JobManager, store: StudioStore, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        try:
            request, root, root_identity, paths = self._prepare_request(store, job)
            jobs.progress(job_id, 20, "validated")
            self._revalidate_request(request, root, root_identity, paths)
        except (JobPathError, StudioError):
            jobs.finish(
                job_id,
                "failed",
                error={
                    "code": "invalid_workspace",
                    "message": "Managed job inputs are unavailable",
                },
            )
            return
        if self._stop.is_set():
            jobs.finish(job_id, "orphaned", reason="service_shutdown")
            return
        process: subprocess.Popen[bytes] | None = None
        tree = _WindowsJob()
        stdout_capture: _BoundedCapture | None = None
        stderr_capture: _BoundedCapture | None = None
        try:
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            )
            process = subprocess.Popen(
                _worker_command(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=_worker_cwd(),
                env=_worker_environment(),
                shell=False,
                start_new_session=os.name == "posix",
                creationflags=creationflags,
            )
            tree.assign(process)
            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            stdout_capture = _BoundedCapture(process.stdout, MAX_WORKER_RESPONSE_BYTES)
            stderr_capture = _BoundedCapture(process.stderr, MAX_WORKER_STDERR_BYTES)
            stdout_capture.start()
            stderr_capture.start()
            process.stdin.write(encode_json(request).encode("utf-8") + b"\n")
            process.stdin.close()
            jobs.progress(job_id, 50, "executing")
            deadline = time.monotonic() + self.timeout_seconds
            stop_reason: str | None = None
            while process.poll() is None:
                if self._stop.is_set():
                    stop_reason = "shutdown"
                    break
                if jobs.cancellation_requested(job_id):
                    stop_reason = "canceled"
                    break
                if time.monotonic() >= deadline:
                    stop_reason = "timeout"
                    break
                self._stop.wait(_POLL_SECONDS)
            if stop_reason is not None:
                _terminate_and_reap(process, tree)
                stdout_capture.join()
                stderr_capture.join()
                if stop_reason == "shutdown":
                    jobs.finish(job_id, "orphaned", reason="service_shutdown")
                elif stop_reason == "canceled":
                    jobs.finish(job_id, "canceled")
                else:
                    jobs.finish(
                        job_id,
                        "failed",
                        error={"code": "timeout", "message": "Managed job timed out"},
                    )
                return
            return_code = process.wait()
            stdout_capture.join()
            stderr_capture.join()
            if self._stop.is_set():
                jobs.finish(job_id, "orphaned", reason="service_shutdown")
                return
            if jobs.cancellation_requested(job_id):
                jobs.finish(job_id, "canceled")
                return
            if stdout_capture.overflow or stderr_capture.overflow:
                self._fail_protocol(jobs, job_id, "Worker output exceeded its bound")
                return
            if return_code != 0 or stderr_capture.payload:
                jobs.finish(
                    job_id,
                    "failed",
                    error={"code": "worker_crashed", "message": "Managed worker crashed"},
                )
                return
            response = self._decode_worker_response(bytes(stdout_capture.payload))
            if response["ok"] is True:
                jobs.finish(job_id, "succeeded", result=response["result"])
            else:
                jobs.finish(job_id, "failed", error=response["error"])
        except (OSError, subprocess.SubprocessError):
            if process is not None:
                _terminate_and_reap(process, tree)
            jobs.finish(
                job_id,
                "failed",
                error={"code": "worker_crashed", "message": "Managed worker could not run"},
            )
        except Exception:
            if process is not None:
                _terminate_and_reap(process, tree)
            self._fail_protocol(jobs, job_id, "Managed worker returned an invalid result")
        finally:
            if stdout_capture is not None:
                stdout_capture.close()
            if stderr_capture is not None:
                stderr_capture.close()
            tree.close()

    @staticmethod
    def _decode_worker_response(payload: bytes) -> dict[str, Any]:
        try:
            response = decode_ndjson_object(payload)
        except StudioError as exc:
            raise ValueError("worker response is not strict JSON") from exc
        if set(response) == {"ok", "result"} and response["ok"] is True:
            if not isinstance(response["result"], dict):
                raise ValueError("worker result is not an object")
            return response
        if set(response) == {"ok", "error"} and response["ok"] is False:
            error = response["error"]
            if (
                not isinstance(error, dict)
                or set(error) != {"code", "message"}
                or error.get("code") not in {"execution_failed", "worker_protocol"}
                or not isinstance(error.get("message"), str)
                or not 1 <= len(error["message"]) <= 512
            ):
                raise ValueError("worker error is invalid")
            return response
        raise ValueError("worker response shape is invalid")

    @staticmethod
    def _fail_protocol(jobs: JobManager, job_id: str, message: str) -> None:
        jobs.finish(
            job_id,
            "failed",
            error={"code": "worker_protocol", "message": message[:512]},
        )
