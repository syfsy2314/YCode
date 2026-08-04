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


@pytest.mark.asyncio
async def test_background_start_returns_before_connection_and_runs_callback() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    callbacks = 0

    class BlockingConnection:
        def __init__(self, config, redactor) -> None:
            del redactor
            self.config = config
            self.state = McpConnectionState.STARTING

        @property
        def status(self) -> McpServerStatus:
            return McpServerStatus(self.config.name, "stdio", self.state, 0)

        async def start(self) -> McpDiscoveryResult:
            started.set()
            await release.wait()
            self.state = McpConnectionState.READY
            return McpDiscoveryResult(self.config.name, "2026-07-28", ())

        async def close(self) -> None:
            self.state = McpConnectionState.CLOSED

    manager = McpManager(
        McpConfigSet((server("slow"),)),
        ToolRegistry(),
        SecretRedactor(),
        BlockingConnection,
    )

    def completed() -> None:
        nonlocal callbacks
        callbacks += 1

    manager.add_startup_callback(completed)
    manager.start_background()
    await started.wait()

    assert manager.snapshot().starting_count == 1
    assert callbacks == 0

    release.set()
    await manager.start()

    assert callbacks == 1
    assert manager.snapshot().ready_count == 1
    await manager.close()


@pytest.mark.asyncio
async def test_close_cancels_background_start() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    closed = asyncio.Event()

    class BlockingConnection:
        def __init__(self, config, redactor) -> None:
            del redactor
            self.config = config
            self.state = McpConnectionState.STARTING

        @property
        def status(self) -> McpServerStatus:
            return McpServerStatus(self.config.name, "stdio", self.state, 0)

        async def start(self) -> McpDiscoveryResult:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return McpDiscoveryResult(self.config.name, "2026-07-28", ())

        async def close(self) -> None:
            self.state = McpConnectionState.CLOSED
            closed.set()

    manager = McpManager(
        McpConfigSet((server("slow"),)),
        ToolRegistry(),
        SecretRedactor(),
        BlockingConnection,
    )
    manager.start_background()
    await started.wait()

    await manager.close()

    assert cancelled.is_set()
    assert closed.is_set()
