#!/usr/bin/env python3
"""Verify canonical multi-genre release lineage and hosted native evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from gamepack_runtime.distribution import RUNTIME_BUNDLE_ROOT
from scripts.generate_generic_asset_fixtures import build_fixture_documents
from worldforge import __version__ as WORLD_FORGE_VERSION
from worldforge.__main__ import _resolve_generic_assetpack_cli_source
from worldforge.asset_io import (
    AssetContractError,
    read_bound_bytes,
    write_bytes_atomic,
    write_json_atomic,
)
from worldforge.creation_contracts import read_creation_object
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
from worldforge.retained_tree import RetainedTreeError, RetainedTreeSnapshot, capture_retained_tree
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


def _checked_subprocess_json(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float = _PROCESS_TIMEOUT_SECONDS,
    output_limit: int = _MAX_PROCESS_OUTPUT_BYTES,
) -> dict[str, Any]:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 < timeout_seconds <= _PROCESS_TIMEOUT_SECONDS
        or isinstance(output_limit, bool)
        or not isinstance(output_limit, int)
        or not 1 <= output_limit <= _MAX_PROCESS_OUTPUT_BYTES
    ):
        raise ValueError("subprocess bounds are invalid")
    process = subprocess.Popen(
        list(arguments),
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
        _fail("standalone_execution_failed", "subprocess pipes were unavailable")

    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()

    def read_bounded(stream: Any, target: bytearray) -> None:
        try:
            while chunk := stream.read(65536):
                remaining = output_limit + 1 - len(target)
                target.extend(chunk[:remaining])
                if len(target) > output_limit:
                    overflow.set()
                    stream.close()
                    return
        finally:
            try:
                stream.close()
            except OSError:
                pass

    readers = (
        threading.Thread(target=read_bounded, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=read_bounded, args=(process.stderr, stderr), daemon=True),
    )
    for reader in readers:
        reader.start()
    try:
        return_code = process.wait(timeout=float(timeout_seconds))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        for reader in readers:
            reader.join()
        _fail("standalone_execution_timeout", "standalone subprocess exceeded its deadline")
    for reader in readers:
        reader.join()
    if overflow.is_set():
        _fail("standalone_output_too_large", "standalone subprocess output exceeded its bound")
    if return_code != 0:
        _fail(
            "standalone_execution_failed",
            f"{Path(arguments[2]).name if len(arguments) > 2 else 'command'}: "
            f"{bytes(stderr).decode('utf-8', errors='replace').strip()}",
        )
    return _decode_json_object(
        bytes(stdout),
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


def _run_extracted_native_smoke(
    *,
    extracted_root: Path,
    environment: Mapping[str, str],
    adapter_id: str,
    adapter_version: str,
    platform_id: str,
) -> dict[str, Any]:
    report = _checked_subprocess_json(
        (sys.executable, "-I", str(extracted_root / "scripts/native_smoke.py")),
        cwd=extracted_root.parent,
        environment=environment,
    )
    fields = {"adapter_id", "adapter_version", "frames", "platform_id", "status"}
    if (
        set(report) != fields
        or report.get("status") != "native_smoke_executed"
        or report.get("adapter_id") != adapter_id
        or report.get("adapter_version") != adapter_version
        or report.get("platform_id") != platform_id
        or type(report.get("frames")) is not int
        or not 1 <= report["frames"] <= 120
    ):
        _fail("standalone_execution_invalid", "native smoke output is not exact")
    return report


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
) -> dict[str, Any]:
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
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONHOME", "PYTHONPATH"}
        }
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
                    environment=environment,
                    adapter_id=implementation["adapter"]["adapter_id"],
                    adapter_version=implementation["adapter"]["adapter_version"],
                    platform_id=host["platform_id"],
                )
            except MultigenreReleaseError:
                reason = "native_execution_failed"
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
    return {
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


def run_release_gate(
    *,
    source_root: Path,
    report_path: Path,
    work_root: Path,
    native_mode: str,
    runtime_wheel: Path | None = None,
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
    report_path = report_path.absolute()
    work_root = work_root.absolute()
    if _inside(report_path, source_root) or _inside(work_root, source_root):
        _fail("release_output_inside_repository", "report and work roots must be external")
    if os.path.lexists(report_path) or os.path.lexists(work_root):
        _fail("release_output_exists", "report and work roots must not exist")
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
    cases = []
    for case_id in CASES:
        materialize_release_input_subtree(authority, case_id, fixture_root / case_id)
        cases.append(
            _run_case(
                case_id=case_id,
                fixture_root=fixture_root,
                work_root=work_root,
                host=host,
                native_mode=native_mode,
                platform_locks=platform_locks,
            )
        )
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
    publish_operational_report(report_path, report, source_root=source_root)
    if status == "failed":
        _fail("native_required_incomplete", ", ".join(failures))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--native", choices=("off", "optional", "required"), default="off")
    parser.add_argument("--aggregate", nargs="+", type=Path)
    parser.add_argument("--runtime-wheel", type=Path)
    parser.add_argument("--verify-runtime-wheel", type=Path)
    args = parser.parse_args(argv)
    try:
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
                    )
            else:
                result = run_release_gate(
                    source_root=source_root,
                    report_path=args.report,
                    work_root=work_root,
                    native_mode=args.native,
                    runtime_wheel=args.runtime_wheel,
                )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
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
