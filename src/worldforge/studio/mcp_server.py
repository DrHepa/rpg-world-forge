from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO

from worldforge.studio.changesets import (
    MAX_CHANGE_FILE_BYTES,
    MAX_CHANGESET_OPERATIONS,
    ChangesetManager,
)
from worldforge.studio.contracts import WORKSPACE_ID_PATTERN
from worldforge.studio.errors import StudioError, invalid_request, not_found
from worldforge.studio.storage import StudioStore, encode_json
from worldforge.studio.workspaces import WorkspaceManager

MCP_PROTOCOL_VERSION = "2025-11-25"
MAX_MCP_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
MAX_LIST_LIMIT = 100
ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")

TOOL_NAMES = (
    "forge_get_changeset",
    "forge_list_changesets",
    "forge_stage_changeset",
)


def _tool_definitions() -> list[dict[str, Any]]:
    path = {
        "type": "string",
        "format": "rpg-world-forge-portable-source-path",
        "pattern": (
            r"^source/[^/\\\u0000-\u001f<>:\u0022|?*]+"
            r"(?:/[^/\\\u0000-\u001f<>:\u0022|?*]+)*$"
        ),
        "maxLength": MAX_MCP_MESSAGE_BYTES,
        "x-worldforge-max-utf8-bytes": MAX_MCP_MESSAGE_BYTES,
        "x-worldforge-path-policy": {
            "root": "source/",
            "separator": "/",
            "normalization": "NFC",
            "reject_traversal_segments": True,
            "reject_windows_reserved_names": True,
            "reject_trailing_dot_or_space": True,
            "max_component_utf8_bytes": 255,
            "collision_key": "NFC-casefold",
        },
    }
    content = {
        "type": "string",
        "maxLength": MAX_CHANGE_FILE_BYTES,
        "x-worldforge-max-utf8-bytes": MAX_CHANGE_FILE_BYTES,
    }

    def operation(kind: str, *, with_content: bool) -> dict[str, Any]:
        properties: dict[str, Any] = {
            "path": path,
            "operation": {"const": kind},
        }
        required = ["path", "operation"]
        if with_content:
            properties["content"] = content
            required.append("content")
        return {
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": properties,
        }

    return [
        {
            "name": "forge_stage_changeset",
            "description": "Stage a reviewable changeset for the bound Forge workspace.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["operations"],
                "properties": {
                    "changeset_id": {
                        "type": "string",
                        "pattern": "^[a-z0-9][a-z0-9_-]{0,127}$",
                    },
                    "operations": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_CHANGESET_OPERATIONS,
                        "items": {
                            "oneOf": [
                                operation("create", with_content=True),
                                operation("replace", with_content=True),
                                operation("delete", with_content=False),
                            ]
                        },
                    },
                },
            },
        },
        {
            "name": "forge_get_changeset",
            "description": "Read one changeset from the bound Forge workspace.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["changeset_id"],
                "properties": {
                    "changeset_id": {
                        "type": "string",
                        "pattern": "^[a-z0-9][a-z0-9_-]{0,127}$",
                    }
                },
            },
        },
        {
            "name": "forge_list_changesets",
            "description": "List changesets for the bound Forge workspace.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {"enum": ["staged", "approved", "rejected", "applied"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIST_LIMIT},
                },
            },
        },
    ]


