from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gamepack_runtime import GameLogicError
from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.models import RUNTIME_API_VERSION, SUPPORTED_RUNTIME_FEATURES
from worldforge import (
    game_materialization_bundle,
    game_package,
    game_persistence,
    game_runtime_bundle,
    generic_asset_authority,
    generic_asset_fixture_authority,
    generic_asset_processing,
    generic_asset_production,
    generic_assetpack,
    generic_assets,
    generic_headless,
    generic_runtime,
    persistence_generation,
    repository_boundary,
    runtime_implementation,
    runtime_platform_lock,
    standalone_game,
)
from worldforge.asset_contracts import validate_asset_bibles
from worldforge.asset_inventory import derive_asset_inventory
from worldforge.asset_io import AssetContractError, read_json_object
from worldforge.asset_manifest_v3 import bind_asset_plan, finalize_asset_release
from worldforge.asset_processing import process_asset_recipe, verify_processing_receipt
from worldforge.asset_production import create_production_request, validate_production_receipt
from worldforge.assetpack import build_assetpack, verify_assetpack
from worldforge.assets import AssetManifestError, init_asset_manifest, validate_asset_manifest
from worldforge.bundle import (
    BundleError,
    export_runtime_bundle,
    import_runtime_bundle,
    verify_runtime_bundle,
)
from worldforge.claims import validate_claims
from worldforge.compiler import CompilationError, compile_project
from worldforge.composed_game import ComposedGameError, import_composed_bundle
from worldforge.contract_catalog import ContractCatalogError, audit_contracts
from worldforge.creation_contracts import read_creation_object
from worldforge.creation_route import CreationRouteError, route_creation_project
from worldforge.creation_scaffold import (
    CREATION_CONTENT_MODES,
    CREATION_PROJECT_KINDS,
    GAMEPLAY_FAMILIES,
    NARRATIVE_AUTHORSHIP_MODES,
    NARRATIVE_REQUIREMENTS,
    NARRATIVE_TOPOLOGIES,
    PRESENTATION_MODES,
    RUNTIME_SUPPORT_INTENTS,
    WORLD_PRESENCES,
    CreationScaffoldError,
    create_creation_project,
)
from worldforge.creation_workflow import (
    CreationWorkflowError,
    complete_creation_phase,
    load_creation_workflow_status,
    reconcile_creation_workflow,
    reopen_creation_phase,
)
from worldforge.file_stat import is_link_or_reparse, path_file_stat
from worldforge.game_analysis import (
    GameAnalysisError,
    analyze_gamepack,
    publish_game_analysis,
)
from worldforge.game_boundary import GameBoundaryError, audit_game_repository
from worldforge.game_scaffold import (
    GameScaffoldError,
    create_game_project,
    update_game_runtime_snapshot,
)
from worldforge.gamepack import (
    GamepackError,
    GamepackPartialPublicationError,
    build_authoring_capability_ledger,
    build_gamepack,
    load_game_source_project,
    load_gamepack,
    preflight_game_artifact_output,
    publish_capability_ledger,
    publish_gamepack,
)
from worldforge.identity_audit import IdentityAuditError, audit_identities
from worldforge.map_import import (
    MapImportError,
    import_map_file,
    load_mapping,
    write_imported_map,
)
from worldforge.narrative_analysis import analyze_project, write_analysis
from worldforge.project import SourceProjectError, load_source_project
from worldforge.renderpack import RenderPackBuildError, build_renderpack
from worldforge.runtime_audit import audit_runtime
from worldforge.scaffold import ScaffoldError, create_world_project
from worldforge.validation import validate_project
from worldforge.workflow import (
    WorkflowError,
    complete_phase,
    describe_status,
    load_status,
    reopen_phase,
)
from worldforge.world_lifecycle import (
    bump_world_version,
    clone_world_project,
    inspect_world_project,
    migrate_world_project,
    upgrade_legacy_world_project,
)
from worldforge.world_project_migration import WorldProjectMigrationError


class _CliCleanupError(RuntimeError):
    """Carries an owned CLI cleanup failure behind the primary operation error."""


_GENERIC_ASSET_DOCUMENT_VALIDATORS = {
    generic_assets.ASSET_SUBJECT_FORMAT: generic_assets.validate_asset_subject_document,
    generic_assets.ASSET_TARGET_FORMAT: generic_assets.validate_asset_target_document,
    generic_assets.ASSET_STYLE_FORMAT: generic_assets.validate_asset_style_document,
    generic_assets.ASSET_INVENTORY_FORMAT: generic_assets.validate_asset_inventory_document,
    generic_assets.ASSET_SPEC_FORMAT: generic_assets.validate_asset_specification_document,
    generic_asset_production.ASSET_PRODUCTION_REQUEST_FORMAT: (
        generic_asset_production.validate_asset_production_request_document
    ),
    generic_asset_production.ASSET_PRODUCTION_RECEIPT_FORMAT: (
        generic_asset_production.validate_asset_production_receipt_document
    ),
    generic_asset_production.ASSET_SELECTION_FORMAT: (
        generic_asset_production.validate_asset_selection_document
    ),
    generic_asset_production.ASSET_PROVENANCE_FORMAT: (
        generic_asset_production.validate_asset_provenance_record_document
    ),
    generic_asset_production.ASSET_LICENSE_FORMAT: (
        generic_asset_production.validate_asset_license_record_document
    ),
    generic_asset_processing.ASSET_PROCESSING_RECIPE_FORMAT: (
        generic_asset_processing.validate_asset_processing_recipe_document
    ),
    generic_asset_processing.ASSET_PROCESSING_RECEIPT_FORMAT: (
        generic_asset_processing.validate_asset_processing_receipt_document
    ),
    generic_asset_processing.ASSET_QA_REPORT_FORMAT: (
        generic_asset_processing.validate_asset_qa_report_document
    ),
    generic_asset_processing.ASSET_MANIFEST_FORMAT: (
        generic_asset_processing.validate_asset_manifest_document
    ),
    generic_asset_authority.ASSET_QA_REVIEW_RECEIPT_FORMAT: (
        generic_asset_authority.validate_asset_qa_review_receipt_document
    ),
    generic_asset_authority.ASSET_RELEASE_AUTHORITY_FORMAT: (
        generic_asset_authority.validate_asset_release_authority_document
    ),
}


def _validate_generic_asset_contract(path: Path) -> dict[str, Any]:
    document = read_creation_object(path)
    contract_format = document.get("format")
    if not isinstance(contract_format, str):
        raise ValueError("generic asset contract format must be a string")
    validator = _GENERIC_ASSET_DOCUMENT_VALIDATORS.get(contract_format)
    if validator is None:
        raise ValueError(f"unsupported generic asset contract format: {contract_format}")
    if document.get("format_version") != 1:
        raise ValueError(f"unsupported {contract_format} format_version")
    return validator(document)


