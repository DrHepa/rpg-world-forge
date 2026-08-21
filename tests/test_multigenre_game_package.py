from __future__ import annotations

import concurrent.futures
import contextlib
import copy
import errno
import hashlib
import io
import json
import os
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
import zipfile
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import worldforge.game_package as game_package_module
from gamepack_runtime.distribution import (
    GAME_LOCK_PATH,
    GAME_MANIFEST_PATH,
    canonical_contract_hash,
)
from gamepack_runtime.game_package import (
    GAME_PACKAGE_FORMAT,
    MAX_GAME_PACKAGE_ARCHIVE_BYTES,
    PACKAGE_MANIFEST_PATH,
    GamePackageError,
    build_game_package_from_standalone,
    validate_game_package_document,
    verify_game_package_bytes,
    verify_game_package_file,
)
from gamepack_runtime.persistence_io import (
    PersistenceIOError,
    publish_bytes_noreplace,
)
from tests.test_multigenre_standalone_materialization import _ready_materialization
from worldforge.directory_publish import RetainedStageWriter
from worldforge.game_package import (
    WorldForgeGamePackageError,
    extract_game_package,
    package_game,
    recover_game_package_extraction,
    rollback_game_package_extraction,
    verify_game_package,
)
from worldforge.standalone_game import materialize_game

ROOT = Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def _standalone(name: str, root: Path):
    with _ready_materialization(name, root) as source:
        target = root / f"{name}-standalone"
        verified = materialize_game(source.root, target)
        try:
            yield target, verified
        finally:
            verified.close()


def _rewrite_zip(
    payload: bytes,
    *,
    mutate: Callable[
        [str, zipfile.ZipInfo, bytes],
        tuple[str, zipfile.ZipInfo | None, bytes],
    ],
) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(payload), "r")
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as target:
        for original in source.infolist():
            data = source.read(original)
            name, info, data = mutate(original.filename, original, data)
            if info is None:
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = (0o100644) << 16
            target.writestr(info, data)
    return output.getvalue()


def _reseal_package_manifest(document: dict[str, object]) -> None:
    document["package_id"] = (
        "game_package_"
        + canonical_contract_hash(
            {
                "files": document["files"],
                "game_id": document["game_id"],
                "lineage": document["lineage"],
                "payload_lock": document["payload_lock"],
                "standalone_game": document["standalone_game"],
            }
        )[:40]
    )
    document["content_hash"] = canonical_contract_hash(document)


