"""Bounded native smoke entry point with explicit host admission."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from gamepack_raylib_2d.app import RuntimeApp
from gamepack_raylib_2d.audit import AdapterBoundaryError, audit_adapter_boundary
from gamepack_raylib_2d.backend import PyrayBackend


class NativeSmokeError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _machine() -> str:
    if os.name != "nt":
        try:
            return os.uname().machine.casefold()
        except (AttributeError, OSError):
            return "unknown"

    class _SystemInfo(ctypes.Structure):
        _fields_ = [
            ("processor_architecture", ctypes.c_ushort),
            ("reserved", ctypes.c_ushort),
            ("page_size", ctypes.c_ulong),
            ("minimum_application_address", ctypes.c_void_p),
            ("maximum_application_address", ctypes.c_void_p),
            ("active_processor_mask", ctypes.c_size_t),
            ("number_of_processors", ctypes.c_ulong),
            ("processor_type", ctypes.c_ulong),
            ("allocation_granularity", ctypes.c_ulong),
            ("processor_level", ctypes.c_ushort),
            ("processor_revision", ctypes.c_ushort),
        ]

    try:
        information = _SystemInfo()
        ctypes.windll.kernel32.GetNativeSystemInfo(ctypes.byref(information))
    except (AttributeError, OSError):
        return "unknown"
    return {
        0: "x86",
        5: "arm",
        6: "ia64",
        9: "x86_64",
        12: "arm64",
    }.get(int(information.processor_architecture), "unknown")


def _platform_id() -> str:
    if sys.platform.startswith("linux"):
        family = "linux"
    elif os.name == "nt":
        family = "windows"
    else:
        raise NativeSmokeError(
            "native_platform_unsupported",
            f"unsupported operating system: {sys.platform}",
        )
    machine = _machine()
    if machine not in {"x86_64", "amd64"}:
        raise NativeSmokeError(
            "native_platform_unsupported",
            f"native raylib evidence is not declared for {family}/{machine}",
        )
    return f"platform:{family}_x86_64"


def native_smoke(
    bundle_root: str | Path,
    *,
    max_frames: int = 2,
    hidden: bool = True,
) -> dict[str, object]:
    """Exercise a bounded native session only on a declared host target."""

    if type(max_frames) is not int or not 1 <= max_frames <= 120:
        raise NativeSmokeError(
            "native_smoke_input_invalid",
            "max_frames must be an exact integer from 1 through 120",
        )
    if type(hidden) is not bool:
        raise NativeSmokeError(
            "native_smoke_input_invalid",
            "hidden must be an exact boolean",
        )
    platform_id = _platform_id()
    try:
        audit = audit_adapter_boundary(Path(__file__).parent)
    except AdapterBoundaryError as exc:
        raise NativeSmokeError("native_boundary_failed", str(exc)) from exc
    if audit["violations"]:
        raise NativeSmokeError(
            "native_boundary_failed",
            "adapter boundary audit reported violations",
        )
    try:
        app = RuntimeApp.from_bundle(
            bundle_root,
            backend=PyrayBackend(),
            hidden=hidden,
        )
        try:
            completed_frames = app.run(max_frames=max_frames)
            if completed_frames < 1:
                raise NativeSmokeError(
                    "native_execution_incomplete",
                    "native smoke closed before completing one frame",
                )
            return {
                "adapter_id": app.implementation.adapter_id,
                "adapter_version": app.implementation.adapter_version,
                "platform_id": platform_id,
                "frames": completed_frames,
                "status": "native_smoke_executed",
            }
        finally:
            app.close()
    except NativeSmokeError:
        raise
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise NativeSmokeError("native_execution_failed", str(exc)) from exc


__all__ = ["NativeSmokeError", "native_smoke"]
