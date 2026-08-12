from __future__ import annotations

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.test_multigenre_materialization_contracts import _runtime_bundle
from worldforge.game_materialization_bundle import (
    GAME_MATERIALIZATION_BUNDLE_MANIFEST,
    GameMaterializationBundleError,
    build_game_materialization_bundle,
    build_game_materialization_bundle_manifest,
    recover_game_materialization_bundle,
    rollback_game_materialization_bundle,
)


class GameMaterializationBundlePublicationTests(unittest.TestCase):
    def test_concurrent_identical_publishers_fail_closed_then_converge_on_retry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialization-concurrent-") as temporary:
            root = Path(temporary)
            destination = root / "bundle"
            start = threading.Barrier(2)
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:

                def publish() -> tuple[str, object]:
                    start.wait(timeout=10)
                    try:
                        with build_game_materialization_bundle(
                            destination,
                            runtime_bundle_root=runtime_bundle.root,
                        ) as verified:
                            return "published", verified.manifest
                    except GameMaterializationBundleError as exc:
                        return exc.reason_code, exc.recovery_evidence

                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _index: publish(), range(2)))
                published = [value for status, value in results if status == "published"]
                busy = [
                    value
                    for status, value in results
                    if status == "game_materialization_bundle_publication_busy"
                ]
                self.assertEqual(1, len(published), results)
                self.assertEqual([{}], busy, results)
                retained_identity = (destination.stat().st_dev, destination.stat().st_ino)
                with build_game_materialization_bundle(
                    destination,
                    runtime_bundle_root=runtime_bundle.root,
                ) as retried:
                    self.assertEqual(published[0], retried.manifest)
                    self.assertEqual(retained_identity, retried.root_identity)
            self.assertTrue(destination.is_dir())

    def test_canonical_builder_derives_exact_runtime_implementation_and_locks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialization-canonical-") as temporary:
            root = Path(temporary)
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:
                manifest, payloads = build_game_materialization_bundle_manifest(
                    runtime_bundle_root=runtime_bundle.root,
                )
                first = build_game_materialization_bundle(
                    root / "first",
                    runtime_bundle_root=runtime_bundle.root,
                )
                second = build_game_materialization_bundle(
                    root / "second",
                    runtime_bundle_root=runtime_bundle.root,
                )
                try:
                    self.assertEqual(first.manifest, manifest)
                    self.assertEqual(second.manifest, manifest)
                    self.assertEqual(
                        first.read_bytes(GAME_MATERIALIZATION_BUNDLE_MANIFEST),
                        second.read_bytes(GAME_MATERIALIZATION_BUNDLE_MANIFEST),
                    )
                    self.assertEqual(len(manifest["platform_locks"]["locks"]), 4)
                    self.assertEqual(
                        manifest["runtime_implementation"]["content_hash"],
                        manifest["lineage"]["runtime_implementation_hash"],
                    )
                    self.assertEqual(
                        set(first.files) - {GAME_MATERIALIZATION_BUNDLE_MANIFEST},
                        set(payloads),
                    )
                finally:
                    first.close()
                    second.close()

    def test_publication_binds_parent_and_never_replaces_foreign_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialization-authority-") as temporary:
            root = Path(temporary)
            destination = root / "bundle"
            parent_info = root.stat()
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:
                verified = build_game_materialization_bundle(
                    destination,
                    runtime_bundle_root=runtime_bundle.root,
                    expected_parent_identity=(parent_info.st_dev, parent_info.st_ino),
                )
                verified.close()
                marker = destination / "foreign.bin"
                marker.write_bytes(b"foreign")
                with self.assertRaises(GameMaterializationBundleError):
                    build_game_materialization_bundle(
                        destination,
                        runtime_bundle_root=runtime_bundle.root,
                        expected_parent_identity=(parent_info.st_dev, parent_info.st_ino),
                    )
                self.assertEqual(marker.read_bytes(), b"foreign")

                crossed = root / "crossed"
                with self.assertRaisesRegex(
                    GameMaterializationBundleError,
                    "parent identity",
                ):
                    build_game_materialization_bundle(
                        crossed,
                        runtime_bundle_root=runtime_bundle.root,
                        expected_parent_identity=(parent_info.st_dev, parent_info.st_ino + 1),
                    )
                self.assertFalse(crossed.exists())

    def test_crash_before_publish_recovers_exact_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialization-recovery-") as temporary:
            root = Path(temporary)
            destination = root / "bundle"
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:

                def crash(event: str, _relative: str | None) -> None:
                    if event == "before_destination_publish":
                        raise RuntimeError("injected materialization crash")

                with self.assertRaisesRegex(RuntimeError, "injected materialization crash"):
                    build_game_materialization_bundle(
                        destination,
                        runtime_bundle_root=runtime_bundle.root,
                        _publication_hook=crash,
                    )

            journal = root / ".bundle.game-materialization-bundle.journal.json"
            self.assertTrue(journal.is_file())
            recovered = recover_game_materialization_bundle(destination)
            self.assertIsNotNone(recovered)
            assert recovered is not None
            try:
                self.assertEqual(recovered.manifest["state"], "materialization_ready")
            finally:
                recovered.close()
            self.assertFalse(journal.exists())
            self.assertEqual(
                rollback_game_materialization_bundle(root / "not-created"),
                {"status": "no_operation"},
            )

    @unittest.skipUnless(
        os.name in {"posix", "nt"},
        "requires a supported native publication platform",
    )
    def test_partial_stage_is_never_deleted_without_identity_bound_proof(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialization-partial-") as temporary:
            root = Path(temporary)
            destination = root / "bundle"
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:

                def crash(event: str, relative: str | None) -> None:
                    if (
                        event == "after_stage_file_write"
                        and relative == GAME_MATERIALIZATION_BUNDLE_MANIFEST
                    ):
                        raise RuntimeError("injected partial materialization crash")

                with self.assertRaisesRegex(
                    RuntimeError,
                    "injected partial materialization crash",
                ):
                    build_game_materialization_bundle(
                        destination,
                        runtime_bundle_root=runtime_bundle.root,
                        _publication_hook=crash,
                    )

            with self.assertRaises(GameMaterializationBundleError) as raised:
                rollback_game_materialization_bundle(destination)
            if os.name == "posix":
                self.assertEqual(
                    raised.exception.reason_code,
                    "game_materialization_bundle_rollback_recovery_required",
                )
                self.assertTrue(raised.exception.recovery_evidence)


if __name__ == "__main__":
    unittest.main()
