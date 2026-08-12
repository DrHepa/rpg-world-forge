"""Code-owned, provider-free standalone game shell bytes."""

from __future__ import annotations

import hashlib
import textwrap
from types import MappingProxyType
from typing import Final

from worldforge.integrity import canonical_json_bytes


def _text(value: str) -> bytes:
    return textwrap.dedent(value).lstrip("\n").encode("utf-8")


RUN_GAME = _text(
    r"""
    from __future__ import annotations

    import os
    import stat
    import sys
    from pathlib import Path


    def _root() -> Path:
        script = Path(__file__)
        info = script.lstat()
        if script.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SystemExit("ERROR standalone_launcher_unsafe: run_game.py is unsafe")
        lexical = Path(os.path.abspath(os.fspath(script.parent)))
        physical = script.resolve(strict=True).parent
        if lexical != physical:
            raise SystemExit("ERROR standalone_root_mismatch: lexical and physical roots differ")
        return physical


    ROOT = _root()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "game_data/runtime-bundle/runtime/snapshot-tree"))

    from game.runner import main


    if __name__ == "__main__":
        raise SystemExit(main(ROOT))
    """
)

VERIFY_GAME = _text(
    r"""
    from __future__ import annotations

    import json
    import os
    import stat
    import sys
    from pathlib import Path


    def _root() -> Path:
        script = Path(__file__)
        info = script.lstat()
        if script.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("standalone_verifier_unsafe: verifier is unsafe")
        lexical = Path(os.path.abspath(os.fspath(script.parent.parent)))
        physical = script.resolve(strict=True).parents[1]
        if lexical != physical:
            raise RuntimeError(
                "standalone_root_mismatch: lexical and physical roots differ"
            )
        return physical


    def main() -> int:
        try:
            root = _root()
            sys.dont_write_bytecode = True
            runtime = root / "game_data/runtime-bundle/runtime/snapshot-tree"
            sys.path.insert(0, str(runtime))
            from gamepack_runtime.distribution import verify_standalone_distribution
            from gamepack_raylib_2d.resources import load_runtime_bundle

            report = verify_standalone_distribution(root)
            loaded = load_runtime_bundle(root / "game_data/runtime-bundle")
            if loaded.manifest["content_hash"] != report["runtime_bundle_hash"]:
                raise RuntimeError(
                    "standalone_game_lineage_mismatch: runtime bundle hash differs"
                )
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR {exc}", file=sys.stderr)
            return 1


    if __name__ == "__main__":
        raise SystemExit(main())
    """
)

PACKAGE_GAME = _text(
    r"""
    from __future__ import annotations

    import argparse
    import json
    import os
    import stat
    import sys
    from pathlib import Path


    def _root() -> Path:
        script = Path(__file__)
        info = script.lstat()
        if script.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("standalone_packager_unsafe: packager is unsafe")
        lexical = Path(os.path.abspath(os.fspath(script.parent.parent)))
        physical = script.resolve(strict=True).parents[1]
        if lexical != physical:
            raise RuntimeError(
                "standalone_root_mismatch: lexical and physical roots differ"
            )
        return physical


    def _destination(value: str, root: Path) -> Path:
        destination = Path(os.path.abspath(value))
        parent = destination.parent
        parent_info = parent.lstat()
        if (
            parent.is_symlink()
            or not stat.S_ISDIR(parent_info.st_mode)
            or parent.resolve(strict=True) != parent
        ):
            raise ValueError("game_package_parent_invalid: output parent is unsafe")
        root_name = os.path.normcase(os.path.realpath(root))
        destination_name = os.path.normcase(os.path.realpath(destination))
        try:
            common = os.path.commonpath((root_name, destination_name))
        except ValueError:
            common = ""
        if common == root_name:
            raise ValueError(
                "game_package_destination_invalid: output must be outside the game"
            )
        return destination


    def _publish(
        path: Path,
        payload: bytes,
        verifier,
        publication_hook=None,
    ) -> None:
        from gamepack_runtime.game_package import MAX_GAME_PACKAGE_ARCHIVE_BYTES
        from gamepack_runtime.persistence_io import publish_bytes_noreplace

        parent_info = path.parent.lstat()
        parent_identity = parent_info.st_dev, parent_info.st_ino

        def validate_written_archive(written):
            verified = verifier(written)
            try:
                if verified.archive_bytes != payload:
                    raise ValueError(
                        "game_package_publication_indeterminate: "
                        "temporary archive differs from its exact source bytes"
                    )
            finally:
                verified.close()

        publish_bytes_noreplace(
            path.parent,
            path.name,
            payload,
            expected_parent_identity=parent_identity,
            limit=MAX_GAME_PACKAGE_ARCHIVE_BYTES,
            validate=validate_written_archive,
            publication_hook=publication_hook,
            mode=0o644,
        )


    def main() -> int:
        parser = argparse.ArgumentParser(
            description="Build one deterministic generic standalone game package"
        )
        parser.add_argument("output")
        args = parser.parse_args()
        try:
            root = _root()
            destination = _destination(args.output, root)
            sys.dont_write_bytecode = True
            runtime = root / "game_data/runtime-bundle/runtime/snapshot-tree"
            sys.path.insert(0, str(runtime))
            from gamepack_runtime.game_package import (
                build_game_package_from_standalone,
                verify_game_package_bytes,
                verify_game_package_file,
            )

            package = build_game_package_from_standalone(root)
            _publish(
                destination,
                package.archive_bytes,
                verify_game_package_bytes,
            )
            visible = verify_game_package_file(destination)
            if visible.archive_sha256 != package.archive_sha256:
                raise ValueError(
                    "game_package_publication_indeterminate: output bytes differ"
                )
            manifest = visible.manifest
            print(
                json.dumps(
                    {
                        "archive_sha256": visible.archive_sha256,
                        "content_hash": manifest["content_hash"],
                        "package_id": manifest["package_id"],
                        "path": str(destination),
                        "standalone_game_hash": manifest["standalone_game"][
                            "content_hash"
                        ],
                        "status": "packaged",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR {exc}", file=sys.stderr)
            return 1


    if __name__ == "__main__":
        raise SystemExit(main())
    """
)

