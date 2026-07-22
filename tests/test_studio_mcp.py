from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worldforge.repository_boundary import FORGE_ROOT
from worldforge.scaffold import create_world_project
from worldforge.studio.changesets import (
    MAX_CHANGE_FILE_BYTES,
    MAX_CHANGESET_OPERATIONS,
    ChangesetManager,
)
from worldforge.studio.mcp_server import MAX_JSON_DEPTH, MAX_MCP_MESSAGE_BYTES, serve
from worldforge.studio.storage import StudioStore
from worldforge.studio.workspaces import WorkspaceManager


def _message(method: str, params: object, request_id: int | None = None) -> bytes:
    value: dict[str, object] = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        value["id"] = request_id
    return (json.dumps(value, separators=(",", ":")) + "\n").encode()


def _initialize_request(request_id: int = 1) -> bytes:
    return _message(
        "initialize",
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {"roots": {"listChanged": False}},
            "clientInfo": {"name": "fake-codex", "version": "0.144.6"},
        },
        request_id,
    )


def _initialized() -> bytes:
    return _message("notifications/initialized", {})


def _initialize(request_id: int = 1) -> bytes:
    return _initialize_request(request_id) + _initialized()


class ForgeMcpTests(unittest.TestCase):
    def _workspace(self, root: Path, workspace_id: str) -> tuple[Path, Path]:
        world = root / workspace_id
        create_world_project(world, world_id=f"{workspace_id}_world", title="MCP", language="en")
        return world, root / "studio-data"

    def _register(self, data_dir: Path, world: Path, workspace_id: str) -> None:
        with StudioStore(data_dir) as store:
            WorkspaceManager(store).register(
                {
                    "workspace_id": workspace_id,
                    "forge_root": str(FORGE_ROOT),
                    "world_root": str(world),
                }
            )

    def _serve(
        self, payload: bytes, *, data_dir: Path, workspace_id: str
    ) -> tuple[int, list[dict[str, object]]]:
        output = io.BytesIO()
        code = serve(io.BytesIO(payload), output, data_dir=data_dir, workspace_id=workspace_id)
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_fake_client_lists_only_three_tools_and_stages_without_applying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world, data_dir = self._workspace(root, "workspace_01")
            self._register(data_dir, world, "workspace_01")
            payload = b"".join(
                [
                    _initialize(),
                    _message("tools/list", {}, 2),
                    _message(
                        "tools/call",
                        {
                            "name": "forge_stage_changeset",
                            "arguments": {
                                "changeset_id": "mcp_change_01",
                                "operations": [
                                    {
                                        "path": "source/mcp-note.txt",
                                        "operation": "create",
                                        "content": "staged only\n",
                                    }
                                ],
                            },
                        },
                        3,
                    ),
                    _message(
                        "tools/call",
                        {
                            "name": "forge_get_changeset",
                            "arguments": {"changeset_id": "mcp_change_01"},
                        },
                        4,
                    ),
                    _message(
                        "tools/call",
                        {"name": "forge_list_changesets", "arguments": {"limit": 10}},
                        5,
                    ),
                    _message(
                        "tools/call",
                        {"name": "changeset.apply", "arguments": {}},
                        6,
                    ),
                ]
            )

            code, responses = self._serve(payload, data_dir=data_dir, workspace_id="workspace_01")

            self.assertEqual(0, code)
            self.assertEqual("2025-11-25", responses[0]["result"]["protocolVersion"])
            names = [tool["name"] for tool in responses[1]["result"]["tools"]]
            self.assertEqual(
                [
                    "forge_stage_changeset",
                    "forge_get_changeset",
                    "forge_list_changesets",
                ],
                names,
            )
            staged = responses[2]["result"]["structuredContent"]["changeset"]
            self.assertEqual("staged", staged["status"])
            self.assertEqual("workspace_01", staged["workspace_id"])
            self.assertEqual(
                "mcp_change_01",
                responses[3]["result"]["structuredContent"]["changeset"]["changeset_id"],
            )
            self.assertEqual(1, len(responses[4]["result"]["structuredContent"]["changesets"]))
            self.assertTrue(responses[5]["result"]["isError"])
            self.assertFalse((world / "source/mcp-note.txt").exists())

    def test_bound_workspace_cannot_read_another_workspace_changeset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, data_dir = self._workspace(root, "workspace_01")
            second, _ = self._workspace(root, "workspace_02")
            self._register(data_dir, first, "workspace_01")
            with StudioStore(data_dir) as store:
                WorkspaceManager(store).register(
                    {
                        "workspace_id": "workspace_02",
                        "forge_root": str(FORGE_ROOT),
                        "world_root": str(second),
                    }
                )
                ChangesetManager(store).create(
                    {
                        "changeset_id": "other_change",
                        "workspace_id": "workspace_02",
                        "operations": [
                            {
                                "path": "source/other.txt",
                                "operation": "create",
                                "content": "other\n",
                            }
                        ],
                    }
                )
            payload = _initialize() + _message(
                "tools/call",
                {
                    "name": "forge_get_changeset",
                    "arguments": {"changeset_id": "other_change"},
                },
                2,
            )

            _, responses = self._serve(payload, data_dir=data_dir, workspace_id="workspace_01")

            error = responses[1]["result"]["structuredContent"]["error"]
            self.assertEqual("not_found", error["code"])
            self.assertNotIn(str(second), json.dumps(responses))

    def test_secondary_attachment_never_runs_changeset_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world, data_dir = self._workspace(root, "workspace_01")
            self._register(data_dir, world, "workspace_01")

            with mock.patch.object(
                ChangesetManager,
                "recover_journals",
                side_effect=AssertionError("secondary attachment attempted recovery"),
            ):
                code, responses = self._serve(
                    _initialize(), data_dir=data_dir, workspace_id="workspace_01"
                )

            self.assertEqual(0, code)
            self.assertEqual(1, len(responses))

    def test_tools_are_unavailable_until_initialized_notification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world, data_dir = self._workspace(root, "workspace_01")
            self._register(data_dir, world, "workspace_01")

            _, responses = self._serve(
                _initialize_request()
                + _message("tools/list", {}, 2)
                + _initialized()
                + _message("tools/list", {}, 3),
                data_dir=data_dir,
                workspace_id="workspace_01",
            )

            self.assertEqual(3, len(responses))
            self.assertEqual("2025-11-25", responses[0]["result"]["protocolVersion"])
            self.assertEqual(-32602, responses[1]["error"]["code"])
            self.assertIn("completed initialization", responses[1]["error"]["message"])
            self.assertEqual(3, len(responses[2]["result"]["tools"]))

    def test_unknown_and_malformed_notifications_are_silent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world, data_dir = self._workspace(root, "workspace_01")
            self._register(data_dir, world, "workspace_01")

            _, responses = self._serve(
                _initialize_request()
                + _message("notifications/future", {"payload": "ignored"})
                + _message("notifications/cancelled", {})
                + _message("notifications/initialized", {"unexpected": True})
                + _message("ping", {}, 2),
                data_dir=data_dir,
                workspace_id="workspace_01",
            )

            self.assertEqual(2, len(responses))
            self.assertEqual(1, responses[0]["id"])
            self.assertEqual(2, responses[1]["id"])
            self.assertEqual({}, responses[1]["result"])

    def test_duplicate_and_out_of_order_lifecycle_messages_fail_without_responses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world, data_dir = self._workspace(root, "workspace_01")
            self._register(data_dir, world, "workspace_01")

            _, responses = self._serve(
                _initialized()
                + _initialize_request()
                + _initialized()
                + _initialized()
                + _initialize_request(2)
                + _message("tools/list", {}, 3),
                data_dir=data_dir,
                workspace_id="workspace_01",
            )

            self.assertEqual(3, len(responses))
            self.assertEqual(1, responses[0]["id"])
            self.assertEqual(-32602, responses[1]["error"]["code"])
            self.assertIn("already initialized", responses[1]["error"]["message"])
            self.assertEqual(3, len(responses[2]["result"]["tools"]))

    def test_stage_schema_and_execution_share_operation_content_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world, data_dir = self._workspace(root, "workspace_01")
            (world / "source/delete-me.txt").write_text("delete me\n", encoding="utf-8")
            self._register(data_dir, world, "workspace_01")
            cases = [
                (
                    {
                        "path": "source/create-me.txt",
                        "operation": "create",
                        "content": "create\n",
                    },
                    False,
                ),
                ({"path": "source/delete-me.txt", "operation": "delete"}, False),
                ({"path": "source/missing-content.txt", "operation": "create"}, True),
                ({"path": "source/manifest.json", "operation": "replace"}, True),
                (
                    {
                        "path": "source/delete-me.txt",
                        "operation": "delete",
                        "content": "forbidden",
                    },
                    True,
                ),
            ]
            requests = [_initialize(), _message("tools/list", {}, 2)]
            for index, (operation, _expected_error) in enumerate(cases, start=3):
                requests.append(
                    _message(
                        "tools/call",
                        {
                            "name": "forge_stage_changeset",
                            "arguments": {
                                "changeset_id": f"schema_case_{index}",
                                "operations": [operation],
                            },
                        },
                        index,
                    )
                )

            _, responses = self._serve(
                b"".join(requests), data_dir=data_dir, workspace_id="workspace_01"
            )

            stage = next(
                tool
                for tool in responses[1]["result"]["tools"]
                if tool["name"] == "forge_stage_changeset"
            )
            variants = stage["inputSchema"]["properties"]["operations"]["items"]["oneOf"]
            by_kind = {variant["properties"]["operation"]["const"]: variant for variant in variants}
            self.assertEqual({"create", "replace", "delete"}, set(by_kind))
            self.assertEqual({"path", "operation", "content"}, set(by_kind["create"]["required"]))
            self.assertEqual({"path", "operation", "content"}, set(by_kind["replace"]["required"]))
            self.assertEqual({"path", "operation"}, set(by_kind["delete"]["required"]))
            self.assertNotIn("content", by_kind["delete"]["properties"])
            self.assertTrue(all(variant["additionalProperties"] is False for variant in variants))
            self.assertEqual(
                MAX_CHANGESET_OPERATIONS,
                stage["inputSchema"]["properties"]["operations"]["maxItems"],
            )
            for variant in variants:
                self.assertEqual(MAX_MCP_MESSAGE_BYTES, variant["properties"]["path"]["maxLength"])
            for kind in ("create", "replace"):
                self.assertEqual(
                    MAX_CHANGE_FILE_BYTES,
                    by_kind[kind]["properties"]["content"]["x-worldforge-max-utf8-bytes"],
                )
            self.assertEqual(
                [expected_error for _operation, expected_error in cases],
                [response["result"]["isError"] for response in responses[2:]],
            )

    def test_strict_json_depth_size_and_closed_tool_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            world, data_dir = self._workspace(root, "workspace_01")
            self._register(data_dir, world, "workspace_01")
            nested: object = None
            for _ in range(MAX_JSON_DEPTH + 2):
                nested = [nested]
            malformed = b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n'
            oversized = b"x" * (MAX_MCP_MESSAGE_BYTES + 1) + b"\n"
            closed = _initialize() + _message(
                "tools/call",
                {
                    "name": "forge_list_changesets",
                    "arguments": {"limit": 1, "workspace_id": "workspace_02"},
                },
                2,
            )
            payload = (
                malformed
                + oversized
                + (
                    json.dumps(
                        {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": nested},
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
                + closed
            )

            code, responses = self._serve(payload, data_dir=data_dir, workspace_id="workspace_01")

            self.assertEqual(0, code)
            self.assertEqual(-32602, responses[0]["error"]["code"])
            self.assertEqual(-32602, responses[1]["error"]["code"])
            self.assertEqual(-32602, responses[2]["error"]["code"])
            self.assertEqual("2025-11-25", responses[3]["result"]["protocolVersion"])
            self.assertTrue(responses[4]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
