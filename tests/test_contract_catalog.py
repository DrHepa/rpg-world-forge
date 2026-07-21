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

ROOT = Path(__file__).resolve().parents[1]


class ContractCatalogTests(unittest.TestCase):
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
                fixture_path.write_bytes(
                    (
                        json.dumps(mutated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                    ).encode("utf-8")
                )
                with self.assertRaisesRegex(
                    ContractCatalogError,
                    rf"contracts/{entry_index}/fixtures/0/format: fixture value",
                ):
                    audit_contracts(root)

            with self.subTest("version mismatch"):
                mutated = {**original, "format_version": 999}
                fixture_path.write_bytes(
                    (
                        json.dumps(mutated, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                    ).encode("utf-8")
                )
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
            (root / "contracts/catalog.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
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
                (root / "contracts/catalog.json").write_text(
                    json.dumps(catalog, ensure_ascii=False, sort_keys=True) + "\n",
                    encoding="utf-8",
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
            (root / "contracts/catalog.json").write_text(
                json.dumps(subset, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
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
            (root / "contracts/catalog.json").write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ContractCatalogError, message):
                audit_contracts(root)

    def _assert_installed_rejected(self, catalog: dict[str, object], message: str) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / "contracts", root / "contracts")
            shutil.copytree(ROOT / "schemas", root / "schemas")
            (root / "contracts/catalog.json").write_text(
                json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_roots", return_value=[root]
                ),
                self.assertRaisesRegex(ContractCatalogError, message),
            ):
                audit_contracts()


if __name__ == "__main__":
    unittest.main()
