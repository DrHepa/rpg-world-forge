from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.jsonio import MAX_NDJSON_LINE_BYTES, decode_ndjson_object
from worldforge.studio.service import serve


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
                        "operation": "forge.validate",
                        "input": {},
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


if __name__ == "__main__":
    unittest.main()
