from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from unittest import mock

from isoworld.content.file_stat import descriptor_file_stat
from scripts.generate_generic_assetpack_schema import build_schema
from tests.test_m5_asset_io import _PosixBackedWindowsStageApi
from tests.test_multigenre_asset_processing import _build_processing_chain
from tests.test_multigenre_asset_production import _media_matrix_cases
from worldforge import asset_io as asset_io_module
from worldforge import directory_publish as directory_publish_module
from worldforge import generic_assetpack as assetpack_module
from worldforge.__main__ import _resolve_generic_assetpack_cli_source, main
from worldforge.creation_contracts import canonical_creation_hash
from worldforge.directory_publish import (
    DirectoryPublishError,
)
from worldforge.generic_asset_production import (
    GenericAssetProductionError,
    inspect_runtime_asset_bytes,
)
from worldforge.generic_assetpack import (
    GENERIC_ASSETPACK_FORMAT,
    GenericAssetpackError,
    build_generic_assetpack_manifest,
    recover_generic_assetpack,
    rollback_generic_assetpack,
    seal_generic_assetpack,
    serialize_generic_assetpack,
    validate_generic_assetpack_document,
    verify_generic_assetpack,
)

ROOT = Path(__file__).resolve().parents[1]


def _mutate_glb(
    payload: bytes,
    operations: list[dict[str, object]],
) -> bytes:
    if len(payload) < 20 or payload[:4] != b"glTF":
        raise AssertionError("GLB parity fixture is invalid")
    json_length, json_type = struct.unpack_from("<II", payload, 12)
    if json_type != 0x4E4F534A or 20 + json_length > len(payload):
        raise AssertionError("GLB parity fixture has no valid JSON chunk")
    document = json.loads(payload[20 : 20 + json_length].rstrip(b" \0").decode("utf-8"))
    for operation in operations:
        if operation.get("op") != "set":
            raise AssertionError("unsupported GLB parity operation")
        path = operation.get("path")
        if not isinstance(path, list) or not path:
            raise AssertionError("invalid GLB parity operation path")
        target = document
        for segment in path[:-1]:
            target = target[segment]
        target[path[-1]] = copy.deepcopy(operation.get("value"))
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded += b" " * (-len(encoded) % 4)
    tail = payload[20 + json_length :]
    total_length = 12 + 8 + len(encoded) + len(tail)
    return (
        struct.pack("<4sII", b"glTF", 2, total_length)
        + struct.pack("<II", len(encoded), 0x4E4F534A)
        + encoded
        + tail
    )


