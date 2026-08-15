#!/usr/bin/env python3
"""Plan, execute, and aggregate the private hosted headless unittest suite."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gamepack_runtime.persistence_io import PersistenceIOError, publish_bytes_noreplace
from worldforge.asset_io import (
    AssetContractError,
    open_verified_output_parent,
    read_bound_bytes,
)
from worldforge.file_stat import is_link_or_reparse
from worldforge.integrity import canonical_json_bytes

_MANIFEST_FORMAT = "world-forge.headless_suite_shards"
_MANIFEST_VERSION = 1
_PLAN_FORMAT = "world-forge.headless_suite_plan"
_PLAN_VERSION = 1
_EVENT_FORMAT = "world-forge.headless_suite_test_event"
_EVENT_VERSION = 1
_RECEIPT_FORMAT = "world-forge.headless_suite_receipt"
_RECEIPT_VERSION = 1
_AGGREGATE_FORMAT = "world-forge.headless_suite_aggregate"
_AGGREGATE_VERSION = 1
_AGGREGATE_ATTEMPT_FORMAT = "world-forge.headless_suite_aggregate_attempt"
_MANIFEST_LIMIT = 1024 * 1024
_PLAN_LIMIT = 8 * 1024 * 1024
_EVENTS_LIMIT = 16 * 1024 * 1024
_RECEIPT_LIMIT = 64 * 1024
_AGGREGATE_LIMIT = 1024 * 1024
_WORKER_TIMEOUT_SECONDS = 6000
_SHARD_IDS = tuple(f"s{index:02d}" for index in range(16))
_MODULE_PATTERN = re.compile(r"test_[a-z0-9_]+")
_TEST_ID_PATTERN = re.compile(r"test_[a-z0-9_]+\.[A-Za-z_][A-Za-z0-9_]*\.test_[a-z0-9_]+")
_HEX_40 = re.compile(r"[0-9a-f]{40}")
_HEX_64 = re.compile(r"[0-9a-f]{64}")
_TERMINAL_OUTCOMES = {
    "crash",
    "duplicate",
    "error",
    "expected_failure",
    "failure",
    "incomplete",
    "missing",
    "passed",
    "skipped",
    "timeout",
    "unexpected_success",
}
_ACCEPTED_OUTCOMES = {"expected_failure", "passed", "skipped"}
_INCOMPLETE_OUTCOMES = {"crash", "duplicate", "incomplete", "missing", "timeout"}


class HeadlessSuiteError(RuntimeError):
    """Raised when private headless-suite evidence is not exact."""


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DiscoveredSuite:
    test_ids: tuple[str, ...]
    modules: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class ChildRun:
    raw_events: bytes
    return_code: int | None
    timed_out: bool


def terminal_event(test_id: str, outcome: str) -> dict[str, object]:
    if type(test_id) is not str or not test_id or type(outcome) is not str:
        raise HeadlessSuiteError("terminal test event is invalid")
    if outcome not in _TERMINAL_OUTCOMES:
        raise HeadlessSuiteError("terminal test outcome is invalid")
    return {
        "format": _EVENT_FORMAT,
        "format_version": _EVENT_VERSION,
        "outcome": outcome,
        "test_id": test_id,
    }


class HeadlessTestResult(unittest.TestResult):
    """Coalesce unittest callbacks into one terminal outcome per parent test."""

    def __init__(self, planned_ids: Sequence[str]) -> None:
        super().__init__()
        self._planned_ids = tuple(planned_ids)
        self._planned = set(self._planned_ids)
        self._outcomes: dict[str, str] = {}
        self._started: set[str] = set()

    @staticmethod
    def _parent_id(test: object) -> str:
        parent = getattr(test, "test_case", test)
        identifier = getattr(parent, "id", None)
        value = identifier() if callable(identifier) else ""
        return value if type(value) is str else ""

    def _set(self, test: object, outcome: str, *, replace: bool = True) -> None:
        test_id = self._parent_id(test)
        if test_id in self._planned and (replace or test_id not in self._outcomes):
            self._outcomes[test_id] = outcome

    def _fixture_outcome(self, holder: object, outcome: str) -> None:
        identifier = getattr(holder, "id", lambda: "")()
        match = re.search(r"\(([^()]+)\)$", identifier if type(identifier) is str else "")
        affected: list[str] = []
        if match is not None:
            prefix = match.group(1) + "."
            affected = [test_id for test_id in self._planned_ids if test_id.startswith(prefix)]
        if not affected:
            affected = [test_id for test_id in self._planned_ids if test_id not in self._outcomes]
        for test_id in affected:
            self._outcomes[test_id] = outcome

    def startTest(self, test: object) -> None:  # noqa: N802 - unittest API
        super().startTest(test)
        test_id = self._parent_id(test)
        if test_id in self._started:
            self._outcomes[test_id] = "duplicate"
        self._started.add(test_id)

    def addSuccess(self, test: object) -> None:  # noqa: N802 - unittest API
        super().addSuccess(test)
        self._set(test, "passed", replace=False)

    def addFailure(self, test: object, err: object) -> None:  # noqa: N802 - unittest API
        super().addFailure(test, err)  # type: ignore[arg-type]
        self._set(test, "failure")

    def addError(self, test: object, err: object) -> None:  # noqa: N802 - unittest API
        super().addError(test, err)  # type: ignore[arg-type]
        if self._parent_id(test) in self._planned:
            self._set(test, "error")
        else:
            self._fixture_outcome(test, "error")

    def addSkip(self, test: object, reason: str) -> None:  # noqa: N802 - unittest API
        super().addSkip(test, reason)
        if self._parent_id(test) in self._planned:
            self._set(test, "skipped", replace=False)
        else:
            self._fixture_outcome(test, "skipped")

    def addExpectedFailure(self, test: object, err: object) -> None:  # noqa: N802
        super().addExpectedFailure(test, err)  # type: ignore[arg-type]
        self._set(test, "expected_failure")

    def addUnexpectedSuccess(self, test: object) -> None:  # noqa: N802 - unittest API
        super().addUnexpectedSuccess(test)
        self._set(test, "unexpected_success")

    def addSubTest(self, test: object, subtest: object, err: object) -> None:  # noqa: N802
        super().addSubTest(test, subtest, err)  # type: ignore[arg-type]
        if err is None:
            return
        exception_type = err[0]  # type: ignore[index]
        outcome = "failure" if issubclass(exception_type, test.failureException) else "error"
        self._set(test, outcome)

    def terminal_events(self) -> tuple[dict[str, object], ...]:
        return tuple(
            terminal_event(test_id, self._outcomes.get(test_id, "incomplete"))
            for test_id in self._planned_ids
        )


def terminal_events_accepted(events: Sequence[Mapping[str, object]]) -> bool:
    return all(event.get("outcome") in _ACCEPTED_OUTCOMES for event in events)


def _canonical_json_line(document: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(
                document,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise HeadlessSuiteError(f"terminal test event is invalid: {exc}") from exc


def encode_terminal_events(events: Sequence[Mapping[str, object]]) -> bytes:
    payload = b"".join(_canonical_json_line(event) for event in events)
    if len(payload) > _EVENTS_LIMIT:
        raise HeadlessSuiteError("terminal test events exceeded their bound")
    return payload


def parse_terminal_events(payload: bytes) -> tuple[dict[str, object], ...]:
    if type(payload) is not bytes or len(payload) > _EVENTS_LIMIT:
        raise HeadlessSuiteError("terminal test events exceeded their bound")
    if payload and not payload.endswith(b"\n"):
        raise HeadlessSuiteError("terminal test events framing is invalid")
    events: list[dict[str, object]] = []
    for raw_line in payload.splitlines(keepends=True):
        try:
            event = json.loads(raw_line, object_pairs_hook=_reject_duplicate_keys)
        except (RecursionError, UnicodeError, ValueError) as exc:
            raise HeadlessSuiteError("terminal test event is invalid") from exc
        if (
            type(event) is not dict
            or set(event) != {"format", "format_version", "outcome", "test_id"}
            or event.get("format") != _EVENT_FORMAT
            or event.get("format_version") != _EVENT_VERSION
            or type(event.get("test_id")) is not str
            or _TEST_ID_PATTERN.fullmatch(event["test_id"]) is None
            or event.get("outcome") not in _TERMINAL_OUTCOMES
            or _canonical_json_line(event) != raw_line
        ):
            raise HeadlessSuiteError("terminal test event is invalid")
        events.append(event)
    return tuple(events)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise _DuplicateJsonKey(key)
        document[key] = value
    return document


def _load_canonical_json(path: Path, *, limit: int) -> tuple[dict[str, Any], bytes]:
    try:
        retained = read_bound_bytes(path, limit=limit)
        payload = retained.payload
        document = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, RecursionError, UnicodeError, ValueError) as exc:
        raise HeadlessSuiteError(f"{path.name} is invalid: {exc}") from exc
    if type(document) is not dict:
        raise HeadlessSuiteError(f"{path.name} must contain one object")
    try:
        canonical = canonical_json_bytes(document)
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise HeadlessSuiteError(f"{path.name} is invalid: {exc}") from exc
    if canonical != payload:
        raise HeadlessSuiteError(f"{path.name} is noncanonical")
    return document, payload


def _is_int(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _validate_calibration(calibration: object) -> None:
    fields = {
        "commit",
        "discovered_module_count",
        "discovered_test_count",
        "ordered_test_id_stream_sha256",
        "ordered_test_id_stream_size_bytes",
        "run_log_sha256",
        "skipped_test_count",
        "source_tree",
        "wall_time_milliseconds",
    }
    if type(calibration) is not dict or set(calibration) != fields:
        raise HeadlessSuiteError("manifest calibration is invalid")
    if (
        type(calibration["commit"]) is not str
        or _HEX_40.fullmatch(calibration["commit"]) is None
        or type(calibration["source_tree"]) is not str
        or re.fullmatch(r"[0-9a-f]{7,40}", calibration["source_tree"]) is None
        or type(calibration["ordered_test_id_stream_sha256"]) is not str
        or _HEX_64.fullmatch(calibration["ordered_test_id_stream_sha256"]) is None
        or type(calibration["run_log_sha256"]) is not str
        or _HEX_64.fullmatch(calibration["run_log_sha256"]) is None
        or not _is_int(calibration["discovered_module_count"], minimum=1)
        or not _is_int(calibration["discovered_test_count"], minimum=1)
        or not _is_int(calibration["ordered_test_id_stream_size_bytes"], minimum=1)
        or not _is_int(calibration["skipped_test_count"])
        or not _is_int(calibration["wall_time_milliseconds"], minimum=1)
    ):
        raise HeadlessSuiteError("manifest calibration is invalid")


def validate_manifest(
    manifest: Mapping[str, object],
    *,
    discovery: DiscoveredSuite | None = None,
) -> None:
    if type(manifest) is not dict or set(manifest) != {
        "calibration",
        "discovery",
        "format",
        "format_version",
        "modules",
        "shards",
    }:
        raise HeadlessSuiteError("manifest fields are invalid")
    if manifest["format"] != _MANIFEST_FORMAT or manifest["format_version"] != _MANIFEST_VERSION:
        raise HeadlessSuiteError("manifest identity is invalid")
    rules = manifest["discovery"]
    if type(rules) is not dict or rules != {
        "pattern": "test_*.py",
        "start_directory": "tests",
    }:
        raise HeadlessSuiteError("manifest discovery rules are invalid")
    _validate_calibration(manifest["calibration"])

    modules = manifest["modules"]
    if (
        type(modules) is not list
        or not modules
        or any(
            type(module) is not str or _MODULE_PATTERN.fullmatch(module) is None
            for module in modules
        )
        or modules != sorted(modules)
        or len(modules) != len(set(modules))
    ):
        raise HeadlessSuiteError("manifest modules are invalid")
    shards = manifest["shards"]
    if type(shards) is not list or len(shards) != len(_SHARD_IDS):
        raise HeadlessSuiteError("manifest shard IDs are invalid")
    assigned: list[str] = []
    actual_ids: list[str] = []
    for shard in shards:
        if type(shard) is not dict or set(shard) != {
            "calibration_state",
            "id",
            "modules",
            "test_count",
            "weight_seconds",
        }:
            raise HeadlessSuiteError("manifest shard fields are invalid")
        shard_id = shard["id"]
        actual_ids.append(shard_id if type(shard_id) is str else "")
        shard_modules = shard["modules"]
        if (
            type(shard_modules) is not list
            or not shard_modules
            or shard_modules != sorted(shard_modules)
            or any(
                type(module) is not str or _MODULE_PATTERN.fullmatch(module) is None
                for module in shard_modules
            )
        ):
            raise HeadlessSuiteError("manifest module assignment is invalid")
        assigned.extend(shard_modules)
        state = shard["calibration_state"]
        weight = shard["weight_seconds"]
        if (
            state not in {"complete", "partial", "unmeasured"}
            or not _is_int(shard["test_count"])
            or (state == "unmeasured" and weight is not None)
            or (state != "unmeasured" and not _is_int(weight, minimum=1))
        ):
            raise HeadlessSuiteError("manifest shard calibration is invalid")
    if actual_ids != list(_SHARD_IDS):
        raise HeadlessSuiteError("manifest shard IDs are invalid")
    if (
        sum(shard["test_count"] for shard in shards)
        != manifest["calibration"]["discovered_test_count"]
    ):
        raise HeadlessSuiteError("manifest shard calibration is invalid")
    if len(assigned) != len(set(assigned)) or sorted(assigned) != modules:
        raise HeadlessSuiteError("manifest module assignment is invalid")
    if discovery is not None and set(modules) != set(discovery.modules):
        raise HeadlessSuiteError("manifest does not match discovered modules")


def load_manifest(path: Path) -> dict[str, Any]:
    manifest, _payload = _load_canonical_json(Path(path), limit=_MANIFEST_LIMIT)
    validate_manifest(manifest)
    return manifest


def validate_plan(plan: Mapping[str, object]) -> None:
    if type(plan) is not dict or set(plan) != {
        "axis",
        "discovery",
        "format",
        "format_version",
        "manifest_sha256",
        "shards",
        "source",
    }:
        raise HeadlessSuiteError("headless suite plan fields are invalid")
    if plan["format"] != _PLAN_FORMAT or plan["format_version"] != _PLAN_VERSION:
        raise HeadlessSuiteError("headless suite plan identity is invalid")
    axis = plan["axis"]
    if type(axis) is not dict or set(axis) != {
        "python_full_version",
        "python_minor",
        "runner_image",
        "runner_os",
    }:
        raise HeadlessSuiteError("headless suite plan axis is invalid")
    _validate_axis_value(axis["runner_os"], label="runner OS", pattern=r"Linux|Windows")
    _validate_axis_value(
        axis["runner_image"],
        label="runner image",
        pattern=r"[A-Za-z0-9._:+-]+",
    )
    full = _validate_axis_value(
        axis["python_full_version"],
        label="full Python version",
        pattern=r"3\.(?:11|12)\.[0-9]+",
    )
    minor = _validate_axis_value(
        axis["python_minor"],
        label="Python minor",
        pattern=r"3\.(?:11|12)",
    )
    if not full.startswith(minor + "."):
        raise HeadlessSuiteError("headless suite plan Python axis is inconsistent")
    source = plan["source"]
    if (
        type(source) is not dict
        or set(source) != {"commit", "tree"}
        or type(source.get("commit")) is not str
        or _HEX_40.fullmatch(source["commit"]) is None
        or type(source.get("tree")) is not str
        or _HEX_40.fullmatch(source["tree"]) is None
        or type(plan["manifest_sha256"]) is not str
        or _HEX_64.fullmatch(plan["manifest_sha256"]) is None
    ):
        raise HeadlessSuiteError("headless suite plan source is invalid")
    discovery = plan["discovery"]
    ordered_test_ids = discovery.get("ordered_test_ids") if type(discovery) is dict else None
    if (
        type(discovery) is not dict
        or set(discovery)
        != {
            "module_count",
            "ordered_test_ids",
            "test_count",
            "test_ids_sha256",
            "test_ids_size_bytes",
        }
        or not _is_int(discovery.get("module_count"), minimum=1)
        or not _is_int(discovery.get("test_count"), minimum=1)
        or not _is_int(discovery.get("test_ids_size_bytes"), minimum=1)
        or type(discovery.get("test_ids_sha256")) is not str
        or _HEX_64.fullmatch(discovery["test_ids_sha256"]) is None
        or type(ordered_test_ids) is not list
        or any(
            type(test_id) is not str or _TEST_ID_PATTERN.fullmatch(test_id) is None
            for test_id in ordered_test_ids
        )
        or len(ordered_test_ids) != len(set(ordered_test_ids))
    ):
        raise HeadlessSuiteError("headless suite plan discovery is invalid")
    discovery_stream = _stream_bytes(ordered_test_ids)
    if (
        discovery["test_count"] != len(ordered_test_ids)
        or discovery["module_count"]
        != len({test_id.split(".", 1)[0] for test_id in ordered_test_ids})
        or discovery["test_ids_size_bytes"] != len(discovery_stream)
        or discovery["test_ids_sha256"] != hashlib.sha256(discovery_stream).hexdigest()
    ):
        raise HeadlessSuiteError("headless suite plan discovery hash is invalid")
    shards = plan["shards"]
    if type(shards) is not list or len(shards) != len(_SHARD_IDS):
        raise HeadlessSuiteError("headless suite plan shards are invalid")
    all_ids: list[str] = []
    for expected_shard_id, shard in zip(_SHARD_IDS, shards, strict=True):
        if type(shard) is not dict or set(shard) != {
            "expected_test_count",
            "expected_test_ids",
            "expected_test_ids_sha256",
            "expected_test_ids_size_bytes",
            "id",
        }:
            raise HeadlessSuiteError("headless suite plan shard is invalid")
        test_ids = shard["expected_test_ids"]
        if (
            shard["id"] != expected_shard_id
            or type(test_ids) is not list
            or any(
                type(test_id) is not str or _TEST_ID_PATTERN.fullmatch(test_id) is None
                for test_id in test_ids
            )
            or len(test_ids) != len(set(test_ids))
        ):
            raise HeadlessSuiteError("headless suite plan shard is invalid")
        stream = _stream_bytes(test_ids)
        if (
            shard["expected_test_count"] != len(test_ids)
            or shard["expected_test_ids_size_bytes"] != len(stream)
            or shard["expected_test_ids_sha256"] != hashlib.sha256(stream).hexdigest()
        ):
            raise HeadlessSuiteError("headless suite plan shard hash is invalid")
        all_ids.extend(test_ids)
    if (
        len(all_ids) != len(set(all_ids))
        or len(all_ids) != discovery["test_count"]
        or set(all_ids) != set(ordered_test_ids)
    ):
        raise HeadlessSuiteError("headless suite plan test IDs are not exact")


def load_plan(path: Path) -> dict[str, Any]:
    plan, _payload = _load_canonical_json(Path(path), limit=_PLAN_LIMIT)
    validate_plan(plan)
    return plan


def _flatten_suite(suite: Iterable[object]) -> Iterable[object]:
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _flatten_suite(test)
        else:
            yield test


def collect_discovered_tests(
    tests: Iterable[object],
    *,
    loader_errors: Sequence[str] = (),
) -> DiscoveredSuite:
    if loader_errors:
        raise HeadlessSuiteError("unittest discovery reported loader errors")
    test_ids: list[str] = []
    modules: dict[str, list[str]] = {}
    seen: set[str] = set()
    for test in tests:
        if test.__class__.__name__ == "_FailedTest":
            raise HeadlessSuiteError("unittest discovery produced _FailedTest")
        identifier_method = getattr(test, "id", None)
        identifier = identifier_method() if callable(identifier_method) else None
        if type(identifier) is not str or _TEST_ID_PATTERN.fullmatch(identifier) is None:
            raise HeadlessSuiteError("unittest discovery produced a noncanonical test ID")
        if identifier in seen:
            raise HeadlessSuiteError("unittest discovery produced a duplicate test ID")
        seen.add(identifier)
        test_ids.append(identifier)
        module = identifier.split(".", 1)[0]
        modules.setdefault(module, []).append(identifier)
    if not test_ids:
        raise HeadlessSuiteError("unittest discovery was empty")
    return DiscoveredSuite(
        test_ids=tuple(test_ids),
        modules={module: tuple(ids) for module, ids in sorted(modules.items())},
    )


def discover_suite(source_root: Path, manifest: Mapping[str, object]) -> DiscoveredSuite:
    validate_manifest(manifest)
    root = Path(source_root).resolve(strict=True)
    rules = manifest["discovery"]
    tests_root = (root / rules["start_directory"]).resolve(strict=True)
    loader = unittest.TestLoader()
    suite = loader.discover(
        str(tests_root),
        pattern=rules["pattern"],
        top_level_dir=str(tests_root),
    )
    discovery = collect_discovered_tests(
        _flatten_suite(suite),
        loader_errors=tuple(loader.errors),
    )
    validate_manifest(manifest, discovery=discovery)
    return discovery


def _stream_bytes(test_ids: Sequence[str]) -> bytes:
    return b"".join(test_id.encode("utf-8") + b"\n" for test_id in test_ids)


def _validate_axis_value(value: object, *, label: str, pattern: str, limit: int = 128) -> str:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > limit
        or re.fullmatch(pattern, value) is None
    ):
        raise HeadlessSuiteError(f"{label} is invalid")
    return value


def build_plan(
    manifest: Mapping[str, object],
    discovery: DiscoveredSuite,
    *,
    manifest_sha256: str,
    commit: str,
    source_tree: str,
    runner_os: str,
    runner_image: str,
    python_full_version: str,
    python_minor: str,
) -> dict[str, Any]:
    validate_manifest(manifest, discovery=discovery)
    if type(manifest_sha256) is not str or _HEX_64.fullmatch(manifest_sha256) is None:
        raise HeadlessSuiteError("manifest hash is invalid")
    if type(commit) is not str or _HEX_40.fullmatch(commit) is None:
        raise HeadlessSuiteError("source commit is invalid")
    if type(source_tree) is not str or _HEX_40.fullmatch(source_tree) is None:
        raise HeadlessSuiteError("source tree is invalid")
    runner_os = _validate_axis_value(
        runner_os,
        label="runner OS",
        pattern=r"Linux|Windows",
    )
    runner_image = _validate_axis_value(
        runner_image,
        label="runner image",
        pattern=r"[A-Za-z0-9._:+-]+",
    )
    python_full_version = _validate_axis_value(
        python_full_version,
        label="full Python version",
        pattern=r"3\.(?:11|12)\.[0-9]+",
    )
    python_minor = _validate_axis_value(
        python_minor,
        label="Python minor",
        pattern=r"3\.(?:11|12)",
    )
    if not python_full_version.startswith(python_minor + "."):
        raise HeadlessSuiteError("Python version axis is inconsistent")

    shards = []
    planned_ids: list[str] = []
    for shard in manifest["shards"]:
        module_names = set(shard["modules"])
        expected = tuple(
            test_id for test_id in discovery.test_ids if test_id.split(".", 1)[0] in module_names
        )
        payload = _stream_bytes(expected)
        planned_ids.extend(expected)
        shards.append(
            {
                "expected_test_count": len(expected),
                "expected_test_ids": list(expected),
                "expected_test_ids_sha256": hashlib.sha256(payload).hexdigest(),
                "expected_test_ids_size_bytes": len(payload),
                "id": shard["id"],
            }
        )
    if len(planned_ids) != len(set(planned_ids)) or set(planned_ids) != set(discovery.test_ids):
        raise HeadlessSuiteError("planned test IDs are incomplete")
    stream = _stream_bytes(discovery.test_ids)
    return {
        "axis": {
            "python_full_version": python_full_version,
            "python_minor": python_minor,
            "runner_image": runner_image,
            "runner_os": runner_os,
        },
        "discovery": {
            "module_count": len(discovery.modules),
            "ordered_test_ids": list(discovery.test_ids),
            "test_count": len(discovery.test_ids),
            "test_ids_sha256": hashlib.sha256(stream).hexdigest(),
            "test_ids_size_bytes": len(stream),
        },
        "format": _PLAN_FORMAT,
        "format_version": _PLAN_VERSION,
        "manifest_sha256": manifest_sha256,
        "shards": shards,
        "source": {"commit": commit, "tree": source_tree},
    }


def _plan_shard(plan: Mapping[str, object], shard_id: str) -> dict[str, Any]:
    shards = plan.get("shards")
    if type(shards) is not list:
        raise HeadlessSuiteError("worker plan shards are invalid")
    matches = [shard for shard in shards if type(shard) is dict and shard.get("id") == shard_id]
    if len(matches) != 1:
        raise HeadlessSuiteError("worker shard is not exact")
    shard = matches[0]
    expected = shard.get("expected_test_ids")
    if (
        type(expected) is not list
        or any(
            type(test_id) is not str or _TEST_ID_PATTERN.fullmatch(test_id) is None
            for test_id in expected
        )
        or len(expected) != len(set(expected))
    ):
        raise HeadlessSuiteError("worker expected test IDs are invalid")
    return shard


def finalize_worker_evidence(
    plan: Mapping[str, object],
    *,
    shard_id: str,
    raw_events: bytes,
    return_code: int | None,
    timed_out: bool,
) -> tuple[bytes, dict[str, Any]]:
    shard = _plan_shard(plan, shard_id)
    expected = tuple(shard["expected_test_ids"])
    violations: set[str] = set()
    try:
        parsed = parse_terminal_events(raw_events)
    except HeadlessSuiteError:
        parsed = ()
        violations.add("invalid_event_stream")
    observed: dict[str, dict[str, object]] = {}
    duplicates: set[str] = set()
    extras: set[str] = set()
    expected_set = set(expected)
    for event in parsed:
        test_id = event["test_id"]
        if test_id not in expected_set:
            extras.add(test_id)
            continue
        if test_id in observed:
            duplicates.add(test_id)
            continue
        observed[test_id] = event
        if event["outcome"] in _INCOMPLETE_OUTCOMES:
            violations.add(f"terminal_{event['outcome']}")
    if duplicates:
        violations.add("duplicate_event")
    if extras:
        violations.add("extra_event")
    if timed_out:
        violations.add("timeout")
    elif return_code is None or return_code not in {0, 1}:
        violations.add("crash")

    normalized = []
    for test_id in expected:
        if test_id in duplicates:
            outcome = "duplicate"
        elif test_id in observed:
            outcome = observed[test_id]["outcome"]
        elif timed_out:
            outcome = "timeout"
        elif return_code is None or return_code not in {0, 1}:
            outcome = "crash"
        else:
            outcome = "missing"
            violations.add("missing_event")
        normalized.append(terminal_event(test_id, outcome))
    events_payload = encode_terminal_events(tuple(normalized))
    complete = not violations and len(parsed) == len(expected)
    accepted = terminal_events_accepted(tuple(normalized))
    status = "passed" if complete and accepted and return_code == 0 else "failed"
    counts: dict[str, int] = {}
    for event in normalized:
        outcome = event["outcome"]
        counts[outcome] = counts.get(outcome, 0) + 1
    try:
        plan_payload = canonical_json_bytes(dict(plan))
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise HeadlessSuiteError(f"worker plan is invalid: {exc}") from exc
    axis = plan.get("axis")
    if type(axis) is not dict:
        raise HeadlessSuiteError("worker plan axis is invalid")
    receipt = {
        "axis": dict(axis),
        "event_count": len(normalized),
        "events_sha256": hashlib.sha256(events_payload).hexdigest(),
        "format": _RECEIPT_FORMAT,
        "format_version": _RECEIPT_VERSION,
        "outcome_counts": dict(sorted(counts.items())),
        "plan_sha256": hashlib.sha256(plan_payload).hexdigest(),
        "return_code": return_code,
        "shard_id": shard_id,
        "state": "complete" if complete else "incomplete",
        "status": status,
        "timed_out": timed_out,
        "violations": sorted(violations),
    }
    return events_payload, receipt


def require_exact_plan(
    plan: Mapping[str, object],
    *,
    expected: Mapping[str, object],
) -> None:
    try:
        actual_payload = canonical_json_bytes(dict(plan))
        expected_payload = canonical_json_bytes(dict(expected))
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise HeadlessSuiteError(f"plan drift is invalid: {exc}") from exc
    if actual_payload != expected_payload:
        raise HeadlessSuiteError("headless suite plan drifted from the current checkout")


def _external_output_directory(output_dir: Path, *, source_root: Path) -> Path:
    source = Path(source_root).resolve(strict=True)
    requested = Path(output_dir).absolute()
    try:
        requested.relative_to(source)
    except ValueError:
        pass
    else:
        raise HeadlessSuiteError("headless suite evidence must remain outside the source tree")
    try:
        info = requested.lstat()
        if is_link_or_reparse(info) or not requested.is_dir():
            raise AssetContractError("evidence output directory is unsafe")
        resolved = requested.resolve(strict=True)
        try:
            resolved.relative_to(source)
        except ValueError:
            pass
        else:
            raise AssetContractError("evidence output directory resolves inside source")
    except (AssetContractError, OSError) as exc:
        raise HeadlessSuiteError(f"headless suite output is invalid: {exc}") from exc
    return resolved


def _publish_payloads(
    output_dir: Path,
    payloads: Sequence[tuple[str, bytes, int]],
    *,
    source_root: Path,
) -> None:
    output = _external_output_directory(output_dir, source_root=source_root)
    try:
        with open_verified_output_parent(output, create=False) as parent:
            identity = parent.identities[-1]
            for name, payload, limit in payloads:
                publish_bytes_noreplace(
                    output,
                    name,
                    payload,
                    expected_parent_identity=identity,
                    limit=limit,
                    mode=0o600,
                )
            parent.assert_current()
            parent.flush_durable(context="headless suite evidence parent")
    except (AssetContractError, FileExistsError, OSError, PersistenceIOError) as exc:
        raise HeadlessSuiteError(f"headless suite evidence publish failed: {exc}") from exc


def publish_plan(
    output_dir: Path,
    plan: Mapping[str, object],
    *,
    source_root: Path,
) -> Path:
    try:
        payload = canonical_json_bytes(dict(plan))
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise HeadlessSuiteError(f"headless suite plan is invalid: {exc}") from exc
    _publish_payloads(
        output_dir,
        (("plan.json", payload, _PLAN_LIMIT),),
        source_root=source_root,
    )
    return Path(output_dir).resolve(strict=True) / "plan.json"


def publish_worker_evidence(
    output_dir: Path,
    events_payload: bytes,
    receipt: Mapping[str, object],
    *,
    source_root: Path,
) -> None:
    try:
        receipt_payload = canonical_json_bytes(dict(receipt))
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise HeadlessSuiteError(f"headless suite receipt is invalid: {exc}") from exc
    _publish_payloads(
        output_dir,
        (
            ("events.jsonl", events_payload, _EVENTS_LIMIT),
            ("receipt.json", receipt_payload, _RECEIPT_LIMIT),
        ),
        source_root=source_root,
    )


def execute_planned_tests(
    plan: Mapping[str, object],
    *,
    shard_id: str,
    loader: unittest.TestLoader | Any | None = None,
) -> tuple[tuple[dict[str, object], ...], int]:
    shard = _plan_shard(plan, shard_id)
    expected = tuple(shard["expected_test_ids"])
    if loader is None:
        loader = unittest.TestLoader()
    suite = loader.loadTestsFromNames(list(expected))
    tests = tuple(_flatten_suite(suite))
    if loader.errors:
        raise HeadlessSuiteError("worker loader errors made the shard incomplete")
    if any(test.__class__.__name__ == "_FailedTest" for test in tests):
        raise HeadlessSuiteError("worker loader produced _FailedTest")
    loaded_ids = tuple(test.id() for test in tests)
    if loaded_ids != expected:
        raise HeadlessSuiteError("worker loaded test IDs drifted from its plan")
    result = HeadlessTestResult(expected)
    with open(os.devnull, "w", encoding="utf-8") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            unittest.TestSuite(tests).run(result)
    events = result.terminal_events()
    return events, 0 if terminal_events_accepted(events) else 1


def run_worker_controller(
    plan: Mapping[str, object],
    *,
    shard_id: str,
    output_dir: Path,
    source_root: Path,
    child_runner: Any,
) -> int:
    try:
        child = child_runner(plan, shard_id=shard_id)
    except (
        HeadlessSuiteError,
        OSError,
        RecursionError,
        subprocess.SubprocessError,
        ValueError,
    ):
        child = ChildRun(b"", None, False)
    if not isinstance(child, ChildRun):
        child = ChildRun(b"", None, False)
    events_payload, receipt = finalize_worker_evidence(
        plan,
        shard_id=shard_id,
        raw_events=child.raw_events,
        return_code=child.return_code,
        timed_out=child.timed_out,
    )
    publish_worker_evidence(
        output_dir,
        events_payload,
        receipt,
        source_root=source_root,
    )
    return 0 if receipt["status"] == "passed" else 1


def _child_command(source_root: Path, plan_path: Path, shard_id: str) -> list[str]:
    return [
        sys.executable,
        "-I",
        str(Path(__file__).resolve()),
        "_child",
        "--source-root",
        str(source_root),
        "--plan",
        str(plan_path),
        "--shard",
        shard_id,
    ]


def run_child_process(
    plan: Mapping[str, object],
    *,
    shard_id: str,
    source_root: Path,
) -> ChildRun:
    from scripts import verify_multigenre_release as release_gate

    validate_plan(plan)
    _plan_shard(plan, shard_id)
    root = Path(source_root).resolve(strict=True)
    containment_root = Path(release_gate.__file__).resolve().parents[1]
    if root != containment_root:
        raise HeadlessSuiteError("worker source diverged from its containment authority")
    with tempfile.TemporaryDirectory(prefix="wf-headless-worker-") as temporary:
        scratch = Path(temporary)
        plan_path = scratch / "plan.json"
        plan_path.write_bytes(canonical_json_bytes(dict(plan)))
        try:
            result = release_gate._run_bounded_subprocess_execution(
                _child_command(root, plan_path, shard_id),
                cwd=root,
                environment=release_gate._sanitized_release_child_environment(os.environ),
                timeout_seconds=_WORKER_TIMEOUT_SECONDS,
                output_limit=_EVENTS_LIMIT,
            )
        except (release_gate.MultigenreReleaseError, OSError, ValueError):
            return ChildRun(b"", None, False)
        if result.stdout_overflow or result.stderr_overflow:
            return ChildRun(b"", None, False)
        return ChildRun(result.stdout, result.return_code, result.timed_out)


def _expected_artifact_names() -> set[str]:
    return {
        f"headless-suite-{runner_os}-py{python_minor}-{shard_id}"
        for runner_os in ("Linux", "Windows")
        for python_minor in ("3.11", "3.12")
        for shard_id in _SHARD_IDS
    }


def _artifact_axis(name: str) -> tuple[str, str, str]:
    match = re.fullmatch(
        r"headless-suite-(Linux|Windows)-py(3\.(?:11|12))-(s(?:0[0-9]|1[0-5]))",
        name,
    )
    if match is None:
        raise HeadlessSuiteError("aggregate artifact name is invalid")
    return match.group(1), match.group(2), match.group(3)


def _validate_receipt(
    receipt: Mapping[str, object],
    *,
    plan: Mapping[str, object],
    plan_payload: bytes,
    shard_id: str,
    events: Sequence[Mapping[str, object]],
    events_payload: bytes,
) -> None:
    if type(receipt) is not dict or set(receipt) != {
        "axis",
        "event_count",
        "events_sha256",
        "format",
        "format_version",
        "outcome_counts",
        "plan_sha256",
        "return_code",
        "shard_id",
        "state",
        "status",
        "timed_out",
        "violations",
    }:
        raise HeadlessSuiteError("aggregate receipt fields are invalid")
    counts: dict[str, int] = {}
    for event in events:
        outcome = event["outcome"]
        counts[outcome] = counts.get(outcome, 0) + 1
    if (
        receipt["format"] != _RECEIPT_FORMAT
        or receipt["format_version"] != _RECEIPT_VERSION
        or receipt["axis"] != plan["axis"]
        or receipt["shard_id"] != shard_id
        or receipt["event_count"] != len(events)
        or receipt["events_sha256"] != hashlib.sha256(events_payload).hexdigest()
        or receipt["plan_sha256"] != hashlib.sha256(plan_payload).hexdigest()
        or receipt["outcome_counts"] != dict(sorted(counts.items()))
        or receipt["return_code"] != 0
        or receipt["state"] != "complete"
        or receipt["status"] != "passed"
        or receipt["timed_out"] is not False
        or receipt["violations"] != []
    ):
        raise HeadlessSuiteError("aggregate receipt is not a successful exact row")


def _bounded_directory_entries(
    directory: Path,
    *,
    limit: int,
    label: str,
) -> tuple[os.DirEntry[str], ...]:
    entries: list[os.DirEntry[str]] = []
    try:
        with os.scandir(directory) as scanned:
            for entry in scanned:
                if len(entries) >= limit:
                    raise HeadlessSuiteError(f"{label} exceeded its entry bound")
                entries.append(entry)
    except HeadlessSuiteError:
        raise
    except OSError as exc:
        raise HeadlessSuiteError(f"{label} is unreadable: {exc}") from exc
    return tuple(entries)


def _safe_directory_entry(entry: os.DirEntry[str], *, directory: bool) -> bool:
    try:
        info = entry.stat(follow_symlinks=False)
    except OSError:
        return False
    expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    return not is_link_or_reparse(info) and expected_type


def aggregate_artifacts(artifacts_root: Path) -> dict[str, Any]:
    requested = Path(os.path.abspath(artifacts_root))
    try:
        root_info = requested.lstat()
        if is_link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise AssetContractError("artifact root is unsafe")
        with open_verified_output_parent(requested, create=False) as retained:
            aggregate = _aggregate_retained_artifacts(retained.path, retained=retained)
            retained.assert_current()
            return aggregate
    except HeadlessSuiteError:
        raise
    except (AssetContractError, OSError) as exc:
        raise HeadlessSuiteError(f"aggregate artifact root is invalid: {exc}") from exc


def _aggregate_retained_artifacts(
    root: Path,
    *,
    retained: Any,
) -> dict[str, Any]:
    entry_list = _bounded_directory_entries(
        root,
        limit=len(_expected_artifact_names()),
        label="aggregate artifact root",
    )
    names = {entry.name for entry in entry_list}
    if (
        len(entry_list) != 64
        or names != _expected_artifact_names()
        or any(not _safe_directory_entry(entry, directory=True) for entry in entry_list)
    ):
        raise HeadlessSuiteError("aggregate artifact rows are not the exact 4x16 matrix")

    axis_plans: dict[tuple[str, str], bytes] = {}
    axis_rows: dict[tuple[str, str], list[dict[str, object]]] = {}
    common_source: dict[str, object] | None = None
    common_discovery: dict[str, object] | None = None
    common_manifest: str | None = None
    common_suite_binding: bytes | None = None
    for name in sorted(names):
        runner_os, python_minor, shard_id = _artifact_axis(name)
        directory = root / name
        file_entries = _bounded_directory_entries(
            directory,
            limit=3,
            label=f"aggregate row {name}",
        )
        if (
            {entry.name for entry in file_entries} != {"events.jsonl", "plan.json", "receipt.json"}
            or len(file_entries) != 3
            or any(not _safe_directory_entry(entry, directory=False) for entry in file_entries)
        ):
            raise HeadlessSuiteError("aggregate row file inventory is invalid")
        plan, plan_payload = _load_canonical_json(directory / "plan.json", limit=_PLAN_LIMIT)
        validate_plan(plan)
        if plan["axis"]["runner_os"] != runner_os or plan["axis"]["python_minor"] != python_minor:
            raise HeadlessSuiteError("aggregate row axis does not match its artifact")
        axis_key = (runner_os, python_minor)
        previous_plan = axis_plans.setdefault(axis_key, plan_payload)
        if previous_plan != plan_payload:
            raise HeadlessSuiteError("aggregate plans drifted within one axis")
        suite_binding = canonical_json_bytes(
            {
                "discovery": plan["discovery"],
                "manifest_sha256": plan["manifest_sha256"],
                "shards": plan["shards"],
                "source": plan["source"],
            }
        )
        if common_source is None:
            common_source = dict(plan["source"])
            common_discovery = dict(plan["discovery"])
            common_manifest = plan["manifest_sha256"]
            common_suite_binding = suite_binding
        elif (
            plan["source"] != common_source
            or plan["discovery"] != common_discovery
            or plan["manifest_sha256"] != common_manifest
        ):
            raise HeadlessSuiteError("aggregate source, discovery, or manifest hashes drifted")
        elif suite_binding != common_suite_binding:
            raise HeadlessSuiteError("aggregate planned test IDs drifted across axes")
        try:
            events_payload = read_bound_bytes(
                directory / "events.jsonl",
                limit=_EVENTS_LIMIT,
            ).payload
            events = parse_terminal_events(events_payload)
            receipt, receipt_payload = _load_canonical_json(
                directory / "receipt.json",
                limit=_RECEIPT_LIMIT,
            )
        except (AssetContractError, OSError) as exc:
            raise HeadlessSuiteError(f"aggregate row payload is unsafe: {exc}") from exc
        shard = _plan_shard(plan, shard_id)
        expected_ids = tuple(shard["expected_test_ids"])
        if tuple(
            event["test_id"] for event in events
        ) != expected_ids or not terminal_events_accepted(events):
            raise HeadlessSuiteError("aggregate terminal outcomes are incomplete or rejected")
        _validate_receipt(
            receipt,
            plan=plan,
            plan_payload=plan_payload,
            shard_id=shard_id,
            events=events,
            events_payload=events_payload,
        )
        axis_rows.setdefault(axis_key, []).append(
            {
                "events_sha256": hashlib.sha256(events_payload).hexdigest(),
                "receipt_sha256": hashlib.sha256(receipt_payload).hexdigest(),
                "shard_id": shard_id,
                "test_count": len(events),
            }
        )

    if set(axis_rows) != {
        ("Linux", "3.11"),
        ("Linux", "3.12"),
        ("Windows", "3.11"),
        ("Windows", "3.12"),
    } or any(len(rows) != 16 for rows in axis_rows.values()):
        raise HeadlessSuiteError("aggregate axes are incomplete")
    axes = []
    for axis_key in sorted(axis_rows):
        plan = json.loads(axis_plans[axis_key])
        planned_ids = {
            test_id for shard in plan["shards"] for test_id in shard["expected_test_ids"]
        }
        observed_count = sum(row["test_count"] for row in axis_rows[axis_key])
        if len(planned_ids) != plan["discovery"]["test_count"] or observed_count != len(
            planned_ids
        ):
            raise HeadlessSuiteError("aggregate shard union has a gap or overlap")
        axes.append(
            {
                **dict(plan["axis"]),
                "plan_sha256": hashlib.sha256(axis_plans[axis_key]).hexdigest(),
                "shards": sorted(axis_rows[axis_key], key=lambda row: row["shard_id"]),
            }
        )
    assert common_source is not None
    assert common_discovery is not None
    assert common_manifest is not None
    retained.assert_current()
    return {
        "axes": axes,
        "discovery": common_discovery,
        "format": _AGGREGATE_FORMAT,
        "format_version": _AGGREGATE_VERSION,
        "manifest_sha256": common_manifest,
        "source": common_source,
        "status": "passed",
    }


def run_aggregate_controller(
    artifacts_root: Path,
    *,
    output_dir: Path,
    source_root: Path,
) -> int:
    try:
        aggregate = aggregate_artifacts(artifacts_root)
    except HeadlessSuiteError:
        attempt = {
            "authoritative": False,
            "format": _AGGREGATE_ATTEMPT_FORMAT,
            "format_version": 1,
            "reason_code": "headless_suite_aggregate_invalid",
            "state": "failed",
        }
        try:
            _publish_payloads(
                output_dir,
                (("aggregate-attempt.json", canonical_json_bytes(attempt), _RECEIPT_LIMIT),),
                source_root=source_root,
            )
        except HeadlessSuiteError:
            pass
        return 1
    attempt = {
        "authoritative": False,
        "format": _AGGREGATE_ATTEMPT_FORMAT,
        "format_version": 1,
        "reason_code": None,
        "state": "awaiting_aggregate",
    }
    try:
        _publish_payloads(
            output_dir,
            (
                ("aggregate-attempt.json", canonical_json_bytes(attempt), _RECEIPT_LIMIT),
                ("aggregate.json", canonical_json_bytes(aggregate), _AGGREGATE_LIMIT),
            ),
            source_root=source_root,
        )
    except HeadlessSuiteError:
        return 1
    return 0


def _current_plan(
    *,
    source_root: Path,
    manifest_path: Path,
    github_sha: str,
    runner_os: str,
    runner_image: str,
    python_minor: str,
) -> dict[str, Any]:
    manifest, manifest_payload = _load_canonical_json(manifest_path, limit=_MANIFEST_LIMIT)
    validate_manifest(manifest)
    commit, tree = resolve_git_source(source_root, github_sha=github_sha)
    discovery = discover_suite(source_root, manifest)
    actual_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    if python_minor != actual_minor:
        raise HeadlessSuiteError("requested Python minor does not match the interpreter")
    return build_plan(
        manifest,
        discovery,
        manifest_sha256=hashlib.sha256(manifest_payload).hexdigest(),
        commit=commit,
        source_tree=tree,
        runner_os=runner_os,
        runner_image=runner_image,
        python_full_version=platform.python_version(),
        python_minor=python_minor,
    )


def _child_main(*, source_root: Path, plan_path: Path, shard_id: str) -> int:
    root = Path(source_root).resolve(strict=True)
    tests_root = str((root / "tests").resolve(strict=True))
    for entry in (tests_root, str(root)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    plan = load_plan(plan_path)
    events, return_code = execute_planned_tests(plan, shard_id=shard_id)
    sys.stdout.buffer.write(encode_terminal_events(events))
    sys.stdout.buffer.flush()
    return return_code


def resolve_git_source(source_root: Path, *, github_sha: str) -> tuple[str, str]:
    if type(github_sha) is not str or _HEX_40.fullmatch(github_sha) is None:
        raise HeadlessSuiteError("GITHUB_SHA is invalid")
    root = Path(source_root).resolve(strict=True)
    try:
        worktree = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise HeadlessSuiteError(f"source worktree could not be verified: {exc}") from exc
    if worktree:
        raise HeadlessSuiteError("source worktree is not clean")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise HeadlessSuiteError(f"source HEAD could not be resolved: {exc}") from exc
    if head != github_sha:
        raise HeadlessSuiteError("GITHUB_SHA does not match source HEAD")
    try:
        tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise HeadlessSuiteError(f"source tree could not be resolved: {exc}") from exc
    if _HEX_40.fullmatch(tree) is None:
        raise HeadlessSuiteError("source tree identity is invalid")
    return head, tree


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for mode in ("plan", "worker"):
        command = subparsers.add_parser(mode)
        command.add_argument("--source-root", type=Path, required=True)
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--output-dir", type=Path, required=True)
        command.add_argument("--github-sha", required=True)
        command.add_argument("--runner-os", required=True)
        command.add_argument("--runner-image", required=True)
        command.add_argument("--python-minor", required=True)
        if mode == "worker":
            command.add_argument("--plan", type=Path, required=True)
            command.add_argument("--shard", choices=_SHARD_IDS, required=True)
    child = subparsers.add_parser("_child", help=argparse.SUPPRESS)
    child.add_argument("--source-root", type=Path, required=True)
    child.add_argument("--plan", type=Path, required=True)
    child.add_argument("--shard", choices=_SHARD_IDS, required=True)
    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--source-root", type=Path, required=True)
    aggregate.add_argument("--artifacts-root", type=Path, required=True)
    aggregate.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "_child":
            return _child_main(
                source_root=args.source_root,
                plan_path=args.plan,
                shard_id=args.shard,
            )
        if args.mode == "aggregate":
            return run_aggregate_controller(
                args.artifacts_root,
                output_dir=args.output_dir,
                source_root=args.source_root,
            )
        expected = _current_plan(
            source_root=args.source_root,
            manifest_path=args.manifest,
            github_sha=args.github_sha,
            runner_os=args.runner_os,
            runner_image=args.runner_image,
            python_minor=args.python_minor,
        )
        if args.mode == "plan":
            publish_plan(args.output_dir, expected, source_root=args.source_root)
            return 0
        plan = load_plan(args.plan)
        require_exact_plan(plan, expected=expected)
        return run_worker_controller(
            plan,
            shard_id=args.shard,
            output_dir=args.output_dir,
            source_root=args.source_root,
            child_runner=lambda document, *, shard_id: run_child_process(
                document,
                shard_id=shard_id,
                source_root=args.source_root,
            ),
        )
    except (HeadlessSuiteError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {"reason_code": "headless_suite_failed", "status": "failed", "detail": str(exc)},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
