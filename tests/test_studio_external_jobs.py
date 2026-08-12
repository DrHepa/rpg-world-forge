from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from isoworld.content.file_stat import path_file_stat
from tests.test_multigenre_standalone_materialization import _ready_materialization
from worldforge.game_package import (
    WorldForgeGamePackageError,
    extract_game_package,
    package_game,
)
from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.standalone_game import StandaloneGameError, materialize_game
from worldforge.studio.contracts import (
    validate_studio_external_grant,
    validate_studio_job,
    validate_studio_protocol_envelope,
)
from worldforge.studio.errors import StudioError
from worldforge.studio.executor import JobScheduler
from worldforge.studio.external_grants import ExternalGrantManager
from worldforge.studio.external_jobs import (
    ExternalJobExecutionError,
    execute_external_operation,
)
from worldforge.studio.jobs import JobManager
from worldforge.studio.service import StudioService
from worldforge.studio.storage import SCHEMA_VERSION, StudioStore
from worldforge.studio.workspaces import WorkspaceManager

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_TIMESTAMP = "2026-07-30T12:00:00Z"


def _contains_path(value: object, path: Path) -> bool:
    if isinstance(value, dict):
        return any(_contains_path(item, path) for item in value.values())
    if isinstance(value, list):
        return any(_contains_path(item, path) for item in value)
    return isinstance(value, str) and str(path) in value


