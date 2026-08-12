"""Hosted native release candidate boundary for exact public GitHub release evidence.

This module deliberately separates structural hosted-release candidates from
positive release authority.  Python is not a sandbox: these checks block data/API
forgery through normal public APIs, not arbitrary same-interpreter code with
frame, ctypes, or memory-control privileges.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from worldforge import multigenre_release_contract as release_gate
from worldforge.asset_io import AssetContractError, read_bound_bytes, write_bytes_atomic
from worldforge.creation_contracts import _validate_json_structure
from worldforge.integrity import canonical_json_bytes

HOSTED_NATIVE_RELEASE_AUTHORITY_FORMAT = "world-forge.hosted_native_release_authority"
HOSTED_NATIVE_RELEASE_AUTHORITY_VERSION = 1
HOSTED_NATIVE_RELEASE_AUTHORITY_SCHEMA_ID = (
    "https://world-forge.local/schemas/hosted-native-release-authority.schema.json"
)
HOSTED_NATIVE_ATTESTATION_RECEIPT_FORMAT = "world-forge.hosted_native_release_attestation_receipt"
MAX_HOSTED_NATIVE_RELEASE_AUTHORITY_BYTES = 16 * 1024 * 1024
REPOSITORY_ID = "1305601753"
_ALLOWED_REPOSITORIES = frozenset({"DrHepa/rpg-world-forge", "DrHepa/world-forge"})
_MAIN_REF = "refs/heads/main"
_EVENT = "push"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID_RE = re.compile(r"^[1-9][0-9]{0,31}$")


class HostedNativeReleaseAuthorityError(ValueError):
    """Raised when hosted native release authority cannot be proven exactly."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}")


def _fail(reason_code: str, detail: str) -> None:
    raise HostedNativeReleaseAuthorityError(reason_code, detail)


def _sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _sha1(value: object) -> bool:
    return isinstance(value, str) and _SHA1_RE.fullmatch(value) is not None


def _content_hash(document: Mapping[str, object]) -> str:
    payload = dict(document)
    payload.pop("content_hash", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def canonical_hosted_authority_id(document: Mapping[str, object]) -> str:
    payload = dict(document)
    payload.pop("authority_id", None)
    payload.pop("content_hash", None)
    return "hosted_native_release_" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:40]


def _seal(document: dict[str, Any]) -> dict[str, Any]:
    document["authority_id"] = canonical_hosted_authority_id(document)
    document["content_hash"] = _content_hash(document)
    return document


@dataclass(frozen=True, slots=True)
class _BoundIdentity:
    sha256: str
    identity: tuple[int, int]
    size_bytes: int


def _freeze_json(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return copy.deepcopy(value)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    return copy.deepcopy(value)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(_thaw_json(value))).hexdigest()