def _resolve_generic_assetpack_cli_source(
    manifest_path: Path,
    *,
    _resolution_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Resolve one bounded conventional D2 tree by exact identity for D3 sealing."""

    try:
        manifest_path = Path(os.path.abspath(manifest_path))
        initial_info = path_file_stat(manifest_path)
        if (
            is_link_or_reparse(initial_info)
            or not stat.S_ISREG(initial_info.st_mode)
            or initial_info.st_nlink != 1
        ):
            raise ValueError("asset manifest must be a standalone regular file")
        initial_state = (
            initial_info.st_dev,
            initial_info.st_ino,
            initial_info.st_mode,
            initial_info.st_nlink,
            initial_info.st_size,
            initial_info.st_mtime_ns,
            initial_info.st_ctime_ns,
        )
        manifest = generic_asset_processing.validate_asset_manifest_document(
            read_creation_object(manifest_path)
        )
        opened_info = path_file_stat(manifest_path)
        opened_state = (
            opened_info.st_dev,
            opened_info.st_ino,
            opened_info.st_mode,
            opened_info.st_nlink,
            opened_info.st_size,
            opened_info.st_mtime_ns,
            opened_info.st_ctime_ns,
        )
        if opened_state != initial_state:
            raise ValueError("asset manifest identity changed during initial resolution")
        asset_root = manifest_path.absolute().parent
        project_root = asset_root.parent
        fixed_documents = {
            "subject": (
                asset_root / "subject.json",
                generic_assets.validate_asset_subject_document,
            ),
            "target": (
                asset_root / "target.json",
                generic_assets.validate_asset_target_document,
            ),
            "style": (
                asset_root / "style.json",
                generic_assets.validate_asset_style_document,
            ),
            "inventory": (
                asset_root / "inventory.json",
                generic_assets.validate_asset_inventory_document,
            ),
        }
        resolved = {
            name: validator(read_creation_object(path))
            for name, (path, validator) in fixed_documents.items()
        }
        artifacts = project_root / "artifacts"
        gamepack_paths = sorted(artifacts.glob("*.gamepack.json"))
        if not gamepack_paths or len(gamepack_paths) > 16:
            raise ValueError("artifacts must contain 1..16 direct gamepack candidates")
        gamepack_candidates = [load_gamepack(path) for path in gamepack_paths]
        gamepack_matches = [
            candidate
            for candidate in gamepack_candidates
            if candidate["format"] == manifest["gamepack"]["format"]
            and candidate["format_version"] == manifest["gamepack"]["format_version"]
            and candidate["game"]["id"] == manifest["gamepack"]["id"]
            and candidate["content_hash"] == manifest["gamepack"]["content_hash"]
        ]
        if len(gamepack_matches) != 1:
            raise ValueError("gamepack identity did not resolve exactly once")

        id_fields = {
            generic_assets.ASSET_SPEC_FORMAT: "spec_id",
            generic_asset_production.ASSET_PRODUCTION_REQUEST_FORMAT: "request_id",
            generic_asset_production.ASSET_PRODUCTION_RECEIPT_FORMAT: "receipt_id",
            generic_asset_production.ASSET_SELECTION_FORMAT: "selection_id",
            generic_asset_production.ASSET_PROVENANCE_FORMAT: "provenance_id",
            generic_asset_production.ASSET_LICENSE_FORMAT: "license_record_id",
            generic_asset_processing.ASSET_PROCESSING_RECIPE_FORMAT: "recipe_id",
            generic_asset_processing.ASSET_PROCESSING_RECEIPT_FORMAT: ("processing_receipt_id"),
            generic_asset_processing.ASSET_QA_REPORT_FORMAT: "qa_report_id",
        }

        def exact_document(
            documents: list[dict[str, Any]],
            identity: dict[str, Any],
        ) -> dict[str, Any]:
            id_field = id_fields[identity["format"]]
            matches = [
                document
                for document in documents
                if document.get("format") == identity["format"]
                and document.get("format_version") == identity["format_version"]
                and document.get(id_field) == identity["id"]
                and document.get("content_hash") == identity["content_hash"]
            ]
            if len(matches) != 1:
                raise ValueError(f"{identity['format']} identity did not resolve exactly once")
            return matches[0]

        asset_records = []
        for entry in manifest["assets"]:
            asset_id = entry["asset"]["asset_id"]
            specification = read_creation_object(asset_root / "specs" / f"{asset_id}.json")
            production_root = asset_root / "production" / asset_id
            candidate_paths = sorted(production_root.glob("*.json"))
            if not candidate_paths or len(candidate_paths) > 64:
                raise ValueError(f"{asset_id} production directory must contain 1..64 JSON records")
            documents = [read_creation_object(candidate_path) for candidate_path in candidate_paths]
            documents.append(specification)
            asset_records.append(
                {
                    "specification": exact_document(
                        documents,
                        entry["specification"],
                    ),
                    "request": exact_document(documents, entry["request"]),
                    "receipt": exact_document(documents, entry["receipt"]),
                    "selection": exact_document(documents, entry["selection"]),
                    "provenance": exact_document(documents, entry["provenance"]),
                    "license_records": [
                        exact_document(documents, identity) for identity in entry["licenses"]
                    ],
                    "recipe": exact_document(
                        documents,
                        entry["processing_recipe"],
                    ),
                    "processing_receipt": exact_document(
                        documents,
                        entry["processing_receipt"],
                    ),
                    "qa_report": exact_document(documents, entry["qa_report"]),
                }
            )
        if _resolution_hook is not None:
            _resolution_hook("before_manifest_revalidation")
        final_manifest = generic_asset_processing.validate_asset_manifest_document(
            read_creation_object(manifest_path)
        )
        final_info = path_file_stat(manifest_path)
        final_state = (
            final_info.st_dev,
            final_info.st_ino,
            final_info.st_mode,
            final_info.st_nlink,
            final_info.st_size,
            final_info.st_mtime_ns,
            final_info.st_ctime_ns,
        )
        if final_state != initial_state or final_manifest != manifest:
            raise ValueError(
                "asset manifest identity or canonical content changed during source resolution"
            )
        qa_reviews, release_authority, _candidate_assetpack = (
            generic_asset_fixture_authority.resolve_repository_fixture_asset_authority(
                project_root=project_root,
                manifest=manifest,
                gamepack=gamepack_matches[0],
                **resolved,
                asset_records=asset_records,
                artifact_root=project_root,
            )
        )
        return {
            "manifest": manifest,
            "gamepack": gamepack_matches[0],
            **resolved,
            "asset_records": asset_records,
            "artifact_root": project_root,
            "qa_reviews": qa_reviews,
            "release_authority": release_authority,
        }
    except generic_asset_fixture_authority.RepositoryFixtureAssetAuthorityError as exc:
        raise generic_assetpack.GenericAssetpackError(
            exc.reason_code,
            exc.detail,
        ) from exc
    except generic_assetpack.GenericAssetpackError:
        raise
    except (GamepackError, OSError, TypeError, ValueError) as exc:
        raise generic_assetpack.GenericAssetpackError(
            "assetpack_source_resolve_failed",
            str(exc),
        ) from exc


def _consume_owned_bundle(bundle: Any, body: Callable[[Any], str]) -> str:
    primary_error: BaseException | None = None
    message: str | None = None
    try:
        message = body(bundle)
    except BaseException as exc:
        primary_error = exc

    cleanup_error: BaseException | None = None
    try:
        bundle.close()
    except BaseException as exc:
        cleanup_error = exc

    if primary_error is not None:
        if cleanup_error is not None:
            combined = _CliCleanupError(f"bundle cleanup failed: {cleanup_error}")
            primary_error.add_note(str(combined))
            raise primary_error from combined
        raise primary_error
    if cleanup_error is not None:
        raise cleanup_error
    assert message is not None
    return message


def _cli_error_detail(error: BaseException) -> str:
    detail = str(error)
    if isinstance(error.__cause__, _CliCleanupError):
        detail += f"; {error.__cause__}"
    return detail


_RECONCILE_CREATION_DESCRIPTION = (
    "Validate and reconcile changed generic creation inputs through the canonical "
    "workflow transition. Run this before status, reopen, or completion after any "
    "upstream identity hash changes. Output and errors are deterministic JSON."
)


class _ReconcileCreationArgumentError(ValueError):
    """Raised instead of exiting for reconcile-creation argument failures."""


class _ReconcileCreationArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _ReconcileCreationArgumentError(message)


def _configure_reconcile_creation_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_root", type=Path)
    parser.add_argument("--expected-status-hash", required=True)
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        type=Path,
        help="current validated generic artifact; repeat to supply the exact registry",
    )


def _parse_cli_arguments() -> argparse.Namespace | None:
    raw_arguments = sys.argv[1:]
    if not raw_arguments or raw_arguments[0] != "reconcile-creation":
        return build_parser().parse_args()
    parser = _ReconcileCreationArgumentParser(
        prog="worldforge reconcile-creation",
        description=_RECONCILE_CREATION_DESCRIPTION,
    )
    _configure_reconcile_creation_parser(parser)
    try:
        arguments = parser.parse_args(raw_arguments[1:])
    except _ReconcileCreationArgumentError as exc:
        print(
            json.dumps(
                {
                    "detail": str(exc),
                    "reason_code": "creation_workflow_cli_arguments_invalid",
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return None
    arguments.command = "reconcile-creation"
    return arguments


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline deterministic multi-genre creation forge with a retained legacy RPG lane"
        ),
        epilog=(
            "Generic creation lane: new-creation, reconcile-creation, phase workflow, "
            "compile-game, generic "
            "assets/runtime/readiness/materialization. Legacy RPG lane: worldpack, "
            "isoworld, retained world, M5 asset, bundle, and pyray commands."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    new_world = commands.add_parser("new-world", help="create a minimal independent world project")
    new_world.add_argument("target", type=Path)
    new_world.add_argument("--id", dest="world_id", required=True)
    new_world.add_argument("--title", required=True)
    new_world.add_argument("--language", default="es")
    new_world.add_argument("--version", default="0.1.0")
    new_world.add_argument("--actor-id")
    new_world.add_argument("--actor-name")

    new_creation = commands.add_parser(
        "new-creation",
        help="scaffold a kind-aware generic creation project",
        description=(
            "A neutral authoring library is the default and is not an executable game. "
            "Explicit game creation "
            "requires independent gameplay, world, narrative, presentation, and "
            "runtime-support facets; no runtime adapter is inferred from genre."
        ),
    )
    new_creation.add_argument("target", type=Path)
    new_creation.add_argument("--id", dest="project_id", required=True)
    new_creation.add_argument("--title", required=True)
    new_creation.add_argument("--language", default="en")
    new_creation.add_argument("--version", default="0.1.0")
    new_creation.add_argument(
        "--kind",
        dest="project_kind",
        choices=CREATION_PROJECT_KINDS,
        default="universe_library",
        help="project kind; defaults to the backward-compatible universe_library scaffold",
    )
    new_creation.add_argument(
        "--gameplay-family",
        choices=GAMEPLAY_FAMILIES,
        help="required for game projects; controlled primary gameplay family",
    )
    new_creation.add_argument(
        "--core-verb",
        dest="initial_core_verb",
        help="required for games; portable ID for the initial reviewed core verb",
    )
    new_creation.add_argument(
        "--core-loop",
        dest="initial_core_loop",
        help="required for games; initial reviewed core-loop statement",
    )
    new_creation.add_argument(
        "--world-presence",
        choices=WORLD_PRESENCES,
        help="required for games; none, abstract, symbolic, or diegetic",
    )
    new_creation.add_argument(
        "--narrative-requirement",
        choices=NARRATIVE_REQUIREMENTS,
        help="required for games; none is a complete choice and creates no narrative module",
    )
    new_creation.add_argument(
        "--narrative-authorship",
        choices=NARRATIVE_AUTHORSHIP_MODES,
        help="required and non-none when narrative is optional or required",
    )
    new_creation.add_argument(
        "--narrative-topology",
        choices=NARRATIVE_TOPOLOGIES,
        help="required and non-none when narrative is optional or required",
    )
    new_creation.add_argument(
        "--presentation-mode",
        choices=PRESENTATION_MODES,
        help="required for games; presentation is independent from gameplay family",
    )
    new_creation.add_argument(
        "--runtime-support-intent",
        choices=RUNTIME_SUPPORT_INTENTS,
        help=(
            "required for games; authoring_only requests no platform evaluation, while "
            "compatibility_assessment records targets but still selects no adapter"
        ),
    )
    new_creation.add_argument(
        "--asset-content-mode",
        choices=CREATION_CONTENT_MODES,
        help=(
            "game-only production.content_modes.assets choice; defaults to authored and "
            "can be not_applicable for an authoring-only no-asset scaffold"
        ),
    )
    new_creation.add_argument("--json", action="store_true")

    world_status = commands.add_parser(
        "world-status",
        help="inspect a canonical v2/v3 world-authoring project",
    )
    world_status.add_argument("project_root", type=Path)

    upgrade_world = commands.add_parser(
        "upgrade-world",
        help="explicitly migrate a legacy v1 world project to v2",
    )
    upgrade_world.add_argument("project_root", type=Path)
    upgrade_world.add_argument("--version", required=True)
    upgrade_world.add_argument("--reason", required=True)
    upgrade_world.add_argument("--approved-by", required=True)

    clone_world = commands.add_parser(
        "clone-world",
        help="derive a new independent world project from canonical source",
    )
    clone_world.add_argument("source_root", type=Path)
    clone_world.add_argument("target_root", type=Path)
    clone_world.add_argument("--id", dest="world_id", required=True)
    clone_world.add_argument("--title", required=True)
    clone_world.add_argument("--version", default="0.1.0")

    bump_world = commands.add_parser(
        "bump-world-version",
        help="apply an optimistic-lock stable SemVer bump to a world",
    )
    bump_world.add_argument("project_root", type=Path)
    bump_world.add_argument("--expected-version", required=True)
    bump_world.add_argument("--part", choices=("major", "minor", "patch"), required=True)
    bump_world.add_argument("--reason", required=True)
    bump_world.add_argument("--approved-by", required=True)

    migrate_world = commands.add_parser(
        "migrate-world-project",
        help="explicitly migrate a retained world project from v2 to v3",
    )
    migrate_world.add_argument("project_root", type=Path)
    migrate_world.add_argument("--expected-source-hash", required=True)
    migrate_world.add_argument("--mode", choices=("dry-run", "apply"), required=True)

    phase_status = commands.add_parser("phase-status", help="show the active creation phase")
    phase_status.add_argument("project_root", type=Path)
    phase_status.add_argument("--json", action="store_true")

    reconcile_creation = commands.add_parser(
        "reconcile-creation",
        help="reconcile changed generic creation inputs through a status-hash CAS",
        description=_RECONCILE_CREATION_DESCRIPTION,
    )
    _configure_reconcile_creation_parser(reconcile_creation)

    complete = commands.add_parser(
        "complete-phase",
        help="validate a phase report and advance sequentially",
    )
    complete.add_argument("project_root", type=Path)
    complete.add_argument("--report", type=Path, required=True)
    complete.add_argument("--expected-status-hash")
    complete.add_argument(
        "--artifact",
        action="append",
        default=[],
        type=Path,
        help="validated generic artifact used by report evidence; repeat as needed",
    )
    complete.add_argument("--json", action="store_true")

    reopen = commands.add_parser(
        "reopen-phase",
        help="reopen a completed phase and invalidate dependent work",
    )
    reopen.add_argument("project_root", type=Path)
    reopen.add_argument("--phase", required=True)
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--approved-by", required=True)
    reopen.add_argument("--expected-status-hash")
    reopen.add_argument("--json", action="store_true")

    claims = commands.add_parser(
        "validate-claims",
        help="detect invalid claims and overlapping agent-owned paths",
    )
    claims.add_argument("project_root", type=Path)

    init_assets = commands.add_parser(
        "init-assets",
        help="initialize asset production bound to a worldpack hash",
    )
    init_assets.add_argument("worldpack", type=Path)
    init_assets.add_argument("--output", type=Path, required=True)
    init_assets.add_argument("--target-id", default="primary")
    init_assets.add_argument("--target-dimension", choices=("2d", "2_5d", "3d"))
    init_assets.add_argument(
        "--enable-modly",
        action="store_true",
        help="explicitly enable the reviewed local Modly route (disabled by default)",
    )

    validate_bibles = commands.add_parser(
        "validate-asset-bibles",
        help="validate approved visual/audio direction against one target",
    )
    validate_bibles.add_argument("--target", type=Path, required=True)
    validate_bibles.add_argument("--visual", type=Path, required=True)
    validate_bibles.add_argument("--audio", type=Path, required=True)

    derive_inventory = commands.add_parser(
        "derive-asset-inventory",
        help="derive a deterministic target-specific inventory from locked canon",
    )
    derive_inventory.add_argument("worldpack", type=Path)
    derive_inventory.add_argument("--target", type=Path, required=True)
    derive_inventory.add_argument("--visual-bible", type=Path, required=True)
    derive_inventory.add_argument("--audio-bible", type=Path, required=True)
    derive_inventory.add_argument("--output", type=Path, required=True)

    bind_plan = commands.add_parser(
        "bind-asset-plan",
        help="bind approved bibles, derived inventory, and exact specs to manifest v3",
    )
    bind_plan.add_argument("manifest", type=Path)
    bind_plan.add_argument("--visual-bible", type=Path, required=True)
    bind_plan.add_argument("--audio-bible", type=Path, required=True)
    bind_plan.add_argument("--inventory", type=Path, required=True)
    bind_plan.add_argument("--expected-hash", required=True)

    finalize_assets = commands.add_parser(
        "finalize-asset-release",
        help="seal a built renderpack or assetpack into manifest v3 by exact hash",
    )
    finalize_assets.add_argument("manifest", type=Path)
    finalize_assets.add_argument("--deliverable", type=Path, required=True)
    finalize_assets.add_argument("--worldpack", type=Path, required=True)
    finalize_assets.add_argument("--expected-hash", required=True)

    production_request = commands.add_parser(
        "create-production-request",
        help="emit a hash-bound external asset-production request without calling a provider",
    )
    production_request.add_argument("asset_root", type=Path)
    production_request.add_argument("specification_file")
    production_request.add_argument("--output", type=Path, required=True)
    production_request.add_argument("--id", dest="request_id", required=True)
    production_request.add_argument("--route", choices=("openai", "modly"), required=True)
    production_request.add_argument(
        "--executor",
        choices=("openai_image", "blender_mcp", "modly_cli_mcp", "human", "procedural"),
        required=True,
    )
    production_request.add_argument("--operation", required=True)
    production_request.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="ROLE=FILE",
    )
    production_request.add_argument("--parameters", type=Path)
    production_request.add_argument(
        "--expected-output",
        action="append",
        default=[],
        metavar="ROLE=MEDIA_TYPE",
        help="override final spec outputs for one intermediate production operation",
    )
    production_request.add_argument("--parent-receipt-hash", action="append", default=[])
    production_request.add_argument("--reviewed-script")

    production_receipt = commands.add_parser(
        "validate-production-receipt",
        help="validate a sanitized OpenAI, Blender MCP, or Modly CLI MCP receipt",
    )
    production_receipt.add_argument("receipt", type=Path)
    production_receipt.add_argument("--asset-root", type=Path, required=True)

    process_asset = commands.add_parser(
        "process-asset",
        help="execute one finite deterministic asset-processing recipe",
    )
    process_asset.add_argument("recipe", type=Path)
    process_asset.add_argument("--asset-root", type=Path, required=True)
    process_asset.add_argument("--output-directory", type=Path, required=True)

    verify_processing = commands.add_parser(
        "verify-processing",
        help="re-verify a deterministic processing receipt and output bytes",
    )
    verify_processing.add_argument("receipt", type=Path)
    verify_processing.add_argument(
        "--asset-root",
        type=Path,
        help="authoritative asset root required by processing receipt v2",
    )

    validate_assets = commands.add_parser(
        "validate-assets",
        help="validate asset provenance, licenses, and processed files",
    )
    validate_assets.add_argument("manifest", type=Path)
    validate_assets.add_argument("--profile", choices=("draft", "release"), default="draft")
    validate_assets.add_argument("--worldpack", type=Path)

    validate_generic_assets = commands.add_parser(
        "validate-generic-asset-contract",
        help="strictly validate one versioned World Forge generic asset contract",
    )
    validate_generic_assets.add_argument("contract", type=Path)

    renderpack = commands.add_parser(
        "build-renderpack",
        help="compile approved processed assets into a runtime-only renderpack",
    )
    renderpack.add_argument("manifest", type=Path)
    renderpack.add_argument("--worldpack", type=Path, required=True)
    renderpack.add_argument("--output", type=Path, required=True)

    assetpack = commands.add_parser(
        "build-assetpack",
        help="compile processed 3d assets into a provider-neutral GLB handoff",
    )
    assetpack.add_argument("manifest", type=Path)
    assetpack.add_argument("--worldpack", type=Path, required=True)
    assetpack.add_argument("--output", type=Path, required=True)

    verify_assets_3d = commands.add_parser(
        "verify-assetpack",
        help="verify a neutral 3d assetpack and every contained file",
    )
    verify_assets_3d.add_argument("assetpack", type=Path)
    verify_assets_3d.add_argument("--worldpack", type=Path)

    seal_generic_assets = commands.add_parser(
        "seal-generic-assetpack",
        help="seal one release-ready generic asset manifest into a runtime-only directory",
    )
    seal_generic_assets.add_argument("manifest", type=Path)
    seal_generic_assets.add_argument("--output", type=Path, required=True)

    verify_generic_assets = commands.add_parser(
        "verify-generic-assetpack",
        help="integrally verify one sealed generic assetpack directory",
    )
    verify_generic_assets.add_argument("assetpack", type=Path)
    verify_generic_assets.add_argument("--expected-hash")

    recover_generic_assets = commands.add_parser(
        "recover-generic-assetpack",
        help="recover one exact journaled generic assetpack publication",
    )
    recover_generic_assets.add_argument("assetpack", type=Path)

    rollback_generic_assets = commands.add_parser(
        "rollback-generic-assetpack",
        help="roll back only an identity-bound uncommitted generic assetpack stage",
    )
    rollback_generic_assets.add_argument("assetpack", type=Path)

    validate = commands.add_parser("validate", help="validate source data and references")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--profile", choices=("draft", "release"), default="release")

    compile_cmd = commands.add_parser("compile", help="compile a static worldpack")
    compile_cmd.add_argument("manifest", type=Path)
    compile_cmd.add_argument("--output", type=Path, required=True)

    compile_game = commands.add_parser(
        "compile-game",
        help="compile a generic game project without certifying runtime support",
        description=(
            "Compilation does not certify runtime support, assets, packaging, or release. "
            "It emits one immutable gamepack for a supported generic source shape."
        ),
    )
    compile_game.add_argument("project", type=Path)
    compile_game.add_argument("--output", type=Path, required=True)
    compile_game.add_argument("--ledger-output", type=Path)

    analyze = commands.add_parser(
        "analyze-narrative",
        help="report unreachable narrative content and possible softlocks",
    )
    analyze.add_argument("manifest", type=Path)
    analyze.add_argument("--output", type=Path)
    analyze.add_argument("--fail-on", choices=("error", "warning", "never"), default="error")

    analyze_game = commands.add_parser(
        "analyze-game",
        help="run the compiler-selected bounded analysis for an immutable gamepack",
    )
    analyze_game.add_argument("gamepack", type=Path)
    analyze_game.add_argument("--output", type=Path)

    inspect_game_runtime = commands.add_parser(
        "inspect-game-runtime",
        help="inspect exact generic runtime compatibility without certifying release",
        description=(
            "Inspect exact generic runtime compatibility and missing capability evidence; "
            "this does not certify release."
        ),
    )
    inspect_game_runtime.add_argument("gamepack", type=Path)
    inspect_game_runtime.add_argument("inventory", type=Path)
    inspect_game_runtime.add_argument("assetpack", type=Path)
    inspect_game_runtime.add_argument("--registry", type=Path, required=True)
    inspect_game_runtime.add_argument("--snapshot", type=Path, required=True)
    inspect_game_runtime.add_argument(
        "--evidence",
        type=Path,
        action="append",
        default=[],
    )

    build_game_runtime_bundle = commands.add_parser(
        "build-game-runtime-bundle",
        help="build an immutable runtime-only pre-execution generic game bundle",
    )
    build_game_runtime_bundle.add_argument("gamepack", type=Path)
    build_game_runtime_bundle.add_argument("inventory", type=Path)
    build_game_runtime_bundle.add_argument("assetpack", type=Path)
    build_game_runtime_bundle.add_argument("--snapshot", type=Path, required=True)
    build_game_runtime_bundle.add_argument("--registry", type=Path, required=True)
    build_game_runtime_bundle.add_argument("--composition", type=Path, required=True)
    build_game_runtime_bundle.add_argument("--support-report", type=Path, required=True)
    build_game_runtime_bundle.add_argument("--output", type=Path, required=True)

    verify_game_runtime_bundle = commands.add_parser(
        "verify-game-runtime-bundle",
        help="integrally verify an immutable generic pre-execution runtime bundle",
    )
    verify_game_runtime_bundle.add_argument("bundle", type=Path)
    verify_game_runtime_bundle.add_argument("--expected-hash")

    recover_game_runtime_bundle = commands.add_parser(
        "recover-game-runtime-bundle",
        help="recover one exact journaled generic runtime bundle publication",
    )
    recover_game_runtime_bundle.add_argument("bundle", type=Path)

    rollback_game_runtime_bundle = commands.add_parser(
        "rollback-game-runtime-bundle",
        help="roll back only an identity-bound uncommitted runtime bundle stage",
    )
    rollback_game_runtime_bundle.add_argument("bundle", type=Path)

    inspect_runtime_implementation = commands.add_parser(
        "inspect-runtime-implementation",
        help="inspect one closed executable runtime implementation identity",
    )
    inspect_runtime_implementation.add_argument("implementation", type=Path)

    inspect_runtime_platform_lock = commands.add_parser(
        "inspect-runtime-platform-lock",
        help="inspect one exact audited raylib platform lock",
    )
    inspect_runtime_platform_lock.add_argument("platform_lock", type=Path)

    build_game_materialization_bundle = commands.add_parser(
        "build-game-materialization-bundle",
        help="wrap one runtime bundle in a blocked executable-materialization envelope",
    )
    build_game_materialization_bundle.add_argument("runtime_bundle", type=Path)
    build_game_materialization_bundle.add_argument("implementation", type=Path)
    build_game_materialization_bundle.add_argument(
        "--platform-lock",
        type=Path,
        action="append",
        required=True,
    )
    build_game_materialization_bundle.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    verify_game_materialization_bundle = commands.add_parser(
        "verify-game-materialization-bundle",
        help="integrally verify a contract-only executable-materialization envelope",
    )
    verify_game_materialization_bundle.add_argument("bundle", type=Path)
    verify_game_materialization_bundle.add_argument("--expected-hash")

    materialize_game = commands.add_parser(
        "materialize-game",
        help="transactionally materialize one verified generic standalone game",
    )
    materialize_game.add_argument("materialization_bundle", type=Path)
    materialize_game.add_argument("target", type=Path)
    materialize_game.add_argument("--expected-hash")

    recover_game_materialization = commands.add_parser(
        "recover-game-materialization",
        help="recover one identity-bound interrupted standalone game publication",
    )
    recover_game_materialization.add_argument("target", type=Path)

    rollback_game_materialization = commands.add_parser(
        "rollback-game-materialization",
        help="remove only an identity-bound unpublished standalone game stage",
    )
    rollback_game_materialization.add_argument("target", type=Path)

    package_game = commands.add_parser(
        "package-game",
        help="publish one deterministic generic standalone game archive",
    )
    package_game.add_argument("game", type=Path)
    package_game.add_argument("output", type=Path)

    verify_game_package = commands.add_parser(
        "verify-game-package",
        help="integrally verify one deterministic generic standalone game archive",
    )
    verify_game_package.add_argument("package", type=Path)

    extract_game_package = commands.add_parser(
        "extract-game-package",
        help="transactionally extract one verified generic standalone game archive",
    )
    extract_game_package.add_argument("package", type=Path)
    extract_game_package.add_argument("target", type=Path)

    recover_game_package_extraction = commands.add_parser(
        "recover-game-package-extraction",
        help="recover one identity-bound interrupted generic package extraction",
    )
    recover_game_package_extraction.add_argument("target", type=Path)

    rollback_game_package_extraction = commands.add_parser(
        "rollback-game-package-extraction",
        help="remove only an identity-bound unpublished package extraction stage",
    )
    rollback_game_package_extraction.add_argument("target", type=Path)

    verify_game_save = commands.add_parser(
        "verify-game-save",
        help="verify one generic game save against an exact runtime bundle",
    )
    verify_game_save.add_argument("save", type=Path)
    verify_game_save.add_argument("--bundle", type=Path, required=True)

    verify_game_replay = commands.add_parser(
        "verify-game-replay",
        help="verify and re-execute one generic game replay against an exact runtime bundle",
    )
    verify_game_replay.add_argument("replay", type=Path)
    verify_game_replay.add_argument("--bundle", type=Path, required=True)

    verify_persistence_generation = commands.add_parser(
        "verify-persistence-generation",
        help="verify one immutable save/replay generation against an exact runtime bundle",
    )
    verify_persistence_generation.add_argument("generation", type=Path)
    verify_persistence_generation.add_argument("--bundle", type=Path, required=True)

    verify_game_headless = commands.add_parser(
        "verify-game-headless",
        help="execute an exact generic game script and publish external headless evidence",
    )
    verify_game_headless.add_argument("bundle", type=Path)
    verify_game_headless.add_argument("script", type=Path)
    verify_game_headless.add_argument("--output", type=Path, required=True)
    verify_game_headless.add_argument("--expected-bundle-hash")

    verify_game_headless_evidence = commands.add_parser(
        "verify-game-headless-evidence",
        help="integrally re-execute and verify an external generic headless evidence set",
    )
    verify_game_headless_evidence.add_argument("evidence", type=Path)
    verify_game_headless_evidence.add_argument("--bundle", type=Path, required=True)

    import_map = commands.add_parser(
        "import-map",
        help="convert a finite Tiled or embedded LDtk JSON layer to an internal map",
    )
    import_map.add_argument("source", type=Path)
    import_map.add_argument("--format", choices=("auto", "tiled", "ldtk"), default="auto")
    import_map.add_argument("--id", dest="map_id", required=True)
    import_map.add_argument("--display-name", required=True)
    import_map.add_argument("--mapping", type=Path, required=True)
    import_map.add_argument("--layer")
    import_map.add_argument("--level")
    import_map.add_argument("--default-tile")
    import_map.add_argument("--output", type=Path, required=True)

    audit = commands.add_parser("audit-runtime", help="reject AI SDK imports in runtime")
    audit.add_argument("runtime_root", type=Path)
    audit_contracts_cmd = commands.add_parser(
        "audit-contracts",
        help="audit the machine-readable public contract catalog",
    )
    audit_contracts_cmd.add_argument("--source-root", type=Path)
    audit_identities_cmd = commands.add_parser(
        "audit-identities",
        help="audit every legacy product and repository identity reference",
    )
    audit_identities_cmd.add_argument("--source-root", type=Path, default=Path("."))
    audit_identities_cmd.add_argument("--allowlist", type=Path)
    audit_game = commands.add_parser(
        "audit-game",
        help="reject Forge, world-authoring, and AI leakage in a game repository",
    )
    audit_game.add_argument("game_root", type=Path)

    export_bundle = commands.add_parser(
        "export-bundle",
        help="export a deterministic runtime-only world bundle",
    )
    export_bundle.add_argument("worldpack", type=Path)
    export_bundle.add_argument("renderpack", type=Path)
    export_bundle.add_argument("destination", type=Path)
    export_bundle.add_argument("--release-id", required=True)
    export_bundle.add_argument("--licenses", type=Path, required=True)

    verify_bundle = commands.add_parser(
        "verify-bundle",
        help="verify an immutable runtime bundle and every payload hash",
    )
    verify_bundle.add_argument("bundle", type=Path)
    verify_bundle.add_argument("--expected-hash")

    import_bundle = commands.add_parser(
        "import-bundle",
        help="atomically import one verified release into a standalone game",
    )
    import_bundle.add_argument("bundle", type=Path)
    import_bundle.add_argument("game_root", type=Path)
    import_bundle.add_argument("--expected-hash", required=True)

    import_composed = commands.add_parser(
        "import-composed-bundle",
        help="atomically import a composed release using the fixed built-in adapter registry",
    )
    import_composed.add_argument("bundle", type=Path)
    import_composed.add_argument("game_root", type=Path)
    import_composed.add_argument("--expected-hash", required=True)

    check_compatibility = commands.add_parser(
        "check-compatibility",
        help="compare a worldpack with an explicit runtime API/features",
    )
    check_compatibility.add_argument("worldpack", type=Path)
    check_compatibility.add_argument("--runtime-version", default=RUNTIME_API_VERSION)
    check_compatibility.add_argument(
        "--feature",
        action="append",
        dest="features",
        help="runtime feature ID; repeat to define a custom feature set",
    )

    new_game = commands.add_parser(
        "new-game",
        help="materialize a clean standalone pyray/raylib game project",
    )
    new_game.add_argument("target", type=Path)
    new_game.add_argument("--id", dest="game_id", required=True)
    new_game.add_argument("--title", required=True)
    new_game.add_argument("--source-revision")

    update_runtime = commands.add_parser(
        "update-game-runtime",
        help="atomically replace a game's complete vendored runtime snapshot",
    )
    update_runtime.add_argument("game_root", type=Path)
    update_runtime.add_argument("--expected-hash", required=True)
    update_runtime.add_argument("--source-revision")
    return parser


def main() -> int:
    args = _parse_cli_arguments()
    if args is None:
        return 2
    try:
        if args.command == "new-world":
            manifest = create_world_project(
                args.target,
                world_id=args.world_id,
                title=args.title,
                language=args.language,
                actor_id=args.actor_id,
                actor_name=args.actor_name,
                version=args.version,
            )
            print(f"OK manifest={manifest}")
            return 0

        if args.command == "new-creation":
            try:
                project_path = create_creation_project(
                    args.target,
                    project_id=args.project_id,
                    title=args.title,
                    default_locale=args.language,
                    project_version=args.version,
                    project_kind=args.project_kind,
                    gameplay_family=args.gameplay_family,
                    initial_core_verb=args.initial_core_verb,
                    initial_core_loop=args.initial_core_loop,
                    world_presence=args.world_presence,
                    narrative_requirement=args.narrative_requirement,
                    narrative_authorship=args.narrative_authorship,
                    narrative_topology=args.narrative_topology,
                    presentation_mode=args.presentation_mode,
                    runtime_support_intent=args.runtime_support_intent,
                    asset_content_mode=args.asset_content_mode,
                )
            except CreationScaffoldError as exc:
                if exc.reason_code != "creation_scaffold_inputs_invalid":
                    raise
                print(
                    json.dumps(
                        {
                            "detail": exc.detail,
                            "reason_code": exc.reason_code,
                            "status": "error",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )
                return 2
            project = read_creation_object(project_path)
            if args.json:
                print(
                    json.dumps(
                        {
                            "path": str(project_path),
                            "project": project,
                            "route": "generic",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            else:
                print(f"OK project={project_path} route=generic")
            return 0

        if args.command == "world-status":
            inspection = inspect_world_project(args.project_root)
            print(
                f"OK world={inspection.world_id} version={inspection.world_version} "
                f"phase={inspection.current_phase or 'complete'} "
                f"revision={inspection.revision} "
                f"canon_locked={str(inspection.canon_locked).lower()}"
            )
            return 0

        if args.command == "upgrade-world":
            inspection = upgrade_legacy_world_project(
                args.project_root,
                version=args.version,
                reason=args.reason,
                approved_by=args.approved_by,
            )
            print(
                f"OK world={inspection.world_id} version={inspection.world_version} "
                "format_version=2"
            )
            return 0

        if args.command == "clone-world":
            manifest = clone_world_project(
                args.source_root,
                args.target_root,
                world_id=args.world_id,
                title=args.title,
                version=args.version,
            )
            print(f"OK manifest={manifest} world={args.world_id} version={args.version}")
            return 0

        if args.command == "bump-world-version":
            version = bump_world_version(
                args.project_root,
                expected_version=args.expected_version,
                part=args.part,
                reason=args.reason,
                approved_by=args.approved_by,
            )
            print(f"OK world={args.project_root} version={version}")
            return 0

        if args.command == "migrate-world-project":
            result = migrate_world_project(
                args.project_root,
                expected_source_hash=args.expected_source_hash,
                mode=args.mode,
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "phase-status":
            route = route_creation_project(args.project_root)
            if route == "generic":
                status = load_creation_workflow_status(args.project_root)
                if args.json:
                    print(json.dumps(status, ensure_ascii=False, sort_keys=True))
                else:
                    print(
                        f"OK project={status['project']['id']} "
                        f"phase={status['current_phase'] or 'complete'} "
                        f"revision={status['revision']} route=generic"
                    )
            elif args.json:
                print(
                    json.dumps(
                        load_status(args.project_root),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            else:
                print(describe_status(args.project_root))
            return 0

        if args.command == "reconcile-creation":
            try:
                artifacts = tuple(read_creation_object(path) for path in args.artifact)
            except ValueError as exc:
                raise CreationWorkflowError(
                    f"creation reconciliation artifact is invalid: {exc}",
                    reason_code="creation_workflow_artifact_invalid",
                ) from exc
            status = reconcile_creation_workflow(
                args.project_root,
                artifact_registry=artifacts,
                expected_status_hash=args.expected_status_hash,
            )
            print(
                json.dumps(
                    {
                        "changed": status["content_hash"] != args.expected_status_hash,
                        "route": "generic",
                        "workflow_status": status,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "complete-phase":
            route = route_creation_project(args.project_root)
            if route == "generic":
                if args.expected_status_hash is None:
                    raise CreationWorkflowError(
                        "--expected-status-hash is required for generic creation projects"
                    )
                artifacts = tuple(read_creation_object(path) for path in args.artifact)
                status = complete_creation_phase(
                    args.project_root,
                    args.report,
                    expected_status_hash=args.expected_status_hash,
                    artifact_registry=artifacts,
                )
            else:
                if args.artifact or args.expected_status_hash is not None:
                    raise CreationWorkflowError(
                        "--artifact and --expected-status-hash are only valid for generic "
                        "creation projects"
                    )
                status = complete_phase(args.project_root, args.report)
            if args.json:
                print(json.dumps(status, ensure_ascii=False, sort_keys=True))
                return 0
            print(
                f"OK completed={status['completed_phases'][-1]} "
                f"next={status['current_phase'] or 'complete'} revision={status['revision']}"
            )
            return 0

        if args.command == "reopen-phase":
            route = route_creation_project(args.project_root)
            if route == "generic":
                if args.expected_status_hash is None:
                    raise CreationWorkflowError(
                        "--expected-status-hash is required for generic creation projects"
                    )
                status = reopen_creation_phase(
                    args.project_root,
                    args.phase,
                    reason=args.reason,
                    approved_by=args.approved_by,
                    expected_status_hash=args.expected_status_hash,
                )
            else:
                if args.expected_status_hash is not None:
                    raise CreationWorkflowError(
                        "--expected-status-hash is only valid for generic creation projects"
                    )
                status = reopen_phase(
                    args.project_root,
                    args.phase,
                    reason=args.reason,
                    approved_by=args.approved_by,
                )
            if args.json:
                print(json.dumps(status, ensure_ascii=False, sort_keys=True))
                return 0
            if route == "generic":
                print(
                    f"OK reopened={status['current_phase']} "
                    f"revision={status['revision']} route=generic"
                )
            else:
                print(
                    f"OK reopened={status['current_phase']} revision={status['revision']} "
                    f"canon_locked={str(status['canon_locked']).lower()}"
                )
            return 0

        if args.command == "validate-claims":
            issues = validate_claims(args.project_root)
            if issues:
                for issue in issues:
                    print(f"ERROR {issue}")
                return 1
            print(f"OK claims={args.project_root}")
            return 0

        if args.command == "init-assets":
            manifest = init_asset_manifest(
                args.worldpack,
                args.output,
                target_dimension=args.target_dimension,
                target_id=args.target_id,
                enable_modly=args.enable_modly,
            )
            print(
                f"OK output={args.output} world={manifest['world_id']} "
                f"hash={manifest['world_content_hash']}"
            )
            return 0

        if args.command == "validate-asset-bibles":
            issues = validate_asset_bibles(args.visual, args.audio, args.target)
            if issues:
                for issue in issues:
                    print(f"ERROR {issue}")
                return 1
            print(f"OK target={args.target} visual={args.visual} audio={args.audio}")
            return 0

        if args.command == "derive-asset-inventory":
            inventory = derive_asset_inventory(
                args.worldpack,
                args.target,
                args.visual_bible,
                args.audio_bible,
                args.output,
            )
            required = sum(1 for item in inventory["requirements"] if item["required"])
            print(
                f"OK output={args.output} target={inventory['target_id']} "
                f"requirements={len(inventory['requirements'])} required={required} "
                f"hash={inventory['content_hash']}"
            )
            return 0

        if args.command == "bind-asset-plan":
            manifest = bind_asset_plan(
                args.manifest,
                visual_bible_path=args.visual_bible,
                audio_bible_path=args.audio_bible,
                inventory_path=args.inventory,
                expected_manifest_hash=args.expected_hash,
            )
            print(
                f"OK manifest={args.manifest} assets={len(manifest['assets'])} "
                f"hash={manifest['content_hash']}"
            )
            return 0

        if args.command == "finalize-asset-release":
            manifest = finalize_asset_release(
                args.manifest,
                args.deliverable,
                args.worldpack,
                expected_manifest_hash=args.expected_hash,
            )
            print(
                f"OK manifest={args.manifest} deliverable={manifest['deliverable']['file']} "
                f"hash={manifest['content_hash']}"
            )
            return 0

        if args.command == "create-production-request":
            inputs: list[tuple[str, str]] = []
            for raw_input in args.input:
                if "=" not in raw_input:
                    raise AssetContractError("--input must use ROLE=FILE")
                role, relative = raw_input.split("=", 1)
                if not role or not relative:
                    raise AssetContractError("--input must use non-empty ROLE=FILE")
                inputs.append((role, relative))
            parameters = None if args.parameters is None else read_json_object(args.parameters)
            expected_outputs: list[dict[str, str]] = []
            for raw_output in args.expected_output:
                if "=" not in raw_output:
                    raise AssetContractError("--expected-output must use ROLE=MEDIA_TYPE")
                role, media_type = raw_output.split("=", 1)
                if not role or not media_type:
                    raise AssetContractError("--expected-output must use non-empty ROLE=MEDIA_TYPE")
                expected_outputs.append({"role": role, "media_type": media_type})
            request = create_production_request(
                args.asset_root,
                args.specification_file,
                args.output,
                request_id=args.request_id,
                route=args.route,
                executor=args.executor,
                operation=args.operation,
                inputs=inputs,
                parameters=parameters,
                expected_outputs=expected_outputs or None,
                parent_receipt_hashes=args.parent_receipt_hash,
                reviewed_script_file=args.reviewed_script,
            )
            print(
                f"OK request={args.output} asset={request['asset_id']} "
                f"executor={request['executor']} hash={request['content_hash']}"
            )
            return 0

        if args.command == "validate-production-receipt":
            issues = validate_production_receipt(args.receipt, asset_root=args.asset_root)
            if issues:
                for issue in issues:
                    print(f"ERROR {issue}")
                return 1
            print(f"OK receipt={args.receipt}")
            return 0

        if args.command == "process-asset":
            receipt = process_asset_recipe(
                args.recipe,
                args.output_directory,
                asset_root=args.asset_root,
            )
            print(
                f"OK output={args.output_directory} operation={receipt['operation']} "
                f"hash={receipt['content_hash']}"
            )
            return 0

        if args.command == "verify-processing":
            receipt = verify_processing_receipt(args.receipt, asset_root=args.asset_root)
            print(
                f"OK receipt={args.receipt} operation={receipt['operation']} "
                f"hash={receipt['content_hash']}"
            )
            return 0

        if args.command == "validate-assets":
            issues = validate_asset_manifest(
                args.manifest,
                profile=args.profile,
                worldpack_path=args.worldpack,
            )
            if issues:
                for issue in issues:
                    print(f"ERROR {issue}")
                return 1
            print(f"OK assets={args.manifest} profile={args.profile}")
            return 0

        if args.command == "validate-generic-asset-contract":
            document = _validate_generic_asset_contract(args.contract)
            print(
                f"OK format={document['format']} version={document['format_version']} "
                f"hash={document['content_hash']}"
            )
            return 0

        if args.command == "build-renderpack":
            payload = build_renderpack(args.manifest, args.worldpack, args.output)
            print(
                f"OK output={args.output} world={payload['world_id']} "
                f"assets={len(payload['assets'])} hash={payload['content_hash']}"
            )
            return 0

        if args.command == "build-assetpack":
            payload = build_assetpack(args.manifest, args.worldpack, args.output)
            print(
                f"OK output={args.output} world={payload['world_id']} "
                f"assets={len(payload['assets'])} hash={payload['content_hash']}"
            )
            return 0

        if args.command == "verify-assetpack":
            payload = verify_assetpack(args.assetpack, args.worldpack)
            print(
                f"OK assetpack={args.assetpack} world={payload['world_id']} "
                f"assets={len(payload['assets'])} hash={payload['content_hash']}"
            )
            return 0

        if args.command == "seal-generic-assetpack":
            source = _resolve_generic_assetpack_cli_source(args.manifest)
            verified = generic_assetpack.seal_generic_assetpack(
                args.output,
                **source,
            )
            try:
                payload = {**verified.evidence, "path": str(args.output)}
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "verify-generic-assetpack":
            verified = generic_assetpack.verify_generic_assetpack(
                args.assetpack,
                expected_content_hash=args.expected_hash,
            )
            try:
                payload = {**verified.evidence, "path": str(args.assetpack)}
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "recover-generic-assetpack":
            verified = generic_assetpack.recover_generic_assetpack(args.assetpack)
            if verified is None:
                payload = {
                    "path": str(args.assetpack),
                    "status": "no_operation",
                }
            else:
                try:
                    payload = {
                        **verified.evidence,
                        "path": str(args.assetpack),
                    }
                finally:
                    verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "rollback-generic-assetpack":
            payload = {
                **generic_assetpack.rollback_generic_assetpack(args.assetpack),
                "path": str(args.assetpack),
            }
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "validate":
            project = load_source_project(args.manifest)
            issues = validate_project(project, profile=args.profile)
            if issues:
                for issue in issues:
                    print(f"ERROR {issue}")
                return 1
            total = sum(len(items) for items in project.collections.values())
            print(f"OK world={project.world['id']} objects={total} profile={args.profile}")
            return 0

        if args.command == "compile":
            payload = compile_project(args.manifest, args.output)
            print(
                f"OK output={args.output} hash={payload['content_hash']} "
                f"world={payload['world']['id']}"
            )
            return 0

        if args.command == "compile-game":
            project = load_game_source_project(args.project)
            payload = build_gamepack(project)
            ledger_payload = (
                None if args.ledger_output is None else build_authoring_capability_ledger(payload)
            )
            output = preflight_game_artifact_output(args.output)
            ledger_output = None
            if args.ledger_output is not None:
                ledger_output = preflight_game_artifact_output(args.ledger_output)
                if os.path.normcase(os.fspath(output)) == os.path.normcase(
                    os.fspath(ledger_output)
                ):
                    raise GamepackError(
                        "output_collision",
                        "gamepack and capability-ledger outputs must differ",
                    )
            published = publish_gamepack(output, payload)
            try:
                if ledger_output is not None:
                    assert ledger_payload is not None
                    publish_capability_ledger(ledger_output, ledger_payload)
            except BaseException as exc:
                assert ledger_output is not None
                raise GamepackPartialPublicationError(
                    published=published,
                    failed_output=ledger_output,
                    cause=exc,
                ) from exc
            summary = {
                "adapter": "declared"
                if payload["runtime_requirements"]["requested_adapter"] is not None
                else "absent",
                "assets": "unplanned",
                "compilation": "compiled",
                "gamepack": {
                    "format": payload["format"],
                    "version": payload["format_version"],
                    "id": payload["game"]["id"],
                    "hash": payload["content_hash"],
                },
                "ledger_hash": (None if ledger_payload is None else ledger_payload["content_hash"]),
                "reason_codes": ["adapter_not_evaluated", "assets_unplanned"],
                "release": "blocked",
            }
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "analyze-narrative":
            project = load_source_project(args.manifest)
            validation_issues = validate_project(project)
            if validation_issues:
                for issue in validation_issues:
                    print(f"ERROR {issue}")
                return 1
            report = analyze_project(project)
            if args.output is not None:
                write_analysis(args.output, report)
                print(
                    f"OK output={args.output} errors={report['summary']['error']} "
                    f"warnings={report['summary']['warning']}"
                )
            else:
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            if args.fail_on == "error" and report["summary"]["error"]:
                return 1
            if args.fail_on == "warning" and (
                report["summary"]["error"] or report["summary"]["warning"]
            ):
                return 1
            return 0

        if args.command == "analyze-game":
            gamepack = load_gamepack(args.gamepack)
            report = analyze_gamepack(gamepack)
            if args.output is None:
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                published = publish_game_analysis(
                    args.output,
                    report,
                    gamepack=gamepack,
                )
                print(
                    json.dumps(
                        {
                            "content_hash": published.content_hash,
                            "gamepack_hash": gamepack["content_hash"],
                            "output": os.fspath(published.path),
                            "status": report["status"],
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
            return 0 if report["status"] == "passed" else 1

        if args.command == "inspect-game-runtime":
            gamepack = load_gamepack(args.gamepack)
            inventory = generic_assets.validate_asset_inventory_document(
                read_creation_object(args.inventory)
            )
            snapshot = generic_runtime.load_runtime_snapshot(args.snapshot)
            registry = generic_runtime.load_runtime_adapter_registry(
                args.registry,
                snapshot=snapshot,
            )
            structural_evidence = [
                generic_runtime.load_runtime_evidence(path) for path in args.evidence
            ]
            result = generic_runtime.resolve_runtime_compatibility(
                gamepack,
                inventory,
                args.assetpack,
                registry=registry,
                snapshot=snapshot,
                evidence=[],
            )
            composition = result["composition"]
            report = result["report"]
            for evidence in structural_evidence:
                generic_runtime.validate_runtime_evidence_document(
                    evidence,
                    composition=composition,
                )
            reason_codes = list(report["reason_codes"])
            if structural_evidence:
                reason_codes.append("runtime_evidence_authority_missing")
            reason_codes = sorted(set(reason_codes), key=lambda item: item.encode("utf-8"))
            print(
                json.dumps(
                    {
                        "adapter": report["dimensions"]["adapter"],
                        "compatibility_status": report["compatibility_status"],
                        "composition_hash": composition["content_hash"],
                        "reason_codes": reason_codes,
                        "report_hash": report["content_hash"],
                        "status": (
                            "ready" if report["dimensions"]["release"] == "ready" else "blocked"
                        ),
                        "supported": report["supported"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "build-game-runtime-bundle":
            verified = game_runtime_bundle.build_game_runtime_bundle(
                args.output,
                gamepack_path=args.gamepack,
                inventory_path=args.inventory,
                assetpack_root=args.assetpack,
                snapshot_path=args.snapshot,
                registry_path=args.registry,
                composition_path=args.composition,
                support_report_path=args.support_report,
            )
            try:
                payload = {**verified.evidence, "path": str(args.output)}
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "verify-game-runtime-bundle":
            verified = game_runtime_bundle.verify_game_runtime_bundle(
                args.bundle,
                expected_content_hash=args.expected_hash,
            )
            try:
                payload = {
                    **verified.evidence,
                    "path": str(args.bundle),
                }
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "recover-game-runtime-bundle":
            verified = game_runtime_bundle.recover_game_runtime_bundle(args.bundle)
            if verified is None:
                payload = {
                    "path": str(args.bundle),
                    "status": "no_operation",
                }
            else:
                try:
                    payload = {
                        **verified.evidence,
                        "path": str(args.bundle),
                        "status": "verified",
                    }
                finally:
                    verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "rollback-game-runtime-bundle":
            payload = {
                **game_runtime_bundle.rollback_game_runtime_bundle(args.bundle),
                "path": str(args.bundle),
            }
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "inspect-runtime-implementation":
            implementation = runtime_implementation.load_runtime_implementation(args.implementation)
            print(
                json.dumps(
                    {
                        "adapter_id": implementation["adapter"]["adapter_id"],
                        "content_hash": implementation["content_hash"],
                        "implementation_id": implementation["implementation_id"],
                        "materialization_ready": False,
                        "platform_lock_count": len(implementation["platform_locks"]),
                        "status": "declared",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "inspect-runtime-platform-lock":
            platform_lock = runtime_platform_lock.load_runtime_platform_lock(args.platform_lock)
            print(
                json.dumps(
                    {
                        "abi": platform_lock["python"]["abi"],
                        "content_hash": platform_lock["content_hash"],
                        "lock_id": platform_lock["lock_id"],
                        "os": platform_lock["platform"]["os"],
                        "python_minor": platform_lock["python"]["minor"],
                        "status": "audited",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "build-game-materialization-bundle":
            implementation = runtime_implementation.load_runtime_implementation(args.implementation)
            platform_locks = [
                runtime_platform_lock.load_runtime_platform_lock(path)
                for path in args.platform_lock
            ]
            verified = game_materialization_bundle.build_game_materialization_bundle(
                args.output,
                runtime_bundle_root=args.runtime_bundle,
                runtime_implementation=implementation,
                platform_locks=platform_locks,
            )
            try:
                payload = {**verified.evidence, "path": str(args.output)}
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "verify-game-materialization-bundle":
            verified = game_materialization_bundle.verify_game_materialization_bundle(
                args.bundle,
                expected_content_hash=args.expected_hash,
            )
            try:
                payload = {**verified.evidence, "path": str(args.bundle)}
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "materialize-game":
            verified = standalone_game.materialize_game(
                args.materialization_bundle,
                args.target,
                expected_content_hash=args.expected_hash,
            )
            try:
                payload = {**verified.evidence, "path": str(args.target)}
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "recover-game-materialization":
            verified = standalone_game.recover_standalone_game(args.target)
            if verified is None:
                payload = {"path": str(args.target), "status": "no_operation"}
            else:
                try:
                    payload = {**verified.evidence, "path": str(args.target)}
                finally:
                    verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "rollback-game-materialization":
            payload = {
                **standalone_game.rollback_standalone_game(args.target),
                "path": str(args.target),
            }
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "package-game":
            verified = game_package.package_game(args.game, args.output)
            manifest = verified.manifest
            payload = {
                "archive_sha256": verified.archive_sha256,
                "content_hash": manifest["content_hash"],
                "package_id": manifest["package_id"],
                "path": str(args.output),
                "standalone_game_hash": manifest["standalone_game"]["content_hash"],
                "status": "packaged",
            }
            verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "verify-game-package":
            verified = game_package.verify_game_package(args.package)
            manifest = verified.manifest
            payload = {
                "archive_sha256": verified.archive_sha256,
                "content_hash": manifest["content_hash"],
                "package_id": manifest["package_id"],
                "path": str(args.package),
                "standalone_game_hash": manifest["standalone_game"]["content_hash"],
                "status": "verified",
            }
            verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "extract-game-package":
            verified = game_package.extract_game_package(args.package, args.target)
            try:
                payload = {**verified.evidence, "path": str(args.target)}
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "recover-game-package-extraction":
            verified = game_package.recover_game_package_extraction(args.target)
            if verified is None:
                payload = {"path": str(args.target), "status": "no_operation"}
            else:
                try:
                    payload = {**verified.evidence, "path": str(args.target)}
                finally:
                    verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "rollback-game-package-extraction":
            payload = {
                **game_package.rollback_game_package_extraction(args.target),
                "path": str(args.target),
            }
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "verify-game-save":
            print(
                json.dumps(
                    game_persistence.verify_game_save(
                        args.save,
                        bundle_root=args.bundle,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "verify-game-replay":
            print(
                json.dumps(
                    game_persistence.verify_game_replay(
                        args.replay,
                        bundle_root=args.bundle,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "verify-persistence-generation":
            print(
                json.dumps(
                    persistence_generation.verify_persistence_generation(
                        args.generation,
                        bundle_root=args.bundle,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "verify-game-headless":
            verified = generic_headless.build_headless_evidence_set(
                args.output,
                bundle_root=args.bundle,
                script_path=args.script,
                expected_bundle_hash=args.expected_bundle_hash,
            )
            try:
                payload = generic_headless.build_headless_authority_result(
                    verified,
                    path=args.output,
                )
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "verify-game-headless-evidence":
            verified = generic_headless.verify_headless_evidence_set(
                args.evidence,
                bundle_root=args.bundle,
            )
            try:
                payload = generic_headless.build_headless_authority_result(
                    verified,
                    path=args.evidence,
                )
            finally:
                verified.close()
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0

        if args.command == "import-map":
            mapping = load_mapping(args.mapping)
            imported = import_map_file(
                args.source,
                source_format=args.format,
                map_id=args.map_id,
                display_name=args.display_name,
                mapping=mapping,
                layer_name=args.layer,
                level_name=args.level,
                default_tile=args.default_tile,
            )
            write_imported_map(args.output, imported)
            print(
                f"OK output={args.output} map={imported['id']} "
                f"size={imported['width']}x{imported['height']}"
            )
            return 0

        if args.command == "audit-runtime":
            findings = audit_runtime(args.runtime_root)
            if findings:
                for finding in findings:
                    print(f"ERROR {finding}")
                return 1
            print(f"OK runtime={args.runtime_root} ai_imports=0")
            return 0

        if args.command == "audit-contracts":
            try:
                result = audit_contracts(args.source_root)
            except ContractCatalogError as exc:
                print(f"ERROR {exc}", file=sys.stderr)
                return 1
            print(
                f"OK contracts={result.contracts} mode={result.mode} catalog={result.catalog_path}"
            )
            return 0

        if args.command == "audit-identities":
            try:
                result = audit_identities(
                    args.source_root,
                    allowlist_path=args.allowlist,
                )
            except IdentityAuditError as exc:
                print(f"ERROR {exc}", file=sys.stderr)
                return 1
            print(
                f"OK entries={result.entries} occurrences={result.occurrences} "
                f"allowlist={result.allowlist_path}"
            )
            return 0

        if args.command == "export-bundle":
            bundle = export_runtime_bundle(
                args.worldpack,
                args.renderpack,
                args.destination,
                release_id=args.release_id,
                licenses_directory=args.licenses,
            )
            message = _consume_owned_bundle(
                bundle,
                lambda owned: (
                    f"OK bundle={owned.root} world={owned.world_id} "
                    f"release={owned.release_id} hash={owned.bundle_hash}"
                ),
            )
            print(message)
            return 0

        if args.command == "verify-bundle":
            bundle = verify_runtime_bundle(
                args.bundle,
                expected_bundle_hash=args.expected_hash,
            )
            message = _consume_owned_bundle(
                bundle,
                lambda owned: (
                    f"OK bundle={owned.root} world={owned.world_id} "
                    f"release={owned.release_id} hash={owned.bundle_hash}"
                ),
            )
            print(message)
            return 0

        if args.command == "import-bundle":
            imported = import_runtime_bundle(
                args.bundle,
                args.game_root,
                expected_bundle_hash=args.expected_hash,
            )
            bundle = verify_runtime_bundle(
                args.bundle,
                expected_bundle_hash=args.expected_hash,
            )
            message = _consume_owned_bundle(
                bundle,
                lambda owned: (
                    f"OK imported={imported} world={owned.world_id} "
                    f"release={owned.release_id} hash={owned.bundle_hash}"
                ),
            )
            print(message)
            return 0

        if args.command == "import-composed-bundle":
            imported = import_composed_bundle(
                args.bundle,
                args.game_root,
                expected_bundle_hash=args.expected_hash,
            )
            print(f"OK imported={imported} hash={args.expected_hash}")
            return 0

        if args.command == "check-compatibility":
            pack = load_worldpack(args.worldpack)
            features = (
                SUPPORTED_RUNTIME_FEATURES if args.features is None else frozenset(args.features)
            )
            report = pack.compatibility_with(args.runtime_version, features)
            print(
                f"{'OK' if report.compatible else 'INCOMPATIBLE'} world={pack.world_id} "
                f"runtime={report.runtime_version} "
                f"api_compatible={str(report.api_compatible).lower()} "
                f"missing_required={','.join(report.missing_required_features) or '-'} "
                f"missing_optional={','.join(report.missing_optional_features) or '-'}"
            )
            return 0 if report.compatible else 1

        if args.command == "new-game":
            game = create_game_project(
                args.target,
                game_id=args.game_id,
                title=args.title,
                source_revision=args.source_revision,
            )
            print(f"OK game={game}")
            return 0

        if args.command == "update-game-runtime":
            manifest = update_game_runtime_snapshot(
                args.game_root,
                expected_content_hash=args.expected_hash,
                source_revision=args.source_revision,
            )
            print(
                f"OK game={args.game_root} runtime={manifest['runtime_version']} "
                f"hash={manifest['content_hash']}"
            )
            return 0

        if args.command == "audit-game":
            if repository_boundary.repository_kind(args.game_root) == "generic_game":
                verified = standalone_game.verify_standalone_game(args.game_root)
                verified.close()
                print(f"OK game={args.game_root} authoring_leaks=0")
                return 0
            findings = audit_game_repository(args.game_root)
            if findings:
                for finding in findings:
                    print(f"ERROR {finding}")
                return 1
            print(f"OK game={args.game_root} authoring_leaks=0")
            return 0
        raise AssertionError(f"unhandled command: {args.command}")
    except SourceProjectError as exc:
        print(f"ERROR {exc}")
        return 1
    except ScaffoldError as exc:
        print(f"ERROR {exc}")
        return 1
    except AssetManifestError as exc:
        print(f"ERROR {exc}")
        return 1
    except WorldProjectMigrationError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except WorkflowError as exc:
        print(f"ERROR {exc}")
        return 1
    except (CreationRouteError, CreationScaffoldError, CreationWorkflowError) as exc:
        if args.command == "reconcile-creation":
            print(
                json.dumps(
                    {
                        "detail": str(exc),
                        "reason_code": getattr(
                            exc,
                            "reason_code",
                            "creation_workflow_reconciliation_failed",
                        ),
                        "status": "error",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        else:
            print(f"ERROR {exc}", file=sys.stderr)
        return 1
    except MapImportError as exc:
        print(f"ERROR {exc}")
        return 1
    except CompilationError as exc:
        for issue in exc.issues:
            print(f"ERROR {issue}")
        return 1
    except RenderPackBuildError as exc:
        print(f"ERROR {exc}")
        return 1
    except GameBoundaryError as exc:
        print(f"ERROR {exc}")
        return 1
    except (BundleError, ComposedGameError, GameScaffoldError, WorldPackError) as exc:
        print(f"ERROR {_cli_error_detail(exc)}")
        return 1
    except GamepackPartialPublicationError as exc:
        print(json.dumps(exc.receipt, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    except GamepackError as exc:
        if args.command == "compile-game":
            print(
                json.dumps(
                    {
                        "detail": exc.detail,
                        "reason_code": exc.reason_code,
                        "status": "error",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        else:
            print(f"ERROR {_cli_error_detail(exc)}", file=sys.stderr)
        return 1
    except GameAnalysisError as exc:
        print(f"ERROR {_cli_error_detail(exc)}", file=sys.stderr)
        return 1
    except generic_assetpack.GenericAssetpackError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except generic_runtime.RuntimeContractError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except game_runtime_bundle.GameRuntimeBundleError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except game_materialization_bundle.GameMaterializationBundleError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except standalone_game.StandaloneGameError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except game_package.WorldForgeGamePackageError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except runtime_implementation.RuntimeImplementationError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except runtime_platform_lock.RuntimePlatformLockError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except generic_headless.GenericHeadlessError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except GameLogicError as exc:
        print(
            json.dumps(
                {
                    "detail": exc.detail,
                    "reason_code": exc.reason_code,
                    "status": "error",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(f"ERROR {_cli_error_detail(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
