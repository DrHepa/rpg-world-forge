from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.models import RUNTIME_API_VERSION, SUPPORTED_RUNTIME_FEATURES
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
from worldforge.game_boundary import GameBoundaryError, audit_game_repository
from worldforge.game_scaffold import (
    GameScaffoldError,
    create_game_project,
    update_game_runtime_snapshot,
)
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
from worldforge.workflow import WorkflowError, complete_phase, describe_status, reopen_phase
from worldforge.world_lifecycle import (
    bump_world_version,
    clone_world_project,
    inspect_world_project,
    upgrade_legacy_world_project,
)


class _CliCleanupError(RuntimeError):
    """Carries an owned CLI cleanup failure behind the primary operation error."""


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline worldpack authoring forge")
    commands = parser.add_subparsers(dest="command", required=True)

    new_world = commands.add_parser("new-world", help="create a minimal independent world project")
    new_world.add_argument("target", type=Path)
    new_world.add_argument("--id", dest="world_id", required=True)
    new_world.add_argument("--title", required=True)
    new_world.add_argument("--language", default="es")
    new_world.add_argument("--version", default="0.1.0")
    new_world.add_argument("--actor-id")
    new_world.add_argument("--actor-name")

    world_status = commands.add_parser(
        "world-status",
        help="inspect a canonical v2 world-authoring project",
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

    phase_status = commands.add_parser("phase-status", help="show the active creation phase")
    phase_status.add_argument("project_root", type=Path)

    complete = commands.add_parser(
        "complete-phase",
        help="validate a phase report and advance sequentially",
    )
    complete.add_argument("project_root", type=Path)
    complete.add_argument("--report", type=Path, required=True)

    reopen = commands.add_parser(
        "reopen-phase",
        help="reopen a completed phase and invalidate dependent work",
    )
    reopen.add_argument("project_root", type=Path)
    reopen.add_argument("--phase", required=True)
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--approved-by", required=True)

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

    validate = commands.add_parser("validate", help="validate source data and references")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--profile", choices=("draft", "release"), default="release")

    compile_cmd = commands.add_parser("compile", help="compile a static worldpack")
    compile_cmd.add_argument("manifest", type=Path)
    compile_cmd.add_argument("--output", type=Path, required=True)

    analyze = commands.add_parser(
        "analyze-narrative",
        help="report unreachable narrative content and possible softlocks",
    )
    analyze.add_argument("manifest", type=Path)
    analyze.add_argument("--output", type=Path)
    analyze.add_argument("--fail-on", choices=("error", "warning", "never"), default="error")

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
    args = build_parser().parse_args()
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

        if args.command == "phase-status":
            print(describe_status(args.project_root))
            return 0

        if args.command == "complete-phase":
            status = complete_phase(args.project_root, args.report)
            print(
                f"OK completed={status['completed_phases'][-1]} "
                f"next={status['current_phase'] or 'complete'} revision={status['revision']}"
            )
            return 0

        if args.command == "reopen-phase":
            status = reopen_phase(
                args.project_root,
                args.phase,
                reason=args.reason,
                approved_by=args.approved_by,
            )
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
    except WorkflowError as exc:
        print(f"ERROR {exc}")
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
    except ValueError as exc:
        print(f"ERROR {_cli_error_detail(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