PINNED_GH_VERSION = "2.97.0"
PINNED_GH_ARCHIVE_SHA256 = "a2c9b8497e1f85b1ad0dfcb78b5a622e098801b8e461e459e88e1ee12f018112"
PINNED_GH_BINARY_SHA256 = "141507c337e8b202ad398550c3b73d72f5af92e86f71665214538a81efd4c409"
HOSTED_NATIVE_ATTESTATION_POLICY_ID = (
    "world-forge.hosted_native_release_attestation_receipt.v1.pinned-gh-2.97.0"
)
HOSTED_NATIVE_ATTESTATION_RECEIPT_VERSION = 1
HOSTED_NATIVE_ATTESTATION_RECEIPT_SCHEMA_ID = (
    "https://world-forge.local/schemas/hosted-native-release-attestation-receipt.schema.json"
)
HOSTED_NATIVE_VERIFIER_ENVIRONMENT = {"GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}
HOSTED_NATIVE_SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
_MAX_VERIFIER_OUTPUT_BYTES = 1024 * 1024
_VERIFIER_TIMEOUT_SECONDS = 60.0
_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _reject_duplicate_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not (parsed == parsed and abs(parsed) != float("inf")):
        raise ValueError(f"non-finite JSON number is forbidden: {value}")
    return parsed


def _decode_verifier_json(payload: bytes) -> Any:
    if len(payload) > _MAX_VERIFIER_OUTPUT_BYTES:
        _fail("hosted_native_verifier_result_invalid", "verifier output exceeds byte limit")
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        _fail("hosted_native_verifier_result_invalid", str(exc))


def _closed_dict(
    value: object,
    fields: set[str],
    *,
    context: str,
    reason_code: str = "hosted_native_verifier_result_invalid",
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(reason_code, f"{context} fields are not closed")
    return value


def _bounded_object_evidence(value: object, *, context: str) -> dict[str, object]:
    if type(value) is not dict:
        _fail("hosted_native_verifier_result_invalid", f"{context} must be an object")
    return {"count": 1, "sha256": _canonical_hash(value)}


def _bounded_array_evidence(value: object, *, context: str) -> dict[str, object]:
    if type(value) is not list or not value:
        _fail("hosted_native_verifier_result_invalid", f"{context} must be a non-empty array")
    if len(value) > 16:
        _fail("hosted_native_verifier_result_invalid", f"{context} exceeds bounded evidence count")
    for item in value:
        if type(item) is not dict:
            _fail("hosted_native_verifier_result_invalid", f"{context} entries must be objects")
    return {"count": len(value), "sha256": _canonical_hash(value)}


def parse_hosted_native_verifier_result(
    payload: bytes,
    *,
    expected_subject_name: str,
    expected_subject_sha256: str,
) -> dict[str, Any]:
    """Parse the documented gh 2.97.0 attestation JSON array for this verifier policy."""

    if not isinstance(payload, bytes) or not payload:
        _fail("hosted_native_verifier_result_invalid", "verifier output must be bytes")
    if not isinstance(expected_subject_name, str) or not expected_subject_name:
        _fail("hosted_native_verifier_result_invalid", "expected subject name is invalid")
    if not _sha256(expected_subject_sha256):
        _fail("hosted_native_verifier_result_invalid", "expected subject digest is invalid")
    document = _decode_verifier_json(payload)
    if type(document) is not list or len(document) != 1:
        _fail(
            "hosted_native_verifier_result_invalid", "verifier output root must be one-entry array"
        )
    entry = _closed_dict(
        document[0],
        {"attestation", "verificationResult"},
        context="verifier result",
    )
    attestation = entry["attestation"]
    if type(attestation) is not dict:
        _fail("hosted_native_verifier_result_invalid", "attestation must be an object")
    verification = _closed_dict(
        entry["verificationResult"],
        {"statement", "verifiedTimestamps", "signature"},
        context="verification result",
    )
    statement = _closed_dict(
        verification["statement"],
        {"_type", "predicateType", "subject", "predicate"},
        context="statement",
    )
    subjects = statement["subject"]
    if type(subjects) is not list or len(subjects) != 1:
        _fail("hosted_native_verifier_result_invalid", "exactly one subject is required")
    subject = _closed_dict(subjects[0], {"name", "digest"}, context="subject")
    digest = _closed_dict(subject["digest"], {"sha256"}, context="subject digest")
    if (
        statement["predicateType"] != HOSTED_NATIVE_SLSA_PREDICATE_TYPE
        or subject["name"] != expected_subject_name
        or digest["sha256"] != expected_subject_sha256
    ):
        _fail("hosted_native_verifier_result_invalid", "predicate or subject did not match")
    timestamps_evidence = _bounded_array_evidence(
        verification["verifiedTimestamps"], context="verified timestamps"
    )
    signature = _closed_dict(verification["signature"], {"certificate"}, context="signature")
    certificate_evidence = _bounded_object_evidence(
        signature["certificate"], context="signature certificate"
    )
    return {
        "predicate_type": HOSTED_NATIVE_SLSA_PREDICATE_TYPE,
        "verified_subject": {"name": expected_subject_name, "sha256": expected_subject_sha256},
        "attestation": copy.deepcopy(attestation),
        "statement_sha256": _canonical_hash(statement),
        "verified_timestamps": timestamps_evidence,
        "signature_certificate": certificate_evidence,
    }


def _validate_parsed_verifier_result(
    value: Mapping[str, object], *, expected_subject_name: str, expected_subject_sha256: str
) -> dict[str, Any]:
    parsed = _closed_dict(
        dict(value),
        {
            "predicate_type",
            "verified_subject",
            "attestation",
            "statement_sha256",
            "verified_timestamps",
            "signature_certificate",
        },
        context="parsed verifier result",
    )
    subject = _closed_dict(
        parsed["verified_subject"], {"name", "sha256"}, context="parsed verifier subject"
    )
    if (
        parsed["predicate_type"] != HOSTED_NATIVE_SLSA_PREDICATE_TYPE
        or subject["name"] != expected_subject_name
        or subject["sha256"] != expected_subject_sha256
        or type(parsed["attestation"]) is not dict
        or not _sha256(parsed["statement_sha256"])
    ):
        _fail("hosted_native_verifier_result_invalid", "parsed verifier result did not match")
    for key in ("verified_timestamps", "signature_certificate"):
        evidence = _closed_dict(parsed[key], {"count", "sha256"}, context=f"parsed {key}")
        if (
            type(evidence["count"]) is not int
            or evidence["count"] < 1
            or not _sha256(evidence["sha256"])
        ):
            _fail("hosted_native_verifier_result_invalid", "parsed evidence is invalid")
    return copy.deepcopy(parsed)


def _decode_strict_json_value(payload: bytes, *, reason_code: str, source: object) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_non_finite,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        _fail(reason_code, f"{source}: {exc}")


def _load_bundle_attestation(path: str | Path) -> tuple[bytes, str, dict[str, Any]]:
    payload, payload_sha = _file_sha(path)
    if not payload.strip():
        _fail("hosted_native_bundle_invalid", "bundle is empty")
    decoded: Any
    try:
        decoded = _decode_strict_json_value(
            payload, reason_code="hosted_native_bundle_invalid", source=path
        )
        if payload != canonical_json_bytes(decoded):
            _fail("hosted_native_bundle_invalid", "bundle JSON must be canonical")
    except HostedNativeReleaseAuthorityError:
        lines = [line for line in payload.splitlines() if line.strip()]
        if len(lines) != 1:
            _fail("hosted_native_bundle_invalid", "bundle JSONL must contain exactly one entry")
        decoded = _decode_strict_json_value(
            lines[0], reason_code="hosted_native_bundle_invalid", source=path
        )
        if payload != lines[0] + b"\n":
            _fail("hosted_native_bundle_invalid", "bundle JSONL must be one canonical line")
    if type(decoded) is not dict:
        _fail("hosted_native_bundle_invalid", "bundle attestation must be an object")
    return payload, payload_sha, decoded


def _run_bounded(argv: Sequence[str], *, runner: Callable[..., Any] | None = None) -> Any:
    if any(type(item) is not str or not item for item in argv):
        _fail("hosted_native_verifier_failed", "verifier argv must be non-empty strings")
    env = dict(HOSTED_NATIVE_VERIFIER_ENVIRONMENT)
    run = subprocess.run if runner is None else runner
    try:
        return run(
            list(argv),
            capture_output=True,
            env=env,
            input=None,
            shell=False,
            timeout=_VERIFIER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        _fail("hosted_native_verifier_failed", f"verifier timed out: {exc}")
    except OSError as exc:
        _fail("hosted_native_verifier_failed", str(exc))


def _bounded_process_output(value: object, *, stream: str) -> bytes:
    if type(value) is str:
        payload = value.encode("utf-8", errors="replace")
    elif type(value) is bytes:
        payload = value
    elif value is None:
        payload = b""
    else:
        _fail("hosted_native_verifier_failed", f"{stream} has unsupported type")
    if len(payload) > _MAX_VERIFIER_OUTPUT_BYTES:
        _fail("hosted_native_verifier_failed", f"{stream} exceeds byte limit")
    return payload


def _precheck_pinned_path(path_value: str | Path, *, role: str) -> Path:
    path = Path(path_value)
    try:
        info = path.lstat()
    except OSError as exc:
        _fail("hosted_native_verifier_file_invalid", str(exc))
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail("hosted_native_verifier_file_invalid", f"{role} must be one standalone regular file")
    return path


def _read_pinned_file(
    path_value: str | Path,
    *,
    expected_sha256: str,
    mismatch_code: str,
    role: str,
    limit: int,
) -> tuple[Path, str]:
    path = _precheck_pinned_path(path_value, role=role)
    try:
        payload = read_bound_bytes(path, limit=limit).payload
    except (AssetContractError, OSError) as exc:
        _fail("hosted_native_verifier_file_invalid", str(exc))
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        _fail(mismatch_code, f"{role} sha256 is not pinned")
    return path, digest


def _verify_pinned_github_cli_impl(
    archive_path: str | Path,
    gh_path: str | Path,
    *,
    runner: Callable[..., Any] | None = None,
    expected_archive_sha256: str = PINNED_GH_ARCHIVE_SHA256,
    expected_binary_sha256: str = PINNED_GH_BINARY_SHA256,
) -> dict[str, str]:
    _precheck_pinned_path(archive_path, role="gh archive")
    _precheck_pinned_path(gh_path, role="gh binary")
    archive, archive_digest = _read_pinned_file(
        archive_path,
        expected_sha256=expected_archive_sha256,
        mismatch_code="hosted_native_verifier_archive_mismatch",
        role="gh archive",
        limit=128 * 1024 * 1024,
    )
    path, binary_digest = _read_pinned_file(
        gh_path,
        expected_sha256=expected_binary_sha256,
        mismatch_code="hosted_native_verifier_binary_mismatch",
        role="gh binary",
        limit=64 * 1024 * 1024,
    )
    if archive.resolve() == path.resolve():
        _fail("hosted_native_verifier_file_invalid", "gh archive and binary must be distinct files")
    result = _run_bounded([str(path), "--version"], runner=runner)
    stdout = _bounded_process_output(getattr(result, "stdout", b""), stream="stdout")
    _bounded_process_output(getattr(result, "stderr", b""), stream="stderr")
    if getattr(result, "returncode", 1) != 0:
        _fail("hosted_native_verifier_failed", "gh --version failed")
    first_line = stdout.decode("utf-8", errors="replace").splitlines()[0:1]
    if not first_line or not first_line[0].startswith(f"gh version {PINNED_GH_VERSION} "):
        _fail("hosted_native_verifier_version_mismatch", "gh version is not pinned")
    return {
        "name": "gh",
        "version": PINNED_GH_VERSION,
        "archive_sha256": archive_digest,
        "binary_sha256": binary_digest,
        "closed_policy_id": HOSTED_NATIVE_ATTESTATION_POLICY_ID,
    }


def verify_pinned_github_cli(archive_path: str | Path, gh_path: str | Path) -> dict[str, str]:
    return _verify_pinned_github_cli_impl(archive_path, gh_path)


def _verify_pinned_github_cli_for_tests(
    archive_path: str | Path,
    gh_path: str | Path,
    *,
    runner: Callable[..., Any],
    expected_archive_sha256: str,
    expected_binary_sha256: str,
) -> dict[str, str]:
    return _verify_pinned_github_cli_impl(
        archive_path,
        gh_path,
        runner=runner,
        expected_archive_sha256=expected_archive_sha256,
        expected_binary_sha256=expected_binary_sha256,
    )


def _validate_command_policy_environment(value: object, *, context: str) -> dict[str, str]:
    environment = _closed_dict(
        value,
        set(HOSTED_NATIVE_VERIFIER_ENVIRONMENT),
        context=context,
        reason_code="hosted_native_receipt_invalid",
    )
    if environment != HOSTED_NATIVE_VERIFIER_ENVIRONMENT:
        _fail("hosted_native_receipt_invalid", f"{context} is invalid")
    return dict(HOSTED_NATIVE_VERIFIER_ENVIRONMENT)


def _candidate_document(candidate: object) -> dict[str, Any]:
    try:
        return validate_hosted_native_release_authority_document(candidate.document)  # type: ignore[attr-defined]
    except AttributeError:
        _fail("hosted_native_candidate_required", "candidate handle is required")


def _file_sha(
    path: str | Path, *, limit: int = MAX_HOSTED_NATIVE_RELEASE_AUTHORITY_BYTES
) -> tuple[bytes, str]:
    try:
        payload = read_bound_bytes(path, limit=limit).payload
    except (AssetContractError, OSError) as exc:
        _fail("hosted_native_read_failed", str(exc))
    return payload, hashlib.sha256(payload).hexdigest()


def _receipt_id(document: Mapping[str, object]) -> str:
    payload = dict(document)
    payload.pop("receipt_id", None)
    payload.pop("content_hash", None)
    return (
        "hosted_native_release_attestation_receipt_"
        + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()[:40]
    )


def _receipt_hash(document: Mapping[str, object]) -> str:
    payload = dict(document)
    payload.pop("content_hash", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_hosted_native_attestation_receipt_document(
    *,
    candidate: object,
    authority_path: str | Path,
    bundle_path: str | Path,
    verifier_result: Mapping[str, object],
    verification_result_sha256: str,
    verifier: Mapping[str, object],
    command_policy: Mapping[str, object],
    informational_attestation: Mapping[str, object],
) -> dict[str, Any]:
    candidate_document = _candidate_document(candidate)
    authority_payload, authority_sha = _file_sha(authority_path)
    if authority_payload != canonical_json_bytes(candidate_document):
        _fail("hosted_native_hash_mismatch", "authority file does not match candidate bytes")
    bundle_payload, bundle_sha, bundle_attestation = _load_bundle_attestation(bundle_path)
    verifier_result_checked = _validate_parsed_verifier_result(
        verifier_result if type(verifier_result) is dict else dict(verifier_result),
        expected_subject_name=Path(authority_path).name,
        expected_subject_sha256=authority_sha,
    )
    verifier_fields = {"name", "version", "archive_sha256", "binary_sha256", "closed_policy_id"}
    if type(verifier) is not dict or set(verifier) != verifier_fields:
        _fail("hosted_native_receipt_invalid", "verifier fields are not closed")
    if (
        verifier.get("name") != "gh"
        or verifier.get("version") != PINNED_GH_VERSION
        or verifier.get("archive_sha256") != PINNED_GH_ARCHIVE_SHA256
        or verifier.get("binary_sha256") != PINNED_GH_BINARY_SHA256
        or verifier.get("closed_policy_id") != HOSTED_NATIVE_ATTESTATION_POLICY_ID
        or not _sha256(verification_result_sha256)
    ):
        _fail("hosted_native_receipt_invalid", "verifier pin or result hash is invalid")
    if canonical_json_bytes(bundle_attestation) != canonical_json_bytes(
        verifier_result_checked["attestation"]
    ):
        _fail("hosted_native_bundle_mismatch", "bundle attestation does not match verifier output")
    command = _closed_dict(
        dict(command_policy),
        {"argv", "timeout_seconds", "environment"},
        context="receipt command policy",
        reason_code="hosted_native_receipt_invalid",
    )
    if (
        type(command["argv"]) is not list
        or any(type(item) is not str or not item for item in command["argv"])
        or command["timeout_seconds"] != int(_VERIFIER_TIMEOUT_SECONDS)
    ):
        _fail("hosted_native_receipt_invalid", "command policy is invalid")
    command["environment"] = _validate_command_policy_environment(
        command["environment"], context="receipt command policy environment"
    )
    metadata_fields = {"id", "url"}
    if (
        type(informational_attestation) is not dict
        or set(informational_attestation) != metadata_fields
        or type(informational_attestation.get("id")) is not str
        or not informational_attestation.get("id")
        or type(informational_attestation.get("url")) is not str
        or not str(informational_attestation.get("url")).startswith("https://github.com/")
    ):
        _fail("hosted_native_receipt_invalid", "informational attestation metadata is invalid")
    source = candidate_document["source"]
    document: dict[str, Any] = {
        "format": HOSTED_NATIVE_ATTESTATION_RECEIPT_FORMAT,
        "format_version": HOSTED_NATIVE_ATTESTATION_RECEIPT_VERSION,
        "receipt_id": "",
        "candidate": {
            "format": candidate_document["format"],
            "format_version": candidate_document["format_version"],
            "id": candidate_document["authority_id"],
            "content_hash": candidate_document["content_hash"],
            "filename": Path(authority_path).name,
            "payload_sha256": authority_sha,
        },
        "source": copy.deepcopy(source),
        "verifier": copy.deepcopy(dict(verifier)),
        "command_policy": copy.deepcopy(dict(command)),
        "informational_attestation": {
            "id": informational_attestation["id"],
            "url": informational_attestation["url"],
            "trust_role": "informational-only",
        },
        "predicate_type": HOSTED_NATIVE_SLSA_PREDICATE_TYPE,
        "bundle": {
            "filename": Path(bundle_path).name,
            "sha256": _canonical_hash(bundle_attestation),
            "payload_sha256": bundle_sha,
        },
        "verification_result_sha256": verification_result_sha256,
        "verified_subject": copy.deepcopy(verifier_result_checked["verified_subject"]),
        "verified_evidence": {
            "statement_sha256": verifier_result_checked["statement_sha256"],
            "timestamps": copy.deepcopy(verifier_result_checked["verified_timestamps"]),
            "signature_certificate": copy.deepcopy(
                verifier_result_checked["signature_certificate"]
            ),
        },
        "content_hash": "",
    }
    document["receipt_id"] = _receipt_id(document)
    document["content_hash"] = _receipt_hash(document)
    return document


def validate_hosted_native_attestation_receipt_document(value: object) -> dict[str, Any]:
    try:
        document = copy.deepcopy(value)
        _validate_json_structure(document, context="hosted native attestation receipt")
    except Exception as exc:
        _fail("hosted_native_receipt_invalid", str(exc))
    fields = {
        "format",
        "format_version",
        "receipt_id",
        "candidate",
        "source",
        "verifier",
        "command_policy",
        "informational_attestation",
        "predicate_type",
        "bundle",
        "verification_result_sha256",
        "verified_subject",
        "verified_evidence",
        "content_hash",
    }
    if type(document) is not dict or set(document) != fields:
        _fail("hosted_native_receipt_invalid", "receipt fields are not closed")
    if (
        document.get("format") != HOSTED_NATIVE_ATTESTATION_RECEIPT_FORMAT
        or document.get("format_version") != HOSTED_NATIVE_ATTESTATION_RECEIPT_VERSION
        or not isinstance(document.get("receipt_id"), str)
        or not document["receipt_id"].startswith("hosted_native_release_attestation_receipt_")
        or document.get("predicate_type") != HOSTED_NATIVE_SLSA_PREDICATE_TYPE
        or not _sha256(document.get("verification_result_sha256"))
        or not _sha256(document.get("content_hash"))
    ):
        _fail("hosted_native_receipt_invalid", "receipt identity is invalid")
    candidate = _closed_dict(
        document["candidate"],
        {"format", "format_version", "id", "content_hash", "filename", "payload_sha256"},
        context="receipt candidate",
    )
    if (
        candidate["format"] != HOSTED_NATIVE_RELEASE_AUTHORITY_FORMAT
        or candidate["format_version"] != HOSTED_NATIVE_RELEASE_AUTHORITY_VERSION
        or type(candidate["id"]) is not str
        or not candidate["id"].startswith("hosted_native_release_")
        or not _sha256(candidate["content_hash"])
        or not _sha256(candidate["payload_sha256"])
        or type(candidate["filename"]) is not str
        or Path(candidate["filename"]).name != candidate["filename"]
    ):
        _fail("hosted_native_receipt_invalid", "receipt candidate is invalid")
    _validate_source(
        document["source"],
        {
            "source_input_tree_hash": document["source"].get("input_tree_hash"),
            "source_revision": document["source"].get("revision"),
        },
    )
    verifier = _closed_dict(
        document["verifier"],
        {"name", "version", "archive_sha256", "binary_sha256", "closed_policy_id"},
        context="receipt verifier",
    )
    if verifier != {
        "name": "gh",
        "version": PINNED_GH_VERSION,
        "archive_sha256": PINNED_GH_ARCHIVE_SHA256,
        "binary_sha256": PINNED_GH_BINARY_SHA256,
        "closed_policy_id": HOSTED_NATIVE_ATTESTATION_POLICY_ID,
    }:
        _fail("hosted_native_receipt_invalid", "receipt verifier pin is invalid")
    command = _closed_dict(
        document["command_policy"],
        {"argv", "timeout_seconds", "environment"},
        context="receipt command policy",
        reason_code="hosted_native_receipt_invalid",
    )
    if (
        type(command["argv"]) is not list
        or any(type(item) is not str or not item for item in command["argv"])
        or command["timeout_seconds"] != int(_VERIFIER_TIMEOUT_SECONDS)
    ):
        _fail("hosted_native_receipt_invalid", "receipt command policy is invalid")
    command["environment"] = _validate_command_policy_environment(
        command["environment"], context="receipt command policy environment"
    )
    metadata = _closed_dict(
        document["informational_attestation"],
        {"id", "url", "trust_role"},
        context="receipt informational attestation",
        reason_code="hosted_native_receipt_invalid",
    )
    if (
        type(metadata["id"]) is not str
        or not metadata["id"]
        or type(metadata["url"]) is not str
        or not metadata["url"].startswith("https://github.com/")
        or metadata["trust_role"] != "informational-only"
    ):
        _fail("hosted_native_receipt_invalid", "receipt informational attestation is invalid")
    bundle = _closed_dict(
        document["bundle"],
        {"filename", "sha256", "payload_sha256"},
        context="receipt bundle",
        reason_code="hosted_native_receipt_invalid",
    )
    if (
        type(bundle["filename"]) is not str
        or Path(bundle["filename"]).name != bundle["filename"]
        or not _sha256(bundle["sha256"])
        or not _sha256(bundle["payload_sha256"])
    ):
        _fail("hosted_native_receipt_invalid", "receipt bundle is invalid")
    subject = _closed_dict(
        document["verified_subject"], {"name", "sha256"}, context="receipt subject"
    )
    if subject["name"] != candidate["filename"] or subject["sha256"] != candidate["payload_sha256"]:
        _fail("hosted_native_receipt_invalid", "receipt subject does not bind candidate")
    evidence = _closed_dict(
        document["verified_evidence"],
        {"statement_sha256", "timestamps", "signature_certificate"},
        context="receipt evidence",
        reason_code="hosted_native_receipt_invalid",
    )
    if not _sha256(evidence["statement_sha256"]):
        _fail("hosted_native_receipt_invalid", "receipt statement evidence is invalid")
    for key in ("timestamps", "signature_certificate"):
        nested = _closed_dict(
            evidence[key],
            {"count", "sha256"},
            context=f"receipt {key}",
            reason_code="hosted_native_receipt_invalid",
        )
        if type(nested["count"]) is not int or nested["count"] < 1 or not _sha256(nested["sha256"]):
            _fail("hosted_native_receipt_invalid", "receipt evidence is empty")
    if document["receipt_id"] != _receipt_id(document) or document["content_hash"] != _receipt_hash(
        document
    ):
        _fail("hosted_native_receipt_invalid", "receipt hash is not canonical")
    return document


def serialize_hosted_native_attestation_receipt(value: object) -> bytes:
    return canonical_json_bytes(validate_hosted_native_attestation_receipt_document(value))


def _load_receipt(path: str | Path) -> dict[str, Any]:
    try:
        payload = read_bound_bytes(path, limit=MAX_HOSTED_NATIVE_RELEASE_AUTHORITY_BYTES).payload
        document = release_gate._decode_json_object(
            payload, source=Path(path), reason_code="hosted_native_receipt_invalid"
        )
    except HostedNativeReleaseAuthorityError:
        raise
    except (AssetContractError, OSError, ValueError) as exc:
        _fail("hosted_native_read_failed", str(exc))
    if payload != canonical_json_bytes(document):
        _fail("hosted_native_receipt_invalid", "receipt is not canonical JSON")
    return validate_hosted_native_attestation_receipt_document(document)


def _gh_verify_argv(
    gh_path: str | Path,
    authority_path: str | Path,
    bundle_path: str | Path,
    source: Mapping[str, object],
) -> list[str]:
    return [
        str(gh_path),
        "attestation",
        "verify",
        str(authority_path),
        "--bundle",
        str(bundle_path),
        "--repo",
        str(source["repository"]),
        "--signer-workflow",
        str(source["workflow_ref"]),
        "--source-ref",
        str(source["ref"]),
        "--source-digest",
        str(source["revision"]),
        "--signer-digest",
        str(source["workflow_sha"]),
        "--deny-self-hosted-runners",
        "--predicate-type",
        HOSTED_NATIVE_SLSA_PREDICATE_TYPE,
        "--format",
        "json",
    ]


def _command_policy(argv: Sequence[str]) -> dict[str, object]:
    return {
        "argv": list(argv),
        "timeout_seconds": int(_VERIFIER_TIMEOUT_SECONDS),
        "environment": dict(HOSTED_NATIVE_VERIFIER_ENVIRONMENT),
    }


def _run_gh_verify(
    authority_path: str | Path,
    bundle_path: str | Path,
    gh_archive_path: str | Path,
    gh_path: str | Path,
    source: Mapping[str, object],
) -> tuple[bytes, dict[str, object], dict[str, object]]:
    verifier = verify_pinned_github_cli(gh_archive_path, gh_path)
    argv = _gh_verify_argv(gh_path, authority_path, bundle_path, source)
    result = _run_bounded(argv)
    stdout = _bounded_process_output(getattr(result, "stdout", b""), stream="stdout")
    _bounded_process_output(getattr(result, "stderr", b""), stream="stderr")
    if getattr(result, "returncode", 1) != 0:
        _fail("hosted_native_verifier_failed", "gh attestation verify failed")
    return stdout, verifier, _command_policy(argv)


def write_hosted_native_attestation_receipt(
    candidate: object,
    *,
    authority_path: str | Path,
    bundle_path: str | Path,
    gh_archive_path: str | Path,
    gh_path: str | Path,
    receipt_path: str | Path,
    attestation_id: str,
    attestation_url: str,
) -> dict[str, Any]:
    source = _candidate_document(candidate)["source"]
    stdout, verifier, command_policy = _run_gh_verify(
        authority_path, bundle_path, gh_archive_path, gh_path, source
    )
    _payload, authority_sha = _file_sha(authority_path)
    parsed = parse_hosted_native_verifier_result(
        stdout,
        expected_subject_name=Path(authority_path).name,
        expected_subject_sha256=authority_sha,
    )
    receipt = build_hosted_native_attestation_receipt_document(
        candidate=candidate,
        authority_path=authority_path,
        bundle_path=bundle_path,
        verifier_result=parsed,
        verification_result_sha256=hashlib.sha256(stdout).hexdigest(),
        verifier=verifier,
        command_policy=command_policy,
        informational_attestation={"id": attestation_id, "url": attestation_url},
    )
    try:
        write_bytes_atomic(
            receipt_path, serialize_hosted_native_attestation_receipt(receipt), durable_parent=True
        )
    except AssetContractError as exc:
        _fail("hosted_native_write_failed", str(exc))
    reread = _load_receipt(receipt_path)
    if reread != receipt:
        _fail("hosted_native_hash_mismatch", "receipt reread did not match exact bytes")
    return receipt


def _initialize_hosted_native_trust_boundary() -> tuple[
    type,
    type,
    type,
    type,
    Callable[[str | Path], Any],
    Callable[[Sequence[Any]], Any],
    Callable[[Any], dict[str, Any]],
    Callable[[Any, Mapping[str, object]], Any],
    Callable[[Any, object, object], Any],
    Callable[[str | Path, Any], Any],
]:
    row_registry: dict[int, tuple[str, str, _BoundIdentity, Any]] = {}
    aggregate_registry: dict[int, tuple[str, tuple[int, ...], Any]] = {}
    candidate_registry: dict[int, tuple[str, Any, Any]] = {}

    class _NoCopyMixin:
        __slots__ = ()

        def __setattr__(self, name: str, value: object) -> None:
            raise TypeError(f"{type(self).__name__} is immutable")

        def __copy__(self) -> object:
            raise TypeError(f"{type(self).__name__} cannot be copied")

        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise TypeError(f"{type(self).__name__} cannot be copied")

        def __reduce_ex__(self, protocol: int) -> object:
            raise TypeError(f"{type(self).__name__} cannot be pickled")

        def __reduce__(self) -> object:
            raise TypeError(f"{type(self).__name__} cannot be pickled")

        def __init_subclass__(cls, **kwargs: object) -> None:
            if cls.__bases__ == (_NoCopyMixin,):
                return
            raise TypeError(f"{cls.__name__} cannot be subclassed")

    class Row(_NoCopyMixin):
        """Opaque row handle minted only from one exact canonical report file."""

        __slots__ = ("_document", "_identity", "_sha256")

        def __init__(self) -> None:
            raise TypeError("VerifiedHostedNativeRow is created only by trusted verification")

        @property
        def document(self) -> Mapping[str, Any]:
            return MappingProxyType(_thaw_json(self._document))

        @property
        def sha256(self) -> str:
            return self._sha256

        @property
        def identity(self) -> tuple[int, int]:
            return self._identity.identity

    class Aggregate(_NoCopyMixin):
        """Opaque exact-matrix aggregate minted from verified hosted rows."""

        __slots__ = ("_document", "_rows")

        def __init__(self) -> None:
            raise TypeError("VerifiedHostedNativeAggregate is created only by trusted verification")

        @property
        def document(self) -> Mapping[str, Any]:
            return MappingProxyType(_thaw_json(self._document))

        @property
        def rows(self) -> tuple[Row, ...]:
            return self._rows

    class Candidate(_NoCopyMixin):
        """Opaque structural candidate; not positive hosted native release authority."""

        __slots__ = ("_aggregate", "_document")

        def __init__(self) -> None:
            raise TypeError("HostedNativeReleaseCandidate is created only by trusted verification")

        @property
        def document(self) -> dict[str, Any]:
            return _thaw_json(self._document)

        @property
        def aggregate(self) -> Mapping[str, Any]:
            return MappingProxyType(_thaw_json(self._aggregate))

    class Authority(_NoCopyMixin):
        """Future positive handle; no current code path can mint this class."""

        __slots__ = ("_aggregate", "_document", "_receipt")

        def __init__(self, *args: object, **kwargs: object) -> None:
            raise TypeError(
                "VerifiedHostedNativeReleaseAuthority is created only by trusted verification"
            )

        @property
        def document(self) -> dict[str, Any]:
            return _thaw_json(self._document)

        @property
        def aggregate(self) -> Mapping[str, Any]:
            return MappingProxyType(_thaw_json(self._aggregate))

    Row.__name__ = "VerifiedHostedNativeRow"
    Row.__qualname__ = "VerifiedHostedNativeRow"
    Aggregate.__name__ = "VerifiedHostedNativeAggregate"
    Aggregate.__qualname__ = "VerifiedHostedNativeAggregate"
    Candidate.__name__ = "HostedNativeReleaseCandidate"
    Candidate.__qualname__ = "HostedNativeReleaseCandidate"
    Authority.__name__ = "VerifiedHostedNativeReleaseAuthority"
    Authority.__qualname__ = "VerifiedHostedNativeReleaseAuthority"

    def mint_row(document: Mapping[str, Any], sha256: str, identity: _BoundIdentity) -> Any:
        row = object.__new__(Row)
        frozen = _freeze_json(dict(document))
        object.__setattr__(row, "_document", frozen)
        object.__setattr__(row, "_sha256", sha256)
        object.__setattr__(row, "_identity", identity)
        row_registry[id(row)] = (_canonical_hash(frozen), sha256, identity, frozen)
        return row

    def require_row(value: object) -> Any:
        if type(value) is not Row:
            _fail("hosted_native_row_required", "an exact verified hosted row handle is required")
        try:
            document = value._document
            sha256 = value._sha256
            identity = value._identity
        except AttributeError:
            _fail("hosted_native_row_required", "row handle is incomplete")
        ledger = row_registry.get(id(value))
        canonical = _canonical_hash(document)
        if (
            ledger is None
            or ledger != (canonical, sha256, identity, document)
            or canonical != sha256
        ):
            _fail("hosted_native_row_required", "row handle failed ledger revalidation")
        return value

    def mint_aggregate(document: Mapping[str, Any], rows: Sequence[Any]) -> Any:
        aggregate = object.__new__(Aggregate)
        frozen = _freeze_json(dict(document))
        checked_rows = tuple(require_row(row) for row in rows)
        object.__setattr__(aggregate, "_document", frozen)
        object.__setattr__(aggregate, "_rows", checked_rows)
        row_ids = tuple(id(row) for row in checked_rows)
        aggregate_registry[id(aggregate)] = (_canonical_hash(frozen), row_ids, frozen)
        return aggregate

    def require_aggregate(value: object) -> Any:
        if type(value) is not Aggregate:
            _fail(
                "hosted_native_aggregate_required",
                "an exact verified hosted aggregate handle is required",
            )
        try:
            document = value._document
            rows = value._rows
        except AttributeError:
            _fail("hosted_native_aggregate_required", "aggregate handle is incomplete")
        for row in rows:
            require_row(row)
        ledger = aggregate_registry.get(id(value))
        canonical = _canonical_hash(document)
        if ledger != (canonical, tuple(id(row) for row in rows), document):
            _fail("hosted_native_aggregate_required", "aggregate handle failed ledger revalidation")
        return value

    def mint_candidate(document: Mapping[str, Any], aggregate: Any) -> Any:
        checked = require_aggregate(aggregate)
        candidate = object.__new__(Candidate)
        frozen_document = _freeze_json(dict(document))
        frozen_aggregate = _freeze_json(_thaw_json(checked._document))
        object.__setattr__(candidate, "_document", frozen_document)
        object.__setattr__(candidate, "_aggregate", frozen_aggregate)
        candidate_registry[id(candidate)] = (
            _canonical_hash(frozen_document),
            frozen_document,
            frozen_aggregate,
        )
        return candidate

    def require_candidate(value: object) -> Any:
        if type(value) is not Candidate:
            _fail(
                "hosted_native_candidate_required",
                "an exact hosted native release candidate handle is required",
            )
        try:
            document = value._document
            aggregate = value._aggregate
        except AttributeError:
            _fail("hosted_native_candidate_required", "candidate handle is incomplete")
        canonical = _canonical_hash(document)
        if candidate_registry.get(id(value)) != (canonical, document, aggregate):
            _fail("hosted_native_candidate_required", "candidate handle failed ledger revalidation")
        validated = validate_hosted_native_release_authority_document(_thaw_json(document))
        if validated["aggregate"] != _thaw_json(aggregate):
            _fail("hosted_native_candidate_required", "candidate aggregate drifted")
        return value

    def loaded_report_from_row(row: Row) -> release_gate.LoadedReleaseReport:
        checked = require_row(row)
        document = _thaw_json(checked._document)
        payload = canonical_json_bytes(document)
        return release_gate.LoadedReleaseReport(
            document=document,
            payload=payload,
            sha256=checked._sha256,
        )

    def row_reference(row: Row) -> dict[str, Any]:
        checked = require_row(row)
        document = _thaw_json(checked._document)
        host = document["host"]
        return {
            "os": host["os"],
            "python_minor": host["python_minor"],
            "python_abi": host["python_abi"],
            "runner_image": host["runner_image"],
            "report_format": document["format"],
            "report_version": document["format_version"],
            "report_sha256": checked._sha256,
            "source_revision": document["source"]["revision"],
            "source_input_tree_hash": document["source"]["input_tree_hash"],
            "native_mode": document["native_mode"],
            "status": document["status"],
        }

    def verify_row_file(path: str | Path) -> Row:
        """Verify one canonical hosted multigenre row file and return an opaque handle."""

        source = Path(path)
        try:
            retained = read_bound_bytes(source, limit=release_gate._MAX_PROCESS_OUTPUT_BYTES)
            document = release_gate._decode_json_object(retained.payload, source=source)
            if retained.payload != canonical_json_bytes(document):
                _fail("hosted_native_row_invalid", f"{source}: row is not canonical JSON")
            checked = release_gate.validate_release_report(document, hosted=True)
        except HostedNativeReleaseAuthorityError:
            raise
        except (
            AssetContractError,
            OSError,
            release_gate.MultigenreReleaseError,
            ValueError,
        ) as exc:
            _fail("hosted_native_row_invalid", str(exc))
        digest = hashlib.sha256(retained.payload).hexdigest()
        return mint_row(
            document=checked,
            sha256=digest,
            identity=_BoundIdentity(
                sha256=digest,
                identity=retained.identity,
                size_bytes=retained.size_bytes or len(retained.payload),
            ),
        )

    def verify_aggregate(rows: Sequence[Row]) -> Aggregate:
        """Aggregate the exact four hosted rows without accepting public JSON."""

        checked_rows = [require_row(row) for row in rows]
        matrix = [_row_sort_key(_thaw_json(row._document)) for row in checked_rows]
        if len(matrix) != len(set(matrix)):
            _fail("hosted_native_matrix_duplicate", "matrix row appears more than once")
        if set(matrix) != set(release_gate.REQUIRED_MATRIX) or len(matrix) != len(
            release_gate.REQUIRED_MATRIX
        ):
            _fail(
                "hosted_native_matrix_incomplete", "exact linux/windows cp311/cp312 rows required"
            )
        ordered = sorted(checked_rows, key=lambda row: _row_sort_key(_thaw_json(row._document)))
        try:
            aggregate = release_gate.aggregate_release_reports(
                [loaded_report_from_row(row) for row in ordered]
            )
        except release_gate.MultigenreReleaseError as exc:
            reason = (
                "hosted_native_matrix_duplicate"
                if exc.reason_code.endswith("duplicate")
                else "hosted_native_matrix_incomplete"
                if exc.reason_code.endswith("incomplete")
                else "hosted_native_row_invalid"
            )
            _fail(reason, str(exc))
        return mint_aggregate(document=aggregate, rows=ordered)

    def build_authority(
        aggregate: Aggregate,
        *,
        source: Mapping[str, object],
    ) -> dict[str, Any]:
        """Build the canonical structural candidate document from opaque aggregate evidence."""

        checked = require_aggregate(aggregate)
        aggregate_document = _thaw_json(checked._document)
        source_document = _validate_source(dict(source), aggregate_document)
        rows = [row_reference(row) for row in checked._rows]
        reports = [_thaw_json(row._document) for row in checked._rows]
        subjects = [_subject_reference(case_id, reports) for case_id in release_gate.CASES]
        dimensions = {
            "repositories": [source_document["repository"]],
            "refs": [_MAIN_REF],
            "events": [_EVENT],
            "platforms": ["linux", "windows"],
            "python_abis": ["cp311", "cp312"],
            "subjects": list(release_gate.CASES),
            "native_modes": ["required"],
        }
        return _seal(
            {
                "format": HOSTED_NATIVE_RELEASE_AUTHORITY_FORMAT,
                "format_version": HOSTED_NATIVE_RELEASE_AUTHORITY_VERSION,
                "authority_id": "",
                "source": source_document,
                "aggregate": aggregate_document,
                "rows": rows,
                "subjects": subjects,
                "dimensions": dimensions,
                "release_status": "ready",
                "supported": True,
                "reason_codes": [],
                "content_hash": "",
            }
        )

    def derive_authority(
        aggregate: Aggregate,
        document: Mapping[str, object],
    ) -> Candidate:
        checked = require_aggregate(aggregate)
        validated = validate_hosted_native_release_authority_document(document)
        expected = build_authority(checked, source=validated["source"])
        if validated != expected:
            _fail(
                "hosted_native_subject_mismatch", "candidate is not derived from exact row evidence"
            )
        return mint_candidate(document=validated, aggregate=checked)

    def verify_attested(
        candidate: Candidate, receipt: object, gh_archive: object, verifier: object | None = None
    ) -> Authority:
        checked = require_candidate(candidate)
        if not isinstance(receipt, (str, Path)):
            _fail("hosted_native_receipt_invalid", "receipt path is required")
        if not isinstance(gh_archive, (str, Path)) or not isinstance(verifier, (str, Path)):
            _fail(
                "hosted_native_verifier_file_invalid",
                "pinned gh archive and binary paths are required",
            )
        receipt_document = _load_receipt(receipt)
        candidate_document = _thaw_json(checked._document)
        if (
            receipt_document["candidate"]["id"] != candidate_document["authority_id"]
            or receipt_document["candidate"]["content_hash"] != candidate_document["content_hash"]
        ):
            _fail("hosted_native_hash_mismatch", "receipt does not bind candidate")
        # Rerun the pinned verifier before minting; raw/copy receipt JSON is never sufficient.
        authority_path = Path(receipt).with_name(str(receipt_document["candidate"]["filename"]))
        bundle_path = Path(receipt).with_name(str(receipt_document["bundle"]["filename"]))
        stdout, verifier_info, command_policy = _run_gh_verify(
            authority_path, bundle_path, gh_archive, verifier, candidate_document["source"]
        )
        authority_payload, authority_sha = _file_sha(authority_path)
        if authority_payload != canonical_json_bytes(candidate_document):
            _fail("hosted_native_hash_mismatch", "authority file does not match candidate")
        parsed = parse_hosted_native_verifier_result(
            stdout,
            expected_subject_name=receipt_document["candidate"]["filename"],
            expected_subject_sha256=authority_sha,
        )
        expected = build_hosted_native_attestation_receipt_document(
            candidate=candidate,
            authority_path=authority_path,
            bundle_path=bundle_path,
            verifier_result=parsed,
            verification_result_sha256=hashlib.sha256(stdout).hexdigest(),
            verifier=verifier_info,
            command_policy=command_policy,
            informational_attestation={
                "id": receipt_document["informational_attestation"]["id"],
                "url": receipt_document["informational_attestation"]["url"],
            },
        )
        if expected != receipt_document:
            _fail("hosted_native_hash_mismatch", "receipt no longer matches pinned verification")
        authority = object.__new__(Authority)
        object.__setattr__(authority, "_document", _freeze_json(candidate_document))
        object.__setattr__(authority, "_aggregate", checked._aggregate)
        object.__setattr__(authority, "_receipt", _freeze_json(receipt_document))
        return authority

    def verify_file(path: str | Path, candidate: Candidate) -> Authority:
        """Verify candidate file binding before requiring an attested receipt sidecar."""

        checked = require_candidate(candidate)
        source = Path(path)
        try:
            retained = read_bound_bytes(source, limit=MAX_HOSTED_NATIVE_RELEASE_AUTHORITY_BYTES)
            document = _load_hosted_native_release_authority_from_retained(
                retained.payload,
                source=source,
            )
        except HostedNativeReleaseAuthorityError:
            raise
        except (AssetContractError, OSError, ValueError) as exc:
            _fail("hosted_native_read_failed", str(exc))
        expected_document = _thaw_json(checked._document)
        expected_payload = canonical_json_bytes(expected_document)
        if (
            document != expected_document
            or retained.payload != expected_payload
            or hashlib.sha256(retained.payload).hexdigest() != _canonical_hash(checked._document)
        ):
            _fail("hosted_native_hash_mismatch", "authority file does not match candidate record")
        _fail(
            "hosted_native_attestation_unavailable",
            "attestation receipt and pinned gh path are required",
        )

    public_names = {
        verify_row_file: "verify_hosted_native_row_file",
        verify_aggregate: "verify_hosted_native_aggregate",
        build_authority: "build_hosted_native_release_authority",
        derive_authority: "derive_hosted_native_release_authority",
        verify_attested: "verify_attested_hosted_native_release_authority_file",
        verify_file: "verify_hosted_native_release_authority_file",
    }
    for function, name in public_names.items():
        function.__name__ = name
        function.__qualname__ = name

    return (
        Row,
        Aggregate,
        Candidate,
        Authority,
        verify_row_file,
        verify_aggregate,
        build_authority,
        derive_authority,
        verify_attested,
        verify_file,
    )


def _row_sort_key(report: Mapping[str, Any]) -> tuple[str, str]:
    host = report["host"]
    return str(host["os"]), str(host["python_minor"])


def _validate_source(source: object, aggregate: Mapping[str, Any]) -> dict[str, Any]:
    if type(source) is not dict:
        _fail("hosted_native_source_invalid", "source must be an object")
    expected = {
        "repository_id",
        "repository",
        "workflow_ref",
        "workflow_sha",
        "revision",
        "input_tree_hash",
        "ref",
        "event",
        "run_id",
        "run_attempt",
    }
    if set(source) != expected:
        _fail("hosted_native_source_invalid", "source fields are not closed")
    repository = source.get("repository")
    revision = source.get("revision")
    workflow_sha = source.get("workflow_sha")
    workflow_ref = source.get("workflow_ref")
    if (
        source.get("repository_id") != REPOSITORY_ID
        or repository not in _ALLOWED_REPOSITORIES
        or source.get("ref") != _MAIN_REF
        or source.get("event") != _EVENT
        or not _sha1(revision)
        or workflow_sha != revision
        or workflow_ref != f"{repository}/.github/workflows/ci.yml@{_MAIN_REF}"
        or source.get("input_tree_hash") != aggregate.get("source_input_tree_hash")
        or revision != aggregate.get("source_revision")
        or not isinstance(source.get("run_id"), str)
        or _RUN_ID_RE.fullmatch(source["run_id"]) is None
        or type(source.get("run_attempt")) is not int
        or source["run_attempt"] < 1
    ):
        _fail("hosted_native_source_invalid", "source is not the exact hosted main push context")
    return copy.deepcopy(source)


def _subject_reference(case_id: str, reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cases = [
        next(case for case in report["cases"] if case["case_id"] == case_id) for report in reports
    ]
    reference = cases[0]
    for case in cases[1:]:
        if any(case[field] != reference[field] for field in ("hashes", "identities", "lineage")):
            _fail("hosted_native_subject_mismatch", f"{case_id} crossed deterministic lineage")
    native_verified = [
        {"os": report["host"]["os"], "python_abi": report["host"]["python_abi"]}
        for report, case in zip(reports, cases, strict=True)
        if case["native_evidence"]["state"] == "passed"
    ]
    if len(native_verified) != len(release_gate.REQUIRED_MATRIX):
        _fail("hosted_native_subject_mismatch", f"{case_id} lacks complete native verification")
    hashes = reference["hashes"]
    identities = reference["identities"]
    runtime_authority = {
        "id": identities["runtime_support_authority_id"],
        "content_hash": hashes["runtime_support_authority"],
    }
    runtime_report = {
        "id": identities["runtime_support_report_id"],
        "content_hash": hashes["runtime_support_report"],
    }
    if (
        runtime_report == runtime_authority
        or runtime_report["content_hash"] == runtime_authority["content_hash"]
    ):
        _fail(
            "hosted_native_subject_mismatch",
            f"{case_id} support report reuses authority identity",
        )
    return {
        "subject_id": case_id,
        "runtime_support_authority": runtime_authority,
        "runtime_support_report": runtime_report,
        "lineage": copy.deepcopy(reference["lineage"]),
        "identities": copy.deepcopy(identities),
        "hashes": copy.deepcopy(hashes),
        "native_verified": native_verified,
        "adapter_verified": True,
        "packaging_verified": True,
        "release_status": "ready",
        "supported": True,
        "reason_codes": [],
    }


def validate_hosted_native_release_authority_document(value: object) -> dict[str, Any]:
    """Validate public structure without returning any verified authority handle."""

    try:
        document = copy.deepcopy(value)
        _validate_json_structure(document, context="hosted native release authority")
    except Exception as exc:
        _fail("hosted_native_invalid", str(exc))
    if type(document) is not dict:
        _fail("hosted_native_invalid", "authority root must be an object")
    fields = {
        "format",
        "format_version",
        "authority_id",
        "source",
        "aggregate",
        "rows",
        "subjects",
        "dimensions",
        "release_status",
        "supported",
        "reason_codes",
        "content_hash",
    }
    if set(document) != fields:
        _fail("hosted_native_invalid", "authority fields are not closed")
    if (
        document.get("format") != HOSTED_NATIVE_RELEASE_AUTHORITY_FORMAT
        or document.get("format_version") != HOSTED_NATIVE_RELEASE_AUTHORITY_VERSION
        or document.get("release_status") != "ready"
        or document.get("supported") is not True
        or document.get("reason_codes") != []
        or not isinstance(document.get("authority_id"), str)
        or not document["authority_id"].startswith("hosted_native_release_")
        or not _sha256(document.get("content_hash"))
    ):
        _fail("hosted_native_invalid", "authority identity is invalid")
    try:
        aggregate = release_gate.validate_aggregate_report(document["aggregate"])
        _validate_source(document["source"], aggregate)
    except release_gate.MultigenreReleaseError as exc:
        _fail("hosted_native_invalid", str(exc))
    rows = document["rows"]
    if type(rows) is not list or len(rows) != 4:
        _fail("hosted_native_invalid", "authority must contain exactly four rows")
    expected_pairs = [
        (os_name, f"cp{minor.replace('.', '')}") for os_name, minor in release_gate.REQUIRED_MATRIX
    ]
    actual_pairs = [(row.get("os"), row.get("python_abi")) for row in rows if type(row) is dict]
    if actual_pairs != expected_pairs:
        _fail("hosted_native_invalid", "authority rows are not canonical")
    row_fields = {
        "os",
        "python_minor",
        "python_abi",
        "runner_image",
        "report_format",
        "report_version",
        "report_sha256",
        "source_revision",
        "source_input_tree_hash",
        "native_mode",
        "status",
    }
    for row, aggregate_row in zip(rows, aggregate["reports"], strict=True):
        if (
            type(row) is not dict
            or set(row) != row_fields
            or row.get("report_sha256") != aggregate_row.get("report_sha256")
            or row.get("native_mode") != "required"
            or row.get("status") != "passed"
            or row.get("source_revision") != aggregate["source_revision"]
            or row.get("source_input_tree_hash") != aggregate["source_input_tree_hash"]
        ):
            _fail("hosted_native_invalid", "authority row does not match aggregate")
    subjects = document["subjects"]
    actual_subjects = (
        [item.get("subject_id") for item in subjects if type(item) is dict]
        if type(subjects) is list
        else []
    )
    if type(subjects) is not list or actual_subjects != list(release_gate.CASES):
        _fail("hosted_native_invalid", "authority subjects are not canonical")
    subject_fields = {
        "subject_id",
        "runtime_support_authority",
        "runtime_support_report",
        "lineage",
        "identities",
        "hashes",
        "native_verified",
        "adapter_verified",
        "packaging_verified",
        "release_status",
        "supported",
        "reason_codes",
    }
    id_hash_fields = {"id", "content_hash"}
    for subject in subjects:
        if (
            type(subject) is not dict
            or set(subject) != subject_fields
            or subject.get("release_status") != "ready"
            or subject.get("supported") is not True
            or subject.get("reason_codes") != []
            or subject.get("adapter_verified") is not True
            or subject.get("packaging_verified") is not True
            or len(subject.get("native_verified", [])) != 4
            or type(subject.get("runtime_support_authority")) is not dict
            or type(subject.get("runtime_support_report")) is not dict
            or set(subject["runtime_support_authority"]) != id_hash_fields
            or set(subject["runtime_support_report"]) != id_hash_fields
            or not _sha256(subject["runtime_support_authority"].get("content_hash"))
            or not _sha256(subject["runtime_support_report"].get("content_hash"))
            or subject["runtime_support_authority"] == subject["runtime_support_report"]
            or subject["runtime_support_authority"].get("content_hash")
            == subject["runtime_support_report"].get("content_hash")
        ):
            _fail("hosted_native_subject_mismatch", "authority subject is not release-ready")
    dimensions = document["dimensions"]
    if dimensions != {
        "repositories": [document["source"]["repository"]],
        "refs": [_MAIN_REF],
        "events": [_EVENT],
        "platforms": ["linux", "windows"],
        "python_abis": ["cp311", "cp312"],
        "subjects": list(release_gate.CASES),
        "native_modes": ["required"],
    }:
        _fail("hosted_native_invalid", "authority dimensions are not the exact closure")
    if document["authority_id"] != canonical_hosted_authority_id(document):
        _fail("hosted_native_hash_mismatch", "authority_id is not canonical")
    if document["content_hash"] != _content_hash(document):
        _fail("hosted_native_hash_mismatch", "content_hash is not canonical")
    return document


def serialize_hosted_native_release_authority(value: object) -> bytes:
    return canonical_json_bytes(validate_hosted_native_release_authority_document(value))


def _load_hosted_native_release_authority_from_retained(
    payload: bytes,
    *,
    source: Path,
) -> dict[str, Any]:
    document = release_gate._decode_json_object(
        payload,
        source=source,
        reason_code="hosted_native_json_invalid",
    )
    if payload != canonical_json_bytes(document):
        _fail("hosted_native_encoding_invalid", "authority is not canonical JSON")
    return validate_hosted_native_release_authority_document(document)


def load_hosted_native_release_authority(path: str | Path) -> dict[str, Any]:
    try:
        retained = read_bound_bytes(path, limit=MAX_HOSTED_NATIVE_RELEASE_AUTHORITY_BYTES)
        return _load_hosted_native_release_authority_from_retained(
            retained.payload,
            source=Path(path),
        )
    except HostedNativeReleaseAuthorityError:
        raise
    except (AssetContractError, OSError, ValueError) as exc:
        _fail("hosted_native_read_failed", str(exc))


(
    VerifiedHostedNativeRow,
    VerifiedHostedNativeAggregate,
    HostedNativeReleaseCandidate,
    VerifiedHostedNativeReleaseAuthority,
    verify_hosted_native_row_file,
    verify_hosted_native_aggregate,
    build_hosted_native_release_authority,
    derive_hosted_native_release_authority,
    verify_attested_hosted_native_release_authority_file,
    verify_hosted_native_release_authority_file,
) = _initialize_hosted_native_trust_boundary()
del _initialize_hosted_native_trust_boundary


__all__ = [
    "HOSTED_NATIVE_ATTESTATION_RECEIPT_FORMAT",
    "HOSTED_NATIVE_ATTESTATION_POLICY_ID",
    "HOSTED_NATIVE_ATTESTATION_RECEIPT_SCHEMA_ID",
    "HOSTED_NATIVE_ATTESTATION_RECEIPT_VERSION",
    "HOSTED_NATIVE_SLSA_PREDICATE_TYPE",
    "PINNED_GH_ARCHIVE_SHA256",
    "PINNED_GH_BINARY_SHA256",
    "PINNED_GH_VERSION",
    "build_hosted_native_attestation_receipt_document",
    "parse_hosted_native_verifier_result",
    "serialize_hosted_native_attestation_receipt",
    "validate_hosted_native_attestation_receipt_document",
    "verify_pinned_github_cli",
    "write_hosted_native_attestation_receipt",
    "HOSTED_NATIVE_RELEASE_AUTHORITY_FORMAT",
    "HOSTED_NATIVE_RELEASE_AUTHORITY_SCHEMA_ID",
    "HOSTED_NATIVE_RELEASE_AUTHORITY_VERSION",
    "MAX_HOSTED_NATIVE_RELEASE_AUTHORITY_BYTES",
    "HostedNativeReleaseAuthorityError",
    "HostedNativeReleaseCandidate",
    "VerifiedHostedNativeAggregate",
    "VerifiedHostedNativeReleaseAuthority",
    "VerifiedHostedNativeRow",
    "build_hosted_native_release_authority",
    "canonical_hosted_authority_id",
    "derive_hosted_native_release_authority",
    "load_hosted_native_release_authority",
    "serialize_hosted_native_release_authority",
    "validate_hosted_native_release_authority_document",
    "verify_attested_hosted_native_release_authority_file",
    "verify_hosted_native_aggregate",
    "verify_hosted_native_release_authority_file",
    "verify_hosted_native_row_file",
]
