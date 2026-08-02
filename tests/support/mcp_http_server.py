"""真实 Streamable HTTP 集成测试使用的可控 Server。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver import Context

server = MCPServer("ycode-test-http", version="1.0.0", log_level="ERROR")


def _record(event: str, **details: Any) -> None:
    state_path = os.environ.get("YCODE_MCP_STATE_FILE")
    if not state_path:
        return
    with Path(state_path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **details}, ensure_ascii=False) + "\n")


@server.tool(description="Return text over Streamable HTTP.")
async def echo(value: str, ctx: Context) -> str:
    headers = ctx.headers or {}
    _record("call", tool="echo", value=value, session_id=headers.get("mcp-session-id"))
    return value


@server.tool(description="Confirm a test Header without returning its value.")
async def header_received(ctx: Context) -> bool:
    expected = os.environ.get("YCODE_MCP_EXPECTED_HEADER")
    received = (ctx.headers or {}).get("x-ycode-test")
    _record("call", tool="header_received", matched=received == expected)
    return received == expected


@server.tool(description="Wait for a controlled interval.")
async def slow(delay_seconds: float, ctx: Context) -> str:
    headers = ctx.headers or {}
    _record("call", tool="slow", session_id=headers.get("mcp-session-id"))
    await asyncio.sleep(delay_seconds)
    return "finished"


@server.tool(description="Return a distinct value after a controlled delay.")
async def delayed_echo(value: str, delay_seconds: float) -> str:
    _record("call", tool="delayed_echo", value=value)
    await asyncio.sleep(delay_seconds)
    _record("completed", tool="delayed_echo", value=value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--json-response", action="store_true")
    arguments = parser.parse_args()
    _record("started", json_response=arguments.json_response)
    server.run(
        "streamable-http",
        host="127.0.0.1",
        port=arguments.port,
        streamable_http_path="/mcp",
        json_response=arguments.json_response,
    )


if __name__ == "__main__":
    main()
