"""多 MCP Server 的启动、注册与状态汇总。"""

import asyncio
from collections.abc import Callable

from ycode.config.environment import SecretRedactor
from ycode.config.mcp import McpConfigSet, McpServerConfig
from ycode.mcp.connection import McpConnection
from ycode.mcp.models import (
    McpConnectionState,
    McpErrorSummary,
    McpServerStatus,
    McpStatusReport,
)
from ycode.mcp.tool import MCPToolWrapper
from ycode.security.models import SecurityConfigWarning
from ycode.tools.registry import ToolRegistry


class McpManager:
    def __init__(
        self,
        config: McpConfigSet,
        registry: ToolRegistry,
        redactor: SecretRedactor,
        connection_factory: Callable[
            [McpServerConfig, SecretRedactor], McpConnection
        ] = McpConnection,
    ) -> None:
        self._config = config
        self._registry = registry
        self._redactor = redactor
        self._connections = {
            server.name: connection_factory(server, redactor)
            for server in config.servers
            if server.enabled
        }
        self._status_order: list[tuple[int, str]] = []
        self._static_statuses: dict[str, McpServerStatus] = {}
        self._tool_counts: dict[str, int] = {}
        self._registration_errors: dict[str, McpErrorSummary] = {}
        self._security_warnings: tuple[SecurityConfigWarning, ...] = ()
        self._start_task: asyncio.Task[None] | None = None
        self._startup_callbacks: list[Callable[[], None]] = []
        for index, server in zip(config.entry_indices, config.servers, strict=True):
            self._status_order.append((index, server.name))
            if not server.enabled:
                self._static_statuses[server.name] = McpServerStatus(
                    server.name, server.transport, McpConnectionState.DISABLED, 0
                )
        for issue in config.issues:
            name = issue.server_name or f"entry_{issue.entry_index + 1}"
            key = f"invalid:{issue.entry_index}:{name}"
            self._status_order.append((issue.entry_index, key))
            self._static_statuses[key] = McpServerStatus(
                name,
                "invalid",
                McpConnectionState.INVALID,
                0,
                McpErrorSummary(issue.code, issue.message),
            )
        self._status_order.sort(key=lambda item: (item[0], item[1]))
        self._close_task: asyncio.Task[None] | None = None

    def add_startup_callback(self, callback: Callable[[], None]) -> None:
        if self._start_task is not None and self._start_task.done():
            if not self._start_task.cancelled() and self._start_task.exception() is None:
                try:
                    callback()
                except Exception:
                    pass
            return
        self._startup_callbacks.append(callback)

    def start_background(self) -> None:
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._start())

    async def start(self) -> None:
        self.start_background()
        assert self._start_task is not None
        await asyncio.shield(self._start_task)

    async def _start(self) -> None:
        try:
            await self._start_connections()
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        for callback in self._startup_callbacks:
            try:
                callback()
            except Exception:
                continue
        self._startup_callbacks.clear()

    async def _start_connections(self) -> None:
        async with asyncio.TaskGroup() as group:
            tasks = {
                name: group.create_task(connection.start())
                for name, connection in self._connections.items()
            }
        for name, task in tasks.items():
            connection = self._connections[name]
            discovery = task.result()
            count = 0
            if discovery.issues:
                self._registration_errors[name] = discovery.issues[0]
            if connection.state is McpConnectionState.READY:
                for descriptor in discovery.tools:
                    try:
                        self._registry.register(
                            MCPToolWrapper(descriptor, connection, self._redactor)
                        )
                    except ValueError:
                        self._registration_errors[name] = McpErrorSummary(
                            "tool_name_conflict", "MCP 工具公开名称与现有工具冲突。"
                        )
                        continue
                    count += 1
            self._tool_counts[name] = count

    def snapshot(self) -> McpStatusReport:
        statuses: list[McpServerStatus] = []
        for _, key in self._status_order:
            connection = self._connections.get(key)
            if connection is None:
                statuses.append(self._static_statuses[key])
                continue
            current = connection.status
            statuses.append(
                McpServerStatus(
                    current.name,
                    current.transport,
                    current.state,
                    self._tool_counts.get(key, 0),
                    self._registration_errors.get(key, current.last_error),
                )
            )
        return McpStatusReport(tuple(statuses), self._security_warnings)

    def set_security_warnings(self, warnings: tuple[SecurityConfigWarning, ...]) -> None:
        self._security_warnings = tuple(warnings)

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await self._close_task

    async def _close(self) -> None:
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            await asyncio.gather(self._start_task, return_exceptions=True)
        await asyncio.gather(*(connection.close() for connection in self._connections.values()))
