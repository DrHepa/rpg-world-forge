from __future__ import annotations

import json
import unicodedata
import unittest
from pathlib import Path

from worldforge.contract_catalog import audit_contracts, load_contract_catalog
from worldforge.studio.contracts import (
    PORTABLE_SOURCE_PATH_FORMAT,
    StudioContractError,
    studio_source_path,
    validate_forge_workspace,
    validate_studio_changeset,
    validate_studio_job,
    validate_studio_protocol_envelope,
)


class StudioContractTests(unittest.TestCase):
    def test_catalog_audits_all_studio_contracts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = audit_contracts(root)
        entries = {entry["id"] for entry in load_contract_catalog(root)["contracts"]}

        self.assertTrue(
            {"forge-workspace", "studio-protocol", "studio-changeset", "studio-job"} <= entries
        )
        self.assertGreaterEqual(result.contracts, 35)

    def test_workspace_validator_is_closed_and_versioned(self) -> None:
        workspace = {
            "format": "rpg-world-forge.forge_workspace",
            "format_version": 1,
            "workspace_id": "workspace_01",
            "forge_root": "/forge",
            "world_root": "/world",
            "game_root": None,
            "bundle_root": None,
            "created_at": "2026-07-22T12:00:00Z",
        }

        self.assertEqual(workspace, validate_forge_workspace(workspace))
        with self.assertRaisesRegex(StudioContractError, "unknown fields"):
            validate_forge_workspace({**workspace, "provider": "openai"})
        with self.assertRaisesRegex(StudioContractError, "format_version"):
            validate_forge_workspace({**workspace, "format_version": True})

    def test_changeset_validator_enforces_operation_shape(self) -> None:
        changeset = {
            "format": "rpg-world-forge.studio_changeset",
            "format_version": 1,
            "changeset_id": "0123456789abcdef0123456789abcdef",
            "workspace_id": "workspace_01",
            "status": "staged",
            "operations": [
                {
                    "path": "source/lore/entry.md",
                    "operation": "create",
                    "base_sha256": None,
                    "proposed_sha256": "a" * 64,
                    "size": 4,
                }
            ],
            "created_at": "2026-07-22T12:00:00Z",
            "updated_at": "2026-07-22T12:00:00Z",
        }

        self.assertEqual(changeset, validate_studio_changeset(changeset))
        invalid = {**changeset, "operations": [{**changeset["operations"][0], "size": True}]}
        with self.assertRaisesRegex(StudioContractError, "size"):
            validate_studio_changeset(invalid)

    def test_changeset_validator_matches_every_bounded_schema_relationship(self) -> None:
        operation = {
            "path": "source/lore/entry.md",
            "operation": "create",
            "base_sha256": None,
            "proposed_sha256": "a" * 64,
            "size": 4,
        }
        changeset = {
            "format": "rpg-world-forge.studio_changeset",
            "format_version": 1,
            "changeset_id": "0123456789abcdef0123456789abcdef",
            "workspace_id": "workspace_01",
            "status": "staged",
            "operations": [operation],
            "created_at": "2026-07-22T12:00:00Z",
            "updated_at": "2026-07-22T12:00:00Z",
        }
        invalid_operations = (
            {**operation, "path": "other/file.txt"},
            {**operation, "base_sha256": "b" * 64},
            {**operation, "proposed_sha256": None},
            {**operation, "size": 16 * 1024 * 1024 + 1},
            {
                **operation,
                "operation": "replace",
                "base_sha256": None,
            },
            {
                **operation,
                "operation": "delete",
                "base_sha256": "b" * 64,
                "proposed_sha256": "a" * 64,
                "size": 0,
            },
            {
                **operation,
                "operation": "delete",
                "base_sha256": "b" * 64,
                "proposed_sha256": None,
                "size": 1,
            },
        )
        for invalid in invalid_operations:
            with self.subTest(operation=invalid), self.assertRaises(StudioContractError):
                validate_studio_changeset({**changeset, "operations": [invalid]})
        with self.assertRaisesRegex(StudioContractError, "256"):
            validate_studio_changeset({**changeset, "operations": [operation] * 257})
        with self.assertRaises(StudioContractError):
            validate_studio_changeset({**changeset, "changeset_id": "A"})
        with self.assertRaises(StudioContractError):
            validate_studio_changeset({**changeset, "created_at": "not-a-timestamp"})

    def test_portable_source_path_schema_annotation_matches_python_validator(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "schemas/studio-changeset.schema.json").read_text(encoding="utf-8")
        )
        path_contract = schema["$defs"]["operation"]["properties"]["path"]
        self.assertEqual(PORTABLE_SOURCE_PATH_FORMAT, path_contract["format"])
        self.assertEqual(
            {
                "root": "source/",
                "separator": "/",
                "normalization": "NFC",
                "reject_traversal_segments": True,
                "reject_windows_reserved_names": True,
                "reject_trailing_dot_or_space": True,
                "max_component_utf8_bytes": 255,
                "collision_key": "NFC-casefold",
            },
            path_contract["x-worldforge-path-policy"],
        )

        operation = {
            "path": "source/lore/entry.md",
            "operation": "create",
            "base_sha256": None,
            "proposed_sha256": "a" * 64,
            "size": 4,
        }
        changeset = {
            "format": "rpg-world-forge.studio_changeset",
            "format_version": 1,
            "changeset_id": "0123456789abcdef0123456789abcdef",
            "workspace_id": "workspace_01",
            "status": "staged",
            "operations": [operation],
            "created_at": "2026-07-22T12:00:00Z",
            "updated_at": "2026-07-22T12:00:00Z",
        }
        valid = "source/lore/caf\N{LATIN SMALL LETTER E WITH ACUTE}.md"
        self.assertEqual(valid, studio_source_path(valid).as_posix())
        self.assertEqual(
            valid,
            validate_studio_changeset({**changeset, "operations": [{**operation, "path": valid}]})[
                "operations"
            ][0]["path"],
        )

        decomposed = unicodedata.normalize("NFD", valid)
        invalid_paths = (
            "source/../escape.md",
            "source/CON.txt",
            "source/nul",
            "source/trailing.",
            "source/trailing ",
            f"source/{'a' * 256}.txt",
            "source/" + ("\N{LATIN SMALL LETTER E WITH ACUTE}" * 128),
            decomposed,
            "source/\ud800.txt",
            "source//entry.md",
            "source",
        )
        for path in invalid_paths:
            with self.subTest(path=path):
                self.assertIsNone(studio_source_path(path))
                with self.assertRaises(StudioContractError):
                    validate_studio_changeset(
                        {**changeset, "operations": [{**operation, "path": path}]}
                    )

    def test_job_and_protocol_validators_reject_unknown_values(self) -> None:
        job = {
            "format": "rpg-world-forge.studio_job",
            "format_version": 1,
            "job_id": "0123456789abcdef0123456789abcdef",
            "workspace_id": "workspace_01",
            "operation": "forge.validate",
            "state": "queued",
            "input": {},
            "result": None,
            "error": None,
            "created_at": "2026-07-22T12:00:00Z",
            "updated_at": "2026-07-22T12:00:00Z",
        }
        self.assertEqual(job, validate_studio_job(job))
        with self.assertRaisesRegex(StudioContractError, "state"):
            validate_studio_job({**job, "state": "executing"})

        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "request",
            "request_id": "request-1",
            "method": "service.initialize",
            "params": {},
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        with self.assertRaisesRegex(StudioContractError, "method"):
            validate_studio_protocol_envelope({**request, "method": "provider.execute"})


if __name__ == "__main__":
    unittest.main()
