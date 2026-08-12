"""Installable multi-genre release evidence contract validation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from worldforge.integrity import canonical_json_bytes
from worldforge.runtime_platform_lock import (
    build_builtin_runtime_platform_locks,
)

REPORT_FORMAT = "world-forge.multigenre_release_evidence"
AGGREGATE_FORMAT = "world-forge.multigenre_release_aggregate"
REPORT_VERSION = 1
CASES = ("abstract-puzzle", "branching-narrative")
CASE_ADAPTERS = {
    "abstract-puzzle": ("gamepack_raylib_2d_puzzle", "1.1.0"),
    "branching-narrative": ("gamepack_raylib_2d_text", "1.1.0"),
}
REQUIRED_MATRIX = (
    ("linux", "3.11"),
    ("linux", "3.12"),
    ("windows", "3.11"),
    ("windows", "3.12"),
)
REQUIRED_CASE_STAGES = (
    "validate",
    "compile_first",
    "compile_second",
    "analysis",
    "capability_ledger",
    "asset_production",
    "asset_processing",
    "asset_qa",
    "asset_seal",
    "runtime_support",
    "runtime_bundle",
    "materialization_bundle",
    "standalone_materialize",
    "standalone_audit",
    "independent_verify",
    "headless",
    "persistence",
    "package_first",
    "package_second",
    "extract",
    "extracted_verify",
    "extracted_headless",
    "native",
)
LINEAGE_STAGES = REQUIRED_CASE_STAGES[:-1]
_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){2,3}\Z")
_REASON_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,159}\Z")
_NATIVE_FAILURE_REASONS = frozenset(
    {"native_disabled", "native_execution_failed", "native_platform_unsupported"}
)
_MAX_PROCESS_OUTPUT_BYTES = 1024 * 1024
_MAX_RUNTIME_WHEEL_BYTES = 16 * 1024 * 1024
_PROCESS_TIMEOUT_SECONDS = 120.0
_EXPECTED_TOOLCHAIN = {
    "pillow": "12.3.0",
    "raylib": "6.0.1.0",
    "world_forge": "0.7.0",
}
_RUNTIME_AUTHORITY_MARKERS = (
    b"world-forge.runtime_support_authority",
    b"world-forge.hosted_native_release_authority",
    b"world-forge.hosted_native_release_attestation_receipt",
)
_RELEASE_HOST_FIELDS = frozenset(
    {
        "architecture",
        "os",
        "platform_id",
        "python_abi",
        "python_implementation",
        "python_minor",
        "runner_image",
    }
)


@dataclass(frozen=True, slots=True)
class LoadedReleaseReport:
    document: dict[str, Any]
    payload: bytes
    sha256: str


def _version(value: object) -> bool:
    return isinstance(value, str) and _VERSION_PATTERN.fullmatch(value) is not None


class MultigenreReleaseError(RuntimeError):
    """A closed release gate or aggregation failure."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise MultigenreReleaseError(reason_code, detail)


def native_untested_evidence(mode: str) -> dict[str, object]:
    if mode != "off":
        _fail("native_mode_invalid", "untested native evidence is only valid for off mode")
    return {
        "adapter_id": None,
        "adapter_version": None,
        "extracted_runtime_bundle_hash": None,
        "frames": 0,
        "gamepack_hash": None,
        "platform_lock_hash": None,
        "platform_lock_id": None,
        "reason_code": "native_disabled",
        "runtime_artifact_sha256": None,
        "state": "untested",
    }


def require_native_host(mode: str, host: Mapping[str, object]) -> None:
    if not isinstance(mode, str) or mode not in {"off", "optional", "required"}:
        _fail("native_mode_invalid", f"unsupported native mode: {mode!r}")
    if mode == "off":
        return
    if host.get("os") not in {"linux", "windows"} or host.get("architecture") != "x86_64":
        _fail(
            "native_platform_unsupported",
            "native raylib evidence is not declared for "
            f"{host.get('os')}/{host.get('architecture')}",
        )
    if host.get("python_minor") not in {"3.11", "3.12"}:
        _fail(
            "native_platform_unsupported",
            f"unsupported Python minor: {host.get('python_minor')}",
        )


