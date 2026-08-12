from __future__ import annotations

import ctypes
import os
import signal
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class CreationProcessError(RuntimeError):
    """A creation worker process could not be identified or contained safely."""


def _linux_process_identity(pid: int) -> dict[str, Any] | None:
    try:
        payload = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CreationProcessError("Could not inspect the Linux creation worker") from exc
    closing = payload.rfind(")")
    if closing < 0:
        raise CreationProcessError("Linux creation worker identity is malformed")
    fields = payload[closing + 2 :].split()
    if len(fields) < 20:
        raise CreationProcessError("Linux creation worker identity is incomplete")
    try:
        process_group_id = int(fields[2])
        session_id = int(fields[3])
        start_time_ticks = int(fields[19])
    except ValueError as exc:
        raise CreationProcessError("Linux creation worker identity is invalid") from exc
    if fields[0] == "Z":
        return None
    return {
        "platform": "linux",
        "pid": pid,
        "process_group_id": process_group_id,
        "session_id": session_id,
        "start_time_ticks": start_time_ticks,
    }


def _windows_process_identity(pid: int) -> dict[str, Any] | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = open_process(0x1000 | 0x00100000, 0, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error in {5, 87, 1168}:
            if error == 1168:
                return None
            raise CreationProcessError("Windows creation worker cannot be inspected")
        return None
    try:
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        get_times = kernel32.GetProcessTimes
        get_times.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
            ctypes.POINTER(ctypes.c_ulonglong),
        ]
        get_times.restype = ctypes.c_int
        if not get_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise CreationProcessError("Windows creation worker identity is unavailable")
        if exit_time.value:
            return None
        return {
            "platform": "windows",
            "pid": pid,
            "creation_time": int(creation.value),
        }
    finally:
        close_handle(ctypes.c_void_p(handle))


def creation_process_identity(pid: int) -> dict[str, Any]:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise CreationProcessError("Creation worker pid is invalid")
    identity = (
        _linux_process_identity(pid)
        if sys_platform_linux()
        else _windows_process_identity(pid)
        if os.name == "nt"
        else None
    )
    if identity is None:
        raise CreationProcessError("Creation worker exited before identity registration")
    return identity


def sys_platform_linux() -> bool:
    import sys

    return sys.platform.startswith("linux") and os.name == "posix"


def terminate_registered_creation_process(
    pid: int,
    expected: Mapping[str, Any],
    *,
    grace_seconds: float = 2.0,
) -> None:
    if expected.get("pid") != pid:
        raise CreationProcessError("Stored creation worker pid binding changed")
    current = (
        _linux_process_identity(pid)
        if sys_platform_linux()
        else _windows_process_identity(pid)
        if os.name == "nt"
        else None
    )
    if current is None or dict(expected) != current:
        return
    if current["platform"] == "linux":
        if current["process_group_id"] != pid or current["session_id"] != pid:
            raise CreationProcessError("Linux creation worker containment changed")
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if _linux_process_identity(pid) is None:
                return
            time.sleep(0.025)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            if _linux_process_identity(pid) is None:
                return
            time.sleep(0.025)
        raise CreationProcessError("Linux creation worker did not terminate")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    terminate_process = kernel32.TerminateProcess
    terminate_process.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    terminate_process.restype = ctypes.c_int
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    wait_for_single_object.restype = ctypes.c_uint32
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = open_process(0x0001 | 0x00100000, 0, pid)
    if not handle:
        if ctypes.get_last_error() == 1168:
            return
        raise CreationProcessError("Windows creation worker cannot be reopened")
    try:
        bound_handle = ctypes.c_void_p(handle)
        if not terminate_process(bound_handle, 1):
            raise CreationProcessError("Windows creation worker could not be terminated")
        if wait_for_single_object(bound_handle, int(grace_seconds * 1000)) == 0x00000102:
            raise CreationProcessError("Windows creation worker did not terminate")
    finally:
        close_handle(ctypes.c_void_p(handle))
