from __future__ import annotations

import json
import unicodedata
import unittest
from pathlib import Path

from worldforge.contract_catalog import audit_contracts, load_contract_catalog
from worldforge.studio.contracts import (
    PORTABLE_SOURCE_PATH_FORMAT,
    StudioContractError,
    studio_job_path,
    studio_source_path,
    validate_forge_workspace,
    validate_job_create_params,
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

    def test_job_validator_reads_legacy_v1_and_enforces_managed_v2(self) -> None:
        managed = {
            "format": "rpg-world-forge.studio_job",
            "format_version": 2,
            "job_id": "0123456789abcdef0123456789abcdef",
            "workspace_id": "workspace_01",
            "operation": "runtime.headless",
            "state": "queued",
            "input": {"worldpack": "build/worldpack.json", "ticks": 0},
            "result": None,
            "error": None,
            "created_at": "2026-07-22T12:00:00Z",
            "updated_at": "2026-07-22T12:00:00Z",
        }
        self.assertEqual(managed, validate_studio_job(managed))
        with self.assertRaisesRegex(StudioContractError, "operation"):
            validate_studio_job({**managed, "operation": "forge.validate"})
        with self.assertRaisesRegex(StudioContractError, "input"):
            validate_studio_job({**managed, "input": {"legacy_command": "validate"}})
        with self.assertRaisesRegex(StudioContractError, "state"):
            validate_studio_job({**managed, "state": "executing"})

        legacy = {
            **managed,
            "format_version": 1,
            "operation": "forge.validate",
            "state": "running",
            "input": {"profile": "release", "options": ["old", 1]},
            "result": {"partial": True},
            "error": {"legacy_code": "pending"},
        }
        self.assertEqual(legacy, validate_studio_job(legacy))
        legacy_managed_name = {
            **legacy,
            "job_id": "legacy_managed_name",
            "operation": "runtime.headless",
            "input": {"legacy_command": "headless --old-contract"},
        }
        self.assertEqual(legacy_managed_name, validate_studio_job(legacy_managed_name))
        with self.assertRaisesRegex(StudioContractError, "format_version"):
            validate_studio_job({**legacy, "format_version": 3})

    def test_protocol_validator_rejects_unknown_methods(self) -> None:

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

    def test_job_create_is_a_closed_operation_specific_allowlist(self) -> None:
        valid = (
            ("asset.receipt.validate", {"receipt": "receipts/item.json"}),
            (
                "assetpack.verify",
                {"assetpack": "build/assets.json", "worldpack": "build/world.json"},
            ),
            ("runtime.headless", {"worldpack": "build/world.json", "ticks": 0}),
            (
                "runtime.replay",
                {"worldpack": "build/world.json", "replay": "replays/slot.json"},
            ),
        )
        for operation, job_input in valid:
            with self.subTest(operation=operation):
                params = {
                    "workspace_id": "workspace_01",
                    "operation": operation,
                    "input": job_input,
                }
                self.assertEqual(params, validate_job_create_params(params))

        invalid = (
            {"workspace_id": "workspace_01", "operation": "provider.execute", "input": {}},
            {
                "workspace_id": "workspace_01",
                "operation": "runtime.headless",
                "input": {"worldpack": "build/world.json", "ticks": True},
            },
            {
                "workspace_id": "workspace_01",
                "operation": "runtime.headless",
                "input": {"worldpack": "build/world.json", "ticks": -1},
            },
            {
                "workspace_id": "workspace_01",
                "operation": "runtime.headless",
                "input": {"worldpack": "build/world.json", "ticks": 1_000_001},
            },
            {
                "workspace_id": "workspace_01",
                "operation": "runtime.replay",
                "input": {"worldpack": "../world.json", "replay": "slot.json"},
            },
            {
                "workspace_id": "workspace_01",
                "operation": "assetpack.verify",
                "input": {"assetpack": "pack.json"},
            },
            {
                "workspace_id": "workspace_01",
                "operation": "asset.receipt.validate",
                "input": {"receipt": "receipt.json", "executor": "python"},
            },
        )
        for params in invalid:
            with self.subTest(params=params), self.assertRaises(StudioContractError):
                validate_job_create_params(params)
        self.assertIsNone(studio_job_path("build/../escape.json"))
        self.assertIsNone(studio_job_path("build/CON.json"))

    def test_protocol_discriminates_job_create_and_cancel(self) -> None:
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "request",
            "request_id": "job-1",
            "method": "job.create",
            "params": {
                "workspace_id": "workspace_01",
                "operation": "runtime.headless",
                "input": {"worldpack": "build/world.json", "ticks": 0},
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        with self.assertRaises(StudioContractError):
            validate_studio_protocol_envelope(
                {
                    **request,
                    "params": {
                        "workspace_id": "workspace_01",
                        "operation": "shell.execute",
                        "input": {"command": "echo unsafe"},
                    },
                }
            )
        cancel = {
            **request,
            "request_id": "cancel-1",
            "method": "job.cancel",
            "params": {"job_id": "job_01"},
        }
        self.assertEqual(cancel, validate_studio_protocol_envelope(cancel))
        with self.assertRaises(StudioContractError):
            validate_studio_protocol_envelope(
                {**cancel, "params": {"job_id": "job_01", "signal": "kill"}}
            )

        managed_job = {
            "format": "rpg-world-forge.studio_job",
            "format_version": 2,
            "job_id": "job_01",
            "workspace_id": "workspace_01",
            "operation": "runtime.headless",
            "state": "queued",
            "input": {"worldpack": "build/world.json", "ticks": 0},
            "result": None,
            "error": None,
            "created_at": "2026-07-22T12:00:00Z",
            "updated_at": "2026-07-22T12:00:00Z",
        }
        create_response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "response",
            "request_id": "job-1",
            "method": "job.create",
            "result": {"job": managed_job},
        }
        self.assertEqual(create_response, validate_studio_protocol_envelope(create_response))
        legacy_job = {
            **managed_job,
            "format_version": 1,
            "operation": "forge.validate",
            "input": {"profile": "release"},
        }
        with self.assertRaisesRegex(StudioContractError, "managed v2"):
            validate_studio_protocol_envelope({**create_response, "result": {"job": legacy_job}})
        cancel_response = {
            **create_response,
            "request_id": "cancel-1",
            "method": "job.cancel",
            "result": {"job": legacy_job},
        }
        self.assertEqual(cancel_response, validate_studio_protocol_envelope(cancel_response))

    def test_protocol_discriminates_source_read_requests_and_responses(self) -> None:
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "request",
            "request_id": "read-1",
            "method": "source.read",
            "params": {"workspace_id": "workspace_01", "path": "source/world.json"},
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        with self.assertRaisesRegex(StudioContractError, "missing fields"):
            validate_studio_protocol_envelope({**request, "params": {}})

        response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "response",
            "request_id": "read-1",
            "method": "source.read",
            "result": {
                "document": {
                    "path": "source/world.json",
                    "kind": "world",
                    "size": 3,
                    "sha256": "0" * 64,
                    "encoding": "utf-8",
                    "content": "{}\n",
                    "json": {},
                }
            },
        }
        self.assertEqual(response, validate_studio_protocol_envelope(response))
        missing_method = dict(response)
        missing_method.pop("method")
        with self.assertRaisesRegex(StudioContractError, "missing fields"):
            validate_studio_protocol_envelope(missing_method)
        with self.assertRaises(StudioContractError):
            validate_studio_protocol_envelope({**response, "method": "source.list"})
        with self.assertRaises(StudioContractError):
            validate_studio_protocol_envelope({**response, "result": {"documents": []}})


if __name__ == "__main__":
    unittest.main()