def require_headless_host(host: Mapping[str, object], *, hosted: bool = False) -> None:
    """Require an exact platform row capable of minting v1 headless release evidence."""

    if (
        type(host) is not dict
        or set(host) != _RELEASE_HOST_FIELDS
        or host.get("os") not in {"linux", "windows"}
        or host.get("architecture") != "x86_64"
        or host.get("python_minor") not in {"3.11", "3.12"}
        or host.get("python_implementation") != "cpython"
        or host.get("python_abi") != f"cp{str(host.get('python_minor')).replace('.', '')}"
        or host.get("runner_image") not in {"local", "ubuntu-24.04", "windows-2022"}
        or host.get("platform_id") != f"platform:{host.get('os')}_{host.get('architecture')}"
        or _expected_platform_lock(host) is None
    ):
        _fail("release_report_host_invalid", "host identity is invalid")
    expected_runner = "ubuntu-24.04" if host["os"] == "linux" else "windows-2022"
    if hosted and host["runner_image"] != expected_runner:
        _fail("release_report_host_invalid", "hosted evidence requires the exact runner image")


def _expected_platform_lock(host: Mapping[str, object]) -> dict[str, Any] | None:
    matches = [
        lock
        for lock in build_builtin_runtime_platform_locks()
        if lock["platform"]["os"] == host.get("os")
        and lock["platform"]["architecture"] == host.get("architecture")
        and lock["python"]["minor"] == host.get("python_minor")
    ]
    return matches[0] if len(matches) == 1 else None


def _runtime_artifact_identity(lock: Mapping[str, object]) -> dict[str, object]:
    dependency = lock.get("dependency")
    artifact = dependency.get("artifact") if isinstance(dependency, Mapping) else None
    if not isinstance(artifact, Mapping):
        _fail("native_platform_lock_invalid", "platform lock has no runtime artifact")
    return {
        "filename": artifact.get("filename"),
        "platform_lock_hash": lock.get("content_hash"),
        "platform_lock_id": lock.get("lock_id"),
        "sha256": artifact.get("sha256"),
        "size_bytes": artifact.get("size_bytes"),
    }