TOOLS = _tool_definitions()


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"JSON float overflows: {value}")
    return parsed


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _read_message(stream: BinaryIO) -> dict[str, Any] | None:
    line = stream.readline(MAX_MCP_MESSAGE_BYTES + 2)
    if line == b"":
        return None
    content_length = len(line) - (1 if line.endswith(b"\n") else 0)
    if content_length > MAX_MCP_MESSAGE_BYTES or (
        len(line) == MAX_MCP_MESSAGE_BYTES + 2 and not line.endswith(b"\n")
    ):
        while line and not line.endswith(b"\n"):
            line = stream.readline(MAX_MCP_MESSAGE_BYTES + 2)
        raise invalid_request("MCP message exceeds the 8 MiB line limit")
    payload = line[:-1] if line.endswith(b"\n") else line
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicates,
            parse_float=_finite_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise invalid_request(f"Malformed MCP JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise invalid_request("MCP message root must be an object")
    _assert_json_bounds(value)
    return value


def _assert_json_bounds(root: object) -> None:
    nodes = 0
    stack: list[tuple[object, int]] = [(root, 0)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise invalid_request("MCP message exceeds JSON complexity limits")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)


def _encode_message(value: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StudioError("internal_error", "Could not encode MCP response") from exc
    if len(payload) > MAX_MCP_MESSAGE_BYTES:
        raise StudioError("internal_error", "MCP response exceeds the 8 MiB line limit")
    return payload + b"\n"


def _write(stream: BinaryIO, value: dict[str, Any]) -> None:
    stream.write(_encode_message(value))
    stream.flush()


def _request_id(value: object) -> str | int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise invalid_request("MCP request id must be a string or integer")
    if isinstance(value, str) and (not value or len(value.encode("utf-8")) > 128):
        raise invalid_request("MCP request id string is invalid")
    return value


def _closed_object(
    value: object,
    *,
    allowed: set[str],
    required: set[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid_request("MCP params must be an object")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown or missing:
        fields = unknown or missing
        raise invalid_request(f"MCP params contain invalid fields: {', '.join(sorted(fields))}")
    return value


class ForgeMcpServer:
    def __init__(self, store: StudioStore, workspace_id: str) -> None:
        if WORKSPACE_ID_PATTERN.fullmatch(workspace_id) is None:
            raise invalid_request("MCP workspace id is invalid")
        self.store = store
        self.workspace = WorkspaceManager(store).get(workspace_id)
        self.workspace_id = workspace_id
        self.changesets = ChangesetManager(store, recover=False)
        self.initialize_completed = False
        self.client_initialized = False

    def handle(self, message: object) -> dict[str, Any] | None:
        if isinstance(message, dict) and "id" not in message:
            self._handle_notification(message)
            return None
        request = _closed_object(
            message,
            allowed={"jsonrpc", "id", "method", "params"},
            required={"jsonrpc", "method"},
        )
        if request["jsonrpc"] != "2.0" or not isinstance(request["method"], str):
            raise invalid_request("MCP message uses an unsupported JSON-RPC contract")
        method = request["method"]
        request_id = _request_id(request["id"])
        try:
            result = self._request(method, request.get("params", {}))
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except StudioError as exc:
            return _jsonrpc_error(request_id, _studio_rpc_code(exc.code), exc.message, exc.details)

    def _handle_notification(self, message: dict[str, Any]) -> None:
        try:
            notification = _closed_object(
                message,
                allowed={"jsonrpc", "method", "params"},
                required={"jsonrpc", "method"},
            )
            if notification["jsonrpc"] != "2.0" or not isinstance(notification["method"], str):
                raise invalid_request("MCP notification uses an unsupported JSON-RPC contract")
            self._notification(notification["method"], notification.get("params", {}))
        except Exception:
            # JSON-RPC notifications never receive a response. Invalid notifications are
            # ignored without changing lifecycle state or retaining attacker-controlled data.
            return

    def _notification(self, method: str, params: object) -> None:
        if method == "notifications/initialized":
            _closed_object(params, allowed=set())
            if not self.initialize_completed:
                raise invalid_request("MCP initialized notification arrived before initialize")
            if self.client_initialized:
                raise invalid_request("MCP initialized notification was already received")
            self.client_initialized = True
            return
        if method == "notifications/cancelled":
            _closed_object(params, allowed={"requestId", "reason"}, required={"requestId"})
            return
        raise invalid_request(f"Unsupported MCP notification: {method}")

    def _request(self, method: str, params: object) -> dict[str, Any]:
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            _closed_object(params, allowed=set())
            return {}
        if not self.client_initialized:
            raise invalid_request("MCP client has not completed initialization")
        if method == "tools/list":
            _closed_object(params, allowed={"cursor"})
            if isinstance(params, dict) and params.get("cursor") is not None:
                raise invalid_request("Forge MCP tool list is not paginated")
            return {"tools": TOOLS}
        if method == "tools/call":
            return self._call_tool(params)
        raise invalid_request(f"Unsupported MCP request method: {method}")

    def _initialize(self, params: object) -> dict[str, Any]:
        if self.initialize_completed:
            raise invalid_request("MCP server is already initialized")
        value = _closed_object(
            params,
            allowed={"protocolVersion", "capabilities", "clientInfo"},
            required={"protocolVersion", "capabilities", "clientInfo"},
        )
        if value["protocolVersion"] != MCP_PROTOCOL_VERSION:
            raise invalid_request("MCP protocol version is unsupported")
        if not isinstance(value["capabilities"], dict):
            raise invalid_request("MCP client capabilities must be an object")
        client = _closed_object(
            value["clientInfo"], allowed={"name", "title", "version"}, required={"name", "version"}
        )
        if not isinstance(client["name"], str) or not isinstance(client["version"], str):
            raise invalid_request("MCP client info is invalid")
        self.initialize_completed = True
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "rpg-world-forge", "version": "0.7.0"},
            "instructions": (
                "Stage and inspect changesets for the bound workspace only. "
                "Approval and application remain human-controlled in Forge Studio."
            ),
        }

    def _call_tool(self, params: object) -> dict[str, Any]:
        value = _closed_object(
            params, allowed={"name", "arguments", "_meta"}, required={"name", "arguments"}
        )
        name = value["name"]
        arguments = value["arguments"]
        if not isinstance(name, str) or name not in TOOL_NAMES:
            return _tool_error("invalid_request", "Unknown or unavailable Forge MCP tool")
        try:
            if name == "forge_stage_changeset":
                result = self._stage(arguments)
            elif name == "forge_get_changeset":
                result = self._get(arguments)
            else:
                result = self._list(arguments)
            return _tool_success(result)
        except StudioError as exc:
            return _tool_error(exc.code, exc.message, exc.details)
        except Exception:
            return _tool_error("internal_error", "Internal Forge MCP tool error")

    def _stage(self, arguments: object) -> dict[str, Any]:
        value = _closed_object(
            arguments,
            allowed={"changeset_id", "operations"},
            required={"operations"},
        )
        changeset_id = value.get("changeset_id")
        if changeset_id is not None and (
            not isinstance(changeset_id, str) or ENTITY_ID_PATTERN.fullmatch(changeset_id) is None
        ):
            raise invalid_request("changeset_id is invalid")
        params = {**value, "workspace_id": self.workspace_id}
        return {"changeset": self.changesets.create(params)}

    def _get(self, arguments: object) -> dict[str, Any]:
        value = _closed_object(arguments, allowed={"changeset_id"}, required={"changeset_id"})
        changeset_id = value["changeset_id"]
        if not isinstance(changeset_id, str) or ENTITY_ID_PATTERN.fullmatch(changeset_id) is None:
            raise invalid_request("changeset_id is invalid")
        changeset = self.changesets.get(changeset_id)
        if changeset["workspace_id"] != self.workspace_id:
            raise not_found(f"Changeset {changeset_id} was not found")
        return {"changeset": changeset}

    def _list(self, arguments: object) -> dict[str, Any]:
        value = _closed_object(arguments, allowed={"status", "limit"})
        limit = value.get("limit", MAX_LIST_LIMIT)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_LIST_LIMIT
        ):
            raise invalid_request(f"limit must be an integer from 1 to {MAX_LIST_LIMIT}")
        return {
            "changesets": self.changesets.list(
                workspace_id=self.workspace_id,
                status=value.get("status"),
                limit=limit,
            )
        }


def _tool_success(value: dict[str, Any]) -> dict[str, Any]:
    text = encode_json(value)
    if len(text.encode("utf-8")) > MAX_MCP_MESSAGE_BYTES // 2:
        return _tool_error("internal_error", "Forge MCP tool result exceeds its size limit")
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": value,
        "isError": False,
    }


