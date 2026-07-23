from __future__ import annotations

import base64
import hashlib
import json
import unicodedata
import unittest
from pathlib import Path

import worldforge.studio.contracts as studio_contracts
from worldforge.contract_catalog import audit_contracts, load_contract_catalog
from worldforge.studio.changeset_review import compute_review_sha256
from worldforge.studio.contracts import (
    EXACT_CHANGESET_METHODS,
    LEGACY_METHODS,
    METHODS,
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
    def test_protocol_schema_method_partition_matches_python(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "schemas/studio-protocol.schema.json").read_text(encoding="utf-8")
        )
        definitions = schema["$defs"]

        self.assertEqual(set(METHODS), set(definitions["method"]["enum"]))
        self.assertEqual(set(LEGACY_METHODS), set(definitions["legacyMethod"]["enum"]))
        self.assertTrue(set(EXACT_CHANGESET_METHODS).isdisjoint(LEGACY_METHODS))
        self.assertTrue(studio_contracts.EXACT_ASSET_PREVIEW_METHODS.isdisjoint(LEGACY_METHODS))
        request_refs = {entry["$ref"] for entry in definitions["request"]["oneOf"]}
        response_refs = {entry["$ref"] for entry in definitions["response"]["oneOf"]}
        for name in (
            "changesetCreate",
            "changesetGet",
            "changesetList",
            "changesetDiff",
            "changesetApprove",
            "changesetReject",
            "changesetApply",
        ):
            self.assertIn(f"#/$defs/{name}Request", request_refs)
            self.assertIn(f"#/$defs/{name}Response", response_refs)
        for name in ("assetPreviewOpen", "assetPreviewRead", "assetPreviewClose"):
            self.assertIn(f"#/$defs/{name}Request", request_refs)
            self.assertIn(f"#/$defs/{name}Response", response_refs)
        self.assertEqual(
            studio_contracts.MAX_ASSET_PREVIEW_BASE64_LENGTH,
            definitions["assetPreviewBase64"]["maxLength"],
        )

    def test_catalog_audits_all_studio_contracts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = audit_contracts(root)
        entries = {entry["id"]: entry for entry in load_contract_catalog(root)["contracts"]}

        self.assertTrue(
            {"forge-workspace", "studio-protocol", "studio-changeset", "studio-job"}
            <= entries.keys()
        )
        self.assertGreaterEqual(result.contracts, 35)
        changeset = entries["studio-changeset"]
        self.assertEqual(2, changeset["version"])
        self.assertEqual("Forge Studio reviewable file changeset v2", changeset["title"])
        self.assertIn("docs/decisions/0015-studio-reviewable-changesets.md", changeset["docs"])
        self.assertIn("tests/test_studio_changesets_v2.py", changeset["tests"])

        schema = json.loads((root / changeset["schema"]).read_text(encoding="utf-8"))
        versions = [schema["$defs"][entry["$ref"].rsplit("/", 1)[-1]] for entry in schema["oneOf"]]
        self.assertEqual(
            [1, 2],
            [entry["properties"]["format_version"]["const"] for entry in versions],
        )
        self.assertTrue(all(entry["additionalProperties"] is False for entry in versions))
        self.assertNotIn("review_sha256", versions[0]["properties"])
        self.assertIn("review_sha256", versions[1]["required"])
        self.assertTrue(
            all(
                "base_size" in operation["required"]
                for operation in schema["$defs"]["operation"]["oneOf"]
            )
        )
        self.assertEqual(2, len(schema["oneOf"]))

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

    def test_changeset_validator_reads_v1_and_closes_reviewable_v2(self) -> None:
        operation = {
            "path": "source/lore/entry.json",
            "operation": "replace",
            "base_sha256": "a" * 64,
            "base_size": 7,
            "proposed_sha256": "b" * 64,
            "size": 9,
        }
        changeset = {
            "format": "rpg-world-forge.studio_changeset",
            "format_version": 2,
            "changeset_id": "0123456789abcdef0123456789abcdef",
            "workspace_id": "workspace_01",
            "status": "applying",
            "review_sha256": compute_review_sha256([operation]),
            "operations": [operation],
            "created_at": "2026-07-23T12:00:00Z",
            "updated_at": "2026-07-23T12:00:00Z",
        }

        self.assertEqual(changeset, validate_studio_changeset(changeset))
        with self.assertRaisesRegex(StudioContractError, "review_sha256"):
            validate_studio_changeset({**changeset, "review_sha256": "0" * 64})
        with self.assertRaisesRegex(StudioContractError, "base_size"):
            validate_studio_changeset(
                {
                    **changeset,
                    "operations": [
                        {key: value for key, value in operation.items() if key != "base_size"}
                    ],
                }
            )
        with self.assertRaisesRegex(StudioContractError, "unknown fields"):
            validate_studio_changeset({**changeset, "reviewed_by": "assistant"})

        for invalid_version in ([], {}, 2.0, "2", None, True):
            with (
                self.subTest(format_version=invalid_version),
                self.assertRaisesRegex(StudioContractError, "format_version"),
            ):
                validate_studio_changeset({**changeset, "format_version": invalid_version})

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
        path_contract = schema["$defs"]["sourcePath"]
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

    def test_protocol_closes_and_bounds_asset_preview_requests_and_results(self) -> None:
        revision = "a" * 64
        entry_id = "asset_" + ("b" * 64)
        handle = "C" * 43
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "request",
            "request_id": "preview-open",
            "method": "asset.preview.open",
            "params": {
                "workspace_id": "workspace_01",
                "manifest_revision": revision,
                "entry_id": entry_id,
            },
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        for forbidden in ("path", "offset", "size", "length", "encoding", "base64"):
            with self.subTest(forbidden=forbidden), self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope(
                    {**request, "params": {**request["params"], forbidden: "forged"}}
                )

        read_request = {
            **request,
            "request_id": "preview-read",
            "method": "asset.preview.read",
            "params": {"handle": handle, "sequence": 0},
        }
        close_request = {
            **request,
            "request_id": "preview-close",
            "method": "asset.preview.close",
            "params": {"handle": handle},
        }
        self.assertEqual(read_request, validate_studio_protocol_envelope(read_request))
        self.assertEqual(close_request, validate_studio_protocol_envelope(close_request))
        for invalid_sequence in (-1, 8192, True, 0.0):
            with (
                self.subTest(sequence=invalid_sequence),
                self.assertRaises(StudioContractError),
            ):
                validate_studio_protocol_envelope(
                    {**read_request, "params": {"handle": handle, "sequence": invalid_sequence}}
                )
        for forged_params in (
            {"handle": handle, "sequence": 0, "offset": 0},
            {"handle": handle, "sequence": 0, "size": 1},
            {"handle": handle, "sequence": 0, "encoding": "base64"},
            {"handle": handle, "sequence": 0, "data_base64": "YQ=="},
            {"handle": handle, "length": 1},
        ):
            with self.subTest(params=forged_params), self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope({**read_request, "params": forged_params})

        open_response = {
            **request,
            "kind": "response",
            "method": "asset.preview.open",
            "result": {
                "handle": handle,
                "manifest_revision": revision,
                "entry_id": entry_id,
                "media_type": "image/png",
                "byte_length": 3,
                "sha256": hashlib.sha256(b"abc").hexdigest(),
                "chunk_bytes": 65_536,
            },
        }
        open_response.pop("params")
        self.assertEqual(open_response, validate_studio_protocol_envelope(open_response))
        self.assertNotIn("path", open_response["result"])
        for invalid_media_type in ("font/ttf", "model/gltf-binary", "image/jpeg"):
            with (
                self.subTest(media_type=invalid_media_type),
                self.assertRaises(StudioContractError),
            ):
                validate_studio_protocol_envelope(
                    {
                        **open_response,
                        "result": {
                            **open_response["result"],
                            "media_type": invalid_media_type,
                        },
                    }
                )

        read_result = {
            "handle": handle,
            "sequence": 0,
            "data_base64": base64.b64encode(b"abc").decode("ascii"),
            "byte_length": 3,
            "cumulative_bytes": 3,
            "cumulative_sha256": hashlib.sha256(b"abc").hexdigest(),
            "eof": True,
        }
        read_response = {
            **open_response,
            "request_id": "preview-read",
            "method": "asset.preview.read",
            "result": read_result,
        }
        self.assertEqual(read_response, validate_studio_protocol_envelope(read_response))
        full_chunk = b"x" * 65_536
        nonfinal = {
            **read_result,
            "data_base64": base64.b64encode(full_chunk).decode("ascii"),
            "byte_length": 65_536,
            "cumulative_bytes": 65_536,
            "cumulative_sha256": hashlib.sha256(full_chunk).hexdigest(),
            "eof": False,
        }
        self.assertEqual(
            {**read_response, "result": nonfinal},
            validate_studio_protocol_envelope({**read_response, "result": nonfinal}),
        )
        invalid_results = (
            {**read_result, "data_base64": "YR=="},
            {**read_result, "data_base64": "YWJj="},
            {**read_result, "data_base64": ""},
            {**read_result, "byte_length": 2},
            {**read_result, "cumulative_bytes": 4},
            {**read_result, "sequence": 1},
            {**read_result, "eof": False},
            {**read_result, "path": "/private/preview.png"},
            {**read_result, "payload": "YWJj"},
        )
        for result in invalid_results:
            with self.subTest(result=result), self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope({**read_response, "result": result})

        close_response = {
            **open_response,
            "request_id": "preview-close",
            "method": "asset.preview.close",
            "result": {"handle": handle, "closed": True},
        }
        self.assertEqual(close_response, validate_studio_protocol_envelope(close_response))
        with self.assertRaises(StudioContractError):
            validate_studio_protocol_envelope(
                {**close_response, "result": {"handle": handle, "closed": False}}
            )

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

    def test_protocol_discriminates_closed_changeset_requests_and_v1_v2_results(self) -> None:
        operation_v2 = {
            "path": "source/lore/entry.md",
            "operation": "replace",
            "base_sha256": "a" * 64,
            "base_size": 4,
            "proposed_sha256": "b" * 64,
            "size": 4,
        }
        changeset_v2 = {
            "format": "rpg-world-forge.studio_changeset",
            "format_version": 2,
            "changeset_id": "changeset_01",
            "workspace_id": "workspace_01",
            "status": "staged",
            "review_sha256": compute_review_sha256([operation_v2]),
            "operations": [operation_v2],
            "created_at": "2026-07-23T12:00:00Z",
            "updated_at": "2026-07-23T12:00:00Z",
        }
        operation_v1 = {key: value for key, value in operation_v2.items() if key != "base_size"}
        changeset_v1 = {key: value for key, value in changeset_v2.items() if key != "review_sha256"}
        changeset_v1["format_version"] = 1
        changeset_v1["operations"] = [operation_v1]
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "request",
            "request_id": "changeset-request",
            "method": "changeset.create",
            "params": {
                "workspace_id": "workspace_01",
                "operations": [
                    {
                        "path": "source/lore/entry.md",
                        "operation": "replace",
                        "expected_base_sha256": "a" * 64,
                        "content": "new\n",
                    }
                ],
            },
        }
        requests = (
            request,
            {
                **request,
                "method": "changeset.get",
                "params": {"changeset_id": "changeset_01"},
            },
            {
                **request,
                "method": "changeset.list",
                "params": {"workspace_id": "workspace_01", "status": "applying", "limit": 1},
            },
            {
                **request,
                "method": "changeset.diff",
                "params": {"changeset_id": "changeset_01"},
            },
            *(
                {
                    **request,
                    "method": method,
                    "params": {
                        "changeset_id": "changeset_01",
                        "expected_review_sha256": changeset_v2["review_sha256"],
                    },
                }
                for method in (
                    "changeset.approve",
                    "changeset.reject",
                    "changeset.apply",
                )
            ),
        )
        for envelope in requests:
            with self.subTest(method=envelope["method"]):
                self.assertEqual(envelope, validate_studio_protocol_envelope(envelope))

        invalid_requests = (
            {**request, "params": {**request["params"], "command": "shell.exec"}},
            {
                **request,
                "params": {
                    "workspace_id": "workspace_01",
                    "operations": [
                        {
                            "path": "source/lore/entry.md",
                            "operation": "replace",
                            "expected_base_sha256": "A" * 64,
                            "content": "new\n",
                        }
                    ],
                },
            },
            {
                **request,
                "params": {
                    "workspace_id": "workspace_01",
                    "operations": [
                        {
                            "path": "source/lore/entry.md",
                            "operation": "replace",
                            "content": "new\n",
                        }
                    ],
                },
            },
            {
                **request,
                "method": "changeset.get",
                "params": {"changeset_id": "changeset_01", "workspace_id": "workspace_01"},
            },
            {
                **request,
                "method": "changeset.list",
                "params": {"status": "created"},
            },
            {
                **request,
                "method": "changeset.diff",
                "params": {"changeset_id": "../bad"},
            },
            {
                **request,
                "method": "changeset.approve",
                "params": {
                    "changeset_id": "changeset_01",
                    "expected_review_sha256": None,
                },
            },
        )
        for envelope in invalid_requests:
            with self.subTest(envelope=envelope), self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope(envelope)

        response = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "response",
            "request_id": "changeset-request",
            "method": "changeset.create",
            "result": {"changeset": changeset_v2},
        }
        for method in (
            "changeset.create",
            "changeset.get",
            "changeset.approve",
            "changeset.reject",
            "changeset.apply",
        ):
            for changeset in (changeset_v1, changeset_v2):
                envelope = {**response, "method": method, "result": {"changeset": changeset}}
                with self.subTest(method=method, version=changeset["format_version"]):
                    self.assertEqual(envelope, validate_studio_protocol_envelope(envelope))

        listed = {
            **response,
            "method": "changeset.list",
            "result": {"changesets": [changeset_v1, changeset_v2]},
        }
        self.assertEqual(listed, validate_studio_protocol_envelope(listed))

        diff_v2 = {
            "changeset_id": "changeset_01",
            "changeset_format_version": 2,
            "available": True,
            "unavailable_reason": None,
            "review_sha256": changeset_v2["review_sha256"],
            "operations": [
                {
                    **operation_v2,
                    "text_hunks": [
                        {
                            "base_start": 1,
                            "base_count": 1,
                            "proposed_start": 1,
                            "proposed_count": 1,
                            "lines": [
                                {"kind": "remove", "text": "old\n"},
                                {"kind": "add", "text": "new\n"},
                            ],
                        }
                    ],
                    "json_pointer_changes": None,
                }
            ],
        }
        diff_response = {
            **response,
            "method": "changeset.diff",
            "result": {"diff": diff_v2},
        }
        self.assertEqual(diff_response, validate_studio_protocol_envelope(diff_response))
        legacy_diff = {
            **diff_v2,
            "changeset_format_version": 1,
            "available": False,
            "unavailable_reason": "legacy_base_bytes_not_retained",
            "review_sha256": None,
            "operations": [],
        }
        legacy_response = {**diff_response, "result": {"diff": legacy_diff}}
        self.assertEqual(legacy_response, validate_studio_protocol_envelope(legacy_response))

        invalid_responses = (
            {**listed, "result": {"changeset": changeset_v2}},
            {
                **diff_response,
                "result": {"diff": {**diff_v2, "changeset_format_version": 1}},
            },
            {
                **diff_response,
                "result": {
                    "diff": {
                        **diff_v2,
                        "operations": [
                            {
                                **diff_v2["operations"][0],
                                "operation": "execute",
                            }
                        ],
                    }
                },
            },
            {
                **diff_response,
                "result": {
                    "diff": {
                        **diff_v2,
                        "operations": [
                            {
                                **diff_v2["operations"][0],
                                "text_hunks": [
                                    {
                                        **diff_v2["operations"][0]["text_hunks"][0],
                                        "lines": [{"kind": [], "text": "bad"}],
                                    }
                                ],
                            }
                        ],
                    }
                },
            },
        )
        for envelope in invalid_responses:
            with self.subTest(envelope=envelope), self.assertRaises(StudioContractError):
                validate_studio_protocol_envelope(envelope)


if __name__ == "__main__":
    unittest.main()