def validate_release_report(
    value: object,
    *,
    hosted: bool = False,
) -> dict[str, Any]:
    """Validate one closed operational report without publishing a public schema."""

    if type(value) is not dict:
        _fail("release_report_invalid", "report root must be an object")
    report = copy.deepcopy(value)
    expected_fields = {
        "format",
        "format_version",
        "status",
        "source",
        "toolchain",
        "host",
        "native_mode",
        "cases",
        "failure_reasons",
    }
    if set(report) != expected_fields:
        _fail("release_report_fields_invalid", "report fields are not the closed v1 set")
    if (
        report["format"] != REPORT_FORMAT
        or type(report["format_version"]) is not int
        or report["format_version"] != REPORT_VERSION
    ):
        _fail("release_report_invalid", "report format/version is unsupported")
    source = report["source"]
    if (
        type(source) is not dict
        or set(source) != {"input_tree_hash", "revision", "tree_state"}
        or not isinstance(source.get("revision"), str)
        or len(source["revision"]) != 40
        or any(character not in "0123456789abcdef" for character in source["revision"])
        or not _sha256(source.get("input_tree_hash"))
        or source.get("tree_state") not in {"clean", "dirty"}
    ):
        _fail("release_report_source_invalid", "source identity is invalid")
    if hosted and source["tree_state"] != "clean":
        _fail("release_report_source_invalid", "hosted evidence requires a clean tree")
    toolchain = report["toolchain"]
    if (
        type(toolchain) is not dict
        or set(toolchain) != {"pillow", "python", "raylib", "raylib_artifact", "world_forge"}
        or not _version(toolchain.get("pillow"))
        or not _version(toolchain.get("python"))
        or not _version(toolchain.get("world_forge"))
        or (toolchain.get("raylib") != "unavailable" and not _version(toolchain.get("raylib")))
    ):
        _fail("release_report_toolchain_invalid", "toolchain identity is invalid")
    host = report["host"]
    require_headless_host(host, hosted=hosted)
    expected_runner = "ubuntu-24.04" if host["os"] == "linux" else "windows-2022"
    if (
        not toolchain["python"].startswith(f"{host['python_minor']}.")
        or (host["runner_image"] != "local" and host["runner_image"] != expected_runner)
        or (
            hosted
            and (
                host["architecture"] != "x86_64"
                or host["runner_image"] != expected_runner
                or any(toolchain[key] != value for key, value in _EXPECTED_TOOLCHAIN.items())
            )
        )
    ):
        _fail("release_report_toolchain_invalid", "toolchain does not match the host")
    if not isinstance(report["native_mode"], str) or report["native_mode"] not in {
        "off",
        "optional",
        "required",
    }:
        _fail("release_report_native_invalid", "native mode is invalid")
    if hosted and report["native_mode"] != "required":
        _fail("release_report_native_invalid", "hosted evidence requires native mode")
    expected_lock = _expected_platform_lock(host)
    artifact = toolchain["raylib_artifact"]
    if report["native_mode"] == "off":
        if artifact is not None:
            _fail("release_report_toolchain_invalid", "native-off report names an artifact")
    elif expected_lock is None:
        if artifact is not None:
            _fail("release_report_toolchain_invalid", "unsupported host names an artifact")
    elif artifact != _runtime_artifact_identity(expected_lock):
        _fail(
            "release_report_toolchain_invalid",
            "runtime artifact does not match the host platform lock",
        )
    cases = report["cases"]
    if (
        type(cases) is not list
        or any(type(item) is not dict for item in cases)
        or [item.get("case_id") for item in cases] != list(CASES)
    ):
        _fail("release_report_cases_invalid", "both canonical cases are required in order")
    for case in cases:
        _validate_case_report(case, native_mode=report["native_mode"], host=host)
    if report["status"] not in {"passed", "failed", "completed_with_native_gap"}:
        _fail("release_report_status_invalid", "report status is unsupported")
    failure_reasons = report["failure_reasons"]
    if type(failure_reasons) is not list or any(
        not isinstance(item, str) or _REASON_PATTERN.fullmatch(item) is None
        for item in failure_reasons
    ):
        _fail("release_report_invalid", "failure_reasons must be an array")
    if failure_reasons != sorted(set(failure_reasons)):
        _fail("release_report_invalid", "failure_reasons must be unique and ordered")
    native_states = {case["native_evidence"]["state"] for case in cases}
    if report["native_mode"] == "off":
        coherent = report["status"] == "passed" and native_states == {"untested"}
    elif report["native_mode"] == "required":
        coherent = (report["status"] == "passed" and native_states == {"passed"}) or (
            report["status"] == "failed" and native_states != {"passed"}
        )
    else:
        coherent = (report["status"] == "passed" and native_states == {"passed"}) or (
            report["status"] == "completed_with_native_gap" and native_states != {"passed"}
        )
    if not coherent:
        _fail("release_report_native_invalid", "native mode and report status disagree")
    expected_failure_reasons = sorted(
        {
            case["native_evidence"]["reason_code"]
            for case in cases
            if case["native_evidence"]["state"] in {"failed", "unavailable"}
        }
    )
    if failure_reasons != expected_failure_reasons:
        _fail("release_report_native_invalid", "native reasons are not exact")
    return report


