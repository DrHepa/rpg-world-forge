"""Transactional materialization and verification for generic standalone games."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Any

from gamepack_runtime.distribution import (
    GAME_LOCK_PATH,
    GAME_MANIFEST_PATH,
    PLATFORM_LOCK_PATH,
    RUNTIME_BUNDLE_ROOT,
    STANDALONE_GAME_FORMAT,
    STANDALONE_GAME_LOCK_FORMAT,
    STANDALONE_PLATFORM_FORMAT,
    StandaloneDistributionError,
    canonical_contract_bytes,
    canonical_contract_hash,
    capture_standalone_tree,
    capture_standalone_tree_with_directories,
    decode_json_object,
    validate_standalone_game_document,
    validate_standalone_game_lock_document,
    validate_standalone_platform_document,
    verify_captured_standalone_distribution,
)
from gamepack_runtime.persistence_io import (
    PersistenceIOError,
    held_persistence_lock,
)
from worldforge._publication_identity import (
    PublicationIdentityCodecError,
    decode_publication_identity,
    encode_publication_identity,
)
from worldforge.asset_io import AssetContractError, open_verified_output_parent
from worldforge.directory_publish import (
    DirectoryIdentity,
    DirectoryPublishError,
    DirectoryPublishIndeterminateError,
    DirectoryPublishRecoveryRequiredError,
    RetainedStageWriter,
    append_append_only_journal,
    create_append_only_journal,
    create_retained_stage,
    fsync_directory,
    publish_directory_noreplace,
    quarantine_and_remove_verified_directory,
    read_append_only_journal_history_state,
    remove_append_only_journal,
    retained_journal_evidence_path,
    retained_recovery_evidence,
)
from worldforge.file_stat import (
    file_identity,
    is_link_or_reparse,
    path_file_stat,
)
from worldforge.game_materialization_bundle import (
    GAME_MATERIALIZATION_BUNDLE_FORMAT,
    GameMaterializationBundleError,
    VerifiedGameMaterializationBundle,
    require_game_materialization_bundle,
)
from worldforge.game_runtime_bundle import (
    GAME_RUNTIME_BUNDLE_MANIFEST,
    GameRuntimeBundleError,
    _verify_game_runtime_bundle_with_stage_capability,
    verify_game_runtime_bundle,
)
from worldforge.generic_runtime import (
    _create_runtime_stage_read_capability,
    _RuntimeStageReadCapability,
)
from worldforge.repository_boundary import FORGE_ROOT, repository_kind
from worldforge.runtime_implementation import (
    RuntimeImplementationError,
    validate_runtime_implementation_document,
)
from worldforge.runtime_platform_lock import (
    RuntimePlatformLockError,
    validate_runtime_platform_lock_document,
)

_MATERIALIZATION_IMPLEMENTATION = "contracts/runtime-implementation.json"
_MATERIALIZATION_LOCK_ROOT = "contracts/platform-locks"
_MATERIALIZATION_LICENSE = "licenses/world-forge-mit.txt"
_STANDALONE_IMPLEMENTATION = "game_data/contracts/runtime-implementation.json"
_STANDALONE_LOCK_ROOT = "game_data/contracts/platform-locks"
_STANDALONE_POLICY = "game_data/contracts/materialization-policy.json"
_MATERIALIZATION_POLICY = "launchers/materialization-policy.json"
_LICENSE_OUTPUT = "LICENSE"
_JOURNAL_FORMAT = "world-forge.standalone_game_publication_journal"
_JOURNAL_VERSION = 1
_JOURNAL_STATES = ("intent", "copying", "ready")
_JOURNAL_FIELDS = frozenset(
    {
        "format",
        "format_version",
        "operation_id",
        "state",
        "stage_name",
        "destination_name",
        "destination_path_hash",
        "parent_identity",
        "stage_identity",
        "standalone_game_hash",
        "payload_lock_hash",
        "payload_tree_hash",
        "materialization_bundle_hash",
        "manifest_sha256",
        "manifest_size_bytes",
        "lock_sha256",
        "lock_size_bytes",
    }
)
_PublicationHook = Callable[[str, Path | None], None]
_AuthorityHook = Callable[[str, Mapping[str, object]], None]


class StandaloneGameError(ValueError):
    """Raised when standalone game materialization cannot prove exact ownership."""

    def __init__(
        self,
        reason_code: str,
        detail: str,
        *,
        recovery_evidence: Mapping[str, object] | None = None,
    ) -> None:
        self.reason_code = reason_code
        self.detail = detail
        self.recovery_evidence = copy.deepcopy(dict(recovery_evidence or {}))
        super().__init__(f"{reason_code}: {detail}")


def _fail(
    reason_code: str,
    detail: str,
    *,
    recovery_evidence: Mapping[str, object] | None = None,
) -> None:
    raise StandaloneGameError(
        reason_code,
        detail,
        recovery_evidence=recovery_evidence,
    )


def _identity(
    document: Mapping[str, object],
    *,
    format_name: str,
    id_field: str,
) -> dict[str, object]:
    return {
        "format": format_name,
        "format_version": 1,
        "id": document[id_field],
        "content_hash": document["content_hash"],
    }


def _payload_lock_identity(lock: Mapping[str, object]) -> dict[str, object]:
    return {
        **_identity(
            lock,
            format_name=STANDALONE_GAME_LOCK_FORMAT,
            id_field="lock_id",
        ),
        "tree_hash": lock["tree_hash"],
    }


def _file_inventory(files: Mapping[str, bytes]) -> list[dict[str, object]]:
    return [
        {
            "path": path,
            "sha256": hashlib.sha256(files[path]).hexdigest(),
            "size_bytes": len(files[path]),
        }
        for path in sorted(files, key=lambda item: item.encode("utf-8"))
    ]


def _build_lock(files: Mapping[str, bytes]) -> dict[str, Any]:
    records = _file_inventory(files)
    tree_hash = canonical_contract_hash({"files": records})
    document: dict[str, Any] = {
        "format": STANDALONE_GAME_LOCK_FORMAT,
        "format_version": 1,
        "lock_id": "standalone_game_lock_" + tree_hash[:40],
        "files": records,
        "tree_hash": tree_hash,
        "content_hash": "",
    }
    document["content_hash"] = canonical_contract_hash(document)
    return validate_standalone_game_lock_document(document)


def _runtime_manifest(source: VerifiedGameMaterializationBundle) -> dict[str, Any]:
    relative = f"runtime-bundle/{GAME_RUNTIME_BUNDLE_MANIFEST}"
    try:
        return decode_json_object(source.read_bytes(relative), relative)
    except StandaloneDistributionError as exc:
        _fail("standalone_game_runtime_bundle_invalid", str(exc))


def _implementation(source: VerifiedGameMaterializationBundle) -> dict[str, Any]:
    try:
        value = decode_json_object(
            source.read_bytes(_MATERIALIZATION_IMPLEMENTATION),
            _MATERIALIZATION_IMPLEMENTATION,
        )
        return validate_runtime_implementation_document(value)
    except (
        GameMaterializationBundleError,
        RuntimeImplementationError,
        StandaloneDistributionError,
    ) as exc:
        _fail("standalone_game_runtime_implementation_invalid", str(exc))


def _platform_locks(
    source: VerifiedGameMaterializationBundle,
) -> list[dict[str, Any]]:
    locks: list[dict[str, Any]] = []
    for identity in source.manifest["platform_locks"]["locks"]:
        path = identity["path"]
        try:
            value = decode_json_object(source.read_bytes(path), path)
            locks.append(validate_runtime_platform_lock_document(value))
        except (
            GameMaterializationBundleError,
            RuntimePlatformLockError,
            StandaloneDistributionError,
        ) as exc:
            _fail("standalone_game_platform_lock_invalid", str(exc))
    return locks


def _build_platform(
    *,
    runtime_manifest: Mapping[str, Any],
    implementation: Mapping[str, Any],
    locks: list[Mapping[str, Any]],
) -> dict[str, Any]:
    adapter = implementation["adapter"]
    snapshot = implementation["snapshot"]
    lock_records = sorted(
        [
            {
                "lock_id": lock["lock_id"],
                "content_hash": lock["content_hash"],
                "os": lock["platform"]["os"],
                "python_minor": lock["python"]["minor"],
                "abi": lock["python"]["abi"],
            }
            for lock in locks
        ],
        key=lambda item: item["lock_id"].encode("utf-8"),
    )
    seed: dict[str, Any] = {
        "requires_python": ">=3.11,<3.13",
        "dependency": {
            "distribution": "raylib",
            "version": "6.0.1.0",
            "pin": "raylib==6.0.1.0",
            "import_module": "pyray",
            "native_api": "raylib-5.5",
        },
        "adapter": copy.deepcopy(adapter),
        "runtime_implementation": {
            "implementation_id": implementation["implementation_id"],
            "content_hash": implementation["content_hash"],
        },
        "runtime_snapshot": copy.deepcopy(snapshot),
        "platform_locks": lock_records,
    }
    document: dict[str, Any] = {
        "format": STANDALONE_PLATFORM_FORMAT,
        "format_version": 1,
        "platform_set_id": "standalone_platform_" + canonical_contract_hash(seed)[:40],
        **seed,
        "content_hash": "",
    }
    document["content_hash"] = canonical_contract_hash(document)
    checked = validate_standalone_platform_document(document)
    contracts = runtime_manifest["contracts"]
    runtime_adapter = contracts["runtime_adapter"]
    runtime_snapshot = contracts["runtime_snapshot"]
    if checked["adapter"] != {
        "adapter_id": runtime_adapter["id"],
        "adapter_version": runtime_adapter["adapter_version"],
        "content_hash": runtime_adapter["content_hash"],
    } or checked["runtime_snapshot"] != {
        "snapshot_id": runtime_snapshot["id"],
        "content_hash": runtime_snapshot["content_hash"],
        "tree_hash": runtime_manifest["runtime_snapshot_tree"]["tree_hash"],
    }:
        _fail(
            "standalone_game_lineage_mismatch",
            "standalone platform differs from the nested runtime bundle",
        )
    return checked


def _launcher_output_files(
    source: VerifiedGameMaterializationBundle,
) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for item in source.manifest["launchers"]["inventory"]:
        if item["role"] == "materialization_policy":
            result[_STANDALONE_POLICY] = source.read_bytes(item["path"])
            continue
        output = item["output_path"]
        if output in result:
            _fail("standalone_game_path_collision", f"duplicate launcher output {output}")
        result[output] = source.read_bytes(item["path"])
    return result


def _build_payload(
    source: VerifiedGameMaterializationBundle,
) -> tuple[dict[str, bytes], dict[str, Any], dict[str, Any], dict[str, Any]]:
    source_manifest = source.manifest
    runtime_manifest = _runtime_manifest(source)
    implementation = _implementation(source)
    locks = _platform_locks(source)
    platform = _build_platform(
        runtime_manifest=runtime_manifest,
        implementation=implementation,
        locks=locks,
    )
    files = _launcher_output_files(source)
    files[_LICENSE_OUTPUT] = source.read_bytes(_MATERIALIZATION_LICENSE)
    for relative, payload in source.files.items():
        if relative.startswith("runtime-bundle/"):
            nested = relative.removeprefix("runtime-bundle/")
            files[f"{RUNTIME_BUNDLE_ROOT}/{nested}"] = payload
    files[_STANDALONE_IMPLEMENTATION] = source.read_bytes(_MATERIALIZATION_IMPLEMENTATION)
    for identity in source_manifest["platform_locks"]["locks"]:
        files[f"{_STANDALONE_LOCK_ROOT}/{identity['id']}.json"] = source.read_bytes(
            identity["path"]
        )
    files[PLATFORM_LOCK_PATH] = canonical_contract_bytes(platform)
    lock = _build_lock(files)
    game_id = runtime_manifest["contracts"]["gamepack"]["id"]
    document: dict[str, Any] = {
        "format": STANDALONE_GAME_FORMAT,
        "format_version": 1,
        "game_id": game_id,
        "state": "materialized",
        "lineage": {
            "gamepack_hash": source_manifest["lineage"]["gamepack_hash"],
            "assetpack_hash": source_manifest["lineage"]["assetpack_hash"],
            "runtime_snapshot_hash": source_manifest["lineage"]["runtime_snapshot_hash"],
            "runtime_composition_hash": source_manifest["lineage"]["composition_hash"],
            "runtime_bundle_hash": source_manifest["lineage"]["runtime_bundle_hash"],
        },
        "materialization_bundle": {
            "format": GAME_MATERIALIZATION_BUNDLE_FORMAT,
            "format_version": 1,
            "id": source_manifest["materialization_bundle_id"],
            "content_hash": source_manifest["content_hash"],
        },
        "runtime_implementation": _identity(
            implementation,
            format_name="world-forge.runtime_implementation",
            id_field="implementation_id",
        ),
        "platform_set": _identity(
            platform,
            format_name=STANDALONE_PLATFORM_FORMAT,
            id_field="platform_set_id",
        ),
        "payload_lock": _payload_lock_identity(lock),
        "entry_points": {
            "game": "run_game.py",
            "verifier": "scripts/verify_game.py",
            "offline_smoke": "scripts/offline_smoke.py",
            "native_smoke": "scripts/native_smoke.py",
        },
        "content_hash": "",
    }
    document["content_hash"] = canonical_contract_hash(document)
    manifest = validate_standalone_game_document(document)
    return files, manifest, lock, platform


def build_standalone_game_documents(
    source: VerifiedGameMaterializationBundle,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build deterministic standalone contracts from one retained source snapshot.

    This object-only entry point is intentionally publication-free.  It lets a
    bounded worker derive the exact candidate contracts without receiving a
    native target path or fabricating an authoring checkout.
    """

    if not source.manifest["materialization_ready"]:
        _fail(
            "materialization_bundle_not_ready",
            "materialization bundle lacks the complete exact standalone launcher inventory",
        )
    _files, manifest, lock, platform = _build_payload(source)
    return (
        copy.deepcopy(manifest),
        copy.deepcopy(lock),
        copy.deepcopy(platform),
    )