def _tool_error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    value = {"error": {"code": code, "message": message, "details": details or {}}}
    return {
        "content": [{"type": "text", "text": encode_json(value)}],
        "structuredContent": value,
        "isError": True,
    }


def _studio_rpc_code(code: str) -> int:
    return -32602 if code == "invalid_request" else -32000


def _jsonrpc_error(
    request_id: str | int | None,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def serve(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    data_dir: str | Path,
    workspace_id: str,
) -> int:
    try:
        store = StudioStore(data_dir, mode="secondary")
        server = ForgeMcpServer(store, workspace_id)
    except StudioError:
        return 1
    try:
        while True:
            try:
                message = _read_message(input_stream)
                if message is None:
                    break
                response = server.handle(message)
            except StudioError as exc:
                response = _jsonrpc_error(
                    None, _studio_rpc_code(exc.code), exc.message, exc.details
                )
            except Exception:
                response = _jsonrpc_error(None, -32603, "Internal Forge MCP server error")
            if response is not None:
                try:
                    _write(output_stream, response)
                except StudioError:
                    _write(output_stream, _jsonrpc_error(None, -32603, "MCP response is too large"))
    finally:
        store.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="worldforge-forge-mcp",
        description="Run the workspace-bound Forge changeset MCP server over stdio.",
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--workspace-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return serve(
        sys.stdin.buffer,
        sys.stdout.buffer,
        data_dir=args.data_dir,
        workspace_id=args.workspace_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