def _validate_case_report(
    case: object,
    *,
    native_mode: str,
    host: Mapping[str, object],
) -> None:
    if type(case) is not dict:
        _fail("release_report_case_invalid", "case must be an object")
    fields = {
        "case_id",
        "status",
        "stages",
        "hashes",
        "lineage",
        "identities",
        "native_evidence",
        "persistence",
    }
    if set(case) != fields:
        _fail("release_report_case_invalid", "case fields are not closed")
    stages = case["stages"]
    if (
        type(stages) is not list
        or len(stages) != len(REQUIRED_CASE_STAGES)
        or any(type(stage) is not dict for stage in stages)
    ):
        _fail("release_report_stage_invalid", "case stages are incomplete")
    if [stage.get("stage") for stage in stages if isinstance(stage, dict)] != list(
        REQUIRED_CASE_STAGES
    ):
        _fail("release_report_stage_invalid", "case stage order or identity is invalid")
    for stage in stages:
        if set(stage) != {"reason_code", "stage", "state"} or stage["state"] not in {
            "passed",
            "failed",
            "untested",
            "unavailable",
        }:
            _fail("release_report_stage_invalid", "case stage state is invalid")
    if any(stage["state"] != "passed" or stage["reason_code"] is not None for stage in stages[:-1]):
        _fail("release_report_stage_invalid", "non-native stages must all pass")
    hashes = case["hashes"]
    hash_fields = {
        "analysis",
        "assetpack",
        "capability_ledger",
        "gamepack",
        "materialization_bundle",
        "package",
        "package_archive",
        "runtime_bundle",
        "runtime_support_authority",
        "runtime_support_report",
        "standalone_game",
    }
    if type(hashes) is not dict or set(hashes) != hash_fields:
        _fail("release_report_hash_invalid", "case hash set is invalid")
    for item in hashes.values():
        if (
            not isinstance(item, str)
            or len(item) != 64
            or any(character not in "0123456789abcdef" for character in item)
        ):
            _fail("release_report_hash_invalid", "case hash is invalid")
    lineage = case["lineage"]
    if type(lineage) is not dict or set(lineage) != set(LINEAGE_STAGES):
        _fail("release_report_lineage_mismatch", "case lineage stages are incomplete")
    if any(not isinstance(value, str) or value != hashes["gamepack"] for value in lineage.values()):
        _fail("release_report_lineage_mismatch", "gamepack hash drifted across the chain")
    identities = case["identities"]
    identity_fields = {
        "adapter_id",
        "adapter_version",
        "assetpack_id",
        "materialization_bundle_id",
        "package_id",
        "runtime_bundle_id",
        "runtime_support_authority_id",
        "runtime_support_report_id",
        "standalone_game_id",
    }
    expected_adapter_id, expected_adapter_version = CASE_ADAPTERS[case["case_id"]]
    if (
        type(identities) is not dict
        or set(identities) != identity_fields
        or any(
            not isinstance(item, str) or not item or len(item) > 160 for item in identities.values()
        )
        or identities["adapter_id"] != expected_adapter_id
        or identities["adapter_version"] != expected_adapter_version
    ):
        _fail("release_report_identity_invalid", "case identities are invalid")
    persistence = case["persistence"]
    expected_endings = (
        ["puzzle_complete"]
        if case["case_id"] == "abstract-puzzle"
        else ["ending_left", "ending_right"]
    )
    expected_count = len(expected_endings)
    if (
        type(persistence) is not dict
        or set(persistence) != {"endings", "replays_verified", "saves_restored", "saves_verified"}
        or persistence["endings"] != expected_endings
        or type(persistence["replays_verified"]) is not int
        or type(persistence["saves_restored"]) is not int
        or type(persistence["saves_verified"]) is not int
        or persistence["replays_verified"] != expected_count
        or persistence["saves_restored"] != expected_count
        or persistence["saves_verified"] != expected_count
    ):
        _fail("release_report_persistence_invalid", "case persistence evidence is invalid")
    native = case["native_evidence"]
    native_fields = {
        "adapter_id",
        "adapter_version",
        "extracted_runtime_bundle_hash",
        "frames",
        "gamepack_hash",
        "platform_lock_hash",
        "platform_lock_id",
        "reason_code",
        "runtime_artifact_sha256",
        "state",
    }
    if type(native) is not dict or set(native) != native_fields:
        _fail("release_report_native_invalid", "native evidence fields are invalid")
    native_stage = stages[-1]
    if (
        native_stage["state"] != native["state"]
        or native_stage["reason_code"] != native["reason_code"]
    ):
        _fail("release_report_native_invalid", "native stage and evidence disagree")
    if native["state"] == "passed":
        expected_lock = _expected_platform_lock(host)
        if (
            native_mode == "off"
            or expected_lock is None
            or native["reason_code"] is not None
            or type(native["frames"]) is not int
            or not 1 <= native["frames"] <= 120
            or native["adapter_id"] != identities["adapter_id"]
            or native["adapter_version"] != identities["adapter_version"]
            or native["gamepack_hash"] != hashes["gamepack"]
            or native["extracted_runtime_bundle_hash"] != hashes["runtime_bundle"]
            or not isinstance(native["platform_lock_id"], str)
            or not native["platform_lock_id"]
            or len(native["platform_lock_id"]) > 160
            or not isinstance(native["platform_lock_hash"], str)
            or len(native["platform_lock_hash"]) != 64
            or any(
                character not in "0123456789abcdef" for character in native["platform_lock_hash"]
            )
            or native["platform_lock_id"] != expected_lock["lock_id"]
            or native["platform_lock_hash"] != expected_lock["content_hash"]
            or native["runtime_artifact_sha256"]
            != expected_lock["dependency"]["artifact"]["sha256"]
        ):
            _fail("release_report_native_invalid", "passed native evidence is not exact")
    elif native["state"] == "untested":
        if (
            native_mode != "off"
            or type(native["frames"]) is not int
            or native != native_untested_evidence("off")
        ):
            _fail("release_report_native_invalid", "untested native evidence is invalid")
    elif native["state"] in {"failed", "unavailable"}:
        empty_bindings = {
            key: native[key]
            for key in (
                "adapter_id",
                "adapter_version",
                "extracted_runtime_bundle_hash",
                "gamepack_hash",
                "platform_lock_hash",
                "platform_lock_id",
                "runtime_artifact_sha256",
            )
        }
        if (
            native_mode == "off"
            or type(native["frames"]) is not int
            or native["frames"] != 0
            or any(value is not None for value in empty_bindings.values())
            or not isinstance(native["reason_code"], str)
            or _REASON_PATTERN.fullmatch(native["reason_code"]) is None
            or native["reason_code"] not in _NATIVE_FAILURE_REASONS
        ):
            _fail("release_report_native_invalid", "failed native evidence is invalid")
    else:
        _fail("release_report_native_invalid", "native evidence state is invalid")
    expected_case_status = "passed" if native["state"] in {"passed", "untested"} else "failed"
    if case["status"] != expected_case_status:
        _fail("release_report_native_invalid", "case status disagrees with native evidence")


