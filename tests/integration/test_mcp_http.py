import asyncio
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from ycode.config.environment import SecretRedactor
from ycode.config.mcp import HttpMcpServerConfig
from ycode.mcp.connection import McpConnection, McpTransportFactory
from ycode.mcp.models import McpConnectionState
from ycode.tools.errors import ToolError

SERVER_PATH = Path(__file__).parents[1] / "support" / "mcp_http_server.py"


def unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@asynccontextmanager
async def http_server(tmp_path: Path, *, json_response: bool):
    port = unused_port()
    state_file = tmp_path / f"http-{port}.jsonl"
    environment = os.environ.copy()
    environment.update(
        {
            "YCODE_MCP_STATE_FILE": str(state_file),
            "YCODE_MCP_EXPECTED_HEADER": "header-secret",
        }
    )
    arguments = [str(SERVER_PATH), "--port", str(port)]
    if json_response:
        arguments.append("--json-response")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        *arguments,
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
            except OSError as error:
                if process.returncode is not None:
                    raise RuntimeError("HTTP fixture exited during startup") from error
                await asyncio.sleep(0.03)
                continue
            writer.close()
            await writer.wait_closed()
            del reader
            break
        else:
            raise TimeoutError("HTTP fixture did not start")
        yield port, state_file
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


def config(port: int, *, tool_timeout: float = 2.0) -> HttpMcpServerConfig:
    return HttpMcpServerConfig.model_validate(
        {
            "name": "remote",
            "transport": "streamable_http",
            "url": f"http://127.0.0.1:{port}/mcp",
            "headers": {"X-YCode-Test": "header-secret"},
            "startup_timeout_seconds": 5,
            "tool_timeout_seconds": tool_timeout,
        }
    )


def text_of(result: object) -> str:
    assert isinstance(result, CallToolResult)
    return "".join(item.text for item in result.content if isinstance(item, TextContent))


def records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
@pytest.mark.parametrize("json_response", [True, False])
async def test_streamable_http_json_and_request_sse(tmp_path: Path, json_response: bool) -> None:
    async with http_server(tmp_path, json_response=json_response) as (port, state_file):
        connection = McpConnection(config(port), SecretRedactor())
        discovery = await connection.start()

        first = await connection.call_tool("echo", {"value": "one"})
        second = await connection.call_tool("echo", {"value": "two"})
        header = await connection.call_tool("header_received", {})

        assert connection.state is McpConnectionState.READY
        assert {item.remote_name for item in discovery.tools} >= {
            "echo",
            "header_received",
            "slow",
        }
        assert text_of(first) == "one"
        assert text_of(second) == "two"
        assert "true" in text_of(header).lower()
        calls = [item for item in records(state_file) if item["event"] == "call"]
        echo_sessions = {item["session_id"] for item in calls if item["tool"] == "echo"}
        assert len(echo_sessions) == 1
        assert "header-secret" not in repr(connection.status)
        await connection.close()


@pytest.mark.asyncio
async def test_streamable_http_timeout_keeps_connection_usable(tmp_path: Path) -> None:
    async with http_server(tmp_path, json_response=True) as (port, _):
        connection = McpConnection(config(port, tool_timeout=0.05), SecretRedactor())
        await connection.start()

        with pytest.raises(ToolError) as caught:
            await connection.call_tool("slow", {"delay_seconds": 1.0})
        assert caught.value.code == "mcp_timeout"
        assert text_of(await connection.call_tool("echo", {"value": "ready"})) == "ready"
        await connection.close()


@pytest.mark.asyncio
async def test_streamable_http_concurrent_responses_match_calls(tmp_path: Path) -> None:
    async with http_server(tmp_path, json_response=True) as (port, state_file):
        connection = McpConnection(config(port), SecretRedactor())
        await connection.start()

        slow_result, fast_result = await asyncio.gather(
            connection.call_tool("delayed_echo", {"value": "slow", "delay_seconds": 0.1}),
            connection.call_tool("delayed_echo", {"value": "fast", "delay_seconds": 0.01}),
        )

        assert text_of(slow_result) == "slow"
        assert text_of(fast_result) == "fast"
        completed = [item["value"] for item in records(state_file) if item["event"] == "completed"]
        assert completed == ["fast", "slow"]
        await connection.close()


def test_http_factory_uses_only_streamable_http_transport() -> None:
    factory = McpTransportFactory(config(65530), SecretRedactor())

    transport = factory.create()

    generator = transport.gen  # type: ignore[attr-defined]
    assert generator.ag_code.co_name == "streamable_http_client"