class VerifiedStandaloneGame:
    """Retained immutable standalone game documents and captured byte inventory."""

    __slots__ = (
        "_closed",
        "_files",
        "_lock",
        "_manifest",
        "_platform",
        "root",
        "root_identity",
    )

    def __init__(
        self,
        root: Path,
        manifest: Mapping[str, Any],
        lock: Mapping[str, Any],
        platform: Mapping[str, Any],
        files: Mapping[str, bytes],
        root_identity: DirectoryIdentity,
    ) -> None:
        self.root = root
        self.root_identity = root_identity
        self._manifest = copy.deepcopy(manifest)
        self._lock = copy.deepcopy(lock)
        self._platform = copy.deepcopy(platform)
        self._files = dict(files)
        self._closed = False

    def _require_open(self) -> None:
        if self._closed:
            _fail("standalone_game_snapshot_closed", "verified game snapshot is closed")

    @property
    def manifest(self) -> dict[str, Any]:
        self._require_open()
        return copy.deepcopy(self._manifest)

    @property
    def lock(self) -> dict[str, Any]:
        self._require_open()
        return copy.deepcopy(self._lock)

    @property
    def platform(self) -> dict[str, Any]:
        self._require_open()
        return copy.deepcopy(self._platform)

    @property
    def files(self) -> Mapping[str, bytes]:
        self._require_open()
        return MappingProxyType(dict(self._files))

    @property
    def evidence(self) -> Mapping[str, object]:
        self._require_open()
        return MappingProxyType(
            {
                "status": "materialized",
                "game_id": self._manifest["game_id"],
                "content_hash": self._manifest["content_hash"],
                "payload_lock_hash": self._lock["content_hash"],
                "runtime_bundle_hash": self._manifest["lineage"]["runtime_bundle_hash"],
                "release": "blocked",
                "native": "untested",
            }
        )

    def close(self) -> None:
        self._files.clear()
        self._closed = True

    def __enter__(self) -> VerifiedStandaloneGame:
        self._require_open()
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: BaseException | None,
        _traceback: object,
    ) -> None:
        self.close()


def _paths_overlap(left: Path, right: Path) -> bool:
    left_name = os.path.normcase(os.path.realpath(os.fspath(left)))
    right_name = os.path.normcase(os.path.realpath(os.fspath(right)))
    try:
        common = os.path.commonpath((left_name, right_name))
    except ValueError:
        return False
    return common in {left_name, right_name}


def require_standalone_materialization_source(
    root: str | Path,
) -> VerifiedGameMaterializationBundle:
    try:
        verified = require_game_materialization_bundle(root)
    except GameMaterializationBundleError as exc:
        _fail(exc.reason_code, exc.detail)
    if not verified.manifest["materialization_ready"]:
        verified.close()
        _fail(
            "materialization_bundle_not_ready",
            "materialization bundle lacks the complete exact standalone launcher inventory",
        )
    return verified


