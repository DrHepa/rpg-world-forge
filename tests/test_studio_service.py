from __future__ import annotations

import base64
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.errors import StudioError
from worldforge.studio.jsonio import MAX_NDJSON_LINE_BYTES, decode_ndjson_object
from worldforge.studio.service import StudioService, _close_runtime, serve
from worldforge.studio.storage import StudioStore


class StudioServiceTests(unittest.TestCase):
    def _serve(self, payload: bytes) -> tuple[int, list[dict[str, object]]]:
        with tempfile.TemporaryDirectory() as directory:
            output = io.BytesIO()
            exit_code = serve(io.BytesIO(payload), output, data_dir=Path(directory) / "data")
        envelopes = [json.loads(line) for line in output.getvalue().splitlines()]
        return exit_code, envelopes

    def test_initializes_and_correlates_errors_without_tracebacks(self) -> None:
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "request",
            "request_id": "request-1",
            "method": "service.initialize",
            "params": {},
        }
        exit_code, responses = self._serve(
            (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        )

        self.assertEqual(0, exit_code)
        self.assertEqual("response", responses[0]["kind"])
        self.assertEqual("request-1", responses[0]["request_id"])
        self.assertEqual("service.initialize", responses[0]["method"])
        self.assertEqual(1, responses[0]["result"]["protocol_version"])

        bad_method = {**request, "request_id": "request-2", "method": "provider.execute"}
        _, responses = self._serve(
            (json.dumps(bad_method, separators=(",", ":")) + "\n").encode("utf-8")
        )
        self.assertEqual("error", responses[0]["kind"])
        self.assertEqual("request-2", responses[0]["request_id"])
        self.assertEqual("invalid_request", responses[0]["error"]["code"])
        self.assertNotIn("Traceback", json.dumps(responses[0]))

        for field, value in (("kind", []), ("method", []), ("method", {})):
            malformed = {**request, "request_id": f"bad-{field}", field: value}
            _, responses = self._serve(
                (json.dumps(malformed, separators=(",", ":")) + "\n").encode("utf-8")
            )
            self.assertEqual("invalid_request", responses[0]["error"]["code"])

    def test_service_close_is_idempotent_and_rejects_post_close_requests(self) -> None:
        request = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "request",
            "request_id": "closed",
            "method": "service.initialize",
            "params": {},
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            StudioStore(Path(directory) / "data") as store,
        ):
            service = StudioService(store)
            service.close()
            service.close()
            with self.assertRaises(StudioError) as raised:
                service.handle(request)
        self.assertEqual("invalid_state", raised.exception.code)

    def test_preview_methods_are_named_closed_and_strip_raw_bytes_from_protocol(self) -> None:
        revision = "a" * 64
        entry_id = "asset_" + ("b" * 64)
        handle = "C" * 43
        payload = b"\x89PNG"
        digest = hashlib.sha256(payload).hexdigest()

        class PreviewManager:
            def __init__(self, _catalog: object) -> None:
                self.calls: list[tuple[object, ...]] = []
                self.shutdown_calls = 0

            def open(self, *args: object) -> dict[str, object]:
                self.calls.append(("open", *args))
                return {
                    "handle": handle,
                    "manifest_revision": revision,
                    "entry_id": entry_id,
                    "media_type": "image/png",
                    "byte_length": len(payload),
                    "sha256": digest,
                }

            def read(self, *args: object) -> dict[str, object]:
                self.calls.append(("read", *args))
                return {
                    "handle": handle,
                    "sequence": 0,
                    "payload": payload,
                    "cumulative_bytes": len(payload),
                    "cumulative_sha256": digest,
                    "eof": True,
                }

            def close(self, *args: object) -> None:
                self.calls.append(("close", *args))

            def shutdown(self) -> None:
                self.shutdown_calls += 1

        manager = PreviewManager(object())
        with (
            tempfile.TemporaryDirectory() as directory,
            StudioStore(Path(directory) / "data") as store,
            patch(
                "worldforge.studio.service.AssetPreviewManager",
                return_value=manager,
            ) as factory,
        ):
            service = StudioService(store)
            base = {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": 1,
                "kind": "request",
            }
            opened = service.handle(
                {
                    **base,
                    "request_id": "preview-open",
                    "method": "asset.preview.open",
                    "params": {
                        "workspace_id": "workspace_01",
                        "manifest_revision": revision,
                        "entry_id": entry_id,
                    },
                }
            )
            read = service.handle(
                {
                    **base,
                    "request_id": "preview-read",
                    "method": "asset.preview.read",
                    "params": {"handle": handle, "sequence": 0},
                }
            )
            closed = service.handle(
                {
                    **base,
                    "request_id": "preview-close",
                    "method": "asset.preview.close",
                    "params": {"handle": handle},
                }
            )
            service.close()
            service.close()

        factory.assert_called_once_with(service.assets)
        self.assertEqual(65_536, opened["result"]["chunk_bytes"])
        self.assertNotIn("path", opened["result"])
        self.assertEqual(
            {
                "handle",
                "sequence",
                "data_base64",
                "byte_length",
                "cumulative_bytes",
                "cumulative_sha256",
                "eof",
            },
            set(read["result"]),
        )
        self.assertEqual(payload, base64.b64decode(read["result"]["data_base64"], validate=True))
        self.assertNotIn("payload", read["result"])
        self.assertEqual({"handle": handle, "closed": True}, closed["result"])
        self.assertEqual(
            [
                ("open", "workspace_01", revision, entry_id),
                ("read", handle, 0),
                ("close", handle),
            ],
            manager.calls,
        )
        self.assertEqual(1, manager.shutdown_calls)

    def test_preview_shutdown_is_first_retryable_stage_and_runtime_attempts_every_stage(
        self,
    ) -> None:
        events: list[str] = []

        class PreviewManager:
            def __init__(self) -> None:
                self.shutdown_calls = 0

            def shutdown(self) -> None:
                self.shutdown_calls += 1
                events.append("preview.shutdown")
                if self.shutdown_calls == 1:
                    raise StudioError("internal_error", "preview cleanup failed")

        class Scheduler:
            def shutdown(self) -> None:
                events.append("scheduler.shutdown")

        class Store:
            def close(self) -> None:
                events.append("store.close")

        manager = PreviewManager()
        service = object.__new__(StudioService)
        service._closed = False
        service._preview_shutdown = False
        service.asset_previews = manager
        first_error = _close_runtime(service, Scheduler(), Store())
        self.assertIsNotNone(first_error)
        assert first_error is not None
        self.assertEqual("preview cleanup failed", first_error.message)
        self.assertEqual(
            ["preview.shutdown", "scheduler.shutdown", "store.close"],
            events,
        )
        service.close()
        service.close()
        self.assertEqual(2, manager.shutdown_calls)

    def test_service_constructor_rolls_back_preview_manager_after_later_failure(self) -> None:
        events: list[str] = []

        class PreviewManager:
            def __init__(self, _catalog: object) -> None:
                events.append("preview.open")

            def shutdown(self) -> None:
                events.append("preview.shutdown")

        with (
            tempfile.TemporaryDirectory() as directory,
            StudioStore(Path(directory) / "data") as store,
            patch("worldforge.studio.service.AssetPreviewManager", PreviewManager),
            patch(
                "worldforge.studio.service.AuthoringManager",
                side_effect=StudioError("internal_error", "later startup failure"),
            ),
            self.assertRaisesRegex(StudioError, "later startup failure"),
        ):
            StudioService(store)
        self.assertEqual(["preview.open", "preview.shutdown"], events)

    def test_eof_closes_service_scheduler_and_store_once_in_reverse_order(self) -> None:
        events: list[str] = []

        class Store:
            def __init__(self, _data_dir: object) -> None:
                events.append("store.open")

            def close(self) -> None:
                events.append("store.close")

        class Scheduler:
            def __init__(self, _data_dir: object) -> None:
                events.append("scheduler.open")

            def start(self) -> None:
                events.append("scheduler.start")

            def shutdown(self) -> None:
                events.append("scheduler.close")

        class Service:
            def __init__(self, _store: object, _scheduler: object) -> None:
                events.append("service.open")

            def close(self) -> None:
                events.append("service.close")

        output = io.BytesIO()
        with (
            patch("worldforge.studio.service.StudioStore", Store),
            patch("worldforge.studio.service.JobScheduler", Scheduler),
            patch("worldforge.studio.service.StudioService", Service),
        ):
            exit_code = serve(io.BytesIO(), output, data_dir="unused")
        self.assertEqual(0, exit_code)
        self.assertEqual(b"", output.getvalue())
        self.assertEqual(
            [
                "store.open",
                "scheduler.open",
                "scheduler.start",
                "service.open",
                "service.close",
                "scheduler.close",
                "store.close",
            ],
            events,
        )

    def test_shutdown_attempts_every_stage_and_preserves_first_error(self) -> None:
        events: list[str] = []

        class Store:
            def __init__(self, _data_dir: object) -> None:
                pass

            def close(self) -> None:
                events.append("store.close")
                raise OSError("private store detail")

        class Scheduler:
            def __init__(self, _data_dir: object) -> None:
                pass

            def start(self) -> None:
                pass

            def shutdown(self) -> None:
                events.append("scheduler.close")
                raise StudioError("internal_error", "scheduler close failed")

        class Service:
            def __init__(self, _store: object, _scheduler: object) -> None:
                pass

            def close(self) -> None:
                events.append("service.close")
                raise StudioError("conflict", "service close failed")

        output = io.BytesIO()
        with (
            patch("worldforge.studio.service.StudioStore", Store),
            patch("worldforge.studio.service.JobScheduler", Scheduler),
            patch("worldforge.studio.service.StudioService", Service),
        ):
            exit_code = serve(io.BytesIO(), output, data_dir="unused")
        response = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertEqual(["service.close", "scheduler.close", "store.close"], events)
        self.assertEqual("conflict", response["error"]["code"])
        self.assertEqual("service close failed", response["error"]["message"])
        self.assertNotIn("private store detail", json.dumps(response))

    def test_startup_failure_rolls_back_started_scheduler_and_store(self) -> None:
        events: list[str] = []

        class Store:
            def __init__(self, _data_dir: object) -> None:
                events.append("store.open")

            def close(self) -> None:
                events.append("store.close")

        class Scheduler:
            def __init__(self, _data_dir: object) -> None:
                events.append("scheduler.open")

            def start(self) -> None:
                events.append("scheduler.start")

            def shutdown(self) -> None:
                events.append("scheduler.close")

        class Service:
            def __init__(self, _store: object, _scheduler: object) -> None:
                events.append("service.open")
                raise StudioError("internal_error", "service startup failed")

        output = io.BytesIO()
        with (
            patch("worldforge.studio.service.StudioStore", Store),
            patch("worldforge.studio.service.JobScheduler", Scheduler),
            patch("worldforge.studio.service.StudioService", Service),
        ):
            exit_code = serve(io.BytesIO(), output, data_dir="unused")
        self.assertEqual(1, exit_code)
        self.assertEqual(
            [
                "store.open",
                "scheduler.open",
                "scheduler.start",
                "service.open",
                "scheduler.close",
                "store.close",
            ],
            events,
        )

    def test_malformed_ndjson_is_rejected_and_stream_continues(self) -> None:
        self.assertEqual(1.25, decode_ndjson_object(b'{"value":1.25}')["value"])
        valid = (
            b'{"protocol":"rpg-world-forge.studio_protocol","protocol_version":1,'
            b'"kind":"request","request_id":"ok","method":"service.initialize",'
            b'"params":{}}\n'
        )
        cases = (
            b'{"kind":"request","kind":"request"}\n',
            b'{"value":NaN}\n',
            b'{"value":1e9999}\n',
            b"[]\n",
            b'{"bad":"\xff"}\n',
            b'{"unterminated":true\n',
            (b"x" * (MAX_NDJSON_LINE_BYTES + 1)) + b"\n",
        )
        payload = b"".join(case + valid for case in cases)
        exit_code, responses = self._serve(payload)

        self.assertEqual(0, exit_code)
        self.assertEqual(len(cases) * 2, len(responses))
        for index in range(0, len(responses), 2):
            self.assertEqual("error", responses[index]["kind"])
            self.assertEqual("invalid_request", responses[index]["error"]["code"])
            self.assertEqual("response", responses[index + 1]["kind"])

    def test_list_filters_reject_non_string_membership_values(self) -> None:
        base = {
            "protocol": "rpg-world-forge.studio_protocol",
            "protocol_version": 1,
            "kind": "request",
            "request_id": "request-list",
        }
        for method, field in (("changeset.list", "status"), ("job.list", "state")):
            for value in ([], {}):
                with self.subTest(method=method, field=field, value=value):
                    request = {**base, "method": method, "params": {field: value}}
                    _, responses = self._serve(
                        (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
                    )
                    self.assertEqual("error", responses[0]["kind"])
                    self.assertEqual("invalid_request", responses[0]["error"]["code"])
                    self.assertNotIn("Traceback", json.dumps(responses[0]))

    def test_mutation_enums_reject_arrays_and_objects_without_internal_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world = temp / "world"
            create_world_project(world, world_id="studio_world", title="Studio", language="en")
            base = {
                "protocol": "rpg-world-forge.studio_protocol",
                "protocol_version": 1,
                "kind": "request",
            }
            requests = [
                {
                    **base,
                    "request_id": "register",
                    "method": "workspace.register",
                    "params": {
                        "workspace_id": "workspace_01",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    },
                }
            ]
            for index, value in enumerate(([], {})):
                requests.append(
                    {
                        **base,
                        "request_id": f"changeset-{index}",
                        "method": "changeset.create",
                        "params": {
                            "workspace_id": "workspace_01",
                            "operations": [
                                {
                                    "path": "source/new.txt",
                                    "operation": value,
                                    "content": "new\n",
                                }
                            ],
                        },
                    }
                )
            requests.append(
                {
                    **base,
                    "request_id": "job-create",
                    "method": "job.create",
                    "params": {
                        "job_id": "job_01",
                        "workspace_id": "workspace_01",
                        "operation": "runtime.headless",
                        "input": {"worldpack": "build/missing-worldpack.json", "ticks": 0},
                    },
                }
            )
            for index, value in enumerate(([], {})):
                requests.append(
                    {
                        **base,
                        "request_id": f"transition-{index}",
                        "method": "job.transition",
                        "params": {"job_id": "job_01", "state": value},
                    }
                )
            payload = b"".join(
                (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
                for request in requests
            )
            output = io.BytesIO()
            exit_code = serve(io.BytesIO(payload), output, data_dir=temp / "data")
            responses = [json.loads(line) for line in output.getvalue().splitlines()]

            self.assertEqual(0, exit_code)
            self.assertEqual("response", responses[0]["kind"])
            self.assertEqual("response", responses[3]["kind"])
            for index in (1, 2, 4, 5):
                self.assertEqual("error", responses[index]["kind"])
                self.assertEqual("invalid_request", responses[index]["error"]["code"])
                self.assertNotIn("Traceback", json.dumps(responses[index]))

    def test_changeset_actions_forward_the_reviewed_v2_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world = temp / "world"
            create_world_project(world, world_id="studio_world", title="Studio", language="en")
            with StudioStore(temp / "data") as store:
                service = StudioService(store)

                def request(
                    request_id: str, method: str, params: dict[str, object]
                ) -> dict[str, object]:
                    return service.handle(
                        {
                            "protocol": "rpg-world-forge.studio_protocol",
                            "protocol_version": 1,
                            "kind": "request",
                            "request_id": request_id,
                            "method": method,
                            "params": params,
                        }
                    )

                request(
                    "register",
                    "workspace.register",
                    {
                        "workspace_id": "workspace_01",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    },
                )
                created = request(
                    "create",
                    "changeset.create",
                    {
                        "workspace_id": "workspace_01",
                        "operations": [
                            {
                                "path": "source/new.txt",
                                "operation": "create",
                                "content": "new\n",
                            }
                        ],
                    },
                )["result"]["changeset"]
                with self.assertRaisesRegex(StudioError, "expected_review_sha256"):
                    request(
                        "missing-review",
                        "changeset.approve",
                        {"changeset_id": created["changeset_id"]},
                    )
                approved = request(
                    "approve",
                    "changeset.approve",
                    {
                        "changeset_id": created["changeset_id"],
                        "expected_review_sha256": created["review_sha256"],
                    },
                )["result"]["changeset"]
                self.assertEqual("approved", approved["status"])

    def test_changeset_stage_verifies_base_and_exposes_exact_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            world = temp / "world"
            create_world_project(world, world_id="studio_world", title="Studio", language="en")
            source_path = world / "source/world.json"
            base_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            with StudioStore(temp / "data") as store:
                service = StudioService(store)

                def request(
                    request_id: str, method: str, params: dict[str, object]
                ) -> dict[str, object]:
                    return service.handle(
                        {
                            "protocol": "rpg-world-forge.studio_protocol",
                            "protocol_version": 1,
                            "kind": "request",
                            "request_id": request_id,
                            "method": method,
                            "params": params,
                        }
                    )

                request(
                    "register",
                    "workspace.register",
                    {
                        "workspace_id": "workspace_01",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(world),
                    },
                )
                with self.assertRaisesRegex(StudioError, "base changed") as mismatch:
                    request(
                        "mismatch",
                        "changeset.create",
                        {
                            "workspace_id": "workspace_01",
                            "operations": [
                                {
                                    "path": "source/world.json",
                                    "operation": "replace",
                                    "expected_base_sha256": "0" * 64,
                                    "content": "{}\n",
                                }
                            ],
                        },
                    )
                self.assertEqual("conflict", mismatch.exception.code)

                staged = request(
                    "stage",
                    "changeset.create",
                    {
                        "workspace_id": "workspace_01",
                        "operations": [
                            {
                                "path": "source/world.json",
                                "operation": "replace",
                                "expected_base_sha256": base_sha256,
                                "content": "{}\n",
                            }
                        ],
                    },
                )["result"]["changeset"]
                self.assertEqual(base_sha256, staged["operations"][0]["base_sha256"])
                diff = request(
                    "diff",
                    "changeset.diff",
                    {"changeset_id": staged["changeset_id"]},
                )["result"]["diff"]
                self.assertTrue(diff["available"])
                self.assertEqual(staged["changeset_id"], diff["changeset_id"])
                self.assertEqual(staged["review_sha256"], diff["review_sha256"])
                self.assertEqual("replace", diff["operations"][0]["operation"])


if __name__ == "__main__":
    unittest.main()