class GenericGamePackageTests(unittest.TestCase):
    def test_extraction_scopes_standalone_stage_capability_to_private_verification_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-package-extract-stage-calls-") as temporary:
            root = Path(temporary)
            destination = root / "extracted"
            with _standalone("abstract-puzzle", root) as (game, _verified):
                package_path = root / "puzzle.wfgame"
                built = build_game_package_from_standalone(game)
                package_path.write_bytes(built.archive_bytes)

            original = game_package_module.verify_standalone_game
            with mock.patch.object(
                game_package_module,
                "verify_standalone_game",
                wraps=original,
            ) as verify_calls:
                verified = extract_game_package(package_path, destination)
                verified.close()

            stage_calls = [
                call
                for call in verify_calls.call_args_list
                if call.kwargs.get("_retained_stage_writer") is not None
            ]
            self.assertEqual(1, len(stage_calls))
            writer = stage_calls[0].kwargs["_retained_stage_writer"]
            self.assertIs(type(writer), RetainedStageWriter)
            self.assertEqual(Path(os.path.abspath(stage_calls[0].args[0])), writer.stage)
            strict_destination_calls = [
                call
                for call in verify_calls.call_args_list
                if Path(os.path.abspath(call.args[0])) == destination
                and call.kwargs.get("_retained_stage_writer") is None
            ]
            self.assertGreaterEqual(len(strict_destination_calls), 2)

    def test_cli_surfaces_return_closed_machine_readable_reports(self) -> None:
        from worldforge.__main__ import main

        def invoke(arguments: list[str]) -> tuple[int, str, str]:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
                mock.patch.object(sys, "argv", ["worldforge", *arguments]),
            ):
                status = main()
            return status, stdout.getvalue(), stderr.getvalue()

        with tempfile.TemporaryDirectory(prefix="wf-game-package-cli-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, standalone):
                package_path = root / "puzzle.wfgame"
                status, stdout, stderr = invoke(["package-game", str(game), str(package_path)])
                self.assertEqual((status, stderr), (0, ""))
                packaged = json.loads(stdout)
                self.assertEqual(packaged["status"], "packaged")
                self.assertEqual(
                    packaged["standalone_game_hash"],
                    standalone.manifest["content_hash"],
                )
                status, stdout, stderr = invoke(["verify-game-package", str(package_path)])
                self.assertEqual((status, stderr), (0, ""))
                self.assertEqual(json.loads(stdout)["status"], "verified")
                target = root / "cli-extracted"
                status, stdout, stderr = invoke(
                    ["extract-game-package", str(package_path), str(target)]
                )
                self.assertEqual((status, stderr), (0, ""))
                self.assertEqual(json.loads(stdout)["status"], "materialized")
                status, stdout, stderr = invoke(["recover-game-package-extraction", str(target)])
                self.assertEqual((status, stderr), (0, ""))
                self.assertEqual(json.loads(stdout)["status"], "no_operation")
                status, stdout, stderr = invoke(["rollback-game-package-extraction", str(target)])
                self.assertEqual((status, stderr), (0, ""))
                self.assertEqual(json.loads(stdout)["status"], "no_operation")

    def test_both_verticals_build_byte_identical_canonical_packages(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-") as temporary:
            root = Path(temporary)
            for name in ("abstract-puzzle", "branching-narrative"):
                with self.subTest(name=name), _standalone(name, root) as (game, standalone):
                    first = build_game_package_from_standalone(game)
                    second = build_game_package_from_standalone(game)
                    self.assertEqual(first.archive_bytes, second.archive_bytes)
                    self.assertEqual(
                        first.archive_sha256, hashlib.sha256(first.archive_bytes).hexdigest()
                    )
                    verified = verify_game_package_bytes(first.archive_bytes)
                    self.assertEqual(verified.manifest, first.manifest)
                    self.assertEqual(verified.manifest["format"], GAME_PACKAGE_FORMAT)
                    self.assertEqual(
                        verified.manifest["standalone_game"]["content_hash"],
                        standalone.manifest["content_hash"],
                    )
                    self.assertEqual(
                        verified.manifest["payload_lock"]["content_hash"],
                        standalone.lock["content_hash"],
                    )
                    self.assertEqual(
                        verified.manifest["payload_lock"]["tree_hash"],
                        standalone.lock["tree_hash"],
                    )
                    self.assertEqual(
                        verified.manifest["lineage"],
                        standalone.manifest["lineage"],
                    )
                    self.assertEqual(
                        verified.manifest["content_hash"],
                        canonical_contract_hash(verified.manifest),
                    )
                    expected = {
                        GAME_MANIFEST_PATH,
                        GAME_LOCK_PATH,
                        *(item["path"] for item in standalone.lock["files"]),
                    }
                    self.assertEqual(
                        {item["path"] for item in verified.manifest["files"]},
                        expected,
                    )
                    self.assertNotIn(PACKAGE_MANIFEST_PATH, expected)
                    with zipfile.ZipFile(io.BytesIO(first.archive_bytes), "r") as archive:
                        infos = archive.infolist()
                        self.assertEqual(
                            [info.filename for info in infos],
                            sorted(
                                [PACKAGE_MANIFEST_PATH, *expected],
                                key=lambda item: item.encode("utf-8"),
                            ),
                        )
                        for info in infos:
                            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
                            self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                            self.assertEqual(info.create_system, 3)
                            self.assertEqual((info.external_attr >> 16) & 0o177777, 0o100644)
                            self.assertEqual(info.extra, b"")
                            self.assertEqual(info.comment, b"")

    def test_package_verifier_rejects_tampering_noncanonical_metadata_and_legacy_zip(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-negative-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                package = build_game_package_from_standalone(game)
                target_path = next(
                    item["path"]
                    for item in package.manifest["files"]
                    if item["path"] not in {GAME_MANIFEST_PATH, GAME_LOCK_PATH}
                )

                tampered = _rewrite_zip(
                    package.archive_bytes,
                    mutate=lambda name, info, data: (
                        name,
                        info,
                        data + b"x" if name == target_path else data,
                    ),
                )
                with self.assertRaisesRegex(
                    GamePackageError,
                    "^game_package_(?:file|archive|canonical)_",
                ):
                    verify_game_package_bytes(tampered)

                def altered_timestamp(name, info, data):
                    replacement = zipfile.ZipInfo(name, (1981, 1, 1, 0, 0, 0))
                    replacement.compress_type = zipfile.ZIP_STORED
                    replacement.create_system = 3
                    replacement.external_attr = (0o100644) << 16
                    return name, replacement, data

                metadata_tampered = _rewrite_zip(
                    package.archive_bytes,
                    mutate=altered_timestamp,
                )
                with self.assertRaisesRegex(
                    GamePackageError,
                    "^game_package_(?:archive|canonical)_",
                ):
                    verify_game_package_bytes(metadata_tampered)

                legacy = io.BytesIO()
                with zipfile.ZipFile(legacy, "w", compression=zipfile.ZIP_STORED) as archive:
                    archive.writestr("game-manifest.json", b"{}")
                with self.assertRaisesRegex(
                    GamePackageError,
                    "^game_package_manifest_missing:",
                ):
                    verify_game_package_bytes(legacy.getvalue())

    def test_invalid_archive_causes_zero_extraction_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-zero-write-") as temporary:
            root = Path(temporary)
            package_path = root / "invalid.wfgame"
            package_path.write_bytes(b"not a zip")
            destination = root / "destination"
            before = set(root.iterdir())
            with self.assertRaisesRegex(
                ValueError,
                "^game_package_archive_invalid:",
            ):
                extract_game_package(package_path, destination)
            self.assertEqual(set(root.iterdir()), before)
            self.assertFalse(destination.exists())
            self.assertFalse(
                root.joinpath(".destination.game-package-extraction.journal.json").exists()
            )

    def test_package_publish_extract_and_external_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-e2e-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, standalone):
                release = root / "release"
                release.mkdir()
                package_path = release / "puzzle.wfgame"
                packaged = package_game(game, package_path)
                self.assertEqual(
                    packaged.manifest["standalone_game"]["content_hash"],
                    standalone.manifest["content_hash"],
                )
                packaged.close()
                verified_package = verify_game_package(package_path)
                archive_sha256 = verified_package.archive_sha256
                verified_package.close()
                extracted = root / "extracted-game"
                verified_game = extract_game_package(package_path, extracted)
                self.assertEqual(
                    verified_game.manifest["content_hash"],
                    standalone.manifest["content_hash"],
                )
                verified_game.close()
                self.assertFalse((extracted / PACKAGE_MANIFEST_PATH).exists())
                self.assertEqual(
                    verify_game_package(package_path).archive_sha256,
                    archive_sha256,
                )

            environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONHOME", "PYTHONPATH"}
            }
            verifier = subprocess.run(
                [sys.executable, "-I", str(extracted / "scripts/verify_game.py")],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(verifier.returncode, 0, verifier.stderr)
            user_data = root / "user-data"
            recorded = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(extracted / "run_game.py"),
                    "--headless-script",
                    str(
                        ROOT
                        / "examples/multigenre-contracts/abstract-puzzle/runtime/headless"
                        / "execution-script.json"
                    ),
                    "--scenario",
                    "swap_tiles",
                    "--user-data",
                    str(user_data),
                    "--save-on-exit-slot",
                    "solved",
                    "--record-replay-slot",
                    "solve",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(recorded.returncode, 0, recorded.stderr)
            replayed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    str(extracted / "run_game.py"),
                    "--user-data",
                    str(user_data),
                    "--replay-slot",
                    "solve",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(replayed.returncode, 0, replayed.stderr)
            self.assertEqual(
                json.loads(replayed.stdout)["classification"]["ending_ids"],
                ["puzzle_complete"],
            )

    def test_extraction_recovery_and_owned_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-recovery-") as temporary:
            root = Path(temporary)
            with _standalone("branching-narrative", root) as (game, _verified):
                package_path = root / "narrative.wfgame"
                packaged = package_game(game, package_path)
                packaged.close()

            recover_target = root / "recover-game"

            def crash_ready(stage: str, _path: Path | None) -> None:
                if stage == "after_ready_journal_written":
                    raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                extract_game_package(
                    package_path,
                    recover_target,
                    _publication_hook=crash_ready,
                )
            recovered = recover_game_package_extraction(recover_target)
            self.assertIsNotNone(recovered)
            assert recovered is not None
            recovered.close()
            self.assertTrue(recover_target.is_dir())

            rollback_target = root / "rollback-game"

            def crash_copying(stage: str, _path: Path | None) -> None:
                if stage == "after_copying_journal_written":
                    raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                extract_game_package(
                    package_path,
                    rollback_target,
                    _publication_hook=crash_copying,
                )
            if sys.platform.startswith("linux") and os.name == "posix":
                with self.assertRaises(WorldForgeGamePackageError) as recovery:
                    recover_game_package_extraction(rollback_target)
                self.assertEqual(
                    "game_package_recovery_required",
                    recovery.exception.reason_code,
                )
                self.assertEqual(
                    next(root.glob(".rollback-game.game-package-stage-*")).name,
                    recovery.exception.recovery_evidence["stage"]["locator"],
                )
                with self.assertRaises(WorldForgeGamePackageError) as raised:
                    rollback_game_package_extraction(rollback_target)
                self.assertEqual(
                    "game_package_rollback_recovery_required",
                    raised.exception.reason_code,
                )
                self.assertIn("retained", raised.exception.detail)
                self.assertEqual(
                    (root / ".rollback-game.game-package-extraction.journal.json").name,
                    raised.exception.recovery_evidence["journal"]["locator"],
                )
                self.assertTrue(next(root.glob(".rollback-game.game-package-stage-*")).is_dir())
                self.assertTrue(
                    (root / ".rollback-game.game-package-extraction.journal.json").is_file()
                )
            else:
                result = rollback_game_package_extraction(rollback_target)
                self.assertEqual(result["status"], "rolled_back")
                self.assertFalse(rollback_target.exists())

    def test_extraction_never_executes_a_substituted_generated_verifier(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-no-exec-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                package_path = root / "puzzle.wfgame"
                package_game(game, package_path).close()
            destination = root / "extracted"
            marker = root / "substituted-verifier-ran"

            def substitute_verifier(stage: str, path: Path | None) -> None:
                if stage != "after_ready_journal_written":
                    return
                self.assertIsNotNone(path)
                assert path is not None
                verifier = path / "scripts/verify_game.py"
                verifier.rename(verifier.with_name("verify_game.original.py"))
                verifier.write_text(
                    "from pathlib import Path\n"
                    f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
                    encoding="utf-8",
                )

            with (
                mock.patch(
                    "subprocess.run",
                    side_effect=AssertionError(
                        "privileged extraction must not execute game-local code"
                    ),
                ),
                self.assertRaisesRegex(
                    WorldForgeGamePackageError,
                    "^game_package_publication_failed:",
                ),
            ):
                extract_game_package(
                    package_path,
                    destination,
                    _publication_hook=substitute_verifier,
                )
            self.assertFalse(marker.exists())
            self.assertFalse(destination.exists())

    def test_recovery_never_executes_a_substituted_generated_verifier(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-recover-no-exec-") as temporary:
            root = Path(temporary)
            with _standalone("branching-narrative", root) as (game, _verified):
                package_path = root / "narrative.wfgame"
                package_game(game, package_path).close()
            destination = root / "recovered"

            def crash_ready(stage: str, _path: Path | None) -> None:
                if stage == "after_ready_journal_written":
                    raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                extract_game_package(
                    package_path,
                    destination,
                    _publication_hook=crash_ready,
                )
            staged = next(root.glob(".recovered.game-package-stage-*"))
            marker = root / "substituted-recovery-verifier-ran"
            verifier = staged / "scripts/verify_game.py"
            verifier.rename(verifier.with_name("verify_game.original.py"))
            verifier.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n",
                encoding="utf-8",
            )
            with (
                mock.patch(
                    "subprocess.run",
                    side_effect=AssertionError(
                        "privileged recovery must not execute game-local code"
                    ),
                ),
                self.assertRaisesRegex(
                    WorldForgeGamePackageError,
                    "^game_package_recovery_ambiguous:",
                ),
            ):
                recover_game_package_extraction(destination)
            self.assertFalse(marker.exists())
            self.assertFalse(destination.exists())

    def test_archive_limits_are_enforced_before_member_reads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-limits-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                package = build_game_package_from_standalone(game)
            with mock.patch(
                "gamepack_runtime.game_package.MAX_GAME_PACKAGE_ENTRIES",
                len(package.manifest["files"]),
            ):
                with self.assertRaisesRegex(
                    GamePackageError,
                    "^game_package_limit_exceeded:",
                ):
                    verify_game_package_bytes(package.archive_bytes)

    def test_archive_rejects_duplicate_nonportable_and_noncanonical_members(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-archive-policy-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                package = build_game_package_from_standalone(game)
            payload_path = next(
                item["path"]
                for item in package.manifest["files"]
                if item["path"] not in {GAME_MANIFEST_PATH, GAME_LOCK_PATH}
            )

            def replace_info(
                name: str,
                *,
                compression: int = zipfile.ZIP_STORED,
                extra: bytes = b"",
            ) -> zipfile.ZipInfo:
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = compression
                info.create_system = 3
                info.external_attr = (0o100644) << 16
                info.extra = extra
                return info

            variants = {
                "deflated": _rewrite_zip(
                    package.archive_bytes,
                    mutate=lambda name, info, data: (
                        name,
                        (
                            replace_info(name, compression=zipfile.ZIP_DEFLATED)
                            if name == payload_path
                            else info
                        ),
                        data,
                    ),
                ),
                "extra": _rewrite_zip(
                    package.archive_bytes,
                    mutate=lambda name, info, data: (
                        name,
                        (
                            replace_info(name, extra=b"\x01\x00\x00\x00")
                            if name == payload_path
                            else info
                        ),
                        data,
                    ),
                ),
                "traversal": _rewrite_zip(
                    package.archive_bytes,
                    mutate=lambda name, info, data: (
                        "../escape" if name == payload_path else name,
                        None if name == payload_path else info,
                        data,
                    ),
                ),
                "casefold": _rewrite_zip(
                    package.archive_bytes,
                    mutate=lambda name, info, data: (
                        "GAME-MANIFEST.json" if name == payload_path else name,
                        None if name == payload_path else info,
                        data,
                    ),
                ),
                "prefix": _rewrite_zip(
                    package.archive_bytes,
                    mutate=lambda name, info, data: (
                        "game-manifest.json/child" if name == payload_path else name,
                        None if name == payload_path else info,
                        data,
                    ),
                ),
                "directory": _rewrite_zip(
                    package.archive_bytes,
                    mutate=lambda name, info, data: (
                        f"{name}/" if name == payload_path else name,
                        None if name == payload_path else info,
                        data,
                    ),
                ),
            }

            duplicate = io.BytesIO()
            with (
                zipfile.ZipFile(io.BytesIO(package.archive_bytes), "r") as source,
                zipfile.ZipFile(duplicate, "w", compression=zipfile.ZIP_STORED) as target,
            ):
                for info in source.infolist():
                    target.writestr(info, source.read(info))
                repeated = source.getinfo(payload_path)
                with self.assertWarns(UserWarning):
                    target.writestr(repeated, source.read(repeated))
            variants["duplicate"] = duplicate.getvalue()

            commented = io.BytesIO(package.archive_bytes)
            with zipfile.ZipFile(commented, "a") as archive:
                archive.comment = b"forbidden"
            variants["comment"] = commented.getvalue()

            encrypted = bytearray(package.archive_bytes)
            for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
                cursor = 0
                while True:
                    cursor = encrypted.find(signature, cursor)
                    if cursor < 0:
                        break
                    flag_at = cursor + offset
                    flags = int.from_bytes(encrypted[flag_at : flag_at + 2], "little")
                    encrypted[flag_at : flag_at + 2] = (flags | 1).to_bytes(2, "little")
                    cursor += len(signature)
            variants["encrypted"] = bytes(encrypted)

            for name, payload in variants.items():
                with self.subTest(name=name), self.assertRaises(GamePackageError):
                    verify_game_package_bytes(payload)

    def test_manifest_policy_rejects_cross_runtime_boundary_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-contract-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                baseline = build_game_package_from_standalone(game).manifest

            def version(
                document: dict[str, object],
                owner: str,
            ) -> None:
                if owner == "package":
                    document["format_version"] = True
                else:
                    nested = document[owner]
                    assert isinstance(nested, dict)
                    nested["format_version"] = True

            def game_id(document: dict[str, object], value: str) -> None:
                document["game_id"] = value
                standalone = document["standalone_game"]
                assert isinstance(standalone, dict)
                standalone["game_id"] = value

            def lock_id(document: dict[str, object], value: str) -> None:
                lock = document["payload_lock"]
                assert isinstance(lock, dict)
                lock["id"] = value

            def add_path(document: dict[str, object], value: str) -> None:
                files = document["files"]
                assert isinstance(files, list)
                files.append(
                    {
                        "path": value,
                        "sha256": "a" * 64,
                        "size_bytes": 1,
                    }
                )
                files.sort(key=lambda item: item["path"].encode("utf-8"))

            mutations: list[tuple[str, Callable[[dict[str, object]], None]]] = [
                ("package_version_boolean", lambda item: version(item, "package")),
                ("standalone_version_boolean", lambda item: version(item, "standalone_game")),
                ("lock_version_boolean", lambda item: version(item, "payload_lock")),
                ("empty_game_id", lambda item: game_id(item, "")),
                ("reserved_game_id", lambda item: game_id(item, "con")),
                ("long_game_id", lambda item: game_id(item, "a" * 65)),
                ("empty_lock_id", lambda item: lock_id(item, "")),
                ("reserved_lock_id", lambda item: lock_id(item, "lpt1")),
                ("uppercase_lock_id", lambda item: lock_id(item, "Invalid")),
                ("trailing_space_path", lambda item: add_path(item, "asset. ")),
                ("reserved_path", lambda item: add_path(item, "CON.txt")),
                ("unicode_path", lambda item: add_path(item, "é.png")),
                ("unsupported_character_path", lambda item: add_path(item, "asset?.png")),
                (
                    "casefold_prefix_collision",
                    lambda item: add_path(item, "GAME-MANIFEST.json/child"),
                ),
            ]
            for name, mutate in mutations:
                document = copy.deepcopy(baseline)
                mutate(document)
                _reseal_package_manifest(document)
                with self.subTest(name=name), self.assertRaises(GamePackageError):
                    validate_game_package_document(document)

    def test_package_file_verification_uses_retained_nonfollowing_reader(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-reader-") as temporary:
            root = Path(temporary)
            package_path = root / "package.wfgame"
            package_path.write_bytes(b"path bytes must not be opened directly")
            expected = object()
            with (
                mock.patch(
                    "gamepack_runtime.game_package.read_immutable_file_bytes",
                    return_value=b"retained exact bytes",
                ) as retained_read,
                mock.patch(
                    "gamepack_runtime.game_package.verify_game_package_bytes",
                    return_value=expected,
                ) as verify_bytes,
                mock.patch(
                    "gamepack_runtime.game_package.os.open",
                    side_effect=AssertionError("path-coupled open is forbidden"),
                ),
            ):
                verified = verify_game_package_file(package_path)
            self.assertIs(verified, expected)
            retained_read.assert_called_once_with(
                package_path,
                limit=MAX_GAME_PACKAGE_ARCHIVE_BYTES,
            )
            verify_bytes.assert_called_once_with(b"retained exact bytes")

            from gamepack_runtime.persistence_io import _WindowsPersistenceApi

            windows_api = object.__new__(_WindowsPersistenceApi)
            with mock.patch.object(
                windows_api,
                "_open_relative",
                return_value=123,
            ) as native_open:
                self.assertEqual(
                    windows_api.open_existing_file(
                        10,
                        "package.wfgame",
                        share_write=False,
                        share_delete=False,
                    ),
                    123,
                )
            self.assertEqual(
                native_open.call_args.kwargs["share"],
                windows_api._SHARE_READ,
            )
            self.assertEqual(
                native_open.call_args.kwargs["options"],
                windows_api._FILE_NON_DIRECTORY_FILE,
            )
            with mock.patch.object(
                windows_api,
                "_open_relative",
                return_value=456,
            ) as native_create:
                self.assertEqual(
                    windows_api.create_temporary(10, ".package.stage"),
                    456,
                )
            self.assertEqual(
                native_create.call_args.kwargs["share"],
                windows_api._SHARE_READ,
            )

    @unittest.skipUnless(
        os.name == "nt",
        "requires native Windows retained-handle sharing semantics",
    )
    def test_windows_package_stage_denies_write_and_delete_sharing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-win-stage-") as temporary:
            parent = Path(temporary)
            info = parent.stat()
            stage_seen = False

            def probe_stage(stage: str, path: Path | None) -> None:
                nonlocal stage_seen
                if stage != "after_temporary_fsync":
                    return
                stage_seen = True
                self.assertIsNotNone(path)
                assert path is not None
                with self.assertRaises(OSError):
                    with path.open("r+b"):
                        pass
                with self.assertRaises(OSError):
                    path.unlink()
                raise RuntimeError("stop after native sharing probe")

            with self.assertRaisesRegex(
                RuntimeError,
                "stop after native sharing probe",
            ):
                publish_bytes_noreplace(
                    parent,
                    "probe.wfgame",
                    b"exact-bytes",
                    expected_parent_identity=(info.st_dev, info.st_ino),
                    limit=11,
                    validate=lambda payload: self.assertEqual(payload, b"exact-bytes"),
                    publication_hook=probe_stage,
                )
            self.assertTrue(stage_seen)
            self.assertEqual(list(parent.iterdir()), [])

    def test_package_file_verification_rejects_symlinks_and_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-links-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                package_path = root / "puzzle.wfgame"
                package_game(game, package_path).close()

            hardlink = root / "hardlink.wfgame"
            os.link(package_path, hardlink)
            try:
                with self.assertRaisesRegex(
                    WorldForgeGamePackageError,
                    "^game_package_file_invalid:",
                ):
                    verify_game_package(hardlink)
            finally:
                hardlink.unlink()

            symlink = root / "symlink.wfgame"
            try:
                symlink.symlink_to(package_path)
            except OSError as exc:
                if os.name != "nt":
                    self.fail(f"symlink creation is unavailable: {exc}")
            else:
                with self.assertRaisesRegex(
                    WorldForgeGamePackageError,
                    "^game_package_file_invalid:",
                ):
                    verify_game_package(symlink)

            if os.name == "nt":
                junction_target = root / "junction-target"
                junction_target.mkdir()
                junction = root / "junction.wfgame"
                created = subprocess.run(
                    [
                        "cmd.exe",
                        "/d",
                        "/s",
                        "/c",
                        f'mklink /J "{junction}" "{junction_target}"',
                    ],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(created.returncode, 0, created.stderr)
                try:
                    with self.assertRaisesRegex(
                        WorldForgeGamePackageError,
                        "^game_package_file_invalid:",
                    ):
                        verify_game_package(junction)
                finally:
                    os.rmdir(junction)

    def test_generated_packager_matches_authoritative_bytes_and_rejects_in_tree_output(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-launcher-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                expected = build_game_package_from_standalone(game)
                release = root / "release"
                release.mkdir()
                destination = release / "puzzle.wfgame"
                environment = {
                    key: value
                    for key, value in os.environ.items()
                    if key not in {"PYTHONHOME", "PYTHONPATH"}
                }
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(game / "scripts/package_game.py"),
                        str(destination),
                    ],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(destination.read_bytes(), expected.archive_bytes)
                self.assertEqual(
                    json.loads(result.stdout)["archive_sha256"],
                    expected.archive_sha256,
                )

                forbidden = game / "game.wfgame"
                rejected = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(game / "scripts/package_game.py"),
                        str(forbidden),
                    ],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(rejected.returncode, 1)
                self.assertIn("game_package_destination_invalid", rejected.stderr)
                self.assertFalse(forbidden.exists())

                namespace = runpy.run_path(
                    str(game / "scripts/package_game.py"),
                    run_name="generated_game_packager_test",
                )
                publish = namespace["_publish"]
                self.assertTrue(callable(publish))

                def verifier(payload: bytes):
                    self.assertEqual(payload, expected.archive_bytes)
                    return verify_game_package_bytes(payload)

                def assert_clean(name: str, invoke: Callable[[Path], None]) -> None:
                    failed = release / f"{name}.wfgame"
                    with self.assertRaises((OSError, ValueError)):
                        invoke(failed)
                    self.assertFalse(failed.exists())
                    self.assertEqual(
                        list(release.glob(f".{failed.name}.game-package-stage-*")),
                        [],
                    )

                write_fault_seen = False

                def fail_write(_target, _payload: bytes) -> None:
                    nonlocal write_fault_seen
                    write_fault_seen = True
                    raise OSError("injected generated package write failure")

                with mock.patch(
                    "gamepack_runtime.persistence_io._write_all",
                    side_effect=fail_write,
                ):
                    assert_clean(
                        "write-failure",
                        lambda output: publish(output, expected.archive_bytes, verifier),
                    )
                self.assertTrue(write_fault_seen)

                real_fsync = os.fsync
                fsync_calls = 0

                def fail_first_fsync(descriptor: int) -> None:
                    nonlocal fsync_calls
                    fsync_calls += 1
                    if fsync_calls == 1:
                        raise OSError("injected generated package fsync failure")
                    real_fsync(descriptor)

                with mock.patch("os.fsync", side_effect=fail_first_fsync):
                    assert_clean(
                        "fsync-failure",
                        lambda output: publish(output, expected.archive_bytes, verifier),
                    )
                self.assertGreaterEqual(fsync_calls, 2)

                real_fstat = os.fstat
                fstat_calls = 0
                seal_fault_seen = False

                def fail_seal(descriptor: int):
                    nonlocal fstat_calls, seal_fault_seen
                    fstat_calls += 1
                    result = real_fstat(descriptor)
                    if stat.S_ISREG(result.st_mode) and result.st_size == len(
                        expected.archive_bytes
                    ):
                        seal_fault_seen = True
                        return types.SimpleNamespace(
                            st_dev=result.st_dev,
                            st_ino=result.st_ino,
                            st_mode=result.st_mode,
                            st_nlink=result.st_nlink,
                            st_size=result.st_size + 1,
                        )
                    return result

                with mock.patch("os.fstat", side_effect=fail_seal):
                    assert_clean(
                        "seal-failure",
                        lambda output: publish(output, expected.archive_bytes, verifier),
                    )
                self.assertGreaterEqual(fstat_calls, 3)
                self.assertTrue(seal_fault_seen)

                verification_seen = False

                class MismatchedVerification:
                    archive_bytes = b"not-the-package"

                    def close(self) -> None:
                        return None

                def mismatched_verifier(_payload: bytes) -> MismatchedVerification:
                    nonlocal verification_seen
                    verification_seen = True
                    return MismatchedVerification()

                assert_clean(
                    "verification-failure",
                    lambda output: publish(
                        output,
                        expected.archive_bytes,
                        mismatched_verifier,
                    ),
                )
                self.assertTrue(verification_seen)

                link_seen = False

                def fail_link(*_args, **_kwargs) -> None:
                    nonlocal link_seen
                    link_seen = True
                    raise OSError("injected generated package link failure")

                with mock.patch(
                    "gamepack_runtime.persistence_io._linux_link_descriptor_no_replace",
                    side_effect=fail_link,
                ):
                    assert_clean(
                        "link-failure",
                        lambda output: publish(output, expected.archive_bytes, verifier),
                    )
                self.assertTrue(link_seen)

                from gamepack_runtime import persistence_io

                real_directory_fsync = persistence_io._fsync_retained_ancestry
                directory_fsync_calls = 0

                def fail_first_directory_fsync(parent) -> None:
                    nonlocal directory_fsync_calls
                    directory_fsync_calls += 1
                    if directory_fsync_calls == 1:
                        raise OSError("injected generated package directory fsync failure")
                    real_directory_fsync(parent)

                with mock.patch(
                    "gamepack_runtime.persistence_io._fsync_retained_ancestry",
                    side_effect=fail_first_directory_fsync,
                ):
                    assert_clean(
                        "directory-fsync-failure",
                        lambda output: publish(output, expected.archive_bytes, verifier),
                    )
                self.assertEqual(directory_fsync_calls, 1)

    def test_archive_publication_is_exclusive_and_cleans_failed_temporary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-publish-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                release = root / "release"
                release.mkdir()
                destination = release / "puzzle.wfgame"
                barrier = threading.Barrier(2)

                def publish() -> tuple[str, str]:
                    barrier.wait()
                    try:
                        verified = package_game(game, destination)
                    except WorldForgeGamePackageError as exc:
                        return "error", exc.reason_code
                    try:
                        return "success", verified.archive_sha256
                    finally:
                        verified.close()

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(lambda _index: publish(), range(2)))
                self.assertEqual(
                    [status for status, _detail in results].count("success"),
                    1,
                )
                self.assertEqual(
                    [status for status, _detail in results].count("error"),
                    1,
                )
                self.assertIn(
                    next(detail for status, detail in results if status == "error"),
                    {
                        "game_package_destination_exists",
                        "game_package_publication_conflict",
                        "game_package_publication_failed",
                    },
                )
                verify_game_package(destination).close()

                transient = release / "transient.wfgame"
                destination_linked = threading.Event()
                finish_publication = threading.Event()

                def pause_after_link(stage: str, _path: Path | None) -> None:
                    if stage == "after_destination_link":
                        destination_linked.set()
                        if not finish_publication.wait(timeout=30):
                            raise RuntimeError("timed out waiting to finish publication")

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    winner = executor.submit(
                        package_game,
                        game,
                        transient,
                        _publication_hook=pause_after_link,
                    )
                    self.assertTrue(destination_linked.wait(timeout=30))
                    try:
                        with self.assertRaisesRegex(
                            WorldForgeGamePackageError,
                            "^game_package_(?:publication_conflict|destination_exists):",
                        ):
                            package_game(game, transient)
                    finally:
                        finish_publication.set()
                    winner.result().close()
                verify_game_package(transient).close()

                failed = release / "failed.wfgame"

                def fail_after_temporary(stage: str, _path: Path | None) -> None:
                    if stage == "after_temporary_fsync":
                        raise RuntimeError("simulated package crash")

                with self.assertRaisesRegex(RuntimeError, "simulated package crash"):
                    package_game(
                        game,
                        failed,
                        _publication_hook=fail_after_temporary,
                    )
                self.assertFalse(failed.exists())
                self.assertEqual(
                    list(release.glob(f".{failed.name}.game-package-stage-*")),
                    [],
                )

    def test_publishers_cleanup_through_retained_parent_after_parent_swap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-parent-swap-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                expected = build_game_package_from_standalone(game)
                namespace = runpy.run_path(
                    str(game / "scripts/package_game.py"),
                    run_name="generated_game_packager_parent_swap_test",
                )
                generated_publish = namespace["_publish"]
                self.assertTrue(callable(generated_publish))

                def verifier(payload: bytes):
                    return verify_game_package_bytes(payload)

                for publisher in ("forge", "generated"):
                    for stage in ("after_temporary_fsync", "after_destination_link"):
                        with self.subTest(publisher=publisher, stage=stage):
                            parent = root / f"{publisher}-{stage}"
                            displaced = root / f"{parent.name}-displaced"
                            parent.mkdir()
                            destination = parent / "probe.wfgame"
                            swap_blocked = False
                            swapped = False

                            def swap_parent(
                                current_stage: str,
                                _path: Path | None,
                                *,
                                expected_stage: str = stage,
                                parent_path: Path = parent,
                                displaced_path: Path = displaced,
                            ) -> None:
                                nonlocal swap_blocked, swapped
                                if current_stage != expected_stage:
                                    return
                                try:
                                    parent_path.rename(displaced_path)
                                except OSError:
                                    if os.name != "nt":
                                        raise
                                    swap_blocked = True
                                    return
                                swapped = True
                                parent_path.mkdir()
                                (parent_path / "foreign.txt").write_text(
                                    "replacement",
                                    encoding="utf-8",
                                )

                            def invoke(
                                *,
                                publisher_kind: str = publisher,
                                destination_path: Path = destination,
                            ):
                                if publisher_kind == "forge":
                                    return package_game(
                                        game,
                                        destination_path,
                                        _publication_hook=swap_parent,
                                    )
                                return generated_publish(
                                    destination_path,
                                    expected.archive_bytes,
                                    verifier,
                                    swap_parent,
                                )

                            if os.name == "nt":
                                try:
                                    result = invoke()
                                except (OSError, RuntimeError, ValueError):
                                    self.assertTrue(swapped)
                                else:
                                    self.assertTrue(swap_blocked)
                                    if hasattr(result, "close"):
                                        result.close()
                                    self.assertTrue(destination.is_file())
                                    verify_game_package(destination).close()
                            else:
                                with self.assertRaises(
                                    (OSError, RuntimeError, ValueError),
                                ):
                                    invoke()
                            if swapped:
                                self.assertTrue(swapped)
                                self.assertFalse(destination.exists())
                                self.assertEqual(
                                    (parent / "foreign.txt").read_text(encoding="utf-8"),
                                    "replacement",
                                )
                                self.assertFalse((displaced / destination.name).exists())
                            for directory in (parent, displaced):
                                if directory.exists():
                                    self.assertEqual(
                                        list(
                                            directory.glob(
                                                f".{destination.name}.game-package-stage-*"
                                            )
                                        ),
                                        [],
                                    )

    def test_retained_publication_api_rejects_non_component_names_before_io(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-retained-names-") as temporary:
            root = Path(temporary)
            parent_path = root / "release"
            parent_path.mkdir()
            parent_info = parent_path.stat()
            invalid_names = (
                "",
                ".",
                "..",
                "../escape.wfgame",
                "nested/escape.wfgame",
                r"nested\escape.wfgame",
            )
            with mock.patch(
                "gamepack_runtime.persistence_io._create_temporary_entry",
            ) as create_temporary:
                for name in invalid_names:
                    with self.subTest(name=name):
                        with self.assertRaisesRegex(
                            PersistenceIOError,
                            "^persistence_target_unsafe:",
                        ):
                            publish_bytes_noreplace(
                                parent_path,
                                name,
                                b"x",
                                expected_parent_identity=(
                                    parent_info.st_dev,
                                    parent_info.st_ino,
                                ),
                                limit=1,
                            )
            create_temporary.assert_not_called()
            self.assertEqual(list(root.iterdir()), [parent_path])
            self.assertEqual(list(parent_path.iterdir()), [])

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "requires Linux anonymous descriptor publication",
    )
    def test_byte_publication_has_no_posix_stage_name_and_reconciles_syscall_ambiguity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-byte-publish-") as temporary:
            root = Path(temporary)
            parent_path = root / "release"
            parent_path.mkdir()
            parent_info = parent_path.stat()
            parent_identity = parent_info.st_dev, parent_info.st_ino
            payload = b"owned-package-bytes"

            stage_paths: list[Path | None] = []

            def fail_without_named_stage(stage: str, path: Path | None) -> None:
                if stage == "after_temporary_fsync":
                    stage_paths.append(path)
                    raise RuntimeError("stop before publication")

            with self.assertRaisesRegex(RuntimeError, "stop before publication"):
                publish_bytes_noreplace(
                    parent_path,
                    "prelink.wfgame",
                    payload,
                    expected_parent_identity=parent_identity,
                    limit=len(payload),
                    validate=lambda actual: self.assertEqual(actual, payload),
                    publication_hook=fail_without_named_stage,
                )
            self.assertEqual(stage_paths, [None])
            self.assertEqual(list(parent_path.iterdir()), [])

            from gamepack_runtime import persistence_io

            def collide_with_winner(
                _source_descriptor: int,
                destination_descriptor: int,
                destination_name: str,
            ) -> None:
                winner = os.open(
                    destination_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o644,
                    dir_fd=destination_descriptor,
                )
                try:
                    self.assertEqual(os.write(winner, b"winner"), 6)
                finally:
                    os.close(winner)
                raise FileExistsError(
                    errno.EEXIST,
                    "destination already exists",
                    destination_name,
                )

            with (
                mock.patch(
                    "gamepack_runtime.persistence_io._linux_link_descriptor_no_replace",
                    side_effect=collide_with_winner,
                ),
                mock.patch(
                    "gamepack_runtime.persistence_io._linux_claim_and_remove_owned_entry",
                ) as claim_winner,
                self.assertRaises(FileExistsError),
            ):
                publish_bytes_noreplace(
                    parent_path,
                    "collision.wfgame",
                    payload,
                    expected_parent_identity=parent_identity,
                    limit=len(payload),
                    validate=lambda actual: self.assertEqual(actual, payload),
                )
            claim_winner.assert_not_called()
            self.assertEqual(
                (parent_path / "collision.wfgame").read_bytes(),
                b"winner",
            )

            real_link = persistence_io._linux_link_descriptor_no_replace

            def link_then_interrupt(*args, **kwargs) -> None:
                real_link(*args, **kwargs)
                raise KeyboardInterrupt("after link")

            with (
                mock.patch(
                    "gamepack_runtime.persistence_io._linux_link_descriptor_no_replace",
                    side_effect=link_then_interrupt,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "after link"),
            ):
                publish_bytes_noreplace(
                    parent_path,
                    "link-ambiguous.wfgame",
                    payload,
                    expected_parent_identity=parent_identity,
                    limit=len(payload),
                    validate=lambda actual: self.assertEqual(actual, payload),
                )
            self.assertFalse((parent_path / "link-ambiguous.wfgame").exists())
            self.assertEqual(
                list(parent_path.glob(".game-package-delete-*")),
                [],
            )

            real_rename = persistence_io._linux_rename_name_noreplace
            rename_interrupted = False

            def rename_then_interrupt(*args, **kwargs) -> None:
                nonlocal rename_interrupted
                real_rename(*args, **kwargs)
                if not rename_interrupted:
                    rename_interrupted = True
                    raise KeyboardInterrupt("after cleanup claim")

            def fail_after_link(stage: str, _path: Path | None) -> None:
                if stage == "after_destination_link":
                    raise RuntimeError("primary publication failure")

            with (
                mock.patch(
                    "gamepack_runtime.persistence_io._linux_rename_name_noreplace",
                    side_effect=rename_then_interrupt,
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "primary publication failure",
                ) as raised,
            ):
                publish_bytes_noreplace(
                    parent_path,
                    "claim-ambiguous.wfgame",
                    payload,
                    expected_parent_identity=parent_identity,
                    limit=len(payload),
                    validate=lambda actual: self.assertEqual(actual, payload),
                    publication_hook=fail_after_link,
                )
            self.assertTrue(rename_interrupted)
            self.assertFalse((parent_path / "claim-ambiguous.wfgame").exists())
            self.assertEqual(
                list(parent_path.glob(".game-package-delete-*")),
                [],
            )
            self.assertTrue(
                any(
                    "after cleanup claim" in note
                    for note in getattr(raised.exception, "__notes__", ())
                )
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "requires Linux descriptor-relative publication",
    )
    def test_publishers_preserve_replaced_names_and_clean_other_owned_links(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-entry-swap-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                expected = build_game_package_from_standalone(game)
                namespace = runpy.run_path(
                    str(game / "scripts/package_game.py"),
                    run_name="generated_game_packager_entry_swap_test",
                )
                generated_publish = namespace["_publish"]

                def verifier(payload: bytes):
                    return verify_game_package_bytes(payload)

                for publisher in ("forge", "generated"):
                    for stage in (
                        "after_temporary_fsync",
                        "after_destination_link",
                    ):
                        with self.subTest(publisher=publisher, stage=stage):
                            parent = root / f"entry-{publisher}-{stage}"
                            parent.mkdir()
                            destination = parent / "probe.wfgame"
                            replaced_path: Path | None = None
                            anonymous_stage_seen = False

                            def replace_entry(
                                current_stage: str,
                                path: Path | None,
                                *,
                                expected_stage: str = stage,
                            ) -> None:
                                nonlocal anonymous_stage_seen, replaced_path
                                if current_stage != expected_stage:
                                    return
                                if current_stage == "after_temporary_fsync":
                                    self.assertIsNone(path)
                                    anonymous_stage_seen = True
                                    raise RuntimeError("anonymous stage has no namespace entry")
                                assert path is not None
                                path.unlink()
                                path.write_bytes(b"foreign")
                                replaced_path = path

                            def invoke(
                                *,
                                publisher_kind: str = publisher,
                                destination_path: Path = destination,
                            ):
                                if publisher_kind == "forge":
                                    return package_game(
                                        game,
                                        destination_path,
                                        _publication_hook=replace_entry,
                                    )
                                return generated_publish(
                                    destination_path,
                                    expected.archive_bytes,
                                    verifier,
                                    replace_entry,
                                )

                            with self.assertRaises(
                                (OSError, RuntimeError, ValueError),
                            ):
                                invoke()
                            if stage == "after_temporary_fsync":
                                self.assertTrue(anonymous_stage_seen)
                                self.assertIsNone(replaced_path)
                                self.assertFalse(destination.exists())
                            else:
                                assert replaced_path is not None
                                self.assertEqual(replaced_path.read_bytes(), b"foreign")
                                self.assertEqual(destination.read_bytes(), b"foreign")
                            self.assertEqual(
                                [
                                    path
                                    for path in parent.glob(
                                        f".{destination.name}.game-package-stage-*"
                                    )
                                    if path != replaced_path
                                ],
                                [],
                            )

    def test_publishers_preserve_primary_and_cleanup_on_close_faults(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-publisher-close-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                expected = build_game_package_from_standalone(game)
                namespace = runpy.run_path(
                    str(game / "scripts/package_game.py"),
                    run_name="generated_game_packager_close_fault_test",
                )
                generated_publish = namespace["_publish"]

                def verifier(payload: bytes):
                    return verify_game_package_bytes(payload)

                for publisher in ("forge", "generated"):
                    for mode in ("close_then_raise", "pure_raise"):
                        with self.subTest(publisher=publisher, mode=mode):
                            parent = root / f"close-{publisher}-{mode}"
                            parent.mkdir()
                            destination = parent / "probe.wfgame"
                            close_state = {"active": False, "seen": False}
                            real_close = os.close

                            def fail_publication(
                                current_stage: str,
                                _path: Path | None,
                                *,
                                state: dict[str, bool] = close_state,
                            ) -> None:
                                if current_stage == "after_temporary_fsync":
                                    state["active"] = True
                                    raise RuntimeError("primary publication failure")

                            def fail_owned_close(
                                descriptor: int,
                                *,
                                close_mode: str = mode,
                                actual_close: Callable[[int], None] = real_close,
                                state: dict[str, bool] = close_state,
                            ) -> None:
                                if state["active"] and not state["seen"]:
                                    try:
                                        info = os.fstat(descriptor)
                                    except OSError:
                                        actual_close(descriptor)
                                        return
                                    if stat.S_ISREG(info.st_mode) and info.st_size == len(
                                        expected.archive_bytes
                                    ):
                                        state["seen"] = True
                                        if close_mode == "close_then_raise":
                                            actual_close(descriptor)
                                        raise OSError(f"injected {close_mode}")
                                actual_close(descriptor)

                            def invoke(
                                *,
                                publisher_kind: str = publisher,
                                destination_path: Path = destination,
                            ):
                                if publisher_kind == "forge":
                                    return package_game(
                                        game,
                                        destination_path,
                                        _publication_hook=fail_publication,
                                    )
                                return generated_publish(
                                    destination_path,
                                    expected.archive_bytes,
                                    verifier,
                                    fail_publication,
                                )

                            with (
                                mock.patch(
                                    "gamepack_runtime.persistence_io.os.close",
                                    side_effect=fail_owned_close,
                                ),
                                self.assertRaisesRegex(
                                    RuntimeError,
                                    "primary publication failure",
                                ) as raised,
                            ):
                                invoke()
                            self.assertTrue(close_state["seen"])
                            self.assertFalse(destination.exists())
                            self.assertEqual(
                                list(parent.glob(f".{destination.name}.game-package-stage-*")),
                                [],
                            )
                            self.assertTrue(
                                any(
                                    f"injected {mode}" in note
                                    for note in getattr(
                                        raised.exception,
                                        "__notes__",
                                        (),
                                    )
                                )
                            )

    def test_extraction_is_exclusive_under_concurrency(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-extract-race-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                package_path = root / "puzzle.wfgame"
                package_game(game, package_path).close()
            destination = root / "extracted"
            barrier = threading.Barrier(2)

            def extract() -> tuple[str, str]:
                barrier.wait()
                try:
                    verified = extract_game_package(package_path, destination)
                except WorldForgeGamePackageError as exc:
                    return "error", exc.reason_code
                try:
                    return "success", verified.manifest["content_hash"]
                finally:
                    verified.close()

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: extract(), range(2)))
            self.assertEqual(
                [status for status, _detail in results].count("success"),
                1,
            )
            self.assertEqual(
                [status for status, _detail in results].count("error"),
                1,
            )
            self.assertIn(
                next(detail for status, detail in results if status == "error"),
                {
                    "game_package_destination_exists",
                    "game_package_extraction_busy",
                },
            )
            self.assertTrue(destination.is_dir())

    def test_destination_lock_preserves_body_error_through_release_cleanup(self) -> None:
        from worldforge import game_package as game_package_module

        class CleanupSensitiveLock:
            def __enter__(self):
                return None

            def __exit__(self, _kind, error, _traceback):
                if error is None:
                    raise PersistenceIOError("injected lock release failure")
                error.add_note("injected lock release cleanup note")
                return False

        def cleanup_sensitive_lock(_path: Path) -> CleanupSensitiveLock:
            return CleanupSensitiveLock()

        with (
            mock.patch.object(
                game_package_module,
                "held_persistence_lock",
                cleanup_sensitive_lock,
            ),
            self.assertRaisesRegex(RuntimeError, "primary extraction failure") as raised,
        ):
            with game_package_module._destination_lock(Path("/unused/game")):
                raise RuntimeError("primary extraction failure")
        self.assertIn(
            "injected lock release cleanup note",
            getattr(raised.exception, "__notes__", ()),
        )

    def test_transplanted_journal_stage_and_parent_swap_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-transplant-") as temporary:
            root = Path(temporary)
            with _standalone("branching-narrative", root) as (game, _verified):
                package_path = root / "narrative.wfgame"
                package_game(game, package_path).close()

            original_parent = root / "original"
            transplanted_parent = root / "transplanted"
            original_parent.mkdir()
            transplanted_parent.mkdir()
            original = original_parent / "game"

            def crash_ready(stage: str, _path: Path | None) -> None:
                if stage == "after_ready_journal_written":
                    raise RuntimeError("simulated crash")

            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                extract_game_package(
                    package_path,
                    original,
                    _publication_hook=crash_ready,
                )
            journal = original_parent / ".game.game-package-extraction.journal.json"
            stage = next(original_parent.glob(".game.game-package-stage-*"))
            transplanted = transplanted_parent / "game"
            copied_stage = transplanted_parent / stage.name
            shutil.copytree(stage, copied_stage)
            copied_journal = transplanted_parent / ".game.game-package-extraction.journal.json"
            shutil.copyfile(journal, copied_journal)
            with self.assertRaisesRegex(
                WorldForgeGamePackageError,
                "^game_package_journal_invalid:",
            ):
                recover_game_package_extraction(transplanted)
            self.assertTrue(copied_stage.is_dir())
            self.assertTrue(copied_journal.is_file())

            parent = root / "parent-swap"
            parent.mkdir()
            destination = parent / "game"
            displaced = root / "parent-swap-displaced"

            def swap_parent(stage_name: str, _path: Path | None) -> None:
                if stage_name == "before_destination_publish":
                    parent.rename(displaced)
                    parent.mkdir()

            with self.assertRaisesRegex(
                WorldForgeGamePackageError,
                "^game_package_(?:publication_indeterminate|extraction_lock_failed):",
            ):
                extract_game_package(
                    package_path,
                    destination,
                    _publication_hook=swap_parent,
                )
            self.assertFalse(destination.exists())

    def test_rollback_preserves_foreign_files_and_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-game-package-foreign-") as temporary:
            root = Path(temporary)
            with _standalone("abstract-puzzle", root) as (game, _verified):
                package_path = root / "puzzle.wfgame"
                package_game(game, package_path).close()

            for kind in ("file", "directory"):
                with self.subTest(kind=kind):
                    destination = root / f"rollback-{kind}"

                    def crash_copying(stage: str, _path: Path | None) -> None:
                        if stage == "after_copying_journal_written":
                            raise RuntimeError("simulated crash")

                    with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                        extract_game_package(
                            package_path,
                            destination,
                            _publication_hook=crash_copying,
                        )
                    stage = next(root.glob(f".{destination.name}.game-package-stage-*"))
                    foreign = stage / "foreign"
                    if kind == "file":
                        foreign.write_bytes(b"foreign")
                    else:
                        foreign.mkdir()
                    with self.assertRaisesRegex(
                        WorldForgeGamePackageError,
                        "^game_package_rollback_ambiguous:",
                    ):
                        rollback_game_package_extraction(destination)
                    self.assertTrue(foreign.exists())
                    self.assertTrue(stage.is_dir())
                    self.assertTrue(
                        root.joinpath(
                            f".{destination.name}.game-package-extraction.journal.json"
                        ).is_file()
                    )


if __name__ == "__main__":
    unittest.main()