def _standalone_runtime_stage_read_capability(
    writer: object,
    *,
    expected_stage: Path,
    capability_root: Path,
) -> _RuntimeStageReadCapability:
    def require_stage_binding() -> None:
        try:
            RetainedStageWriter._require_active_binding(  # noqa: SLF001
                writer,
                expected_stage=expected_stage,
            )
        except DirectoryPublishError as exc:
            _fail("standalone_game_stage_capability_invalid", str(exc))

    require_stage_binding()
    return _create_runtime_stage_read_capability(
        root=capability_root,
        require_binding=require_stage_binding,
    )


def _verify_nested_runtime_bundle_from_retained_standalone_stage(
    nested_root: str | Path,
    *,
    expected_content_hash: str,
    expected_outer_stage: str | Path,
    _retained_stage_writer: RetainedStageWriter,
) -> object:
    outer_stage = Path(os.path.abspath(os.fspath(expected_outer_stage)))
    nested_root_path = Path(os.path.abspath(os.fspath(nested_root)))
    if nested_root_path != outer_stage / RUNTIME_BUNDLE_ROOT:
        _fail(
            "standalone_game_stage_capability_invalid",
            "nested runtime bundle root does not bind the standalone stage",
        )
    stage_capability = _standalone_runtime_stage_read_capability(
        _retained_stage_writer,
        expected_stage=outer_stage,
        capability_root=nested_root_path,
    )
    try:
        nested = _verify_game_runtime_bundle_with_stage_capability(
            nested_root_path,
            expected_content_hash=expected_content_hash,
            _stage_capability=stage_capability,
        )
    finally:
        stage_capability.require_binding()
    stage_capability.require_binding()
    return nested


def verify_standalone_game(
    root: str | Path,
    *,
    expected_content_hash: str | None = None,
    expected_root_identity: DirectoryIdentity | None = None,
    _retained_stage_writer: RetainedStageWriter | None = None,
) -> VerifiedStandaloneGame:
    root_path = Path(os.path.abspath(os.fspath(root)))
    try:
        before = path_file_stat(root_path)
        if is_link_or_reparse(before) or not stat.S_ISDIR(before.st_mode):
            _fail(
                "standalone_game_root_invalid",
                "standalone root must be one real directory",
            )
        root_identity = file_identity(before)
        if expected_root_identity is not None and root_identity != expected_root_identity:
            _fail(
                "standalone_game_root_identity_mismatch",
                "standalone root differs from the expected retained identity",
            )
        files = dict(capture_standalone_tree(root_path))
        after = path_file_stat(root_path)
        if (
            is_link_or_reparse(after)
            or not stat.S_ISDIR(after.st_mode)
            or file_identity(after) != root_identity
        ):
            _fail(
                "standalone_game_tree_unsafe",
                "standalone root changed around its captured snapshot",
            )
        report = verify_captured_standalone_distribution(files, root=root_path)
    except StandaloneDistributionError as exc:
        _fail(exc.reason_code, exc.detail)
    except OSError as exc:
        _fail("standalone_game_root_invalid", str(exc))
    try:
        manifest = validate_standalone_game_document(
            decode_json_object(files[GAME_MANIFEST_PATH], GAME_MANIFEST_PATH)
        )
        lock = validate_standalone_game_lock_document(
            decode_json_object(files[GAME_LOCK_PATH], GAME_LOCK_PATH)
        )
        platform = validate_standalone_platform_document(
            decode_json_object(files[PLATFORM_LOCK_PATH], PLATFORM_LOCK_PATH)
        )
    except (KeyError, StandaloneDistributionError) as exc:
        _fail("standalone_game_verification_failed", str(exc))
    if expected_content_hash is not None and manifest["content_hash"] != expected_content_hash:
        _fail(
            "standalone_game_expected_hash_mismatch",
            "standalone game manifest does not match the expected hash",
        )
    try:
        runtime_root = root_path / RUNTIME_BUNDLE_ROOT
        if _retained_stage_writer is not None:
            nested = _verify_nested_runtime_bundle_from_retained_standalone_stage(
                runtime_root,
                expected_content_hash=manifest["lineage"]["runtime_bundle_hash"],
                expected_outer_stage=root_path,
                _retained_stage_writer=_retained_stage_writer,
            )
        else:
            nested = verify_game_runtime_bundle(
                runtime_root,
                expected_content_hash=manifest["lineage"]["runtime_bundle_hash"],
            )
    except GameRuntimeBundleError as exc:
        _fail("standalone_game_runtime_bundle_invalid", str(exc))
    try:
        runtime_manifest = nested.manifest
        if (
            runtime_manifest["contracts"]["gamepack"]["content_hash"]
            != manifest["lineage"]["gamepack_hash"]
            or runtime_manifest["assetpack"]["manifest"]["content_hash"]
            != manifest["lineage"]["assetpack_hash"]
            or runtime_manifest["contracts"]["runtime_snapshot"]["content_hash"]
            != manifest["lineage"]["runtime_snapshot_hash"]
            or runtime_manifest["contracts"]["runtime_composition"]["content_hash"]
            != manifest["lineage"]["runtime_composition_hash"]
        ):
            _fail(
                "standalone_game_lineage_mismatch",
                "standalone lineage differs from its exact runtime bundle",
            )
    finally:
        nested.close()
    if report["manifest_hash"] != manifest["content_hash"]:
        _fail("standalone_game_verification_failed", "neutral verifier report differs")
    return VerifiedStandaloneGame(
        root_path,
        manifest,
        lock,
        platform,
        files,
        root_identity,
    )


def _run_independent_verifier(root: Path) -> dict[str, object]:
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONHOME", "PYTHONPATH"}
    }
    result = subprocess.run(
        [sys.executable, "-I", str(root / "scripts/verify_game.py")],
        cwd=root,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        _fail(
            "standalone_game_independent_verification_failed",
            result.stderr.strip() or "generated verifier exited unsuccessfully",
        )
    try:
        value = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        _fail("standalone_game_independent_verification_failed", str(exc))
    if type(value) is not dict or value.get("status") != "verified":
        _fail(
            "standalone_game_independent_verification_failed",
            "generated verifier returned an invalid report",
        )
    return value


def _journal_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.standalone-game.journal.json"


def _lock_path(destination: Path) -> Path:
    return destination.parent / f".{destination.name}.standalone-game.lock"


@contextmanager
def _destination_lock(
    destination: Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
) -> Iterator[None]:
    body_error: BaseException | None = None
    try:
        with open_verified_output_parent(destination.parent) as parent:
            if (
                expected_parent_identity is not None
                and parent.identities[-1] != expected_parent_identity
            ):
                _fail(
                    "standalone_game_directory_invalid",
                    "destination parent differs from the expected retained identity",
                )
            with held_persistence_lock(_lock_path(destination)):
                try:
                    yield
                except BaseException as exc:
                    body_error = exc
    except AssetContractError as exc:
        if body_error is not None:
            body_error.add_note(f"standalone retained parent cleanup failed: {exc}")
            raise body_error from None
        _fail("standalone_game_lock_failed", str(exc))
    except PersistenceIOError as exc:
        if body_error is not None:
            exc.add_note(f"standalone operation also failed: {body_error}")
        reason = (
            "standalone_game_publication_busy"
            if "lock" in str(exc).casefold()
            else "standalone_game_lock_failed"
        )
        _fail(reason, str(exc))
    if body_error is not None:
        raise body_error


def _identity_document(identity: DirectoryIdentity) -> dict[str, int | str]:
    try:
        return encode_publication_identity(identity, windows=os.name == "nt")
    except PublicationIdentityCodecError:
        _fail(
            "standalone_game_journal_invalid",
            "journal identity is invalid",
        )


def _identity_from_document(
    value: object,
    *,
    context: str = "stage",
) -> DirectoryIdentity:
    try:
        return decode_publication_identity(
            value,
            context=f"{context} identity",
        )
    except PublicationIdentityCodecError:
        _fail(
            "standalone_game_journal_invalid",
            f"{context} identity is invalid",
        )


def _destination_path_hash(destination: Path) -> str:
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(destination)))
    return hashlib.sha256(os.fsencode(normalized)).hexdigest()


def _journal_document(
    *,
    operation_id: str,
    state: str,
    stage: Path,
    destination: Path,
    parent_identity: DirectoryIdentity,
    stage_identity: DirectoryIdentity | None,
    manifest: Mapping[str, Any],
    lock: Mapping[str, Any],
    materialization_hash: str,
) -> dict[str, object]:
    manifest_payload = canonical_contract_bytes(manifest)
    lock_payload = canonical_contract_bytes(lock)
    return {
        "format": _JOURNAL_FORMAT,
        "format_version": _JOURNAL_VERSION,
        "operation_id": operation_id,
        "state": state,
        "stage_name": stage.name,
        "destination_name": destination.name,
        "destination_path_hash": _destination_path_hash(destination),
        "parent_identity": _identity_document(parent_identity),
        "stage_identity": (None if stage_identity is None else _identity_document(stage_identity)),
        "standalone_game_hash": manifest["content_hash"],
        "payload_lock_hash": lock["content_hash"],
        "payload_tree_hash": lock["tree_hash"],
        "materialization_bundle_hash": materialization_hash,
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "manifest_size_bytes": len(manifest_payload),
        "lock_sha256": hashlib.sha256(lock_payload).hexdigest(),
        "lock_size_bytes": len(lock_payload),
    }