OFFLINE_SMOKE = _text(
    r"""
    from __future__ import annotations

    import json
    import sys
    from pathlib import Path


    ROOT = Path(__file__).resolve(strict=True).parents[1]
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "game_data/runtime-bundle/runtime/snapshot-tree"))


    def main() -> int:
        try:
            from gamepack_runtime.distribution import verify_standalone_distribution
            from gamepack_raylib_2d.resources import load_runtime_bundle

            report = verify_standalone_distribution(ROOT)
            loaded = load_runtime_bundle(ROOT / "game_data/runtime-bundle")
            if loaded.manifest["content_hash"] != report["runtime_bundle_hash"]:
                raise RuntimeError(
                    "standalone_game_lineage_mismatch: runtime bundle hash differs"
                )
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "game_id": report["game_id"],
                    "runtime_bundle_hash": report["runtime_bundle_hash"],
                    "status": "offline_smoke_verified",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0


    if __name__ == "__main__":
        raise SystemExit(main())
    """
)

NATIVE_SMOKE = _text(
    r"""
    from __future__ import annotations

    import json
    import sys
    from pathlib import Path


    ROOT = Path(__file__).resolve(strict=True).parents[1]
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / "game_data/runtime-bundle/runtime/snapshot-tree"))

    from gamepack_raylib_2d.native_smoke import NativeSmokeError, native_smoke


    def main() -> int:
        try:
            report = native_smoke(
                ROOT / "game_data/runtime-bundle",
                max_frames=2,
                hidden=True,
            )
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        except (ImportError, NativeSmokeError, OSError, RuntimeError, ValueError) as exc:
            print(f"ERROR {exc}", file=sys.stderr)
            return 1


    if __name__ == "__main__":
        raise SystemExit(main())
    """
)

