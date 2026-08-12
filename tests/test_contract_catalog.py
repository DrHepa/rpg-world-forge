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

import worldforge.contract_catalog as contract_catalog
from worldforge.__main__ import main
from worldforge.contract_catalog import ContractCatalogError, audit_contracts, load_contract_catalog
from worldforge.integrity import canonical_json_bytes

ROOT = Path(__file__).resolve().parents[1]
_INSTALLED_SUBSET_CONTRACT_IDS = ("contract-catalog", "worldpack")


def _write_canonical_json(path: Path, payload: object) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def _installed_catalog_subset(catalog: dict[str, object]) -> dict[str, object]:
    entries_by_id = {
        entry["id"]: entry
        for entry in catalog["contracts"]  # type: ignore[index]
    }
    return {
        **catalog,
        "contracts": [entries_by_id[contract_id] for contract_id in _INSTALLED_SUBSET_CONTRACT_IDS],
    }


def _write_installed_public_tree(root: Path, catalog: dict[str, object]) -> None:
    (root / "contracts").mkdir(parents=True)
    (root / "schemas").mkdir()
    _write_canonical_json(root / "contracts/catalog.json", catalog)
    public_paths: set[str] = set()
    for entry in catalog["contracts"]:  # type: ignore[index]
        public_paths.add(entry["schema"])  # type: ignore[index]
        for field in ("fixtures", "tests", "docs"):
            public_paths.update(
                value
                for value in entry[field]  # type: ignore[index]
                if value.startswith(("contracts/", "schemas/"))
            )
    for relative in sorted(public_paths):
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


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

    def test_installed_subset_selection_is_semantic_and_order_independent(self) -> None:
        catalog = load_contract_catalog(ROOT)
        reversed_catalog = {
            **catalog,
            "contracts": list(reversed(catalog["contracts"])),
        }

        subset = _installed_catalog_subset(reversed_catalog)

        self.assertEqual(
            ["contract-catalog", "worldpack"],
            [entry["id"] for entry in subset["contracts"]],
        )
        public_paths = {
            path for entry in subset["contracts"] for path in (entry["schema"], *entry["docs"])
        }
        self.assertIn("schemas/contract-catalog.schema.json", public_paths)
        self.assertIn("contracts/README.md", public_paths)

    def test_legacy_identity_allowlist_is_a_closed_auditable_contract(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}

        self.assertEqual(
            {
                "format": "world-forge.legacy_identity_allowlist",
                "version": 1,
                "schema": "schemas/legacy-identity-allowlist.schema.json",
                "python_symbols": ["worldforge.identity_audit:audit_identities"],
                "cli_commands": ["audit-identities"],
                "fixtures": ["contracts/legacy-identity-allowlist.json"],
            },
            {
                key: entries["legacy-identity-allowlist"][key]
                for key in (
                    "format",
                    "version",
                    "schema",
                    "python_symbols",
                    "cli_commands",
                    "fixtures",
                )
            },
        )

    def test_runtime_pack_entries_do_not_claim_authoring_manifest_fixtures(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}

        self.assertEqual([], entries["assetpack"]["fixtures"])
        self.assertEqual([], entries["renderpack"]["fixtures"])
        self.assertTrue(entries["asset-manifest"]["fixtures"])
        self.assertTrue(entries["asset-processing-recipe"]["fixtures"])

    def test_authoring_only_cases_are_represented_without_claiming_catalog_exhaustiveness(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        cases = (
            "action-framing",
            "faction-strategy",
            "modular-roguelite",
            "sports-career",
        )

        for case in cases:
            with self.subTest(case=case):
                self.assertIn(
                    f"examples/multigenre-contracts/{case}/artifacts/{case}.gamepack.json",
                    entries["gamepack"]["fixtures"],
                )
                self.assertIn(
                    f"examples/multigenre-contracts/{case}/artifacts/{case}.authoring-ledger.json",
                    entries["mechanic-capability-ledger"]["fixtures"],
                )
        for contract_id in ("gamepack", "mechanic-capability-ledger"):
            self.assertEqual(1, entries[contract_id]["version"])
            self.assertIn(
                "tests/test_authoring_only_multigenre_fixtures.py",
                entries[contract_id]["tests"],
            )

    def test_generic_assetpack_is_additive_and_has_its_own_public_api(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        entry = entries["generic-assetpack"]

        self.assertEqual("world-forge.assetpack", entry["format"])
        self.assertEqual(1, entry["version"])
        self.assertEqual([], entry["fixtures"])
        self.assertEqual(
            [
                "worldforge.generic_assetpack:build_generic_assetpack_manifest",
                "worldforge.generic_assetpack:recover_generic_assetpack",
                "worldforge.generic_assetpack:rollback_generic_assetpack",
                "worldforge.generic_assetpack:seal_generic_assetpack",
                "worldforge.generic_assetpack:validate_generic_assetpack_document",
                "worldforge.generic_assetpack:verify_generic_assetpack",
            ],
            entry["python_symbols"],
        )
        self.assertEqual("rpg-world-forge.assetpack", entries["assetpack"]["format"])

    def test_generic_runtime_contracts_are_additive_and_separate_from_legacy_m6(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        expected = {
            "generic-runtime-adapter": "world-forge.runtime_adapter",
            "generic-runtime-adapter-registry": ("world-forge.runtime_adapter_registry"),
            "game-runtime-snapshot": "world-forge.game_runtime_snapshot",
            "game-runtime-composition": "world-forge.game_runtime_composition",
            "generic-runtime-evidence": "world-forge.runtime_evidence",
            "generic-runtime-support-report": ("world-forge.runtime_support_report"),
        }
        for contract_id, format_name in expected.items():
            with self.subTest(contract=contract_id):
                entry = entries[contract_id]
                self.assertEqual(entry["format"], format_name)
                self.assertEqual(entry["version"], 1)
                self.assertEqual(entry["m5_phases"], [])
                self.assertIn(
                    "tests/test_multigenre_runtime_contracts.py",
                    entry["tests"],
                )
                self.assertIn(
                    "docs/MULTI_GENRE_ARCHITECTURE.md",
                    entry["docs"],
                )
        self.assertEqual(
            entries["runtime-adapter"]["format"],
            "rpg-world-forge.runtime_adapter",
        )
        self.assertNotEqual(
            entries["generic-runtime-adapter"]["schema"],
            entries["runtime-adapter"]["schema"],
        )

    def test_generic_game_runtime_bundle_is_additive_and_pre_execution_only(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        entry = entries["game-runtime-bundle"]

        self.assertEqual("world-forge.game_runtime_bundle", entry["format"])
        self.assertEqual(1, entry["version"])
        self.assertEqual([], entry["fixtures"])
        self.assertEqual(
            [
                "build-game-runtime-bundle",
                "recover-game-runtime-bundle",
                "rollback-game-runtime-bundle",
                "verify-game-runtime-bundle",
            ],
            entry["cli_commands"],
        )
        self.assertIn(
            "worldforge.game_runtime_bundle:verify_game_runtime_bundle",
            entry["python_symbols"],
        )
        self.assertIn(
            "tests/test_multigenre_game_runtime_bundle.py",
            entry["tests"],
        )

    def test_executable_materialization_contracts_are_additive_and_blocked(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        expected = {
            "runtime-implementation": (
                "world-forge.runtime_implementation",
                "schemas/runtime-implementation.schema.json",
                "worldforge.runtime_implementation:build_runtime_implementation",
            ),
            "runtime-platform-lock": (
                "world-forge.runtime_platform_lock",
                "schemas/runtime-platform-lock.schema.json",
                "worldforge.runtime_platform_lock:build_builtin_runtime_platform_locks",
            ),
            "game-materialization-bundle": (
                "world-forge.game_materialization_bundle",
                "schemas/game-materialization-bundle.schema.json",
                "worldforge.game_materialization_bundle:require_game_materialization_bundle",
            ),
        }
        for contract_id, (format_name, schema, symbol) in expected.items():
            with self.subTest(contract=contract_id):
                entry = entries[contract_id]
                self.assertEqual(entry["format"], format_name)
                self.assertEqual(entry["version"], 1)
                self.assertEqual(entry["schema"], schema)
                self.assertEqual(entry["m5_phases"], [])
                self.assertIn(symbol, entry["python_symbols"])
                self.assertIn(
                    "tests/test_multigenre_materialization_contracts.py",
                    entry["tests"],
                )
                self.assertIn(
                    "docs/MULTI_GENRE_ARCHITECTURE.md",
                    entry["docs"],
                )
        self.assertEqual(
            entries["game-materialization-bundle"]["cli_commands"],
            [
                "build-game-materialization-bundle",
                "verify-game-materialization-bundle",
            ],
        )
        standalone = {
            "standalone-game": "world-forge.standalone_game",
            "standalone-game-lock": "world-forge.standalone_game_lock",
            "standalone-platform": "world-forge.standalone_platform",
        }
        for contract_id, format_name in standalone.items():
            with self.subTest(contract=contract_id):
                entry = entries[contract_id]
                self.assertEqual(entry["format"], format_name)
                self.assertEqual(entry["version"], 1)
                self.assertEqual(entry["m5_phases"], [])
                self.assertIn(
                    "tests/test_multigenre_standalone_materialization.py",
                    entry["tests"],
                )
        self.assertEqual(
            entries["standalone-game"]["cli_commands"],
            [
                "audit-game",
                "materialize-game",
                "recover-game-materialization",
                "rollback-game-materialization",
            ],
        )

    def test_generic_game_persistence_is_additive_and_runtime_bound(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        expected = {
            "game-save": (
                "world-forge.game_save",
                "verify-game-save",
                "worldforge.game_persistence:verify_game_save",
            ),
            "game-replay": (
                "world-forge.game_replay",
                "verify-game-replay",
                "worldforge.game_persistence:verify_game_replay",
            ),
        }
        for contract_id, (format_name, command, symbol) in expected.items():
            with self.subTest(contract=contract_id):
                entry = entries[contract_id]
                self.assertEqual(entry["format"], format_name)
                self.assertEqual(entry["version"], 1)
                self.assertEqual(entry["m5_phases"], [])
                self.assertEqual(entry["cli_commands"], [command])
                self.assertIn(symbol, entry["python_symbols"])
                self.assertIn(
                    "tests/test_multigenre_game_persistence.py",
                    entry["tests"],
                )
                self.assertTrue(entry["fixtures"])

        generation = entries["persistence-generation"]
        self.assertEqual(
            generation["format"],
            "world-forge.persistence_generation",
        )
        self.assertEqual(generation["version"], 1)
        self.assertEqual(generation["m5_phases"], [])
        self.assertEqual(
            generation["cli_commands"],
            ["verify-persistence-generation"],
        )
        self.assertIn(
            "worldforge.persistence_generation:verify_persistence_generation",
            generation["python_symbols"],
        )
        self.assertEqual(
            generation["schema"],
            "schemas/persistence-generation.schema.json",
        )
        self.assertEqual(len(generation["fixtures"]), 8)

    def test_generic_headless_contracts_are_additive_and_evidence_bound(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        expected = {
            "game-execution-script": (
                "world-forge.game_execution_script",
                "schemas/game-execution-script.schema.json",
                "gamepack_runtime.headless:validate_game_execution_script",
            ),
            "headless-execution-receipt": (
                "world-forge.headless_execution_receipt",
                "schemas/headless-execution-receipt.schema.json",
                "gamepack_runtime.headless:validate_headless_execution_receipt",
            ),
            "headless-evidence-set": (
                "world-forge.headless_evidence_set",
                "schemas/headless-evidence-set.schema.json",
                "worldforge.generic_headless:verify_headless_evidence_set",
            ),
        }
        for contract_id, (format_name, schema, symbol) in expected.items():
            with self.subTest(contract=contract_id):
                entry = entries[contract_id]
                self.assertEqual(entry["format"], format_name)
                self.assertEqual(entry["version"], 1)
                self.assertEqual(entry["schema"], schema)
                self.assertEqual(entry["m5_phases"], [])
                self.assertIn(symbol, entry["python_symbols"])
                self.assertIn(
                    "tests/test_multigenre_generic_headless.py",
                    entry["tests"],
                )
                self.assertIn(
                    "docs/MULTI_GENRE_ARCHITECTURE.md",
                    entry["docs"],
                )

    def test_generic_game_package_is_additive_and_has_closed_public_surfaces(self) -> None:
        catalog = load_contract_catalog(ROOT)
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        entry = entries["game-package"]

        self.assertEqual("world-forge.game_package", entry["format"])
        self.assertEqual(1, entry["version"])
        self.assertEqual("schemas/game-package.schema.json", entry["schema"])
        self.assertEqual([], entry["fixtures"])
        self.assertEqual(
            [
                "extract-game-package",
                "package-game",
                "recover-game-package-extraction",
                "rollback-game-package-extraction",
                "verify-game-package",
            ],
            entry["cli_commands"],
        )
        self.assertEqual(
            [
                "gamepack_runtime.game_package:build_game_package_from_standalone",
                "gamepack_runtime.game_package:validate_game_package_document",
                "gamepack_runtime.game_package:verify_game_package_bytes",
                "gamepack_runtime.game_package:verify_game_package_file",
                "worldforge.game_package:extract_game_package",
                "worldforge.game_package:package_game",
                "worldforge.game_package:recover_game_package_extraction",
                "worldforge.game_package:rollback_game_package_extraction",
                "worldforge.game_package:verify_game_package",
            ],
            entry["python_symbols"],
        )
        self.assertEqual(["tests/test_multigenre_game_package.py"], entry["tests"])
        self.assertEqual(
            [
                "README.md",
                "apps/studio/README.md",
                "docs/MULTI_GENRE_ARCHITECTURE.md",
                "docs/ROADMAP.md",
            ],
            entry["docs"],
        )
        semantic_anchors = {
            "world-forge.game_package",
            "package-game",
            "verify-game-package",
            "extract-game-package",
            "recover-game-package-extraction",
            "rollback-game-package-extraction",
            "scripts/verify_game.py",
            "post-extraction",
            "release: blocked",
            "native evidence",
        }
        for relative in entry["docs"]:
            with self.subTest(document=relative):
                documentation = " ".join((ROOT / relative).read_text(encoding="utf-8").split())
                missing = sorted(
                    anchor for anchor in semantic_anchors if anchor not in documentation
                )
                self.assertEqual(
                    missing,
                    [],
                    f"{relative} does not document the complete game-package boundary",
                )
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        architecture = " ".join(
            (ROOT / "docs/MULTI_GENRE_ARCHITECTURE.md").read_text(encoding="utf-8").split()
        )
        self.assertIn("`materialization_ready: true`", readme)
        self.assertIn("Ready envelopes can create", architecture)
        for stale_claim in (
            "standalone-game materialization, native execution, and hosted platform "
            "evidence remain external future transitions",
            "It deliberately reports `contract_only` and `materialization_ready=false`",
        ):
            self.assertNotIn(stale_claim, readme)
        for stale_claim in (
            "a future standalone game",
            "Standalone repository creation and launchers, Studio authoring editing, "
            "hosted native evidence, and release support remain later slices",
            "verified contract-only envelope do not claim runtime executability",
        ):
            self.assertNotIn(stale_claim, architecture)
        self.assertEqual(
            entries["game-execution-script"]["cli_commands"],
            ["verify-game-headless"],
        )
        self.assertEqual(
            entries["headless-execution-receipt"]["cli_commands"],
            ["verify-game-headless"],
        )
        self.assertEqual(
            entries["headless-evidence-set"]["cli_commands"],
            [
                "verify-game-headless",
                "verify-game-headless-evidence",
            ],
        )
        self.assertEqual(
            len(entries["game-execution-script"]["fixtures"]),
            2,
        )
        self.assertEqual(
            entries["headless-execution-receipt"]["fixtures"],
            [],
        )
        self.assertEqual(
            entries["headless-evidence-set"]["fixtures"],
            [],
        )

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
        subset = _installed_catalog_subset(catalog)
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            root = prefix / "share/world-forge"
            _write_installed_public_tree(root, subset)
            with unittest.mock.patch(
                "worldforge.contract_catalog._candidate_install_prefixes", return_value=[prefix]
            ):
                result = audit_contracts()

        self.assertEqual(result.mode, "installed")
        self.assertEqual(result.contracts, 2)
        self.assertIn("share/world-forge", result.catalog_path.as_posix())

    def test_installed_catalog_falls_back_to_legacy_and_rejects_divergent_dual_trees(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        subset = _installed_catalog_subset(catalog)
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            legacy = prefix / "share/rpg-world-forge"
            _write_installed_public_tree(legacy, subset)

            with unittest.mock.patch(
                "worldforge.contract_catalog._candidate_install_prefixes",
                return_value=[prefix],
            ):
                fallback = audit_contracts()
            self.assertIn("share/rpg-world-forge", fallback.catalog_path.as_posix())

            canonical = prefix / "share/world-forge"
            shutil.copytree(legacy, canonical)
            with unittest.mock.patch(
                "worldforge.contract_catalog._candidate_install_prefixes",
                return_value=[prefix],
            ):
                preferred = audit_contracts()
            self.assertIn("share/world-forge", preferred.catalog_path.as_posix())

            schema = canonical / subset["contracts"][0]["schema"]
            schema.write_bytes(schema.read_bytes() + b" ")
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[prefix],
                ),
                self.assertRaisesRegex(ContractCatalogError, "installed public data trees diverge"),
            ):
                audit_contracts()

    def test_installed_discovery_is_global_canonical_first_and_partial_trees_fail(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        subset = _installed_catalog_subset(catalog)
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            legacy_prefix = workspace / "legacy-prefix"
            canonical_prefix = workspace / "canonical-prefix"
            _write_installed_public_tree(
                legacy_prefix / "share/rpg-world-forge",
                subset,
            )
            _write_installed_public_tree(
                canonical_prefix / "share/world-forge",
                subset,
            )
            with unittest.mock.patch(
                "worldforge.contract_catalog._candidate_install_prefixes",
                return_value=[legacy_prefix, canonical_prefix],
            ):
                preferred = audit_contracts()
            self.assertIn("canonical-prefix/share/world-forge", preferred.catalog_path.as_posix())

            partial_prefix = workspace / "partial-prefix"
            (partial_prefix / "share/world-forge/contracts").mkdir(parents=True)
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[partial_prefix, legacy_prefix],
                ),
                self.assertRaisesRegex(
                    ContractCatalogError,
                    "partial installed public data tree",
                ),
            ):
                audit_contracts()

    def test_all_installed_roots_must_be_globally_byte_and_topology_identical(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        subset = _installed_catalog_subset(catalog)
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            canonical_prefix = workspace / "canonical-prefix"
            second_canonical_prefix = workspace / "second-canonical-prefix"
            legacy_prefix = workspace / "legacy-prefix"
            first = canonical_prefix / "share/world-forge"
            second = second_canonical_prefix / "share/world-forge"
            legacy = legacy_prefix / "share/rpg-world-forge"
            _write_installed_public_tree(first, subset)
            shutil.copytree(first, second)
            shutil.copytree(first, legacy)
            schema_relative = subset["contracts"][0]["schema"]
            (legacy / schema_relative).write_bytes((legacy / schema_relative).read_bytes() + b" ")

            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[
                        legacy_prefix,
                        canonical_prefix,
                        second_canonical_prefix,
                    ],
                ),
                self.assertRaisesRegex(
                    ContractCatalogError,
                    "installed public data trees diverge",
                ),
            ):
                audit_contracts()

            shutil.copytree(first, legacy, dirs_exist_ok=True)
            (second / "schemas/unexpected-empty").mkdir()
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[
                        canonical_prefix,
                        second_canonical_prefix,
                        legacy_prefix,
                    ],
                ),
                self.assertRaisesRegex(
                    ContractCatalogError,
                    "unexpected public data directory|installed public data trees diverge",
                ),
            ):
                audit_contracts()

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-race probe")
    def test_installed_root_appearance_after_retained_share_census_fails_closed(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        subset = _installed_catalog_subset(catalog)
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp) / "prefix"
            share = prefix / "share"
            share.mkdir(parents=True)
            staged = prefix / "staged-world-forge"
            _write_installed_public_tree(staged, subset)

            def mutate(event: str, _relative: str | None) -> None:
                if event == "after_share_census":
                    os.replace(staged, share / "world-forge")

            with self.assertRaisesRegex(
                ContractCatalogError,
                "prefix topology changed",
            ):
                contract_catalog._installed_roots(  # noqa: SLF001
                    [prefix],
                    verification_hook=mutate,
                )

    @unittest.skipIf(os.name == "nt", "POSIX descriptor-race probe")
    def test_installed_root_swap_and_restore_is_rejected_by_retained_identity(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        subset = _installed_catalog_subset(catalog)
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp) / "prefix"
            original = prefix / "share/world-forge"
            decoy = prefix / "decoy-world-forge"
            parked = prefix / "parked-world-forge"
            _write_installed_public_tree(original, subset)
            _write_installed_public_tree(decoy, subset)
            schema_relative = subset["contracts"][0]["schema"]
            (decoy / schema_relative).write_bytes((decoy / schema_relative).read_bytes() + b" ")

            def mutate(event: str, relative: str | None) -> None:
                if event == "after_candidate_retained" and relative == "world-forge":
                    os.replace(original, parked)
                    os.replace(decoy, original)
                elif event == "before_candidate_binding_verification" and relative == "world-forge":
                    os.replace(original, decoy)
                    os.replace(parked, original)

            with self.assertRaisesRegex(
                ContractCatalogError,
                "named child binding changed",
            ):
                contract_catalog._installed_roots(  # noqa: SLF001
                    [prefix],
                    verification_hook=mutate,
                )

    def test_installed_public_tree_rejects_unsafe_colliding_extra_and_missing_entries(
        self,
    ) -> None:
        catalog = load_contract_catalog(ROOT)
        subset = _installed_catalog_subset(catalog)

        with self.subTest("symlink root"), tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            real = prefix / "real"
            _write_installed_public_tree(real, subset)
            share = prefix / "share"
            share.mkdir()
            try:
                os.symlink(real, share / "world-forge", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this filesystem")
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[prefix],
                ),
                self.assertRaisesRegex(ContractCatalogError, "unsafe installed public data root"),
            ):
                audit_contracts()

        with self.subTest("portable collision"), tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            root = prefix / "share/world-forge"
            _write_installed_public_tree(root, subset)
            (root / "contracts/README.MD").write_bytes((root / "contracts/README.md").read_bytes())
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[prefix],
                ),
                self.assertRaisesRegex(ContractCatalogError, "portable path collision"),
            ):
                audit_contracts()

        with self.subTest("extra public file"), tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            root = prefix / "share/world-forge"
            _write_installed_public_tree(root, subset)
            (root / "contracts/unreviewed.txt").write_text("extra", encoding="utf-8")
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[prefix],
                ),
                self.assertRaisesRegex(ContractCatalogError, "unexpected public data file"),
            ):
                audit_contracts()

        with self.subTest("extra root file"), tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            root = prefix / "share/world-forge"
            _write_installed_public_tree(root, subset)
            (root / "unreviewed.txt").write_text("extra", encoding="utf-8")
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[prefix],
                ),
                self.assertRaisesRegex(ContractCatalogError, "unexpected public data file"),
            ):
                audit_contracts()

        with self.subTest("extra empty directory"), tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            root = prefix / "share/world-forge"
            _write_installed_public_tree(root, subset)
            (root / "schemas/unexpected-empty").mkdir()
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[prefix],
                ),
                self.assertRaisesRegex(ContractCatalogError, "unexpected public data directory"),
            ):
                audit_contracts()

        with self.subTest("missing referenced file"), tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            root = prefix / "share/world-forge"
            _write_installed_public_tree(root, subset)
            (root / subset["contracts"][0]["schema"]).unlink()
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[prefix],
                ),
                self.assertRaisesRegex(ContractCatalogError, "missing public data file"),
            ):
                audit_contracts()

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
                        "worldforge.contract_catalog._candidate_install_prefixes", return_value=[]
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
            prefix = Path(temp)
            root = prefix / "share/world-forge"
            shutil.copytree(ROOT / "contracts", root / "contracts")
            shutil.copytree(ROOT / "schemas", root / "schemas")
            _write_canonical_json(root / "contracts/catalog.json", catalog)
            with (
                unittest.mock.patch(
                    "worldforge.contract_catalog._candidate_install_prefixes",
                    return_value=[prefix],
                ),
                self.assertRaisesRegex(ContractCatalogError, message),
            ):
                audit_contracts()


if __name__ == "__main__":
    unittest.main()