def _validate_journal(value: object, destination: Path) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _JOURNAL_FIELDS:
        _fail("standalone_game_journal_invalid", "journal fields are not closed")
    document = copy.deepcopy(value)
    if (
        document.get("format") != _JOURNAL_FORMAT
        or document.get("format_version") != _JOURNAL_VERSION
        or document.get("destination_name") != destination.name
        or document.get("destination_path_hash") != _destination_path_hash(destination)
    ):
        _fail("standalone_game_journal_invalid", "journal identity is invalid")
    parent_identity = _identity_from_document(
        document.get("parent_identity"),
        context="parent",
    )
    if _optional_directory_identity(destination.parent) != parent_identity:
        _fail(
            "standalone_game_journal_invalid",
            "journal parent identity differs from its destination",
        )
    operation_id = document.get("operation_id")
    if type(operation_id) is not str or re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        _fail("standalone_game_journal_invalid", "journal operation ID is invalid")
    state_value = document.get("state")
    if state_value not in _JOURNAL_STATES:
        _fail("standalone_game_journal_invalid", "journal state is invalid")
    stage_name = document.get("stage_name")
    if (
        type(stage_name) is not str
        or stage_name != f".{destination.name}.standalone-stage-{operation_id}"
    ):
        _fail("standalone_game_journal_invalid", "journal stage name is invalid")
    if state_value == "intent":
        if document.get("stage_identity") is not None:
            _fail("standalone_game_journal_invalid", "intent cannot bind a stage")
    else:
        _identity_from_document(document.get("stage_identity"))
    for field in (
        "standalone_game_hash",
        "payload_lock_hash",
        "payload_tree_hash",
        "materialization_bundle_hash",
        "manifest_sha256",
        "lock_sha256",
    ):
        value_hash = document.get(field)
        if type(value_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", value_hash) is None:
            _fail("standalone_game_journal_invalid", f"journal {field} is invalid")
    for field in ("manifest_size_bytes", "lock_size_bytes"):
        size = document.get(field)
        if type(size) is not int or not 1 <= size <= 4 * 1024 * 1024:
            _fail("standalone_game_journal_invalid", f"journal {field} is invalid")
    return document


def _expected_journal_history(
    terminal: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    intent = {**terminal, "state": "intent", "stage_identity": None}
    if terminal["state"] == "intent":
        return (intent,)
    copying = {**terminal, "state": "copying"}
    if terminal["state"] == "copying":
        return intent, copying
    return intent, copying, {**terminal, "state": "ready"}


def _history_payloads(document: Mapping[str, Any]) -> tuple[bytes, ...]:
    return tuple(canonical_contract_bytes(item) for item in _expected_journal_history(document))


def _journal_payload_sha256(
    document: Mapping[str, Any],
    *,
    state: str,
) -> str:
    if state not in _JOURNAL_STATES:
        _fail("standalone_game_journal_invalid", "journal authority state is invalid")
    candidate = {**document, "state": state}
    if state == "intent":
        candidate["stage_identity"] = None
    elif candidate.get("stage_identity") is None:
        _fail("standalone_game_journal_invalid", "journal stage authority is unavailable")
    return hashlib.sha256(canonical_contract_bytes(candidate)).hexdigest()


def _require_journal_payload_authority(
    journal: Mapping[str, Any],
    *,
    expected_sha256: str | None,
    expected_state: str | None,
    stage_allocated: bool,
    reason_code: str,
) -> None:
    if (expected_sha256 is None) != (expected_state is None):
        _fail(reason_code, "standalone journal content authority is incomplete")
    if expected_sha256 is None or expected_state is None:
        return
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        _fail(reason_code, "standalone journal content authority is invalid")
    actual_state = str(journal["state"])
    allowed = actual_state == expected_state or (
        expected_state == "copying" and actual_state == "ready"
    )
    if stage_allocated and expected_state == "intent" and actual_state == "copying":
        allowed = True
    if not allowed or _journal_payload_sha256(journal, state=expected_state) != expected_sha256:
        _fail(reason_code, "standalone journal content authority changed")


def _read_journal(
    destination: Path,
) -> tuple[dict[str, Any], DirectoryIdentity, bytes, bool] | None:
    path = _journal_path(destination)
    try:
        loaded = read_append_only_journal_history_state(
            path,
            max_record_bytes=4 * 1024 * 1024,
            max_file_bytes=16 * 1024 * 1024,
        )
    except DirectoryPublishError as exc:
        _fail("standalone_game_journal_invalid", str(exc))
    if loaded is None:
        return None
    payloads, identity, partial_tail = loaded
    documents: list[dict[str, Any]] = []
    for payload in payloads:
        try:
            document = _validate_journal(
                decode_json_object(payload, str(path)),
                destination,
            )
        except StandaloneDistributionError as exc:
            _fail("standalone_game_journal_invalid", str(exc))
        if canonical_contract_bytes(document) != payload:
            _fail("standalone_game_journal_invalid", "journal is not canonical")
        documents.append(document)
    if not documents or tuple(documents) != _expected_journal_history(documents[-1]):
        _fail("standalone_game_journal_invalid", "journal history is not an exact prefix")
    return documents[-1], identity, payloads[-1], partial_tail


def _write_journal(
    destination: Path,
    document: dict[str, Any],
    *,
    create: bool,
    expected_document: Mapping[str, Any] | None = None,
    expected_identity: DirectoryIdentity | None = None,
) -> DirectoryIdentity:
    path = _journal_path(destination)
    payload = canonical_contract_bytes(document)
    try:
        if create:
            try:
                identity = create_append_only_journal(
                    path,
                    payload,
                    max_record_bytes=4 * 1024 * 1024,
                )
            except FileExistsError:
                _fail(
                    "standalone_game_recovery_required",
                    "an incomplete standalone publication journal exists",
                    recovery_evidence=retained_recovery_evidence(journal_path=path),
                )
            fsync_directory(path.parent, context="standalone game journal parent")
            return identity
        if expected_document is None or expected_identity is None:
            _fail("standalone_game_journal_invalid", "journal transition is unbound")
        loaded = _read_journal(destination)
        expected_payload = canonical_contract_bytes(expected_document)
        if (
            loaded is None
            or loaded[0] != expected_document
            or loaded[1] != expected_identity
            or loaded[2] != expected_payload
        ):
            _fail("standalone_game_journal_changed", "journal changed before append")
        return append_append_only_journal(
            path,
            expected_identity=expected_identity,
            expected_payload=expected_payload,
            expected_history=_history_payloads(expected_document),
            updated_payload=payload,
            max_record_bytes=4 * 1024 * 1024,
            max_file_bytes=16 * 1024 * 1024,
            repair_partial_tail=True,
        )
    except StandaloneGameError:
        raise
    except DirectoryPublishError as exc:
        _fail("standalone_game_journal_failed", str(exc))


def _remove_journal(
    destination: Path,
    document: Mapping[str, Any],
    identity: DirectoryIdentity,
) -> None:
    journal_path = _journal_path(destination)
    try:
        retained_journal = remove_append_only_journal(
            journal_path,
            expected_identity=identity,
            expected_payload=canonical_contract_bytes(document),
            expected_history=_history_payloads(document),
            max_record_bytes=4 * 1024 * 1024,
            max_file_bytes=16 * 1024 * 1024,
        )
        if sys.platform.startswith("linux") and os.name == "posix":
            if retained_journal != retained_journal_evidence_path(journal_path, identity):
                _fail(
                    "standalone_game_journal_indeterminate",
                    "terminal journal evidence locator changed",
                )
        elif retained_journal is not None:
            _fail(
                "standalone_game_journal_indeterminate",
                "unexpected terminal journal evidence was returned",
            )
    except DirectoryPublishIndeterminateError as exc:
        _fail("standalone_game_journal_indeterminate", str(exc))
    except DirectoryPublishError as exc:
        _fail("standalone_game_journal_failed", str(exc))


def _optional_directory_identity(path: Path) -> DirectoryIdentity | None:
    try:
        info = path_file_stat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("standalone_game_directory_invalid", str(exc))
    if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        _fail("standalone_game_directory_invalid", f"{path} is unsafe")
    return file_identity(info)


def _require_reset_stage_absent(
    destination: Path,
    operation_id: str,
    *,
    reason_code: str,
) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", operation_id) is None:
        _fail(reason_code, "standalone reset operation authority is invalid")
    stage = destination.parent / (f".{destination.name}.standalone-stage-{operation_id}")
    try:
        identity = _optional_directory_identity(stage)
    except StandaloneGameError:
        _fail(reason_code, "standalone reset stage reappeared unsafely")
    if identity is not None:
        _fail(reason_code, "standalone reset stage reappeared")


def _require_external_destination(destination: Path) -> DirectoryIdentity:
    parent_identity = _optional_directory_identity(destination.parent)
    if parent_identity is None:
        _fail(
            "standalone_game_parent_missing",
            "destination parent must already exist",
        )
    if destination == FORGE_ROOT or FORGE_ROOT in destination.parents:
        _fail(
            "standalone_game_destination_invalid",
            "destination must be one new external directory",
        )
    for ancestor in (destination.parent, *destination.parent.parents):
        kind = repository_kind(ancestor)
        if kind is not None:
            _fail(
                "standalone_game_destination_invalid",
                f"destination cannot be nested inside a {kind} repository",
            )
    return parent_identity


def _journal_matches_game(
    journal: Mapping[str, Any],
    verified: VerifiedStandaloneGame,
    destination: Path,
) -> None:
    manifest = verified.manifest
    lock = verified.lock
    expected = _journal_document(
        operation_id=journal["operation_id"],
        state=journal["state"],
        stage=destination.parent / journal["stage_name"],
        destination=destination,
        parent_identity=_identity_from_document(
            journal["parent_identity"],
            context="parent",
        ),
        stage_identity=(
            None
            if journal["stage_identity"] is None
            else _identity_from_document(journal["stage_identity"])
        ),
        manifest=manifest,
        lock=lock,
        materialization_hash=journal["materialization_bundle_hash"],
    )
    if expected != journal:
        _fail(
            "standalone_game_recovery_mismatch",
            "journal differs from the exact standalone game",
        )


def _recover_locked(
    destination: Path,
    *,
    expected_root_identity: DirectoryIdentity | None = None,
    expected_journal_identity: DirectoryIdentity | None = None,
    expected_operation_id: str | None = None,
    expected_content_hash: str | None = None,
    expected_tree_hash: str | None = None,
    expected_stage_identity: DirectoryIdentity | None = None,
    expected_journal_payload_sha256: str | None = None,
    expected_journal_payload_state: str | None = None,
    allow_missing_expected_journal: bool = False,
    require_journal_for_visible: bool = False,
    require_intent_journal: bool = False,
    stage_allocated: bool = False,
    reset_pending: bool = False,
    reject_unbound_journal: bool = False,
    authority_hook: _AuthorityHook | None = None,
) -> VerifiedStandaloneGame | None:
    _require_external_destination(destination)
    loaded = _read_journal(destination)
    if loaded is None:
        if reset_pending:
            if (
                not allow_missing_expected_journal
                or expected_journal_identity is None
                or expected_operation_id is None
                or _optional_directory_identity(destination) is not None
            ):
                _fail(
                    "standalone_game_recovery_ambiguous",
                    "standalone reset authority is incomplete",
                )
            _require_reset_stage_absent(
                destination,
                expected_operation_id,
                reason_code="standalone_game_recovery_ambiguous",
            )
            if authority_hook is not None:
                authority_hook(
                    "publication_reset",
                    {
                        "journal_identity": list(expected_journal_identity),
                        "operation_id": expected_operation_id,
                    },
                )
            return None
        if (
            expected_journal_identity is not None or expected_operation_id is not None
        ) and not allow_missing_expected_journal:
            _fail(
                "standalone_game_recovery_ambiguous",
                "retained standalone recovery journal disappeared",
            )
        if _optional_directory_identity(destination) is None:
            return None
        if require_journal_for_visible:
            _fail(
                "standalone_game_recovery_ambiguous",
                "visible standalone game has no retained recovery journal",
            )
        return verify_standalone_game(
            destination,
            expected_root_identity=expected_root_identity,
        )
    journal, journal_identity, _payload, partial_tail = loaded
    if reject_unbound_journal and (
        expected_journal_identity is None or expected_operation_id is None
    ):
        _fail(
            "standalone_game_recovery_ambiguous",
            "standalone recovery journal is not bound to trusted authority",
        )
    if expected_journal_identity is not None and journal_identity != expected_journal_identity:
        _fail(
            "standalone_game_recovery_ambiguous",
            "standalone recovery journal identity changed",
        )
    if expected_operation_id is not None and journal["operation_id"] != expected_operation_id:
        _fail(
            "standalone_game_recovery_ambiguous",
            "standalone recovery operation identity changed",
        )
    _require_journal_payload_authority(
        journal,
        expected_sha256=expected_journal_payload_sha256,
        expected_state=expected_journal_payload_state,
        stage_allocated=stage_allocated,
        reason_code="standalone_game_recovery_ambiguous",
    )
    if expected_content_hash is not None and (
        journal["standalone_game_hash"] != expected_content_hash
    ):
        _fail(
            "standalone_game_recovery_ambiguous",
            "standalone recovery content authority changed",
        )
    if expected_tree_hash is not None and journal["payload_tree_hash"] != expected_tree_hash:
        _fail(
            "standalone_game_recovery_ambiguous",
            "standalone recovery tree authority changed",
        )
    if require_intent_journal and journal["state"] != "intent":
        _fail(
            "standalone_game_recovery_ambiguous",
            "standalone recovery advanced before its stage authority was retained",
        )
    if (
        journal["state"] != "intent"
        and expected_stage_identity is not None
        and (_identity_from_document(journal["stage_identity"]) != expected_stage_identity)
    ):
        _fail(
            "standalone_game_recovery_ambiguous",
            "standalone recovery stage authority changed",
        )
    if reset_pending and journal["state"] != "intent":
        stage = destination.parent / journal["stage_name"]
        _fail(
            "standalone_game_recovery_required",
            "a stage-bound rollback must be completed explicitly before resume",
            recovery_evidence=retained_recovery_evidence(
                stage_path=stage,
                stage_identity=expected_stage_identity,
                journal_path=_journal_path(destination),
                journal_identity=journal_identity,
            ),
        )
    if partial_tail and journal["state"] != "copying":
        _fail(
            "standalone_game_journal_invalid",
            "journal has a torn non-recoverable transition",
        )
    stage = destination.parent / journal["stage_name"]
    if stage_allocated:
        if (
            expected_stage_identity is None
            or _optional_directory_identity(stage) != expected_stage_identity
            or _optional_directory_identity(destination) is not None
            or journal["state"] not in {"intent", "copying"}
        ):
            _fail(
                "standalone_game_recovery_ambiguous",
                "allocated standalone stage authority changed",
            )
        if journal["state"] == "intent":
            copying = {
                **journal,
                "state": "copying",
                "stage_identity": _identity_document(expected_stage_identity),
            }
            journal_identity = _write_journal(
                destination,
                copying,
                create=False,
                expected_document=journal,
                expected_identity=journal_identity,
            )
            journal = copying
        if authority_hook is not None:
            authority_hook(
                "publication_staged",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                    "stage_identity": list(expected_stage_identity),
                    "journal_payload_sha256": _journal_payload_sha256(
                        journal,
                        state="copying",
                    ),
                    "journal_payload_state": "copying",
                },
            )
    if journal["state"] == "intent":
        if (
            _optional_directory_identity(stage) is not None
            or _optional_directory_identity(destination) is not None
        ):
            _fail("standalone_game_recovery_ambiguous", "intent has an unbound tree")
        if authority_hook is not None:
            authority_hook(
                "publication_resetting",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                },
            )
        _remove_journal(destination, journal, journal_identity)
        if authority_hook is not None:
            authority_hook(
                "publication_reset",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                },
            )
        return None
    expected_identity = _identity_from_document(journal["stage_identity"])
    stage_identity = _optional_directory_identity(stage)
    destination_identity = _optional_directory_identity(destination)
    if stage_identity == expected_identity and destination_identity is None:
        source = stage
    elif destination_identity == expected_identity and stage_identity is None:
        source = destination
    else:
        _fail(
            "standalone_game_recovery_ambiguous",
            "stage/destination identity is missing, changed, or conflicting",
        )
    try:
        verified = verify_standalone_game(
            source,
            expected_content_hash=journal["standalone_game_hash"],
        )
    except StandaloneGameError as exc:
        if journal["state"] == "copying" and source == stage:
            try:
                _verify_owned_stage_subset(stage, journal)
            except StandaloneGameError:
                pass
            else:
                _fail(
                    "standalone_game_recovery_required",
                    "the exact incomplete materialization stage and journal were retained "
                    "for explicit recovery",
                    recovery_evidence=retained_recovery_evidence(
                        stage_path=stage,
                        stage_identity=expected_identity,
                        journal_path=_journal_path(destination),
                        journal_identity=journal_identity,
                    ),
                )
        _fail("standalone_game_recovery_ambiguous", str(exc))
    try:
        _journal_matches_game(journal, verified, destination)
    finally:
        verified.close()
    if journal["state"] == "copying":
        ready = {**journal, "state": "ready"}
        journal_identity = _write_journal(
            destination,
            ready,
            create=False,
            expected_document=journal,
            expected_identity=journal_identity,
        )
        journal = ready
    if source == stage:
        if _require_external_destination(destination) != _identity_from_document(
            journal["parent_identity"],
            context="parent",
        ):
            _fail(
                "standalone_game_recovery_ambiguous",
                "destination parent changed before recovered publication",
            )
        try:
            with publish_directory_noreplace(
                stage,
                destination,
                expected_source_identity=expected_identity,
                expected_parent_identity=_identity_from_document(
                    journal["parent_identity"],
                    context="parent",
                ),
            ) as published_identity:
                if published_identity != expected_identity:
                    _fail(
                        "standalone_game_publication_identity_mismatch",
                        "recovered root identity changed",
                    )
                fsync_directory(
                    destination.parent,
                    context="recovered standalone game parent",
                )
        except (DirectoryPublishError, FileExistsError) as exc:
            _fail("standalone_game_recovery_failed", str(exc))
    verified = verify_standalone_game(
        destination,
        expected_content_hash=journal["standalone_game_hash"],
    )
    try:
        if verified.root_identity != expected_identity:
            _fail(
                "standalone_game_publication_indeterminate",
                "visible game differs from journal identity",
            )
        _journal_matches_game(journal, verified, destination)
        if _require_external_destination(destination) != _identity_from_document(
            journal["parent_identity"],
            context="parent",
        ):
            _fail(
                "standalone_game_recovery_ambiguous",
                "destination boundary changed during recovery",
            )
        if authority_hook is not None:
            authority_hook(
                "publication_verified",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                    "stage_identity": list(expected_identity),
                    "published_identity": list(verified.root_identity),
                    "journal_payload_sha256": _journal_payload_sha256(
                        journal,
                        state="ready",
                    ),
                    "journal_payload_state": "ready",
                },
            )
        _remove_journal(destination, journal, journal_identity)
        final = verify_standalone_game(
            destination,
            expected_content_hash=journal["standalone_game_hash"],
        )
    finally:
        verified.close()
    if _read_journal(destination) is not None:
        final.close()
        _fail(
            "standalone_game_publication_indeterminate",
            "journal reappeared after finalization",
        )
    return final