class StudioExternalContractTests(unittest.TestCase):
    def test_external_grant_and_v3_job_are_closed_and_pathless(self) -> None:
        grant = {
            "format": "rpg-world-forge.studio_external_grant",
            "format_version": 1,
            "grant_id": "grant_01",
            "workspace_id": "workspace_01",
            "operation": "game.materialize",
            "role": "source",
            "artifact_kind": "game_materialization_bundle",
            "display_name": "neutral-materialization",
            "state": "ready",
            "expected_content_hash": _HASH_A,
            "created_at": _TIMESTAMP,
            "updated_at": _TIMESTAMP,
        }
        self.assertEqual(grant, validate_studio_external_grant(grant))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_external_grant({**grant, "path": "/private/source"})

        job = {
            "format": "rpg-world-forge.studio_job",
            "format_version": 3,
            "job_id": "job_01",
            "workspace_id": "workspace_01",
            "operation": "game.materialize",
            "state": "queued",
            "input": {
                "source_grant_id": "grant_01",
                "target_grant_id": "grant_02",
                "expected_materialization_hash": _HASH_A,
            },
            "result": None,
            "error": None,
            "created_at": _TIMESTAMP,
            "updated_at": _TIMESTAMP,
        }
        self.assertEqual(job, validate_studio_job(job))
        with self.assertRaisesRegex(ValueError, "state"):
            validate_studio_job({**job, "state": "awaiting_user"})
        with self.assertRaisesRegex(ValueError, "invalid fields"):
            validate_studio_job(
                {
                    **job,
                    "input": {
                        **job["input"],
                        "target_path": "/private/target",
                    },
                }
            )

        succeeded = {
            **job,
            "state": "succeeded",
            "result": {
                "operation": "game.materialize",
                "game_id": "neutral_game",
                "standalone_hash": _HASH_B,
                "payload_lock_hash": "c" * 64,
                "runtime_bundle_hash": "d" * 64,
                "target_grant_id": "grant_02",
            },
        }
        self.assertEqual(succeeded, validate_studio_job(succeeded))
        with self.assertRaisesRegex(ValueError, "invalid fields"):
            validate_studio_job(
                {
                    **succeeded,
                    "result": {**succeeded["result"], "target_path": "/private/target"},
                }
            )

    def test_external_recovery_error_carries_closed_pathless_evidence(self) -> None:
        job = {
            "format": "rpg-world-forge.studio_job",
            "format_version": 3,
            "job_id": "job_01",
            "workspace_id": "workspace_01",
            "operation": "game.materialize",
            "state": "orphaned",
            "input": {
                "source_grant_id": "grant_01",
                "target_grant_id": "grant_02",
                "expected_materialization_hash": _HASH_A,
            },
            "result": None,
            "error": {
                "code": "recovery_required",
                "message": "Exact retained output requires recovery",
                "recovery_evidence": {
                    "stage": {
                        "locator": ".worldforge-retained-stage-abc",
                        "identity": [7, 11],
                        "retention": "active",
                    },
                    "journal": {
                        "locator": ".worldforge-retained-journal-def.json",
                        "identity": None,
                        "retention": "active",
                    },
                },
            },
            "created_at": _TIMESTAMP,
            "updated_at": _TIMESTAMP,
        }
        self.assertEqual(job, validate_studio_job(job))
        leaked = json.loads(json.dumps(job))
        leaked["error"]["recovery_evidence"]["stage"]["locator"] = "/private/stage"
        with self.assertRaisesRegex(ValueError, "locator"):
            validate_studio_job(leaked)

    def test_protocol_v2_adds_external_methods_without_broadening_v1(self) -> None:
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 2,
            "kind": "request",
            "request_id": "request_01",
            "method": "job.recover",
            "params": {"job_id": "job_01", "action": "resume"},
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        with self.assertRaisesRegex(ValueError, "not available"):
            validate_studio_protocol_envelope({**request, "protocol_version": 1})
        with self.assertRaisesRegex(ValueError, "action"):
            validate_studio_protocol_envelope(
                {**request, "params": {"job_id": "job_01", "action": "retry"}}
            )
        with self.assertRaisesRegex(ValueError, "not available"):
            validate_studio_protocol_envelope(
                {
                    **request,
                    "method": "world.validate",
                    "params": {"workspace_id": "workspace_01"},
                }
            )
        with self.assertRaisesRegex(ValueError, "external"):
            validate_studio_protocol_envelope(
                {
                    **request,
                    "method": "job.create",
                    "params": {
                        "workspace_id": "workspace_01",
                        "operation": "runtime.headless",
                        "input": {
                            "worldpack": "build/world.worldpack.json",
                            "ticks": 1,
                        },
                    },
                }
            )
        grant_request = {
            **request,
            "method": "external_grant.create",
            "params": {
                "workspace_id": "workspace_01",
                "operation": "game.package.extract",
                "role": "source",
                "artifact_kind": "game_package",
                "display_name": "neutral-package.wfgame",
                "path": "/" + ("a" * 32_766),
                "expected_content_hash": _HASH_A,
            },
        }
        self.assertEqual(
            grant_request,
            validate_studio_protocol_envelope(grant_request),
        )
        with self.assertRaisesRegex(ValueError, "path"):
            validate_studio_protocol_envelope(
                {
                    **grant_request,
                    "params": {
                        **grant_request["params"],
                        "path": "/" + ("a" * 32_767),
                    },
                }
            )

    def test_protocol_v2_job_get_and_list_are_closed_to_v3_jobs(self) -> None:
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 2,
            "kind": "request",
            "request_id": "request_01",
            "method": "job.get",
            "params": {"job_id": "job_01"},
        }
        self.assertEqual(request, validate_studio_protocol_envelope(request))
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            validate_studio_protocol_envelope(
                {
                    **request,
                    "params": {"job_id": "job_01", "workspace_id": "workspace_01"},
                }
            )

        list_request = {
            **request,
            "method": "job.list",
            "params": {
                "workspace_id": "workspace_01",
                "state": "orphaned",
                "limit": 10,
            },
        }
        self.assertEqual(list_request, validate_studio_protocol_envelope(list_request))
        for params in (
            {"unexpected": True},
            {"workspace_id": []},
            {"state": "awaiting_user"},
            {"limit": 0},
            {"limit": True},
        ):
            with self.subTest(params=params), self.assertRaises(ValueError):
                validate_studio_protocol_envelope({**list_request, "params": params})

        v3_job = {
            "format": "rpg-world-forge.studio_job",
            "format_version": 3,
            "job_id": "job_01",
            "workspace_id": "workspace_01",
            "operation": "game.materialize",
            "state": "queued",
            "input": {
                "source_grant_id": "grant_01",
                "target_grant_id": "grant_02",
                "expected_materialization_hash": _HASH_A,
            },
            "result": None,
            "error": None,
            "created_at": _TIMESTAMP,
            "updated_at": _TIMESTAMP,
        }
        response = {
            **list_request,
            "kind": "response",
            "result": {"jobs": [v3_job]},
        }
        response.pop("params")
        self.assertEqual(response, validate_studio_protocol_envelope(response))
        with self.assertRaisesRegex(ValueError, "version"):
            validate_studio_protocol_envelope(
                {
                    **response,
                    "result": {
                        "jobs": [
                            {
                                **v3_job,
                                "format_version": 2,
                                "operation": "runtime.headless",
                                "input": {
                                    "worldpack": "build/worldpack.json",
                                    "ticks": 0,
                                },
                            }
                        ]
                    },
                }
            )

    def test_external_contract_schemas_are_separate_and_cataloged(self) -> None:
        root = Path(__file__).resolve().parents[1]
        legacy_job = json.loads(
            (root / "schemas/studio-job.schema.json").read_text(encoding="utf-8")
        )
        legacy_protocol = json.loads(
            (root / "schemas/studio-protocol.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual([1, 2], legacy_job["properties"]["format_version"]["enum"])
        self.assertEqual(
            1,
            legacy_protocol["properties"]["protocol_version"]["const"],
        )

        grant_schema = json.loads(
            (root / "schemas/studio-external-grant.schema.json").read_text(encoding="utf-8")
        )
        job_v3_schema = json.loads(
            (root / "schemas/studio-job-v3.schema.json").read_text(encoding="utf-8")
        )
        protocol_v2_schema = json.loads(
            (root / "schemas/studio-protocol-v2.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "rpg-world-forge.studio_external_grant",
            grant_schema["properties"]["format"]["const"],
        )
        self.assertEqual(3, job_v3_schema["properties"]["format_version"]["const"])
        self.assertEqual(
            2,
            protocol_v2_schema["properties"]["protocol_version"]["const"],
        )
        grant_branches = grant_schema["oneOf"]
        self.assertEqual(6, len(grant_branches))
        self.assertTrue(
            all(
                set(branch["required"]) == {"operation", "role", "artifact_kind"}
                for branch in grant_branches
            )
        )
        for name in ("materializeOperation", "packageOperation", "extractOperation"):
            self.assertEqual(
                {"operation", "input", "result"},
                set(job_v3_schema["$defs"][name]["required"]),
            )
        job_create_branches = protocol_v2_schema["$defs"]["jobCreateParams"]["oneOf"]
        self.assertEqual(3, len(job_create_branches))
        self.assertTrue(
            all(set(branch["required"]) == {"operation", "input"} for branch in job_create_branches)
        )
        grant_create_branches = protocol_v2_schema["$defs"]["grantCreateParams"]["oneOf"]
        self.assertEqual(6, len(grant_create_branches))
        self.assertTrue(
            all(
                set(branch["required"]) == {"operation", "role", "artifact_kind"}
                for branch in grant_create_branches
            )
        )
        for contract in ("request", "response"):
            field = "params" if contract == "request" else "result"
            for branch in protocol_v2_schema["$defs"][contract]["allOf"][2]["oneOf"]:
                self.assertEqual(
                    {"method", field},
                    set(branch["required"]),
                )
        job_list = protocol_v2_schema["$defs"]["response"]["allOf"][2]["oneOf"][-1]
        self.assertEqual(
            {"$ref": "studio-job-v3.schema.json"},
            job_list["properties"]["result"]["properties"]["jobs"]["items"],
        )
        catalog = json.loads((root / "contracts/catalog.json").read_text(encoding="utf-8"))
        entries = {entry["id"]: entry for entry in catalog["contracts"]}
        self.assertEqual(
            {
                "studio-external-grant",
                "studio-job-v3",
                "studio-protocol-v2",
            },
            {
                key
                for key in entries
                if key
                in {
                    "studio-external-grant",
                    "studio-job-v3",
                    "studio-protocol-v2",
                }
            },
        )


class StudioExternalStorageTests(unittest.TestCase):
    def test_v1_to_v3_upgrade_is_additive_idempotent_and_preserves_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / "studio"
            with StudioStore(data_dir) as store:
                self.assertEqual(5, SCHEMA_VERSION)
                store.connection.execute(
                    "INSERT INTO workspaces "
                    "(workspace_id, record_json, forge_dev, forge_ino, world_dev, world_ino, "
                    "game_dev, game_ino, bundle_dev, bundle_ino) "
                    "VALUES ('workspace_01', '{}', '1', '1', '2', '2', NULL, NULL, NULL, NULL)"
                )
                store.connection.execute(
                    "INSERT INTO jobs (job_id, workspace_id, state, record_json) "
                    "VALUES ('legacy_01', 'workspace_01', 'queued', '{}')"
                )
                store.connection.commit()

            connection = sqlite3.connect(data_dir / "studio.sqlite3")
            connection.execute("DROP TABLE external_grants")
            connection.execute("UPDATE schema_meta SET value = '1' WHERE key = 'schema_version'")
            connection.commit()
            connection.close()

            for _ in range(2):
                with StudioStore(data_dir) as reopened:
                    version = reopened.connection.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()[0]
                    self.assertEqual("5", version)
                    self.assertEqual(
                        "{}",
                        reopened.connection.execute(
                            "SELECT record_json FROM workspaces WHERE workspace_id = 'workspace_01'"
                        ).fetchone()[0],
                    )
                    self.assertEqual(
                        "{}",
                        reopened.connection.execute(
                            "SELECT record_json FROM jobs WHERE job_id = 'legacy_01'"
                        ).fetchone()[0],
                    )
                    tables = {
                        row[0]
                        for row in reopened.connection.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        )
                    }
                    self.assertLessEqual(
                        {
                            "external_grants",
                            "creation_root_grants",
                            "creation_workspaces",
                            "creation_events",
                        },
                        tables,
                    )


class StudioExternalGrantAndJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.world = self.root / "world"
        create_world_project(
            self.world,
            world_id="external_job_world",
            title="External Jobs",
            language="en",
        )
        self.data_dir = self.root / "studio-data"
        self.store = StudioStore(self.data_dir)
        WorkspaceManager(self.store).register(
            {
                "workspace_id": "workspace_01",
                "forge_root": str(FORGE_ROOT),
                "world_root": str(self.world),
            }
        )
        self.external = self.root / "external"
        self.external.mkdir()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _grant_pair(self) -> tuple[dict[str, object], dict[str, object], Path, Path]:
        source = self.external / "materialization"
        source.mkdir()
        target = self.external / "standalone-game"
        grants = ExternalGrantManager(self.store)
        source_grant = grants.create(
            {
                "grant_id": "grant_source",
                "workspace_id": "workspace_01",
                "operation": "game.materialize",
                "role": "source",
                "artifact_kind": "game_materialization_bundle",
                "display_name": "Materialization source",
                "path": str(source),
                "expected_content_hash": _HASH_A,
            }
        )
        target_grant = grants.create(
            {
                "grant_id": "grant_target",
                "workspace_id": "workspace_01",
                "operation": "game.materialize",
                "role": "target",
                "artifact_kind": "standalone_game",
                "display_name": "Standalone target",
                "path": str(target),
                "expected_content_hash": None,
            }
        )
        return source_grant, target_grant, source, target

    def test_paths_remain_private_and_target_reservation_is_atomic(self) -> None:
        source_grant, target_grant, source, target = self._grant_pair()
        self.assertFalse(_contains_path(source_grant, source))
        self.assertFalse(_contains_path(target_grant, target))

        jobs = JobManager(self.store)
        job = jobs.create(
            {
                "job_id": "job_01",
                "workspace_id": "workspace_01",
                "operation": "game.materialize",
                "input": {
                    "source_grant_id": source_grant["grant_id"],
                    "target_grant_id": target_grant["grant_id"],
                    "expected_materialization_hash": _HASH_A,
                },
            }
        )
        self.assertEqual(3, job["format_version"])
        self.assertEqual("reserved", ExternalGrantManager(self.store).get("grant_target")["state"])
        private = self.store.connection.execute(
            "SELECT absolute_path, reserved_job_id, generation "
            "FROM external_grants WHERE grant_id = 'grant_target'"
        ).fetchone()
        self.assertEqual(str(target), private["absolute_path"])
        self.assertEqual("job_01", private["reserved_job_id"])
        self.assertEqual(1, private["generation"])

        with self.assertRaisesRegex(StudioError, "reserved"):
            jobs.create(
                {
                    "job_id": "job_02",
                    "workspace_id": "workspace_01",
                    "operation": "game.materialize",
                    "input": {
                        "source_grant_id": "grant_source",
                        "target_grant_id": "grant_target",
                        "expected_materialization_hash": _HASH_A,
                    },
                }
            )
        self.assertIsNone(
            self.store.connection.execute("SELECT 1 FROM jobs WHERE job_id = 'job_02'").fetchone()
        )

    @unittest.skipUnless(os.name == "posix", "POSIX link semantics required")
    def test_grants_reject_links_hardlinks_and_workspace_overlap(self) -> None:
        grants = ExternalGrantManager(self.store)
        package = self.external / "game.wfgame"
        package.write_bytes(b"package")
        alias = self.external / "alias.wfgame"
        alias.symlink_to(package)
        with self.assertRaises(StudioError):
            grants.create(
                {
                    "workspace_id": "workspace_01",
                    "operation": "game.package.extract",
                    "role": "source",
                    "artifact_kind": "game_package",
                    "display_name": "Linked package",
                    "path": str(alias),
                    "expected_content_hash": _HASH_A,
                }
            )

        hardlink = self.external / "hardlink.wfgame"
        os.link(package, hardlink)
        with self.assertRaises(StudioError):
            grants.create(
                {
                    "workspace_id": "workspace_01",
                    "operation": "game.package.extract",
                    "role": "source",
                    "artifact_kind": "game_package",
                    "display_name": "Hardlinked package",
                    "path": str(package),
                    "expected_content_hash": _HASH_A,
                }
            )

        with self.assertRaisesRegex(StudioError, "overlap"):
            grants.create(
                {
                    "workspace_id": "workspace_01",
                    "operation": "game.materialize",
                    "role": "target",
                    "artifact_kind": "standalone_game",
                    "display_name": "Unsafe target",
                    "path": str(self.world / "generated-game"),
                    "expected_content_hash": None,
                }
            )

    def test_live_grants_reject_duplicate_and_nested_authority(self) -> None:
        grants = ExternalGrantManager(self.store)
        source = self.external / "source"
        source.mkdir()
        first = grants.create(
            {
                "grant_id": "first_source",
                "workspace_id": "workspace_01",
                "operation": "game.materialize",
                "role": "source",
                "artifact_kind": "game_materialization_bundle",
                "display_name": "First source",
                "path": str(source),
                "expected_content_hash": _HASH_A,
            }
        )
        self.assertEqual("ready", first["state"])

        attacks = (
            {
                "grant_id": "duplicate_source",
                "workspace_id": "workspace_01",
                "operation": "game.materialize",
                "role": "source",
                "artifact_kind": "game_materialization_bundle",
                "display_name": "Duplicate source",
                "path": str(source),
                "expected_content_hash": _HASH_A,
            },
            {
                "grant_id": "nested_target",
                "workspace_id": "workspace_01",
                "operation": "game.materialize",
                "role": "target",
                "artifact_kind": "standalone_game",
                "display_name": "Nested target",
                "path": str(source / "nested-game"),
                "expected_content_hash": None,
            },
        )
        for attack in attacks:
            with self.subTest(grant_id=attack["grant_id"]):
                with self.assertRaises(StudioError) as raised:
                    grants.create(attack)
                self.assertEqual("invalid_request", raised.exception.code)
                self.assertIn("overlaps active external authority", raised.exception.message)
                self.assertNotIn(str(source), raised.exception.message)

    def test_startup_orphans_v3_job_and_marks_target_recovery_required(self) -> None:
        self._grant_pair()
        jobs = JobManager(self.store)
        job = jobs.create(
            {
                "job_id": "job_01",
                "workspace_id": "workspace_01",
                "operation": "game.materialize",
                "input": {
                    "source_grant_id": "grant_source",
                    "target_grant_id": "grant_target",
                    "expected_materialization_hash": _HASH_A,
                },
            }
        )
        running = jobs.claim_next()
        self.assertEqual(job["job_id"], running["job_id"])
        self.store.close()

        self.store = StudioStore(self.data_dir)
        orphaned = JobManager(self.store).get("job_01")
        self.assertEqual("orphaned", orphaned["state"])
        self.assertEqual("recovery_required", orphaned["error"]["code"])
        self.assertEqual(
            "recovery_required",
            ExternalGrantManager(self.store).get("grant_target")["state"],
        )
        private = self.store.connection.execute(
            "SELECT absolute_path, reserved_job_id FROM external_grants "
            "WHERE grant_id = 'grant_target'"
        ).fetchone()
        self.assertEqual(str(self.external / "standalone-game"), private["absolute_path"])
        self.assertEqual("job_01", private["reserved_job_id"])

    def test_orphaned_external_job_cannot_be_canceled_outside_recovery(self) -> None:
        self._grant_pair()
        jobs = JobManager(self.store)
        job = jobs.create(
            {
                "job_id": "job_01",
                "workspace_id": "workspace_01",
                "operation": "game.materialize",
                "input": {
                    "source_grant_id": "grant_source",
                    "target_grant_id": "grant_target",
                    "expected_materialization_hash": _HASH_A,
                },
            }
        )
        self.assertEqual("running", jobs.claim_next()["state"])
        jobs.finish(job["job_id"], "orphaned", reason="worker_output_overflow")

        with self.assertRaises(StudioError) as raised:
            jobs.cancel(job["job_id"])
        self.assertEqual("invalid_state", raised.exception.code)
        orphaned = jobs.get(job["job_id"])
        self.assertEqual("orphaned", orphaned["state"])
        self.assertEqual("recovery_required", orphaned["error"]["code"])
        self.assertEqual(
            "recovery_required",
            ExternalGrantManager(self.store).get("grant_target")["state"],
        )
        private = self.store.connection.execute(
            "SELECT reserved_job_id FROM external_grants WHERE grant_id = 'grant_target'"
        ).fetchone()
        self.assertEqual(job["job_id"], private["reserved_job_id"])

    def test_forced_cancel_preserves_typed_recovery_evidence(self) -> None:
        source_grant, target_grant, _source, target = self._grant_pair()
        jobs = JobManager(self.store)
        job = jobs.create(
            {
                "job_id": "job_forced_cancel_evidence",
                "workspace_id": "workspace_01",
                "operation": "game.materialize",
                "input": {
                    "source_grant_id": source_grant["grant_id"],
                    "target_grant_id": target_grant["grant_id"],
                    "expected_materialization_hash": _HASH_A,
                },
            }
        )
        self.assertEqual("running", jobs.claim_next()["state"])
        target.mkdir()
        evidence = {
            "stage": {
                "locator": ".worldforge-retained-stage-abc",
                "identity": [7, 11],
                "retention": "active",
            }
        }
        with patch(
            "worldforge.studio.jobs.execute_external_operation",
            side_effect=ExternalJobExecutionError(
                "recovery_required",
                "Exact retained output requires recovery",
                recovery_evidence=evidence,
            ),
        ):
            completed = jobs.resolve_forced_cancel(job["job_id"])

        self.assertEqual("orphaned", completed["state"])
        self.assertEqual("recovery_required", completed["error"]["code"])
        self.assertEqual(evidence, completed["error"]["recovery_evidence"])

    def test_source_identity_drift_is_detected_without_path_disclosure(self) -> None:
        self._grant_pair()
        source = self.external / "materialization"
        source.rename(self.external / "old-materialization")
        source.mkdir()
        with self.assertRaises(StudioError) as raised:
            ExternalGrantManager(self.store).binding_for_job(
                {
                    "job_id": "job_01",
                    "workspace_id": "workspace_01",
                    "operation": "game.materialize",
                    "input": {
                        "source_grant_id": "grant_source",
                        "target_grant_id": "grant_target",
                        "expected_materialization_hash": _HASH_A,
                    },
                }
            )
        self.assertEqual("conflict", raised.exception.code)
        self.assertNotIn(str(source), raised.exception.message)

    def test_private_rows_retain_native_source_and_target_parent_identities(self) -> None:
        self._grant_pair()
        source = path_file_stat(self.external / "materialization")
        parent = path_file_stat(self.external)
        rows = {
            row["grant_id"]: row
            for row in self.store.connection.execute(
                "SELECT * FROM external_grants ORDER BY grant_id"
            )
        }
        self.assertEqual(
            (source.st_dev, source.st_ino),
            (
                int(rows["grant_source"]["source_dev"]),
                int(rows["grant_source"]["source_ino"]),
            ),
        )
        self.assertEqual(
            (parent.st_dev, parent.st_ino),
            (
                int(rows["grant_target"]["parent_dev"]),
                int(rows["grant_target"]["parent_ino"]),
            ),
        )
        self.assertEqual("standalone-game", rows["grant_target"]["normalized_leaf"])


class StudioExternalExecutionTests(unittest.TestCase):
    def test_worker_preserves_typed_pathless_recovery_evidence(self) -> None:
        import io
        from types import SimpleNamespace

        from worldforge.studio import worker

        stdin = SimpleNamespace(buffer=io.BytesIO(b"{}\n"))
        stdout_buffer = io.BytesIO()
        stdout = SimpleNamespace(buffer=stdout_buffer)
        evidence = {
            "stage": {
                "locator": ".worldforge-retained-stage-abc",
                "identity": [7, 11],
                "retention": "active",
            }
        }
        with (
            patch.object(worker.sys, "stdin", stdin),
            patch.object(worker.sys, "stdout", stdout),
            patch.object(
                worker,
                "execute",
                side_effect=ExternalJobExecutionError(
                    "recovery_required",
                    "Exact retained output requires recovery",
                    recovery_evidence=evidence,
                ),
            ),
        ):
            self.assertEqual(0, worker.main())
        self.assertEqual(
            {
                "ok": False,
                "error": {
                    "code": "recovery_required",
                    "message": "Exact retained output requires recovery",
                    "recovery_evidence": evidence,
                },
            },
            json.loads(stdout_buffer.getvalue()),
        )

    def test_executor_accepts_typed_pathless_recovery_evidence(self) -> None:
        evidence = {
            "journal": {
                "locator": ".worldforge-retained-journal-abc.json",
                "identity": None,
                "retention": "active",
            }
        }
        response = {
            "ok": False,
            "error": {
                "code": "recovery_required",
                "message": "Exact retained journal requires recovery",
                "recovery_evidence": evidence,
            },
        }
        self.assertEqual(
            response,
            JobScheduler._decode_worker_response(  # noqa: SLF001
                json.dumps(response, separators=(",", ":"), sort_keys=True).encode()
            ),
        )

    def test_executor_persists_typed_recovery_evidence_on_orphaned_job(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-studio-external-evidence-") as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            with StudioStore(root / "studio") as store:
                self._workspace(store, root)
                source = external / "source"
                source.mkdir()
                target = external / "target"
                grants = ExternalGrantManager(store)
                grants.create(
                    {
                        "grant_id": "source_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "source",
                        "artifact_kind": "game_materialization_bundle",
                        "display_name": "Source",
                        "path": str(source),
                        "expected_content_hash": _HASH_A,
                    }
                )
                grants.create(
                    {
                        "grant_id": "target_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "target",
                        "artifact_kind": "standalone_game",
                        "display_name": "Target",
                        "path": str(target),
                        "expected_content_hash": None,
                    }
                )
                jobs = JobManager(store)
                job = jobs.create(
                    {
                        "job_id": "evidence_job",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "input": {
                            "source_grant_id": "source_grant",
                            "target_grant_id": "target_grant",
                            "expected_materialization_hash": _HASH_A,
                        },
                    }
                )
                evidence = {
                    "stage": {
                        "locator": ".worldforge-retained-stage-abc",
                        "identity": [7, 11],
                        "retention": "active",
                    }
                }
                response = {
                    "ok": False,
                    "error": {
                        "code": "recovery_required",
                        "message": "Exact retained output requires recovery",
                        "recovery_evidence": evidence,
                    },
                }
                payload = json.dumps(response, separators=(",", ":"), sort_keys=True).encode()
                command = (
                    sys.executable,
                    "-I",
                    "-c",
                    f"import sys;sys.stdin.buffer.read();sys.stdout.buffer.write({payload!r})",
                )
                with patch("worldforge.studio.executor._worker_command", return_value=command):
                    scheduler = JobScheduler(root / "studio", timeout_seconds=10)
                    scheduler.start()
                    try:
                        scheduler.notify()
                        completed = self._wait(jobs, job["job_id"])
                    finally:
                        scheduler.shutdown()
                self.assertEqual("orphaned", completed["state"])
                self.assertEqual(evidence, completed["error"]["recovery_evidence"])

    def _wait(
        self,
        jobs: JobManager,
        job_id: str,
        *,
        timeout: float = 30.0,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = jobs.get(job_id)
            if record["state"] in {"succeeded", "failed", "canceled", "orphaned"}:
                return record
            time.sleep(0.025)
        self.fail(f"job {job_id} did not finish")

    def _workspace(self, store: StudioStore, root: Path) -> None:
        world = root / "world"
        create_world_project(
            world,
            world_id="external_execution_world",
            title="External Execution",
            language="en",
        )
        WorkspaceManager(store).register(
            {
                "workspace_id": "workspace_01",
                "forge_root": str(FORGE_ROOT),
                "world_root": str(world),
            }
        )

    @unittest.skipUnless(
        os.name in {"posix", "nt"},
        "external publication supports Linux and Windows",
    )
    def test_materialize_package_extract_jobs_preserve_exact_hash_lineage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-studio-external-e2e-") as directory:
            root = Path(directory)
            data_dir = root / "studio"
            external = root / "external"
            external.mkdir()
            with (
                _ready_materialization("abstract-puzzle", external) as source,
                StudioStore(data_dir) as store,
            ):
                self._workspace(store, root)
                grants = ExternalGrantManager(store)
                jobs = JobManager(store)

                source_grant = grants.create(
                    {
                        "grant_id": "materialization_source",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "source",
                        "artifact_kind": "game_materialization_bundle",
                        "display_name": "Materialization",
                        "path": str(source.root),
                        "expected_content_hash": source.manifest["content_hash"],
                    }
                )
                game_path = external / "standalone-output"
                target_grant = grants.create(
                    {
                        "grant_id": "materialization_target",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "target",
                        "artifact_kind": "standalone_game",
                        "display_name": "Standalone output",
                        "path": str(game_path),
                        "expected_content_hash": None,
                    }
                )
                source_info = path_file_stat(source.root)
                parent_info = path_file_stat(game_path.parent)
                with self.assertRaisesRegex(
                    ExternalJobExecutionError,
                    "^source_changed:",
                ):
                    execute_external_operation(
                        operation="game.materialize",
                        source=source.root,
                        target=game_path,
                        expected_hash=source.manifest["content_hash"],
                        target_grant_id=target_grant["grant_id"],
                        expected_source_identity=(
                            source_info.st_dev,
                            source_info.st_ino + 1,
                        ),
                        expected_parent_identity=(
                            parent_info.st_dev,
                            parent_info.st_ino,
                        ),
                    )
                self.assertFalse(game_path.exists())
                with self.assertRaisesRegex(
                    ExternalJobExecutionError,
                    "^target_changed:",
                ):
                    execute_external_operation(
                        operation="game.materialize",
                        source=source.root,
                        target=game_path,
                        expected_hash=source.manifest["content_hash"],
                        target_grant_id=target_grant["grant_id"],
                        expected_source_identity=(
                            source_info.st_dev,
                            source_info.st_ino,
                        ),
                        expected_parent_identity=(
                            parent_info.st_dev,
                            parent_info.st_ino + 1,
                        ),
                    )
                self.assertFalse(game_path.exists())
                materialize_job = jobs.create(
                    {
                        "job_id": "materialize_job",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "input": {
                            "source_grant_id": source_grant["grant_id"],
                            "target_grant_id": target_grant["grant_id"],
                            "expected_materialization_hash": source.manifest["content_hash"],
                        },
                    }
                )
                scheduler = JobScheduler(data_dir, timeout_seconds=30)
                scheduler.start()
                try:
                    scheduler.notify()
                    materialized = self._wait(jobs, materialize_job["job_id"])
                    self.assertEqual("succeeded", materialized["state"])
                    result = materialized["result"]
                    self.assertEqual("game.materialize", result["operation"])
                    self.assertEqual(
                        source.manifest["lineage"]["runtime_bundle_hash"],
                        result["runtime_bundle_hash"],
                    )
                    self.assertEqual(
                        "consumed",
                        grants.get("materialization_target")["state"],
                    )
                    self.assertFalse(_contains_path(materialized, game_path))

                    package_path = external / "abstract-puzzle.wfgame"
                    package_source = grants.create(
                        {
                            "grant_id": "package_source",
                            "workspace_id": "workspace_01",
                            "operation": "game.package",
                            "role": "source",
                            "artifact_kind": "standalone_game",
                            "display_name": "Standalone package source",
                            "path": str(game_path),
                            "expected_content_hash": result["standalone_hash"],
                        }
                    )
                    package_target = grants.create(
                        {
                            "grant_id": "package_target",
                            "workspace_id": "workspace_01",
                            "operation": "game.package",
                            "role": "target",
                            "artifact_kind": "game_package",
                            "display_name": "Game package",
                            "path": str(package_path),
                            "expected_content_hash": None,
                        }
                    )
                    package_job = jobs.create(
                        {
                            "job_id": "package_job",
                            "workspace_id": "workspace_01",
                            "operation": "game.package",
                            "input": {
                                "source_grant_id": package_source["grant_id"],
                                "target_grant_id": package_target["grant_id"],
                                "expected_game_hash": result["standalone_hash"],
                            },
                        }
                    )
                    scheduler.notify()
                    packaged = self._wait(jobs, package_job["job_id"])
                    self.assertEqual("succeeded", packaged["state"])
                    self.assertEqual(
                        result["standalone_hash"],
                        packaged["result"]["game_hash"],
                    )

                    extracted_path = external / "extracted-output"
                    extract_source = grants.create(
                        {
                            "grant_id": "extract_source",
                            "workspace_id": "workspace_01",
                            "operation": "game.package.extract",
                            "role": "source",
                            "artifact_kind": "game_package",
                            "display_name": "Package extraction source",
                            "path": str(package_path),
                            "expected_content_hash": packaged["result"]["content_hash"],
                        }
                    )
                    extract_target = grants.create(
                        {
                            "grant_id": "extract_target",
                            "workspace_id": "workspace_01",
                            "operation": "game.package.extract",
                            "role": "target",
                            "artifact_kind": "standalone_game",
                            "display_name": "Extracted game",
                            "path": str(extracted_path),
                            "expected_content_hash": None,
                        }
                    )
                    extract_job = jobs.create(
                        {
                            "job_id": "extract_job",
                            "workspace_id": "workspace_01",
                            "operation": "game.package.extract",
                            "input": {
                                "source_grant_id": extract_source["grant_id"],
                                "target_grant_id": extract_target["grant_id"],
                                "expected_package_hash": packaged["result"]["content_hash"],
                            },
                        }
                    )
                    scheduler.notify()
                    extracted = self._wait(jobs, extract_job["job_id"])
                    self.assertEqual("succeeded", extracted["state"])
                    self.assertEqual(
                        packaged["result"]["content_hash"],
                        extracted["result"]["package_hash"],
                    )
                    self.assertEqual(
                        packaged["result"]["game_hash"],
                        extracted["result"]["game_hash"],
                    )
                    self.assertEqual(
                        result["payload_lock_hash"],
                        extracted["result"]["payload_lock_hash"],
                    )
                    self.assertFalse(_contains_path(extracted, extracted_path))
                    package_info = path_file_stat(package_path)
                    completed = execute_external_operation(
                        operation="game.package.extract",
                        source=package_path,
                        target=extracted_path,
                        expected_hash=packaged["result"]["content_hash"],
                        target_grant_id=extract_target["grant_id"],
                        expected_source_identity=(
                            package_info.st_dev,
                            package_info.st_ino,
                        ),
                        expected_parent_identity=(
                            parent_info.st_dev,
                            parent_info.st_ino,
                        ),
                    )
                    self.assertEqual(extracted["result"], completed)
                    self.assertFalse(
                        any(
                            _contains_path(event, external)
                            for event in store.list_events(limit=1000)
                        )
                    )
                finally:
                    scheduler.shutdown()

    def test_recovery_resume_uses_trusted_verification_and_ambiguous_preserves_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-studio-external-recovery-") as directory:
            root = Path(directory)
            data_dir = root / "studio"
            external = root / "external"
            external.mkdir()
            with (
                _ready_materialization("abstract-puzzle", external) as source,
                StudioStore(data_dir) as store,
            ):
                self._workspace(store, root)
                grants = ExternalGrantManager(store)
                jobs = JobManager(store)
                grants.create(
                    {
                        "grant_id": "source_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "source",
                        "artifact_kind": "game_materialization_bundle",
                        "display_name": "Source",
                        "path": str(source.root),
                        "expected_content_hash": source.manifest["content_hash"],
                    }
                )
                target = external / "recovered-game"
                grants.create(
                    {
                        "grant_id": "target_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "target",
                        "artifact_kind": "standalone_game",
                        "display_name": "Target",
                        "path": str(target),
                        "expected_content_hash": None,
                    }
                )
                job = jobs.create(
                    {
                        "job_id": "recover_job",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "input": {
                            "source_grant_id": "source_grant",
                            "target_grant_id": "target_grant",
                            "expected_materialization_hash": source.manifest["content_hash"],
                        },
                    }
                )
                self.assertEqual("running", jobs.claim_next()["state"])
                verified = materialize_game(
                    source.root,
                    target,
                    expected_content_hash=source.manifest["content_hash"],
                )
                expected_hash = verified.manifest["content_hash"]
                verified.close()
                jobs.finish(job["job_id"], "orphaned", reason="worker_death")
                resumed = jobs.recover(job["job_id"], "resume")
                self.assertEqual("succeeded", resumed["state"])
                self.assertEqual(expected_hash, resumed["result"]["standalone_hash"])

                foreign_target = external / "foreign-target"
                grants.create(
                    {
                        "grant_id": "foreign_source_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "source",
                        "artifact_kind": "game_materialization_bundle",
                        "display_name": "Foreign source",
                        "path": str(source.root),
                        "expected_content_hash": source.manifest["content_hash"],
                    }
                )
                grants.create(
                    {
                        "grant_id": "foreign_target_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "target",
                        "artifact_kind": "standalone_game",
                        "display_name": "Foreign target",
                        "path": str(foreign_target),
                        "expected_content_hash": None,
                    }
                )
                foreign_job = jobs.create(
                    {
                        "job_id": "foreign_job",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "input": {
                            "source_grant_id": "foreign_source_grant",
                            "target_grant_id": "foreign_target_grant",
                            "expected_materialization_hash": source.manifest["content_hash"],
                        },
                    }
                )
                self.assertEqual("running", jobs.claim_next()["state"])
                foreign_target.mkdir()
                marker = foreign_target / "foreign.txt"
                marker.write_text("preserve", encoding="utf-8")
                jobs.finish(foreign_job["job_id"], "orphaned", reason="worker_death")
                with self.assertRaises(StudioError) as raised:
                    jobs.recover(foreign_job["job_id"], "rollback")
                self.assertEqual("recovery_ambiguous", raised.exception.code)
                self.assertEqual("preserve", marker.read_text(encoding="utf-8"))
                self.assertNotIn(str(foreign_target), raised.exception.message)

    def test_visible_targets_finalize_matching_journals_before_resume(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-studio-external-journal-") as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            with _ready_materialization("abstract-puzzle", external) as source:
                parent_info = path_file_stat(external)
                source_info = path_file_stat(source.root)
                materialized_target = external / "materialized"

                def interrupt_materialization(stage: str, _path: Path | None) -> None:
                    if stage == "before_journal_remove":
                        raise RuntimeError("interrupt materialization finalization")

                with self.assertRaisesRegex(
                    StandaloneGameError,
                    "^standalone_game_publication_failed:",
                ):
                    materialize_game(
                        source.root,
                        materialized_target,
                        expected_content_hash=source.manifest["content_hash"],
                        _publication_hook=interrupt_materialization,
                    )
                materialization_journal = external / ".materialized.standalone-game.journal.json"
                self.assertTrue(materialized_target.is_dir())
                self.assertTrue(materialization_journal.is_file())

                materialized = execute_external_operation(
                    operation="game.materialize",
                    source=source.root,
                    target=materialized_target,
                    expected_hash=source.manifest["content_hash"],
                    target_grant_id="materialized_target",
                    expected_source_identity=(
                        source_info.st_dev,
                        source_info.st_ino,
                    ),
                    expected_parent_identity=(
                        parent_info.st_dev,
                        parent_info.st_ino,
                    ),
                )
                self.assertFalse(materialization_journal.exists())

                package_path = external / "materialized.wfgame"
                package = package_game(materialized_target, package_path)
                try:
                    package_hash = package.manifest["content_hash"]
                finally:
                    package.close()
                package_info = path_file_stat(package_path)
                extracted_target = external / "extracted"

                def interrupt_extraction(stage: str, _path: Path | None) -> None:
                    if stage == "before_journal_remove":
                        raise RuntimeError("interrupt extraction finalization")

                with self.assertRaisesRegex(
                    WorldForgeGamePackageError,
                    "^game_package_publication_failed:",
                ):
                    extract_game_package(
                        package_path,
                        extracted_target,
                        _publication_hook=interrupt_extraction,
                    )
                extraction_journal = external / ".extracted.game-package-extraction.journal.json"
                self.assertTrue(extracted_target.is_dir())
                self.assertTrue(extraction_journal.is_file())

                extracted = execute_external_operation(
                    operation="game.package.extract",
                    source=package_path,
                    target=extracted_target,
                    expected_hash=package_hash,
                    target_grant_id="extracted_target",
                    expected_source_identity=(
                        package_info.st_dev,
                        package_info.st_ino,
                    ),
                    expected_parent_identity=(
                        parent_info.st_dev,
                        parent_info.st_ino,
                    ),
                )
                self.assertFalse(extraction_journal.exists())
                self.assertEqual(
                    materialized["standalone_hash"],
                    extracted["game_hash"],
                )

    def test_external_worker_output_overflow_requires_explicit_recovery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-studio-external-overflow-") as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            with StudioStore(root / "studio") as store:
                self._workspace(store, root)
                source = external / "source"
                source.mkdir()
                target = external / "target"
                grants = ExternalGrantManager(store)
                grants.create(
                    {
                        "grant_id": "source_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "source",
                        "artifact_kind": "game_materialization_bundle",
                        "display_name": "Source",
                        "path": str(source),
                        "expected_content_hash": _HASH_A,
                    }
                )
                grants.create(
                    {
                        "grant_id": "target_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "target",
                        "artifact_kind": "standalone_game",
                        "display_name": "Target",
                        "path": str(target),
                        "expected_content_hash": None,
                    }
                )
                jobs = JobManager(store)
                job = jobs.create(
                    {
                        "job_id": "overflow_job",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "input": {
                            "source_grant_id": "source_grant",
                            "target_grant_id": "target_grant",
                            "expected_materialization_hash": _HASH_A,
                        },
                    }
                )
                command = (
                    sys.executable,
                    "-I",
                    "-c",
                    "import sys;sys.stdin.buffer.read();sys.stdout.buffer.write(b'x'*1048577)",
                )
                with patch("worldforge.studio.executor._worker_command", return_value=command):
                    scheduler = JobScheduler(root / "studio", timeout_seconds=10)
                    scheduler.start()
                    try:
                        scheduler.notify()
                        completed = self._wait(jobs, job["job_id"])
                    finally:
                        scheduler.shutdown()

                self.assertEqual("orphaned", completed["state"])
                self.assertEqual("recovery_required", completed["error"]["code"])
                self.assertEqual(
                    "recovery_required",
                    grants.get("target_grant")["state"],
                )
                row = store.connection.execute(
                    "SELECT reserved_job_id FROM external_grants WHERE grant_id = 'target_grant'"
                ).fetchone()
                self.assertEqual(job["job_id"], row["reserved_job_id"])

    def test_queued_cancel_releases_exact_target_reservation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-studio-external-cancel-") as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            with StudioStore(root / "studio") as store:
                self._workspace(store, root)
                source = external / "source"
                source.mkdir()
                target = external / "target"
                grants = ExternalGrantManager(store)
                grants.create(
                    {
                        "grant_id": "source_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "source",
                        "artifact_kind": "game_materialization_bundle",
                        "display_name": "Source",
                        "path": str(source),
                        "expected_content_hash": _HASH_A,
                    }
                )
                grants.create(
                    {
                        "grant_id": "target_grant",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "target",
                        "artifact_kind": "standalone_game",
                        "display_name": "Target",
                        "path": str(target),
                        "expected_content_hash": None,
                    }
                )
                jobs = JobManager(store)
                job = jobs.create(
                    {
                        "job_id": "cancel_job",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "input": {
                            "source_grant_id": "source_grant",
                            "target_grant_id": "target_grant",
                            "expected_materialization_hash": _HASH_A,
                        },
                    }
                )
                self.assertEqual("canceled", jobs.cancel(job["job_id"])["state"])
                self.assertEqual("ready", grants.get("target_grant")["state"])
                row = store.connection.execute(
                    "SELECT reserved_job_id FROM external_grants WHERE grant_id = 'target_grant'"
                ).fetchone()
                self.assertIsNone(row["reserved_job_id"])

    def test_service_v2_exposes_pathless_external_authority_without_broadening_v1(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-studio-external-service-") as directory:
            root = Path(directory)
            external = root / "external"
            external.mkdir()
            source = external / "source"
            source.mkdir()
            with StudioStore(root / "studio") as store:
                self._workspace(store, root)
                service = StudioService(store)
                v1 = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 1,
                        "kind": "request",
                        "request_id": "initialize_v1",
                        "method": "service.initialize",
                        "params": {},
                    }
                )
                self.assertEqual(1, v1["protocol_version"])
                self.assertEqual(1, v1["result"]["service_version"])
                self.assertNotIn("external_grant.create", v1["result"]["methods"])

                v2 = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 2,
                        "kind": "request",
                        "request_id": "initialize_v2",
                        "method": "service.initialize",
                        "params": {},
                    }
                )
                self.assertEqual(2, v2["protocol_version"])
                self.assertEqual(2, v2["result"]["service_version"])
                self.assertIn("external_grant.create", v2["result"]["methods"])
                self.assertIs(
                    True,
                    v2["result"]["capabilities"]["external_artifact_jobs"],
                )

                created = service.handle(
                    {
                        "protocol": "rpg-world-forge.studio_protocol",
                        "protocol_version": 2,
                        "kind": "request",
                        "request_id": "grant_create",
                        "method": "external_grant.create",
                        "params": {
                            "grant_id": "service_source",
                            "workspace_id": "workspace_01",
                            "operation": "game.materialize",
                            "role": "source",
                            "artifact_kind": "game_materialization_bundle",
                            "display_name": "Service source",
                            "path": str(source),
                            "expected_content_hash": _HASH_A,
                        },
                    }
                )
                self.assertEqual(2, created["protocol_version"])
                self.assertEqual("service_source", created["result"]["grant"]["grant_id"])
                self.assertFalse(_contains_path(created, source))
                service.close()

    def test_service_enforces_job_generation_before_disclosure_or_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wf-studio-generation-gate-") as directory:
            root = Path(directory)
            external_root = root / "external"
            external_root.mkdir()
            source = external_root / "source"
            source.mkdir()
            target = external_root / "target"
            with StudioStore(root / "studio") as store:
                self._workspace(store, root)
                grants = ExternalGrantManager(store)
                grants.create(
                    {
                        "grant_id": "grant_source",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "source",
                        "artifact_kind": "game_materialization_bundle",
                        "display_name": "Source",
                        "path": str(source),
                        "expected_content_hash": _HASH_A,
                    }
                )
                grants.create(
                    {
                        "grant_id": "grant_target",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "role": "target",
                        "artifact_kind": "standalone_game",
                        "display_name": "Target",
                        "path": str(target),
                        "expected_content_hash": None,
                    }
                )
                jobs = JobManager(store)
                external = jobs.create(
                    {
                        "job_id": "external_job",
                        "workspace_id": "workspace_01",
                        "operation": "game.materialize",
                        "input": {
                            "source_grant_id": "grant_source",
                            "target_grant_id": "grant_target",
                            "expected_materialization_hash": _HASH_A,
                        },
                    }
                )
                managed = jobs.create(
                    {
                        "job_id": "managed_job",
                        "workspace_id": "workspace_01",
                        "operation": "runtime.headless",
                        "input": {"worldpack": "build/worldpack.json", "ticks": 0},
                    }
                )
                service = StudioService(store)

                def request(
                    protocol_version: int,
                    request_id: str,
                    method: str,
                    params: dict[str, object],
                ) -> dict[str, object]:
                    return service.handle(
                        {
                            "protocol": "rpg-world-forge.studio_protocol",
                            "protocol_version": protocol_version,
                            "kind": "request",
                            "request_id": request_id,
                            "method": method,
                            "params": params,
                        }
                    )

                try:
                    with self.assertRaises(StudioError) as v1_get:
                        request(
                            1,
                            "v1_get_external",
                            "job.get",
                            {"job_id": external["job_id"]},
                        )
                    self.assertEqual("not_found", v1_get.exception.code)
                    with self.assertRaises(StudioError) as v2_get:
                        request(
                            2,
                            "v2_get_managed",
                            "job.get",
                            {"job_id": managed["job_id"]},
                        )
                    self.assertEqual("not_found", v2_get.exception.code)

                    v1_list = request(1, "v1_list", "job.list", {"limit": 100})
                    v2_list = request(2, "v2_list", "job.list", {"limit": 100})
                    self.assertEqual(
                        [managed["job_id"]],
                        [job["job_id"] for job in v1_list["result"]["jobs"]],
                    )
                    self.assertEqual(
                        [external["job_id"]],
                        [job["job_id"] for job in v2_list["result"]["jobs"]],
                    )

                    with self.assertRaises(StudioError) as v1_cancel:
                        request(
                            1,
                            "v1_cancel_external",
                            "job.cancel",
                            {"job_id": external["job_id"]},
                        )
                    self.assertEqual("not_found", v1_cancel.exception.code)
                    with self.assertRaises(StudioError) as v2_cancel:
                        request(
                            2,
                            "v2_cancel_managed",
                            "job.cancel",
                            {"job_id": managed["job_id"]},
                        )
                    self.assertEqual("not_found", v2_cancel.exception.code)
                    self.assertEqual("queued", jobs.get(external["job_id"])["state"])
                    self.assertEqual("queued", jobs.get(managed["job_id"])["state"])
                    self.assertEqual("reserved", grants.get("grant_target")["state"])
                finally:
                    service.close()


if __name__ == "__main__":
    unittest.main()
