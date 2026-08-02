import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolResult, TextContent

from ycode.config.environment import SecretRedactor
from ycode.config.mcp import McpConfigSet, StdioMcpServerConfig
from ycode.mcp.connection import McpConnection
from ycode.mcp.manager import McpManager
from ycode.mcp.models import McpConnectionState
from ycode.tools import ToolRegistry
from ycode.tools.errors import ToolError

SERVER_PATH = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"


def config(state_file: Path, *, tool_timeout: float = 2.0) -> StdioMcpServerConfig:
    return StdioMcpServerConfig.model_validate(
        {
            "name": "fixture",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(SERVER_PATH)],
            "env": {
                "YCODE_MCP_STATE_FILE": str(state_file),
                "YCODE_MCP_TEST_SECRET": "integration-secret",
            },
            "startup_timeout_seconds": 10,
            "tool_timeout_seconds": tool_timeout,
        }
    )


def records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def text_of(result: object) -> str:
    assert isinstance(result, CallToolResult)
    return "".join(item.text for item in result.content if isinstance(item, TextContent))


@pytest.mark.asyncio
async def test_real_stdio_discovers_calls_and_reuses_one_process(tmp_path: Path) -> None:
    state_file = tmp_path / "stdio-state.jsonl"
    connection = McpConnection(config(state_file), SecretRedactor())

    discovery = await connection.start()
    first = await connection.call_tool("echo", {"value": "one"})
    second = await connection.call_tool("echo", {"value": "two"})
    environment = await connection.call_tool("environment_received", {})
    structured = await connection.call_tool("structured", {"value": "shape"})

    assert connection.state is McpConnectionState.READY
    assert {item.remote_name for item in discovery.tools} >= {
        "echo",
        "structured",
        "environment_received",
        "slow",
        "fail",
    }
    assert text_of(first) == "one"
    assert text_of(second) == "two"
    assert "true" in text_of(environment).lower()
    assert isinstance(structured, CallToolResult)
    assert structured.structured_content == {"value": "shape", "length": 5}
    active_records = records(state_file)
    assert len({item["pid"] for item in active_records}) == 1
    assert "integration-secret" not in repr(connection.status)

    await connection.close()

    assert records(state_file)[-1]["event"] == "stopped"


@pytest.mark.asyncio
async def test_real_stdio_drains_and_redacts_large_stderr(tmp_path: Path) -> None:
    state_file = tmp_path / "stderr-state.jsonl"
    redactor = SecretRedactor()
    redactor.add("integration-secret")
    connection = McpConnection(config(state_file), redactor)
    await connection.start()

    result = await connection.call_tool("stderr_burst", {})

    assert text_of(result) == "stderr-drained"
    diagnostic = connection._transport_factory.stderr.value
    assert "integration-secret" not in diagnostic
    assert len(diagnostic.encode("utf-8")) <= 8 * 1024
    await connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["--early-exit", "--invalid-stdout"])
async def test_real_stdio_bad_process_is_isolated_as_unavailable(tmp_path: Path, mode: str) -> None:
    state_file = tmp_path / f"bad-{mode[2:]}.jsonl"
    bad_config = config(state_file).model_copy(update={"args": (str(SERVER_PATH), mode)})
    connection = McpConnection(bad_config, SecretRedactor())

    discovery = await connection.start()

    assert discovery.tools == ()
    assert connection.state is McpConnectionState.UNAVAILABLE
    await connection.close()


@pytest.mark.asyncio
async def test_real_stdio_manager_registers_deferred_wrappers(tmp_path: Path) -> None:
    state_file = tmp_path / "manager-state.jsonl"
    registry = ToolRegistry()
    manager = McpManager(McpConfigSet((config(state_file),)), registry, SecretRedactor())

    await manager.start()

    tool = registry.get("mcp_fixture_echo")
    assert tool is not None
    assert tool.definition.defer_loading is True
    assert manager.snapshot().servers[0].tool_count >= 5
    await manager.close()


@pytest.mark.asyncio
async def test_real_stdio_timeout_cancels_only_the_call(tmp_path: Path) -> None:
    state_file = tmp_path / "timeout-state.jsonl"
    connection = McpConnection(config(state_file, tool_timeout=0.05), SecretRedactor())
    await connection.start()

    with pytest.raises(ToolError) as caught:
        await connection.call_tool("slow", {"delay_seconds": 1.0})
    assert caught.value.code == "mcp_timeout"

    result = await connection.call_tool("echo", {"value": "still-ready"})
    assert text_of(result) == "still-ready"
    assert connection.state is McpConnectionState.READY
    await connection.close()


@pytest.mark.asyncio
async def test_real_stdio_user_cancellation_has_one_terminal_outcome(tmp_path: Path) -> None:
    state_file = tmp_path / "cancel-state.jsonl"
    connection = McpConnection(config(state_file), SecretRedactor())
    await connection.start()
    task = asyncio.create_task(connection.call_tool("slow", {"delay_seconds": 1.0}))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert text_of(await connection.call_tool("echo", {"value": "after-cancel"})) == (
        "after-cancel"
    )
    await connection.close()


@pytest.mark.asyncio
async def test_real_stdio_concurrent_responses_match_their_calls(tmp_path: Path) -> None:
    state_file = tmp_path / "concurrent-state.jsonl"
    connection = McpConnection(config(state_file), SecretRedactor())
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


@pytest.mark.asyncio
async def test_real_stdio_supported_notifications_do_not_change_catalog(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "notifications-state.jsonl"
    connection = McpConnection(config(state_file), SecretRedactor())
    discovery = await connection.start()
    names_before = tuple(item.public_name for item in discovery.tools)

    result = await connection.call_tool("notifications", {})

    assert text_of(result) == "notifications-complete"
    assert tuple(item.public_name for item in discovery.tools) == names_before
    await connection.close()