def recover_standalone_game(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
    expected_root_identity: DirectoryIdentity | None = None,
    expected_journal_identity: DirectoryIdentity | None = None,
    expected_operation_id: str | None = None,
    expected_content_hash: str | None = None,
    expected_tree_hash: str | None = None,
    expected_stage_identity: DirectoryIdentity | None = None,
    expected_journal_payload_sha256: str | None = None,
    expected_journal_payload_state: str | None = None,
    allow_missing_expected_journal: bool = False,
    require_journal_for_visible: bool = False,
    require_intent_journal: bool = False,
    stage_allocated: bool = False,
    reset_pending: bool = False,
    reject_unbound_journal: bool = False,
    _authority_hook: _AuthorityHook | None = None,
) -> VerifiedStandaloneGame | None:
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    parent_identity = _require_external_destination(destination_path)
    if expected_parent_identity is not None and parent_identity != expected_parent_identity:
        _fail(
            "standalone_game_directory_invalid",
            "destination parent differs from the expected identity",
        )
    with _destination_lock(
        destination_path,
        expected_parent_identity=parent_identity,
    ):
        if _require_external_destination(destination_path) != parent_identity:
            _fail(
                "standalone_game_directory_invalid",
                "destination parent changed before recovery",
            )
        verified = _recover_locked(
            destination_path,
            expected_root_identity=expected_root_identity,
            expected_journal_identity=expected_journal_identity,
            expected_operation_id=expected_operation_id,
            expected_content_hash=expected_content_hash,
            expected_tree_hash=expected_tree_hash,
            expected_stage_identity=expected_stage_identity,
            expected_journal_payload_sha256=expected_journal_payload_sha256,
            expected_journal_payload_state=expected_journal_payload_state,
            allow_missing_expected_journal=allow_missing_expected_journal,
            require_journal_for_visible=require_journal_for_visible,
            require_intent_journal=require_intent_journal,
            stage_allocated=stage_allocated,
            reset_pending=reset_pending,
            reject_unbound_journal=reject_unbound_journal,
            authority_hook=_authority_hook,
        )
        if (
            verified is not None
            and expected_root_identity is not None
            and verified.root_identity != expected_root_identity
        ):
            verified.close()
            _fail(
                "standalone_game_root_identity_mismatch",
                "recovered standalone root differs from the retained identity",
            )
        return verified


