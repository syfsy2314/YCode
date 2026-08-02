import asyncio

import pytest

from ycode.config.environment import SecretRedactor
from ycode.config.mcp import McpConfigIssue, McpConfigSet, StdioMcpServerConfig
from ycode.mcp.manager import McpManager
from ycode.mcp.models import (
    McpConnectionState,
    McpDiscoveryResult,
    McpServerStatus,
    McpToolDescriptor,
)
from ycode.tools.arguments import JsonSchemaToolArguments
from ycode.tools.registry import ToolRegistry


def server(name: str, *, enabled: bool = True) -> StdioMcpServerConfig:
    return StdioMcpServerConfig.model_validate(
        {"name": name, "enabled": enabled, "transport": "stdio", "command": "python"}
    )


def descriptor(server_name: str, remote_name: str = "echo") -> McpToolDescriptor:
    return McpToolDescriptor(
        public_name=f"mcp_{server_name}_{remote_name}",
        server_name=server_name,
        remote_name=remote_name,
        description="测试工具",
        arguments=JsonSchemaToolArguments({"type": "object"}),
    )


@pytest.mark.asyncio
async def test_status_preserves_config_order_and_isolates_invalid_disabled() -> None:
    instances = {}

    class FakeConnection:
        def __init__(self, config, redactor) -> None:
            del redactor
            self.config = config
            self.state = McpConnectionState.STARTING
            self.close_count = 0
            instances[config.name] = self

        @property
        def status(self) -> McpServerStatus:
            return McpServerStatus(self.config.name, "stdio", self.state, 0)

        async def start(self) -> McpDiscoveryResult:
            await asyncio.sleep(0.01)
            self.state = McpConnectionState.READY
            return McpDiscoveryResult(
                self.config.name, "2026-07-28", (descriptor(self.config.name),)
            )

        async def close(self) -> None:
            self.close_count += 1
            self.state = McpConnectionState.CLOSED

    config = McpConfigSet(
        servers=(server("ready"), server("disabled", enabled=False)),
        issues=(McpConfigIssue(1, None, "invalid_config", "配置无效"),),
        entry_indices=(0, 2),
    )
    registry = ToolRegistry()
    manager = McpManager(config, registry, SecretRedactor(), FakeConnection)

    await manager.start()
    report = manager.snapshot()

    assert [item.name for item in report.servers] == ["ready", "entry_2", "disabled"]
    assert [item.state for item in report.servers] == [
        McpConnectionState.READY,
        McpConnectionState.INVALID,
        McpConnectionState.DISABLED,
    ]
    assert report.servers[0].tool_count == 1
    assert registry.get("mcp_ready_echo") is not None

    await manager.close()
    await manager.close()
    assert instances["ready"].close_count == 1


@pytest.mark.asyncio
async def test_enabled_servers_start_concurrently() -> None:
    class SlowConnection:
        def __init__(self, config, redactor) -> None:
            del redactor
            self.config = config
            self.state = McpConnectionState.STARTING

        @property
        def status(self) -> McpServerStatus:
            return McpServerStatus(self.config.name, "stdio", self.state, 0)

        async def start(self) -> McpDiscoveryResult:
            await asyncio.sleep(0.03)
            self.state = McpConnectionState.READY
            return McpDiscoveryResult(self.config.name, "2026-07-28", ())

        async def close(self) -> None:
            self.state = McpConnectionState.CLOSED

    manager = McpManager(
        McpConfigSet((server("one"), server("two"))),
        ToolRegistry(),
        SecretRedactor(),
        SlowConnection,
    )
    loop = asyncio.get_running_loop()
    started = loop.time()
    await manager.start()

    assert loop.time() - started < 0.055
    await manager.close()