GAME_RUNNER = _text(
    r"""
    from __future__ import annotations

    import argparse
    import json
    import os
    import sys
    from pathlib import Path

    from gamepack_raylib_2d.resources import load_runtime_bundle
    from gamepack_runtime import (
        GameSession,
        build_game_replay,
        build_game_save,
        play_game_replay,
        read_game_replay_slot,
        read_game_save_slot,
        restore_game_save,
        validate_game_execution_script,
        write_game_replay_slot,
        write_game_save_slot,
    )
    from gamepack_runtime.persistence_io import decode_json_object


    def _parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(prog="run_game.py")
        parser.add_argument("--headless-script", type=Path)
        parser.add_argument("--scenario")
        parser.add_argument("--user-data", type=Path)
        parser.add_argument("--load-slot")
        parser.add_argument("--save-on-exit-slot")
        parser.add_argument("--record-replay-slot")
        parser.add_argument("--replay-slot")
        parser.add_argument("--verify-save-slot")
        replay_conflicts = (
            "headless_script",
            "scenario",
            "load_slot",
            "save_on_exit_slot",
            "record_replay_slot",
            "verify_save_slot",
        )
        original_parse = parser.parse_args

        def checked_parse(args=None):
            parsed = original_parse(args)
            if parsed.replay_slot is not None:
                for field in replay_conflicts:
                    if getattr(parsed, field) is not None:
                        parser.error(
                            f"argument --replay-slot: not allowed with argument "
                            f"--{field.replace('_', '-')}"
                        )
            if parsed.record_replay_slot is not None and parsed.load_slot is not None:
                parser.error(
                    "argument --record-replay-slot: not allowed with argument --load-slot"
                )
            if parsed.verify_save_slot is not None:
                for field in replay_conflicts[:-1]:
                    if getattr(parsed, field) is not None:
                        parser.error(
                            f"argument --verify-save-slot: not allowed with argument "
                            f"--{field.replace('_', '-')}"
                        )
            if parsed.scenario is not None and parsed.headless_script is None:
                parser.error("argument --scenario: requires argument --headless-script")
            if parsed.headless_script is not None and parsed.load_slot is not None:
                parser.error(
                    "argument --headless-script: not allowed with argument --load-slot"
                )
            if (
                parsed.headless_script is not None
                and parsed.scenario is None
                and (
                    parsed.save_on_exit_slot is not None
                    or parsed.record_replay_slot is not None
                )
            ):
                parser.error(
                    "argument --scenario: required for headless persistence"
                )
            return parsed

        parser.parse_args = checked_parse
        return parser


    def _default_user_data(game_id: str) -> Path:
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
            return base / "World Forge/Games" / game_id
        return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / (
            "world-forge/games"
        ) / game_id


    def _user_data(root: Path, requested: Path | None, game_id: str) -> Path:
        value = _default_user_data(game_id) if requested is None else Path(
            os.path.abspath(os.fspath(requested))
        )
        game_root = root.resolve(strict=True)
        resolved = value.resolve(strict=False)
        try:
            common = Path(os.path.commonpath((game_root, resolved)))
        except ValueError:
            return resolved
        if common == game_root:
            raise ValueError("user_data_inside_game: persistence must stay outside the game root")
        return resolved


    def _headless(
        loaded,
        script_path: Path,
        *,
        scenario_id: str | None,
        user_data: Path,
        save_slot: str | None,
        replay_slot: str | None,
    ) -> dict[str, object]:
        script = decode_json_object(
            script_path.read_bytes(),
            source=script_path,
            limit=4 * 1024 * 1024,
        )
        checked = validate_game_execution_script(
            loaded.manifest,
            script,
            gamepack=loaded.gamepack,
            composition=loaded.composition,
            adapter=loaded.adapter,
            runtime_snapshot=loaded.snapshot,
        )
        context = loaded_context(loaded)
        scenarios = checked["scenarios"]
        if scenario_id is not None:
            scenarios = [
                scenario
                for scenario in scenarios
                if scenario["scenario_id"] == scenario_id
            ]
            if len(scenarios) != 1:
                raise ValueError(
                    f"headless_scenario_missing: {scenario_id}"
                )
        reports = []
        for scenario in scenarios:
            session = GameSession(loaded.gamepack)
            results = []
            for action in scenario["actions"]:
                result = session.apply(action["action_id"], action["parameters"])
                if not result.accepted:
                    raise ValueError(
                        f"headless_action_rejected: {scenario['scenario_id']}"
                    )
                results.append(result)
            if session.state_hash != scenario["expected_final_state_hash"]:
                raise ValueError(
                    f"headless_state_mismatch: {scenario['scenario_id']}"
                )
            classification = {
                "terminal": session.classification.terminal,
                "ending_ids": list(session.classification.ending_ids),
                "ending_kind": session.classification.ending_kind,
                "failure_ids": list(session.classification.failure_ids),
                "goal_ids": list(session.classification.goal_ids),
                "recovery_action_ids": list(
                    session.classification.recovery_action_ids
                ),
            }
            if classification != scenario["expected_classification"]:
                raise ValueError(
                    f"headless_classification_mismatch: {scenario['scenario_id']}"
                )
            saved = build_game_save(context, session.state)
            restored = restore_game_save(context, saved)
            replay = build_game_replay(context, results)
            replayed = play_game_replay(context, replay)
            if restored != session.state or replayed.state_hash != session.state_hash:
                raise ValueError(
                    f"headless_persistence_mismatch: {scenario['scenario_id']}"
                )
            if save_slot is not None:
                write_game_save_slot(
                    user_data,
                    save_slot,
                    saved,
                    context,
                )
            if replay_slot is not None:
                write_game_replay_slot(
                    user_data,
                    replay_slot,
                    replay,
                    context,
                )
            reports.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "action_count": len(results),
                    "final_state_hash": session.state_hash,
                    "classification": classification,
                }
            )
        return {
            "status": "passed",
            "native_execution": False,
            "runtime_bundle_hash": loaded.manifest["content_hash"],
            "save_slot": save_slot,
            "replay_slot": replay_slot,
            "scenarios": reports,
        }


    def main(root: Path) -> int:
        try:
            args = _parser().parse_args()
            loaded = load_runtime_bundle(root / "game_data/runtime-bundle")
            user_data = _user_data(
                root,
                args.user_data,
                loaded.gamepack["game"]["id"],
            )
            if args.headless_script is not None:
                print(
                    json.dumps(
                        _headless(
                            loaded,
                            args.headless_script,
                            scenario_id=args.scenario,
                            user_data=user_data,
                            save_slot=args.save_on_exit_slot,
                            replay_slot=args.record_replay_slot,
                        ),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0
            if args.verify_save_slot is not None:
                context = loaded_context(loaded)
                saved = read_game_save_slot(
                    user_data,
                    args.verify_save_slot,
                    context,
                )
                session = GameSession(loaded.gamepack)
                session.restore(restore_game_save(context, saved))
                print(
                    json.dumps(
                        {
                            "classification": {
                                "ending_ids": list(session.classification.ending_ids),
                                "terminal": session.classification.terminal,
                            },
                            "state_hash": session.state_hash,
                            "status": "save_restored",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0
            if args.replay_slot is not None:
                replay = read_game_replay_slot(
                    user_data,
                    args.replay_slot,
                    loaded_context(loaded),
                )
                session = play_game_replay(loaded_context(loaded), replay)
                print(
                    json.dumps(
                        {
                            "classification": {
                                "ending_ids": list(session.classification.ending_ids),
                                "terminal": session.classification.terminal,
                            },
                            "state_hash": session.state_hash,
                            "status": "replay_complete",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0

            from gamepack_raylib_2d.app import RuntimeApp
            from gamepack_raylib_2d.backend import PyrayBackend

            app = RuntimeApp.from_bundle(
                root / "game_data/runtime-bundle",
                backend=PyrayBackend(),
                hidden=False,
            )
            try:
                if args.load_slot is not None:
                    saved = read_game_save_slot(
                        user_data,
                        args.load_slot,
                        app.persistence_context,
                    )
                    app.controller.session.restore(
                        restore_game_save(app.persistence_context, saved)
                    )
                frames = app.run(max_frames=600)
                if args.save_on_exit_slot is not None:
                    write_game_save_slot(
                        user_data,
                        args.save_on_exit_slot,
                        build_game_save(
                            app.persistence_context,
                            app.controller.session.state,
                        ),
                        app.persistence_context,
                    )
                if args.record_replay_slot is not None:
                    write_game_replay_slot(
                        user_data,
                        args.record_replay_slot,
                        build_game_replay(
                            app.persistence_context,
                            app.controller.accepted_results,
                        ),
                        app.persistence_context,
                    )
                print(
                    json.dumps(
                        {
                            "frames": frames,
                            "state_hash": app.controller.session.state_hash,
                            "status": "native_complete",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0
            finally:
                app.close()
        except SystemExit:
            raise
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            reason = getattr(exc, "reason_code", "runtime_error")
            print(f"ERROR {reason}: {getattr(exc, 'detail', str(exc))}", file=sys.stderr)
            return 1


    def loaded_context(loaded):
        from gamepack_runtime import build_game_persistence_context

        return build_game_persistence_context(
            loaded.gamepack,
            loaded.composition,
            loaded.manifest,
            loaded.adapter,
        )
    """
)