def _verify_owned_stage_subset(
    stage: Path,
    journal: Mapping[str, Any],
) -> None:
    captured, directories = capture_standalone_tree_with_directories(stage)
    files = dict(captured)
    if not files:
        if directories:
            _fail(
                "standalone_game_rollback_ambiguous",
                "stage contains foreign empty directories",
            )
        return
    manifest_payload = files.get(GAME_MANIFEST_PATH)
    if (
        manifest_payload is None
        or len(manifest_payload) != journal["manifest_size_bytes"]
        or hashlib.sha256(manifest_payload).hexdigest() != journal["manifest_sha256"]
    ):
        _fail(
            "standalone_game_rollback_ambiguous",
            "stage manifest is absent or differs",
        )
    lock_payload = files.get(GAME_LOCK_PATH)
    if lock_payload is None:
        if set(files) != {GAME_MANIFEST_PATH} or directories:
            _fail(
                "standalone_game_rollback_ambiguous",
                "stage has unbound files or directories before its payload lock",
            )
        return
    if (
        len(lock_payload) != journal["lock_size_bytes"]
        or hashlib.sha256(lock_payload).hexdigest() != journal["lock_sha256"]
    ):
        _fail("standalone_game_rollback_ambiguous", "stage payload lock differs")
    try:
        lock = validate_standalone_game_lock_document(
            decode_json_object(lock_payload, GAME_LOCK_PATH)
        )
    except StandaloneDistributionError as exc:
        _fail("standalone_game_rollback_ambiguous", str(exc))
    records = {item["path"]: item for item in lock["files"]}
    allowed = {GAME_MANIFEST_PATH, GAME_LOCK_PATH, *records}
    owned_directories = {
        "/".join(parts[:depth])
        for path in files
        for parts in (Path(path).parts,)
        for depth in range(1, len(parts))
    }
    if directories != owned_directories:
        _fail(
            "standalone_game_rollback_ambiguous",
            "stage directory closure differs from its present owned files",
        )
    if not set(files).issubset(allowed):
        _fail("standalone_game_rollback_ambiguous", "stage contains foreign files")
    for path, payload in files.items():
        if path in {GAME_MANIFEST_PATH, GAME_LOCK_PATH}:
            continue
        record = records[path]
        if (
            len(payload) != record["size_bytes"]
            or hashlib.sha256(payload).hexdigest() != record["sha256"]
        ):
            _fail(
                "standalone_game_rollback_ambiguous",
                f"stage file differs from its exact lock: {path}",
            )


