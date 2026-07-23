from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from worldforge.__main__ import main
from worldforge.contract_catalog import ContractCatalogError, audit_contracts, load_contract_catalog
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]


def _write_canonical_json(path: Path, payload: object) -> None:
    path.write_bytes(canonical_json_bytes(payload))


class ContractCatalogTests(unittest.TestCase):
    def test_canonical_json_bytes_are_explicit_utf8_lf(self) -> None:
        payload = canonical_json_bytes({"z": "café", "a": 1})

        self.assertEqual(b'{\n  "a": 1,\n  "z": "caf\xc3\xa9"\n}\n', payload)
        self.assertNotIn(b"\r\n", payload)

    def test_source_catalog_audits_every_schema(self) -> None:
        result = audit_contracts(ROOT)
        schemas = {
            path.relative_to(ROOT).as_posix() for path in (ROOT / "schemas").glob("*.schema.json")
        }
        catalog = load_contract_catalog(ROOT)

        self.assertEqual(result.contracts, len(schemas))
        self.assertIn("schemas/contract-catalog.schema.json", schemas)
        self.assertEqual({entry["schema"] for entry in catalog["contracts"]}, schemas)
        self.assertIn("contract-catalog", {entry["id"] for entry in catalog["contracts"]})

    def test_runtime_pack_entries_do_not_claim_authoring_manifest_fixtures(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}

        self.assertEqual([], entries["assetpack"]["fixtures"])
        self.assertEqual([], entries["renderpack"]["fixtures"])
        self.assertTrue(entries["asset-manifest"]["fixtures"])
        self.assertTrue(entries["asset-processing-recipe"]["fixtures"])

    def test_processing_catalog_uses_exact_formats_versions_and_public_symbols(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        recipe = entries["asset-processing-recipe"]
        receipt = entries["asset-processing-receipt"]

        self.assertEqual("rpg-world-forge.asset_processing_recipe", recipe["format"])
        self.assertEqual(1, recipe["version"])
        self.assertEqual(
            [
                "worldforge.asset_processing:process_asset_recipe",
                "worldforge.asset_processing:validate_processing_recipe",
            ],
            recipe["python_symbols"],
        )
        self.assertEqual(2, receipt["version"])
        self.assertIn("v1 read compatibility", receipt["title"])
        recipe_schema = json.loads((ROOT / recipe["schema"]).read_text(encoding="utf-8"))
        receipt_schema = json.loads((ROOT / receipt["schema"]).read_text(encoding="utf-8"))
        for sample_rate in (
            recipe_schema["$defs"]["wav_options"]["properties"]["sample_rate"],
            receipt_schema["$defs"]["wav_details"]["properties"]["sample_rate"],
        ):
            self.assertEqual(8000, sample_rate["minimum"])
            self.assertEqual(192000, sample_rate["maximum"])

    def test_modly_discovery_entry_points_to_its_operational_validator(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        discovery = entries["modly-capability-discovery"]

        self.assertEqual(
            ["worldforge.asset_production:validate_modly_capability_discovery"],
            discovery["python_symbols"],
        )
        self.assertEqual(["tests/test_m5_production.py"], discovery["tests"])

    def test_m6_runtime_composition_entries_are_complete_and_keep_legacy_phase_name(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        expected = {
            "runtime-adapter": "rpg-world-forge.runtime_adapter",
            "runtime-capability-catalog": "rpg-world-forge.runtime_capability_catalog",
            "runtime-compatibility-report": "rpg-world-forge.runtime_compatibility_report",
            "runtime-composition": "rpg-world-forge.runtime_composition",
            "runtime-presentation-profile": "rpg-world-forge.runtime_presentation_profile",
        }

        for contract_id, format_name in expected.items():
            with self.subTest(contract=contract_id):
                entry = entries[contract_id]
                self.assertEqual(format_name, entry["format"])
                self.assertEqual(1, entry["version"])
                self.assertEqual(["M6"], entry["m5_phases"])
                self.assertIn(
                    "tests/test_m6_runtime_composition_contracts.py",
                    entry["tests"],
                )

    def test_json_fixture_identity_is_strict_and_schema_bound(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entry_index, entry = next(
            (index, item)
            for index, item in enumerate(catalog["contracts"])
            if item["id"] == "asset-manifest"
        )
        fixture_relative = entry["fixtures"][0]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "source"
            shutil.copytree(
                ROOT,
                root,
                ignore=shutil.ignore_patterns(".git", ".ruff_cache", "__pycache__", "*.pyc"),
            )
            fixture_path = root / fixture_relative
            original_bytes = fixture_path.read_bytes()
            original = json.loads(original_bytes)

            with self.subTest("format mismatch"):
                mutated = {**original, "format": "rpg-world-forge.assetpack"}
                _write_canonical_json(fixture_path, mutated)
                with self.assertRaisesRegex(
                    ContractCatalogError,
                    rf"contracts/{entry_index}/fixtures/0/format: fixture value",
                ):
                    audit_contracts(root)

            with self.subTest("version mismatch"):
                mutated = {**original, "format_version": 999}
                _write_canonical_json(fixture_path, mutated)
                with self.assertRaisesRegex(
                    ContractCatalogError,
                    rf"contracts/{entry_index}/fixtures/0/format_version: fixture value",
                ):
                    audit_contracts(root)

            with self.subTest("duplicate JSON key"):
                fixture_path.write_bytes(
                    b'{"format":"rpg-world-forge.asset_manifest",'
                    b'"format":"rpg-world-forge.asset_manifest","format_version":3}\n'
                )
                with self.assertRaisesRegex(
                    ContractCatalogError,
                    rf"contracts/{entry_index}/fixtures/0: could not strict-read JSON fixture",
                ):
                    audit_contracts(root)

            fixture_path.write_bytes(original_bytes)
            self.assertEqual("source", audit_contracts(root).mode)

    def test_cli_contract_errors_use_stderr_and_exit_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / "contracts", root / "contracts")
            shutil.copytree(ROOT / "schemas", root / "schemas")
            payload = json.loads((root / "contracts/catalog.json").read_text(encoding="utf-8"))
            payload["contracts"][0]["schema"] = "schemas/missing.schema.json"
            _write_canonical_json(root / "contracts/catalog.json", payload)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                unittest.mock.patch(
                    "sys.argv", ["worldforge", "audit-contracts", "--source-root", str(root)]
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                exit_code = main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("ERROR", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_unknown_missing_duplicate_and_casefold_paths_are_rejected(self) -> None:
        catalog = load_contract_catalog(ROOT)

        with self.subTest("unknown field"):
            mutated = json.loads(json.dumps(catalog))
            mutated["contracts"][0]["extra"] = True
            self._assert_rejected(mutated, "unknown fields")

        with self.subTest("missing field"):
            mutated = json.loads(json.dumps(catalog))
            del mutated["contracts"][0]["docs"]
            self._assert_rejected(mutated, "missing fields")

        with self.subTest("empty docs"):
            mutated = json.loads(json.dumps(catalog))
            mutated["contracts"][0]["docs"] = []
            self._assert_rejected(mutated, "at least one path")

        with self.subTest("integer version below minimum"):
            mutated = json.loads(json.dumps(catalog))
            mutated["contracts"][0]["version"] = 0
            self._assert_rejected(mutated, "at least 1")

        with self.subTest("duplicate id"):
            mutated = json.loads(json.dumps(catalog))
            mutated["contracts"][1]["id"] = mutated["contracts"][0]["id"]
            self._assert_rejected(mutated, "duplicate contract id")

        with self.subTest("casefold path"):
            mutated = json.loads(json.dumps(catalog))
            mutated["contracts"][1]["docs"] = [mutated["contracts"][0]["docs"][0].upper()]
            self._assert_rejected(mutated, "casefold path collision")

        with self.subTest("non-ascii path"):
            mutated = json.loads(json.dumps(catalog))
            mutated["contracts"][0]["docs"] = ["docs/café.md"]
            self._assert_rejected(mutated, "ASCII POSIX path")

        with self.subTest("disallowed python root"):
            mutated = json.loads(json.dumps(catalog))
            mutated["contracts"][0]["python_symbols"] = ["os:path"]
            self._assert_rejected(mutated, "disallowed symbol")

        with self.subTest("invalid cli command string"):
            mutated = json.loads(json.dumps(catalog))
            mutated["contracts"][0]["cli_commands"] = ["derive_asset_inventory"]
            self._assert_rejected(mutated, "ASCII CLI command")

        with self.subTest("schema version parity"):
            mutated = json.loads(json.dumps(catalog))
            for entry in mutated["contracts"]:
                if entry["id"] == "worldpack":
                    entry["version"] = 1
                    break
            self._assert_installed_rejected(mutated, "version does not match schema")

    def test_catalog_must_be_canonical_and_standalone(self) -> None:
        catalog = load_contract_catalog(ROOT)

        with self.subTest("noncanonical"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                shutil.copytree(ROOT / "contracts", root / "contracts")
                shutil.copytree(ROOT / "schemas", root / "schemas")
                (root / "contracts/catalog.json").write_bytes(
                    (json.dumps(catalog, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
                )
                with self.assertRaisesRegex(ContractCatalogError, "canonical"):
                    audit_contracts(root)

        with self.subTest("symlink"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                shutil.copytree(ROOT / "contracts", root / "contracts")
                shutil.copytree(ROOT / "schemas", root / "schemas")
                target = root / "contracts/real-catalog.json"
                os.replace(root / "contracts/catalog.json", target)
                try:
                    os.symlink(target, root / "contracts/catalog.json")
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks are unavailable on this filesystem")
                with self.assertRaisesRegex(ContractCatalogError, "standalone regular file"):
                    audit_contracts(root)

        with self.subTest("hardlink"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                shutil.copytree(ROOT / "contracts", root / "contracts")
                shutil.copytree(ROOT / "schemas", root / "schemas")
                target = root / "contracts/linked-catalog.json"
                try:
                    os.link(root / "contracts/catalog.json", target)
                except (OSError, NotImplementedError):
                    self.skipTest("hardlinks are unavailable on this filesystem")
                with self.assertRaisesRegex(ContractCatalogError, "standalone regular file"):
                    audit_contracts(root)

    def test_installed_subset_does_not_require_docs_tests_or_fixtures(self) -> None:
        catalog = load_contract_catalog(ROOT)
        subset = {**catalog, "contracts": [catalog["contracts"][0], catalog["contracts"][-1]]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "contracts").mkdir()
            (root / "schemas").mkdir()
            _write_canonical_json(root / "contracts/catalog.json", subset)
            for entry in subset["contracts"]:
                source = ROOT / entry["schema"]
                target = root / entry["schema"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            with unittest.mock.patch(
                "worldforge.contract_catalog._candidate_install_roots", return_value=[root]
            ):
                result = audit_contracts()

        self.assertEqual(result.mode, "installed")
        self.assertEqual(result.contracts, 2)

    def test_installed_discovery_does_not_use_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / "contracts", root / "contracts")
            shutil.copytree(ROOT / "schemas", root / "schemas")
            cwd = Path.cwd()
            try:
                os.chdir(root)
                with (
                    unittest.mock.patch(
                        "worldforge.contract_catalog._candidate_install_roots", return_value=[]
                    ),
                    self.assertRaisesRegex(ContractCatalogError, "could not be found"),
                ):
                    audit_contracts()
            finally:
                os.chdir(cwd)

    def _assert_rejected(self, catalog: dict[str, object], message: str) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / "contracts", root / "contracts")
            shutil.copytree(ROOT / "schemas", root / "schemas")
            _write_canonical_json(root / "contracts/catalog.json", catalog)
            with self.assertRaisesRegex(ContractCatalogError, message):
                audit_contracts(root)

    def _assert_installed_rejected(self, catalog: dict[str, object], message: str) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / "contracts", root / "contracts")
            shutil.copytree(ROOT / "schemas", root / "schemas")
            _write_canonical_json(root / "contracts/catalog.json", catalog)
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_roots", return_value=[root]
                ),
                self.assertRaisesRegex(ContractCatalogError, message),
            ):
                audit_contracts()


if __name__ == "__main__":
    unittest.main()
