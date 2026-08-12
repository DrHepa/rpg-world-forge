from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import generate_creation_content_modes as generator

ROOT = Path(__file__).resolve().parents[1]


class CreationContentModeGeneratorTests(unittest.TestCase):
    def _copy_temp_root(self) -> Path:
        temp = Path(tempfile.mkdtemp(prefix="creation-content-mode-generator-"))
        for relative in (
            "schemas/creation-profile.schema.json",
            "schemas/studio-protocol-v5.schema.json",
            "src/worldforge/generated_creation_content_modes.py",
            "apps/studio/src/generated/creation-content-modes.ts",
        ):
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        return temp

    def _run_with_temp_root(self, temp: Path, argv: list[str]) -> int:
        with (
            mock.patch.object(generator, "ROOT", temp),
            mock.patch.object(
                generator,
                "PROFILE_SCHEMA",
                temp / "schemas/creation-profile.schema.json",
            ),
            mock.patch.object(
                generator,
                "PROTOCOL_V5_SCHEMA",
                temp / "schemas/studio-protocol-v5.schema.json",
            ),
            mock.patch.object(
                generator,
                "PYTHON_TARGET",
                temp / "src/worldforge/generated_creation_content_modes.py",
            ),
            mock.patch.object(
                generator,
                "TYPESCRIPT_TARGET",
                temp / "apps/studio/src/generated/creation-content-modes.ts",
                create=True,
            ),
        ):
            return generator.main(argv)

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, document: dict[str, object]) -> None:
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _asset_mode_schema_nodes(schema: object) -> list[dict[str, object]]:
        nodes: list[dict[str, object]] = []
        if isinstance(schema, dict):
            for key, value in schema.items():
                if key == "asset_content_mode" and isinstance(value, dict) and "enum" in value:
                    nodes.append(value)
                else:
                    nodes.extend(CreationContentModeGeneratorTests._asset_mode_schema_nodes(value))
        elif isinstance(schema, list):
            for item in schema:
                nodes.extend(CreationContentModeGeneratorTests._asset_mode_schema_nodes(item))
        return nodes

    def _protocol_schema(self, temp: Path) -> dict[str, object]:
        return self._read_json(temp / "schemas/studio-protocol-v5.schema.json")

    def _write_protocol_schema(self, temp: Path, schema: dict[str, object]) -> None:
        self._write_json(temp / "schemas/studio-protocol-v5.schema.json", schema)

    def test_profile_enum_drift_updates_all_generated_projection_targets(self) -> None:
        temp = self._copy_temp_root()
        profile_path = temp / "schemas/creation-profile.schema.json"
        profile = self._read_json(profile_path)
        enum = profile["$defs"]["productionMode"]["enum"]  # type: ignore[index]
        enum.append("future_reviewed_mode")
        self._write_json(profile_path, profile)

        self.assertEqual(self._run_with_temp_root(temp, ["--write"]), 0)
        self.assertEqual(self._run_with_temp_root(temp, ["--check"]), 0)

        self.assertIn(
            b'"future_reviewed_mode"',
            (temp / "src/worldforge/generated_creation_content_modes.py").read_bytes(),
        )
        self.assertIn(
            b'"future_reviewed_mode"',
            (temp / "apps/studio/src/generated/creation-content-modes.ts").read_bytes(),
        )
        nodes = self._asset_mode_schema_nodes(self._protocol_schema(temp))
        self.assertEqual(len(nodes), 2)
        self.assertTrue(all("future_reviewed_mode" in node["enum"] for node in nodes))

    def test_check_rejects_python_protocol_and_typescript_projection_drift(self) -> None:
        drift_cases = {
            "python": "src/worldforge/generated_creation_content_modes.py",
            "typescript": "apps/studio/src/generated/creation-content-modes.ts",
        }
        for label, relative in drift_cases.items():
            with self.subTest(label=label):
                temp = self._copy_temp_root()
                path = temp / relative
                path.write_bytes(path.read_bytes().replace(b'"authored"', b'"drifted"', 1))
                with self.assertRaisesRegex(SystemExit, "out of date"):
                    self._run_with_temp_root(temp, ["--check"])

        for index in (0, 1):
            with self.subTest(label=f"protocol enum {index}"):
                temp = self._copy_temp_root()
                schema = self._protocol_schema(temp)
                nodes = self._asset_mode_schema_nodes(schema)
                self.assertEqual(len(nodes), 2)
                nodes[index]["enum"] = ["authored"]
                self._write_protocol_schema(temp, schema)
                with self.assertRaisesRegex(SystemExit, "out of date"):
                    self._run_with_temp_root(temp, ["--check"])

    def test_unrelated_protocol_v5_drift_fails_check_and_write_without_mutation(self) -> None:
        def add_property(schema: dict[str, object]) -> None:
            schema["$defs"]["workspaceCreateGameWithoutNarrativeParams"]["properties"][  # type: ignore[index]
                "unexpected_review_property"
            ] = {"type": "string"}

        def add_required(schema: dict[str, object]) -> None:
            schema["$defs"]["workspaceCreateGameWithoutNarrativeParams"]["required"].append(  # type: ignore[index]
                "unexpected_review_property"
            )

        def add_method(schema: dict[str, object]) -> None:
            schema["$defs"]["method"]["enum"].append("review.unexpected")  # type: ignore[index]

        def flip_additional_properties(schema: dict[str, object]) -> None:
            schema["$defs"]["workspaceCreateGameWithoutNarrativeParams"][  # type: ignore[index]
                "additionalProperties"
            ] = True

        mutations = {
            "property": add_property,
            "required": add_required,
            "method": add_method,
            "additionalProperties": flip_additional_properties,
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                temp = self._copy_temp_root()
                schema_path = temp / "schemas/studio-protocol-v5.schema.json"
                schema = self._protocol_schema(temp)
                mutate(schema)
                self._write_protocol_schema(temp, schema)
                before = schema_path.read_bytes()

                with self.assertRaisesRegex(SystemExit, "unreviewed studio-protocol-v5 drift"):
                    self._run_with_temp_root(temp, ["--check"])
                with self.assertRaisesRegex(SystemExit, "unreviewed studio-protocol-v5 drift"):
                    self._run_with_temp_root(temp, ["--write"])
                self.assertEqual(schema_path.read_bytes(), before)

    def test_enum_only_write_repairs_protocol_projection_but_unrelated_write_refuses(self) -> None:
        temp = self._copy_temp_root()
        schema = self._protocol_schema(temp)
        nodes = self._asset_mode_schema_nodes(schema)
        self.assertEqual(len(nodes), 2)
        for node in nodes:
            node["enum"] = ["authored"]
        self._write_protocol_schema(temp, schema)

        self.assertEqual(self._run_with_temp_root(temp, ["--write"]), 0)
        repaired_nodes = self._asset_mode_schema_nodes(self._protocol_schema(temp))
        self.assertTrue(all("not_applicable" in node["enum"] for node in repaired_nodes))
        self.assertEqual(self._run_with_temp_root(temp, ["--check"]), 0)

        schema_path = temp / "schemas/studio-protocol-v5.schema.json"
        schema = self._protocol_schema(temp)
        schema["$defs"]["workspaceCreateGameWithoutNarrativeParams"]["additionalProperties"] = True  # type: ignore[index]
        self._write_protocol_schema(temp, schema)
        before = schema_path.read_bytes()
        with self.assertRaisesRegex(SystemExit, "unreviewed studio-protocol-v5 drift"):
            self._run_with_temp_root(temp, ["--write"])
        self.assertEqual(schema_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