class GenericAssetpackTests(unittest.TestCase):
    def _fixture_source(self, fixture: str) -> dict[str, object]:
        return _resolve_generic_assetpack_cli_source(
            ROOT / "examples" / "multigenre-contracts" / fixture / "assets" / "manifest.json"
        )

    def _reseal_mutation(self, document: dict[str, object]) -> None:
        inventory = document.get("inventory")
        if isinstance(inventory, dict):
            inventory["content_hash"] = canonical_creation_hash(inventory)
        document["assetpack_id"] = assetpack_module._derived_assetpack_id(document)
        document["content_hash"] = canonical_creation_hash(document)

    def test_python_glb_validation_matches_shared_studio_parity_corpus(self) -> None:
        corpus = json.loads(
            (
                ROOT / "tests" / "fixtures" / "generic-assetpack" / "glb-parity-corpus.json"
            ).read_text(encoding="utf-8")
        )
        source = ROOT / corpus["source"]
        baseline = source.read_bytes()
        for case in corpus["cases"]:
            with self.subTest(case=case["id"]):
                payload = _mutate_glb(baseline, case["operations"])
                expectations = {
                    "max_animations": 32,
                    "max_bytes": len(payload),
                    "max_joints": 512,
                    "max_materials": 512,
                    "max_meshes": 512,
                    "max_nodes": 4096,
                    "max_primitives": 4096,
                    "max_triangles": 1_000_000,
                }
                try:
                    inspect_runtime_asset_bytes(
                        payload,
                        role="model",
                        media_type="model/gltf-binary",
                        expectations=expectations,
                    )
                except GenericAssetProductionError:
                    valid = False
                else:
                    valid = True
                self.assertEqual(valid, case["expected_valid"])

    def test_cli_verify_reports_sorted_machine_status_and_stderr_errors(self) -> None:
        verified = mock.Mock()
        verified.evidence = {
            "status": "sealed",
            "assetpack_id": "assetpack_" + ("a" * 48),
            "content_hash": "b" * 64,
            "inventory_hash": "c" * 64,
            "file_count": 2,
            "total_bytes": 7,
        }
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                ["worldforge", "verify-generic-assetpack", "/tmp/example-pack"],
            ),
            mock.patch.object(
                assetpack_module,
                "verify_generic_assetpack",
                return_value=verified,
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 0)
        self.assertEqual(
            json.loads(stdout.getvalue()),
            {**verified.evidence, "path": "/tmp/example-pack"},
        )
        verified.close.assert_called_once_with()
        self.assertEqual(stderr.getvalue(), "")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch(
                "sys.argv",
                ["worldforge", "verify-generic-assetpack", "/tmp/example-pack"],
            ),
            mock.patch.object(
                assetpack_module,
                "verify_generic_assetpack",
                side_effect=GenericAssetpackError(
                    "assetpack_file_hash_mismatch",
                    "tampered bytes",
                ),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {
                "detail": "tampered bytes",
                "reason_code": "assetpack_file_hash_mismatch",
                "status": "error",
            },
        )

    def test_cli_source_resolution_rejects_manifest_replacement_after_scan(self) -> None:
        source = (
            ROOT
            / "examples"
            / "multigenre-contracts"
            / "abstract-puzzle"
            / "assets"
            / "manifest.json"
        )
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-source-swap-") as temporary:
            copied_root = Path(temporary) / "abstract-puzzle"
            shutil.copytree(source.parents[1], copied_root)
            copied_manifest = copied_root / "assets" / "manifest.json"

            def swap_manifest(event: str) -> None:
                if event != "before_manifest_revalidation":
                    return
                replacement = copied_manifest.with_suffix(".replacement")
                replacement.write_bytes(copied_manifest.read_bytes())
                replacement.replace(copied_manifest)

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_source_resolve_failed",
            ):
                _resolve_generic_assetpack_cli_source(
                    copied_manifest,
                    _resolution_hook=swap_manifest,
                )

    def test_schema_accepts_the_exact_d3_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-schema-") as temporary:
            root = Path(temporary)
            media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
            chain = _build_processing_chain(media_case, root)
            assetpack = build_generic_assetpack_manifest(
                chain["manifest"],
                gamepack=chain["gamepack"],
                subject=chain["subject"],
                target=chain["target"],
                style=chain["style"],
                inventory=chain["inventory"],
                asset_records=[chain["record"]],
                artifact_root=root,
                qa_reviews=chain["qa_reviews"],
            )
            schema = build_schema()
            self.assertEqual(schema["properties"]["format"]["const"], assetpack["format"])
            self.assertEqual(schema["properties"]["format_version"]["const"], 1)
            self.assertEqual(schema["properties"]["assets"]["maxItems"], 1024)
            self.assertEqual(len(schema["$defs"]["output"]["oneOf"]), 12)
            referenced = {
                reference.rsplit("/", 1)[-1]
                for reference in json.dumps(schema).split('"')
                if reference.startswith("#/$defs/")
            }
            self.assertLessEqual(referenced, set(schema["$defs"]))

    def test_committed_puzzle_and_narrative_fixtures_are_deterministic(self) -> None:
        for fixture, expected_media in (
            ("abstract-puzzle", "image/png"),
            ("branching-narrative", "font/ttf"),
        ):
            with self.subTest(fixture=fixture):
                source = self._fixture_source(fixture)
                first = build_generic_assetpack_manifest(**source)
                second = build_generic_assetpack_manifest(**source)
                self.assertEqual(first, second)
                self.assertEqual(
                    serialize_generic_assetpack(first),
                    serialize_generic_assetpack(second),
                )
                self.assertEqual(
                    first["gamepack"]["content_hash"],
                    source["gamepack"]["content_hash"],
                )
                self.assertEqual(
                    first["release_ready_manifest"]["content_hash"],
                    source["manifest"]["content_hash"],
                )
                self.assertEqual(
                    first["assets"][0]["outputs"][0]["media_type"],
                    expected_media,
                )

    def test_builder_rejects_non_mapping_asset_records_with_a_contract_error(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        source["asset_records"] = [None]
        with self.assertRaisesRegex(
            GenericAssetpackError,
            "asset_records must contain objects",
        ) as raised:
            build_generic_assetpack_manifest(**source)
        self.assertEqual(raised.exception.reason_code, "assetpack_lineage_mismatch")

    def test_closed_manifest_semantics_reject_resealed_mutation_matrix(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        document = build_generic_assetpack_manifest(**source)

        extra_field = copy.deepcopy(document)
        extra_field["authoring_path"] = "/forbidden"
        self._reseal_mutation(extra_field)

        wrong_count = copy.deepcopy(document)
        wrong_count["inventory"]["file_count"] += 1
        self._reseal_mutation(wrong_count)

        duplicate_asset = copy.deepcopy(document)
        duplicate_asset["assets"].append(copy.deepcopy(duplicate_asset["assets"][0]))
        self._reseal_mutation(duplicate_asset)

        crossed_license = copy.deepcopy(document)
        crossed_license["assets"][0]["outputs"][0]["license_record"]["id"] = "unknown_license"
        self._reseal_mutation(crossed_license)

        substituted_license_hash = copy.deepcopy(document)
        substituted_license_hash["assets"][0]["outputs"][0]["license_record"]["content_hash"] = (
            "f" * 64
        )
        self._reseal_mutation(substituted_license_hash)

        oversized_notice = copy.deepcopy(document)
        oversized_notice_output = oversized_notice["assets"][0]["outputs"][0]
        notice_path = oversized_notice_output["runtime_notice"]["path"]
        oversized_notice_output["runtime_notice"]["size_bytes"] = 4097
        for entry in oversized_notice["inventory"]["files"]:
            if entry["path"] == notice_path:
                oversized_notice["inventory"]["total_bytes"] += 4097 - entry["size_bytes"]
                entry["size_bytes"] = 4097
                break
        self._reseal_mutation(oversized_notice)

        widened_byte_bound = copy.deepcopy(document)
        widened_byte_bound["assets"][0]["outputs"][0]["constraints"]["max_bytes"] += 1
        self._reseal_mutation(widened_byte_bound)

        for name, mutation in (
            ("extra_field", extra_field),
            ("wrong_count", wrong_count),
            ("duplicate_asset", duplicate_asset),
            ("crossed_license", crossed_license),
            ("substituted_license_hash", substituted_license_hash),
            ("oversized_notice", oversized_notice),
            ("widened_byte_bound", widened_byte_bound),
        ):
            with self.subTest(mutation=name):
                with self.assertRaises(GenericAssetpackError):
                    validate_generic_assetpack_document(mutation)

    def test_integral_verifier_rejects_manifest_payload_and_directory_swaps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-swap-") as temporary:
            root = Path(temporary)
            source = self._fixture_source("abstract-puzzle")
            sealed = root / "sealed"
            with seal_generic_assetpack(
                sealed,
                source["manifest"],
                gamepack=source["gamepack"],
                subject=source["subject"],
                target=source["target"],
                style=source["style"],
                inventory=source["inventory"],
                asset_records=source["asset_records"],
                artifact_root=source["artifact_root"],
                qa_reviews=source["qa_reviews"],
                release_authority=source["release_authority"],
            ):
                pass

            def replaced_copy(name: str) -> Path:
                destination = root / name
                shutil.copytree(sealed, destination)
                return destination

            manifest_root = replaced_copy("manifest-swap")

            def swap_manifest(event: str, relative: str | None) -> None:
                if event == "after_manifest_read":
                    manifest = manifest_root / "assetpack.json"
                    replacement = manifest.with_suffix(".replacement")
                    replacement.write_bytes(manifest.read_bytes())
                    replacement.replace(manifest)

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_directory_changed",
            ):
                verify_generic_assetpack(
                    manifest_root,
                    _verification_hook=swap_manifest,
                )

            payload_root = replaced_copy("payload-swap")
            swapped_payload = False

            def swap_payload(event: str, relative: str | None) -> None:
                nonlocal swapped_payload
                if event != "after_file_read" or relative is None or swapped_payload:
                    return
                if relative.startswith("assets/"):
                    payload = payload_root / PurePosixPath(relative)
                    replacement = payload.with_suffix(".replacement")
                    replacement.write_bytes(payload.read_bytes())
                    replacement.replace(payload)
                    swapped_payload = True

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_directory_changed",
            ):
                verify_generic_assetpack(
                    payload_root,
                    _verification_hook=swap_payload,
                )

            directory_root = replaced_copy("directory-swap")
            swapped_directory = False

            def swap_directory(event: str, relative: str | None) -> None:
                nonlocal swapped_directory
                if event != "after_tree_snapshot" or swapped_directory:
                    return
                original = directory_root / "assets"
                retained = directory_root / "assets-retained"
                original.replace(retained)
                shutil.copytree(retained, original)
                shutil.rmtree(retained)
                swapped_directory = True

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_directory_changed",
            ):
                verify_generic_assetpack(
                    directory_root,
                    _verification_hook=swap_directory,
                )

    def test_exact_tree_bounds_fail_before_manifest_semantics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-bounds-") as temporary:
            root = Path(temporary)
            current = root
            for index in range(4):
                current /= f"d{index}"
                current.mkdir()
            with (
                mock.patch.object(
                    assetpack_module,
                    "MAX_GENERIC_ASSETPACK_DEPTH",
                    3,
                ),
                self.assertRaisesRegex(
                    GenericAssetpackError,
                    "assetpack_contract_limit",
                ),
            ):
                verify_generic_assetpack(root)

        with tempfile.TemporaryDirectory(prefix="world-forge-d3-nodes-") as temporary:
            root = Path(temporary)
            for index in range(3):
                (root / f"d{index}").mkdir()
            with (
                mock.patch.object(
                    assetpack_module,
                    "MAX_GENERIC_ASSETPACK_DIRECTORIES",
                    2,
                ),
                self.assertRaisesRegex(
                    GenericAssetpackError,
                    "assetpack_contract_limit",
                ),
            ):
                verify_generic_assetpack(root)

        with tempfile.TemporaryDirectory(prefix="world-forge-d3-files-") as temporary:
            root = Path(temporary)
            for index in range(3):
                (root / f"f{index}").write_bytes(b"x")
            with mock.patch.object(
                assetpack_module,
                "MAX_GENERIC_ASSETPACK_FILES",
                2,
            ):
                snapshot = assetpack_module._snapshot_exact_tree(root)
                self.assertEqual(len(snapshot.files), 3)
                (root / "f3").write_bytes(b"x")
                with self.assertRaisesRegex(
                    GenericAssetpackError,
                    "assetpack_contract_limit",
                ):
                    assetpack_module._snapshot_exact_tree(root)

    def test_multi_output_pack_deduplicates_identical_runtime_notices(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-notice-") as temporary:
            root = Path(temporary)
            media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "atlas")
            chain = _build_processing_chain(media_case, root)
            document = build_generic_assetpack_manifest(
                chain["manifest"],
                gamepack=chain["gamepack"],
                subject=chain["subject"],
                target=chain["target"],
                style=chain["style"],
                inventory=chain["inventory"],
                asset_records=[chain["record"]],
                artifact_root=root,
                qa_reviews=chain["qa_reviews"],
            )
            outputs = document["assets"][0]["outputs"]
            self.assertEqual(len(outputs), 2)
            notice_paths = {output["runtime_notice"]["path"] for output in outputs}
            self.assertEqual(len(notice_paths), 1)
            self.assertEqual(document["inventory"]["file_count"], 3)

    def test_real_cli_seal_verify_recover_and_rollback_noop(self) -> None:
        manifest = (
            ROOT
            / "examples"
            / "multigenre-contracts"
            / "abstract-puzzle"
            / "assets"
            / "manifest.json"
        )
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-cli-") as temporary:
            destination = Path(temporary) / "puzzle-assets"

            def invoke(arguments: list[str]) -> tuple[int, str, str]:
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch("sys.argv", ["worldforge", *arguments]),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                ):
                    status = main()
                return status, stdout.getvalue(), stderr.getvalue()

            status, stdout, stderr = invoke(
                [
                    "seal-generic-assetpack",
                    str(manifest),
                    "--output",
                    str(destination),
                ]
            )
            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            sealed = json.loads(stdout)
            self.assertEqual(sealed["status"], "sealed")

            status, stdout, stderr = invoke(
                [
                    "verify-generic-assetpack",
                    str(destination),
                    "--expected-hash",
                    sealed["content_hash"],
                ]
            )
            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(json.loads(stdout)["assetpack_id"], sealed["assetpack_id"])

            status, stdout, stderr = invoke(["recover-generic-assetpack", str(destination)])
            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(json.loads(stdout)["status"], "sealed")

            status, stdout, stderr = invoke(["rollback-generic-assetpack", str(destination)])
            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(json.loads(stdout)["status"], "no_operation")

    def test_release_ready_chain_builds_runtime_only_sealed_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-build-") as temporary:
            root = Path(temporary)
            media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
            chain = _build_processing_chain(media_case, root)

            assetpack = build_generic_assetpack_manifest(
                chain["manifest"],
                gamepack=chain["gamepack"],
                subject=chain["subject"],
                target=chain["target"],
                style=chain["style"],
                inventory=chain["inventory"],
                asset_records=[chain["record"]],
                artifact_root=root,
                qa_reviews=chain["qa_reviews"],
            )

            notice = chain["licenses"][0]["runtime_notice"]
            notice_path = f"notices/{notice['sha256']}.txt"
            self.assertEqual(assetpack["format"], GENERIC_ASSETPACK_FORMAT)
            self.assertEqual(assetpack["format_version"], 1)
            self.assertEqual(assetpack["state"], "sealed")
            self.assertRegex(assetpack["assetpack_id"], r"^assetpack_[0-9a-f]{48}$")
            self.assertEqual(
                assetpack["gamepack"]["content_hash"],
                chain["gamepack"]["content_hash"],
            )
            self.assertEqual(
                assetpack["release_ready_manifest"]["content_hash"],
                chain["manifest"]["content_hash"],
            )
            self.assertEqual(
                [item["path"] for item in assetpack["inventory"]["files"]],
                ["assets/matrix/texture.png", notice_path],
            )
            self.assertEqual(assetpack["inventory"]["file_count"], 2)
            self.assertEqual(
                assetpack["inventory"]["total_bytes"],
                len(media_case["outputs"][0]["payload"]) + len(notice["text"].encode("utf-8")),
            )
            self.assertEqual(
                assetpack["assets"][0]["outputs"][0],
                {
                    "constraints": media_case["outputs"][0]["expectations"],
                    "license_record": {
                        "content_hash": chain["licenses"][0]["content_hash"],
                        "format": "world-forge.asset_license_record",
                        "format_version": 1,
                        "id": chain["licenses"][0]["license_record_id"],
                    },
                    "media_type": "image/png",
                    "metadata": chain["processing_receipt"]["outputs"][0]["metadata"],
                    "role": "texture",
                    "runtime_notice": {
                        "path": notice_path,
                        "sha256": notice["sha256"],
                        "size_bytes": len(notice["text"].encode("utf-8")),
                    },
                    "runtime_path": "assets/matrix/texture.png",
                    "sha256": hashlib.sha256(media_case["outputs"][0]["payload"]).hexdigest(),
                    "size_bytes": len(media_case["outputs"][0]["payload"]),
                },
            )
            serialized = serialize_generic_assetpack(assetpack)
            self.assertTrue(serialized.endswith(b"\n"))
            decoded = json.loads(serialized)
            self.assertEqual(decoded, assetpack)
            for forbidden in (
                "candidate_artifact_id",
                "locator",
                "prompt",
                "provider",
                "source_path",
                "toolchain",
            ):
                self.assertNotIn(f'"{forbidden}"', serialized.decode("utf-8"))

    def test_seal_integrally_verifies_exact_tree_and_never_replaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-seal-") as temporary:
            root = Path(temporary)
            media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
            chain = _build_processing_chain(media_case, root)
            destination = root / "sealed-assets"
            kwargs = {
                "gamepack": chain["gamepack"],
                "subject": chain["subject"],
                "target": chain["target"],
                "style": chain["style"],
                "inventory": chain["inventory"],
                "asset_records": [chain["record"]],
                "artifact_root": root,
                "qa_reviews": chain["qa_reviews"],
                "release_authority": chain["release_authority"],
            }

            with seal_generic_assetpack(
                destination,
                chain["manifest"],
                **kwargs,
            ) as verified:
                self.assertEqual(verified.status, "sealed")
                self.assertEqual(verified.root, destination.absolute())
                self.assertEqual(
                    verified.read_bytes("assets/matrix/texture.png"),
                    media_case["outputs"][0]["payload"],
                )
                notice = chain["licenses"][0]["runtime_notice"]
                self.assertEqual(
                    verified.read_bytes(f"notices/{notice['sha256']}.txt"),
                    notice["text"].encode("utf-8"),
                )
                self.assertEqual(
                    verified.evidence["content_hash"],
                    verified.manifest["content_hash"],
                )

            with verify_generic_assetpack(destination) as verified:
                self.assertEqual(verified.status, "sealed")
                content_hash = verified.manifest["content_hash"]
            self.assertFalse((root / ".sealed-assets.assetpack.journal.json").exists())
            self.assertEqual(
                (root / ".sealed-assets.assetpack.lock").read_bytes(),
                b"\0",
            )
            with seal_generic_assetpack(
                destination,
                chain["manifest"],
                **kwargs,
            ) as idempotent:
                self.assertEqual(idempotent.manifest["content_hash"], content_hash)
            different = self._fixture_source("branching-narrative")
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_destination_exists",
            ):
                seal_generic_assetpack(
                    destination,
                    **different,
                )

            payload_path = destination / "assets" / "matrix" / "texture.png"
            payload_path.write_bytes(b"\0" * payload_path.stat().st_size)
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_file_hash_mismatch",
            ):
                verify_generic_assetpack(
                    destination,
                    expected_content_hash=content_hash,
                )

    def test_lock_creation_never_writes_beneath_a_replaced_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-lock-parent-") as temporary:
            root = Path(temporary)
            media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
            chain = _build_processing_chain(media_case, root)
            output_parent = root / "outputs"
            displaced_parent = root / "outputs-displaced"
            output_parent.mkdir()
            parent_info = output_parent.stat()
            parent_identity = (parent_info.st_dev, parent_info.st_ino)
            destination = output_parent / "sealed"
            real_open = os.open
            swapped = False

            def swap_before_lock_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                if not swapped and flags & os.O_CREAT and str(path).endswith(".assetpack.lock"):
                    swapped = True
                    output_parent.rename(displaced_parent)
                    output_parent.mkdir()
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(assetpack_module.os, "open", side_effect=swap_before_lock_open),
                self.assertRaises(GenericAssetpackError),
            ):
                seal_generic_assetpack(
                    destination,
                    chain["manifest"],
                    gamepack=chain["gamepack"],
                    subject=chain["subject"],
                    target=chain["target"],
                    style=chain["style"],
                    inventory=chain["inventory"],
                    asset_records=[chain["record"]],
                    artifact_root=root,
                    qa_reviews=chain["qa_reviews"],
                    release_authority=chain["release_authority"],
                    expected_parent_identity=parent_identity,
                )
            self.assertTrue(swapped)
            self.assertEqual([], list(output_parent.iterdir()))

    def test_lock_never_recreates_a_selected_parent_renamed_away(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-lock-missing-") as temporary:
            root = Path(temporary)
            output_parent = root / "outputs"
            displaced_parent = root / "outputs-displaced"
            output_parent.mkdir()
            parent_info = output_parent.stat()
            parent_identity = (parent_info.st_dev, parent_info.st_ino)
            destination = output_parent / "sealed"
            output_parent.rename(displaced_parent)

            with self.assertRaises(GenericAssetpackError):
                with assetpack_module._destination_lock(  # noqa: SLF001
                    destination,
                    expected_parent_identity=parent_identity,
                ):
                    self.fail("missing retained parent must not be recreated")
            self.assertFalse(output_parent.exists())

    def test_retained_windows_journal_name_binding_shares_delete_access(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-windows-share-") as temporary:
            root = Path(temporary)
            journal = root / ".sealed.assetpack.journal.json"
            journal.write_bytes(b"journal")
            info = journal.stat()
            descriptor = os.open(journal, os.O_RDONLY)
            api = mock.Mock()
            api.open_existing_file_strict.return_value = 91
            api.strict_entry_info.return_value = info
            parent = mock.Mock(unsafe=True)
            parent.path = root.absolute()
            parent.parent_fd = None
            parent.windows_api = api
            parent.windows_parent_handle = 73
            try:
                directory_publish_module._require_journal_binding(  # noqa: SLF001
                    journal,
                    descriptor,
                    (info.st_dev, info.st_ino),
                    retained_parent=parent,
                )
            finally:
                os.close(descriptor)
            api.open_existing_file_strict.assert_called_once_with(
                73,
                journal.name,
                share_delete=True,
            )

    def test_windows_raw_create_file_binding_does_not_shadow_relative_method(self) -> None:
        kernel32 = mock.Mock()
        ntdll = mock.Mock()

        def load_library(name: str, **_kwargs: object) -> mock.Mock:
            return kernel32 if name == "kernel32" else ntdll

        with mock.patch.object(
            asset_io_module.ctypes,
            "WinDLL",
            side_effect=load_library,
            create=True,
        ):
            api = asset_io_module._WindowsPublicationApi()  # noqa: SLF001
        self.assertNotIn("create_file", vars(api))
        api._open_relative = mock.Mock(return_value=17)  # noqa: SLF001
        api._state = mock.Mock(  # noqa: SLF001
            return_value=mock.Mock(st_nlink=1, st_size=0)
        )
        self.assertEqual(17, api.create_file(11, "journal.json"))
        api._open_relative.assert_called_once()  # noqa: SLF001

    @unittest.skipUnless(os.name == "posix", "requires POSIX descriptor-backed Windows seam")
    def test_windows_assetpack_stage_omits_delete_while_exact_snapshot_runs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-windows-stage-") as temporary:
            root = Path(temporary)
            stage = root / ".sealed.assetpack-stage"
            parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            info = root.stat()
            parent_identity = (info.st_dev, info.st_ino)
            api = _PosixBackedWindowsStageApi()
            parent = mock.Mock(unsafe=True)
            parent.parent_fd = None
            parent.windows_api = api
            parent.windows_parent_handle = parent_fd
            parent.identities = (parent_identity,)
            parent.assert_current.side_effect = lambda: None
            lock = mock.Mock(unsafe=True)
            lock.parent = parent
            lock.require_binding.side_effect = lambda: None
            payload = b"sealed fixture\n"

            try:
                with (
                    mock.patch.object(
                        assetpack_module,
                        "windows_handle_file_stat",
                        side_effect=descriptor_file_stat,
                    ),
                    assetpack_module._create_anchored_stage(  # noqa: SLF001
                        stage,
                        lock,
                        expected_parent_identity=parent_identity,
                        publication_hook=None,
                    ) as writer,
                ):
                    writer.write_file("assets/fixture.bin", payload)
                    writer.fsync()
                    snapshot = assetpack_module._snapshot_exact_tree(stage)  # noqa: SLF001
                    writer.require_binding()
                    self.assertEqual({"assets/fixture.bin"}, set(snapshot.files))
                    self.assertEqual(payload, (stage / "assets/fixture.bin").read_bytes())
            finally:
                os.close(parent_fd)

            self.assertEqual(
                [
                    ("directory", stage.name, False),
                    ("directory", "assets", False),
                    ("file", "fixture.bin", False),
                ],
                api.creations,
            )

    def test_windows_retained_cleanup_opens_stage_relative_to_parent(self) -> None:
        kernel32 = mock.Mock()
        ntdll = mock.Mock()

        def load_library(name: str, **_kwargs: object) -> mock.Mock:
            return kernel32 if name == "kernel32" else ntdll

        with mock.patch.object(
            asset_io_module.ctypes,
            "WinDLL",
            side_effect=load_library,
            create=True,
        ):
            api = asset_io_module._WindowsPublicationApi()  # noqa: SLF001
        api._open_relative = mock.Mock(return_value=17)  # noqa: SLF001
        api._strict_state = mock.Mock(return_value=mock.Mock())  # noqa: SLF001

        self.assertEqual(
            17,
            api.open_existing_directory_strict(11, ".sealed.assetpack-stage", delete=True),
        )

        call = api._open_relative.call_args  # noqa: SLF001
        self.assertEqual(11, call.args[0])
        self.assertEqual(".sealed.assetpack-stage", call.args[1])
        self.assertTrue(call.kwargs["access"] & api._DELETE)  # noqa: SLF001
        self.assertEqual(api._FILE_DIRECTORY_FILE, call.kwargs["options"])  # noqa: SLF001
        self.assertFalse(call.kwargs["share"] & api._SHARE_DELETE)  # noqa: SLF001

    def test_retained_windows_parent_durability_reopens_by_identity(self) -> None:
        api = mock.Mock()
        identities = ((1, 10), (1, 20))
        api.open_ancestry.return_value = ([31, 32], identities)
        parent = asset_io_module.PinnedOutputParent(
            path=Path("/selected/output"),
            identities=identities,
            windows_api=api,
            windows_handles=(11, 22),
        )

        parent.flush_durable(context="assetpack control parent")

        api.flush_relative_directory.assert_called_once_with(
            11,
            "output",
            (1, 20),
            "assetpack control parent",
        )
        api.flush_handle.assert_not_called()

    def test_assetpack_and_journal_flushes_use_retained_parent_durability(self) -> None:
        parent = mock.Mock(unsafe=True)
        parent.path = Path("/selected/output").absolute()
        parent.parent_fd = None
        parent.windows_api = mock.Mock()
        parent.windows_parent_handle = 22
        parent.identities = ((1, 10), (1, 20))
        journal = parent.path / ".sealed.assetpack.journal.json"

        assetpack_module._flush_retained_assetpack_parent(  # noqa: SLF001
            parent,
            context="assetpack lock parent",
        )
        directory_publish_module._flush_retained_journal_parent(  # noqa: SLF001
            journal,
            parent,
            context="assetpack journal parent",
        )

        self.assertEqual(
            [
                mock.call(context="assetpack lock parent"),
                mock.call(context="assetpack journal parent"),
            ],
            parent.flush_durable.call_args_list,
        )
        parent.windows_api.flush_handle.assert_not_called()

    def test_journal_creation_never_writes_beneath_a_replaced_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-journal-parent-") as temporary:
            root = Path(temporary)
            media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
            chain = _build_processing_chain(media_case, root)
            output_parent = root / "outputs"
            displaced_parent = root / "outputs-displaced"
            output_parent.mkdir()
            parent_info = output_parent.stat()
            parent_identity = (parent_info.st_dev, parent_info.st_ino)
            destination = output_parent / "sealed"
            real_create = assetpack_module.create_append_only_journal
            swapped = False

            def swap_before_journal_create(
                path: Path,
                payload: bytes,
                **kwargs: object,
            ) -> tuple[int, int]:
                nonlocal swapped
                if not swapped:
                    swapped = True
                    output_parent.rename(displaced_parent)
                    output_parent.mkdir()
                return real_create(path, payload, **kwargs)

            with (
                mock.patch.object(
                    assetpack_module,
                    "create_append_only_journal",
                    side_effect=swap_before_journal_create,
                ),
                self.assertRaises(GenericAssetpackError),
            ):
                seal_generic_assetpack(
                    destination,
                    chain["manifest"],
                    gamepack=chain["gamepack"],
                    subject=chain["subject"],
                    target=chain["target"],
                    style=chain["style"],
                    inventory=chain["inventory"],
                    asset_records=[chain["record"]],
                    artifact_root=root,
                    qa_reviews=chain["qa_reviews"],
                    release_authority=chain["release_authority"],
                    expected_parent_identity=parent_identity,
                )
            self.assertTrue(swapped)
            self.assertEqual([], list(output_parent.iterdir()))

    def test_journal_append_never_mutates_a_replacement_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-append-parent-") as temporary:
            root = Path(temporary)
            media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
            chain = _build_processing_chain(media_case, root)
            output_parent = root / "outputs"
            displaced_parent = root / "outputs-displaced"
            output_parent.mkdir()
            parent_info = output_parent.stat()
            parent_identity = (parent_info.st_dev, parent_info.st_ino)
            destination = output_parent / "sealed"
            real_append = assetpack_module.append_append_only_journal
            replacement_payload: bytes | None = None

            def swap_before_journal_append(
                path: Path,
                **kwargs: object,
            ) -> tuple[int, int]:
                nonlocal replacement_payload
                if replacement_payload is None:
                    output_parent.rename(displaced_parent)
                    output_parent.mkdir()
                    retained_journal = displaced_parent / path.name
                    replacement_payload = retained_journal.read_bytes()
                    retained_journal.rename(output_parent / path.name)
                return real_append(path, **kwargs)

            with (
                mock.patch.object(
                    assetpack_module,
                    "append_append_only_journal",
                    side_effect=swap_before_journal_append,
                ),
                self.assertRaises(GenericAssetpackError),
            ):
                seal_generic_assetpack(
                    destination,
                    chain["manifest"],
                    gamepack=chain["gamepack"],
                    subject=chain["subject"],
                    target=chain["target"],
                    style=chain["style"],
                    inventory=chain["inventory"],
                    asset_records=[chain["record"]],
                    artifact_root=root,
                    qa_reviews=chain["qa_reviews"],
                    release_authority=chain["release_authority"],
                    expected_parent_identity=parent_identity,
                )
            self.assertIsNotNone(replacement_payload)
            self.assertEqual(
                replacement_payload,
                (output_parent / ".sealed.assetpack.journal.json").read_bytes(),
            )

    def test_journal_removal_never_deletes_from_a_replacement_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-remove-parent-") as temporary:
            root = Path(temporary)
            media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
            chain = _build_processing_chain(media_case, root)
            output_parent = root / "outputs"
            displaced_parent = root / "outputs-displaced"
            output_parent.mkdir()
            parent_info = output_parent.stat()
            parent_identity = (parent_info.st_dev, parent_info.st_ino)
            destination = output_parent / "sealed"
            real_remove = directory_publish_module.remove_append_only_journal
            replacement_payload: bytes | None = None

            def swap_before_journal_remove(
                path: Path,
                **kwargs: object,
            ) -> None:
                nonlocal replacement_payload
                if replacement_payload is None:
                    output_parent.rename(displaced_parent)
                    output_parent.mkdir()
                    retained_journal = displaced_parent / path.name
                    replacement_payload = retained_journal.read_bytes()
                    retained_journal.rename(output_parent / path.name)
                real_remove(path, **kwargs)

            with (
                mock.patch.object(
                    directory_publish_module,
                    "remove_append_only_journal",
                    side_effect=swap_before_journal_remove,
                ),
                self.assertRaises(GenericAssetpackError),
            ):
                seal_generic_assetpack(
                    destination,
                    chain["manifest"],
                    gamepack=chain["gamepack"],
                    subject=chain["subject"],
                    target=chain["target"],
                    style=chain["style"],
                    inventory=chain["inventory"],
                    asset_records=[chain["record"]],
                    artifact_root=root,
                    qa_reviews=chain["qa_reviews"],
                    release_authority=chain["release_authority"],
                    expected_parent_identity=parent_identity,
                )
            self.assertIsNotNone(replacement_payload)
            self.assertEqual(
                replacement_payload,
                (output_parent / ".sealed.assetpack.journal.json").read_bytes(),
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "parent-swap publication is a native Linux test",
    )
    def test_seal_derives_parent_identity_when_caller_omits_it(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-derived-parent-") as temporary:
            root = Path(temporary)
            output_parent = root / "outputs"
            displaced_parent = root / "outputs-displaced"
            output_parent.mkdir()
            destination = output_parent / "sealed"
            swapped = False

            real_publish = assetpack_module.publish_directory_noreplace

            @contextmanager
            def swap_parent_before_publish(
                stage: Path,
                publish_destination: Path,
                **kwargs: object,
            ):
                nonlocal swapped
                output_parent.rename(displaced_parent)
                output_parent.mkdir()
                retained_stage = displaced_parent / stage.name
                retained_stage.rename(stage)
                swapped = True
                with real_publish(
                    stage,
                    publish_destination,
                    **kwargs,
                ) as published_identity:
                    yield published_identity

            with (
                mock.patch.object(
                    assetpack_module,
                    "publish_directory_noreplace",
                    side_effect=swap_parent_before_publish,
                ),
                self.assertRaises(GenericAssetpackError),
            ):
                seal_generic_assetpack(destination, **source)
            self.assertTrue(swapped)
            self.assertFalse(
                destination.exists(),
                "publication must not become visible beneath a replacement parent",
            )

    def test_recovery_rolls_forward_complete_stage_and_rolls_back_owned_subset(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-recovery-") as temporary:
            root = Path(temporary)
            media_case = next(case for case in _media_matrix_cases() if case["case_id"] == "png")
            chain = _build_processing_chain(media_case, root)
            kwargs = {
                "gamepack": chain["gamepack"],
                "subject": chain["subject"],
                "target": chain["target"],
                "style": chain["style"],
                "inventory": chain["inventory"],
                "asset_records": [chain["record"]],
                "artifact_root": root,
                "qa_reviews": chain["qa_reviews"],
                "release_authority": chain["release_authority"],
            }

            @contextmanager
            def fail_publish(*_args: object, **_kwargs: object):
                raise DirectoryPublishError("synthetic no-replace boundary failure")
                yield

            recover_destination = root / "recover-assets"
            with mock.patch.object(
                assetpack_module,
                "publish_directory_noreplace",
                fail_publish,
            ):
                with self.assertRaisesRegex(
                    GenericAssetpackError,
                    "assetpack_publication_failed",
                ):
                    seal_generic_assetpack(
                        recover_destination,
                        chain["manifest"],
                        **kwargs,
                    )
            stages = list(root.glob(".recover-assets.assetpack-*"))
            self.assertEqual(len(stages), 1)
            with recover_generic_assetpack(recover_destination) as recovered:
                self.assertEqual(recovered.status, "sealed")
            self.assertFalse(stages[0].exists())

            rollback_destination = root / "rollback-assets"
            original_write = assetpack_module._AnchoredStageWriter.write_file
            write_count = 0

            def fail_partial_write(
                writer: object,
                relative: str,
                payload: bytes,
            ) -> None:
                nonlocal write_count
                write_count += 1
                original_write(writer, relative, payload)
                if write_count == 2:
                    raise GenericAssetpackError(
                        "assetpack_stage_write_failed",
                        "synthetic partial stage",
                    )

            with mock.patch.object(
                assetpack_module._AnchoredStageWriter,
                "write_file",
                fail_partial_write,
            ):
                with self.assertRaisesRegex(
                    GenericAssetpackError,
                    "synthetic partial stage",
                ):
                    seal_generic_assetpack(
                        rollback_destination,
                        chain["manifest"],
                        **kwargs,
                    )
            rollback_stages = list(root.glob(".rollback-assets.assetpack-*"))
            self.assertEqual(len(rollback_stages), 1)
            hardlink = rollback_stages[0] / "foreign-hardlink"
            os.link(rollback_stages[0] / "assetpack.json", hardlink)
            with self.assertRaises(GenericAssetpackError):
                rollback_generic_assetpack(rollback_destination)
            self.assertTrue(hardlink.exists())
            hardlink.unlink()

            unknown = rollback_stages[0] / "foreign.txt"
            unknown.write_text("foreign", encoding="utf-8")
            with self.assertRaisesRegex(
                GenericAssetpackError,
                (
                    "assetpack_rollback_recovery_required"
                    if sys.platform.startswith("linux") and os.name == "posix"
                    else "assetpack_rollback_ambiguous"
                ),
            ):
                rollback_generic_assetpack(rollback_destination)
            self.assertEqual(unknown.read_text(encoding="utf-8"), "foreign")
            unknown.unlink()

            retained_stage = rollback_stages[0].with_name(
                f"{rollback_stages[0].name}-retained",
            )
            rollback_stages[0].replace(retained_stage)
            rollback_stages[0].mkdir()
            (rollback_stages[0] / "foreign.txt").write_text(
                "replacement",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_rollback_ambiguous",
            ):
                rollback_generic_assetpack(rollback_destination)
            self.assertEqual(
                (rollback_stages[0] / "foreign.txt").read_text(encoding="utf-8"),
                "replacement",
            )
            shutil.rmtree(rollback_stages[0])
            retained_stage.replace(rollback_stages[0])

            rollback_journal = root / ".rollback-assets.assetpack.journal.json"
            if sys.platform.startswith("linux") and os.name == "posix":
                with self.assertRaises(GenericAssetpackError) as raised:
                    rollback_generic_assetpack(rollback_destination)
                self.assertEqual(
                    "assetpack_rollback_recovery_required",
                    raised.exception.reason_code,
                )
                self.assertTrue(rollback_stages[0].is_dir())
                self.assertTrue(rollback_journal.is_file())
            else:
                self.assertEqual(
                    rollback_generic_assetpack(rollback_destination)["status"],
                    "rolled_back",
                )
                self.assertFalse(rollback_stages[0].exists())
                self.assertFalse(rollback_journal.exists())
            self.assertFalse(rollback_destination.exists())

    def test_append_only_journal_repairs_every_plausible_ready_frame_cut(
        self,
    ) -> None:
        intent = b'{"state":"intent"}\n'
        copying = b'{"state":"copying"}\n'
        ready = b'{"state":"ready"}\n'
        ready_frame = directory_publish_module._journal_frame(ready)  # noqa: SLF001
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-tail-") as temporary:
            root = Path(temporary)
            for cut in range(len(ready_frame)):
                with self.subTest(cut=cut):
                    path = root / f"journal-{cut}"
                    identity = directory_publish_module.create_append_only_journal(
                        path,
                        intent,
                        max_record_bytes=1024,
                    )
                    directory_publish_module.append_append_only_journal(
                        path,
                        expected_identity=identity,
                        expected_payload=intent,
                        expected_history=(intent,),
                        updated_payload=copying,
                        max_record_bytes=1024,
                        max_file_bytes=16 * 1024,
                    )
                    complete_prefix = path.read_bytes()
                    with path.open("ab") as target:
                        target.write(ready_frame[:cut])
                        target.flush()
                        os.fsync(target.fileno())

                    directory_publish_module.append_append_only_journal(
                        path,
                        expected_identity=identity,
                        expected_payload=copying,
                        expected_history=(intent, copying),
                        updated_payload=ready,
                        max_record_bytes=1024,
                        max_file_bytes=16 * 1024,
                        repair_partial_tail=True,
                    )

                    self.assertEqual(
                        complete_prefix + ready_frame,
                        path.read_bytes(),
                    )
                    loaded = directory_publish_module.read_append_only_journal(
                        path,
                        max_record_bytes=1024,
                        max_file_bytes=16 * 1024,
                    )
                    self.assertEqual((ready, identity), loaded)

    def test_append_only_journal_rejects_ambiguous_invalid_and_changed_tails(
        self,
    ) -> None:
        intent = b'{"state":"intent"}\n'
        copying = b'{"state":"copying"}\n'
        ready = b'{"state":"ready"}\n'
        ready_frame = directory_publish_module._journal_frame(ready)  # noqa: SLF001
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-tail-invalid-") as temporary:
            root = Path(temporary)

            for name, tail in (
                ("ambiguous", b"foreign-tail"),
                (
                    "invalid-complete",
                    ready_frame.replace(ready, b'{"state":"reedy"}\n', 1),
                ),
            ):
                with self.subTest(name=name):
                    path = root / name
                    identity = directory_publish_module.create_append_only_journal(
                        path,
                        intent,
                        max_record_bytes=1024,
                    )
                    directory_publish_module.append_append_only_journal(
                        path,
                        expected_identity=identity,
                        expected_payload=intent,
                        expected_history=(intent,),
                        updated_payload=copying,
                        max_record_bytes=1024,
                        max_file_bytes=16 * 1024,
                    )
                    with path.open("ab") as target:
                        target.write(tail)
                        target.flush()
                        os.fsync(target.fileno())
                    before = path.read_bytes()

                    with self.assertRaisesRegex(
                        DirectoryPublishError,
                        "malformed frame",
                    ):
                        directory_publish_module.read_append_only_journal(
                            path,
                            max_record_bytes=1024,
                            max_file_bytes=16 * 1024,
                        )
                    with self.assertRaisesRegex(
                        DirectoryPublishError,
                        "malformed frame",
                    ):
                        directory_publish_module.append_append_only_journal(
                            path,
                            expected_identity=identity,
                            expected_payload=copying,
                            expected_history=(intent, copying),
                            updated_payload=ready,
                            max_record_bytes=1024,
                            max_file_bytes=16 * 1024,
                            repair_partial_tail=True,
                        )
                    self.assertEqual(before, path.read_bytes())

            path = root / "plausible-wrong-transition"
            identity = directory_publish_module.create_append_only_journal(
                path,
                intent,
                max_record_bytes=1024,
            )
            directory_publish_module.append_append_only_journal(
                path,
                expected_identity=identity,
                expected_payload=intent,
                expected_history=(intent,),
                updated_payload=copying,
                max_record_bytes=1024,
                max_file_bytes=16 * 1024,
            )
            wrong_frame = directory_publish_module._journal_frame(  # noqa: SLF001
                b'{"state":"foreign"}\n',
            )
            with path.open("ab") as target:
                target.write(wrong_frame[: len(wrong_frame) // 2])
                target.flush()
                os.fsync(target.fileno())
            before = path.read_bytes()
            self.assertEqual(
                (copying, identity),
                directory_publish_module.read_append_only_journal(
                    path,
                    max_record_bytes=1024,
                    max_file_bytes=16 * 1024,
                ),
            )
            with self.assertRaisesRegex(
                DirectoryPublishError,
                "does not match",
            ):
                directory_publish_module.append_append_only_journal(
                    path,
                    expected_identity=identity,
                    expected_payload=copying,
                    expected_history=(intent, copying),
                    updated_payload=ready,
                    max_record_bytes=1024,
                    max_file_bytes=16 * 1024,
                    repair_partial_tail=True,
                )
            self.assertEqual(before, path.read_bytes())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "journal replacement race is a native Linux test",
    )
    def test_journal_tail_repair_rejects_concurrent_path_replacement(self) -> None:
        intent = b'{"state":"intent"}\n'
        copying = b'{"state":"copying"}\n'
        ready = b'{"state":"ready"}\n'
        ready_frame = directory_publish_module._journal_frame(ready)  # noqa: SLF001
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-tail-race-") as temporary:
            root = Path(temporary)
            path = root / "journal"
            retained = root / "retained-journal"
            identity = directory_publish_module.create_append_only_journal(
                path,
                intent,
                max_record_bytes=1024,
            )
            directory_publish_module.append_append_only_journal(
                path,
                expected_identity=identity,
                expected_payload=intent,
                expected_history=(intent,),
                updated_payload=copying,
                max_record_bytes=1024,
                max_file_bytes=16 * 1024,
            )
            complete_prefix = path.read_bytes()
            with path.open("ab") as target:
                target.write(ready_frame[: len(ready_frame) // 2])
                target.flush()
                os.fsync(target.fileno())

            truncated = threading.Event()
            replaced = threading.Event()

            def replace_path() -> None:
                self.assertTrue(truncated.wait(timeout=10))
                path.replace(retained)
                path.write_bytes(b"foreign replacement")
                replaced.set()

            attacker = threading.Thread(target=replace_path)
            attacker.start()
            real_ftruncate = directory_publish_module.os.ftruncate

            def truncate_then_wait(descriptor: int, length: int) -> None:
                real_ftruncate(descriptor, length)
                truncated.set()
                self.assertTrue(replaced.wait(timeout=10))

            try:
                with (
                    mock.patch.object(
                        directory_publish_module.os,
                        "ftruncate",
                        truncate_then_wait,
                    ),
                    self.assertRaisesRegex(
                        DirectoryPublishError,
                        "path binding changed",
                    ),
                ):
                    directory_publish_module.append_append_only_journal(
                        path,
                        expected_identity=identity,
                        expected_payload=copying,
                        expected_history=(intent, copying),
                        updated_payload=ready,
                        max_record_bytes=1024,
                        max_file_bytes=16 * 1024,
                        repair_partial_tail=True,
                    )
            finally:
                truncated.set()
                attacker.join(timeout=10)
            self.assertFalse(attacker.is_alive())
            self.assertEqual(b"foreign replacement", path.read_bytes())
            self.assertEqual(complete_prefix, retained.read_bytes())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "journal prefix mutation is a native Linux test",
    )
    def test_journal_tail_repair_rejects_changed_complete_prefix(self) -> None:
        intent = b'{"state":"intent"}\n'
        copying = b'{"state":"copying"}\n'
        ready = b'{"state":"ready"}\n'
        ready_frame = directory_publish_module._journal_frame(ready)  # noqa: SLF001
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-tail-prefix-") as temporary:
            path = Path(temporary) / "journal"
            identity = directory_publish_module.create_append_only_journal(
                path,
                intent,
                max_record_bytes=1024,
            )
            directory_publish_module.append_append_only_journal(
                path,
                expected_identity=identity,
                expected_payload=intent,
                expected_history=(intent,),
                updated_payload=copying,
                max_record_bytes=1024,
                max_file_bytes=16 * 1024,
            )
            complete_prefix = path.read_bytes()
            with path.open("ab") as target:
                target.write(ready_frame[: len(ready_frame) // 2])
                target.flush()
                os.fsync(target.fileno())

            real_ftruncate = directory_publish_module.os.ftruncate

            def truncate_then_change_prefix(descriptor: int, length: int) -> None:
                real_ftruncate(descriptor, length)
                with path.open("r+b", buffering=0) as attacker:
                    attacker.write(b"X")
                    os.fsync(attacker.fileno())

            with (
                mock.patch.object(
                    directory_publish_module.os,
                    "ftruncate",
                    truncate_then_change_prefix,
                ),
                self.assertRaisesRegex(
                    DirectoryPublishError,
                    "prefix changed",
                ),
            ):
                directory_publish_module.append_append_only_journal(
                    path,
                    expected_identity=identity,
                    expected_payload=copying,
                    expected_history=(intent, copying),
                    updated_payload=ready,
                    max_record_bytes=1024,
                    max_file_bytes=16 * 1024,
                    repair_partial_tail=True,
                )
            changed_prefix = path.read_bytes()
            self.assertEqual(len(complete_prefix), len(changed_prefix))
            self.assertEqual(b"X", changed_prefix[:1])
            self.assertNotIn(ready_frame, changed_prefix)

    def test_recovery_repairs_a_truncated_ready_transition(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-tail-recovery-") as temporary:
            root = Path(temporary)
            destination = root / "sealed"

            def crash_before_publish(event: str, _relative: str | None) -> None:
                if event == "before_destination_publish":
                    raise RuntimeError("synthetic ready-stage crash")

            with self.assertRaisesRegex(RuntimeError, "ready-stage crash"):
                seal_generic_assetpack(
                    destination,
                    **source,
                    _publication_hook=crash_before_publish,
                )
            journal_path = root / ".sealed.assetpack.journal.json"
            loaded = assetpack_module._read_journal_record(
                journal_path,
                destination,
            )
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual("ready", loaded[0]["state"])
            ready_frame = directory_publish_module._journal_frame(loaded[2])  # noqa: SLF001
            complete = journal_path.read_bytes()
            copying_prefix = complete[: -len(ready_frame)]
            journal_path.write_bytes(
                copying_prefix + ready_frame[: len(ready_frame) // 2],
            )

            with recover_generic_assetpack(destination) as recovered:
                self.assertEqual("sealed", recovered.status)
            self.assertFalse(journal_path.exists())
            self.assertTrue(destination.is_dir())

    def test_recovery_rejects_duplicate_copying_before_partial_ready(
        self,
    ) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(
            prefix="world-forge-d3-history-partial-",
        ) as temporary:
            root = Path(temporary)
            destination = root / "sealed"

            def crash_before_publish(event: str, _relative: str | None) -> None:
                if event == "before_destination_publish":
                    raise RuntimeError("synthetic ready-stage crash")

            with self.assertRaisesRegex(RuntimeError, "ready-stage crash"):
                seal_generic_assetpack(
                    destination,
                    **source,
                    _publication_hook=crash_before_publish,
                )
            journal_path = root / ".sealed.assetpack.journal.json"
            stage = next(root.glob(".sealed.assetpack-*"))
            loaded = assetpack_module._read_journal_record(  # noqa: SLF001
                journal_path,
                destination,
            )
            self.assertIsNotNone(loaded)
            assert loaded is not None
            ready = loaded[0]
            copying = {**ready, "state": "copying"}
            intent = {
                **ready,
                "state": "intent",
                "stage_identity": None,
            }
            intent_payload = assetpack_module.canonical_json_bytes(intent)
            copying_payload = assetpack_module.canonical_json_bytes(copying)
            ready_payload = assetpack_module.canonical_json_bytes(ready)
            ready_frame = directory_publish_module._journal_frame(ready_payload)  # noqa: SLF001
            invalid = (
                intent_payload
                + directory_publish_module._journal_frame(copying_payload)  # noqa: SLF001
                + directory_publish_module._journal_frame(copying_payload)  # noqa: SLF001
                + ready_frame[: len(ready_frame) // 2]
            )
            with journal_path.open("wb") as target:
                target.write(invalid)
                target.flush()
                os.fsync(target.fileno())

            with self.assertRaises(GenericAssetpackError) as captured:
                recover_generic_assetpack(destination)
            self.assertEqual(
                "assetpack_journal_invalid",
                captured.exception.reason_code,
            )
            self.assertEqual(invalid, journal_path.read_bytes())
            self.assertTrue(stage.is_dir())
            self.assertFalse(destination.exists())

    def test_recovery_requires_exact_complete_d3_journal_history(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(
            prefix="world-forge-d3-history-exact-",
        ) as temporary:
            root = Path(temporary)
            destination = root / "sealed"

            def crash_before_publish(event: str, _relative: str | None) -> None:
                if event == "before_destination_publish":
                    raise RuntimeError("synthetic ready-stage crash")

            with self.assertRaisesRegex(RuntimeError, "ready-stage crash"):
                seal_generic_assetpack(
                    destination,
                    **source,
                    _publication_hook=crash_before_publish,
                )
            journal_path = root / ".sealed.assetpack.journal.json"
            stage = next(root.glob(".sealed.assetpack-*"))
            loaded = assetpack_module._read_journal_record(  # noqa: SLF001
                journal_path,
                destination,
            )
            self.assertIsNotNone(loaded)
            assert loaded is not None
            ready = loaded[0]
            copying = {**ready, "state": "copying"}
            intent = {
                **ready,
                "state": "intent",
                "stage_identity": None,
            }

            def history(*records: dict[str, object]) -> bytes:
                payloads = [assetpack_module.canonical_json_bytes(record) for record in records]
                return (
                    payloads[0]
                    + b"".join(
                        directory_publish_module._journal_frame(payload)  # noqa: SLF001
                        for payload in payloads[1:]
                    )
                )

            conflicting_operation = {
                **intent,
                "operation_id": ("f" * 32 if intent["operation_id"] != "f" * 32 else "e" * 32),
            }
            conflicting_source = {
                **copying,
                "source_manifest_hash": (
                    "f" * 64 if copying["source_manifest_hash"] != "f" * 64 else "e" * 64
                ),
            }
            stage_identity = copying["stage_identity"]
            assert isinstance(stage_identity, dict)
            conflicting_stage = {
                **copying,
                "stage_identity": {
                    **stage_identity,
                    "inode": int(stage_identity["inode"]) + 1,
                },
            }
            variants = {
                "duplicate-intent": history(intent, intent, copying, ready),
                "duplicate-copying": history(intent, copying, copying, ready),
                "duplicate-ready": history(intent, copying, ready, ready),
                "skipped-intent": history(copying, ready),
                "skipped-copying": history(intent, ready),
                "reordered-intent-ready-copying": history(intent, ready, copying),
                "reordered-copying-intent-ready": history(copying, intent, ready),
                "reordered-copying-ready-intent": history(copying, ready, intent),
                "reordered-ready-intent-copying": history(ready, intent, copying),
                "reordered-ready-copying-intent": history(ready, copying, intent),
                "extra-copying-after-ready": history(
                    intent,
                    copying,
                    ready,
                    copying,
                ),
                "conflicting-operation": history(
                    conflicting_operation,
                    copying,
                    ready,
                ),
                "conflicting-source": history(
                    intent,
                    conflicting_source,
                    ready,
                ),
                "conflicting-stage": history(
                    intent,
                    conflicting_stage,
                    ready,
                ),
            }
            for name, invalid in variants.items():
                with self.subTest(name=name):
                    with journal_path.open("wb") as target:
                        target.write(invalid)
                        target.flush()
                        os.fsync(target.fileno())
                    with self.assertRaises(GenericAssetpackError) as captured:
                        recover_generic_assetpack(destination)
                    self.assertEqual(
                        "assetpack_journal_invalid",
                        captured.exception.reason_code,
                    )
                    self.assertEqual(invalid, journal_path.read_bytes())
                    self.assertTrue(stage.is_dir())
                    self.assertFalse(destination.exists())

    def test_terminal_and_rollback_partial_tails_are_preserved_without_mutation(
        self,
    ) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-tail-terminal-") as temporary:
            root = Path(temporary)
            ready_destination = root / "ready"

            def crash_before_publish(event: str, _relative: str | None) -> None:
                if event == "before_destination_publish":
                    raise RuntimeError("synthetic ready-stage crash")

            with self.assertRaisesRegex(RuntimeError, "ready-stage crash"):
                seal_generic_assetpack(
                    ready_destination,
                    **source,
                    _publication_hook=crash_before_publish,
                )
            ready_journal = root / ".ready.assetpack.journal.json"
            ready_stage = next(root.glob(".ready.assetpack-*"))
            partial_extra = directory_publish_module._journal_frame(  # noqa: SLF001
                b'{"state":"unexpected"}\n',
            )[:31]
            with ready_journal.open("ab") as target:
                target.write(partial_extra)
                target.flush()
                os.fsync(target.fileno())
            ready_bytes = ready_journal.read_bytes()

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_journal_invalid",
            ):
                recover_generic_assetpack(ready_destination)
            self.assertFalse(ready_destination.exists())
            self.assertTrue(ready_stage.is_dir())
            self.assertEqual(ready_bytes, ready_journal.read_bytes())

            copying_destination = root / "copying"

            def crash_before_first_file(event: str, relative: str | None) -> None:
                if event == "before_stage_file_write" and relative == "assetpack.json":
                    raise RuntimeError("synthetic empty-copying crash")

            with self.assertRaisesRegex(RuntimeError, "empty-copying crash"):
                seal_generic_assetpack(
                    copying_destination,
                    **source,
                    _publication_hook=crash_before_first_file,
                )
            copying_journal = root / ".copying.assetpack.journal.json"
            copying_stage = next(root.glob(".copying.assetpack-*"))
            loaded = assetpack_module._read_journal_record(
                copying_journal,
                copying_destination,
            )
            self.assertIsNotNone(loaded)
            assert loaded is not None
            ready_payload = assetpack_module.canonical_json_bytes(
                {**loaded[0], "state": "ready"},
            )
            partial_ready = directory_publish_module._journal_frame(  # noqa: SLF001
                ready_payload,
            )[:31]
            with copying_journal.open("ab") as target:
                target.write(partial_ready)
                target.flush()
                os.fsync(target.fileno())
            copying_bytes = copying_journal.read_bytes()

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_recovery_ambiguous",
            ):
                recover_generic_assetpack(copying_destination)
            self.assertTrue(copying_stage.is_dir())
            self.assertEqual([], list(copying_stage.iterdir()))
            self.assertEqual(copying_bytes, copying_journal.read_bytes())

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_rollback_ambiguous",
            ):
                rollback_generic_assetpack(copying_destination)
            self.assertTrue(copying_stage.is_dir())
            self.assertEqual([], list(copying_stage.iterdir()))
            self.assertEqual(copying_bytes, copying_journal.read_bytes())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "post-claim name reappearance is a native Linux test",
    )
    def test_d3_claim_removal_retires_exact_journal_without_path_deletion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="world-forge-d3-journal-retirement-",
        ) as temporary:
            root = Path(temporary)
            journal = root / "journal"
            payload = b'{"state":"intent"}\n'
            identity = directory_publish_module.create_append_only_journal(
                journal,
                payload,
                max_record_bytes=1024,
            )
            with (
                mock.patch.object(
                    directory_publish_module.os,
                    "unlink",
                    side_effect=AssertionError("pathname unlink is forbidden"),
                ),
                mock.patch.object(
                    directory_publish_module.os,
                    "rmdir",
                    side_effect=AssertionError("pathname rmdir is forbidden"),
                ),
            ):
                retained = directory_publish_module.remove_d3_append_only_journal(
                    journal,
                    expected_identity=identity,
                    expected_history=(payload,),
                    max_record_bytes=1024,
                    max_file_bytes=16 * 1024,
                )
            self.assertIsNotNone(retained)
            assert retained is not None
            self.assertFalse(journal.exists())
            self.assertEqual(payload, retained.read_bytes())
            self.assertEqual(
                identity,
                directory_publish_module.file_identity(
                    directory_publish_module.path_file_stat(retained),
                ),
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "recovery-name reappearance is a native Linux test",
    )
    def test_recovery_rechecks_stage_and_journal_names_before_success(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(
            prefix="world-forge-d3-recovery-reappear-",
        ) as temporary:
            root = Path(temporary)

            def crash_before_first_file(event: str, relative: str | None) -> None:
                if event == "before_stage_file_write" and relative == "assetpack.json":
                    raise RuntimeError("synthetic empty-copying crash")

            stage_destination = root / "stage"
            with self.assertRaisesRegex(RuntimeError, "empty-copying crash"):
                seal_generic_assetpack(
                    stage_destination,
                    **source,
                    _publication_hook=crash_before_first_file,
                )
            stage_path = next(root.glob(".stage.assetpack-*"))
            stage_journal = root / ".stage.assetpack.journal.json"
            real_remove_stage = assetpack_module.remove_verified_empty_directory

            def remove_then_reappear(
                path: Path,
                identity: tuple[int, int],
                *,
                retained_parent: object | None = None,
            ) -> None:
                real_remove_stage(
                    path,
                    identity,
                    retained_parent=retained_parent,
                )
                path.mkdir()

            with (
                mock.patch.object(
                    assetpack_module,
                    "remove_verified_empty_directory",
                    remove_then_reappear,
                ),
                self.assertRaises(GenericAssetpackError) as captured_stage,
            ):
                recover_generic_assetpack(stage_destination)
            self.assertEqual(
                "assetpack_recovery_required",
                captured_stage.exception.reason_code,
            )
            self.assertTrue(stage_path.is_dir())
            self.assertTrue(stage_journal.is_file())

            def crash_after_stage(event: str, _relative: str | None) -> None:
                if event == "after_stage_created":
                    raise RuntimeError("synthetic intent-stage crash")

            journal_destination = root / "journal"
            with self.assertRaisesRegex(RuntimeError, "intent-stage crash"):
                seal_generic_assetpack(
                    journal_destination,
                    **source,
                    _publication_hook=crash_after_stage,
                )
            intent_stage = next(root.glob(".journal.assetpack-*"))
            retained_stage = intent_stage.with_name(f"{intent_stage.name}-retained")
            intent_stage.replace(retained_stage)
            journal_path = root / ".journal.assetpack.journal.json"
            real_remove_journal = assetpack_module.remove_d3_append_only_journal

            def remove_journal_then_reappear(*args: object, **kwargs: object) -> None:
                real_remove_journal(*args, **kwargs)
                journal_path.write_bytes(b"foreign journal replacement")

            with (
                mock.patch.object(
                    assetpack_module,
                    "remove_d3_append_only_journal",
                    remove_journal_then_reappear,
                ),
                self.assertRaises(GenericAssetpackError) as captured_journal,
            ):
                recover_generic_assetpack(journal_destination)
            self.assertEqual(
                "assetpack_publication_indeterminate",
                captured_journal.exception.reason_code,
            )
            self.assertEqual(
                b"foreign journal replacement",
                journal_path.read_bytes(),
            )
            self.assertTrue(retained_stage.is_dir())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "unbound intent path substitutions are a native Linux test",
    )
    def test_intent_recovery_never_claims_an_unbound_stage_path(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-intent-") as temporary:
            root = Path(temporary)
            destination = root / "sealed"

            def crash_after_stage(event: str, _relative: str | None) -> None:
                if event == "after_stage_created":
                    raise RuntimeError("synthetic intent-stage crash")

            with self.assertRaisesRegex(RuntimeError, "intent-stage crash"):
                seal_generic_assetpack(
                    destination,
                    **source,
                    _publication_hook=crash_after_stage,
                )
            journal_path = root / ".sealed.assetpack.journal.json"
            stage = next(root.glob(".sealed.assetpack-*"))

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_rollback_ambiguous",
            ):
                rollback_generic_assetpack(destination)
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_recovery_ambiguous",
            ):
                recover_generic_assetpack(destination)
            self.assertTrue(stage.is_dir())
            self.assertTrue(journal_path.is_file())

            retained = stage.with_name(f"{stage.name}-retained")
            stage.replace(retained)
            stage.mkdir()
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_recovery_ambiguous",
            ):
                recover_generic_assetpack(destination)
            self.assertTrue(stage.is_dir())
            self.assertEqual([], list(stage.iterdir()))

            (stage / "foreign.txt").write_text("foreign", encoding="utf-8")
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_recovery_ambiguous",
            ):
                recover_generic_assetpack(destination)
            self.assertEqual(
                "foreign",
                (stage / "foreign.txt").read_text(encoding="utf-8"),
            )

            shutil.rmtree(stage)
            stage.symlink_to(retained, target_is_directory=True)
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_recovery_ambiguous",
            ):
                recover_generic_assetpack(destination)
            self.assertTrue(stage.is_symlink())
            self.assertTrue(journal_path.is_file())

            stage.unlink()
            self.assertIsNone(recover_generic_assetpack(destination))
            self.assertFalse(journal_path.exists())
            self.assertTrue(retained.is_dir())

    @unittest.skipUnless(
        (sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt",
        "copying-stage rollback requires native Linux or Windows primitives",
    )
    def test_copying_empty_stage_is_safely_rolled_back_by_identity(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-copying-") as temporary:
            root = Path(temporary)

            def crash_before_first_file(event: str, relative: str | None) -> None:
                if event == "before_stage_file_write" and relative == "assetpack.json":
                    raise RuntimeError("synthetic empty-copying crash")

            recovery_destination = root / "recover"
            with self.assertRaisesRegex(RuntimeError, "empty-copying crash"):
                seal_generic_assetpack(
                    recovery_destination,
                    **source,
                    _publication_hook=crash_before_first_file,
                )
            recovery_stage = next(root.glob(".recover.assetpack-*"))
            self.assertEqual([], list(recovery_stage.iterdir()))
            recovery_journal = root / ".recover.assetpack.journal.json"
            if sys.platform.startswith("linux") and os.name == "posix":
                with self.assertRaises(GenericAssetpackError) as raised:
                    recover_generic_assetpack(recovery_destination)
                self.assertEqual("assetpack_recovery_required", raised.exception.reason_code)
                self.assertIn("retained", raised.exception.detail)
                self.assertEqual(
                    recovery_stage.name,
                    raised.exception.recovery_evidence["stage"]["locator"],
                )
                self.assertEqual(
                    recovery_journal.name,
                    raised.exception.recovery_evidence["journal"]["locator"],
                )
                self.assertTrue(recovery_stage.is_dir())
                self.assertTrue(recovery_journal.is_file())
            else:
                self.assertIsNone(recover_generic_assetpack(recovery_destination))
                self.assertFalse(recovery_stage.exists())
                self.assertFalse(recovery_journal.exists())

            rollback_destination = root / "rollback"
            with self.assertRaisesRegex(RuntimeError, "empty-copying crash"):
                seal_generic_assetpack(
                    rollback_destination,
                    **source,
                    _publication_hook=crash_before_first_file,
                )
            rollback_stage = next(root.glob(".rollback.assetpack-*"))
            rollback_journal = root / ".rollback.assetpack.journal.json"
            if sys.platform.startswith("linux") and os.name == "posix":
                with self.assertRaises(GenericAssetpackError) as raised:
                    rollback_generic_assetpack(rollback_destination)
                self.assertEqual(
                    "assetpack_rollback_recovery_required",
                    raised.exception.reason_code,
                )
                self.assertIn("retained", raised.exception.detail)
                self.assertTrue(rollback_stage.is_dir())
                self.assertTrue(rollback_journal.is_file())
            else:
                self.assertEqual(
                    "rolled_back",
                    rollback_generic_assetpack(rollback_destination)["status"],
                )
                self.assertFalse(rollback_stage.exists())
                self.assertFalse(rollback_journal.exists())

            ambiguous_destination = root / "ambiguous"
            with self.assertRaisesRegex(RuntimeError, "empty-copying crash"):
                seal_generic_assetpack(
                    ambiguous_destination,
                    **source,
                    _publication_hook=crash_before_first_file,
                )
            ambiguous_stage = next(root.glob(".ambiguous.assetpack-*"))
            foreign = ambiguous_stage / "foreign.txt"
            foreign.write_text("foreign", encoding="utf-8")
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_recovery_ambiguous",
            ):
                recover_generic_assetpack(ambiguous_destination)
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_rollback_ambiguous",
            ):
                rollback_generic_assetpack(ambiguous_destination)
            self.assertEqual("foreign", foreign.read_text(encoding="utf-8"))
            self.assertTrue((root / ".ambiguous.assetpack.journal.json").is_file())

    @unittest.skipUnless(
        (sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt",
        "retained-parent cleanup requires Linux or Windows primitives",
    )
    def test_recovery_and_rollback_cleanup_reuse_lock_parent_authority(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-cleanup-parent-") as temporary:
            root = Path(temporary)
            output_parent = root / "outputs"
            output_parent.mkdir()

            def crash_before_first_file(event: str, relative: str | None) -> None:
                if event == "before_stage_file_write" and relative == "assetpack.json":
                    raise RuntimeError("synthetic empty-copying crash")

            for operation in ("recover", "rollback"):
                with self.subTest(operation=operation):
                    destination = output_parent / operation
                    with self.assertRaisesRegex(RuntimeError, "empty-copying crash"):
                        seal_generic_assetpack(
                            destination,
                            **source,
                            _publication_hook=crash_before_first_file,
                        )
                    with mock.patch.object(
                        assetpack_module,
                        "open_verified_output_parent",
                        wraps=assetpack_module.open_verified_output_parent,
                    ) as tracked_open:
                        if sys.platform.startswith("linux") and os.name == "posix":
                            expected_code = (
                                "assetpack_recovery_required"
                                if operation == "recover"
                                else "assetpack_rollback_recovery_required"
                            )
                            command = (
                                recover_generic_assetpack
                                if operation == "recover"
                                else rollback_generic_assetpack
                            )
                            with self.assertRaises(GenericAssetpackError) as raised:
                                command(destination)
                            self.assertEqual(expected_code, raised.exception.reason_code)
                        elif operation == "recover":
                            self.assertIsNone(recover_generic_assetpack(destination))
                        else:
                            self.assertEqual(
                                "rolled_back",
                                rollback_generic_assetpack(destination)["status"],
                            )
                    self.assertEqual(
                        1,
                        tracked_open.call_count,
                        "cleanup must reuse the parent retained by the destination lock",
                    )

    @unittest.skipUnless(
        (sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt",
        "empty-stage removal requires native Linux or Windows primitives",
    )
    def test_empty_stage_removal_never_succeeds_for_missing_or_nonempty_path(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-empty-stage-") as temporary:
            root = Path(temporary)
            nonempty = root / "nonempty"
            nonempty.mkdir()
            foreign = nonempty / "foreign.txt"
            foreign.write_text("foreign", encoding="utf-8")
            identity = directory_publish_module.directory_identity(
                nonempty,
                context="test nonempty stage",
            )

            with self.assertRaisesRegex(
                DirectoryPublishError,
                "no longer empty",
            ):
                directory_publish_module.remove_verified_empty_directory(
                    nonempty,
                    identity,
                )
            self.assertEqual("foreign", foreign.read_text(encoding="utf-8"))

            missing = root / "missing"
            with self.assertRaisesRegex(
                DirectoryPublishError,
                "disappeared",
            ):
                directory_publish_module.remove_verified_empty_directory(
                    missing,
                    identity,
                )

    @unittest.skipUnless(
        (sys.platform.startswith("linux") and os.name == "posix") or os.name == "nt",
        "cleanup parent retention requires Linux or Windows primitives",
    )
    def test_cleanup_fallbacks_never_request_parent_creation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-cleanup-open-") as temporary:
            root = Path(temporary)
            observed_create: list[bool | None] = []
            real_open = directory_publish_module.open_verified_output_parent

            def track_parent_open(path: Path, **kwargs: object):
                observed_create.append(kwargs.get("create"))
                return real_open(path, **kwargs)

            empty = root / "empty"
            empty.mkdir()
            empty_info = empty.stat()
            tree = root / "tree"
            tree.mkdir()
            (tree / "owned.txt").write_bytes(b"owned")
            tree_info = tree.stat()
            journal = root / ".sealed.assetpack.journal.json"
            journal_payload = b'{"state":"intent"}\n'
            journal_identity = directory_publish_module.create_append_only_journal(
                journal,
                journal_payload,
                max_record_bytes=1024,
            )

            with mock.patch.object(
                directory_publish_module,
                "open_verified_output_parent",
                side_effect=track_parent_open,
            ):
                if sys.platform.startswith("linux") and os.name == "posix":
                    with self.assertRaises(
                        directory_publish_module.DirectoryPublishRecoveryRequiredError
                    ):
                        directory_publish_module.remove_verified_empty_directory(
                            empty,
                            (empty_info.st_dev, empty_info.st_ino),
                        )
                    with self.assertRaises(
                        directory_publish_module.DirectoryPublishRecoveryRequiredError
                    ):
                        directory_publish_module.quarantine_and_remove_verified_directory(
                            tree,
                            (tree_info.st_dev, tree_info.st_ino),
                            verify=lambda path: self.assertEqual(
                                b"owned",
                                (path / "owned.txt").read_bytes(),
                            ),
                        )
                else:
                    directory_publish_module.remove_verified_empty_directory(
                        empty,
                        (empty_info.st_dev, empty_info.st_ino),
                    )
                    directory_publish_module.quarantine_and_remove_verified_directory(
                        tree,
                        (tree_info.st_dev, tree_info.st_ino),
                        verify=lambda path: self.assertEqual(
                            b"owned",
                            (path / "owned.txt").read_bytes(),
                        ),
                    )
                directory_publish_module.remove_d3_append_only_journal(
                    journal,
                    expected_identity=journal_identity,
                    expected_history=(journal_payload,),
                    max_record_bytes=1024,
                    max_file_bytes=4096,
                )

            self.assertEqual([False, False, False], observed_create)
            if sys.platform.startswith("linux") and os.name == "posix":
                self.assertTrue(empty.is_dir())
                self.assertTrue(tree.is_dir())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "retained-parent removal redirection is a native Linux test",
    )
    def test_empty_stage_removal_never_redirects_into_a_replacement_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-empty-redirect-") as temporary:
            root = Path(temporary)
            output_parent = root / "outputs"
            displaced_parent = root / "outputs-displaced"
            output_parent.mkdir()
            stage = output_parent / ".sealed.assetpack-stage"
            stage.mkdir()
            info = stage.stat()
            expected_identity = (info.st_dev, info.st_ino)
            real_remove = directory_publish_module._remove_retained_directory  # noqa: SLF001
            swapped = False

            def swap_before_remove(*args: object, **kwargs: object) -> None:
                nonlocal swapped
                output_parent.rename(displaced_parent)
                output_parent.mkdir()
                (displaced_parent / stage.name).rename(stage)
                swapped = True
                real_remove(*args, **kwargs)

            with self.assertRaises((DirectoryPublishError, asset_io_module.AssetContractError)):
                with asset_io_module.open_verified_output_parent(
                    output_parent,
                    create=False,
                ) as parent:
                    with mock.patch.object(
                        directory_publish_module,
                        "_remove_retained_directory",
                        side_effect=swap_before_remove,
                    ):
                        directory_publish_module.remove_verified_empty_directory(
                            stage,
                            expected_identity,
                            retained_parent=parent,
                        )
            self.assertTrue(swapped)
            self.assertTrue(
                stage.is_dir(),
                "cleanup must not delete the exact stage after it moves under a replacement parent",
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "retained-parent removal redirection is a native Linux test",
    )
    def test_nonempty_stage_removal_never_redirects_into_a_replacement_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-tree-redirect-") as temporary:
            root = Path(temporary)
            output_parent = root / "outputs"
            displaced_parent = root / "outputs-displaced"
            output_parent.mkdir()
            stage = output_parent / ".sealed.assetpack-stage"
            stage.mkdir()
            owned = stage / "owned.txt"
            owned.write_bytes(b"owned")
            info = stage.stat()
            expected_identity = (info.st_dev, info.st_ino)
            real_remove = directory_publish_module._remove_retained_directory  # noqa: SLF001
            swapped = False

            def swap_before_remove(*args: object, **kwargs: object) -> None:
                nonlocal swapped
                output_parent.rename(displaced_parent)
                output_parent.mkdir()
                (displaced_parent / stage.name).rename(stage)
                swapped = True
                real_remove(*args, **kwargs)

            def verify_owned(path: Path) -> None:
                self.assertEqual(b"owned", (path / "owned.txt").read_bytes())

            with self.assertRaises((DirectoryPublishError, asset_io_module.AssetContractError)):
                with asset_io_module.open_verified_output_parent(
                    output_parent,
                    create=False,
                ) as parent:
                    with mock.patch.object(
                        directory_publish_module,
                        "_remove_retained_directory",
                        side_effect=swap_before_remove,
                    ):
                        directory_publish_module.quarantine_and_remove_verified_directory(
                            stage,
                            expected_identity,
                            verify=verify_owned,
                            retained_parent=parent,
                        )
            self.assertTrue(swapped)
            self.assertEqual(
                b"owned",
                owned.read_bytes(),
                "cleanup must not delete an owned tree after it moves under a replacement parent",
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "fail-closed retained cleanup is a native Linux test",
    )
    def test_nonempty_stage_fails_before_path_verifier_or_child_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-no-path-verify-") as temporary:
            root = Path(temporary)
            stage = root / ".sealed.assetpack-stage"
            stage.mkdir()
            owned = stage / "owned.txt"
            owned.write_bytes(b"owned")
            info = stage.stat()
            expected_identity = (info.st_dev, info.st_ino)
            callback_called = False

            def verify_owned(_path: Path) -> None:
                nonlocal callback_called
                callback_called = True

            with self.assertRaises(
                directory_publish_module.DirectoryPublishRecoveryRequiredError,
            ):
                directory_publish_module.quarantine_and_remove_verified_directory(
                    stage,
                    expected_identity,
                    verify=verify_owned,
                )
            self.assertFalse(callback_called)
            self.assertEqual(b"owned", owned.read_bytes())
            self.assertTrue(stage.is_dir())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "fail-closed retained cleanup is a native Linux test",
    )
    def test_nonempty_stage_fails_before_retained_verifier_or_child_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-no-fd-verify-") as temporary:
            root = Path(temporary)
            stage = root / ".sealed.assetpack-stage"
            stage.mkdir()
            foreign = stage / "foreign.txt"
            foreign.write_bytes(b"foreign")
            info = stage.stat()
            expected_identity = (info.st_dev, info.st_ino)
            callback_called = False

            def verify_owned(_path: Path, _retained_root_fd: int | None) -> None:
                nonlocal callback_called
                callback_called = True

            with self.assertRaises(
                directory_publish_module.DirectoryPublishRecoveryRequiredError,
            ):
                directory_publish_module.quarantine_and_remove_verified_directory(
                    stage,
                    expected_identity,
                    verify_retained=verify_owned,
                )
            self.assertFalse(callback_called)
            self.assertEqual(b"foreign", foreign.read_bytes())
            self.assertTrue(stage.is_dir())

    @unittest.skipUnless(os.name == "posix", "retained descriptor verification is POSIX-only")
    def test_retained_fd_verifiers_close_child_when_fstat_fails(self) -> None:
        real_open = os.open
        real_close = os.close
        real_fstat = os.fstat
        real_descriptor_file_stat = assetpack_module.descriptor_file_stat

        def require_closed(descriptor: int) -> None:
            try:
                real_fstat(descriptor)
            except OSError:
                return
            real_close(descriptor)
            self.fail(f"retained verifier leaked descriptor {descriptor}")

        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary) / "pack"
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "asset.bin").write_bytes(b"asset")
            root_fd = real_open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                for operation in ("relative_parent", "tree_snapshot"):
                    captured: list[int] = []

                    def capture_open(
                        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                        flags: int,
                        mode: int = 0o777,
                        *,
                        dir_fd: int | None = None,
                        _captured: list[int] = captured,
                    ) -> int:
                        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                        if path == "nested":
                            _captured.append(descriptor)
                        return descriptor

                    def fail_child_fstat(
                        descriptor: int,
                        _captured: list[int] = captured,
                    ):
                        if _captured and descriptor == _captured[-1]:
                            raise OSError("synthetic retained-child fstat failure")
                        return real_descriptor_file_stat(descriptor)

                    with (
                        self.subTest(operation=operation),
                        mock.patch.object(assetpack_module.os, "open", side_effect=capture_open),
                        mock.patch.object(
                            assetpack_module,
                            "descriptor_file_stat",
                            side_effect=fail_child_fstat,
                        ),
                    ):
                        expected_error = (
                            GenericAssetpackError if operation == "relative_parent" else OSError
                        )
                        with self.assertRaises(expected_error):
                            if operation == "relative_parent":
                                with assetpack_module._open_retained_relative_parent(
                                    root_fd,
                                    "nested/asset.bin",
                                ):
                                    self.fail("retained parent unexpectedly opened")
                            else:
                                assetpack_module._snapshot_exact_tree_from_fd(root_fd)
                    self.assertEqual(len(captured), 1)
                    require_closed(captured[0])
            finally:
                real_close(root_fd)

    @unittest.skipUnless(os.name == "posix", "retained descriptor verification is POSIX-only")
    def test_retained_relative_parent_closes_child_when_parent_close_fails(self) -> None:
        real_open = os.open
        real_close = os.close
        real_dup = os.dup
        real_fstat = os.fstat
        retained_parent_fd: int | None = None
        child_fd: int | None = None
        injected = False

        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            root = Path(temporary) / "pack"
            (root / "nested").mkdir(parents=True)
            (root / "nested" / "asset.bin").write_bytes(b"asset")
            root_fd = real_open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))

            def capture_dup(descriptor: int) -> int:
                nonlocal retained_parent_fd
                duplicated = real_dup(descriptor)
                retained_parent_fd = duplicated
                return duplicated

            def capture_open(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal child_fd
                descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                if path == "nested":
                    child_fd = descriptor
                return descriptor

            def fail_parent_close(descriptor: int) -> None:
                nonlocal injected
                if descriptor == retained_parent_fd and not injected:
                    injected = True
                    real_close(descriptor)
                    raise OSError("synthetic retained-parent close failure")
                real_close(descriptor)

            try:
                with (
                    mock.patch.object(assetpack_module.os, "dup", side_effect=capture_dup),
                    mock.patch.object(assetpack_module.os, "open", side_effect=capture_open),
                    mock.patch.object(
                        assetpack_module.os,
                        "close",
                        side_effect=fail_parent_close,
                    ),
                    self.assertRaises(GenericAssetpackError),
                ):
                    with assetpack_module._open_retained_relative_parent(
                        root_fd,
                        "nested/asset.bin",
                    ):
                        self.fail("retained parent unexpectedly opened")
                self.assertTrue(injected)
                self.assertIsNotNone(child_fd)
                assert child_fd is not None
                with self.assertRaises(OSError):
                    real_fstat(child_fd)
            finally:
                if child_fd is not None:
                    try:
                        real_close(child_fd)
                    except OSError:
                        pass
                real_close(root_fd)

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "identity-bound deletion is a native Linux test",
    )
    def test_linux_cleanup_fails_before_a_mutable_child_unlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-child-unlink-") as temporary:
            parent_path = Path(temporary) / "outputs"
            parent_path.mkdir()
            stage = parent_path / ".sealed.assetpack-stage"
            stage.mkdir()
            (stage / "owned.bin").write_bytes(b"owned")
            expected_identity = (stage.stat().st_dev, stage.stat().st_ino)
            real_unlink = os.unlink
            swapped = False

            def swap_before_unlink(
                name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                *,
                dir_fd: int | None = None,
            ) -> None:
                nonlocal swapped
                if (
                    isinstance(name, str)
                    and name.startswith(".worldforge-delete-")
                    and dir_fd is not None
                ):
                    swapped = True
                    os.rename(
                        name,
                        f"{name}.owned-away",
                        src_dir_fd=dir_fd,
                        dst_dir_fd=dir_fd,
                    )
                    foreign_fd = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=dir_fd,
                    )
                    os.close(foreign_fd)
                real_unlink(name, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    directory_publish_module.os,
                    "unlink",
                    side_effect=swap_before_unlink,
                ),
                self.assertRaises((DirectoryPublishError, asset_io_module.AssetContractError)),
            ):
                with asset_io_module.open_verified_output_parent(
                    parent_path,
                    create=False,
                ) as parent:
                    directory_publish_module.quarantine_and_remove_verified_directory(
                        stage,
                        expected_identity,
                        verify_retained=lambda _path, _fd: None,
                        retained_parent=parent,
                    )
            self.assertFalse(swapped, "cleanup must fail before pathname-based unlink")
            self.assertTrue(stage.is_dir())
            self.assertEqual((stage / "owned.bin").read_bytes(), b"owned")

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "identity-bound deletion is a native Linux test",
    )
    def test_linux_cleanup_fails_before_a_mutable_root_rmdir(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-root-rmdir-") as temporary:
            parent_path = Path(temporary) / "outputs"
            parent_path.mkdir()
            stage = parent_path / ".sealed.assetpack-stage"
            stage.mkdir()
            expected_identity = (stage.stat().st_dev, stage.stat().st_ino)
            real_rmdir = os.rmdir
            swapped = False

            def swap_before_rmdir(
                name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                *,
                dir_fd: int | None = None,
            ) -> None:
                nonlocal swapped
                if (
                    isinstance(name, str)
                    and name.startswith(".worldforge-delete-")
                    and dir_fd is not None
                ):
                    swapped = True
                    os.rename(
                        name,
                        f"{name}.owned-away",
                        src_dir_fd=dir_fd,
                        dst_dir_fd=dir_fd,
                    )
                    os.mkdir(name, dir_fd=dir_fd)
                real_rmdir(name, dir_fd=dir_fd)

            with (
                mock.patch.object(
                    directory_publish_module.os,
                    "rmdir",
                    side_effect=swap_before_rmdir,
                ),
                self.assertRaises((DirectoryPublishError, asset_io_module.AssetContractError)),
            ):
                with asset_io_module.open_verified_output_parent(
                    parent_path,
                    create=False,
                ) as parent:
                    directory_publish_module.remove_verified_empty_directory(
                        stage,
                        expected_identity,
                        retained_parent=parent,
                    )
            self.assertFalse(swapped, "cleanup must fail before pathname-based rmdir")
            self.assertTrue(stage.is_dir())

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "retained journal archival is a native Linux test",
    )
    def test_linux_journal_retirement_archives_without_pathname_deletion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-journal-archive-") as temporary:
            root = Path(temporary)
            journal = root / ".sealed.assetpack.journal.json"
            payload = b'{"state":"ready"}\n'
            identity = directory_publish_module.create_append_only_journal(
                journal,
                payload,
                max_record_bytes=1024,
            )
            framed_payload = journal.read_bytes()
            expected_retained = directory_publish_module.retained_journal_evidence_path(
                journal,
                identity,
            )

            with (
                mock.patch.object(
                    directory_publish_module.os,
                    "unlink",
                    side_effect=AssertionError("journal archival must not unlink"),
                    create=True,
                ),
                mock.patch.object(
                    directory_publish_module.os,
                    "rmdir",
                    side_effect=AssertionError("journal archival must not rmdir"),
                    create=True,
                ),
            ):
                retained = directory_publish_module.remove_d3_append_only_journal(
                    journal,
                    expected_identity=identity,
                    expected_history=(payload,),
                    max_record_bytes=1024,
                    max_file_bytes=16 * 1024,
                )

            self.assertIsNotNone(retained)
            assert retained is not None
            self.assertEqual(expected_retained, retained)
            self.assertFalse(journal.exists())
            self.assertEqual(framed_payload, retained.read_bytes())
            self.assertEqual(
                identity,
                directory_publish_module.file_identity(
                    directory_publish_module.path_file_stat(retained)
                ),
            )

    def test_journal_crash_boundaries_are_recoverable_and_never_commit_a_state(
        self,
    ) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-crash-") as temporary:
            root = Path(temporary)

            intent_destination = root / "intent-gap"

            def crash_after_stage(event: str, _relative: str | None) -> None:
                if event == "after_stage_created":
                    raise RuntimeError("synthetic intent-stage crash")

            with self.assertRaisesRegex(RuntimeError, "intent-stage crash"):
                seal_generic_assetpack(
                    intent_destination,
                    **source,
                    _publication_hook=crash_after_stage,
                )
            intent_journal = root / ".intent-gap.assetpack.journal.json"
            loaded_intent = assetpack_module._read_journal_record(
                intent_journal,
                intent_destination,
            )
            self.assertIsNotNone(loaded_intent)
            self.assertEqual(loaded_intent[0]["state"], "intent")
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_recovery_ambiguous",
            ):
                recover_generic_assetpack(intent_destination)
            intent_stage = next(root.glob(".intent-gap.assetpack-*"))
            retained_intent_stage = intent_stage.with_name(
                f"{intent_stage.name}-retained",
            )
            intent_stage.replace(retained_intent_stage)
            self.assertIsNone(recover_generic_assetpack(intent_destination))
            self.assertFalse(intent_journal.exists())
            self.assertEqual(
                list(root.glob(".intent-gap.assetpack-*")),
                [retained_intent_stage],
            )
            self.assertTrue(retained_intent_stage.is_dir())

            ready_destination = root / "ready-gap"

            def crash_before_publish(event: str, _relative: str | None) -> None:
                if event == "before_destination_publish":
                    raise RuntimeError("synthetic ready-stage crash")

            with self.assertRaisesRegex(RuntimeError, "ready-stage crash"):
                seal_generic_assetpack(
                    ready_destination,
                    **source,
                    _publication_hook=crash_before_publish,
                )
            ready_journal = root / ".ready-gap.assetpack.journal.json"
            loaded_ready = assetpack_module._read_journal_record(
                ready_journal,
                ready_destination,
            )
            self.assertIsNotNone(loaded_ready)
            self.assertEqual(loaded_ready[0]["state"], "ready")
            with recover_generic_assetpack(ready_destination) as recovered:
                self.assertEqual(recovered.status, "sealed")
            self.assertFalse(ready_journal.exists())

            removal_destination = root / "removal-gap"

            def crash_before_removal(event: str, _relative: str | None) -> None:
                if event == "before_journal_removal":
                    raise RuntimeError("synthetic journal-removal crash")

            with self.assertRaisesRegex(RuntimeError, "journal-removal crash"):
                seal_generic_assetpack(
                    removal_destination,
                    **source,
                    _publication_hook=crash_before_removal,
                )
            removal_journal = root / ".removal-gap.assetpack.journal.json"
            loaded_removal = assetpack_module._read_journal_record(
                removal_journal,
                removal_destination,
            )
            self.assertIsNotNone(loaded_removal)
            self.assertEqual(loaded_removal[0]["state"], "ready")
            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_rollback_committed",
            ):
                rollback_generic_assetpack(removal_destination)
            self.assertTrue(removal_destination.is_dir())
            self.assertTrue(removal_journal.is_file())
            with recover_generic_assetpack(removal_destination) as recovered:
                self.assertEqual(recovered.status, "sealed")
            self.assertFalse(removal_journal.exists())

    def test_destination_lock_excludes_concurrent_coordinators(self) -> None:
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-locking-") as temporary:
            destination = Path(temporary) / "sealed"
            entered = threading.Event()
            release = threading.Event()
            result: list[str] = []

            def holder() -> None:
                with assetpack_module._destination_lock(destination):
                    entered.set()
                    release.wait(timeout=10)

            thread = threading.Thread(target=holder)
            thread.start()
            self.assertTrue(entered.wait(timeout=10))
            try:
                with self.assertRaisesRegex(
                    GenericAssetpackError,
                    "assetpack_publication_busy",
                ):
                    with assetpack_module._destination_lock(destination):
                        pass
                result.append("excluded")
            finally:
                release.set()
                thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, ["excluded"])
            with assetpack_module._destination_lock(destination) as lock:
                lock.require_binding()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and os.name == "posix",
        "held-lock pathname replacement is a native Linux test",
    )
    def test_replaced_held_lock_aborts_before_first_shared_mutation(self) -> None:
        source = self._fixture_source("abstract-puzzle")
        with tempfile.TemporaryDirectory(prefix="world-forge-d3-lock-") as temporary:
            root = Path(temporary)
            destination = root / "sealed"
            lock_path = root / ".sealed.assetpack.lock"

            def replace_lock(event: str, _relative: str | None) -> None:
                if event != "after_lock_acquired":
                    return
                lock_path.unlink()
                lock_path.write_bytes(b"\0")

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_lock_changed",
            ):
                seal_generic_assetpack(
                    destination,
                    **source,
                    _publication_hook=replace_lock,
                )
            self.assertFalse(destination.exists())
            self.assertFalse((root / ".sealed.assetpack.journal.json").exists())
            self.assertEqual(list(root.glob(".sealed.assetpack-*")), [])

            staged_destination = root / "staged"
            staged_lock = root / ".staged.assetpack.lock"
            replaced = False

            def replace_lock_after_directory(
                event: str,
                relative: str | None,
            ) -> None:
                nonlocal replaced
                if event != "after_stage_directory_created" or relative != "assets" or replaced:
                    return
                staged_lock.unlink()
                staged_lock.write_bytes(b"\0")
                replaced = True

            with self.assertRaisesRegex(
                GenericAssetpackError,
                "assetpack_lock_changed",
            ):
                seal_generic_assetpack(
                    staged_destination,
                    **source,
                    _publication_hook=replace_lock_after_directory,
                )
            self.assertTrue(replaced)
            self.assertFalse(staged_destination.exists())
            staged_journal = root / ".staged.assetpack.journal.json"
            self.assertTrue(staged_journal.is_file())
            if sys.platform.startswith("linux") and os.name == "posix":
                with self.assertRaises(GenericAssetpackError) as raised:
                    rollback_generic_assetpack(staged_destination)
                self.assertEqual(
                    "assetpack_rollback_recovery_required",
                    raised.exception.reason_code,
                )
                self.assertEqual(
                    staged_journal.name,
                    raised.exception.recovery_evidence["journal"]["locator"],
                )
                self.assertTrue(next(root.glob(".staged.assetpack-*")).is_dir())
            else:
                self.assertEqual(
                    rollback_generic_assetpack(staged_destination)["status"],
                    "rolled_back",
                )
