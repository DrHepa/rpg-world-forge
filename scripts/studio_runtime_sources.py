#!/usr/bin/env python3
"""Validate the pinned, fail-closed Studio runtime provenance contract.

This module is intentionally stdlib-only.  It validates checked-in provenance;
it does not download, extract, assemble, or execute any runtime artifact.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import math
import re
import sys
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "apps/studio/packaging/runtime-sources.json"

FORMAT = "rpg-world-forge.studio_runtime_sources"
FORMAT_VERSION = 1
SCHEMA_ID = "https://rpg-world-forge.local/schemas/studio-runtime-sources.schema.json"
TARGET_IDS = ("linux-x64", "win32-x64")
ALLOWED_HTTPS_HOSTS = frozenset({"github.com", "registry.npmjs.org", "www.python.org"})
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 128
MAX_JSON_NODES = 100_000
MAX_JSON_NUMBER_TOKEN_LENGTH = 128

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
RELEASE_PATTERN = re.compile(r"^[0-9]{8}$")
BLOCKER_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
JSON_NUMBER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
PORTABLE_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+@%-]{0,254}$")
WINDOWS_RESERVED = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}

REQUIRED_BLOCKERS = (
    "codex_ripgrep_static_dependency_notice_sbom_incomplete",
    "linux_bwrap_lgpl_corresponding_source_relink_build_materials_incomplete",
    "linux_bwrap_musl_provenance_incomplete",
    "pbs_zlib_ng_license_incomplete",
    "linux_berkeley_db_dbm_route_unresolved",
    "windows_vc_runtime_redistribution_authority_unresolved",
    "github_attestation_trust_root_rfc3161_verification_pending",
)

EXPECTED_BLOCKER_SCOPES = {
    "codex_ripgrep_static_dependency_notice_sbom_incomplete": TARGET_IDS,
    "linux_bwrap_lgpl_corresponding_source_relink_build_materials_incomplete": ("linux-x64",),
    "linux_bwrap_musl_provenance_incomplete": ("linux-x64",),
    "pbs_zlib_ng_license_incomplete": ("linux-x64",),
    "linux_berkeley_db_dbm_route_unresolved": ("linux-x64",),
    "windows_vc_runtime_redistribution_authority_unresolved": ("win32-x64",),
    "github_attestation_trust_root_rfc3161_verification_pending": TARGET_IDS,
}

EXPECTED_BLOCKER_DETAILS = {
    "codex_ripgrep_static_dependency_notice_sbom_incomplete": (
        ("Codex", "ripgrep"),
        "Target-specific notices and an SBOM for statically linked dependency corpora are "
        "incomplete.",
    ),
    "linux_bwrap_lgpl_corresponding_source_relink_build_materials_incomplete": (
        ("bwrap",),
        "LGPL corresponding source, relinkable objects, and exact build materials are incomplete.",
    ),
    "linux_bwrap_musl_provenance_incomplete": (
        ("bwrap", "musl"),
        "The static musl linkage provenance for the bundled bwrap helper is incomplete.",
    ),
    "pbs_zlib_ng_license_incomplete": (
        ("python-build-standalone", "zlib-ng"),
        "PYTHON.json references a zlib-ng license that is absent from the matching full archive.",
    ),
    "linux_berkeley_db_dbm_route_unresolved": (
        ("Berkeley DB", "_dbm"),
        "The Linux Berkeley DB-backed _dbm redistribution route or deterministic pruning "
        "decision is unresolved.",
    ),
    "windows_vc_runtime_redistribution_authority_unresolved": (
        ("Microsoft Visual C++ Runtime",),
        "Authority to redistribute the exact bundled Windows VC runtime files is unresolved.",
    ),
    "github_attestation_trust_root_rfc3161_verification_pending": (
        ("GitHub release attestation", "RFC3161"),
        "Full trust-root validation and RFC3161 verification of the release attestation are "
        "pending.",
    ),
}

EXPECTED_CODEX_INVENTORIES: dict[str, tuple[tuple[str, int, str], ...]] = {
    "linux-x64": (
        (
            "bin/codex",
            298516528,
            "a31ae9450a26216eb1e7c53102fd42123dd675974310b0e2ca3aa4cb622a2c15",
        ),
        (
            "bin/codex-code-mode-host",
            46131096,
            "b3c1b98e0272ed4bff2bf0459574ff5489dee3087149648e43b1b665a76373e1",
        ),
        (
            "codex-package.json",
            205,
            "4415fcb6e062b567abf79960dbbd38f046ce3c8fbb1170e35fd8129d476126d8",
        ),
        (
            "codex-path/rg",
            5445512,
            "ebeaf56f8a25e102e9419933423738b3a2a613a444fd749d695e15eba53f71f2",
        ),
        (
            "codex-resources/bwrap",
            529776,
            "7df960565a0dece99240ea4b9d0e011307817f9f3b73176c7b71fda44fe84765",
        ),
        (
            "codex-resources/zsh/bin/zsh",
            898480,
            "67faaaa89242c4a332e16e508a1977cffc24bf7fca31d4411cdfd101f3831ef3",
        ),
    ),
    "win32-x64": (
        (
            "bin/codex-code-mode-host.exe",
            53594928,
            "8e6dfce22a24dfe09f2e53e02ede1fe12b3efba8b0255fff1357adde98da808a",
        ),
        (
            "bin/codex.exe",
            341225264,
            "4b76ded066d0239115ca97473d010c92072bc5c5550a45dd7cbebe1e9eb956a7",
        ),
        (
            "codex-package.json",
            215,
            "67145d1f55da780801444db14878d6567adfd2e29b52d1a9dbe7969d7465df13",
        ),
        (
            "codex-path/rg.exe",
            4266496,
            "decdd4992f3f1b9a5ef9898f1b40ab16886d579d6516b4efd3d5eaa19364e408",
        ),
        (
            "codex-resources/codex-command-runner.exe",
            1271088,
            "ac075278a65a0b80ae433eb389a45cbf1431c6c95368efdb326f15f881c0ed2e",
        ),
        (
            "codex-resources/codex-windows-sandbox-setup.exe",
            8816944,
            "7191d24f6fb4a26cbbce0d2aecd6deb71fa074a8cb5f24a45d2fa2164473885f",
        ),
    ),
}

EXPECTED_CODEX_TARGETS = {
    "linux-x64": {
        "package_version": "0.144.6-linux-x64",
        "filename": "codex-0.144.6-linux-x64.tgz",
        "url": "https://registry.npmjs.org/@openai/codex/-/codex-0.144.6-linux-x64.tgz",
        "size": 131212687,
        "sha256": "b6752eb2e8c10e6fcc96ac5c1c8ad8342cdb9a74504fb84686addf081a7d2868",
        "sri": (
            "sha512-4E7EnzCg0OnBxCyYnwJ+qnZwWHYe0YScr5ucKWbngE9u4+0XrpWELqq2Kn9jl5GZ"
            "K8MDjU7PrJwFIwusHOHjuw=="
        ),
        "payload_root": "package/vendor/x86_64-unknown-linux-musl",
        "entrypoint": "package/vendor/x86_64-unknown-linux-musl/bin/codex",
        "release_filename": "codex-package-x86_64-unknown-linux-musl.tar.gz",
        "release_url": (
            "https://github.com/openai/codex/releases/download/rust-v0.144.6/"
            "codex-package-x86_64-unknown-linux-musl.tar.gz"
        ),
        "release_size": 128013836,
        "release_sha256": ("99ae48e4743da6c530ecd998ab2f7e66572c092f4190c88dca8236c07b06ce1d"),
        "attestation_url": (
            "https://registry.npmjs.org/-/npm/v1/attestations/@openai%2fcodex@0.144.6-linux-x64"
        ),
        "subject": "pkg:npm/%40openai/codex@0.144.6-linux-x64",
        "subject_sha512": (
            "e04ec49f30a0d0e9c1c42c989f027eaa767058761ed1849caf9b9c2966e7804f"
            "6ee3ed17ae95842eaab62a7f639791992bc3038d4ecfac9c05230bac1ce1e3bb"
        ),
    },
    "win32-x64": {
        "package_version": "0.144.6-win32-x64",
        "filename": "codex-0.144.6-win32-x64.tgz",
        "url": "https://registry.npmjs.org/@openai/codex/-/codex-0.144.6-win32-x64.tgz",
        "size": 145169047,
        "sha256": "e04afbe9841be306455d075ad414993a946c94a399e55d7f9ec223f734cd4101",
        "sri": (
            "sha512-dN39VnjEthKz5io1RNWwZDtErdSn07nW3pGUgvlA6DMxgm/nuGaIAZO/sG/Hgxq/"
            "x5j9HteAENfrFgVkpZ0lFg=="
        ),
        "payload_root": "package/vendor/x86_64-pc-windows-msvc",
        "entrypoint": "package/vendor/x86_64-pc-windows-msvc/bin/codex.exe",
        "release_filename": "codex-package-x86_64-pc-windows-msvc.tar.gz",
        "release_url": (
            "https://github.com/openai/codex/releases/download/rust-v0.144.6/"
            "codex-package-x86_64-pc-windows-msvc.tar.gz"
        ),
        "release_size": 139775975,
        "release_sha256": ("81948ef44eb00f499f32bcd38ce326f59f5a132ca4cd6ec2559fb5fc7cfd90a7"),
        "attestation_url": (
            "https://registry.npmjs.org/-/npm/v1/attestations/@openai%2fcodex@0.144.6-win32-x64"
        ),
        "subject": "pkg:npm/%40openai/codex@0.144.6-win32-x64",
        "subject_sha512": (
            "74ddfd5678c4b612b3e62a3544d5b0643b44add4a7d3b9d6de919482f940e833"
            "31826fe7b866880193bfb06fc7831abfc798fd1ed78010d7eb160564a59d2516"
        ),
    },
}

EXPECTED_PYTHON_TARGETS = {
    "linux-x64": {
        "runtime_filename": (
            "cpython-3.12.13+20260718-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
        ),
        "runtime_url": (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260718/cpython-3.12.13%2B20260718-x86_64-unknown-linux-gnu-"
            "install_only_stripped.tar.gz"
        ),
        "runtime_size": 34199823,
        "runtime_sha256": ("5854aa6ec71cad00334d5065633c210b2e7feb40956767a59a91791cadcf0b79"),
        "metadata_filename": (
            "cpython-3.12.13+20260718-x86_64-unknown-linux-gnu-pgo+lto-full.tar.zst"
        ),
        "metadata_url": (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260718/cpython-3.12.13%2B20260718-x86_64-unknown-linux-gnu-"
            "pgo%2Blto-full.tar.zst"
        ),
        "metadata_size": 112247883,
        "metadata_sha256": ("a92c3870c2907c2ad9b8d93c2af28626b5fba535c0abf60ddc01492dec697d2f"),
        "entrypoint": "python/bin/python3",
        "python_json_size": 116971,
        "python_json_sha256": ("9392b3b8fcc51192e2e174c29e39b1c04a5954f470ab7651a708bb75fe7d00ce"),
        "license_inventory_size": 1999,
        "license_inventory_sha256": (
            "cbc93e43a66eec5a259fd0bfa0931cc43433cb5e8d6590179875d30e5c9d613e"
        ),
    },
    "win32-x64": {
        "runtime_filename": (
            "cpython-3.12.13+20260718-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
        ),
        "runtime_url": (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260718/cpython-3.12.13%2B20260718-x86_64-pc-windows-msvc-"
            "install_only_stripped.tar.gz"
        ),
        "runtime_size": 21932298,
        "runtime_sha256": ("0d422a1439ec308e03f47df551bc30f5994727c456e414b026d202bcda9b7c1c"),
        "metadata_filename": ("cpython-3.12.13+20260718-x86_64-pc-windows-msvc-pgo-full.tar.zst"),
        "metadata_url": (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260718/cpython-3.12.13%2B20260718-x86_64-pc-windows-msvc-"
            "pgo-full.tar.zst"
        ),
        "metadata_size": 43451823,
        "metadata_sha256": ("00de77fbc37588bc89d2f47df14f400c35e201afa2a3d7ffb495eab0945da2db"),
        "entrypoint": "python/python.exe",
        "python_json_size": 62407,
        "python_json_sha256": ("facf73b63d9e851d25292b063067cb0826dc44e6f291e3ade963a2cb0a46665d"),
        "license_inventory_size": 2000,
        "license_inventory_sha256": (
            "4fbafee9e54187b96f6210417da89d14c35c2073320469afceb596d46a2ad966"
        ),
    },
}


class RuntimeSourcesError(ValueError):
    """Raised when runtime provenance is invalid or not redistributable."""

    _MESSAGES = {
        "contract_invalid": "runtime provenance contract is invalid",
        "invalid_json": "runtime provenance source is not strict JSON",
        "invalid_utf8": "runtime provenance source is not UTF-8",
        "redistribution_blocked": "Studio runtime redistribution is blocked",
        "root_not_object": "runtime provenance JSON root is not an object",
        "source_read_failed": "runtime provenance source could not be read",
        "source_too_large": "runtime provenance source exceeds its size limit",
    }

    def __init__(self, code: str, context: str) -> None:
        if code not in self._MESSAGES:
            code = "contract_invalid"
        if not re.fullmatch(r"(?:source|\$(?:\.[A-Za-z0-9_]+|\[[0-9]+\])*)", context):
            context = "$"
        self.code = code
        self.context = context
        self.safe_message = self._MESSAGES[code]
        super().__init__(f"{code} at {context}: {self.safe_message}")

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "context": self.context,
            "message": self.safe_message,
        }


class _DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True)
class VerificationReport:
    """Deterministic result of validating one runtime provenance document."""

    release_ready: bool
    release_status: str
    open_blocker_codes: tuple[str, ...]
    target_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "format": "rpg-world-forge.studio_runtime_sources_verification",
            "format_version": 1,
            "valid": True,
            "release_ready": self.release_ready,
            "release_status": self.release_status,
            "open_blocker_codes": list(self.open_blocker_codes),
            "target_ids": list(self.target_ids),
        }


def _fail(path: str, _detail: str) -> NoReturn:
    raise RuntimeSourcesError("contract_invalid", path)


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _pairs_to_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError("duplicate JSON key")
        result[key] = value
    return result


def _parse_finite_decimal(value: str) -> Decimal:
    if len(value) > MAX_JSON_NUMBER_TOKEN_LENGTH:
        raise ValueError("JSON number exceeds its token limit")
    try:
        parsed = Decimal(value)
        binary_value = float(parsed)
    except (ArithmeticError, ValueError):
        raise ValueError("invalid JSON number") from None
    if not parsed.is_finite() or not math.isfinite(binary_value):
        raise ValueError("non-finite JSON number is forbidden")
    return parsed


class _StrictJsonBudgetScanner:
    """Count JSON values before allocation; object keys are grammar, not nodes."""

    def __init__(self, text: str, *, max_depth: int, max_nodes: int) -> None:
        self.text = text
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.index = 0
        self.nodes = 0

    def scan(self) -> None:
        self._skip_whitespace()
        self._scan_value(0)
        self._skip_whitespace()
        if self.index != len(self.text):
            raise ValueError("unexpected data after JSON value")

    def _scan_value(self, depth: int) -> None:
        self.nodes += 1
        if depth > self.max_depth or self.nodes > self.max_nodes:
            raise ValueError("JSON tree limit exceeded")
        character = self._current()
        if character == "{":
            self._scan_object(depth)
        elif character == "[":
            self._scan_array(depth)
        elif character == '"':
            self._scan_string()
        elif self.text.startswith("true", self.index):
            self.index += 4
        elif self.text.startswith("false", self.index):
            self.index += 5
        elif self.text.startswith("null", self.index):
            self.index += 4
        else:
            match = JSON_NUMBER_PATTERN.match(self.text, self.index)
            if match is None or len(match.group(0)) > MAX_JSON_NUMBER_TOKEN_LENGTH:
                raise ValueError("invalid JSON number")
            self.index = match.end()

    def _scan_object(self, depth: int) -> None:
        self.index += 1
        self._skip_whitespace()
        if self._current() == "}":
            self.index += 1
            return
        while True:
            if self._current() != '"':
                raise ValueError("JSON object key must be a string")
            self._scan_string()
            self._skip_whitespace()
            if self._current() != ":":
                raise ValueError("JSON object key must be followed by a colon")
            self.index += 1
            self._skip_whitespace()
            self._scan_value(depth + 1)
            self._skip_whitespace()
            separator = self._current()
            if separator == "}":
                self.index += 1
                return
            if separator != ",":
                raise ValueError("JSON object members must be comma separated")
            self.index += 1
            self._skip_whitespace()

    def _scan_array(self, depth: int) -> None:
        self.index += 1
        self._skip_whitespace()
        if self._current() == "]":
            self.index += 1
            return
        while True:
            self._scan_value(depth + 1)
            self._skip_whitespace()
            separator = self._current()
            if separator == "]":
                self.index += 1
                return
            if separator != ",":
                raise ValueError("JSON array items must be comma separated")
            self.index += 1
            self._skip_whitespace()

    def _scan_string(self) -> None:
        self.index += 1
        while self.index < len(self.text):
            character = self.text[self.index]
            if character == '"':
                self.index += 1
                return
            if character == "\\":
                self.index += 1
                escaped = self._current()
                if escaped == "u":
                    code_point = self.text[self.index + 1 : self.index + 5]
                    if len(code_point) != 4 or any(
                        character not in "0123456789abcdefABCDEF" for character in code_point
                    ):
                        raise ValueError("invalid JSON Unicode escape")
                    self.index += 5
                    continue
                if escaped not in {'"', "\\", "/", "b", "f", "n", "r", "t"}:
                    raise ValueError("invalid JSON escape")
                self.index += 1
                continue
            if ord(character) < 0x20:
                raise ValueError("unescaped JSON control character")
            self.index += 1
        raise ValueError("unterminated JSON string")

    def _skip_whitespace(self) -> None:
        while self._current() in {" ", "\t", "\r", "\n"}:
            self.index += 1

    def _current(self) -> str:
        if self.index >= len(self.text):
            return ""
        return self.text[self.index]


def load_strict_json_bytes(
    raw: bytes,
    *,
    max_bytes: int = MAX_JSON_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
    max_nodes: int = MAX_JSON_NODES,
) -> dict[str, Any]:
    """Load a strict UTF-8 JSON object without duplicate or non-finite numbers."""

    if (
        type(max_bytes) is not int
        or type(max_depth) is not int
        or type(max_nodes) is not int
        or max_bytes < 2
        or max_depth < 0
        or max_nodes < 1
        or len(raw) > max_bytes
    ):
        raise RuntimeSourcesError("invalid_json", "source")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeSourcesError("invalid_utf8", "source") from None
    try:
        _StrictJsonBudgetScanner(
            text,
            max_depth=max_depth,
            max_nodes=max_nodes,
        ).scan()
        document = json.loads(
            text,
            object_pairs_hook=_pairs_to_object,
            parse_float=_parse_finite_decimal,
            parse_int=int,
            parse_constant=_reject_constant,
        )
    except (_DuplicateKeyError, RecursionError, ValueError, json.JSONDecodeError):
        raise RuntimeSourcesError("invalid_json", "source") from None
    if not isinstance(document, dict):
        raise RuntimeSourcesError("root_not_object", "source")
    return document


def load_strict_json(path: Path = DEFAULT_SOURCE) -> dict[str, Any]:
    """Read and strictly decode one bounded provenance source file."""

    try:
        raw = path.read_bytes()
    except OSError:
        raise RuntimeSourcesError("source_read_failed", "source") from None
    if len(raw) > MAX_JSON_BYTES:
        raise RuntimeSourcesError("source_too_large", "source")
    return load_strict_json_bytes(raw)


def _object(value: Any, path: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    actual = set(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        _fail(path, f"object keys do not match; missing={missing}, extra={extra}")
    return value


def _array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    return value


def _string(
    value: Any,
    path: str,
    *,
    nonempty: bool = True,
    max_length: int | None = None,
) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        _fail(path, "must be a non-empty string")
    if max_length is not None and len(value) > max_length:
        _fail(path, f"must be no longer than {max_length} characters")
    if unicodedata.normalize("NFC", value) != value:
        _fail(path, "must be NFC-normalized")
    return value


def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    maximum = 2147483647
    if isinstance(value, bool):
        _fail(path, "must be an integer and not a boolean")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            _fail(path, "must be an integer and not a boolean")
        if value < minimum or value > maximum:
            _fail(path, f"must be between {minimum} and {maximum}")
        normalized = int(value)
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            _fail(path, "must be an integer and not a boolean")
        if value < minimum or value > maximum:
            _fail(path, f"must be between {minimum} and {maximum}")
        normalized = int(value)
    else:
        _fail(path, "must be an integer and not a boolean")
    if normalized < minimum or normalized > maximum:
        _fail(path, f"must be at least {minimum}")
    return normalized


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(path, "must be a boolean")
    return value


def _expect(value: Any, expected: Any, path: str) -> None:
    number_types = (int, float, Decimal)
    if (
        not isinstance(value, bool)
        and not isinstance(expected, bool)
        and isinstance(value, number_types)
        and isinstance(expected, number_types)
    ):
        if isinstance(value, float) and not math.isfinite(value):
            _fail(path, "must equal the pinned numeric value")
        if isinstance(expected, float) and not math.isfinite(expected):
            _fail(path, "must equal the pinned numeric value")
        if Decimal(str(value)) != Decimal(str(expected)):
            _fail(path, "must equal the pinned numeric value")
        return
    if type(value) is not type(expected) or value != expected:
        _fail(path, f"must equal pinned value {expected!r}")


def _sha256(value: Any, path: str) -> str:
    digest = _string(value, path)
    if not SHA256_PATTERN.fullmatch(digest) or len(set(digest)) == 1:
        _fail(path, "must be a non-placeholder lowercase SHA-256")
    return digest


def _sha1(value: Any, path: str) -> str:
    digest = _string(value, path)
    if not SHA1_PATTERN.fullmatch(digest) or len(set(digest)) == 1:
        _fail(path, "must be a non-placeholder lowercase SHA-1")
    return digest


def _portable_filename(value: Any, path: str) -> str:
    filename = _string(value, path, max_length=255)
    if (
        len(filename.encode("utf-8")) > 255
        or "/" in filename
        or "\\" in filename
        or filename in {".", ".."}
        or filename.endswith((" ", "."))
        or not PORTABLE_COMPONENT_PATTERN.fullmatch(filename)
    ):
        _fail(path, "must be a portable canonical filename")
    stem = filename.split(".", 1)[0].casefold()
    if stem in WINDOWS_RESERVED:
        _fail(path, "must not use a reserved Windows filename")
    return filename


def _portable_path(value: Any, path: str) -> str:
    relative = _string(value, path, max_length=1024)
    parsed = PurePosixPath(relative)
    if (
        parsed.is_absolute()
        or parsed.as_posix() != relative
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        _fail(path, "must be a normalized portable relative path")
    for index, component in enumerate(parsed.parts):
        component_path = f"{path}.component[{index}]"
        if (
            len(component.encode("utf-8")) > 255
            or component.endswith((" ", "."))
            or not re.fullmatch(r"^[A-Za-z0-9._+@%-]+$", component)
        ):
            _fail(component_path, "must be a portable canonical path component")
        stem = component.lstrip(".").split(".", 1)[0].casefold()
        if stem in WINDOWS_RESERVED:
            _fail(component_path, "must not use a reserved Windows path component")
    return relative


def _https_url(value: Any, path: str, *, expected_filename: str | None = None) -> str:
    url = _string(value, path, max_length=2048)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise RuntimeSourcesError("contract_invalid", path) from None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HTTPS_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or not parsed.path.startswith("/")
        or parsed.query
        or parsed.fragment
    ):
        _fail(path, "must use an allowlisted credential-free HTTPS URL without query or fragment")
    if expected_filename is not None:
        url_filename = unquote(PurePosixPath(parsed.path).name)
        if url_filename != expected_filename:
            _fail(path, "URL filename must match the declared filename")
    return url


def _artifact(
    value: Any,
    path: str,
    *,
    expected_filename: str,
    expected_url: str,
    expected_size: int,
    expected_sha256: str,
) -> None:
    artifact = _object(value, path, {"filename", "url", "size", "sha256"})
    filename = _portable_filename(artifact["filename"], f"{path}.filename")
    _https_url(artifact["url"], f"{path}.url", expected_filename=filename)
    _integer(artifact["size"], f"{path}.size", minimum=1)
    _sha256(artifact["sha256"], f"{path}.sha256")
    _expect(filename, expected_filename, f"{path}.filename")
    _expect(artifact["url"], expected_url, f"{path}.url")
    _expect(artifact["size"], expected_size, f"{path}.size")
    _expect(artifact["sha256"], expected_sha256, f"{path}.sha256")


def _sri_sha512(value: Any, path: str) -> str:
    sri = _string(value, path)
    if not sri.startswith("sha512-"):
        _fail(path, "must be an sha512 SRI value")
    encoded = sri.removeprefix("sha512-")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise RuntimeSourcesError("contract_invalid", path) from None
    if len(decoded) != 64:
        _fail(path, "sha512 SRI payload must contain 64 bytes")
    return decoded.hex()


def _reject_forbidden_scalars(value: Any, path: str = "$") -> None:
    stack: list[Any] = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
            continue
        if isinstance(node, list):
            stack.extend(node)
            continue
        if isinstance(node, Decimal):
            if not node.is_finite():
                _fail(path, "must not be non-finite")
            continue
        if isinstance(node, float):
            if not math.isfinite(node):
                _fail(path, "must not be non-finite")
            continue
        if isinstance(node, str):
            folded = node.casefold()
            for marker in ("placeholder", "replace-me", "replace_me", "todo", "tbd"):
                if marker in folded:
                    _fail(path, f"must not contain placeholder marker {marker!r}")


def _validate_codex(value: Any) -> None:
    path = "$.codex"
    codex = _object(
        value,
        path,
        {
            "package",
            "version",
            "repository",
            "tag",
            "commit",
            "workflow",
            "release_published_at",
            "release_sha256sums",
            "runtime_layout",
            "targets",
        },
    )
    _expect(codex["package"], "@openai/codex", f"{path}.package")
    version = _string(codex["version"], f"{path}.version")
    if not VERSION_PATTERN.fullmatch(version):
        _fail(f"{path}.version", "must be a semantic version")
    _expect(version, "0.144.6", f"{path}.version")
    _expect(codex["repository"], "https://github.com/openai/codex", f"{path}.repository")
    _https_url(codex["repository"], f"{path}.repository")
    _expect(codex["tag"], f"rust-v{version}", f"{path}.tag")
    _sha1(codex["commit"], f"{path}.commit")
    _expect(
        codex["commit"],
        "5d1fbf26c43abc65a203928b2e31561cb039e06d",
        f"{path}.commit",
    )
    _expect(codex["workflow"], ".github/workflows/rust-release.yml", f"{path}.workflow")
    _portable_path(codex["workflow"], f"{path}.workflow")
    _expect(
        codex["release_published_at"],
        "2026-07-18T13:51:52Z",
        f"{path}.release_published_at",
    )
    _artifact(
        codex["release_sha256sums"],
        f"{path}.release_sha256sums",
        expected_filename="codex-package_SHA256SUMS",
        expected_url=(
            "https://github.com/openai/codex/releases/download/rust-v0.144.6/"
            "codex-package_SHA256SUMS"
        ),
        expected_size=1392,
        expected_sha256="db72a7585c594e141201dea9fea37a3686d2668aaee603b96794712c8e394e0d",
    )
    layout = _object(
        codex["runtime_layout"],
        f"{path}.runtime_layout",
        {"preserve_complete_payload_root", "runtime_downloads_allowed"},
    )
    _expect(
        _boolean(
            layout["preserve_complete_payload_root"],
            f"{path}.runtime_layout.preserve_complete_payload_root",
        ),
        True,
        f"{path}.runtime_layout.preserve_complete_payload_root",
    )
    _expect(
        _boolean(
            layout["runtime_downloads_allowed"],
            f"{path}.runtime_layout.runtime_downloads_allowed",
        ),
        False,
        f"{path}.runtime_layout.runtime_downloads_allowed",
    )

    targets = _array(codex["targets"], f"{path}.targets")
    if len(targets) != len(TARGET_IDS):
        _fail(f"{path}.targets", "must contain exactly the two supported x64 targets")
    actual_ids: list[str] = []
    all_filenames: set[str] = set()
    for index, target_value in enumerate(targets):
        target_path = f"{path}.targets[{index}]"
        target = _object(
            target_value,
            target_path,
            {
                "target_id",
                "package_version",
                "archive",
                "sri",
                "payload_root",
                "entrypoint",
                "release_archive",
                "npm_provenance",
                "inventory",
            },
        )
        target_id = _string(target["target_id"], f"{target_path}.target_id")
        actual_ids.append(target_id)
        if target_id not in EXPECTED_CODEX_TARGETS:
            _fail(f"{target_path}.target_id", "unsupported target")
        expected = EXPECTED_CODEX_TARGETS[target_id]
        _expect(
            target["package_version"],
            expected["package_version"],
            f"{target_path}.package_version",
        )
        _artifact(
            target["archive"],
            f"{target_path}.archive",
            expected_filename=str(expected["filename"]),
            expected_url=str(expected["url"]),
            expected_size=int(expected["size"]),
            expected_sha256=str(expected["sha256"]),
        )
        all_filenames.add(str(target["archive"]["filename"]).casefold())
        sri_hex = _sri_sha512(target["sri"], f"{target_path}.sri")
        _expect(target["sri"], expected["sri"], f"{target_path}.sri")
        _expect(target["payload_root"], expected["payload_root"], f"{target_path}.payload_root")
        _portable_path(target["payload_root"], f"{target_path}.payload_root")
        _expect(target["entrypoint"], expected["entrypoint"], f"{target_path}.entrypoint")
        _portable_path(target["entrypoint"], f"{target_path}.entrypoint")
        if not str(target["entrypoint"]).startswith(f"{target['payload_root']}/"):
            _fail(f"{target_path}.entrypoint", "must be rooted under payload_root")
        _artifact(
            target["release_archive"],
            f"{target_path}.release_archive",
            expected_filename=str(expected["release_filename"]),
            expected_url=str(expected["release_url"]),
            expected_size=int(expected["release_size"]),
            expected_sha256=str(expected["release_sha256"]),
        )
        all_filenames.add(str(target["release_archive"]["filename"]).casefold())

        provenance = _object(
            target["npm_provenance"],
            f"{target_path}.npm_provenance",
            {
                "url",
                "predicate_type",
                "subject",
                "subject_sha512",
                "source_commit",
                "workflow_ref",
                "workflow_path",
            },
        )
        _https_url(provenance["url"], f"{target_path}.npm_provenance.url")
        _expect(
            provenance["url"],
            expected["attestation_url"],
            f"{target_path}.npm_provenance.url",
        )
        _expect(
            provenance["predicate_type"],
            "https://slsa.dev/provenance/v1",
            f"{target_path}.npm_provenance.predicate_type",
        )
        _expect(
            provenance["subject"],
            expected["subject"],
            f"{target_path}.npm_provenance.subject",
        )
        subject_sha512 = _string(
            provenance["subject_sha512"],
            f"{target_path}.npm_provenance.subject_sha512",
        )
        if not re.fullmatch(r"^[0-9a-f]{128}$", subject_sha512):
            _fail(
                f"{target_path}.npm_provenance.subject_sha512",
                "must be a lowercase SHA-512",
            )
        _expect(
            subject_sha512,
            expected["subject_sha512"],
            f"{target_path}.npm_provenance.subject_sha512",
        )
        _expect(
            sri_hex,
            subject_sha512,
            f"{target_path}.npm_provenance.subject_sha512",
        )
        _expect(
            provenance["source_commit"],
            codex["commit"],
            f"{target_path}.npm_provenance.source_commit",
        )
        _expect(
            provenance["workflow_ref"],
            f"refs/tags/{codex['tag']}",
            f"{target_path}.npm_provenance.workflow_ref",
        )
        _expect(
            provenance["workflow_path"],
            codex["workflow"],
            f"{target_path}.npm_provenance.workflow_path",
        )

        inventory = _array(target["inventory"], f"{target_path}.inventory")
        expected_inventory = EXPECTED_CODEX_INVENTORIES[target_id]
        if len(inventory) != len(expected_inventory):
            _fail(f"{target_path}.inventory", "must contain the complete pinned payload inventory")
        normalized_paths: set[str] = set()
        actual_inventory: list[tuple[str, int, str]] = []
        for item_index, item_value in enumerate(inventory):
            item_path = f"{target_path}.inventory[{item_index}]"
            item = _object(item_value, item_path, {"path", "size", "sha256"})
            relative = _portable_path(item["path"], f"{item_path}.path")
            normalized = unicodedata.normalize("NFC", relative).casefold()
            if normalized in normalized_paths:
                _fail(f"{item_path}.path", "collides under NFC/casefold")
            normalized_paths.add(normalized)
            size = _integer(item["size"], f"{item_path}.size", minimum=1)
            digest = _sha256(item["sha256"], f"{item_path}.sha256")
            actual_inventory.append((relative, size, digest))
        _expect(
            tuple(actual_inventory),
            expected_inventory,
            f"{target_path}.inventory",
        )
        relative_entrypoint = str(target["entrypoint"]).removeprefix(f"{target['payload_root']}/")
        if relative_entrypoint not in {item[0] for item in actual_inventory}:
            _fail(f"{target_path}.entrypoint", "must be present in the payload inventory")
    _expect(tuple(actual_ids), TARGET_IDS, f"{path}.targets")
    if len(all_filenames) != len(TARGET_IDS) * 2:
        _fail(f"{path}.targets", "archive filenames must be unique under casefold")


def _validate_python(value: Any) -> None:
    path = "$.python"
    python = _object(
        value,
        path,
        {
            "distribution",
            "python_version",
            "release",
            "repository",
            "commit",
            "release_published_at",
            "release_sha256sums",
            "release_attestation",
            "cpython_source",
            "targets",
        },
    )
    _expect(
        python["distribution"],
        "python-build-standalone",
        f"{path}.distribution",
    )
    version = _string(python["python_version"], f"{path}.python_version")
    if not VERSION_PATTERN.fullmatch(version):
        _fail(f"{path}.python_version", "must be a semantic version")
    _expect(version, "3.12.13", f"{path}.python_version")
    release = _string(python["release"], f"{path}.release")
    if not RELEASE_PATTERN.fullmatch(release):
        _fail(f"{path}.release", "must be an eight-digit immutable release tag")
    _expect(release, "20260718", f"{path}.release")
    _expect(
        python["repository"],
        "https://github.com/astral-sh/python-build-standalone",
        f"{path}.repository",
    )
    _https_url(python["repository"], f"{path}.repository")
    _sha1(python["commit"], f"{path}.commit")
    _expect(
        python["commit"],
        "0e4d9c24b72d28573e622518f09b16aef4a33be8",
        f"{path}.commit",
    )
    _expect(
        python["release_published_at"],
        "2026-07-18T20:43:55Z",
        f"{path}.release_published_at",
    )
    _artifact(
        python["release_sha256sums"],
        f"{path}.release_sha256sums",
        expected_filename="SHA256SUMS",
        expected_url=(
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260718/SHA256SUMS"
        ),
        expected_size=121896,
        expected_sha256="d3634980354a9a30cb30e13a3957780995b423f04b1d29dff537667f36bcd64e",
    )

    attestation = _object(
        python["release_attestation"],
        f"{path}.release_attestation",
        {
            "media_type",
            "predicate_type",
            "release_database_id",
            "subject_uri",
            "subject_commit_sha1",
            "bundle_size",
            "bundle_sha256",
            "verification_status",
        },
    )
    _expect(
        attestation["media_type"],
        "application/vnd.dev.sigstore.bundle.v0.3+json",
        f"{path}.release_attestation.media_type",
    )
    _expect(
        attestation["predicate_type"],
        "https://in-toto.io/attestation/release/v0.2",
        f"{path}.release_attestation.predicate_type",
    )
    _expect(
        _integer(
            attestation["release_database_id"],
            f"{path}.release_attestation.release_database_id",
            minimum=1,
        ),
        356187877,
        f"{path}.release_attestation.release_database_id",
    )
    _expect(
        attestation["subject_uri"],
        "pkg:github/astral-sh/python-build-standalone@20260718",
        f"{path}.release_attestation.subject_uri",
    )
    _sha1(
        attestation["subject_commit_sha1"],
        f"{path}.release_attestation.subject_commit_sha1",
    )
    _expect(
        attestation["subject_commit_sha1"],
        python["commit"],
        f"{path}.release_attestation.subject_commit_sha1",
    )
    _expect(
        _integer(
            attestation["bundle_size"],
            f"{path}.release_attestation.bundle_size",
            minimum=1,
        ),
        201917,
        f"{path}.release_attestation.bundle_size",
    )
    _sha256(
        attestation["bundle_sha256"],
        f"{path}.release_attestation.bundle_sha256",
    )
    _expect(
        attestation["bundle_sha256"],
        "b05936e385b0ec50f847e30a34bb20b41dd78c45ea864b9c175c309ffe5c0b64",
        f"{path}.release_attestation.bundle_sha256",
    )
    _expect(
        attestation["verification_status"],
        "pending_trust_root_and_rfc3161",
        f"{path}.release_attestation.verification_status",
    )

    _artifact(
        python["cpython_source"],
        f"{path}.cpython_source",
        expected_filename="Python-3.12.13.tar.xz",
        expected_url="https://www.python.org/ftp/python/3.12.13/Python-3.12.13.tar.xz",
        expected_size=20801708,
        expected_sha256="c08bc65a81971c1dd5783182826503369466c7e67374d1646519adf05207b684",
    )

    targets = _array(python["targets"], f"{path}.targets")
    if len(targets) != len(TARGET_IDS):
        _fail(f"{path}.targets", "must contain exactly the two supported x64 targets")
    actual_ids: list[str] = []
    all_filenames: set[str] = set()
    for index, target_value in enumerate(targets):
        target_path = f"{path}.targets[{index}]"
        target = _object(
            target_value,
            target_path,
            {
                "target_id",
                "runtime_archive",
                "metadata_archive",
                "payload_root",
                "entrypoint",
                "python_json",
                "license_inventory",
            },
        )
        target_id = _string(target["target_id"], f"{target_path}.target_id")
        actual_ids.append(target_id)
        if target_id not in EXPECTED_PYTHON_TARGETS:
            _fail(f"{target_path}.target_id", "unsupported target")
        expected = EXPECTED_PYTHON_TARGETS[target_id]
        _artifact(
            target["runtime_archive"],
            f"{target_path}.runtime_archive",
            expected_filename=str(expected["runtime_filename"]),
            expected_url=str(expected["runtime_url"]),
            expected_size=int(expected["runtime_size"]),
            expected_sha256=str(expected["runtime_sha256"]),
        )
        _artifact(
            target["metadata_archive"],
            f"{target_path}.metadata_archive",
            expected_filename=str(expected["metadata_filename"]),
            expected_url=str(expected["metadata_url"]),
            expected_size=int(expected["metadata_size"]),
            expected_sha256=str(expected["metadata_sha256"]),
        )
        for key in ("runtime_archive", "metadata_archive"):
            normalized = str(target[key]["filename"]).casefold()
            if normalized in all_filenames:
                _fail(f"{target_path}.{key}.filename", "collides under NFC/casefold")
            all_filenames.add(normalized)
        _expect(target["payload_root"], "python", f"{target_path}.payload_root")
        _portable_path(target["payload_root"], f"{target_path}.payload_root")
        _expect(target["entrypoint"], expected["entrypoint"], f"{target_path}.entrypoint")
        _portable_path(target["entrypoint"], f"{target_path}.entrypoint")

        python_json = _object(
            target["python_json"],
            f"{target_path}.python_json",
            {"path", "size", "sha256"},
        )
        _expect(
            python_json["path"],
            "python/PYTHON.json",
            f"{target_path}.python_json.path",
        )
        _portable_path(python_json["path"], f"{target_path}.python_json.path")
        _expect(
            _integer(
                python_json["size"],
                f"{target_path}.python_json.size",
                minimum=1,
            ),
            expected["python_json_size"],
            f"{target_path}.python_json.size",
        )
        _sha256(python_json["sha256"], f"{target_path}.python_json.sha256")
        _expect(
            python_json["sha256"],
            expected["python_json_sha256"],
            f"{target_path}.python_json.sha256",
        )

        licenses = _object(
            target["license_inventory"],
            f"{target_path}.license_inventory",
            {"format", "entry_count", "size", "sha256"},
        )
        _expect(
            licenses["format"],
            "sha256-tab-size-tab-path-lf",
            f"{target_path}.license_inventory.format",
        )
        _expect(
            _integer(
                licenses["entry_count"],
                f"{target_path}.license_inventory.entry_count",
                minimum=1,
            ),
            19,
            f"{target_path}.license_inventory.entry_count",
        )
        _expect(
            _integer(
                licenses["size"],
                f"{target_path}.license_inventory.size",
                minimum=1,
            ),
            expected["license_inventory_size"],
            f"{target_path}.license_inventory.size",
        )
        _sha256(licenses["sha256"], f"{target_path}.license_inventory.sha256")
        _expect(
            licenses["sha256"],
            expected["license_inventory_sha256"],
            f"{target_path}.license_inventory.sha256",
        )
    _expect(tuple(actual_ids), TARGET_IDS, f"{path}.targets")


def _validate_blockers(
    redistribution: dict[str, Any],
    blockers_value: Any,
) -> tuple[str, ...]:
    path = "$.blockers"
    blockers = _array(blockers_value, path)
    if len(blockers) != len(REQUIRED_BLOCKERS):
        _fail(path, "must contain every required redistribution blocker")
    codes: list[str] = []
    open_codes: list[str] = []
    for index, blocker_value in enumerate(blockers):
        blocker_path = f"{path}[{index}]"
        blocker = _object(
            blocker_value,
            blocker_path,
            {"code", "status", "target_ids", "components", "reason"},
        )
        code = _string(blocker["code"], f"{blocker_path}.code", max_length=128)
        if not BLOCKER_CODE_PATTERN.fullmatch(code):
            _fail(f"{blocker_path}.code", "must be a canonical blocker code")
        codes.append(code)
        status = _string(blocker["status"], f"{blocker_path}.status")
        if status not in {"open", "closed"}:
            _fail(f"{blocker_path}.status", "must be open or closed")
        if status == "open":
            open_codes.append(code)
        target_ids = tuple(
            _string(target_id, f"{blocker_path}.target_ids[{target_index}]")
            for target_index, target_id in enumerate(
                _array(blocker["target_ids"], f"{blocker_path}.target_ids")
            )
        )
        if len(set(target_ids)) != len(target_ids) or any(
            target_id not in TARGET_IDS for target_id in target_ids
        ):
            _fail(f"{blocker_path}.target_ids", "must contain unique supported targets")
        components = _array(blocker["components"], f"{blocker_path}.components")
        if not components:
            _fail(f"{blocker_path}.components", "must not be empty")
        component_names = [
            _string(
                component,
                f"{blocker_path}.components[{component_index}]",
                max_length=128,
            )
            for component_index, component in enumerate(components)
        ]
        if len({component.casefold() for component in component_names}) != len(component_names):
            _fail(f"{blocker_path}.components", "must be unique under casefold")
        reason = _string(blocker["reason"], f"{blocker_path}.reason", max_length=512)
        if code in EXPECTED_BLOCKER_SCOPES:
            _expect(
                target_ids,
                EXPECTED_BLOCKER_SCOPES[code],
                f"{blocker_path}.target_ids",
            )
            expected_components, expected_reason = EXPECTED_BLOCKER_DETAILS[code]
            _expect(
                tuple(component_names),
                expected_components,
                f"{blocker_path}.components",
            )
            _expect(reason, expected_reason, f"{blocker_path}.reason")
    _expect(tuple(codes), REQUIRED_BLOCKERS, path)

    declared_open = tuple(
        _string(code, f"$.redistribution.open_blocker_codes[{index}]")
        for index, code in enumerate(
            _array(
                redistribution["open_blocker_codes"],
                "$.redistribution.open_blocker_codes",
            )
        )
    )
    _expect(declared_open, tuple(open_codes), "$.redistribution.open_blocker_codes")
    return declared_open


def _validate_global_artifact_filenames(root: dict[str, Any]) -> None:
    artifact_paths = [
        "$.codex.release_sha256sums",
        *(
            f"$.codex.targets[{index}].{field}"
            for index in range(len(root["codex"]["targets"]))
            for field in ("archive", "release_archive")
        ),
        "$.python.release_sha256sums",
        "$.python.cpython_source",
        *(
            f"$.python.targets[{index}].{field}"
            for index in range(len(root["python"]["targets"]))
            for field in ("runtime_archive", "metadata_archive")
        ),
    ]
    artifacts = [
        root["codex"]["release_sha256sums"],
        *(
            target[field]
            for target in root["codex"]["targets"]
            for field in ("archive", "release_archive")
        ),
        root["python"]["release_sha256sums"],
        root["python"]["cpython_source"],
        *(
            target[field]
            for target in root["python"]["targets"]
            for field in ("runtime_archive", "metadata_archive")
        ),
    ]
    seen: dict[str, str] = {}
    for path, artifact in zip(artifact_paths, artifacts, strict=True):
        filename = str(artifact["filename"])
        normalized = unicodedata.normalize("NFC", filename).casefold()
        if previous := seen.get(normalized):
            _fail(f"{path}.filename", f"collides under NFC/casefold with {previous}.filename")
        seen[normalized] = path


def validate_document(document: dict[str, Any]) -> VerificationReport:
    """Validate one already-decoded runtime provenance document."""

    if not isinstance(document, dict):
        raise RuntimeSourcesError("root_not_object", "$")
    _reject_forbidden_scalars(document)
    root = _object(
        document,
        "$",
        {
            "schema_id",
            "format",
            "format_version",
            "supported_targets",
            "redistribution",
            "codex",
            "python",
            "blockers",
        },
    )
    _expect(root["schema_id"], SCHEMA_ID, "$.schema_id")
    _expect(root["format"], FORMAT, "$.format")
    _integer(root["format_version"], "$.format_version", minimum=1)
    _expect(root["format_version"], FORMAT_VERSION, "$.format_version")
    supported_targets = tuple(
        _string(target, f"$.supported_targets[{index}]")
        for index, target in enumerate(_array(root["supported_targets"], "$.supported_targets"))
    )
    _expect(supported_targets, TARGET_IDS, "$.supported_targets")

    redistribution = _object(
        root["redistribution"],
        "$.redistribution",
        {"status", "release_ready", "open_blocker_codes"},
    )
    status = _string(redistribution["status"], "$.redistribution.status")
    if status not in {"blocked", "ready"}:
        _fail("$.redistribution.status", "must be blocked or ready")
    release_ready = _boolean(
        redistribution["release_ready"],
        "$.redistribution.release_ready",
    )

    _validate_codex(root["codex"])
    _validate_python(root["python"])
    _validate_global_artifact_filenames(root)
    open_codes = _validate_blockers(redistribution, root["blockers"])

    expected_ready = not open_codes
    expected_status = "ready" if expected_ready else "blocked"
    _expect(release_ready, expected_ready, "$.redistribution.release_ready")
    _expect(status, expected_status, "$.redistribution.status")

    # This pinned revision is deliberately not redistributable.  Closing a
    # blocker requires updating the provenance contract and this policy code.
    _expect(open_codes, REQUIRED_BLOCKERS, "$.redistribution.open_blocker_codes")
    _expect(release_ready, False, "$.redistribution.release_ready")
    _expect(status, "blocked", "$.redistribution.status")
    return VerificationReport(
        release_ready=False,
        release_status="blocked",
        open_blocker_codes=open_codes,
        target_ids=supported_targets,
    )


def verify_source_file(path: Path = DEFAULT_SOURCE) -> VerificationReport:
    """Strictly load and validate the authoritative checked-in source file."""

    return validate_document(load_strict_json(path))


def require_redistributable(document: dict[str, Any]) -> VerificationReport:
    """Require a validated source with no open blockers and a ready assertion."""

    report = validate_document(document)
    if not report.release_ready or report.release_status != "ready" or report.open_blocker_codes:
        raise RuntimeSourcesError("redistribution_blocked", "$.redistribution")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate pinned Studio runtime provenance without network access."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="runtime provenance JSON source",
    )
    parser.add_argument(
        "--require-redistributable",
        action="store_true",
        help="fail unless every redistribution blocker is closed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        document = load_strict_json(args.source)
        report = (
            require_redistributable(document)
            if args.require_redistributable
            else validate_document(document)
        )
    except RuntimeSourcesError as exc:
        print(
            json.dumps(
                {
                    "format": "rpg-world-forge.studio_runtime_sources_verification",
                    "format_version": 1,
                    "valid": False,
                    "error": exc.as_dict(),
                },
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            report.as_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
