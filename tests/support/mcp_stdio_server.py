"""真实 stdio MCP 集成测试使用的可控 Server。"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

server = MCPServer("ycode-test-stdio", version="1.0.0")


def _record(event: str, **details: Any) -> None:
    state_path = os.environ.get("YCODE_MCP_STATE_FILE")
    if not state_path:
        return
    payload = {"event": event, "pid": os.getpid(), **details}
    with Path(state_path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


@server.tool(description="Return the supplied text.")
async def echo(value: str) -> str:
    _record("call", tool="echo", arguments={"value": value})
    return value


@server.tool(description="Return text plus a structured object.", structured_output=True)
async def structured(value: str) -> dict[str, object]:
    _record("call", tool="structured", arguments={"value": value})
    return {"value": value, "length": len(value)}


@server.tool(description="Report whether the configured secret reached the process.")
async def environment_received() -> bool:
    _record("call", tool="environment_received", arguments={})
    return os.environ.get("YCODE_MCP_TEST_SECRET") == "integration-secret"


@server.tool(description="Wait for a controlled interval.")
async def slow(delay_seconds: float) -> str:
    _record("call", tool="slow", arguments={"delay_seconds": delay_seconds})
    await asyncio.sleep(delay_seconds)
    return "finished"


@server.tool(description="Return a distinct value after a controlled delay.")
async def delayed_echo(value: str, delay_seconds: float) -> str:
    _record("call", tool="delayed_echo", arguments={"value": value})
    await asyncio.sleep(delay_seconds)
    _record("completed", tool="delayed_echo", value=value)
    return value


@server.tool(description="Emit supported notifications before returning.")
async def notifications(ctx: Context) -> str:
    await ctx.info("test log notification")
    await ctx.report_progress(1, 1, "complete")
    await ctx.notify_tools_changed()
    _record("call", tool="notifications", arguments={})
    return "notifications-complete"


@server.tool(description="Raise a controlled tool error.")
async def fail() -> str:
    _record("call", tool="fail", arguments={})
    raise ValueError("controlled failure")


@server.tool(description="Write a large diagnostic burst to stderr.")
async def stderr_burst() -> str:
    secret = os.environ.get("YCODE_MCP_TEST_SECRET", "no-secret")
    sys.stderr.write((secret + " diagnostic\n") * 2000)
    sys.stderr.flush()
    _record("call", tool="stderr_burst", arguments={})
    return "stderr-drained"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    parser.add_argument("--early-exit", action="store_true")
    parser.add_argument("--invalid-stdout", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_check:
        print("stdio fixture self-check passed", file=sys.stderr)
        return
    if arguments.invalid_stdout:
        print("this is not json-rpc", flush=True)
        return
    if arguments.early_exit:
        return
    _record("started")
    try:
        if arguments.legacy:
            _run_legacy()
        else:
            server.run("stdio")
    finally:
        _record("stopped")


def _run_legacy() -> None:
    """模拟只理解 2025 initialize 的旧 Server。"""

    for line in sys.stdin:
        request = json.loads(line)
        request_id = request.get("id")
        method = request.get("method")
        if method is None:
            if request_id == "server-request-1":
                _record(
                    "client_response",
                    has_error="error" in request,
                    has_result="result" in request,
                )
            continue
        if request_id is None:
            continue
        if method == "server/discover":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        elif method == "initialize":
            _record(
                "initialize",
                capabilities=(request.get("params") or {}).get("capabilities", {}),
            )
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ycode-test-legacy", "version": "1.0.0"},
                },
            }
        elif method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Legacy echo",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                            },
                        }
                    ]
                },
            }
        elif method == "tools/call":
            parameters = request.get("params") or {}
            value = (parameters.get("arguments") or {}).get("value", "")
            _record("call", tool=parameters.get("name"), arguments={"value": value})
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": value}],
                    "isError": False,
                },
            }
        else:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        if method == "initialize":
            # 客户端应忽略已完成 ID 的重复响应和从未发出的未知 ID。
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": 999999, "result": {}},
                    separators=(",", ":"),
                )
                + "\n"
            )
            sys.stdout.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "server-request-1",
                        "method": "roots/list",
                        "params": {},
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