def _sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_aggregate_report(value: object) -> dict[str, Any]:
    """Validate one closed exact-matrix aggregate report."""

    if type(value) is not dict:
        _fail("release_aggregate_invalid", "aggregate root must be an object")
    aggregate = copy.deepcopy(value)
    fields = {
        "format",
        "format_version",
        "status",
        "source_input_tree_hash",
        "source_revision",
        "matrix",
        "reports",
        "fixtures",
    }
    if set(aggregate) != fields:
        _fail("release_aggregate_invalid", "aggregate fields are not the closed v1 set")
    if (
        aggregate["format"] != AGGREGATE_FORMAT
        or type(aggregate["format_version"]) is not int
        or aggregate["format_version"] != REPORT_VERSION
        or aggregate["status"] != "passed"
        or not _sha256(aggregate.get("source_input_tree_hash"))
        or not isinstance(aggregate["source_revision"], str)
        or len(aggregate["source_revision"]) != 40
        or any(character not in "0123456789abcdef" for character in aggregate["source_revision"])
    ):
        _fail("release_aggregate_invalid", "aggregate identity is invalid")
    expected_matrix = [
        {"os": os_name, "python_minor": python_minor} for os_name, python_minor in REQUIRED_MATRIX
    ]
    if aggregate["matrix"] != expected_matrix:
        _fail("release_aggregate_invalid", "aggregate matrix is not exact")
    reports = aggregate["reports"]
    if type(reports) is not list or len(reports) != len(REQUIRED_MATRIX):
        _fail("release_aggregate_invalid", "aggregate report evidence is incomplete")
    for item, expected in zip(reports, expected_matrix, strict=True):
        if (
            type(item) is not dict
            or set(item) != {"os", "python_minor", "report_sha256"}
            or item.get("os") != expected["os"]
            or item.get("python_minor") != expected["python_minor"]
            or not _sha256(item.get("report_sha256"))
        ):
            _fail("release_aggregate_invalid", "aggregate report evidence is invalid")
    fixtures = aggregate["fixtures"]
    if type(fixtures) is not list or len(fixtures) != len(CASES):
        _fail("release_aggregate_invalid", "aggregate fixture evidence is incomplete")
    fixture_fields = {
        "case_id",
        "gamepack_hash",
        "package_hash",
        "package_archive_sha256",
    }
    for fixture, case_id in zip(fixtures, CASES, strict=True):
        if (
            type(fixture) is not dict
            or set(fixture) != fixture_fields
            or fixture.get("case_id") != case_id
            or any(not _sha256(fixture.get(field)) for field in fixture_fields - {"case_id"})
        ):
            _fail("release_aggregate_invalid", "aggregate fixture evidence is invalid")
    return aggregate


