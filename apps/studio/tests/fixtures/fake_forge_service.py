from __future__ import annotations

import json
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"

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