SHELL_TEST = _text(
    r"""
    from __future__ import annotations

    import json
    import subprocess
    import sys
    import unittest
    from pathlib import Path


    ROOT = Path(__file__).resolve().parents[1]


    class GameShellTests(unittest.TestCase):
        def test_independent_verifier(self) -> None:
            result = subprocess.run(
                [sys.executable, "-I", str(ROOT / "scripts/verify_game.py")],
                cwd=ROOT,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "verified")


    if __name__ == "__main__":
        unittest.main()
    """
)

PYPROJECT = _text(
    """
    [project]
    name = "standalone-raylib-game"
    version = "1.0.0"
    requires-python = ">=3.11,<3.13"
    dependencies = ["raylib==6.0.1.0"]

    [tool.ruff]
    target-version = "py311"
    """
)

REQUIREMENTS = _text(
    """
    raylib==6.0.1.0 \\
        --hash=sha256:6b126a8b9e9a0d36dc796fb0ae1bd7473464a4b126315e332079e5eca7215116 \\
        --hash=sha256:a665bd824128396f70435f959399d76c2bb460ce1867fb9d19b41490b70a0d2a \\
        --hash=sha256:bcd224e184c5d64fb6d57bbdabc07124a6f64455ec711d748a0c148b3b26b914 \\
        --hash=sha256:64ee5407b3e222045a2b4e6c41ede77a7be05c90335e0679c4765d0e5bcf3ba6
    """
)