def aggregate_release_reports(reports: Sequence[object]) -> dict[str, Any]:
    """Aggregate the exact four mandatory hosted matrix reports."""

    checked_inputs = []
    for report in reports:
        if isinstance(report, LoadedReleaseReport):
            document = report.document
            report_sha256 = report.sha256
        else:
            document = report
            report_sha256 = hashlib.sha256(canonical_json_bytes(report)).hexdigest()
        checked_inputs.append((validate_release_report(document, hosted=True), report_sha256))
    checked = [document for document, _report_sha256 in checked_inputs]
    rows = [(report["host"]["os"], report["host"]["python_minor"]) for report in checked]
    if len(rows) != len(set(rows)):
        _fail("release_aggregate_matrix_duplicate", "matrix row appears more than once")
    if set(rows) != set(REQUIRED_MATRIX) or len(rows) != len(REQUIRED_MATRIX):
        _fail("release_aggregate_matrix_incomplete", "exact 2x2 matrix is required")
    revisions = {report["source"]["revision"] for report in checked}
    input_tree_hashes = {report["source"]["input_tree_hash"] for report in checked}
    if len(revisions) != 1 or len(input_tree_hashes) != 1:
        _fail("release_aggregate_source_mismatch", "matrix source inputs differ")
    reference = checked[0]
    fixtures = []
    for case_id in CASES:
        reference_case = next(case for case in reference["cases"] if case["case_id"] == case_id)
        identity = {
            "case_id": case_id,
            "gamepack_hash": reference_case["hashes"]["gamepack"],
            "package_hash": reference_case["hashes"]["package"],
            "package_archive_sha256": reference_case["hashes"]["package_archive"],
        }
        for report in checked:
            case = next(item for item in report["cases"] if item["case_id"] == case_id)
            if case["native_evidence"]["state"] != "passed":
                _fail("release_aggregate_native_incomplete", f"{case_id} native evidence missing")
            deterministic_fields = (
                "hashes",
                "identities",
                "lineage",
                "persistence",
            )
            if any(case[field] != reference_case[field] for field in deterministic_fields) or (
                case["stages"][:-1] != reference_case["stages"][:-1]
            ):
                _fail("release_aggregate_fixture_mismatch", f"{case_id} hashes differ")
            native_common_fields = {
                "adapter_id",
                "adapter_version",
                "extracted_runtime_bundle_hash",
                "frames",
                "gamepack_hash",
                "reason_code",
                "state",
            }
            if {key: case["native_evidence"][key] for key in native_common_fields} != {
                key: reference_case["native_evidence"][key] for key in native_common_fields
            }:
                _fail("release_aggregate_fixture_mismatch", f"{case_id} native output differs")
        fixtures.append(identity)
    if any(
        report["native_mode"] != "required" or report["status"] != "passed" for report in checked
    ):
        _fail("release_aggregate_native_incomplete", "matrix report is not required-native passed")
    matrix = [
        {"os": os_name, "python_minor": python_minor} for os_name, python_minor in sorted(rows)
    ]
    report_evidence = [
        {
            "os": report["host"]["os"],
            "python_minor": report["host"]["python_minor"],
            "report_sha256": report_sha256,
        }
        for report, report_sha256 in sorted(
            checked_inputs,
            key=lambda item: (
                item[0]["host"]["os"],
                item[0]["host"]["python_minor"],
            ),
        )
    ]
    return validate_aggregate_report(
        {
            "format": AGGREGATE_FORMAT,
            "format_version": REPORT_VERSION,
            "status": "passed",
            "source_input_tree_hash": input_tree_hashes.pop(),
            "source_revision": revisions.pop(),
            "matrix": matrix,
            "reports": report_evidence,
            "fixtures": fixtures,
        }
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite JSON number: {value}")
    return parsed


def _decode_json_object(
    payload: bytes,
    *,
    source: object,
    reason_code: str = "release_report_json_invalid",
) -> dict[str, Any]:
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        _fail(reason_code, f"{source}: {exc}")
    if type(document) is not dict:
        _fail(reason_code, f"{source}: root must be an object")
    return document
