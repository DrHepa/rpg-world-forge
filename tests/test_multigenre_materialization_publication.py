from __future__ import annotations

import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import worldforge.game_materialization_bundle as game_materialization_bundle
import worldforge.game_runtime_bundle as game_runtime_bundle
import worldforge.generic_runtime as generic_runtime
from tests.test_multigenre_materialization_contracts import _runtime_bundle
from worldforge.directory_publish import (
    DirectoryPublishError,
    RetainedStageWriter,
    create_retained_stage,
)
from worldforge.game_materialization_bundle import (
    GAME_MATERIALIZATION_BUNDLE_MANIFEST,
    GameMaterializationBundleError,
    build_game_materialization_bundle,
    build_game_materialization_bundle_manifest,
    recover_game_materialization_bundle,
    rollback_game_materialization_bundle,
)


class GameMaterializationBundlePublicationTests(unittest.TestCase):
    def test_verifier_accepts_stage_capability_only_from_its_writer(self) -> None:
        class _CaptureStopped(RuntimeError):
            pass

        with tempfile.TemporaryDirectory(prefix="wf-materialization-stage-scope-") as temporary:
            root = Path(temporary) / "stage"
            observed: list[object | None] = []

            def stop_capture(
                _root: Path,
                *,
                hook: object | None,
                retained_root_fd: int | None = None,
                stage_capability: object | None = None,
            ) -> object:
                self.assertIsNone(hook)
                self.assertIsNone(retained_root_fd)
                observed.append(stage_capability)
                raise _CaptureStopped

            forged = object.__new__(RetainedStageWriter)
            forged.stage = root
            forged.require_binding = lambda: None  # type: ignore[method-assign]
            with (
                mock.patch.object(
                    game_materialization_bundle,
                    "_capture_bundle_tree",
                    side_effect=stop_capture,
                ),
                self.assertRaisesRegex(
                    GameMaterializationBundleError,
                    "game_materialization_bundle_stage_capability_invalid",
                ),
            ):
                game_materialization_bundle.verify_game_materialization_bundle(
                    root,
                    _retained_stage_writer=forged,
                )

            with create_retained_stage(root) as writer:
                with (
                    mock.patch.object(
                        game_materialization_bundle,
                        "_capture_bundle_tree",
                        side_effect=stop_capture,
                    ),
                    self.assertRaises(_CaptureStopped),
                ):
                    game_materialization_bundle.verify_game_materialization_bundle(
                        root,
                        _retained_stage_writer=writer,
                    )
                with self.assertRaisesRegex(
                    GameMaterializationBundleError,
                    "game_materialization_bundle_stage_capability_invalid",
                ):
                    game_materialization_bundle.verify_game_materialization_bundle(
                        root / "crossed",
                        _retained_stage_writer=writer,
                    )

            self.assertEqual(1, len(observed))
            capability = observed[0]
            self.assertIsInstance(
                capability,
                generic_runtime._RuntimeStageReadCapability,  # noqa: SLF001
            )
            assert isinstance(capability, generic_runtime._RuntimeStageReadCapability)  # noqa: SLF001
            self.assertEqual(root, capability.root)
            share_mode = generic_runtime._WindowsRuntimeTreeApi._share_mode_for(  # noqa: SLF001
                capability
            )
            self.assertEqual(0x00000003, share_mode)
            self.assertEqual(0, share_mode & 0x00000004)

            with self.assertRaisesRegex(
                GameMaterializationBundleError,
                "game_materialization_bundle_stage_capability_invalid",
            ):
                game_materialization_bundle.verify_game_materialization_bundle(
                    root,
                    _retained_stage_writer=writer,
                )

            with (
                mock.patch.object(
                    game_materialization_bundle,
                    "_capture_bundle_tree",
                    side_effect=stop_capture,
                ),
                self.assertRaises(_CaptureStopped),
            ):
                game_materialization_bundle.verify_game_materialization_bundle(root)
            self.assertIsNone(observed[-1])

            with self.assertRaisesRegex(
                GameMaterializationBundleError,
                "game_materialization_bundle_stage_capability_invalid",
            ):
                game_materialization_bundle.verify_game_materialization_bundle(
                    root,
                    _retained_stage_writer=object(),  # type: ignore[arg-type]
                )

    def test_verifier_rechecks_retained_stage_binding_after_capture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialization-stage-mutation-") as temporary:
            root = Path(temporary) / "stage"
            mutation_checks = 0
            real_authority = RetainedStageWriter._require_active_binding  # noqa: SLF001

            def reject_post_capture_mutation(
                writer: object,
                *,
                expected_stage: Path,
            ) -> None:
                nonlocal mutation_checks
                mutation_checks += 1
                real_authority(writer, expected_stage=expected_stage)
                if mutation_checks == 2:
                    raise DirectoryPublishError("retained stage binding changed")

            def capture_with_post_check(
                _root: Path,
                *,
                hook: object | None,
                retained_root_fd: int | None = None,
                stage_capability: object | None = None,
            ) -> object:
                self.assertIsNone(hook)
                self.assertIsNone(retained_root_fd)
                assert isinstance(stage_capability, generic_runtime._RuntimeStageReadCapability)  # noqa: SLF001
                stage_capability.require_binding()
                raise AssertionError("post-capture mutation was not rejected")

            with create_retained_stage(root) as writer:
                with (
                    mock.patch.object(
                        RetainedStageWriter,
                        "_require_active_binding",
                        side_effect=reject_post_capture_mutation,
                    ),
                    mock.patch.object(
                        game_materialization_bundle,
                        "_capture_bundle_tree",
                        side_effect=capture_with_post_check,
                    ),
                    self.assertRaisesRegex(
                        GameMaterializationBundleError,
                        "game_materialization_bundle_stage_capability_invalid",
                    ),
                ):
                    game_materialization_bundle.verify_game_materialization_bundle(
                        root,
                        _retained_stage_writer=writer,
                    )
            self.assertEqual(2, mutation_checks)

    def test_verifier_scopes_nested_runtime_capture_to_retained_subroot(self) -> None:
        class _NestedStopped(RuntimeError):
            pass

        with tempfile.TemporaryDirectory(prefix="wf-materialization-nested-stage-") as temporary:
            root = Path(temporary)
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:
                manifest, payloads = (
                    game_materialization_bundle.build_game_materialization_bundle_manifest(
                        runtime_bundle_root=runtime_bundle.root,
                    )
                )
            stage = root / "stage"
            manifest_payload = game_materialization_bundle.serialize_game_materialization_bundle(
                manifest
            )
            all_files = {
                GAME_MATERIALIZATION_BUNDLE_MANIFEST: manifest_payload,
                **payloads,
            }
            tree = game_runtime_bundle._PhysicalTree(  # noqa: SLF001
                root_state=(1, 2, 0o40700, 1, 0, 10, 10),
                files=frozenset(all_files),
                directories=frozenset(
                    game_materialization_bundle._expected_directories(tuple(all_files))  # noqa: SLF001
                ),
            )
            observed: list[tuple[Path, object | None]] = []

            def stop_nested(root_arg: str | Path, **kwargs: object) -> object:
                observed.append(
                    (
                        Path(os.path.abspath(os.fspath(root_arg))),
                        kwargs.get("_stage_capability"),
                    )
                )
                raise _NestedStopped

            with create_retained_stage(stage) as writer:
                with (
                    mock.patch.object(
                        game_materialization_bundle,
                        "_capture_bundle_tree",
                        return_value=(all_files, tree),
                    ),
                    mock.patch.object(
                        game_materialization_bundle,
                        "_verify_game_runtime_bundle_with_stage_capability",
                        side_effect=stop_nested,
                    ),
                    self.assertRaises(_NestedStopped),
                ):
                    game_materialization_bundle.verify_game_materialization_bundle(
                        stage,
                        _retained_stage_writer=writer,
                    )

            self.assertEqual(1, len(observed))
            nested_root, capability = observed[0]
            self.assertEqual(stage / "runtime-bundle", nested_root)
            self.assertIsInstance(
                capability,
                generic_runtime._RuntimeStageReadCapability,  # noqa: SLF001
            )
            assert isinstance(capability, generic_runtime._RuntimeStageReadCapability)  # noqa: SLF001
            self.assertEqual(nested_root, capability.root)

    def test_runtime_public_verifier_rejects_raw_stage_capability(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-runtime-raw-cap-public-") as temporary:
            root = Path(temporary) / "runtime-bundle"
            capability = generic_runtime._create_runtime_stage_read_capability(  # noqa: SLF001
                root=root,
                require_binding=lambda: None,
            )

            def reject_raw_capability(*_args: object, **_kwargs: object) -> object:
                raise AssertionError("raw stage capability reached public runtime capture")

            with (
                mock.patch.object(
                    game_runtime_bundle,
                    "_capture_bundle_tree",
                    side_effect=reject_raw_capability,
                ),
                self.assertRaises(TypeError),
            ):
                game_runtime_bundle.verify_game_runtime_bundle(
                    root,
                    _stage_capability=capability,
                )

    def test_nested_runtime_helper_requires_active_outer_retained_writer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialization-nested-helper-") as temporary:
            root = Path(temporary)
            outer_stage = root / "stage"
            nested_root = outer_stage / "runtime-bundle"

            forged = object.__new__(RetainedStageWriter)
            forged.stage = outer_stage
            forged.require_binding = lambda: None  # type: ignore[method-assign]

            for writer, expected_stage in (
                (forged, outer_stage),
                (object(), outer_stage),
            ):
                with self.subTest(writer=type(writer).__name__):
                    with self.assertRaisesRegex(
                        GameMaterializationBundleError,
                        "game_materialization_bundle_stage_capability_invalid",
                    ):
                        game_materialization_bundle._verify_nested_runtime_bundle_from_retained_materialization_stage(  # noqa: SLF001
                            nested_root,
                            expected_content_hash="0" * 64,
                            expected_outer_stage=expected_stage,
                            _retained_stage_writer=writer,
                        )

            with create_retained_stage(outer_stage) as writer:
                with self.assertRaisesRegex(
                    GameMaterializationBundleError,
                    "game_materialization_bundle_stage_capability_invalid",
                ):
                    game_materialization_bundle._verify_nested_runtime_bundle_from_retained_materialization_stage(  # noqa: SLF001
                        nested_root,
                        expected_content_hash="0" * 64,
                        expected_outer_stage=outer_stage / "crossed",
                        _retained_stage_writer=writer,
                    )

            with self.assertRaisesRegex(
                GameMaterializationBundleError,
                "game_materialization_bundle_stage_capability_invalid",
            ):
                game_materialization_bundle._verify_nested_runtime_bundle_from_retained_materialization_stage(  # noqa: SLF001
                    nested_root,
                    expected_content_hash="0" * 64,
                    expected_outer_stage=outer_stage,
                    _retained_stage_writer=writer,
                )

    def test_publication_scopes_stage_capability_to_private_verification_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-materialization-stage-calls-") as temporary:
            root = Path(temporary)
            destination = root / "bundle"
            with _runtime_bundle("abstract-puzzle", root) as runtime_bundle:
                original = game_materialization_bundle.verify_game_materialization_bundle
                with mock.patch.object(
                    game_materialization_bundle,
                    "verify_game_materialization_bundle",
                    wraps=original,
                ) as verify_calls:
                    verified = game_materialization_bundle.build_game_materialization_bundle(
                        destination,
                        runtime_bundle_root=runtime_bundle.root,
                    )
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
