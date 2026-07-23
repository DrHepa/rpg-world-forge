from __future__ import annotations

import json
import signal
import subprocess
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
RELEASE_PATH = sys.argv[2] if len(sys.argv) > 2 else ""

if MODE == "backpressure":
    time.sleep(0.25)
if MODE == "stalled":
    time.sleep(60)


def write_bytes(payload: bytes) -> None:
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


for raw_line in sys.stdin.buffer:
    try:
        request = json.loads(raw_line)
    except json.JSONDecodeError:
        write_bytes(b"{broken\n")
        continue

    if MODE == "malformed":
        write_bytes(b"{broken\n")
        continue
    if MODE == "oversized":
        write_bytes(("x" * 512 + "\n").encode())
        continue
    if MODE == "silent":
        continue
    if MODE == "crash":
        raise SystemExit(17)

    response = (
        json.dumps(
            {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": 1,
                "kind": "response",
                "request_id": request["request_id"],
                "method": ("workspace.list" if MODE == "mismatched-method" else request["method"]),
                "result": {
                    "service": "rpg-world-forge.studio",
                    "service_version": 1,
                    "protocol": "rpg-world-forge.studio_protocol",
                    "protocol_version": 1,
                    "methods": ["service.initialize"],
                    "capabilities": {},
                },
            },
            separators=(",", ":"),
        )
        + "\n"
    ).encode()

    if MODE == "event":
        write_bytes(
            (
                json.dumps(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 1,
                        "kind": "event",
                        "request_id": None,
                        "event": {"type": "fixture.ready"},
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()
        )
    if MODE == "stderr":
        sys.stderr.write("e" * 512)
        sys.stderr.flush()
    if MODE == "split":
        write_bytes(response[:13])
        time.sleep(0.01)
        write_bytes(response[13:])
        continue
    if MODE == "delayed":
        time.sleep(0.4)
    write_bytes(response)


if MODE in {"eof", "delayed", "hang-after-eof"}:
    sys.stderr.write("fixture.eof\n")
    sys.stderr.flush()

if MODE in {"descendant-after-eof", "descendant-ignore-term-after-eof"}:
    ignore_term = MODE == "descendant-ignore-term-after-eof"
    release_loop = (
        "while not release.exists() and time.monotonic() < deadline:\n    time.sleep(0.05)"
    )
    descendant = (
        "import pathlib,signal,sys,time;"
        + (
            "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
            if ignore_term and hasattr(signal, "SIGTERM")
            else ""
        )
        + "sys.stderr.write('fixture.descendant-ready\\n');sys.stderr.flush();"
        + f"release=pathlib.Path({RELEASE_PATH!r});deadline=time.monotonic()+30;"
        + f"exec({release_loop!r})"
    )
    subprocess.Popen([sys.executable, "-c", descendant])
    sys.stderr.write("fixture.root-exited\n")
    sys.stderr.flush()

if MODE == "hang-after-eof":
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