README = _text(
    """
    # Standalone World Forge Game

    This directory is an immutable, offline-capable game distribution.
    It contains compiled game logic, sealed assets, and a bounded raylib adapter.
    It does not contain authoring projects, prompts, provider SDKs, or runtime AI.

    Verify it from any working directory:

    ```bash
    python -I scripts/verify_game.py
    ```

    Run the native game only on a declared Linux or Windows x86_64 target with
    the exact locked `raylib` dependency installed:

    ```bash
    python -I run_game.py
    ```
    """
)

THIRD_PARTY_NOTICES = _text(
    """
    # Third-Party Notices

    This game distribution uses the `raylib` Python package version 6.0.1.0 and
    the raylib native API declared by `platform.lock.json`. Consult the installed
    package metadata for its exact license texts. Game assets retain the licenses
    and provenance recorded in the sealed runtime bundle.
    """
)

GITIGNORE = _text(
    """
    __pycache__/
    *.py[cod]
    .venv/
    user-data/
    saves/
    replays/
    """
)

GAME_INIT = _text(
    '''
    """Game-local launcher package; immutable runtime code stays in game_data."""
    '''
)


STANDALONE_TEMPLATE_FILES: Final = MappingProxyType(
    {
        ".gitignore": (GITIGNORE, "gitignore"),
        "README.md": (README, "game_readme"),
        "THIRD_PARTY_NOTICES.md": (THIRD_PARTY_NOTICES, "third_party_notices"),
        "pyproject.toml": (PYPROJECT, "game_package"),
        "requirements.txt": (REQUIREMENTS, "requirements"),
        "run_game.py": (RUN_GAME, "game_launcher"),
        "scripts/native_smoke.py": (NATIVE_SMOKE, "native_smoke_launcher"),
        "scripts/offline_smoke.py": (OFFLINE_SMOKE, "offline_smoke_launcher"),
        "scripts/package_game.py": (PACKAGE_GAME, "game_packager"),
        "scripts/verify_game.py": (VERIFY_GAME, "game_verifier"),
        "src/game/__init__.py": (GAME_INIT, "game_source"),
        "src/game/runner.py": (GAME_RUNNER, "game_source"),
        "tests/test_game_shell.py": (SHELL_TEST, "game_test"),
    }
)

REQUIRED_LAUNCHER_ROLES: Final = (
    "game_launcher",
    "game_packager",
    "game_verifier",
    "native_smoke_launcher",
)


def materialization_policy_bytes(*, ready: bool) -> bytes:
    present = sorted(
        {
            role
            for _path, (_payload, role) in STANDALONE_TEMPLATE_FILES.items()
            if role in REQUIRED_LAUNCHER_ROLES
        }
        if ready
        else set(),
        key=lambda item: item.encode("utf-8"),
    )
    missing = [role for role in REQUIRED_LAUNCHER_ROLES if role not in present]
    return canonical_json_bytes(
        {
            "format": "world-forge.game_materialization_policy",
            "format_version": 1,
            "materialization_ready": not missing,
            "present_launcher_roles": present,
            "reason_codes": ([] if not missing else ["standalone_launcher_inventory_incomplete"]),
            "required_launcher_roles": list(REQUIRED_LAUNCHER_ROLES),
            "template_tree_hash": (
                hashlib.sha256(
                    b"".join(
                        path.encode("utf-8") + b"\0" + hashlib.sha256(payload).digest()
                        for path, (payload, _role) in sorted(
                            STANDALONE_TEMPLATE_FILES.items(),
                            key=lambda item: item[0].encode("utf-8"),
                        )
                    )
                ).hexdigest()
                if ready
                else None
            ),
        }
    )


__all__ = [
    "REQUIRED_LAUNCHER_ROLES",
    "STANDALONE_TEMPLATE_FILES",
    "materialization_policy_bytes",
]