def rollback_standalone_game(
    destination: str | Path,
    *,
    expected_parent_identity: DirectoryIdentity | None = None,
    expected_journal_identity: DirectoryIdentity | None = None,
    expected_operation_id: str | None = None,
    expected_content_hash: str | None = None,
    expected_tree_hash: str | None = None,
    expected_stage_identity: DirectoryIdentity | None = None,
    expected_journal_payload_sha256: str | None = None,
    expected_journal_payload_state: str | None = None,
    allow_missing_expected_journal: bool = False,
    require_intent_journal: bool = False,
    stage_allocated: bool = False,
    reset_pending: bool = False,
    reject_unbound_journal: bool = False,
    _authority_hook: _AuthorityHook | None = None,
) -> dict[str, object]:
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    parent_identity = _require_external_destination(destination_path)
    if expected_parent_identity is not None and parent_identity != expected_parent_identity:
        _fail(
            "standalone_game_directory_invalid",
            "destination parent differs from the expected identity",
        )
    with _destination_lock(
        destination_path,
        expected_parent_identity=parent_identity,
    ):
        if _require_external_destination(destination_path) != parent_identity:
            _fail(
                "standalone_game_directory_invalid",
                "destination parent changed before rollback",
            )
        loaded = _read_journal(destination_path)
        if loaded is None:
            if reset_pending:
                if (
                    not allow_missing_expected_journal
                    or expected_journal_identity is None
                    or expected_operation_id is None
                    or _optional_directory_identity(destination_path) is not None
                ):
                    _fail(
                        "standalone_game_rollback_ambiguous",
                        "standalone rollback reset authority is incomplete",
                    )
                _require_reset_stage_absent(
                    destination_path,
                    expected_operation_id,
                    reason_code="standalone_game_rollback_ambiguous",
                )
                if _authority_hook is not None:
                    _authority_hook(
                        "publication_reset",
                        {
                            "journal_identity": list(expected_journal_identity),
                            "operation_id": expected_operation_id,
                        },
                    )
                return {"status": "rolled_back", "operation_id": expected_operation_id}
            if (
                expected_journal_identity is not None or expected_operation_id is not None
            ) and not allow_missing_expected_journal:
                _fail(
                    "standalone_game_rollback_ambiguous",
                    "retained standalone rollback journal disappeared",
                )
            return {"status": "no_operation"}
        journal, journal_identity, _payload, partial_tail = loaded
        if reject_unbound_journal and (
            expected_journal_identity is None or expected_operation_id is None
        ):
            _fail(
                "standalone_game_rollback_ambiguous",
                "standalone rollback journal is not bound to trusted authority",
            )
        if expected_journal_identity is not None and journal_identity != expected_journal_identity:
            _fail(
                "standalone_game_rollback_ambiguous",
                "standalone rollback journal identity changed",
            )
        if expected_operation_id is not None and (journal["operation_id"] != expected_operation_id):
            _fail(
                "standalone_game_rollback_ambiguous",
                "standalone rollback operation identity changed",
            )
        _require_journal_payload_authority(
            journal,
            expected_sha256=expected_journal_payload_sha256,
            expected_state=expected_journal_payload_state,
            stage_allocated=stage_allocated,
            reason_code="standalone_game_rollback_ambiguous",
        )
        if expected_content_hash is not None and (
            journal["standalone_game_hash"] != expected_content_hash
        ):
            _fail(
                "standalone_game_rollback_ambiguous",
                "standalone rollback content authority changed",
            )
        if expected_tree_hash is not None and (journal["payload_tree_hash"] != expected_tree_hash):
            _fail(
                "standalone_game_rollback_ambiguous",
                "standalone rollback tree authority changed",
            )
        if require_intent_journal and journal["state"] != "intent":
            _fail(
                "standalone_game_rollback_ambiguous",
                "standalone rollback advanced before its stage authority was retained",
            )
        if (
            journal["state"] != "intent"
            and expected_stage_identity is not None
            and (_identity_from_document(journal["stage_identity"]) != expected_stage_identity)
        ):
            _fail(
                "standalone_game_rollback_ambiguous",
                "standalone rollback stage authority changed",
            )
        if partial_tail:
            _fail("standalone_game_rollback_ambiguous", "journal has a torn tail")
        if _optional_directory_identity(destination_path) is not None:
            _fail(
                "standalone_game_rollback_committed",
                "rollback never removes a visible standalone game",
            )
        stage = destination_path.parent / journal["stage_name"]
        owned_stage_identity: DirectoryIdentity | None = None
        if journal["state"] == "intent":
            actual_stage_identity = _optional_directory_identity(stage)
            if stage_allocated:
                if actual_stage_identity is None and reset_pending:
                    pass
                elif (
                    expected_stage_identity is None
                    or actual_stage_identity != expected_stage_identity
                ):
                    _fail(
                        "standalone_game_rollback_ambiguous",
                        "allocated rollback stage identity changed",
                    )
                else:
                    _verify_owned_stage_subset(stage, journal)
                    owned_stage_identity = expected_stage_identity
            elif actual_stage_identity is not None:
                _fail("standalone_game_rollback_ambiguous", "intent has a stage")
        else:
            expected_identity = _identity_from_document(journal["stage_identity"])
            if _require_external_destination(destination_path) != _identity_from_document(
                journal["parent_identity"],
                context="parent",
            ):
                _fail(
                    "standalone_game_rollback_ambiguous",
                    "destination parent changed before rollback cleanup",
                )
            actual_stage_identity = _optional_directory_identity(stage)
            if actual_stage_identity is None and reset_pending:
                pass
            elif actual_stage_identity != expected_identity:
                _fail("standalone_game_rollback_ambiguous", "stage identity changed")
            else:
                _verify_owned_stage_subset(stage, journal)
                owned_stage_identity = expected_identity
        if _authority_hook is not None:
            _authority_hook(
                "publication_resetting",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                },
            )
        if owned_stage_identity is not None:
            try:
                quarantine_and_remove_verified_directory(
                    stage,
                    owned_stage_identity,
                    verify=lambda path: _verify_owned_stage_subset(path, journal),
                )
            except DirectoryPublishRecoveryRequiredError as exc:
                _fail(
                    "standalone_game_rollback_recovery_required",
                    "automatic rollback cleanup is unavailable; the exact owned stage "
                    f"and materialization journal were retained: {exc}",
                    recovery_evidence=retained_recovery_evidence(
                        stage_path=stage,
                        stage_identity=owned_stage_identity,
                        journal_path=_journal_path(destination_path),
                        journal_identity=journal_identity,
                    ),
                )
            except DirectoryPublishIndeterminateError as exc:
                _fail("standalone_game_rollback_indeterminate", str(exc))
            except (DirectoryPublishError, OSError) as exc:
                _fail("standalone_game_rollback_failed", str(exc))
        _remove_journal(destination_path, journal, journal_identity)
        if _authority_hook is not None:
            _authority_hook(
                "publication_reset",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": journal["operation_id"],
                },
            )
        return {
            "status": "rolled_back",
            "operation_id": journal["operation_id"],
            "content_hash": journal["standalone_game_hash"],
        }


