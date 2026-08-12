from __future__ import annotations

import json
import signal
import subprocess
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
RELEASE_PATH = sys.argv[2] if len(sys.argv) > 2 else ""
V1_METHODS = [
    "service.initialize",
    "workspace.register",
    "workspace.list",
    "workspace.get",
    "workspace.overview",
    "source.list",
    "source.read",
    "asset.catalog.list",
    "asset.catalog.inspect",
    "asset.preview.open",
    "asset.preview.read",
    "asset.preview.close",
    "world.validate",
    "world.analyze",
    "events.list",
    "changeset.create",
    "changeset.get",
    "changeset.list",
    "changeset.diff",
    "changeset.approve",
    "changeset.reject",
    "changeset.apply",
    "job.create",
    "job.get",
    "job.list",
    "job.transition",
    "job.cancel",
]
V3_METHODS = sorted(
    [
        "service.initialize",
        "creation_root_grant.create",
        "creation_root_grant.get",
        "creation_root_grant.revoke",
        "creation_workspace.create",
        "creation_workspace.recover",
        "creation_workspace.register",
        "creation_workspace.get",
        "creation_workspace.list",
        "creation_workspace.open",
        "creation_document.list",
        "creation_document.read",
        "creation_changeset.create",
        "creation_changeset.get",
        "creation_changeset.list",
        "creation_changeset.diff",
        "creation_changeset.approve",
        "creation_changeset.reject",
        "creation_changeset.apply",
        "creation_changeset.recover",
        "creation_workflow.get",
        "creation_workflow.reconcile",
        "creation_phase.read",
        "creation_phase.validate",
        "creation_phase.complete",
        "creation_phase.reopen",
        "creation_readiness.inspect",
    ]
)
V4_METHODS = [
    "service.initialize",
    "creation_artifact.list",
    "creation_artifact.inspect",
    "creation_evidence.inspect",
    "creation_output_grant.create",
    "creation_output_grant.get",
    "creation_output_grant.list",
    "creation_output_grant.revoke",
    "creation_preview.open",
    "creation_preview.read",
    "creation_preview.close",
    "creation_job.create",
    "creation_job.get",
    "creation_job.list",
    "creation_job.cancel",
    "creation_job.recover",
    "creation_event.list",
]
V5_METHODS = sorted([*V4_METHODS, "creation_workspace.create"])

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

    request_protocol_version = request.get("protocol_version", 1)
    response_protocol_version = 1 if MODE == "mismatched-version" else request_protocol_version
    initialize_result = {
        "service": "rpg-world-forge.studio",
        "service_version": response_protocol_version,
        "protocol": "rpg-world-forge.studio_protocol",
        "protocol_version": response_protocol_version,
        "methods": V1_METHODS,
        "capabilities": {},
    }
    if response_protocol_version == 3:
        initialize_result = {
            "service": "world-forge.studio",
            "service_version": 3,
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 3,
            "methods": V3_METHODS,
            "capabilities": {
                "generic_creation": True,
                "safe_project_creation": True,
                "read_only_documents": True,
                "profile_editing": True,
                "generic_jobs": False,
                "reviewed_changesets": True,
                "workflow_mutations": True,
                "inline_phase_reports": True,
            },
        }
    if response_protocol_version == 4:
        initialize_result = {
            "service": "world-forge.studio",
            "service_version": 4,
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 4,
            "methods": V4_METHODS,
            "capabilities": {
                "creation_evidence_projection": True,
                "creation_jobs": True,
                "creation_output_grants": True,
                "creation_runtime_compose": True,
                "creation_runtime_bundle": True,
                "creation_materialization_bundle": True,
                "creation_asset_previews": True,
                "game_packaging": True,
                "game_package_extraction": True,
                "asset_previews": False,
                "materialization_execution": True,
            },
        }
        if MODE == "incompatible-v4":
            initialize_result["capabilities"]["creation_jobs"] = False
    if response_protocol_version == 5:
        initialize_result = {
            "service": "world-forge.studio",
            "service_version": 5,
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 5,
            "methods": V5_METHODS,
            "capabilities": {
                "creation_evidence_projection": True,
                "creation_jobs": True,
                "creation_output_grants": True,
                "creation_runtime_compose": True,
                "creation_runtime_bundle": True,
                "creation_materialization_bundle": True,
                "creation_asset_previews": True,
                "game_packaging": True,
                "game_package_extraction": True,
                "asset_previews": False,
                "materialization_execution": True,
                "asset_authority_reviews": True,
                "asset_release_authority": True,
                "runtime_headless_authority": True,
                "creation_preview_pre_release": True,
            },
        }
        if MODE == "incompatible-v5":
            initialize_result["capabilities"]["runtime_headless_authority"] = False
    response = (
        json.dumps(
            {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": response_protocol_version,
                "kind": "response",
                "request_id": request["request_id"],
                "method": ("workspace.list" if MODE == "mismatched-method" else request["method"]),
                "result": initialize_result,
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
