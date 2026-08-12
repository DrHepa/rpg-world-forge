from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gamepack_runtime.game_package import build_game_package_from_standalone
from gamepack_runtime.headless import (
    build_game_execution_script,
    serialize_game_execution_script,
)
from scripts.generate_generic_runtime_schemas import build_schemas
from tests.test_generic_creation_workflow import _puzzle_asset_graph
from tests.test_multigenre_game_runtime_bundle import _build_bundle
from tests.test_multigenre_standalone_materialization import (
    _headless_scenarios,
    _ready_materialization,
)
from worldforge.__main__ import _resolve_generic_assetpack_cli_source
from worldforge.contract_catalog import load_contract_catalog
from worldforge.creation_contracts import canonical_creation_hash, load_creation_project
from worldforge.creation_readiness import (
    build_creation_readiness,
    validate_creation_readiness,
)
from worldforge.game_package import extract_game_package
from worldforge.game_package_extraction import (
    build_game_package_extraction_evidence,
)
from worldforge.game_runtime_bundle import verify_game_runtime_bundle
from worldforge.generic_assetpack import verify_generic_assetpack
from worldforge.generic_headless import (
    VerifiedHeadlessEvidenceSet,
    build_headless_authority_result,
    build_headless_evidence_set,
)
from worldforge.runtime_support_authority import (
    RUNTIME_SUPPORT_AUTHORITY_FORMAT,
    RuntimeSupportAuthorityError,
    VerifiedRuntimeSupportAuthority,
    attach_native_evidence,
    attach_verified_game_package,
    attach_verified_headless_evidence,
    derive_runtime_evidence,
    derive_runtime_support_report,
    initialize_runtime_support_authority,
    serialize_runtime_support_authority,
    validate_runtime_support_authority_document,
)
from worldforge.standalone_game import materialize_game

ROOT = Path(__file__).resolve().parents[1]


def _bundle_document(bundle: object, relative: str) -> dict[str, object]:
    value = json.loads(bundle.read_bytes(relative))
    if type(value) is not dict:
        raise AssertionError(f"{relative} is not a JSON object")
    return value


class RuntimeSupportAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory(prefix="world-forge-runtime-authority-")
        cls.root = Path(cls._temporary.name)
        cls._materialization_context = _ready_materialization("abstract-puzzle", cls.root)
        cls.materialization = cls._materialization_context.__enter__()
        cls.bundle_root = cls.materialization.root / "runtime-bundle"

        bundle = verify_game_runtime_bundle(cls.bundle_root)
        try:
            adapter_path = bundle.manifest["contracts"]["runtime_adapter"]["path"]
            cls.gamepack = _bundle_document(bundle, "contracts/gamepack.json")
            cls.inventory = json.loads(
                (
                    ROOT / "examples/multigenre-contracts/abstract-puzzle/assets/inventory.json"
                ).read_text(encoding="utf-8")
            )
            cls.composition = _bundle_document(bundle, "contracts/runtime-composition.json")
            cls.registry = _bundle_document(
                bundle,
                "contracts/runtime-adapter-registry.json",
            )
            cls.snapshot = _bundle_document(bundle, "contracts/runtime-snapshot.json")
            cls.adapter = _bundle_document(bundle, adapter_path)
            script = build_game_execution_script(
                bundle.manifest,
                gamepack=cls.gamepack,
                composition=cls.composition,
                adapter=cls.adapter,
                runtime_snapshot=cls.snapshot,
                scenarios=_headless_scenarios("abstract-puzzle"),
            )
        finally:
            bundle.close()

        fixture_root = ROOT / "examples/multigenre-contracts/abstract-puzzle"
        source = _resolve_generic_assetpack_cli_source(fixture_root / "assets/manifest.json")
        cls.assetpack = verify_generic_assetpack(cls.bundle_root / "assetpack")
        cls.asset_release_authority = source["release_authority"]
        cls.base = initialize_runtime_support_authority(
            gamepack=cls.gamepack,
            inventory=cls.inventory,
            composition=cls.composition,
            registry=cls.registry,
            snapshot=cls.snapshot,
            verified_assetpack=cls.assetpack,
            asset_release_authority=cls.asset_release_authority,
        )

        script_path = cls.root / "abstract-puzzle-execution-script.json"
        script_path.write_bytes(serialize_game_execution_script(script))
        with mock.patch(
            "gamepack_runtime.headless._native_machine",
            return_value="x86_64",
        ):
            cls.headless = build_headless_evidence_set(
                cls.root / "abstract-puzzle-headless",
                bundle_root=cls.bundle_root,
                script_path=script_path,
            )

        narrative_bundle = _build_bundle("branching-narrative", cls.root)
        try:
            cls.narrative_bundle_root = narrative_bundle.root
            narrative_adapter_path = narrative_bundle.manifest["contracts"]["runtime_adapter"][
                "path"
            ]
            narrative_script = build_game_execution_script(
                narrative_bundle.manifest,
                gamepack=_bundle_document(narrative_bundle, "contracts/gamepack.json"),
                composition=_bundle_document(
                    narrative_bundle,
                    "contracts/runtime-composition.json",
                ),
                adapter=_bundle_document(narrative_bundle, narrative_adapter_path),
                runtime_snapshot=_bundle_document(
                    narrative_bundle,
                    "contracts/runtime-snapshot.json",
                ),
                scenarios=_headless_scenarios("branching-narrative"),
            )
        finally:
            narrative_bundle.close()
        narrative_script_path = cls.root / "branching-narrative-execution-script.json"
        narrative_script_path.write_bytes(serialize_game_execution_script(narrative_script))
        with mock.patch(
            "gamepack_runtime.headless._native_machine",
            return_value="x86_64",
        ):
            cls.narrative_headless = build_headless_evidence_set(
                cls.root / "branching-narrative-headless",
                bundle_root=cls.narrative_bundle_root,
                script_path=narrative_script_path,
            )

        cls.standalone = materialize_game(
            cls.materialization.root,
            cls.root / "abstract-puzzle-standalone",
        )
        cls.package = build_game_package_from_standalone(cls.standalone.root)
        package_path = cls.root / "abstract-puzzle.wfgame"
        package_path.write_bytes(cls.package.archive_bytes)
        cls.extracted = extract_game_package(
            package_path,
            cls.root / "abstract-puzzle-extracted",
        )
        cls.extraction = build_game_package_extraction_evidence(
            cls.package.manifest,
            archive_sha256=cls.package.archive_sha256,
            archive_size_bytes=len(cls.package.archive_bytes),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        for value in (
            cls.extracted,
            cls.package,
            cls.standalone,
            cls.narrative_headless,
            cls.headless,
            cls.assetpack,
        ):
            value.close()
        cls._materialization_context.__exit__(None, None, None)
        cls._temporary.cleanup()

    def test_initial_authority_binds_exact_core_and_cannot_be_forged_from_json(self) -> None:
        document = self.base.document

        self.assertEqual(RUNTIME_SUPPORT_AUTHORITY_FORMAT, document["format"])
        self.assertEqual(self.gamepack["content_hash"], document["gamepack"]["content_hash"])
        self.assertEqual(
            self.composition["content_hash"],
            document["composition"]["content_hash"],
        )
        self.assertEqual(
            self.assetpack.manifest["content_hash"],
            document["assetpack"]["content_hash"],
        )
        self.assertEqual([], derive_runtime_evidence(self.base))
        support = derive_runtime_support_report(self.base)
        self.assertFalse(support["supported"])
        self.assertEqual("blocked", support["dimensions"]["release"])
        self.assertIn("native_evidence_missing", support["reason_codes"])
        self.assertEqual(document, validate_runtime_support_authority_document(document))
        self.assertTrue(serialize_runtime_support_authority(document).endswith(b"\n"))

        with self.assertRaises(TypeError):
            VerifiedRuntimeSupportAuthority(document)
        for raw in (document, support, {"status": "verified"}, True, "a" * 64):
            with (
                self.subTest(raw=type(raw).__name__),
                self.assertRaisesRegex(
                    RuntimeSupportAuthorityError,
                    "runtime_support_authority_required",
                ),
            ):
                derive_runtime_support_report(raw)

    def test_raw_headless_result_and_self_attested_overclaim_never_attach(self) -> None:
        raw_result = build_headless_authority_result(
            self.headless,
            path=self.headless.root,
        )
        with self.assertRaisesRegex(
            RuntimeSupportAuthorityError,
            "runtime_support_authority_headless_required",
        ):
            attach_verified_headless_evidence(
                self.base,
                raw_result,
                bundle_root=self.bundle_root,
            )

        overclaim = copy.deepcopy(self.headless.manifest)
        overclaim["runtime_evidence"]["execution_status"] = "native_verified"
        forged = VerifiedHeadlessEvidenceSet(
            self.headless.root,
            overclaim,
            dict(self.headless.files),
            self.headless.root_identity,
        )
        try:
            with self.assertRaisesRegex(
                RuntimeSupportAuthorityError,
                "runtime_support_authority_headless_mismatch",
            ):
                attach_verified_headless_evidence(
                    self.base,
                    forged,
                    bundle_root=self.bundle_root,
                )
        finally:
            forged.close()

    def test_headless_attach_reverifies_exact_lineage_and_rejects_platform_collision(
        self,
    ) -> None:
        with mock.patch(
            "gamepack_runtime.headless._native_machine",
            return_value="x86_64",
        ):
            authority = attach_verified_headless_evidence(
                self.base,
                self.headless,
                bundle_root=self.bundle_root,
            )

        evidence = derive_runtime_evidence(authority)
        self.assertEqual(1, len(evidence))
        self.assertEqual("headless_verified", evidence[0]["execution_status"])
        self.assertEqual("unverified", evidence[0]["packaging_status"])
        self.assertEqual(
            ["headless", "save_replay"],
            sorted(check["kind"] for check in evidence[0]["checks"]),
        )
        self.assertFalse(authority.document["supported"])
        self.assertEqual("unavailable", authority.document["native_status"])

        with (
            mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ),
            self.assertRaisesRegex(
                RuntimeSupportAuthorityError,
                "runtime_support_authority_platform_collision",
            ),
        ):
            attach_verified_headless_evidence(
                authority,
                self.headless,
                bundle_root=self.bundle_root,
            )

    def test_cross_composition_headless_fails_before_positive_authority(self) -> None:
        with (
            mock.patch(
                "gamepack_runtime.headless._native_machine",
                return_value="x86_64",
            ),
            self.assertRaisesRegex(
                RuntimeSupportAuthorityError,
                "runtime_support_authority_headless_mismatch",
            ),
        ):
            attach_verified_headless_evidence(
                self.base,
                self.narrative_headless,
                bundle_root=self.narrative_bundle_root,
            )

    def test_package_only_and_cross_lineage_extraction_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            RuntimeSupportAuthorityError,
            "runtime_support_authority_headless_required",
        ):
            attach_verified_game_package(
                self.base,
                self.package,
                extracted_standalone=self.extracted,
                extraction_evidence=self.extraction,
            )

        with mock.patch(
            "gamepack_runtime.headless._native_machine",
            return_value="x86_64",
        ):
            headless = attach_verified_headless_evidence(
                self.base,
                self.headless,
                bundle_root=self.bundle_root,
            )
        crossed = copy.deepcopy(self.extraction)
        crossed["lineage"]["runtime_bundle_hash"] = "f" * 64
        crossed["content_hash"] = canonical_creation_hash(crossed)
        with self.assertRaisesRegex(
            RuntimeSupportAuthorityError,
            "runtime_support_authority_package_lineage_mismatch",
        ):
            attach_verified_game_package(
                headless,
                self.package,
                extracted_standalone=self.extracted,
                extraction_evidence=crossed,
            )

    def test_exact_package_and_extraction_upgrade_packaging_but_not_native_release(
        self,
    ) -> None:
        with mock.patch(
            "gamepack_runtime.headless._native_machine",
            return_value="x86_64",
        ):
            headless = attach_verified_headless_evidence(
                self.base,
                self.headless,
                bundle_root=self.bundle_root,
            )
        packaged = attach_verified_game_package(
            headless,
            self.package,
            extracted_standalone=self.extracted,
            extraction_evidence=self.extraction,
        )

        evidence = derive_runtime_evidence(packaged)
        self.assertEqual("verified", evidence[0]["packaging_status"])
        self.assertEqual(
            "passed",
            next(
                check["status"]
                for check in evidence[0]["checks"]
                if check["check_id"] == "check:package_verification"
            ),
        )
        support = derive_runtime_support_report(packaged)
        self.assertEqual("blocked", support["dimensions"]["release"])
        self.assertFalse(support["supported"])
        self.assertIn("native_evidence_missing", support["reason_codes"])
        self.assertEqual("blocked", packaged.document["release_status"])
        self.assertIn(
            "runtime_support_authority_native_unavailable",
            packaged.document["reason_codes"],
        )
        encoded_files = b"".join(self.extracted.files.values())
        self.assertNotIn(RUNTIME_SUPPORT_AUTHORITY_FORMAT.encode(), encoded_files)

    def test_readiness_ignores_raw_runtime_and_package_claims_without_authority(self) -> None:
        loaded = load_creation_project(
            ROOT / "examples/multigenre-contracts/abstract-puzzle/project.json"
        )
        with mock.patch(
            "gamepack_runtime.headless._native_machine",
            return_value="x86_64",
        ):
            headless = attach_verified_headless_evidence(
                self.base,
                self.headless,
                bundle_root=self.bundle_root,
            )
        packaged = attach_verified_game_package(
            headless,
            self.package,
            extracted_standalone=self.extracted,
            extraction_evidence=self.extraction,
        )
        runtime_evidence = derive_runtime_evidence(packaged)
        support = derive_runtime_support_report(packaged)
        artifacts = (
            *_puzzle_asset_graph(),
            self.snapshot,
            self.registry,
            self.composition,
            *runtime_evidence,
            support,
            self.package.manifest,
        )

        untrusted = build_creation_readiness(loaded, artifacts=artifacts)
        self.assertEqual(
            ["untested", "untested"],
            [entry["status"] for entry in untrusted["dimensions"]["execution"]],
        )
        self.assertEqual("unverified", untrusted["dimensions"]["packaging"])
        self.assertIn(
            "runtime_evidence_authority_missing",
            untrusted["blocker_reason_codes"],
        )
        self.assertIn(
            "packaging_evidence_authority_missing",
            untrusted["blocker_reason_codes"],
        )
        self.assertIn(
            "native_evidence_authority_unavailable",
            untrusted["blocker_reason_codes"],
        )

        authoritative = build_creation_readiness(
            loaded,
            artifacts=artifacts,
            runtime_support_authority=packaged,
        )
        self.assertEqual(
            ["headless_verified", "untested"],
            [entry["status"] for entry in authoritative["dimensions"]["execution"]],
        )
        self.assertEqual("verified", authoritative["dimensions"]["packaging"])
        self.assertNotIn(
            "runtime_evidence_authority_missing",
            authoritative["blocker_reason_codes"],
        )
        self.assertNotIn(
            "packaging_evidence_authority_missing",
            authoritative["blocker_reason_codes"],
        )
        self.assertIn(
            "native_evidence_authority_unavailable",
            authoritative["blocker_reason_codes"],
        )
        self.assertEqual("blocked", authoritative["dimensions"]["release"])
        self.assertFalse(authoritative["release_ready"])
        self.assertEqual(
            authoritative,
            validate_creation_readiness(
                authoritative,
                loaded,
                artifacts=artifacts,
                runtime_support_authority=packaged,
            ),
        )

    def test_readiness_headless_authority_upgrades_execution_only(self) -> None:
        loaded = load_creation_project(
            ROOT / "examples/multigenre-contracts/abstract-puzzle/project.json"
        )
        with mock.patch(
            "gamepack_runtime.headless._native_machine",
            return_value="x86_64",
        ):
            headless = attach_verified_headless_evidence(
                self.base,
                self.headless,
                bundle_root=self.bundle_root,
            )
        evidence = derive_runtime_evidence(headless)
        support = derive_runtime_support_report(headless)
        artifacts = (
            *_puzzle_asset_graph(),
            self.snapshot,
            self.registry,
            self.composition,
            *evidence,
            support,
        )
        readiness = build_creation_readiness(
            loaded,
            artifacts=artifacts,
            runtime_support_authority=headless,
        )
        self.assertEqual(
            ["headless_verified", "untested"],
            [entry["status"] for entry in readiness["dimensions"]["execution"]],
        )
        self.assertEqual("unverified", readiness["dimensions"]["packaging"])
        self.assertIn("packaging_evidence_missing", readiness["blocker_reason_codes"])
        self.assertEqual("blocked", readiness["dimensions"]["release"])

    def test_native_claims_and_manual_release_ready_edits_are_impossible(self) -> None:
        for claim in (
            {"execution_status": "native_verified"},
            {"check_id": "check:native_raylib", "status": "passed"},
            True,
            "f" * 64,
        ):
            with (
                self.subTest(claim=claim),
                self.assertRaisesRegex(
                    RuntimeSupportAuthorityError,
                    "runtime_support_authority_native_unavailable",
                ),
            ):
                attach_native_evidence(self.base, claim)

        edited = copy.deepcopy(self.base.document)
        edited["release_status"] = "ready"
        edited["supported"] = True
        edited["content_hash"] = canonical_creation_hash(edited)
        with self.assertRaisesRegex(
            RuntimeSupportAuthorityError,
            "runtime_support_authority_overclaim",
        ):
            validate_runtime_support_authority_document(edited)

        tampered = copy.deepcopy(self.base.document)
        tampered["assetpack"]["content_hash"] = "f" * 64
        with self.assertRaisesRegex(
            RuntimeSupportAuthorityError,
            "runtime_support_authority_hash_mismatch",
        ):
            validate_runtime_support_authority_document(tampered)

    def test_schema_generated_types_and_catalog_register_every_schema_contract(self) -> None:
        schema = build_schemas()["runtime-support-authority.schema.json"]
        self.assertEqual(
            RUNTIME_SUPPORT_AUTHORITY_FORMAT,
            schema["properties"]["format"]["const"],
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual("blocked", schema["properties"]["release_status"]["const"])
        self.assertFalse(schema["properties"]["supported"]["const"])

        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        schema_count = len(list((ROOT / "schemas").glob("*.schema.json")))
        self.assertEqual(schema_count, len(entries))
        entry = entries["runtime-support-authority"]
        self.assertEqual(RUNTIME_SUPPORT_AUTHORITY_FORMAT, entry["format"])
        self.assertEqual("schemas/runtime-support-authority.schema.json", entry["schema"])
        self.assertEqual(
            [
                ("examples/multigenre-contracts/abstract-puzzle/runtime/support-authority.json"),
                (
                    "examples/multigenre-contracts/branching-narrative/"
                    "runtime/support-authority.json"
                ),
            ],
            entry["fixtures"],
        )
        self.assertIn(
            "worldforge.runtime_support_authority:initialize_runtime_support_authority",
            entry["python_symbols"],
        )
        declarations = (ROOT / "apps/studio/src/generated/world-forge-contracts.d.ts").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "export interface WorldForgeTrustedRuntimeSupportAuthorityV1",
            declarations,
        )
        self.assertIn('format: "world-forge.runtime_support_authority";', declarations)


if __name__ == "__main__":
    unittest.main()
