#!/usr/bin/env python3
"""Verify canonical multi-genre release lineage and hosted native evidence."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import hmac
import importlib.metadata
import json
import os
import platform
import re
import secrets
import select
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from gamepack_runtime.distribution import RUNTIME_BUNDLE_ROOT
from gamepack_runtime.persistence_io import PersistenceIOError, publish_bytes_noreplace
from scripts.generate_generic_asset_fixtures import build_fixture_documents
from worldforge import __version__ as WORLD_FORGE_VERSION
from worldforge.__main__ import _resolve_generic_assetpack_cli_source
from worldforge.asset_io import (
    AssetContractError,
    open_verified_output_parent,
    read_bound_bytes,
    remove_retained_regular_file_at,
    write_bytes_atomic,
    write_json_atomic,
)
from worldforge.creation_contracts import read_creation_object
from worldforge.file_stat import descriptor_file_stat, file_identity, is_link_or_reparse
from worldforge.game_analysis import analyze_gamepack, serialize_game_analysis
from worldforge.game_boundary import audit_game_repository
from worldforge.game_materialization_bundle import build_game_materialization_bundle
from worldforge.game_package import extract_game_package, package_game, verify_game_package
from worldforge.game_package_extraction import build_game_package_extraction_evidence
from worldforge.game_runtime_bundle import build_game_runtime_bundle
from worldforge.gamepack import (
    build_authoring_capability_ledger,
    compile_game_project,
    load_game_source_project,
    serialize_capability_ledger,
)
from worldforge.generic_asset_processing import (
    build_asset_manifest,
    build_asset_processing_receipt,
    build_asset_qa_report,
    load_asset_processing_recipe,
)
from worldforge.generic_asset_production import (
    load_asset_license_record,
    load_asset_production_receipt,
    load_asset_production_request,
    load_asset_provenance_record,
    load_asset_selection,
)
from worldforge.generic_assetpack import seal_generic_assetpack
from worldforge.generic_assets import (
    load_asset_inventory,
    load_asset_specification,
    load_asset_style,
    load_asset_subject,
    load_asset_target,
)
from worldforge.generic_headless import (
    GenericHeadlessError,
    build_headless_evidence_set,
)
from worldforge.integrity import canonical_json_bytes
from worldforge.multigenre_release_contract import (
    _EXPECTED_TOOLCHAIN,
    _MAX_PROCESS_OUTPUT_BYTES,
    _MAX_RUNTIME_WHEEL_BYTES,
    _PROCESS_TIMEOUT_SECONDS,
    _RUNTIME_AUTHORITY_MARKERS,
    AGGREGATE_FORMAT,
    CASE_ADAPTERS,
    CASES,
    LINEAGE_STAGES,
    REPORT_FORMAT,
    REPORT_VERSION,
    REQUIRED_CASE_STAGES,
    REQUIRED_MATRIX,
    LoadedReleaseReport,
    MultigenreReleaseError,
    _decode_json_object,
    _expected_platform_lock,
    _fail,
    _runtime_artifact_identity,
    _sha256,
    aggregate_release_reports,
    native_untested_evidence,
    require_headless_host,
    require_native_host,
    validate_aggregate_report,
    validate_release_report,
)
from worldforge.persistence_generation import verify_persistence_generation
from worldforge.retained_tree import (
    RetainedTreeCapacityError,
    RetainedTreeError,
    RetainedTreeSnapshot,
    capture_retained_directory_file_census,
    capture_retained_tree,
    verify_retained_tree_snapshot,
)
from worldforge.runtime_implementation import load_runtime_implementation
from worldforge.runtime_platform_lock import load_runtime_platform_lock
from worldforge.runtime_support_authority import (
    RuntimeSupportAuthorityError,
    attach_verified_game_package,
    attach_verified_headless_evidence,
    derive_runtime_evidence,
    derive_runtime_support_report,
    initialize_runtime_support_authority,
    validate_runtime_support_authority_document,
)
from worldforge.standalone_game import materialize_game, verify_standalone_game

__all__ = [
    "AGGREGATE_FORMAT",
    "CASES",
    "CASE_ADAPTERS",
    "LINEAGE_STAGES",
    "REPORT_FORMAT",
    "REPORT_VERSION",
    "REQUIRED_CASE_STAGES",
    "REQUIRED_MATRIX",
    "LoadedReleaseReport",
    "MultigenreReleaseError",
    "_EXPECTED_TOOLCHAIN",
    "_MAX_PROCESS_OUTPUT_BYTES",
    "_MAX_RUNTIME_WHEEL_BYTES",
    "_PROCESS_TIMEOUT_SECONDS",
    "_RUNTIME_AUTHORITY_MARKERS",
    "_decode_json_object",
    "_expected_platform_lock",
    "_fail",
    "_runtime_artifact_identity",
    "_sha256",
    "aggregate_release_reports",
    "native_untested_evidence",
    "require_headless_host",
    "require_native_host",
    "validate_aggregate_report",
    "validate_release_report",
]


@dataclass(frozen=True, slots=True)
class ReleaseInputAuthority:
    snapshot: RetainedTreeSnapshot
    tree_hash: str


@dataclass(frozen=True, slots=True)
class _BoundedProcessResult:
    return_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_overflow: bool
    stderr_overflow: bool


@dataclass(frozen=True, slots=True)
class _NativeSmokeFailure:
    reason_code: str
    evidence_path: str


class _TrustedNativeEvidenceFailure(MultigenreReleaseError):
    """A native failure whose diagnostic output was parent-attested for CI upload."""


@dataclass(frozen=True, slots=True)
class _CaseRunResult:
    report: dict[str, Any]
    native_failure: _NativeSmokeFailure | None


_NATIVE_SMOKE_REPORT_LIMIT = 16 * 1024
_NATIVE_SMOKE_ATTEMPT_LIMIT = 16 * 1024
_NATIVE_SMOKE_ATTEMPT_FORMAT = "world-forge.native_smoke_attempt"
_NATIVE_SMOKE_EVIDENCE_MODE = 0o600
_NATIVE_SMOKE_EVIDENCE_MAX_FILES = 4
_NATIVE_SMOKE_INGRESS_CLEANUP_MAX_FILES = 64
_NATIVE_SMOKE_EVIDENCE_ROOT_PREFIX = "world-forge-native-smoke-evidence-"
_NATIVE_SMOKE_EVIDENCE_ROOT_ATTEMPTS = 100
_PROCESS_TREE_REAP_SECONDS = 1.0
_LINUX_BROKER_READY_TIMEOUT_SECONDS = 10.0
_PROCESS_PIPE_JOIN_SECONDS = 1.0
_CONTAINED_PROCESS_TIMEOUT_CEILING_SECONDS = 6000
_CONTAINED_PROCESS_OUTPUT_CEILING_BYTES = 16 * 1024 * 1024
_LINUX_PROCESS_STAT_LIMIT = 4096
_LINUX_DESCENDANT_MAX_PROCESSES = 4096
_LINUX_PR_SET_CHILD_SUBREAPER = 36
_LINUX_PR_GET_CHILD_SUBREAPER = 37
_LINUX_BROKER_RECORD_LIMIT = 1024
_LINUX_BROKER_SECRET_BYTES = 32
_LINUX_FIXED_POINT_STABLE_SCANS = 2
_WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_CLOSE = 0x00002000
_WINDOWS_PROCESS_START_GATE = b"\x01"
_WINDOWS_PROCESS_BOOTSTRAP = (
    "import subprocess, sys\n"
    "gate = sys.stdin.buffer.read(1)\n"
    "if gate != b'\\x01':\n"
    "    raise SystemExit(125)\n"
    "completed = subprocess.run(sys.argv[1:], stdin=subprocess.DEVNULL, check=False)\n"
    "raise SystemExit(completed.returncode)\n"
)
_LINUX_PROCESS_BROKER_BOOTSTRAP = (
    "import sys\n"
    f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r})\n"
    f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
    "from scripts.verify_multigenre_release import _linux_process_broker\n"
    "raise SystemExit(\n"
    "    _linux_process_broker(\n"
    "        sys.argv[3:], ready_fd=int(sys.argv[1]), secret_fd=int(sys.argv[2])\n"
    "    )\n"
    ")\n"
)
_LINUX_TARGET_BOOTSTRAP = (
    "import os, sys\n"
    "gate = sys.stdin.buffer.read(1)\n"
    "if gate != b'\\x01':\n"
    "    raise SystemExit(125)\n"
    "os.execvpe(sys.argv[1], sys.argv[1:], os.environ)\n"
)


class _DuplicateNativeSmokeReportKey(ValueError):
    """Raised when a native-smoke report repeats any JSON object key."""


@dataclass(frozen=True, slots=True)
class _LinuxProcessState:
    pid: int
    parent_pid: int
    start_time: int


def _linux_process_state(pid: int) -> _LinuxProcessState | None:
    try:
        with open(f"/proc/{pid}/stat", "rb", buffering=0) as source:
            payload = source.read(_LINUX_PROCESS_STAT_LIMIT + 1)
    except (FileNotFoundError, ProcessLookupError):
        return None
    except PermissionError:
        return None
    if len(payload) > _LINUX_PROCESS_STAT_LIMIT:
        raise OSError("Linux process identity exceeded its bound")
    closing = payload.rfind(b")")
    if closing <= 0 or closing + 2 >= len(payload):
        raise OSError("Linux process identity was malformed")
    fields = payload[closing + 2 :].split()
    if len(fields) < 20:
        raise OSError("Linux process identity was incomplete")
    try:
        return _LinuxProcessState(
            pid=pid,
            parent_pid=int(fields[1]),
            start_time=int(fields[19]),
        )
    except ValueError as exc:
        raise OSError("Linux process identity was invalid") from exc


def _iter_linux_process_states() -> Iterator[_LinuxProcessState]:
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                if not entry.name.isascii() or not entry.name.isdecimal():
                    continue
                state = _linux_process_state(int(entry.name))
                if state is not None:
                    yield state
    except OSError:
        raise


class _LinuxBrokerDescendantContainment:
    """Contain and prove empty one broker-owned Linux descendant domain."""

    def __init__(self, libc: Any | None = None) -> None:
        if os.name != "posix" or not sys.platform.startswith("linux"):
            raise OSError("Linux descendant containment is unavailable")
        if libc is None:
            libc = ctypes.CDLL(None, use_errno=True)
        self._prctl = libc.prctl
        self._prctl.restype = ctypes.c_int
        self._previous_state: int | None = None
        self._closed = False
        current = ctypes.c_int()
        if (
            self._prctl(
                _LINUX_PR_GET_CHILD_SUBREAPER,
                ctypes.byref(current),
                0,
                0,
                0,
            )
            != 0
        ):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        if current.value not in {0, 1}:
            raise OSError("Linux descendant containment returned an invalid state")
        self._previous_state = current.value
        if (
            current.value == 0
            and self._prctl(
                _LINUX_PR_SET_CHILD_SUBREAPER,
                1,
                0,
                0,
                0,
            )
            != 0
        ):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        self._parent_pid = os.getpid()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if (
            self._previous_state == 0
            and self._prctl(
                _LINUX_PR_SET_CHILD_SUBREAPER,
                0,
                0,
                0,
                0,
            )
            != 0
        ):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))

    @staticmethod
    def _same_process(pid: int, start_time: int) -> bool:
        state = _linux_process_state(pid)
        return state is not None and state.start_time == start_time

    @staticmethod
    def _signal(pid: int, start_time: int, value: int) -> bool:
        if not _LinuxBrokerDescendantContainment._same_process(pid, start_time):
            return False
        try:
            os.kill(pid, value)
        except ProcessLookupError:
            return False
        except PermissionError as exc:
            raise OSError(errno.EPERM, "Linux descendant could not be signalled") from exc
        return True

    def _discover(
        self,
        tracked: dict[int, int],
        *,
        freeze: bool,
    ) -> tuple[bool, bool]:
        changed = False
        overflow = False
        for pid, start_time in tuple(tracked.items()):
            if not self._same_process(pid, start_time):
                tracked.pop(pid, None)
        for state in _iter_linux_process_states():
            if state.pid == self._parent_pid:
                continue
            if state.pid in tracked and tracked[state.pid] == state.start_time:
                continue
            if state.parent_pid != self._parent_pid and state.parent_pid not in tracked:
                continue
            changed = True
            if len(tracked) >= _LINUX_DESCENDANT_MAX_PROCESSES:
                overflow = True
                self._signal(state.pid, state.start_time, signal.SIGKILL)
                continue
            tracked[state.pid] = state.start_time
            self._signal(
                state.pid,
                state.start_time,
                signal.SIGSTOP if freeze else signal.SIGKILL,
            )
        return changed, overflow

    @staticmethod
    def _reap(pid: int) -> None:
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, ProcessLookupError):
            pass

    def terminate_and_reap(self, process: subprocess.Popen[bytes]) -> int:
        target_state = _linux_process_state(process.pid)
        tracked = {target_state.pid: target_state.start_time} if target_state is not None else {}
        overflow = False
        if target_state is not None and self._same_process(
            target_state.pid, target_state.start_time
        ):
            try:
                os.killpg(target_state.pid, signal.SIGSTOP)
            except ProcessLookupError:
                pass
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise

        freeze_deadline = time.monotonic() + _PROCESS_TREE_REAP_SECONDS
        stable_scans = 0
        while time.monotonic() < freeze_deadline and stable_scans < _LINUX_FIXED_POINT_STABLE_SCANS:
            changed, exceeded = self._discover(tracked, freeze=True)
            overflow = overflow or exceeded
            stable_scans = 0 if changed else stable_scans + 1
            if stable_scans < _LINUX_FIXED_POINT_STABLE_SCANS:
                time.sleep(0.005)
        freeze_fixed_point = stable_scans >= _LINUX_FIXED_POINT_STABLE_SCANS

        if target_state is not None and self._same_process(
            target_state.pid, target_state.start_time
        ):
            try:
                os.killpg(target_state.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    raise
        for pid, start_time in tuple(tracked.items()):
            self._signal(pid, start_time, signal.SIGKILL)

        try:
            return_code = process.wait(timeout=_PROCESS_TREE_REAP_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise OSError("Linux contained target could not be reaped") from exc

        reap_deadline = time.monotonic() + _PROCESS_TREE_REAP_SECONDS
        stable_scans = 0
        while time.monotonic() < reap_deadline and stable_scans < _LINUX_FIXED_POINT_STABLE_SCANS:
            changed, exceeded = self._discover(tracked, freeze=False)
            overflow = overflow or exceeded
            for pid, start_time in tuple(tracked.items()):
                self._reap(pid)
                if not self._same_process(pid, start_time):
                    tracked.pop(pid, None)
            stable_scans = 0 if changed or tracked else stable_scans + 1
            if stable_scans < _LINUX_FIXED_POINT_STABLE_SCANS:
                time.sleep(0.005)
        reap_fixed_point = stable_scans >= _LINUX_FIXED_POINT_STABLE_SCANS
        survivors = [
            pid for pid, start_time in tracked.items() if self._same_process(pid, start_time)
        ]
        if not freeze_fixed_point:
            raise OSError("Linux descendant freeze fixed-point proof timed out")
        if not reap_fixed_point:
            raise OSError("Linux descendant reap fixed-point proof timed out")
        if survivors:
            raise OSError("Linux descendant containment could not reap every process")
        if overflow:
            raise OSError("Linux descendant containment exceeded its process bound")
        return return_code


@dataclass(slots=True)
class _LinuxBrokerAuthority:
    status_fd: int
    secret: bytes
    nonce: str
    broker_pid: int
    broker_start_time: int
    target_pid: int
    target_start_time: int

    def close(self) -> None:
        if self.status_fd >= 0:
            os.close(self.status_fd)
            self.status_fd = -1
        self.secret = b""


def _linux_broker_payload(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise OSError("Linux broker record was invalid") from exc


def _linux_broker_nonce(secret: bytes) -> str:
    if type(secret) is not bytes or len(secret) != _LINUX_BROKER_SECRET_BYTES:
        raise OSError("Linux broker secret was invalid")
    return hashlib.sha256(b"world-forge-linux-broker\0" + secret).hexdigest()[:32]


def _linux_broker_record(
    payload: Mapping[str, object],
    *,
    secret: bytes,
) -> bytes:
    _linux_broker_nonce(secret)
    canonical_payload = _linux_broker_payload(payload)
    signature = hmac.new(secret, canonical_payload, hashlib.sha256).hexdigest()
    encoded = (
        json.dumps(
            {"payload": payload, "signature": signature},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    if len(encoded) > _LINUX_BROKER_RECORD_LIMIT:
        raise OSError("Linux broker record exceeded its bound")
    return encoded


def _write_linux_broker_record(
    descriptor: int,
    payload: Mapping[str, object],
    *,
    secret: bytes,
) -> None:
    encoded = _linux_broker_record(payload, secret=secret)
    if os.write(descriptor, encoded) != len(encoded):
        raise OSError("Linux broker record write was incomplete")


def _read_linux_broker_record(
    descriptor: int,
    *,
    timeout: float,
    secret: bytes,
) -> dict[str, Any]:
    _linux_broker_nonce(secret)
    deadline = time.monotonic() + timeout
    encoded = bytearray()
    while b"\n" not in encoded:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OSError("Linux broker record timed out")
        readable, _writable, _exceptional = select.select((descriptor,), (), (), remaining)
        if not readable:
            raise OSError("Linux broker record timed out")
        chunk = os.read(descriptor, _LINUX_BROKER_RECORD_LIMIT + 1 - len(encoded))
        if not chunk:
            raise OSError("Linux broker record was missing")
        encoded.extend(chunk)
        if len(encoded) > _LINUX_BROKER_RECORD_LIMIT:
            raise OSError("Linux broker record exceeded its bound")
    if encoded.count(b"\n") != 1 or not encoded.endswith(b"\n"):
        raise OSError("Linux broker record framing was invalid")
    try:
        document = json.loads(
            encoded,
            object_pairs_hook=_reject_native_smoke_duplicate_keys,
        )
    except (RecursionError, UnicodeError, ValueError) as exc:
        raise OSError("Linux broker record was invalid") from exc
    if (
        type(document) is not dict
        or set(document) != {"payload", "signature"}
        or type(document.get("payload")) is not dict
        or type(document.get("signature")) is not str
        or re.fullmatch(r"[0-9a-f]{64}", document["signature"]) is None
    ):
        raise OSError("Linux broker record was invalid")
    canonical_wrapper = _linux_broker_payload(document) + b"\n"
    if canonical_wrapper != bytes(encoded):
        raise OSError("Linux broker record was noncanonical")
    broker_payload = document["payload"]
    expected_signature = hmac.new(
        secret,
        _linux_broker_payload(broker_payload),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(document["signature"], expected_signature):
        raise OSError("Linux broker record signature was invalid")
    return broker_payload


def _relay_linux_broker_stream(source: Any, destination: Any) -> None:
    try:
        read = getattr(source, "read1", source.read)
        while chunk := read(65536):
            destination.write(chunk)
            destination.flush()
    finally:
        source.close()


def _linux_process_broker(
    arguments: Sequence[str],
    *,
    ready_fd: int,
    secret_fd: int,
) -> int:
    """Contain one command and attest domain emptiness over an exclusive pipe."""

    if (
        not arguments
        or any(type(argument) is not str for argument in arguments)
        or type(ready_fd) is not int
        or ready_fd < 3
        or type(secret_fd) is not int
        or secret_fd < 3
        or secret_fd == ready_fd
    ):
        return 70
    secret = bytearray()
    try:
        while len(secret) < _LINUX_BROKER_SECRET_BYTES:
            chunk = os.read(secret_fd, _LINUX_BROKER_SECRET_BYTES - len(secret))
            if not chunk:
                return 70
            secret.extend(chunk)
        if os.read(secret_fd, 1):
            return 70
    except OSError:
        return 70
    finally:
        try:
            os.close(secret_fd)
        except OSError:
            pass
    secret_bytes = bytes(secret)
    try:
        nonce = _linux_broker_nonce(secret_bytes)
    except OSError:
        return 70
    try:
        containment = _LinuxBrokerDescendantContainment()
    except OSError:
        return 70
    termination_requested = False

    def request_termination(_signum: int, _frame: Any) -> None:
        nonlocal termination_requested
        termination_requested = True

    signal.signal(signal.SIGTERM, request_termination)
    process: subprocess.Popen[bytes] | None = None
    relays: tuple[threading.Thread, threading.Thread] = ()
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", _LINUX_TARGET_BOOTSTRAP, *arguments],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
        target_state = _linux_process_state(process.pid)
        broker_state = _linux_process_state(os.getpid())
        if (
            target_state is None
            or broker_state is None
            or process.stdin is None
            or process.stdout is None
            or process.stderr is None
        ):
            raise OSError("Linux broker process identities were unavailable")
        relays = (
            threading.Thread(
                target=_relay_linux_broker_stream,
                args=(process.stdout, sys.stdout.buffer),
                daemon=True,
            ),
            threading.Thread(
                target=_relay_linux_broker_stream,
                args=(process.stderr, sys.stderr.buffer),
                daemon=True,
            ),
        )
        for relay in relays:
            relay.start()
        _write_linux_broker_record(
            ready_fd,
            {
                "broker_pid": broker_state.pid,
                "broker_start_time": broker_state.start_time,
                "event": "target_ready",
                "nonce": nonce,
                "target_pid": target_state.pid,
                "target_start_time": target_state.start_time,
            },
            secret=secret_bytes,
        )
        gate = sys.stdin.buffer.read(1)
        if gate != _WINDOWS_PROCESS_START_GATE:
            raise OSError("Linux broker release gate was unavailable")
        process.stdin.write(_WINDOWS_PROCESS_START_GATE)
        process.stdin.flush()
        process.stdin.close()
        while process.poll() is None and not termination_requested:
            time.sleep(0.005)
        return_code = containment.terminate_and_reap(process)
        relay_deadline = time.monotonic() + _PROCESS_PIPE_JOIN_SECONDS
        for relay in relays:
            relay.join(timeout=max(0.0, relay_deadline - time.monotonic()))
        if any(relay.is_alive() for relay in relays):
            raise OSError("Linux broker output relays did not terminate")
        normalized = min(255, 128 + abs(return_code)) if return_code < 0 else min(255, return_code)
        _write_linux_broker_record(
            ready_fd,
            {
                "broker_pid": broker_state.pid,
                "domain_empty": True,
                "event": "domain_empty",
                "nonce": nonce,
                "return_code": normalized,
                "target_pid": target_state.pid,
                "target_start_time": target_state.start_time,
            },
            secret=secret_bytes,
        )
        return normalized
    except (OSError, subprocess.SubprocessError, ValueError):
        if process is not None:
            try:
                containment.terminate_and_reap(process)
            except (OSError, subprocess.SubprocessError):
                pass
        return 70
    finally:
        try:
            containment.close()
        except OSError:
            pass
        try:
            os.close(ready_fd)
        except OSError:
            pass


def _validate_linux_broker_ready(
    record: Mapping[str, object],
    *,
    nonce: str,
    broker_state: _LinuxProcessState,
) -> tuple[int, int]:
    if set(record) != {
        "broker_pid",
        "broker_start_time",
        "event",
        "nonce",
        "target_pid",
        "target_start_time",
    } or record != {
        "broker_pid": broker_state.pid,
        "broker_start_time": broker_state.start_time,
        "event": "target_ready",
        "nonce": nonce,
        "target_pid": record.get("target_pid"),
        "target_start_time": record.get("target_start_time"),
    }:
        raise OSError("Linux broker ready record was invalid")
    target_pid = record["target_pid"]
    target_start_time = record["target_start_time"]
    if (
        type(target_pid) is not int
        or target_pid <= 0
        or type(target_start_time) is not int
        or target_start_time <= 0
    ):
        raise OSError("Linux broker target identity was invalid")
    state = _linux_process_state(target_pid)
    if state is None or state.start_time != target_start_time:
        raise OSError("Linux broker target identity changed before release")
    return target_pid, target_start_time


def _validate_linux_broker_complete(
    record: Mapping[str, object],
    *,
    authority: _LinuxBrokerAuthority,
    return_code: int,
) -> None:
    if type(return_code) is not int or not 0 <= return_code <= 255:
        raise OSError("Linux broker return code was invalid")
    expected = {
        "broker_pid": authority.broker_pid,
        "domain_empty": True,
        "event": "domain_empty",
        "nonce": authority.nonce,
        "return_code": return_code,
        "target_pid": authority.target_pid,
        "target_start_time": authority.target_start_time,
    }
    if record != expected:
        raise OSError("Linux broker domain-empty record was invalid")


def _terminate_linux_broker(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.kill(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _start_linux_contained_process(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    popen: Any = subprocess.Popen,
) -> tuple[subprocess.Popen[bytes], _LinuxBrokerAuthority]:
    status_reader, status_writer = os.pipe()
    secret_reader, secret_writer = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    authority: _LinuxBrokerAuthority | None = None
    secret = secrets.token_bytes(_LINUX_BROKER_SECRET_BYTES)
    try:
        nonce = _linux_broker_nonce(secret)
        process = popen(
            [
                sys.executable,
                "-I",
                "-c",
                _LINUX_PROCESS_BROKER_BOOTSTRAP,
                str(status_writer),
                str(secret_reader),
                *arguments,
            ],
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
            pass_fds=(status_writer, secret_reader),
        )
        broker_state = _linux_process_state(process.pid)
        if broker_state is None:
            raise OSError("Linux broker identity was unavailable")
        os.close(status_writer)
        status_writer = -1
        os.close(secret_reader)
        secret_reader = -1
        if os.write(secret_writer, secret) != len(secret):
            raise OSError("Linux broker secret write was incomplete")
        os.close(secret_writer)
        secret_writer = -1
        if process.stdin is None:
            raise OSError("Linux broker release gate was unavailable")
        ready = _read_linux_broker_record(
            status_reader,
            timeout=_LINUX_BROKER_READY_TIMEOUT_SECONDS,
            secret=secret,
        )
        target_pid, target_start_time = _validate_linux_broker_ready(
            ready,
            nonce=nonce,
            broker_state=broker_state,
        )
        authority = _LinuxBrokerAuthority(
            status_fd=status_reader,
            secret=secret,
            nonce=nonce,
            broker_pid=broker_state.pid,
            broker_start_time=broker_state.start_time,
            target_pid=target_pid,
            target_start_time=target_start_time,
        )
        status_reader = -1
        process.stdin.write(_WINDOWS_PROCESS_START_GATE)
        process.stdin.flush()
        process.stdin.close()
        return process, authority
    except BaseException as exc:
        if authority is not None:
            try:
                authority.close()
            except OSError:
                pass
            authority = None
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process is not None:
            _terminate_linux_broker(process)
            try:
                process.wait(timeout=_PROCESS_TREE_REAP_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
                try:
                    process.wait(timeout=_PROCESS_TREE_REAP_SECONDS)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            for stream in (process.stdout, process.stderr):
                close = getattr(stream, "close", None)
                if close is not None:
                    try:
                        close()
                    except OSError:
                        pass
        if isinstance(exc, MultigenreReleaseError):
            raise
        _fail(
            "subprocess_containment_lost",
            f"Linux broker did not establish containment: {exc}",
        )
    finally:
        if status_reader >= 0:
            os.close(status_reader)
        if status_writer >= 0:
            os.close(status_writer)
        if secret_reader >= 0:
            os.close(secret_reader)
        if secret_writer >= 0:
            os.close(secret_writer)


class _WindowsJobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _WindowsJobObjectIoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _WindowsJobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _WindowsJobObjectBasicLimitInformation),
        ("IoInfo", _WindowsJobObjectIoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsKillOnCloseJob:
    def __init__(self, kernel32: Any | None = None) -> None:
        if kernel32 is None:
            win_dll = getattr(ctypes, "WinDLL", None)
            if os.name != "nt" or win_dll is None:
                raise OSError("Windows process containment is unavailable")
            kernel32 = win_dll("kernel32", use_last_error=True)
        self._create = kernel32.CreateJobObjectW
        self._create.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._create.restype = ctypes.c_void_p
        self._configure = kernel32.SetInformationJobObject
        self._configure.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._configure.restype = ctypes.c_int
        self._assign = kernel32.AssignProcessToJobObject
        self._assign.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._assign.restype = ctypes.c_int
        self._terminate = kernel32.TerminateJobObject
        self._terminate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._terminate.restype = ctypes.c_int
        self._close = kernel32.CloseHandle
        self._close.argtypes = [ctypes.c_void_p]
        self._close.restype = ctypes.c_int

        created = self._create(None, None)
        handle = created if isinstance(created, int) else getattr(created, "value", None)
        if not handle:
            raise OSError("Windows process containment could not be created")
        self.handle: int | None = int(handle)
        information = _WindowsJobObjectExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = _WINDOWS_JOB_OBJECT_LIMIT_KILL_ON_CLOSE
        if not self._configure(
            ctypes.c_void_p(self.handle),
            _WINDOWS_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            close_succeeded = bool(self._close(ctypes.c_void_p(self.handle)))
            self.handle = None
            detail = " and could not be closed" if not close_succeeded else ""
            raise OSError(f"Windows process containment could not be configured{detail}")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if self.handle is None:
            raise OSError("Windows process containment is already closed")
        process_handle = getattr(process, "_handle", None)
        if process_handle is None or not self._assign(
            ctypes.c_void_p(self.handle),
            ctypes.c_void_p(int(process_handle)),
        ):
            raise OSError("Windows process could not enter containment")

    def terminate_and_close(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        self.handle = None
        terminated = bool(self._terminate(ctypes.c_void_p(handle), 1))
        closed = bool(self._close(ctypes.c_void_p(handle)))
        if not terminated or not closed:
            raise OSError("Windows process containment cleanup failed")


def _start_windows_contained_process(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    kernel32: Any | None = None,
    popen: Any = subprocess.Popen,
) -> tuple[subprocess.Popen[bytes], _WindowsKillOnCloseJob]:
    job = _WindowsKillOnCloseJob(kernel32)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = popen(
            [
                sys.executable,
                "-I",
                "-c",
                _WINDOWS_PROCESS_BOOTSTRAP,
                *arguments,
            ],
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise OSError("Windows process containment pipes were unavailable")
        job.assign(process)
        process.stdin.write(_WINDOWS_PROCESS_START_GATE)
        process.stdin.flush()
        process.stdin.close()
        return process, job
    except BaseException:
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            job.terminate_and_close()
        except OSError:
            pass
        if process is not None:
            try:
                process.wait(timeout=_PROCESS_TREE_REAP_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                pass
        raise


def load_release_report(path: Path) -> LoadedReleaseReport:
    """Retain, strictly decode, and hash one exact canonical report file."""

    source = path.absolute()
    try:
        payload = read_bound_bytes(source, limit=_MAX_PROCESS_OUTPUT_BYTES).payload
    except (AssetContractError, OSError) as exc:
        _fail("release_report_read_failed", str(exc))
    document = _decode_json_object(payload, source=source)
    if payload != canonical_json_bytes(document):
        _fail("release_report_encoding_invalid", f"{source}: report is not canonical JSON")
    return LoadedReleaseReport(
        document=document,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def publish_operational_report(
    path: Path,
    report: Mapping[str, object],
    *,
    source_root: Path,
) -> None:
    """Publish one canonical report exclusively outside the source repository."""

    destination = path.absolute()
    repository = source_root.resolve(strict=True)
    if _inside(destination, repository):
        _fail("release_output_inside_repository", "report must be external")
    if os.path.lexists(destination):
        _fail("release_report_output_exists", "refusing to replace an existing report")
    destination.parent.mkdir(parents=True, exist_ok=True)
    resolved_destination = destination.parent.resolve(strict=True) / destination.name
    if _inside(resolved_destination, repository):
        _fail("release_output_inside_repository", "report must resolve outside the repository")
    try:
        write_json_atomic(destination, report, durable_parent=True)
    except AssetContractError as exc:
        reason = (
            "release_report_output_exists"
            if "overwrite" in str(exc).casefold() or "exist" in str(exc).casefold()
            else "release_report_publish_failed"
        )
        _fail(reason, str(exc))


def _host_context() -> dict[str, str]:
    if os.name == "nt":
        os_name = "windows"
    elif sys.platform.startswith("linux"):
        os_name = "linux"
    else:
        os_name = sys.platform.casefold()
    raw_machine = platform.machine().casefold()
    architecture = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "x86_64": "x86_64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(raw_machine, raw_machine or "unknown")
    return {
        "architecture": architecture,
        "os": os_name,
        "platform_id": f"platform:{os_name}_{architecture}",
        "python_abi": f"cp{sys.version_info.major}{sys.version_info.minor}",
        "python_implementation": platform.python_implementation().casefold(),
        "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
        "runner_image": os.environ.get("WORLD_FORGE_RUNNER_IMAGE", "local"),
    }


def _sanitized_release_child_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    excluded_names = {"PYTHONHOME", "PYTHONPATH"}
    excluded_prefixes = ("ACTIONS_", "GITHUB_", "RUNNER_")
    return {
        key: value
        for key, value in environment.items()
        if key.upper() not in excluded_names and not key.upper().startswith(excluded_prefixes)
    }


def _single_line_absolute_path(value: object, *, label: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or len(value.splitlines()) != 1
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail(
            "native_smoke_evidence_output_invalid",
            f"{label} must be one bounded path line",
        )
    path = Path(value)
    if not path.is_absolute():
        _fail(
            "native_smoke_evidence_output_invalid",
            f"{label} must be absolute",
        )
    return path


def _native_smoke_ci_publication_context(
    environment: Mapping[str, str],
    *,
    native_mode: str,
) -> tuple[Path | None, Path | None]:
    if native_mode == "off" or environment.get("GITHUB_ACTIONS") != "true":
        return None, None
    raw_parent = environment.get("RUNNER_TEMP")
    raw_output = environment.get("GITHUB_OUTPUT")
    if raw_parent is None or raw_output is None:
        _fail(
            "native_smoke_evidence_output_missing",
            "GitHub native evidence output paths are unavailable",
        )
    parent = _single_line_absolute_path(raw_parent, label="RUNNER_TEMP")
    output = _single_line_absolute_path(raw_output, label="GITHUB_OUTPUT")
    try:
        parent_info = parent.lstat()
        output_info = output.lstat()
        if is_link_or_reparse(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
            raise AssetContractError("RUNNER_TEMP is unsafe")
        if (
            is_link_or_reparse(output_info)
            or not stat.S_ISREG(output_info.st_mode)
            or output_info.st_nlink != 1
            or output_info.st_size != 0
        ):
            raise AssetContractError("GITHUB_OUTPUT is unsafe")
        parent = parent.resolve(strict=True)
        output = output.absolute()
    except (AssetContractError, OSError) as exc:
        _fail(
            "native_smoke_evidence_output_invalid",
            f"GitHub native evidence output paths are unavailable: {exc}",
        )
    return parent, output


def _source_context(root: Path) -> dict[str, str]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    state = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {"revision": revision, "tree_state": "dirty" if state else "clean"}


def verify_runtime_wheel(
    path: Path,
    selected_lock: Mapping[str, object],
) -> dict[str, object]:
    """Measure one standalone wheel and require the exact platform-lock artifact."""

    expected = _runtime_artifact_identity(selected_lock)
    source = path.absolute()
    try:
        retained = read_bound_bytes(source, limit=_MAX_RUNTIME_WHEEL_BYTES)
    except (AssetContractError, OSError) as exc:
        _fail("native_runtime_artifact_mismatch", str(exc))
    measured = {
        "filename": source.name,
        "platform_lock_hash": selected_lock.get("content_hash"),
        "platform_lock_id": selected_lock.get("lock_id"),
        "sha256": hashlib.sha256(retained.payload).hexdigest(),
        "size_bytes": len(retained.payload),
    }
    if measured != expected:
        _fail(
            "native_runtime_artifact_mismatch",
            "runtime wheel bytes differ from the selected platform lock",
        )
    return measured


def _toolchain_context(
    runtime_artifact: Mapping[str, object] | None = None,
) -> dict[str, object]:
    def installed_version(distribution: str) -> str:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    return {
        "pillow": installed_version("pillow"),
        "python": platform.python_version(),
        "raylib": installed_version("raylib"),
        "raylib_artifact": None if runtime_artifact is None else dict(runtime_artifact),
        "world_forge": WORLD_FORGE_VERSION,
    }


def _passed_stage(stage: str) -> dict[str, object]:
    return {"reason_code": None, "stage": stage, "state": "passed"}


def _retained_tree_hash(captured: RetainedTreeSnapshot) -> str:
    directories = captured.directories
    files = captured.files
    identity = {
        "directories": list(directories),
        "files": [
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            for relative, payload in sorted(files.items())
        ],
    }
    return hashlib.sha256(canonical_json_bytes(identity)).hexdigest()


def _publish_retained_payload(
    *,
    directories: Sequence[str],
    files: Mapping[str, bytes],
    destination: Path,
) -> None:
    expected_directories = tuple(directories)
    expected_files = dict(files)
    try:
        destination.mkdir(parents=True, exist_ok=False)
        for relative in expected_directories:
            if relative in {"", "."}:
                continue
            path = PurePosixPath(relative)
            destination.joinpath(*path.parts).mkdir(exist_ok=False)
        for relative, payload in sorted(expected_files.items()):
            path = PurePosixPath(relative)
            write_bytes_atomic(destination.joinpath(*path.parts), payload)
        copied = capture_retained_tree(destination)
    except (AssetContractError, OSError, RetainedTreeError, ValueError) as exc:
        _fail("release_source_tree_invalid", str(exc))
    if copied.directories != expected_directories or copied.files != expected_files:
        _fail("release_source_tree_invalid", "retained source bytes changed during copy")


def _publish_captured_tree(captured: RetainedTreeSnapshot, destination: Path) -> None:
    _publish_retained_payload(
        directories=captured.directories,
        files=captured.files,
        destination=destination,
    )


def copy_release_source_tree(source: Path, destination: Path) -> None:
    """Copy one retained, link-free source tree into a new external root."""

    try:
        captured = capture_retained_tree(source)
    except (OSError, RetainedTreeError) as exc:
        _fail("release_source_tree_invalid", str(exc))
    _publish_captured_tree(captured, destination)


def capture_release_inputs(source_root: Path) -> ReleaseInputAuthority:
    """Capture the complete fixture closure once as the release input authority."""

    source = source_root / "examples" / "multigenre-contracts"
    try:
        captured = capture_retained_tree(source)
    except (OSError, RetainedTreeError) as exc:
        _fail("release_source_tree_invalid", str(exc))
    return ReleaseInputAuthority(
        snapshot=captured,
        tree_hash=_retained_tree_hash(captured),
    )


def materialize_release_input_subtree(
    authority: ReleaseInputAuthority,
    relative: str,
    destination: Path,
) -> None:
    """Materialize one subtree directly from the retained release-authority bytes."""

    if type(authority) is not ReleaseInputAuthority or type(relative) is not str:
        _fail("release_source_tree_invalid", "release input authority is invalid")
    subtree = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or subtree.is_absolute()
        or subtree.as_posix() != relative
        or any(part in {"", ".", ".."} for part in subtree.parts)
    ):
        _fail("release_source_tree_invalid", "release input subtree is invalid")
    prefix = f"{relative}/"
    if relative not in authority.snapshot.directories:
        _fail("release_source_tree_invalid", f"release input subtree is missing: {relative}")
    directories = tuple(
        "" if item == relative else item.removeprefix(prefix)
        for item in authority.snapshot.directories
        if item == relative or item.startswith(prefix)
    )
    files = {
        item.removeprefix(prefix): payload
        for item, payload in authority.snapshot.files.items()
        if item.startswith(prefix)
    }
    _publish_retained_payload(
        directories=directories,
        files=files,
        destination=destination,
    )


def _run_bounded_subprocess(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float = _PROCESS_TIMEOUT_SECONDS,
    output_limit: int = _MAX_PROCESS_OUTPUT_BYTES,
) -> _BoundedProcessResult:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < timeout_seconds <= _PROCESS_TIMEOUT_SECONDS
        or isinstance(output_limit, bool)
        or not isinstance(output_limit, int)
        or not 1 <= output_limit <= _MAX_PROCESS_OUTPUT_BYTES
    ):
        raise ValueError("subprocess bounds are invalid")
    return _run_bounded_subprocess_execution(
        arguments,
        cwd=cwd,
        environment=environment,
        timeout_seconds=timeout_seconds,
        output_limit=output_limit,
    )


def _run_bounded_subprocess_execution(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    output_limit: int,
) -> _BoundedProcessResult:
    """Run one command inside the reviewed cross-platform descendant authority."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < timeout_seconds <= _CONTAINED_PROCESS_TIMEOUT_CEILING_SECONDS
        or isinstance(output_limit, bool)
        or not isinstance(output_limit, int)
        or not 1 <= output_limit <= _CONTAINED_PROCESS_OUTPUT_CEILING_BYTES
    ):
        raise ValueError("contained subprocess bounds are invalid")
    windows_job: _WindowsKillOnCloseJob | None = None
    linux_authority: _LinuxBrokerAuthority | None = None
    try:
        if os.name == "nt":
            process, windows_job = _start_windows_contained_process(
                arguments,
                cwd=cwd,
                environment=environment,
            )
        elif os.name == "posix" and sys.platform.startswith("linux"):
            process, linux_authority = _start_linux_contained_process(
                arguments,
                cwd=cwd,
                environment=environment,
            )
        else:
            _fail(
                "standalone_execution_failed",
                "safe subprocess containment is unavailable",
            )
    except MultigenreReleaseError:
        raise
    except (OSError, ValueError) as exc:
        _fail("standalone_execution_failed", f"subprocess containment failed: {exc}")
    if process.stdout is None or process.stderr is None:
        if windows_job is not None:
            try:
                windows_job.terminate_and_close()
            except OSError:
                pass
        elif linux_authority is not None:
            _terminate_linux_broker(process)
            linux_authority.close()
        if process.returncode is None:
            try:
                process.wait(timeout=_PROCESS_TREE_REAP_SECONDS)
            except (OSError, subprocess.TimeoutExpired):
                pass
        _fail("standalone_execution_failed", "subprocess pipes were unavailable")

    stdout = bytearray()
    stderr = bytearray()
    stdout_overflow = threading.Event()
    stderr_overflow = threading.Event()
    reader_errors: list[BaseException] = []

    def read_bounded(
        stream: Any,
        target: bytearray,
        overflow: threading.Event,
    ) -> None:
        try:
            read = getattr(stream, "read1", stream.read)
            while chunk := read(65536):
                remaining = output_limit - len(target)
                target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    continue
        except BaseException as exc:
            reader_errors.append(exc)
        finally:
            try:
                stream.close()
            except OSError:
                pass

    readers = (
        threading.Thread(
            target=read_bounded,
            args=(process.stdout, stdout, stdout_overflow),
            daemon=True,
        ),
        threading.Thread(
            target=read_bounded,
            args=(process.stderr, stderr, stderr_overflow),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + float(timeout_seconds)
    timed_out = False
    while process.poll() is None:
        if stdout_overflow.is_set() or stderr_overflow.is_set():
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            process.wait(timeout=min(0.01, remaining))
        except subprocess.TimeoutExpired:
            pass
    containment_error: OSError | subprocess.TimeoutExpired | None = None
    containment_lost: str | None = None
    return_code: int | None = None
    if windows_job is not None:
        try:
            windows_job.terminate_and_close()
        except OSError as exc:
            containment_error = exc
    elif linux_authority is not None:
        if process.poll() is None:
            _terminate_linux_broker(process)
        try:
            return_code = process.wait(timeout=_PROCESS_TREE_REAP_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                return_code = process.wait(timeout=_PROCESS_TREE_REAP_SECONDS)
            except subprocess.TimeoutExpired:
                return_code = process.returncode if process.returncode is not None else -1
            containment_lost = "Linux broker did not terminate after cleanup request"
        try:
            complete = _read_linux_broker_record(
                linux_authority.status_fd,
                timeout=_PROCESS_TREE_REAP_SECONDS,
                secret=linux_authority.secret,
            )
            _validate_linux_broker_complete(
                complete,
                authority=linux_authority,
                return_code=return_code,
            )
        except OSError as exc:
            containment_lost = containment_lost or str(exc)
        finally:
            linux_authority.close()
    if return_code is None:
        try:
            return_code = process.wait(timeout=_PROCESS_TREE_REAP_SECONDS)
        except subprocess.TimeoutExpired as exc:
            containment_error = containment_error or exc
            try:
                return_code = process.wait(timeout=_PROCESS_TREE_REAP_SECONDS)
            except subprocess.TimeoutExpired:
                return_code = process.returncode if process.returncode is not None else -1
    reader_deadline = time.monotonic() + _PROCESS_PIPE_JOIN_SECONDS
    for reader in readers:
        reader.join(timeout=max(0.0, reader_deadline - time.monotonic()))
    if any(reader.is_alive() for reader in readers):
        if linux_authority is not None:
            containment_lost = containment_lost or "Linux broker output pipes remained open"
        else:
            _fail("standalone_execution_failed", "subprocess output capture did not terminate")
    if containment_lost is not None:
        _fail("subprocess_containment_lost", containment_lost)
    if containment_error is not None:
        _fail(
            "standalone_execution_failed",
            f"subprocess containment cleanup failed: {containment_error}",
        )
    if reader_errors:
        _fail("standalone_execution_failed", "subprocess output capture failed")
    return _BoundedProcessResult(
        return_code=return_code,
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        timed_out=timed_out,
        stdout_overflow=stdout_overflow.is_set(),
        stderr_overflow=stderr_overflow.is_set(),
    )


def _checked_subprocess_json(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float = _PROCESS_TIMEOUT_SECONDS,
    output_limit: int = _MAX_PROCESS_OUTPUT_BYTES,
) -> dict[str, Any]:
    result = _run_bounded_subprocess(
        arguments,
        cwd=cwd,
        environment=environment,
        timeout_seconds=timeout_seconds,
        output_limit=output_limit,
    )
    if result.timed_out:
        _fail("standalone_execution_timeout", "standalone subprocess exceeded its deadline")
    if result.stdout_overflow or result.stderr_overflow:
        _fail("standalone_output_too_large", "standalone subprocess output exceeded its bound")
    if result.return_code != 0:
        _fail(
            "standalone_execution_failed",
            f"{Path(arguments[2]).name if len(arguments) > 2 else 'command'}: "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}",
        )
    return _decode_json_object(
        result.stdout,
        source=Path(arguments[2]).name if len(arguments) > 2 else "command",
        reason_code="standalone_execution_invalid",
    )


def _validate_independent_verifier(
    report: object,
    *,
    game_root: Path,
    standalone_manifest: Mapping[str, object],
    runtime_bundle_hash: str,
) -> None:
    fields = {
        "authoring_dependencies",
        "files",
        "game_id",
        "manifest_hash",
        "payload_lock_hash",
        "payload_tree_hash",
        "root",
        "runtime_ai_capabilities",
        "runtime_bundle_hash",
        "status",
    }
    lock = read_creation_object(game_root / "game.lock.json")
    if (
        type(report) is not dict
        or set(report) != fields
        or report.get("status") != "verified"
        or Path(str(report.get("root"))).resolve(strict=True) != game_root.resolve(strict=True)
        or report.get("game_id") != standalone_manifest["game_id"]
        or report.get("manifest_hash") != standalone_manifest["content_hash"]
        or report.get("payload_lock_hash") != lock["content_hash"]
        or report.get("payload_tree_hash") != lock["tree_hash"]
        or report.get("runtime_bundle_hash") != runtime_bundle_hash
        or report.get("authoring_dependencies") != 0
        or report.get("runtime_ai_capabilities") != 0
        or type(report.get("files")) is not int
        or not 1 <= report["files"] <= 4096
    ):
        _fail("standalone_execution_invalid", "independent verifier output is not exact")


def _validate_headless_record(
    report: object,
    *,
    scenario: Mapping[str, object],
    slot: str,
    runtime_bundle_hash: str,
) -> None:
    fields = {
        "native_execution",
        "replay_slot",
        "runtime_bundle_hash",
        "save_slot",
        "scenarios",
        "status",
    }
    expected_scenario = {
        "action_count": len(scenario["actions"]),
        "classification": scenario["expected_classification"],
        "final_state_hash": scenario["expected_final_state_hash"],
        "scenario_id": scenario["scenario_id"],
    }
    if (
        type(report) is not dict
        or set(report) != fields
        or report.get("status") != "passed"
        or report.get("native_execution") is not False
        or report.get("runtime_bundle_hash") != runtime_bundle_hash
        or report.get("save_slot") != slot
        or report.get("replay_slot") != slot
        or report.get("scenarios") != [expected_scenario]
    ):
        _fail("standalone_execution_invalid", f"{slot}: headless output is not exact")


def _validate_replay_report(
    report: object,
    *,
    scenario: Mapping[str, object],
    ending: str,
) -> None:
    if (
        type(report) is not dict
        or set(report) != {"classification", "state_hash", "status"}
        or report.get("status") != "replay_complete"
        or report.get("state_hash") != scenario["expected_final_state_hash"]
        or report.get("classification") != {"ending_ids": [ending], "terminal": True}
    ):
        _fail("standalone_execution_invalid", f"{ending}: replay output is not exact")


def _validate_save_restore_report(
    report: object,
    *,
    scenario: Mapping[str, object],
    ending: str,
) -> None:
    if (
        type(report) is not dict
        or set(report) != {"classification", "state_hash", "status"}
        or report.get("status") != "save_restored"
        or report.get("state_hash") != scenario["expected_final_state_hash"]
        or report.get("classification") != {"ending_ids": [ending], "terminal": True}
    ):
        _fail("standalone_execution_invalid", f"{ending}: save restore output is not exact")


def _validate_persistence_report(report: object, *, kind: str, slot: str) -> None:
    fields = {
        "content_hash",
        "format",
        "format_version",
        "kind",
        "operation",
        "payload_hash",
        "sequence",
        "slot",
        "status",
    }
    if (
        type(report) is not dict
        or set(report) != fields
        or report.get("format") != "world-forge.persistence_generation"
        or report.get("format_version") != 1
        or report.get("kind") != kind
        or report.get("operation") != "write"
        or report.get("slot") != slot
        or report.get("status") != "verified"
        or type(report.get("sequence")) is not int
        or report["sequence"] < 0
        or not _sha256(report.get("content_hash"))
        or not _sha256(report.get("payload_hash"))
    ):
        _fail("persistence_output_invalid", f"{slot}: {kind} generation is not exact")


def _validate_native_smoke_ingress(
    case_ingress: Path,
    *,
    expected_identity: tuple[int, int],
) -> None:
    try:
        census = capture_retained_directory_file_census(
            case_ingress,
            maximum_entries=1,
            authority_root=case_ingress,
            expected_authority_identity=expected_identity,
        )
    except RetainedTreeCapacityError:
        _fail(
            "native_smoke_ingress_unexpected",
            "native smoke ingress contains an unexpected entry",
        )
    except (RetainedTreeError, OSError):
        _fail(
            "native_smoke_ingress_unsafe",
            "native smoke ingress contains an unsafe entry",
        )
    if census.names != ("report.json",):
        _fail(
            "native_smoke_ingress_unexpected",
            "native smoke ingress inventory is not exact",
        )


def _remove_native_smoke_ingress_file(parent: Any, name: str) -> None:
    if parent.parent_fd is not None:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent.parent_fd,
            )
            retained = descriptor_file_stat(descriptor)
            named = os.stat(name, dir_fd=parent.parent_fd, follow_symlinks=False)
            if (
                is_link_or_reparse(retained)
                or is_link_or_reparse(named)
                or not stat.S_ISREG(retained.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or retained.st_nlink != 1
                or named.st_nlink != 1
                or file_identity(retained) != file_identity(named)
            ):
                raise AssetContractError("native smoke ingress file identity is unsafe")
            remove_retained_regular_file_at(
                parent.parent_fd,
                name,
                descriptor,
                expected_identity=file_identity(retained),
            )
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return
    if parent.windows_api is None or parent.windows_parent_handle is None:
        raise AssetContractError("secure ingress cleanup primitives are unavailable")
    handle = parent.windows_api.open_existing_file_strict(
        parent.windows_parent_handle,
        name,
        delete=True,
    )
    try:
        parent.windows_api.strict_entry_info(
            handle,
            context=f"native smoke ingress file {name}",
        )
        parent.windows_api.dispose_ex(handle)
    finally:
        parent.windows_api.close(handle)


def _remove_empty_native_smoke_ingress_case(
    ingress_root: Path,
    case_id: str,
    *,
    expected_identity: tuple[int, int],
) -> None:
    with open_verified_output_parent(ingress_root, create=False) as parent:
        if parent.parent_fd is not None:
            named = os.stat(case_id, dir_fd=parent.parent_fd, follow_symlinks=False)
            if (
                is_link_or_reparse(named)
                or not stat.S_ISDIR(named.st_mode)
                or file_identity(named) != expected_identity
            ):
                raise AssetContractError("native smoke ingress case identity changed")
            os.rmdir(case_id, dir_fd=parent.parent_fd)
            os.fsync(parent.parent_fd)
            return
        if parent.windows_api is None or parent.windows_parent_handle is None:
            raise AssetContractError("secure ingress cleanup primitives are unavailable")
        handle = parent.windows_api.open_existing_directory_strict(
            parent.windows_parent_handle,
            case_id,
            delete=True,
        )
        try:
            info = parent.windows_api.strict_directory_info(
                handle,
                context=f"native smoke ingress case {case_id}",
            )
            if file_identity(info) != expected_identity:
                raise AssetContractError("native smoke ingress case identity changed")
            parent.windows_api.dispose_ex(handle)
        finally:
            parent.windows_api.close(handle)


def _cleanup_native_smoke_ingress(
    ingress_root: Path,
    case_ingress: Path,
    case_id: str,
    *,
    expected_identity: tuple[int, int],
) -> bool:
    try:
        census = capture_retained_directory_file_census(
            case_ingress,
            maximum_entries=_NATIVE_SMOKE_INGRESS_CLEANUP_MAX_FILES,
            authority_root=case_ingress,
            expected_authority_identity=expected_identity,
        )
        with open_verified_output_parent(case_ingress, create=False) as parent:
            if parent.identities[-1] != expected_identity:
                raise AssetContractError("native smoke ingress case identity changed")
            for name in census.names:
                _remove_native_smoke_ingress_file(parent, name)
        _remove_empty_native_smoke_ingress_case(
            ingress_root,
            case_id,
            expected_identity=expected_identity,
        )
        return True
    except (AssetContractError, OSError, RetainedTreeError):
        return False


def _run_extracted_native_smoke(
    *,
    extracted_root: Path,
    ingress_root: Path,
    evidence_root: Path,
    case_id: str,
    environment: Mapping[str, str],
    adapter_id: str,
    adapter_version: str,
    platform_id: str,
    timeout_seconds: float = _PROCESS_TIMEOUT_SECONDS,
    output_limit: int = _MAX_PROCESS_OUTPUT_BYTES,
    evidence_collector: dict[str, dict[str, bytes]] | None = None,
) -> dict[str, Any]:
    extracted = extracted_root.resolve(strict=True)
    try:
        ingress = ingress_root.resolve(strict=True)
        evidence = evidence_root.resolve(strict=True)
    except OSError as exc:
        _fail("native_smoke_evidence_invalid", f"native smoke roots are invalid: {exc}")
    if (
        _inside(evidence, extracted)
        or _inside(ingress, extracted)
        or _inside(evidence, ingress)
        or _inside(ingress, evidence)
    ):
        _fail(
            "native_smoke_report_inside_extracted_tree",
            "native smoke ingress and evidence must remain separate and external",
        )
    if (
        not isinstance(case_id, str)
        or not case_id
        or case_id in {".", ".."}
        or "/" in case_id
        or "\\" in case_id
        or "\x00" in case_id
        or len(case_id) > 128
    ):
        _fail("native_smoke_evidence_invalid", "native smoke case id is invalid")
    case_ingress = ingress / case_id
    case_evidence = evidence / case_id
    if os.path.lexists(case_evidence):
        _fail(
            "native_smoke_evidence_exists",
            f"native smoke evidence already exists for {case_id}",
        )
    try:
        case_ingress.mkdir(mode=0o700)
    except FileExistsError:
        _fail(
            "native_smoke_ingress_exists",
            f"native smoke ingress already exists for {case_id}",
        )
    except OSError as exc:
        _fail(
            "native_smoke_ingress_invalid",
            f"native smoke ingress could not be created for {case_id}: {exc}",
        )
    report_path = case_ingress / "report.json"
    evidence_display = f"native-smoke-evidence/{case_id}"
    try:
        extracted_before = capture_retained_tree(extracted)
    except (RetainedTreeError, OSError) as exc:
        _fail(
            "native_smoke_extracted_tree_invalid",
            f"{case_id}: extracted game could not be retained: {exc}",
        )

    result: _BoundedProcessResult | None = None
    reason_code: str | None = None
    report: dict[str, Any] | None = None
    report_payload: bytes | None = None
    ingress_identity: tuple[int, int] | None = None
    try:
        with open_verified_output_parent(case_ingress, create=False) as pinned:
            ingress_identity = pinned.identities[-1]
            result = _run_bounded_subprocess(
                (
                    sys.executable,
                    "-I",
                    str(extracted / "scripts/native_smoke.py"),
                    "--report",
                    str(report_path),
                    "--report-parent-device",
                    str(ingress_identity[0]),
                    "--report-parent-inode",
                    str(ingress_identity[1]),
                ),
                cwd=extracted.parent,
                environment=environment,
                timeout_seconds=timeout_seconds,
                output_limit=output_limit,
            )
            pinned.assert_current()

            reason_code = _native_smoke_process_reason(result)
            try:
                verify_retained_tree_snapshot(extracted, extracted_before)
            except (RetainedTreeError, OSError):
                reason_code = reason_code or "native_smoke_extracted_tree_changed"

            if reason_code is None:
                try:
                    report, report_payload = _load_native_smoke_report(
                        report_path,
                        adapter_id=adapter_id,
                        adapter_version=adapter_version,
                        platform_id=platform_id,
                    )
                except MultigenreReleaseError as exc:
                    reason_code = exc.reason_code
                else:
                    try:
                        _validate_native_smoke_ingress(
                            case_ingress,
                            expected_identity=ingress_identity,
                        )
                    except MultigenreReleaseError as exc:
                        reason_code = exc.reason_code
                        report = None
                        report_payload = None
            pinned.assert_current()
    except MultigenreReleaseError:
        raise
    except AssetContractError:
        reason_code = reason_code or "native_smoke_ingress_changed"

    if result is None or ingress_identity is None:
        _fail(
            "native_smoke_ingress_changed",
            f"{case_id}: retained ingress became unavailable; see {evidence_display}",
        )
    cleanup_succeeded = _cleanup_native_smoke_ingress(
        ingress,
        case_ingress,
        case_id,
        expected_identity=ingress_identity,
    )
    if not cleanup_succeeded:
        reason_code = reason_code or "native_smoke_ingress_cleanup_failed"

    try:
        case_evidence.mkdir(mode=0o700)
    except FileExistsError:
        _fail(
            reason_code or "native_smoke_evidence_exists",
            f"native smoke evidence already exists for {case_id}",
        )
    except OSError as exc:
        _fail(
            reason_code or "native_smoke_evidence_invalid",
            f"native smoke evidence could not be created for {case_id}: {exc}",
        )
    try:
        with open_verified_output_parent(case_evidence, create=False) as pinned:
            evidence_identity = pinned.identities[-1]
            closure = _publish_native_smoke_diagnostics(
                case_evidence=case_evidence,
                parent_identity=evidence_identity,
                case_id=case_id,
                result=result,
                reason_code=reason_code,
                report_payload=report_payload,
                output_limit=output_limit,
            )
            if evidence_collector is not None:
                if case_id in evidence_collector:
                    _fail(
                        reason_code or "native_smoke_evidence_exists",
                        f"native smoke evidence was already collected for {case_id}",
                    )
                evidence_collector[case_id] = dict(closure)
            pinned.assert_current()
    except MultigenreReleaseError:
        raise
    except AssetContractError:
        _fail(
            reason_code or "native_smoke_evidence_changed",
            f"{case_id}: retained evidence ancestry changed; see {evidence_display}",
        )
    if reason_code is not None:
        _fail(reason_code, f"{case_id}: see {evidence_display}")
    if report is None:
        _fail(
            "native_smoke_report_invalid",
            f"{case_id}: validated native smoke report is unavailable",
        )
    return report


def _native_smoke_process_reason(result: _BoundedProcessResult) -> str | None:
    if result.stdout_overflow and result.stderr_overflow:
        return "native_smoke_streams_too_large"
    if result.stdout_overflow:
        return "native_smoke_stdout_too_large"
    if result.stderr_overflow:
        return "native_smoke_stderr_too_large"
    if result.timed_out:
        return "native_smoke_timeout"
    if result.return_code != 0:
        return "native_smoke_exit_nonzero"
    return None


def _reject_native_smoke_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateNativeSmokeReportKey(key)
        value[key] = item
    return value


def _reject_native_smoke_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _load_native_smoke_report(
    path: Path,
    *,
    adapter_id: str,
    adapter_version: str,
    platform_id: str,
) -> tuple[dict[str, Any], bytes]:
    try:
        initial = path.lstat()
    except FileNotFoundError:
        _fail("native_smoke_report_missing", "native smoke report was not published")
    except OSError as exc:
        _fail("native_smoke_report_unsafe", f"native smoke report could not be inspected: {exc}")
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    file_attributes = getattr(initial, "st_file_attributes", 0)
    if (
        not stat.S_ISREG(initial.st_mode)
        or initial.st_nlink != 1
        or bool(file_attributes & reparse_flag)
    ):
        _fail(
            "native_smoke_report_unsafe",
            "native smoke report must be a standalone regular file",
        )
    if initial.st_size > _NATIVE_SMOKE_REPORT_LIMIT:
        _fail("native_smoke_report_too_large", "native smoke report exceeded its byte bound")
    try:
        first = read_bound_bytes(path, limit=_NATIVE_SMOKE_REPORT_LIMIT)
    except (AssetContractError, OSError):
        _fail("native_smoke_report_changed", "native smoke report changed before retention")
    if first.identity != (initial.st_dev, initial.st_ino) or first.size_bytes != initial.st_size:
        _fail("native_smoke_report_changed", "native smoke report identity changed")
    try:
        document = json.loads(
            first.payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_native_smoke_duplicate_keys,
            parse_constant=_reject_native_smoke_nonfinite,
        )
    except _DuplicateNativeSmokeReportKey:
        _fail("native_smoke_report_duplicate_keys", "native smoke report repeats a JSON key")
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("native_smoke_report_invalid", "native smoke report is not strict JSON")
    if type(document) is not dict:
        _fail("native_smoke_report_invalid", "native smoke report must be a JSON object")
    try:
        canonical = canonical_json_bytes(document)
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError):
        _fail(
            "native_smoke_report_noncanonical",
            "native smoke report cannot be encoded as canonical JSON",
        )
    if first.payload != canonical:
        _fail("native_smoke_report_noncanonical", "native smoke report is not canonical JSON")
    fields = {"adapter_id", "adapter_version", "frames", "platform_id", "status"}
    if (
        set(document) != fields
        or document.get("status") != "native_smoke_executed"
        or document.get("adapter_id") != adapter_id
        or document.get("adapter_version") != adapter_version
        or document.get("platform_id") != platform_id
        or type(document.get("frames")) is not int
        or not 1 <= document["frames"] <= 120
    ):
        _fail(
            "native_smoke_report_contract_invalid",
            "native smoke report fields are not exact",
        )
    try:
        second = read_bound_bytes(path, limit=_NATIVE_SMOKE_REPORT_LIMIT)
    except (AssetContractError, OSError):
        _fail("native_smoke_report_changed", "native smoke report changed after validation")
    if (
        second.identity != first.identity
        or second.size_bytes != first.size_bytes
        or second.change_time_ns != first.change_time_ns
        or second.payload != first.payload
    ):
        _fail("native_smoke_report_changed", "native smoke report changed after validation")
    return document, first.payload


def _publish_native_smoke_diagnostics(
    *,
    case_evidence: Path,
    parent_identity: tuple[int, int],
    case_id: str,
    result: _BoundedProcessResult,
    reason_code: str | None,
    report_payload: bytes | None,
    output_limit: int,
) -> dict[str, bytes]:
    trusted_report = report_payload if reason_code is None else None
    report_record = {
        "filename": "report.json",
        "sha256": (
            hashlib.sha256(trusted_report).hexdigest() if trusted_report is not None else None
        ),
        "size_bytes": len(trusted_report) if trusted_report is not None else None,
    }
    stdout_record = {
        "filename": "stdout.log",
        "sha256": hashlib.sha256(result.stdout).hexdigest(),
        "size_bytes": len(result.stdout),
        "truncated": result.stdout_overflow,
    }
    stderr_record = {
        "filename": "stderr.log",
        "sha256": hashlib.sha256(result.stderr).hexdigest(),
        "size_bytes": len(result.stderr),
        "truncated": result.stderr_overflow,
    }
    attempt = {
        "case_id": case_id,
        "format": _NATIVE_SMOKE_ATTEMPT_FORMAT,
        "format_version": 1,
        "reason_code": reason_code,
        "report": report_record,
        "return_code": result.return_code,
        "state": "passed" if reason_code is None else "failed",
        "stderr": stderr_record,
        "stdout": stdout_record,
        "timed_out": result.timed_out,
    }
    try:
        attempt_payload = canonical_json_bytes(attempt)
        payloads = []
        if trusted_report is not None:
            payloads.append(("report.json", trusted_report, _NATIVE_SMOKE_REPORT_LIMIT))
        payloads.extend(
            (
                ("stdout.log", result.stdout, output_limit),
                ("stderr.log", result.stderr, output_limit),
                ("attempt.json", attempt_payload, _NATIVE_SMOKE_ATTEMPT_LIMIT),
            )
        )
        for name, payload, limit in payloads:
            publish_bytes_noreplace(
                case_evidence,
                name,
                payload,
                expected_parent_identity=parent_identity,
                limit=limit,
                mode=_NATIVE_SMOKE_EVIDENCE_MODE,
            )
        census = capture_retained_directory_file_census(
            case_evidence,
            maximum_entries=_NATIVE_SMOKE_EVIDENCE_MAX_FILES,
            authority_root=case_evidence,
            expected_authority_identity=parent_identity,
        )
        if census.names != tuple(sorted(name for name, _payload, _limit in payloads)):
            raise PersistenceIOError("native smoke evidence inventory is not exact")
        for name, expected_payload, limit in payloads:
            retained = read_bound_bytes(case_evidence / name, limit=limit)
            if retained.payload != expected_payload:
                raise PersistenceIOError("native smoke evidence bytes changed")
    except (
        AssetContractError,
        FileExistsError,
        OSError,
        PersistenceIOError,
        RecursionError,
        RetainedTreeError,
        TypeError,
        UnicodeError,
        ValueError,
    ) as exc:
        _fail(
            reason_code or "native_smoke_diagnostics_publish_failed",
            f"{case_id}: native smoke diagnostics could not be published: {exc}",
        )
    return {name: payload for name, payload, _limit in payloads}


def _publish_native_smoke_evidence_closure(
    trusted_parent: Path,
    records: Mapping[str, Mapping[str, bytes]],
    *,
    forbidden_roots: Sequence[Path] = (),
) -> Path:
    allowed_files = {
        "attempt.json": _NATIVE_SMOKE_ATTEMPT_LIMIT,
        "report.json": _NATIVE_SMOKE_REPORT_LIMIT,
        "stderr.log": _MAX_PROCESS_OUTPUT_BYTES,
        "stdout.log": _MAX_PROCESS_OUTPUT_BYTES,
    }
    required_files = {"attempt.json", "stderr.log", "stdout.log"}
    if type(records) is not dict or set(records) != set(CASES):
        _fail("native_smoke_evidence_invalid", "native smoke evidence cases are invalid")
    expected_files: dict[str, bytes] = {}
    for case_id, files in records.items():
        if (
            type(files) is not dict
            or not required_files.issubset(files)
            or not set(files).issubset(allowed_files)
        ):
            _fail(
                "native_smoke_evidence_invalid",
                f"native smoke evidence closure is invalid for {case_id}",
            )
        for name, payload in files.items():
            if type(payload) is not bytes or len(payload) > allowed_files[name]:
                _fail(
                    "native_smoke_evidence_invalid",
                    f"native smoke evidence payload is invalid for {case_id}/{name}",
                )
            expected_files[f"{case_id}/{name}"] = payload
    try:
        requested_parent = trusted_parent.absolute()
        requested_info = requested_parent.lstat()
        if is_link_or_reparse(requested_info) or not stat.S_ISDIR(requested_info.st_mode):
            raise AssetContractError("trusted native smoke evidence parent is unsafe")
        trusted_parent = requested_parent.resolve(strict=True)
        resolved_forbidden = tuple(root.resolve(strict=True) for root in forbidden_roots)
        evidence_root: Path | None = None
        created_posix_descriptor: int | None = None
        created_windows_handle: int | None = None
        with open_verified_output_parent(trusted_parent, create=False) as retained_parent:
            try:
                for _ in range(_NATIVE_SMOKE_EVIDENCE_ROOT_ATTEMPTS):
                    token = secrets.token_hex(16)
                    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None:
                        raise AssetContractError("native smoke evidence name entropy is invalid")
                    name = f"{_NATIVE_SMOKE_EVIDENCE_ROOT_PREFIX}{token}"
                    candidate = trusted_parent / name
                    if any(
                        _inside(candidate, forbidden) or _inside(forbidden, candidate)
                        for forbidden in resolved_forbidden
                    ):
                        raise AssetContractError(
                            "native smoke evidence root overlaps an unsafe root"
                        )
                    try:
                        if retained_parent.parent_fd is not None:
                            os.mkdir(name, mode=0o700, dir_fd=retained_parent.parent_fd)
                            created_posix_descriptor = os.open(
                                name,
                                os.O_RDONLY
                                | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_CLOEXEC", 0)
                                | getattr(os, "O_NOFOLLOW", 0),
                                dir_fd=retained_parent.parent_fd,
                            )
                            created_info = descriptor_file_stat(created_posix_descriptor)
                        elif (
                            retained_parent.windows_api is not None
                            and retained_parent.windows_parent_handle is not None
                        ):
                            created_windows_handle = retained_parent.windows_api.create_directory(
                                retained_parent.windows_parent_handle,
                                name,
                                request_delete=False,
                            )
                            created_info = retained_parent.windows_api.strict_directory_info(
                                created_windows_handle,
                                context="trusted native smoke evidence root",
                            )
                        else:
                            raise AssetContractError(
                                "secure native smoke evidence allocation is unavailable"
                            )
                    except FileExistsError:
                        continue
                    evidence_root = candidate
                    retained_parent.assert_current()
                    visible = evidence_root.lstat()
                    if (
                        is_link_or_reparse(created_info)
                        or is_link_or_reparse(visible)
                        or not stat.S_ISDIR(created_info.st_mode)
                        or not stat.S_ISDIR(visible.st_mode)
                        or file_identity(created_info) != file_identity(visible)
                    ):
                        raise AssetContractError(
                            "native smoke evidence root identity changed during allocation"
                        )
                    break
                if evidence_root is None:
                    raise FileExistsError("could not allocate exclusive native smoke evidence root")
                with open_verified_output_parent(evidence_root, create=False) as evidence_parent:
                    root_identity = evidence_parent.identities[-1]
                    if root_identity != file_identity(created_info):
                        raise AssetContractError(
                            "native smoke evidence root identity changed before publication"
                        )
                    for case_id in sorted(records):
                        case_evidence = evidence_root / case_id
                        case_evidence.mkdir(mode=0o700)
                        with open_verified_output_parent(
                            case_evidence, create=False
                        ) as case_parent:
                            parent_identity = case_parent.identities[-1]
                            for name in sorted(records[case_id]):
                                publish_bytes_noreplace(
                                    case_evidence,
                                    name,
                                    records[case_id][name],
                                    expected_parent_identity=parent_identity,
                                    limit=allowed_files[name],
                                    mode=_NATIVE_SMOKE_EVIDENCE_MODE,
                                )
                        evidence_parent.assert_current()
                    expected = RetainedTreeSnapshot(
                        root=evidence_root.absolute(),
                        root_identity=root_identity,
                        directories=("", *sorted(records)),
                        files=dict(sorted(expected_files.items())),
                    )
                    verify_retained_tree_snapshot(evidence_root, expected)
                    evidence_parent.assert_current()
                retained_parent.flush_durable(context="trusted native smoke evidence parent")
                retained_parent.assert_current()
            finally:
                if created_posix_descriptor is not None:
                    os.close(created_posix_descriptor)
                if created_windows_handle is not None and retained_parent.windows_api is not None:
                    retained_parent.windows_api.close(created_windows_handle)
    except MultigenreReleaseError:
        raise
    except (
        AssetContractError,
        FileExistsError,
        OSError,
        PersistenceIOError,
        RetainedTreeError,
    ) as exc:
        _fail(
            "native_smoke_evidence_publish_failed",
            f"native smoke upload evidence could not be published: {exc}",
        )
    return evidence_root.resolve(strict=True)


def _has_complete_native_smoke_evidence_records(
    records: Mapping[str, Mapping[str, bytes]],
) -> bool:
    allowed_files = {
        "attempt.json",
        "report.json",
        "stderr.log",
        "stdout.log",
    }
    required_files = {"attempt.json", "stderr.log", "stdout.log"}
    return (
        type(records) is dict
        and set(records) == set(CASES)
        and all(
            type(files) is dict
            and required_files.issubset(files)
            and set(files).issubset(allowed_files)
            and all(type(payload) is bytes for payload in files.values())
            for files in records.values()
        )
    )


def _publish_trusted_native_release_row(
    evidence_root: Path,
    report: Mapping[str, object],
) -> Path:
    host = report.get("host")
    if type(host) is not dict:
        _fail("native_release_row_publish_failed", "native release host is invalid")
    os_name = {"linux": "Linux", "windows": "Windows"}.get(host.get("os"))
    python_minor = host.get("python_minor")
    if (
        os_name is None
        or not isinstance(python_minor, str)
        or re.fullmatch(r"3\.[0-9]+", python_minor) is None
    ):
        _fail("native_release_row_publish_failed", "native release host is invalid")
    name = f"world-forge-{os_name}-py{python_minor}.json"
    payload = canonical_json_bytes(report)
    if len(payload) > _MAX_PROCESS_OUTPUT_BYTES:
        _fail("native_release_row_publish_failed", "native release row exceeded its bound")
    try:
        with open_verified_output_parent(evidence_root, create=False) as retained_parent:
            identity = retained_parent.identities[-1]
            publish_bytes_noreplace(
                evidence_root,
                name,
                payload,
                expected_parent_identity=identity,
                limit=_MAX_PROCESS_OUTPUT_BYTES,
                mode=_NATIVE_SMOKE_EVIDENCE_MODE,
            )
            retained = read_bound_bytes(
                evidence_root / name,
                limit=_MAX_PROCESS_OUTPUT_BYTES,
            )
            if retained.payload != payload:
                raise PersistenceIOError("native release row bytes changed")
            retained_parent.flush_durable(context="trusted native release row")
            retained_parent.assert_current()
    except MultigenreReleaseError:
        raise
    except (
        AssetContractError,
        FileExistsError,
        OSError,
        PersistenceIOError,
        RetainedTreeError,
    ) as exc:
        _fail(
            "native_release_row_publish_failed",
            f"trusted native release row could not be published: {exc}",
        )
    return (evidence_root / name).resolve(strict=True)


def _publish_native_smoke_github_output(
    github_output_path: Path | None,
    evidence_root: Path,
    release_row_path: Path,
) -> None:
    if github_output_path is None:
        _fail(
            "native_smoke_evidence_output_missing",
            "GitHub native evidence output path is unavailable",
        )
    output_text = str(github_output_path)
    _single_line_absolute_path(output_text, label="GITHUB_OUTPUT")
    expected_name = evidence_root.name
    if (
        not expected_name.startswith(_NATIVE_SMOKE_EVIDENCE_ROOT_PREFIX)
        or re.fullmatch(
            rf"{re.escape(_NATIVE_SMOKE_EVIDENCE_ROOT_PREFIX)}[0-9a-f]{{32}}",
            expected_name,
        )
        is None
    ):
        _fail(
            "native_smoke_evidence_output_invalid",
            "native smoke evidence root name is invalid",
        )
    descriptor: int | None = None
    try:
        evidence_root = evidence_root.resolve(strict=True)
        release_row_path = release_row_path.resolve(strict=True)
        evidence_text = str(evidence_root)
        release_row_text = str(release_row_path)
        _single_line_absolute_path(evidence_text, label="native smoke evidence path")
        _single_line_absolute_path(release_row_text, label="native release row path")
        evidence_info = evidence_root.lstat()
        row_info = release_row_path.lstat()
        output_info = github_output_path.lstat()
        if is_link_or_reparse(evidence_info) or not stat.S_ISDIR(evidence_info.st_mode):
            raise AssetContractError("native smoke evidence root is unsafe")
        if (
            release_row_path.parent != evidence_root
            or re.fullmatch(
                r"world-forge-(?:Linux|Windows)-py3\.[0-9]+\.json",
                release_row_path.name,
            )
            is None
            or is_link_or_reparse(row_info)
            or not stat.S_ISREG(row_info.st_mode)
            or row_info.st_nlink != 1
            or row_info.st_size > _MAX_PROCESS_OUTPUT_BYTES
        ):
            raise AssetContractError("native release row is unsafe")
        if (
            is_link_or_reparse(output_info)
            or not stat.S_ISREG(output_info.st_mode)
            or output_info.st_nlink != 1
            or output_info.st_size != 0
        ):
            raise AssetContractError("GitHub output file is unsafe")
        descriptor = os.open(
            github_output_path,
            os.O_WRONLY
            | os.O_APPEND
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        retained = descriptor_file_stat(descriptor)
        if (
            is_link_or_reparse(retained)
            or not stat.S_ISREG(retained.st_mode)
            or retained.st_nlink != 1
            or file_identity(retained) != file_identity(output_info)
        ):
            raise AssetContractError("GitHub output file identity changed")
        output_payload = (
            f"native_smoke_evidence_path={evidence_text}\n"
            "native_smoke_evidence_published=true\n"
            f"release_row_path={release_row_text}\n"
            "release_row_published=true\n"
        ).encode()
        if os.write(descriptor, output_payload) != len(output_payload):
            raise OSError("GitHub output publication write was incomplete")
        named_after_path = github_output_path.lstat()
        retained_after_path = descriptor_file_stat(descriptor)
        if (
            file_identity(named_after_path) != file_identity(output_info)
            or file_identity(retained_after_path) != file_identity(output_info)
            or retained_after_path.st_size != len(output_payload)
        ):
            raise AssetContractError("GitHub output file changed before publication")
    except MultigenreReleaseError:
        raise
    except (AssetContractError, OSError, UnicodeError, ValueError) as exc:
        _fail(
            "native_smoke_evidence_output_failed",
            f"trusted native smoke evidence output could not be published: {exc}",
        )
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _regenerate_asset_production(
    *,
    case_root: Path,
    case_id: str,
    fixture_root: Path,
) -> None:
    asset_id = "board_ui" if case_id == "abstract-puzzle" else "narrative_ui_font"
    production = case_root / "assets/production" / asset_id
    canonical_receipt = read_creation_object(production / "receipt.json")
    canonical_processing = read_creation_object(production / "processing-receipt.json")
    candidate_locator = canonical_receipt["outputs"][0]["locator"]
    processed_locator = canonical_processing["outputs"][0]["locator"]
    for locator in (candidate_locator, processed_locator):
        path = case_root / locator
        path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass

    try:
        generated = build_fixture_documents(
            case_id,
            artifact_root=case_root,
            source_root=fixture_root,
        )
    except (AssetContractError, OSError, ValueError) as exc:
        _fail("asset_production_failed", f"{case_id}: {exc}")
    canonical_case = fixture_root / case_id
    for canonical_path, document, payload in generated:
        try:
            relative = canonical_path.relative_to(canonical_case)
        except ValueError:
            _fail("asset_production_failed", f"{case_id}: generated path escaped fixture")
        actual = case_root / relative
        if not actual.is_file() or actual.read_bytes() != payload:
            _fail("asset_production_determinism_failed", f"{case_id}: {relative} drifted")
        if document is not None and read_creation_object(actual) != document:
            _fail("asset_production_determinism_failed", f"{case_id}: {relative} is invalid")


def _load_asset_chain(case_root: Path, case_id: str) -> dict[str, Any]:
    asset_id = {
        "abstract-puzzle": "board_ui",
        "branching-narrative": "narrative_ui_font",
    }[case_id]
    gamepack_path = case_root / "artifacts" / f"{case_id}.gamepack.json"
    subject_path = case_root / "assets/subject.json"
    target_path = case_root / "assets/target.json"
    style_path = case_root / "assets/style.json"
    inventory_path = case_root / "assets/inventory.json"
    specification_path = case_root / "assets/specs" / f"{asset_id}.json"
    gamepack = read_creation_object(gamepack_path)
    subject = load_asset_subject(subject_path, gamepack_path=gamepack_path)
    target = load_asset_target(
        target_path,
        gamepack_path=gamepack_path,
        subject_path=subject_path,
    )
    style = load_asset_style(
        style_path,
        gamepack_path=gamepack_path,
        subject_path=subject_path,
        target_path=target_path,
    )
    inventory = load_asset_inventory(
        inventory_path,
        gamepack_path=gamepack_path,
        subject_path=subject_path,
        target_path=target_path,
        style_path=style_path,
    )
    specification = load_asset_specification(
        specification_path,
        gamepack=gamepack,
        subject=subject,
        target=target,
        style=style,
        inventory=inventory,
    )
    production = case_root / "assets/production" / asset_id
    common = {
        "gamepack": gamepack,
        "subject": subject,
        "target": target,
        "style": style,
        "inventory": inventory,
        "specification": specification,
    }
    request = load_asset_production_request(production / "request.json", **common)
    receipt = load_asset_production_receipt(
        production / "receipt.json",
        request=request,
        artifact_root=case_root,
        **common,
    )
    selection = load_asset_selection(
        production / "selection.json",
        receipt=receipt,
        request=request,
        artifact_root=case_root,
        **common,
    )
    provenance = load_asset_provenance_record(
        production / "provenance.json",
        selection=selection,
        receipt=receipt,
        request=request,
        artifact_root=case_root,
        **common,
    )
    license_record = load_asset_license_record(
        production / "license.json",
        provenance=provenance,
        selection=selection,
        receipt=receipt,
        request=request,
        artifact_root=case_root,
        **common,
    )
    lineage = {
        **common,
        "request": request,
        "receipt": receipt,
        "selection": selection,
        "provenance": provenance,
        "license_records": [license_record],
        "artifact_root": case_root,
    }
    recipe_path = production / "recipe.json"
    recipe = load_asset_processing_recipe(recipe_path, **lineage)
    canonical_processing_receipt = read_creation_object(production / "processing-receipt.json")
    processing_receipt = build_asset_processing_receipt(
        recipe,
        processing_receipt_id=canonical_processing_receipt["processing_receipt_id"],
        **lineage,
    )
    if processing_receipt != canonical_processing_receipt:
        _fail("asset_processing_determinism_failed", f"{case_id} receipt bytes drifted")
    canonical_qa = read_creation_object(production / "qa-report.json")
    qa_report = build_asset_qa_report(
        processing_receipt,
        recipe=recipe,
        qa_report_id=canonical_qa["qa_report_id"],
        acceptance_results=canonical_qa["acceptance_criteria"],
        **lineage,
    )
    if qa_report != canonical_qa or qa_report["status"] != "passed":
        _fail("asset_qa_failed", f"{case_id} QA report drifted or failed")
    record = {
        "specification": specification,
        "request": request,
        "receipt": receipt,
        "selection": selection,
        "provenance": provenance,
        "license_records": [license_record],
        "recipe": recipe,
        "processing_receipt": processing_receipt,
        "qa_report": qa_report,
    }
    canonical_manifest = read_creation_object(case_root / "assets/manifest.json")
    authority_source = _resolve_generic_assetpack_cli_source(case_root / "assets/manifest.json")
    manifest = build_asset_manifest(
        gamepack,
        subject,
        target,
        style,
        inventory,
        manifest_id=canonical_manifest["manifest_id"],
        state="release_ready",
        asset_records=[record],
        artifact_root=case_root,
        qa_reviews=authority_source["qa_reviews"],
    )
    if manifest != canonical_manifest:
        _fail("asset_manifest_determinism_failed", f"{case_id} manifest drifted")
    return {
        **common,
        "manifest": manifest,
        "asset_records": [record],
        "qa_reviews": authority_source["qa_reviews"],
        "release_authority": authority_source["release_authority"],
    }


def _select_platform_lock(
    locks: Sequence[Mapping[str, object]],
    host: Mapping[str, str],
) -> Mapping[str, object] | None:
    matches = [
        lock
        for lock in locks
        if lock["platform"]["os"] == host["os"]
        and lock["platform"]["architecture"] == host["architecture"]
        and lock["python"]["minor"] == host["python_minor"]
    ]
    if len(matches) > 1:
        _fail("native_platform_lock_ambiguous", "multiple platform locks match the host")
    return matches[0] if matches else None


def assert_materialized_platform_lock(
    materialization_manifest: Mapping[str, object],
    selected_lock: Mapping[str, object],
) -> None:
    """Require native evidence to name the exact lock embedded in the game."""

    platform_locks = materialization_manifest.get("platform_locks")
    identities = platform_locks.get("locks") if isinstance(platform_locks, Mapping) else None
    expected = {
        "id": selected_lock.get("lock_id"),
        "content_hash": selected_lock.get("content_hash"),
        "os": (
            selected_lock.get("platform", {}).get("os")
            if isinstance(selected_lock.get("platform"), Mapping)
            else None
        ),
        "python_minor": (
            selected_lock.get("python", {}).get("minor")
            if isinstance(selected_lock.get("python"), Mapping)
            else None
        ),
    }
    matches = (
        []
        if not isinstance(identities, list)
        else [
            item
            for item in identities
            if isinstance(item, Mapping)
            and all(item.get(key) == value for key, value in expected.items())
        ]
    )
    if len(matches) != 1:
        _fail(
            "native_platform_lock_identity_mismatch",
            "selected native platform lock is not bound into the materialization",
        )


def _run_persistence_workflow(
    case_id: str,
    game_root: Path,
    fixture_root: Path,
    user_data: Path,
    runtime_bundle_root: Path,
    runtime_bundle_hash: str,
    environment: Mapping[str, str],
) -> dict[str, object]:
    user_data.parent.mkdir(parents=True, exist_ok=True)
    expected = (
        (("swap_tiles", "puzzle_complete"),)
        if case_id == "abstract-puzzle"
        else (("choose_left", "ending_left"), ("choose_right", "ending_right"))
    )
    endings: list[str] = []
    save_reports = []
    save_restore_reports = []
    replay_reports = []
    execution_script = read_creation_object(fixture_root / "runtime/headless/execution-script.json")
    for scenario, ending in expected:
        slot = scenario
        expected_scenario = next(
            item for item in execution_script["scenarios"] if item["scenario_id"] == scenario
        )
        saves_before = set(user_data.joinpath("saves").rglob("*.json"))
        replays_before = set(user_data.joinpath("replays").rglob("*.json"))
        recorded = _checked_subprocess_json(
            (
                sys.executable,
                "-I",
                str(game_root / "run_game.py"),
                "--headless-script",
                str(fixture_root / "runtime/headless/execution-script.json"),
                "--scenario",
                scenario,
                "--user-data",
                str(user_data),
                "--save-on-exit-slot",
                slot,
                "--record-replay-slot",
                slot,
            ),
            cwd=user_data.parent,
            environment=environment,
        )
        _validate_headless_record(
            recorded,
            scenario=expected_scenario,
            slot=slot,
            runtime_bundle_hash=runtime_bundle_hash,
        )
        restored = _checked_subprocess_json(
            (
                sys.executable,
                "-I",
                str(game_root / "run_game.py"),
                "--user-data",
                str(user_data),
                "--verify-save-slot",
                slot,
            ),
            cwd=user_data.parent,
            environment=environment,
        )
        _validate_save_restore_report(restored, scenario=expected_scenario, ending=ending)
        save_restore_reports.append(restored)
        replayed = _checked_subprocess_json(
            (
                sys.executable,
                "-I",
                str(game_root / "run_game.py"),
                "--user-data",
                str(user_data),
                "--replay-slot",
                slot,
            ),
            cwd=user_data.parent,
            environment=environment,
        )
        _validate_replay_report(replayed, scenario=expected_scenario, ending=ending)
        endings.append(ending)
        save_paths = sorted(set(user_data.joinpath("saves").rglob("*.json")) - saves_before)
        replay_paths = sorted(set(user_data.joinpath("replays").rglob("*.json")) - replays_before)
        if len(save_paths) != 1 or len(replay_paths) != 1:
            _fail("persistence_output_missing", f"{case_id}/{slot} did not emit one save/replay")
        _assert_runtime_authority_external(
            "save file",
            {str(save_paths[0]): save_paths[0].read_bytes()},
        )
        _assert_runtime_authority_external(
            "replay file",
            {str(replay_paths[0]): replay_paths[0].read_bytes()},
        )
        save_report = verify_persistence_generation(save_paths[0], bundle_root=runtime_bundle_root)
        replay_report = verify_persistence_generation(
            replay_paths[0], bundle_root=runtime_bundle_root
        )
        _validate_persistence_report(save_report, kind="save", slot=slot)
        _validate_persistence_report(replay_report, kind="replay", slot=slot)
        save_reports.append(save_report)
        replay_reports.append(replay_report)
    return {
        "endings": sorted(endings),
        "replays_verified": len(replay_reports),
        "saves_restored": len(save_restore_reports),
        "saves_verified": len(save_reports),
    }


def _assert_gamepack_hash(actual: object, expected: str, stage: str) -> None:
    if actual != expected:
        _fail("release_lineage_mismatch", f"{stage} gamepack hash differs")


def _assert_runtime_authority_external(
    artifact: str,
    files: Mapping[str, bytes],
) -> None:
    if any(
        marker in payload for payload in files.values() for marker in _RUNTIME_AUTHORITY_MARKERS
    ):
        _fail(
            "runtime_support_authority_leaked",
            f"{artifact} embeds an external runtime/native authority companion",
        )


def _run_case(
    *,
    case_id: str,
    fixture_root: Path,
    work_root: Path,
    host: Mapping[str, str],
    native_mode: str,
    platform_locks: Sequence[Mapping[str, object]],
    native_evidence_records: dict[str, dict[str, bytes]],
) -> _CaseRunResult:
    case_root = fixture_root / case_id
    compiled_first = case_root / "artifacts" / f"{case_id}.gamepack.json"
    compiled_first.unlink()
    compiled_second = work_root / "compiled-second" / f"{case_id}.gamepack.json"
    compiled_second.parent.mkdir(parents=True, exist_ok=True)

    load_game_source_project(case_root)
    stages = [_passed_stage("validate")]
    first = compile_game_project(case_root, compiled_first)
    stages.append(_passed_stage("compile_first"))
    second = compile_game_project(case_root, compiled_second)
    stages.append(_passed_stage("compile_second"))
    if first != second or compiled_first.read_bytes() != compiled_second.read_bytes():
        _fail("gamepack_determinism_failed", f"{case_id} compilation differs")
    gamepack_hash = first["content_hash"]
    lineage: dict[str, str] = {}

    def bind_lineage(stage: str, actual: object) -> None:
        _assert_gamepack_hash(actual, gamepack_hash, stage)
        lineage[stage] = str(actual)

    bind_lineage("validate", first["content_hash"])
    bind_lineage("compile_first", first["content_hash"])
    bind_lineage("compile_second", second["content_hash"])

    analysis = analyze_gamepack(first)
    if analysis["status"] != "passed":
        _fail("game_analysis_failed", f"{case_id} analysis status is {analysis['status']}")
    analysis_path = work_root / "analysis" / f"{case_id}.json"
    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    analysis_path.write_bytes(serialize_game_analysis(analysis))
    bind_lineage("analysis", analysis["gamepack"]["content_hash"])
    stages.append(_passed_stage("analysis"))

    ledger = build_authoring_capability_ledger(first)
    ledger_path = work_root / "ledgers" / f"{case_id}.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_bytes(serialize_capability_ledger(ledger))
    bind_lineage("capability_ledger", ledger["gamepack"]["content_hash"])
    stages.append(_passed_stage("capability_ledger"))

    asset_id = "board_ui" if case_id == "abstract-puzzle" else "narrative_ui_font"
    _regenerate_asset_production(
        case_root=case_root,
        case_id=case_id,
        fixture_root=fixture_root,
    )
    stages.append(_passed_stage("asset_production"))
    processed = case_root / "assets/production" / asset_id / "processed"
    processing_receipt = read_creation_object(
        case_root / "assets/production" / asset_id / "processing-receipt.json"
    )
    processed_file = case_root / processing_receipt["outputs"][0]["locator"]
    processed_file.unlink()
    processed_file.parent.rmdir()
    processed.rmdir()
    asset_chain = _load_asset_chain(case_root, case_id)
    stages.extend(
        (
            _passed_stage("asset_processing"),
            _passed_stage("asset_qa"),
        )
    )
    asset_gamepack_hash = asset_chain["manifest"]["gamepack"]["content_hash"]
    bind_lineage("asset_production", asset_gamepack_hash)
    bind_lineage("asset_processing", asset_gamepack_hash)
    bind_lineage("asset_qa", asset_gamepack_hash)
    assetpack = seal_generic_assetpack(
        work_root / "assetpacks" / case_id,
        asset_chain["manifest"],
        gamepack=asset_chain["gamepack"],
        subject=asset_chain["subject"],
        target=asset_chain["target"],
        style=asset_chain["style"],
        inventory=asset_chain["inventory"],
        asset_records=asset_chain["asset_records"],
        artifact_root=case_root,
        qa_reviews=asset_chain["qa_reviews"],
        release_authority=asset_chain["release_authority"],
    )
    try:
        assetpack_manifest = assetpack.manifest
        _assert_runtime_authority_external("assetpack", assetpack.files)
        bind_lineage("asset_seal", assetpack_manifest["gamepack"]["content_hash"])
        stages.append(_passed_stage("asset_seal"))

        runtime_snapshot = read_creation_object(fixture_root / "runtime/snapshot.json")
        _assert_runtime_authority_external(
            "runtime snapshot",
            {"runtime/snapshot.json": canonical_json_bytes(runtime_snapshot)},
        )
        runtime_registry = read_creation_object(fixture_root / "runtime/registry.json")
        runtime_composition = read_creation_object(case_root / "runtime/composition.json")
        runtime_authority = initialize_runtime_support_authority(
            gamepack=first,
            inventory=asset_chain["inventory"],
            composition=runtime_composition,
            registry=runtime_registry,
            snapshot=runtime_snapshot,
            verified_assetpack=assetpack,
            asset_release_authority=asset_chain["release_authority"],
        )
        initial_runtime_authority = runtime_authority.document
        canonical_runtime_authority = validate_runtime_support_authority_document(
            read_creation_object(case_root / "runtime/support-authority.json")
        )
        if initial_runtime_authority != canonical_runtime_authority:
            _fail(
                "runtime_support_authority_drifted",
                f"{case_id} initial runtime authority differs from its canonical companion",
            )
        initial_support = derive_runtime_support_report(runtime_authority)
        if initial_support != read_creation_object(case_root / "runtime/support-report.json"):
            _fail(
                "runtime_support_authority_drifted",
                f"{case_id} support report is not derived by exact runtime authority",
            )

        runtime_bundle = build_game_runtime_bundle(
            work_root / "runtime-bundles" / case_id,
            gamepack_path=compiled_first,
            inventory_path=case_root / "assets/inventory.json",
            assetpack_root=assetpack.root,
            snapshot_path=fixture_root / "runtime/snapshot.json",
            registry_path=fixture_root / "runtime/registry.json",
            composition_path=case_root / "runtime/composition.json",
            support_report_path=case_root / "runtime/support-report.json",
        )
    finally:
        assetpack.close()
    try:
        runtime_manifest = runtime_bundle.manifest
        _assert_runtime_authority_external("runtime bundle", runtime_bundle.files)
        support_report = read_creation_object(case_root / "runtime/support-report.json")
        allowed_pending = {
            "adapter_not_verified",
            "headless_evidence_missing",
            "native_evidence_missing",
            "packaging_evidence_missing",
            "save_replay_evidence_missing",
        }
        if (
            support_report["missing_capabilities"]
            or set(support_report["reason_codes"]) - allowed_pending
            or support_report["compatibility_status"] not in {"partially_supported", "supported"}
        ):
            _fail("runtime_support_failed", f"{case_id} has unresolved capabilities")
        if (
            runtime_manifest["contracts"]["runtime_support_report"]["content_hash"]
            != support_report["content_hash"]
        ):
            _fail("runtime_support_failed", f"{case_id} support identity drifted")
        runtime_gamepack_hash = runtime_manifest["contracts"]["gamepack"]["content_hash"]
        bind_lineage("runtime_support", runtime_gamepack_hash)
        bind_lineage("runtime_bundle", runtime_gamepack_hash)
        stages.extend((_passed_stage("runtime_support"), _passed_stage("runtime_bundle")))
        headless_parent = work_root / "headless-authority"
        headless_parent.mkdir(parents=True, exist_ok=True)
        try:
            verified_headless = build_headless_evidence_set(
                headless_parent / case_id,
                bundle_root=runtime_bundle.root,
                script_path=case_root / "runtime/headless/execution-script.json",
            )
            try:
                runtime_authority = attach_verified_headless_evidence(
                    runtime_authority,
                    verified_headless,
                    bundle_root=runtime_bundle.root,
                )
            finally:
                verified_headless.close()
        except (GenericHeadlessError, RuntimeSupportAuthorityError) as exc:
            _fail("runtime_evidence_authority_unavailable", f"{case_id}: {exc}")
        authoritative_headless_evidence = derive_runtime_evidence(runtime_authority)
        if (
            len(authoritative_headless_evidence) != 1
            or authoritative_headless_evidence[0]["execution_status"] != "headless_verified"
            or authoritative_headless_evidence[0]["packaging_status"] != "unverified"
        ):
            _fail(
                "runtime_evidence_authority_invalid",
                f"{case_id} exact headless authority projection is invalid",
            )
        implementation = load_runtime_implementation(
            case_root / "runtime/runtime-implementation.json"
        )
        materialization = build_game_materialization_bundle(
            work_root / "materialization-bundles" / case_id,
            runtime_bundle_root=runtime_bundle.root,
            runtime_implementation=implementation,
            platform_locks=platform_locks,
        )
    finally:
        runtime_bundle.close()
    try:
        materialization_manifest = materialization.manifest
        _assert_runtime_authority_external("materialization bundle", materialization.files)
        bind_lineage(
            "materialization_bundle",
            materialization_manifest["lineage"]["gamepack_hash"],
        )
        stages.append(_passed_stage("materialization_bundle"))
        selected_lock = _select_platform_lock(platform_locks, host)
        if selected_lock is not None:
            assert_materialized_platform_lock(materialization_manifest, selected_lock)
        standalone = materialize_game(
            materialization.root,
            work_root / "standalone" / case_id,
            expected_content_hash=materialization_manifest["content_hash"],
        )
    finally:
        materialization.close()
    try:
        standalone_manifest = standalone.manifest
        _assert_runtime_authority_external("standalone game", standalone.files)
        bind_lineage(
            "standalone_materialize",
            standalone_manifest["lineage"]["gamepack_hash"],
        )
        stages.append(_passed_stage("standalone_materialize"))
        findings = audit_game_repository(standalone.root)
        if findings:
            _fail("standalone_audit_failed", "; ".join(map(str, findings)))
        verify_standalone_game(
            standalone.root,
            expected_content_hash=standalone_manifest["content_hash"],
        ).close()
        bind_lineage("standalone_audit", standalone_manifest["lineage"]["gamepack_hash"])
        stages.append(_passed_stage("standalone_audit"))
        environment = _sanitized_release_child_environment(os.environ)
        independent = _checked_subprocess_json(
            (sys.executable, "-I", str(standalone.root / "scripts/verify_game.py")),
            cwd=work_root,
            environment=environment,
        )
        _validate_independent_verifier(
            independent,
            game_root=standalone.root,
            standalone_manifest=standalone_manifest,
            runtime_bundle_hash=runtime_manifest["content_hash"],
        )
        bind_lineage("independent_verify", standalone_manifest["lineage"]["gamepack_hash"])
        stages.append(_passed_stage("independent_verify"))
        user_data = work_root / "user-data" / case_id
        persistence = _run_persistence_workflow(
            case_id,
            standalone.root,
            case_root,
            user_data,
            standalone.root / RUNTIME_BUNDLE_ROOT,
            runtime_manifest["content_hash"],
            environment,
        )
        bind_lineage("headless", standalone_manifest["lineage"]["gamepack_hash"])
        bind_lineage("persistence", standalone_manifest["lineage"]["gamepack_hash"])
        stages.extend((_passed_stage("headless"), _passed_stage("persistence")))
        first_package = package_game(
            standalone.root,
            work_root / "packages-first" / f"{case_id}.wfgame",
        )
        try:
            first_package_manifest = first_package.manifest
            first_archive_sha256 = first_package.archive_sha256
            _assert_runtime_authority_external(
                "package archive",
                {f"{case_id}.wfgame": first_package.archive_bytes},
            )
        finally:
            first_package.close()
        stages.append(_passed_stage("package_first"))
        second_package = package_game(
            standalone.root,
            work_root / "packages-second" / f"{case_id}.wfgame",
        )
        try:
            if (
                second_package.manifest != first_package_manifest
                or second_package.archive_sha256 != first_archive_sha256
                or (work_root / "packages-second" / f"{case_id}.wfgame").read_bytes()
                != (work_root / "packages-first" / f"{case_id}.wfgame").read_bytes()
            ):
                _fail("game_package_determinism_failed", f"{case_id} packages differ")
            _assert_runtime_authority_external(
                "package archive",
                {f"{case_id}.wfgame": second_package.archive_bytes},
            )
        finally:
            second_package.close()
        stages.append(_passed_stage("package_second"))
        bind_lineage("package_first", first_package_manifest["lineage"]["gamepack_hash"])
        bind_lineage("package_second", first_package_manifest["lineage"]["gamepack_hash"])
        verified_package = verify_game_package(work_root / "packages-first" / f"{case_id}.wfgame")
        try:
            _assert_runtime_authority_external("game package", verified_package.files)
            extracted = extract_game_package(
                work_root / "packages-first" / f"{case_id}.wfgame",
                work_root / "extracted" / case_id,
            )
            try:
                _assert_runtime_authority_external("package extracted files", extracted.files)
                extraction_evidence = build_game_package_extraction_evidence(
                    verified_package.manifest,
                    archive_sha256=verified_package.archive_sha256,
                    archive_size_bytes=len(verified_package.archive_bytes),
                )
                runtime_authority = attach_verified_game_package(
                    runtime_authority,
                    verified_package,
                    extracted_standalone=extracted,
                    extraction_evidence=extraction_evidence,
                )
            except RuntimeSupportAuthorityError as exc:
                _fail("packaging_evidence_authority_invalid", f"{case_id}: {exc}")
            finally:
                extracted.close()
        finally:
            verified_package.close()
        packaged_runtime_evidence = derive_runtime_evidence(runtime_authority)
        packaged_runtime_support = derive_runtime_support_report(runtime_authority)
        if (
            len(packaged_runtime_evidence) != 1
            or packaged_runtime_evidence[0]["packaging_status"] != "verified"
            or packaged_runtime_support["dimensions"]["release"] != "blocked"
            or packaged_runtime_support["supported"]
            or runtime_authority.document["package_evidence"] is None
            or runtime_authority.document["native_status"] != "unavailable"
        ):
            _fail(
                "packaging_evidence_authority_invalid",
                f"{case_id} exact packaging authority projection is invalid",
            )
        bind_lineage("extract", first_package_manifest["lineage"]["gamepack_hash"])
        stages.append(_passed_stage("extract"))
        extracted_root = work_root / "extracted" / case_id
        extracted_verified = verify_standalone_game(
            extracted_root,
            expected_content_hash=standalone_manifest["content_hash"],
        )
        extracted_verified.close()
        extracted_independent = _checked_subprocess_json(
            (sys.executable, "-I", str(extracted_root / "scripts/verify_game.py")),
            cwd=work_root,
            environment=environment,
        )
        _validate_independent_verifier(
            extracted_independent,
            game_root=extracted_root,
            standalone_manifest=standalone_manifest,
            runtime_bundle_hash=runtime_manifest["content_hash"],
        )
        bind_lineage("extracted_verify", standalone_manifest["lineage"]["gamepack_hash"])
        stages.append(_passed_stage("extracted_verify"))
        extracted_persistence = _run_persistence_workflow(
            case_id,
            extracted_root,
            case_root,
            work_root / "extracted-user-data" / case_id,
            extracted_root / RUNTIME_BUNDLE_ROOT,
            runtime_manifest["content_hash"],
            environment,
        )
        if extracted_persistence != persistence:
            _fail("extracted_headless_mismatch", f"{case_id} extracted behavior drifted")
        bind_lineage("extracted_headless", standalone_manifest["lineage"]["gamepack_hash"])
        stages.append(_passed_stage("extracted_headless"))

        native_failure = None
        if native_mode == "off":
            native_evidence = native_untested_evidence("off")
            stages.append(
                {"reason_code": "native_disabled", "stage": "native", "state": "untested"}
            )
        elif selected_lock is None or host["architecture"] != "x86_64":
            reason = "native_platform_unsupported"
            native_evidence = {
                **native_untested_evidence("off"),
                "reason_code": reason,
                "state": "unavailable",
            }
            stages.append({"reason_code": reason, "stage": "native", "state": "unavailable"})
        else:
            try:
                smoke = _run_extracted_native_smoke(
                    extracted_root=extracted_root,
                    ingress_root=work_root / "native-smoke-ingress",
                    evidence_root=work_root / "native-smoke-staging",
                    case_id=case_id,
                    environment=environment,
                    adapter_id=implementation["adapter"]["adapter_id"],
                    adapter_version=implementation["adapter"]["adapter_version"],
                    platform_id=host["platform_id"],
                    evidence_collector=native_evidence_records,
                )
            except MultigenreReleaseError as exc:
                reason = "native_execution_failed"
                native_failure = _NativeSmokeFailure(
                    exc.reason_code,
                    f"native-smoke-evidence/{case_id}",
                )
                native_evidence = {
                    **native_untested_evidence("off"),
                    "reason_code": reason,
                    "state": "failed",
                }
                stages.append({"reason_code": reason, "stage": "native", "state": "failed"})
            else:
                native_evidence = {
                    "adapter_id": smoke["adapter_id"],
                    "adapter_version": smoke["adapter_version"],
                    "extracted_runtime_bundle_hash": runtime_manifest["content_hash"],
                    "frames": smoke["frames"],
                    "gamepack_hash": gamepack_hash,
                    "platform_lock_hash": selected_lock["content_hash"],
                    "platform_lock_id": selected_lock["lock_id"],
                    "reason_code": None,
                    "runtime_artifact_sha256": selected_lock["dependency"]["artifact"]["sha256"],
                    "state": "passed",
                }
                stages.append(_passed_stage("native"))
    finally:
        standalone.close()

    hashes = {
        "analysis": analysis["content_hash"],
        "assetpack": assetpack_manifest["content_hash"],
        "capability_ledger": ledger["content_hash"],
        "gamepack": gamepack_hash,
        "materialization_bundle": materialization_manifest["content_hash"],
        "package": first_package_manifest["content_hash"],
        "package_archive": first_archive_sha256,
        "runtime_bundle": runtime_manifest["content_hash"],
        "runtime_support_authority": initial_runtime_authority["content_hash"],
        "runtime_support_report": initial_support["content_hash"],
        "standalone_game": standalone_manifest["content_hash"],
    }
    report = {
        "case_id": case_id,
        "status": "passed" if native_evidence["state"] in {"passed", "untested"} else "failed",
        "stages": stages,
        "hashes": hashes,
        "lineage": lineage,
        "identities": {
            "adapter_id": implementation["adapter"]["adapter_id"],
            "adapter_version": implementation["adapter"]["adapter_version"],
            "assetpack_id": assetpack_manifest["assetpack_id"],
            "materialization_bundle_id": materialization_manifest["materialization_bundle_id"],
            "package_id": first_package_manifest["package_id"],
            "runtime_bundle_id": runtime_manifest["bundle_id"],
            "runtime_support_authority_id": initial_runtime_authority["authority_id"],
            "runtime_support_report_id": initial_support["report_id"],
            "standalone_game_id": standalone_manifest["game_id"],
        },
        "native_evidence": native_evidence,
        "persistence": persistence,
    }
    return _CaseRunResult(report=report, native_failure=native_failure)


def _native_failure_detail(results: Sequence[_CaseRunResult]) -> str:
    return "; ".join(
        f"{result.report['case_id']}: {result.native_failure.reason_code} "
        f"[{result.native_failure.evidence_path}]"
        for result in results
        if result.native_failure is not None
    )


def _raise_native_evidence_publish_failure(
    failure: tuple[str, str],
    *,
    primary_native_detail: str,
) -> None:
    if primary_native_detail:
        _fail(
            failure[0],
            f"{primary_native_detail}; diagnostics: {failure[0]}",
        )
    _fail(*failure)


def run_release_gate(
    *,
    source_root: Path,
    report_path: Path | None,
    work_root: Path,
    native_mode: str,
    runtime_wheel: Path | None = None,
    native_evidence_parent: Path | None = None,
    github_output_path: Path | None = None,
) -> dict[str, Any]:
    source_root = source_root.resolve(strict=True)
    host = _host_context()
    if not isinstance(native_mode, str) or native_mode not in {"off", "optional", "required"}:
        _fail("native_mode_invalid", f"unsupported native mode: {native_mode!r}")
    if native_mode == "required":
        require_native_host(native_mode, host)
    require_headless_host(host)
    if native_mode == "off" and runtime_wheel is not None:
        _fail("release_cli_arguments_invalid", "native-off mode does not accept a runtime wheel")
    if native_mode == "required":
        if runtime_wheel is None:
            _fail(
                "native_runtime_artifact_missing",
                "native execution requires the exact locked raylib wheel",
            )
        expected_lock = _expected_platform_lock(host)
        if expected_lock is None:
            _fail(
                "native_platform_unsupported",
                f"native raylib evidence is not declared for {host['os']}/{host['architecture']}",
            )
        verify_runtime_wheel(runtime_wheel, expected_lock)
    source_identity = _source_context(source_root)
    work_root = work_root.absolute()
    if report_path is not None:
        report_path = report_path.absolute()
    if (report_path is not None and _inside(report_path, source_root)) or _inside(
        work_root, source_root
    ):
        _fail("release_output_inside_repository", "report and work roots must be external")
    if (report_path is not None and os.path.lexists(report_path)) or os.path.lexists(work_root):
        _fail("release_output_exists", "report and work roots must not exist")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if _inside(report_path.parent.resolve(strict=True) / report_path.name, source_root):
            _fail("release_output_inside_repository", "report must resolve outside the repository")
    work_root.parent.mkdir(parents=True, exist_ok=True)
    if _inside(work_root.parent.resolve(strict=True) / work_root.name, source_root):
        _fail("release_output_inside_repository", "work root must resolve outside the repository")
    work_root.mkdir(parents=True)
    for relative in (
        "assetpacks",
        "runtime-bundles",
        "materialization-bundles",
        "standalone",
        "packages-first",
        "packages-second",
        "extracted",
        "native-smoke-ingress",
        "native-smoke-staging",
    ):
        (work_root / relative).mkdir()
    authority = capture_release_inputs(source_root)
    fixture_root = work_root / "source-input"
    materialize_release_input_subtree(authority, "runtime", fixture_root / "runtime")
    input_tree_hash = authority.tree_hash
    lock_root = fixture_root / "runtime/platform-locks"
    platform_locks = [load_runtime_platform_lock(path) for path in sorted(lock_root.glob("*.json"))]
    selected_host_lock = _select_platform_lock(platform_locks, host)
    runtime_artifact = None
    if native_mode != "off" and selected_host_lock is not None:
        if runtime_wheel is None:
            _fail(
                "native_runtime_artifact_missing",
                "native execution requires the exact locked raylib wheel",
            )
        runtime_artifact = verify_runtime_wheel(runtime_wheel, selected_host_lock)
    elif runtime_wheel is not None:
        _fail(
            "native_runtime_artifact_mismatch",
            "runtime wheel has no selected host platform lock",
        )
    toolchain = _toolchain_context(runtime_artifact)
    if native_mode != "off" and selected_host_lock is not None:
        if (
            toolchain["raylib"] != selected_host_lock["dependency"]["version"]
            or toolchain["pillow"] != _EXPECTED_TOOLCHAIN["pillow"]
            or toolchain["world_forge"] != _EXPECTED_TOOLCHAIN["world_forge"]
        ):
            _fail("native_toolchain_mismatch", "installed toolchain differs from platform lock")
    case_results = []
    native_evidence_records: dict[str, dict[str, bytes]] = {}
    for case_id in CASES:
        materialize_release_input_subtree(authority, case_id, fixture_root / case_id)
        case_results.append(
            _run_case(
                case_id=case_id,
                fixture_root=fixture_root,
                work_root=work_root,
                host=host,
                native_mode=native_mode,
                platform_locks=platform_locks,
                native_evidence_records=native_evidence_records,
            )
        )
    cases = [result.report for result in case_results]
    if _source_context(source_root) != source_identity:
        _fail("release_source_changed", "repository identity changed during verification")
    native_states = {case["native_evidence"]["state"] for case in cases}
    if native_mode == "required" and native_states != {"passed"}:
        status = "failed"
        failures = sorted(
            {
                case["native_evidence"]["reason_code"]
                for case in cases
                if case["native_evidence"]["reason_code"] is not None
            }
        )
    elif native_mode == "optional" and native_states != {"passed"}:
        status = "completed_with_native_gap"
        failures = sorted(
            {
                case["native_evidence"]["reason_code"]
                for case in cases
                if case["native_evidence"]["reason_code"] is not None
            }
        )
    else:
        status = "passed"
        failures = []
    report = validate_release_report(
        {
            "format": REPORT_FORMAT,
            "format_version": REPORT_VERSION,
            "status": status,
            "source": {**source_identity, "input_tree_hash": input_tree_hash},
            "toolchain": toolchain,
            "host": host,
            "native_mode": native_mode,
            "cases": cases,
            "failure_reasons": failures,
        }
    )
    primary_native_detail = _native_failure_detail(case_results)
    if report_path is not None:
        publish_operational_report(report_path, report, source_root=source_root)
    evidence_publish_failure: tuple[str, str] | None = None
    if native_mode != "off" and _has_complete_native_smoke_evidence_records(
        native_evidence_records
    ):
        try:
            evidence_root = _publish_native_smoke_evidence_closure(
                native_evidence_parent or Path(tempfile.gettempdir()),
                native_evidence_records,
                forbidden_roots=(
                    source_root,
                    work_root,
                    work_root / "extracted",
                    work_root / "native-smoke-ingress",
                    work_root / "native-smoke-staging",
                ),
            )
            if github_output_path is not None:
                release_row_path = _publish_trusted_native_release_row(evidence_root, report)
                _publish_native_smoke_github_output(
                    github_output_path,
                    evidence_root,
                    release_row_path,
                )
        except MultigenreReleaseError as exc:
            evidence_publish_failure = (exc.reason_code, str(exc))
    if evidence_publish_failure is not None and not primary_native_detail:
        _raise_native_evidence_publish_failure(
            evidence_publish_failure,
            primary_native_detail=primary_native_detail,
        )
    if status == "failed":
        detail = primary_native_detail or ", ".join(failures)
        if evidence_publish_failure is not None:
            detail = f"{detail}; diagnostics: {evidence_publish_failure[0]}"
        elif github_output_path is not None:
            raise _TrustedNativeEvidenceFailure(
                "native_required_incomplete",
                detail,
            )
        _fail(
            "native_required_incomplete",
            detail,
        )
    if evidence_publish_failure is not None:
        _raise_native_evidence_publish_failure(
            evidence_publish_failure,
            primary_native_detail=primary_native_detail,
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--native", choices=("off", "optional", "required"), default="off")
    parser.add_argument("--aggregate", nargs="+", type=Path)
    parser.add_argument("--runtime-wheel", type=Path)
    parser.add_argument("--verify-runtime-wheel", type=Path)
    args = parser.parse_args(argv)
    try:
        native_evidence_parent, github_output_path = _native_smoke_ci_publication_context(
            os.environ,
            native_mode=args.native,
        )
        if args.aggregate:
            if (
                args.work_root is not None
                or args.native != "off"
                or args.runtime_wheel is not None
                or args.verify_runtime_wheel is not None
            ):
                _fail(
                    "release_cli_arguments_invalid",
                    "aggregate mode accepts only report inputs",
                )
            if args.report is None:
                _fail("release_cli_arguments_invalid", "aggregate mode requires --report")
            reports = [load_release_report(path) for path in args.aggregate]
            result = aggregate_release_reports(reports)
            publish_operational_report(
                args.report,
                result,
                source_root=Path(__file__).resolve().parents[1],
            )
        elif args.verify_runtime_wheel is not None:
            if args.work_root is not None or args.native != "off" or args.runtime_wheel is not None:
                _fail(
                    "release_cli_arguments_invalid",
                    "wheel verification does not accept release execution options",
                )
            if args.report is None:
                _fail("release_cli_arguments_invalid", "wheel verification requires --report")
            source_root = Path(__file__).resolve().parents[1]
            host = _host_context()
            require_native_host("required", host)
            selected_lock = _expected_platform_lock(host)
            if selected_lock is None:
                _fail("native_platform_unsupported", "host has no audited platform lock")
            artifact = verify_runtime_wheel(args.verify_runtime_wheel, selected_lock)
            result = {
                "artifact": artifact,
                "format": "world-forge.runtime_wheel_attestation",
                "format_version": 1,
                "host": host,
            }
            publish_operational_report(args.report, result, source_root=source_root)
        else:
            if args.report is None and github_output_path is None:
                _fail(
                    "release_cli_arguments_invalid",
                    "release execution requires --report outside trusted native CI",
                )
            source_root = Path(__file__).resolve().parents[1]
            work_root = args.work_root
            if work_root is None:
                with tempfile.TemporaryDirectory(prefix="wf-multigenre-release-") as temporary:
                    result = run_release_gate(
                        source_root=source_root,
                        report_path=args.report,
                        work_root=Path(temporary) / "work",
                        native_mode=args.native,
                        runtime_wheel=args.runtime_wheel,
                        native_evidence_parent=native_evidence_parent,
                        github_output_path=github_output_path,
                    )
            else:
                result = run_release_gate(
                    source_root=source_root,
                    report_path=args.report,
                    work_root=work_root,
                    native_mode=args.native,
                    runtime_wheel=args.runtime_wheel,
                    native_evidence_parent=native_evidence_parent,
                    github_output_path=github_output_path,
                )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except _TrustedNativeEvidenceFailure as exc:
        print(
            json.dumps(
                {"detail": str(exc), "reason_code": exc.reason_code, "status": "failed"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 0
    except (MultigenreReleaseError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason_code", "multigenre_release_failed")
        print(
            json.dumps(
                {"detail": str(exc), "reason_code": reason, "status": "failed"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