def materialize_game(
    materialization_bundle: str | Path,
    destination: str | Path,
    *,
    expected_content_hash: str | None = None,
    expected_source_identity: DirectoryIdentity | None = None,
    expected_parent_identity: DirectoryIdentity | None = None,
    _verified_source: VerifiedGameMaterializationBundle | None = None,
    _publication_hook: _PublicationHook | None = None,
    _authority_hook: _AuthorityHook | None = None,
    _expected_journal_identity: DirectoryIdentity | None = None,
    _expected_operation_id: str | None = None,
    _expected_stage_identity: DirectoryIdentity | None = None,
    _expected_journal_payload_sha256: str | None = None,
    _expected_journal_payload_state: str | None = None,
    _require_intent_journal: bool = False,
    _stage_allocated: bool = False,
    _reset_pending: bool = False,
    _reject_unbound_journal: bool = False,
) -> VerifiedStandaloneGame:
    if not ((sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt"):
        _fail(
            "standalone_game_platform_unsupported",
            "standalone materialization supports only Linux and Windows",
        )
    destination_path = Path(os.path.abspath(os.fspath(destination)))
    source_path = Path(os.path.abspath(os.fspath(materialization_bundle)))
    stage = destination_path.parent / (
        f".{destination_path.name}.standalone-stage-{uuid.uuid4().hex}"
    )
    parent_identity = _require_external_destination(destination_path)
    if expected_parent_identity is not None and parent_identity != expected_parent_identity:
        _fail(
            "standalone_game_directory_invalid",
            "destination parent differs from the expected identity",
        )
    if _paths_overlap(destination_path, source_path) or _paths_overlap(stage, source_path):
        _fail(
            "standalone_game_path_overlap",
            "source, stage, and destination must be disjoint",
        )
    source = (
        require_standalone_materialization_source(source_path)
        if _verified_source is None
        else _verified_source
    )
    close_source = _verified_source is None
    try:
        if source.root != source_path or (
            expected_source_identity is not None
            and source.root_identity != expected_source_identity
        ):
            _fail(
                "game_materialization_bundle_identity_mismatch",
                "materialization source differs from the expected retained identity",
            )
        if not source.manifest["materialization_ready"]:
            _fail(
                "materialization_bundle_not_ready",
                "materialization bundle lacks the complete exact standalone launcher inventory",
            )
        if (
            expected_content_hash is not None
            and source.manifest["content_hash"] != expected_content_hash
        ):
            _fail(
                "game_materialization_bundle_expected_hash_mismatch",
                "materialization bundle does not match the expected immutable hash",
            )
        payload, manifest, lock, _platform = _build_payload(source)
    finally:
        if close_source:
            source.close()
    all_files = {
        GAME_MANIFEST_PATH: canonical_contract_bytes(manifest),
        GAME_LOCK_PATH: canonical_contract_bytes(lock),
        **payload,
    }
    with _destination_lock(
        destination_path,
        expected_parent_identity=parent_identity,
    ):
        if _require_external_destination(destination_path) != parent_identity:
            _fail(
                "standalone_game_directory_invalid",
                "destination parent changed before materialization",
            )
        if (
            _read_journal(destination_path) is None
            and _optional_directory_identity(destination_path) is not None
        ):
            _fail(
                "standalone_game_destination_exists",
                "destination already contains a standalone game",
            )
        recovered = _recover_locked(
            destination_path,
            expected_journal_identity=_expected_journal_identity,
            expected_operation_id=_expected_operation_id,
            expected_content_hash=manifest["content_hash"],
            expected_tree_hash=lock["tree_hash"],
            expected_stage_identity=_expected_stage_identity,
            expected_journal_payload_sha256=_expected_journal_payload_sha256,
            expected_journal_payload_state=_expected_journal_payload_state,
            allow_missing_expected_journal=_reset_pending,
            require_intent_journal=_require_intent_journal,
            stage_allocated=_stage_allocated,
            reset_pending=_reset_pending,
            reject_unbound_journal=_reject_unbound_journal,
            authority_hook=_authority_hook,
        )
        if recovered is not None:
            if recovered.manifest["content_hash"] == manifest["content_hash"]:
                return recovered
            recovered.close()
            _fail(
                "standalone_game_destination_exists",
                "destination contains a different immutable standalone game",
            )
        if destination_path.exists() or destination_path.is_symlink():
            _fail(
                "standalone_game_destination_exists",
                "destination already exists",
            )
        operation_id = uuid.uuid4().hex
        stage = destination_path.parent / (
            f".{destination_path.name}.standalone-stage-{operation_id}"
        )
        intent = _journal_document(
            operation_id=operation_id,
            state="intent",
            stage=stage,
            destination=destination_path,
            parent_identity=parent_identity,
            stage_identity=None,
            manifest=manifest,
            lock=lock,
            materialization_hash=manifest["materialization_bundle"]["content_hash"],
        )
        journal = intent
        journal_identity = _write_journal(
            destination_path,
            intent,
            create=True,
        )
        if _authority_hook is not None:
            _authority_hook(
                "publication_started",
                {
                    "journal_identity": list(journal_identity),
                    "operation_id": operation_id,
                    "journal_payload_sha256": _journal_payload_sha256(
                        journal,
                        state="intent",
                    ),
                    "journal_payload_state": "intent",
                },
            )
        if _publication_hook is not None:
            _publication_hook("after_intent_journal_written", None)
        try:
            with create_retained_stage(
                stage,
                expected_parent_identity=parent_identity,
            ) as writer:
                stage_identity = writer.identity
                if _authority_hook is not None:
                    _authority_hook(
                        "publication_stage_allocated",
                        {
                            "journal_identity": list(journal_identity),
                            "operation_id": operation_id,
                            "stage_identity": list(stage_identity),
                            "journal_payload_sha256": _journal_payload_sha256(
                                journal,
                                state="intent",
                            ),
                            "journal_payload_state": "intent",
                        },
                    )
                copying = _journal_document(
                    operation_id=operation_id,
                    state="copying",
                    stage=stage,
                    destination=destination_path,
                    parent_identity=parent_identity,
                    stage_identity=stage_identity,
                    manifest=manifest,
                    lock=lock,
                    materialization_hash=manifest["materialization_bundle"]["content_hash"],
                )
                journal_identity = _write_journal(
                    destination_path,
                    copying,
                    create=False,
                    expected_document=journal,
                    expected_identity=journal_identity,
                )
                journal = copying
                if _authority_hook is not None:
                    _authority_hook(
                        "publication_staged",
                        {
                            "journal_identity": list(journal_identity),
                            "operation_id": operation_id,
                            "stage_identity": list(stage_identity),
                            "journal_payload_sha256": _journal_payload_sha256(
                                journal,
                                state="copying",
                            ),
                            "journal_payload_state": "copying",
                        },
                    )
                if _publication_hook is not None:
                    _publication_hook("after_copying_journal_written", stage)
                ordered_files = (
                    GAME_MANIFEST_PATH,
                    GAME_LOCK_PATH,
                    *sorted(
                        (
                            relative
                            for relative in all_files
                            if relative not in {GAME_MANIFEST_PATH, GAME_LOCK_PATH}
                        ),
                        key=lambda item: item.encode("utf-8"),
                    ),
                )
                for relative in ordered_files:
                    writer.write_file(relative, all_files[relative])
                    if _publication_hook is not None:
                        _publication_hook("after_file_written", stage / relative)
                writer.fsync()
                verified_stage = verify_standalone_game(
                    stage,
                    expected_content_hash=manifest["content_hash"],
                    _retained_stage_writer=writer,
                )
                try:
                    _journal_matches_game(journal, verified_stage, destination_path)
                finally:
                    verified_stage.close()
                _run_independent_verifier(stage)
                ready = {**journal, "state": "ready"}
                journal_identity = _write_journal(
                    destination_path,
                    ready,
                    create=False,
                    expected_document=journal,
                    expected_identity=journal_identity,
                )
                journal = ready
                if _publication_hook is not None:
                    _publication_hook("after_ready_journal_written", stage)
                writer.require_binding()
            if _publication_hook is not None:
                _publication_hook("before_destination_publish", stage)
            if _require_external_destination(destination_path) != parent_identity:
                _fail(
                    "standalone_game_publication_indeterminate",
                    "destination boundary changed before publication",
                )
            with publish_directory_noreplace(
                stage,
                destination_path,
                expected_source_identity=stage_identity,
                expected_parent_identity=parent_identity,
            ) as published_identity:
                if published_identity != stage_identity:
                    _fail(
                        "standalone_game_publication_identity_mismatch",
                        "published root identity changed",
                    )
                fsync_directory(
                    destination_path.parent,
                    context="published standalone game parent",
                )
                verified = verify_standalone_game(
                    destination_path,
                    expected_content_hash=manifest["content_hash"],
                )
                try:
                    if verified.root_identity != stage_identity:
                        _fail(
                            "standalone_game_publication_indeterminate",
                            "visible destination differs from the retained stage",
                        )
                    _journal_matches_game(journal, verified, destination_path)
                    _run_independent_verifier(destination_path)
                except BaseException:
                    verified.close()
                    raise
                if _require_external_destination(destination_path) != parent_identity:
                    verified.close()
                    _fail(
                        "standalone_game_publication_indeterminate",
                        "destination boundary changed during publication",
                    )
                if _publication_hook is not None:
                    _publication_hook("before_journal_remove", destination_path)
                if _authority_hook is not None:
                    _authority_hook(
                        "publication_verified",
                        {
                            "journal_identity": list(journal_identity),
                            "operation_id": operation_id,
                            "stage_identity": list(stage_identity),
                            "published_identity": list(verified.root_identity),
                            "journal_payload_sha256": _journal_payload_sha256(
                                journal,
                                state="ready",
                            ),
                            "journal_payload_state": "ready",
                        },
                    )
                _remove_journal(destination_path, journal, journal_identity)
                if _publication_hook is not None:
                    _publication_hook("after_journal_remove", destination_path)
                if _read_journal(destination_path) is not None:
                    verified.close()
                    _fail(
                        "standalone_game_publication_indeterminate",
                        "journal reappeared after finalization",
                    )
                final = verify_standalone_game(
                    destination_path,
                    expected_content_hash=manifest["content_hash"],
                )
                verified.close()
                if final.root_identity != stage_identity:
                    final.close()
                    _fail(
                        "standalone_game_publication_indeterminate",
                        "destination identity changed after journal removal",
                    )
                return final
        except StandaloneGameError:
            raise
        except FileExistsError as exc:
            _fail("standalone_game_destination_exists", str(exc))
        except DirectoryPublishError as exc:
            _fail(
                "standalone_game_publication_failed",
                f"{exc}; retained stage: {stage}",
            )


__all__ = [
    "STANDALONE_GAME_FORMAT",
    "STANDALONE_GAME_LOCK_FORMAT",
    "STANDALONE_PLATFORM_FORMAT",
    "StandaloneGameError",
    "VerifiedStandaloneGame",
    "build_standalone_game_documents",
    "materialize_game",
    "recover_standalone_game",
    "require_standalone_materialization_source",
    "rollback_standalone_game",
    "validate_standalone_game_document",
    "validate_standalone_game_lock_document",
    "validate_standalone_platform_document",
    "verify_standalone_game",
]
