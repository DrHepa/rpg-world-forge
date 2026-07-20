from __future__ import annotations

import argparse
import json
from pathlib import Path

from isoworld.content.loader import WorldPackError, load_worldpack
from isoworld.content.models import RUNTIME_API_VERSION, SUPPORTED_RUNTIME_FEATURES
from worldforge.assets import AssetManifestError, init_asset_manifest, validate_asset_manifest
from worldforge.bundle import (
    BundleError,
    export_runtime_bundle,
    import_runtime_bundle,
    verify_runtime_bundle,
)
from worldforge.claims import validate_claims
from worldforge.compiler import CompilationError, compile_project
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
            manifest = init_asset_manifest(args.worldpack, args.output)
            print(
                f"OK output={args.output} world={manifest['world_id']} "
                f"hash={manifest['world_content_hash']}"
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

        if args.command == "export-bundle":
            bundle = export_runtime_bundle(
                args.worldpack,
                args.renderpack,
                args.destination,
                release_id=args.release_id,
                licenses_directory=args.licenses,
            )
            print(
                f"OK bundle={bundle.root} world={bundle.world_id} "
                f"release={bundle.release_id} hash={bundle.bundle_hash}"
            )
            return 0

        if args.command == "verify-bundle":
            bundle = verify_runtime_bundle(
                args.bundle,
                expected_bundle_hash=args.expected_hash,
            )
            print(
                f"OK bundle={bundle.root} world={bundle.world_id} "
                f"release={bundle.release_id} hash={bundle.bundle_hash}"
            )
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
            print(
                f"OK imported={imported} world={bundle.world_id} "
                f"release={bundle.release_id} hash={bundle.bundle_hash}"
            )
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
    except (BundleError, GameScaffoldError, WorldPackError) as exc:
        print(f"ERROR {exc}")
        return 1
    except ValueError as exc:
        print(f"ERROR {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
