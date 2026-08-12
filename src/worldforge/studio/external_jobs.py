from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from worldforge.game_materialization_bundle import (
    GameMaterializationBundleError,
    verify_game_materialization_bundle,
)
from worldforge.game_package import (
    WorldForgeGamePackageError,
    extract_game_package,
    package_game,
    recover_game_package_extraction,
    rollback_game_package_extraction,
    verify_game_package,
)
from worldforge.standalone_game import (
    StandaloneGameError,
    materialize_game,
    recover_standalone_game,
    rollback_standalone_game,
    verify_standalone_game,
)
from worldforge.studio.errors import StudioError
from worldforge.studio.workspaces import _pinned_ancestor_identities


class ExternalJobExecutionError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        recovery_evidence: dict[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.recovery_evidence = dict(recovery_evidence or {})
        super().__init__(f"{code}: {message}")


def _fail(
    code: str,
    message: str,
    *,
    recovery_evidence: dict[str, object] | None = None,
) -> None:
    raise ExternalJobExecutionError(
        code,
        message,
        recovery_evidence=recovery_evidence,
    )


def _materialization_source(
    source: Path,
    expected_identity: tuple[int, int],
) -> object:
    try:
        verified = verify_game_materialization_bundle(source)
    except GameMaterializationBundleError:
        _fail("source_changed", "Materialization source is not an exact verified bundle")
    if verified.root_identity != expected_identity:
        verified.close()
        _fail("source_changed", "Materialization source identity changed")
    return verified


def _standalone_source(
    source: Path,
    expected_identity: tuple[int, int],
) -> object:
    try:
        return verify_standalone_game(
            source,
            expected_root_identity=expected_identity,
        )
    except StandaloneGameError:
        _fail("source_changed", "Standalone source is not an exact verified game")


def _package_source(
    source: Path,
    expected_identity: tuple[int, int],
) -> object:
    try:
        return verify_game_package(
            source,
            expected_file_identity=expected_identity,
        )
    except WorldForgeGamePackageError:
        _fail("source_changed", "Package source is not an exact verified archive")


def _require_hash(actual: str, expected: str) -> None:
    if actual != expected:
        _fail("source_changed", "External source does not match its expected content hash")


@contextmanager
def _retained_target_parent(
    target: Path,
    expected_identity: tuple[int, int],
) -> Iterator[None]:
    try:
        with _pinned_ancestor_identities(
            target.parent,
            context="External target parent",
        ) as identities:
            if identities[-1] != expected_identity:
                _fail("target_changed", "External target parent identity changed")
            yield
    except StudioError:
        _fail("target_changed", "External target parent identity changed")


def _materialize_result(verified: object, target_grant_id: str) -> dict[str, Any]:
    manifest = verified.manifest  # type: ignore[attr-defined]
    lock = verified.lock  # type: ignore[attr-defined]
    return {
        "operation": "game.materialize",
        "game_id": manifest["game_id"],
        "standalone_hash": manifest["content_hash"],
        "payload_lock_hash": lock["content_hash"],
        "runtime_bundle_hash": manifest["lineage"]["runtime_bundle_hash"],
        "target_grant_id": target_grant_id,
    }


def _package_result(verified: object, target_grant_id: str) -> dict[str, Any]:
    manifest = verified.manifest  # type: ignore[attr-defined]
    return {
        "operation": "game.package",
        "package_id": manifest["package_id"],
        "content_hash": manifest["content_hash"],
        "archive_sha256": verified.archive_sha256,  # type: ignore[attr-defined]
        "game_id": manifest["game_id"],
        "game_hash": manifest["standalone_game"]["content_hash"],
        "target_grant_id": target_grant_id,
    }


def _extract_result(
    package_manifest: dict[str, Any],
    archive_sha256: str,
    verified: object,
    target_grant_id: str,
) -> dict[str, Any]:
    manifest = verified.manifest  # type: ignore[attr-defined]
    lock = verified.lock  # type: ignore[attr-defined]
    return {
        "operation": "game.package.extract",
        "package_id": package_manifest["package_id"],
        "package_hash": package_manifest["content_hash"],
        "archive_sha256": archive_sha256,
        "game_id": manifest["game_id"],
        "game_hash": manifest["content_hash"],
        "payload_lock_hash": lock["content_hash"],
        "target_grant_id": target_grant_id,
    }


def execute_external_operation(
    *,
    operation: str,
    source: Path,
    target: Path,
    expected_hash: str,
    target_grant_id: str,
    expected_source_identity: tuple[int, int],
    expected_parent_identity: tuple[int, int],
) -> dict[str, Any]:
    with _retained_target_parent(target, expected_parent_identity):
        return _execute_external_operation_bound(
            operation=operation,
            source=source,
            target=target,
            expected_hash=expected_hash,
            target_grant_id=target_grant_id,
            expected_source_identity=expected_source_identity,
            expected_parent_identity=expected_parent_identity,
        )


def _execute_external_operation_bound(
    *,
    operation: str,
    source: Path,
    target: Path,
    expected_hash: str,
    target_grant_id: str,
    expected_source_identity: tuple[int, int],
    expected_parent_identity: tuple[int, int],
) -> dict[str, Any]:
    try:
        if operation == "game.materialize":
            source_snapshot = _materialization_source(source, expected_source_identity)
            try:
                source_manifest = source_snapshot.manifest  # type: ignore[attr-defined]
                _require_hash(source_manifest["content_hash"], expected_hash)
                if target.exists() or target.is_symlink():
                    existing = recover_standalone_game(
                        target,
                        expected_parent_identity=expected_parent_identity,
                    )
                    if existing is None:
                        _fail(
                            "recovery_ambiguous",
                            "Visible standalone game disappeared during recovery",
                        )
                    try:
                        manifest = existing.manifest
                        expected_lineage = {
                            "gamepack_hash": source_manifest["lineage"]["gamepack_hash"],
                            "assetpack_hash": source_manifest["lineage"]["assetpack_hash"],
                            "runtime_snapshot_hash": source_manifest["lineage"][
                                "runtime_snapshot_hash"
                            ],
                            "runtime_composition_hash": source_manifest["lineage"][
                                "composition_hash"
                            ],
                            "runtime_bundle_hash": source_manifest["lineage"][
                                "runtime_bundle_hash"
                            ],
                        }
                        if manifest["lineage"] != expected_lineage:
                            _fail(
                                "recovery_ambiguous",
                                "Visible standalone game does not match "
                                "the reserved materialization",
                            )
                        return _materialize_result(existing, target_grant_id)
                    finally:
                        existing.close()
                verified = materialize_game(
                    source,
                    target,
                    expected_content_hash=expected_hash,
                    expected_source_identity=expected_source_identity,
                    expected_parent_identity=expected_parent_identity,
                    _verified_source=source_snapshot,  # type: ignore[arg-type]
                )
                try:
                    return _materialize_result(verified, target_grant_id)
                finally:
                    verified.close()
            finally:
                source_snapshot.close()  # type: ignore[attr-defined]
        if operation == "game.package":
            source_snapshot = _standalone_source(source, expected_source_identity)
            try:
                _require_hash(
                    source_snapshot.manifest["content_hash"],  # type: ignore[attr-defined]
                    expected_hash,
                )
                if target.exists() or target.is_symlink():
                    existing = verify_game_package(target)
                    try:
                        manifest = existing.manifest
                        if manifest["standalone_game"]["content_hash"] != expected_hash:
                            _fail(
                                "recovery_ambiguous",
                                "Visible package does not match the reserved standalone game",
                            )
                        return _package_result(existing, target_grant_id)
                    finally:
                        existing.close()
                verified = package_game(
                    source,
                    target,
                    expected_source_identity=expected_source_identity,
                    expected_parent_identity=expected_parent_identity,
                    _verified_source=source_snapshot,  # type: ignore[arg-type]
                )
                try:
                    return _package_result(verified, target_grant_id)
                finally:
                    verified.close()
            finally:
                source_snapshot.close()  # type: ignore[attr-defined]
        if operation == "game.package.extract":
            source_snapshot = _package_source(source, expected_source_identity)
            try:
                package_manifest = source_snapshot.manifest  # type: ignore[attr-defined]
                archive_sha256 = source_snapshot.archive_sha256  # type: ignore[attr-defined]
                _require_hash(package_manifest["content_hash"], expected_hash)
                if target.exists() or target.is_symlink():
                    existing = recover_game_package_extraction(
                        target,
                        expected_parent_identity=expected_parent_identity,
                    )
                    if existing is None:
                        existing = verify_standalone_game(
                            target,
                            expected_content_hash=package_manifest["standalone_game"][
                                "content_hash"
                            ],
                        )
                    try:
                        return _extract_result(
                            package_manifest,
                            archive_sha256,
                            existing,
                            target_grant_id,
                        )
                    finally:
                        existing.close()
                verified = extract_game_package(
                    source,
                    target,
                    expected_source_identity=expected_source_identity,
                    expected_parent_identity=expected_parent_identity,
                    _verified_package=source_snapshot,  # type: ignore[arg-type]
                )
                try:
                    if (
                        verified.manifest["content_hash"]
                        != package_manifest["standalone_game"]["content_hash"]
                    ):
                        _fail(
                            "recovery_ambiguous",
                            "Extracted game does not match the reserved package",
                        )
                    return _extract_result(
                        package_manifest,
                        archive_sha256,
                        verified,
                        target_grant_id,
                    )
                finally:
                    verified.close()
            finally:
                source_snapshot.close()  # type: ignore[attr-defined]
    except ExternalJobExecutionError:
        raise
    except StandaloneGameError as exc:
        if exc.reason_code in {
            "standalone_game_directory_invalid",
            "standalone_game_lock_failed",
        }:
            _fail("target_changed", "External target parent identity changed")
        if exc.reason_code.endswith("_recovery_required"):
            _fail(
                "recovery_required",
                "External standalone artifact requires retained-evidence recovery",
                recovery_evidence=exc.recovery_evidence,
            )
        _fail("recovery_ambiguous", "External artifact mutation requires recovery")
    except WorldForgeGamePackageError as exc:
        if exc.reason_code in {
            "game_package_destination_invalid",
            "game_package_extraction_lock_failed",
        }:
            _fail("target_changed", "External target parent identity changed")
        if exc.reason_code.endswith("_recovery_required"):
            _fail(
                "recovery_required",
                "External package artifact requires retained-evidence recovery",
                recovery_evidence=exc.recovery_evidence,
            )
        _fail("recovery_ambiguous", "External artifact mutation requires recovery")
    except GameMaterializationBundleError:
        _fail("recovery_ambiguous", "External artifact mutation requires recovery")
    _fail("execution_failed", "External operation is unsupported")


def rollback_external_operation(
    *,
    operation: str,
    target: Path,
    expected_parent_identity: tuple[int, int],
) -> None:
    try:
        if target.exists() or target.is_symlink():
            _fail(
                "recovery_ambiguous",
                "Visible destination bytes are preserved for explicit resume verification",
            )
        if operation == "game.materialize":
            result = rollback_standalone_game(
                target,
                expected_parent_identity=expected_parent_identity,
            )
        elif operation == "game.package.extract":
            result = rollback_game_package_extraction(
                target,
                expected_parent_identity=expected_parent_identity,
            )
        elif operation == "game.package":
            return
        else:
            _fail("recovery_failed", "External operation is unsupported")
    except ExternalJobExecutionError:
        raise
    except (StandaloneGameError, WorldForgeGamePackageError) as exc:
        if exc.reason_code.endswith("_recovery_required"):
            _fail(
                "recovery_required",
                "External rollback requires retained-evidence recovery",
                recovery_evidence=exc.recovery_evidence,
            )
        _fail(
            "recovery_ambiguous",
            "External mutation ownership could not be proven; bytes were preserved",
        )
    if result.get("status") not in {"no_operation", "rolled_back"}:
        _fail(
            "recovery_ambiguous",
            "External mutation ownership could not be proven; bytes were preserved",
        )
