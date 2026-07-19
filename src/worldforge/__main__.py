from __future__ import annotations

import argparse
from pathlib import Path

from worldforge.assets import AssetManifestError, init_asset_manifest, validate_asset_manifest
from worldforge.claims import validate_claims
from worldforge.compiler import CompilationError, compile_project
from worldforge.map_import import (
    MapImportError,
    import_map_file,
    load_mapping,
    write_imported_map,
)
from worldforge.project import SourceProjectError, load_source_project
from worldforge.runtime_audit import audit_runtime
from worldforge.scaffold import ScaffoldError, create_world_project
from worldforge.validation import validate_project
from worldforge.workflow import WorkflowError, complete_phase, describe_status, reopen_phase


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline worldpack authoring forge")
    commands = parser.add_subparsers(dest="command", required=True)

    new_world = commands.add_parser("new-world", help="create a minimal independent world project")
    new_world.add_argument("target", type=Path)
    new_world.add_argument("--id", dest="world_id", required=True)
    new_world.add_argument("--title", required=True)
    new_world.add_argument("--language", default="es")
    new_world.add_argument("--actor-id")
    new_world.add_argument("--actor-name")

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

    validate = commands.add_parser("validate", help="validate source data and references")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("--profile", choices=("draft", "release"), default="release")

    compile_cmd = commands.add_parser("compile", help="compile a static worldpack")
    compile_cmd.add_argument("manifest", type=Path)
    compile_cmd.add_argument("--output", type=Path, required=True)

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
            )
            print(f"OK manifest={manifest}")
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

        findings = audit_runtime(args.runtime_root)
        if findings:
            for finding in findings:
                print(f"ERROR {finding}")
            return 1
        print(f"OK runtime={args.runtime_root} ai_imports=0")
        return 0
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


if __name__ == "__main__":
    raise SystemExit(main())
