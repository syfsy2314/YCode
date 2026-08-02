"""MCP 传输构造与连接生命周期组件。"""

import asyncio
import os
import threading
from contextlib import AbstractAsyncContextManager, AsyncExitStack

import httpx2
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from ycode.config.environment import SecretRedactor
from ycode.config.mcp import HttpMcpServerConfig, McpServerConfig, StdioMcpServerConfig
from ycode.mcp.models import (
    McpConnectionState,
    McpDiscoveryResult,
    McpErrorSummary,
    McpServerStatus,
    McpToolDescriptor,
)
from ycode.mcp.naming import map_tool_names
from ycode.tools.arguments import JsonSchemaToolArguments
from ycode.tools.errors import ToolError

_STDERR_BUFFER_BYTES = 8 * 1024
_HTTP_READ_TIMEOUT_GRACE_SECONDS = 1.0
_MCP_CONNECTION_CLOSED = -32000
_MCP_REQUEST_TIMEOUT = -32001


class RedactingStderrSink:
    """有界保存子进程诊断，避免向终端泄露原始 stderr。"""

    def __init__(self, redactor: SecretRedactor) -> None:
        self._redactor = redactor
        self._value = ""
        self._lock = threading.Lock()
        self._read_fd: int | None = None
        self._write_fd: int | None = None
        self._reader_thread: threading.Thread | None = None

    def write(self, value: str) -> int:
        self._append(value)
        return len(value)

    def flush(self) -> None:
        return None

    def fileno(self) -> int:
        if self._write_fd is None:
            self._read_fd, self._write_fd = os.pipe()
            self._reader_thread = threading.Thread(target=self._read_pipe, daemon=True)
            self._reader_thread.start()
        return self._write_fd

    @property
    def value(self) -> str:
        with self._lock:
            return self._value

    def close(self) -> None:
        if self._write_fd is not None:
            os.close(self._write_fd)
            self._write_fd = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1)
            self._reader_thread = None

    def _read_pipe(self) -> None:
        read_fd = self._read_fd
        if read_fd is None:
            return
        try:
            while chunk := os.read(read_fd, 4096):
                self._append(chunk.decode("utf-8", errors="replace"))
        finally:
            os.close(read_fd)
            self._read_fd = None

    def _append(self, value: str) -> None:
        redacted = self._redactor.redact_text(value)
        with self._lock:
            merged = (self._value + redacted).encode("utf-8")[-_STDERR_BUFFER_BYTES:]
            self._value = merged.decode("utf-8", errors="ignore")


class McpTransportFactory:
    """按配置惰性构造 SDK transport，不在构造阶段启动连接。"""

    def __init__(self, config: McpServerConfig, redactor: SecretRedactor) -> None:
        self.config = config
        self.stderr = RedactingStderrSink(redactor)
        self._http_client: httpx2.AsyncClient | None = None
        self._stdio_parameters: StdioServerParameters | None = None

    @property
    def http_client(self) -> httpx2.AsyncClient | None:
        return self._http_client

    @property
    def stdio_parameters(self) -> StdioServerParameters | None:
        return self._stdio_parameters

    def create(self) -> AbstractAsyncContextManager[object]:
        if isinstance(self.config, StdioMcpServerConfig):
            parameters = StdioServerParameters(
                command=self.config.command,
                args=list(self.config.args),
                env={name: value.get_secret_value() for name, value in self.config.env.items()},
            )
            self._stdio_parameters = parameters
            return stdio_client(parameters, errlog=self.stderr)

        return streamable_http_client(self.config.url, http_client=self._get_http_client())

    async def close(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
        self.stderr.close()

    def _get_http_client(self) -> httpx2.AsyncClient:
        if self._http_client is None:
            if not isinstance(self.config, HttpMcpServerConfig):
                raise TypeError("HTTP transport 需要 HTTP MCP 配置")
            self._http_client = httpx2.AsyncClient(
                headers={
                    name: value.get_secret_value() for name, value in self.config.headers.items()
                },
                follow_redirects=False,
                timeout=httpx2.Timeout(
                    connect=self.config.startup_timeout_seconds,
                    read=(self.config.tool_timeout_seconds + _HTTP_READ_TIMEOUT_GRACE_SECONDS),
                    write=self.config.tool_timeout_seconds,
                    pool=self.config.startup_timeout_seconds,
                ),
            )
        return self._http_client


class McpConnection:
    """在单个所有权任务中维护 MCP Client 与 transport 上下文。"""

    def __init__(self, config: McpServerConfig, redactor: SecretRedactor) -> None:
        self.config = config
        self._redactor = redactor
        self._transport_factory = McpTransportFactory(config, redactor)
        self._state = McpConnectionState.STARTING
        self._last_error: McpErrorSummary | None = None
        self._discovery: McpDiscoveryResult | None = None
        self._owner_task: asyncio.Task[None] | None = None
        self._client: Client | None = None
        self._inflight: set[asyncio.Task[object]] = set()
        self._startup_complete = asyncio.Event()
        self._close_requested = asyncio.Event()
        self._close_task: asyncio.Task[None] | None = None
        self._reconnect_lock = asyncio.Lock()

    @property
    def state(self) -> McpConnectionState:
        return self._state

    @property
    def status(self) -> McpServerStatus:
        return McpServerStatus(
            name=self.config.name,
            transport=self.config.transport,
            state=self._state,
            tool_count=len(self._discovery.tools) if self._discovery else 0,
            last_error=self._last_error,
        )

    async def start(self) -> McpDiscoveryResult:
        if self._owner_task is None:
            self._state = McpConnectionState.STARTING
            self._owner_task = asyncio.create_task(self._run_owner(discover=True))
        await self._startup_complete.wait()
        if self._discovery is not None:
            return self._discovery
        return McpDiscoveryResult(self.config.name, "unknown", ())

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await self._close_task

    async def call_tool(self, remote_name: str, arguments: dict[str, object]) -> object:
        """仅向 READY Client 发送一次远端调用。"""

        if self._state is McpConnectionState.DISCONNECTED:
            await self._reconnect()
            if self._state is McpConnectionState.DISCONNECTED:
                raise ToolError("mcp_connection_error", "MCP 连接重建失败。")
        if self._state is not McpConnectionState.READY or self._client is None:
            raise ToolError("mcp_unavailable", "MCP Server 当前不可用。")
        task = asyncio.create_task(self._client.call_tool(remote_name, arguments))
        self._inflight.add(task)
        try:
            async with asyncio.timeout(self.config.tool_timeout_seconds):
                return await task
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except TimeoutError as error:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise ToolError("mcp_timeout", "MCP 工具调用超时。") from error
        except ToolError:
            raise
        except MCPError as error:
            if error.code == _MCP_CONNECTION_CLOSED:
                self._mark_disconnected()
                raise ToolError("mcp_connection_error", "MCP 连接已断开。") from error
            if error.code == _MCP_REQUEST_TIMEOUT:
                raise ToolError("mcp_timeout", "MCP 工具调用超时。") from error
            raise ToolError("mcp_protocol_error", "MCP 协议拒绝了工具调用。") from error
        except Exception as error:
            self._mark_disconnected()
            raise ToolError("mcp_connection_error", "MCP 连接已断开。") from error
        finally:
            self._inflight.discard(task)

    async def _run_owner(self, *, discover: bool) -> None:
        stack = AsyncExitStack()
        try:
            async with asyncio.timeout(self.config.startup_timeout_seconds):
                client = await stack.enter_async_context(
                    Client(self._transport_factory.create(), mode="auto")
                )
                self._client = client
                protocol_version = str(client.protocol_version or "unknown")
                if discover:
                    self._discovery = await self._discover(client, protocol_version)
            self._state = McpConnectionState.READY
            self._last_error = None
            self._startup_complete.set()
            await self._close_requested.wait()
        except asyncio.CancelledError:
            if not self._close_requested.is_set():
                self._mark_disconnected()
        except Exception:
            self._last_error = McpErrorSummary(
                "mcp_startup_error", self._redactor.redact_text("MCP Server 启动失败。")
            )
            self._state = (
                McpConnectionState.DISCONNECTED if not discover else McpConnectionState.UNAVAILABLE
            )
            self._startup_complete.set()
        finally:
            self._client = None
            self._startup_complete.set()
            try:
                await stack.aclose()
            except BaseException:
                if self._state not in {
                    McpConnectionState.CLOSING,
                    McpConnectionState.CLOSED,
                }:
                    self._mark_disconnected()

    async def _reconnect(self) -> None:
        async with self._reconnect_lock:
            if self._state is not McpConnectionState.DISCONNECTED:
                return
            if self._owner_task is not None:
                await self._owner_task
            if self._close_task is not None:
                return
            self._state = McpConnectionState.RECONNECTING
            self._close_requested = asyncio.Event()
            self._startup_complete = asyncio.Event()
            self._owner_task = asyncio.create_task(self._run_owner(discover=False))
            await self._startup_complete.wait()

    async def _discover(self, client: Client, protocol_version: str) -> McpDiscoveryResult:
        remote_tools: list[object] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await client.list_tools(cursor=cursor)
            remote_tools.extend(page.tools)
            cursor = page.next_cursor
            if cursor is None:
                break
            if cursor in seen_cursors:
                raise RuntimeError("MCP tools/list 返回重复 cursor")
            seen_cursors.add(cursor)

        mapped, naming_issues = map_tool_names(
            self.config.name, [tool.name for tool in remote_tools]
        )
        public_by_remote = {item.remote_name: item.public_name for item in mapped}
        descriptors: list[McpToolDescriptor] = []
        issues = [McpErrorSummary("invalid_tool_name", issue.message) for issue in naming_issues]
        for tool in remote_tools:
            public_name = public_by_remote.get(tool.name)
            if public_name is None:
                continue
            try:
                descriptors.append(
                    McpToolDescriptor(
                        public_name=public_name,
                        server_name=self.config.name,
                        remote_name=tool.name,
                        description=tool.description or f"MCP 工具：{tool.name}",
                        arguments=JsonSchemaToolArguments(tool.input_schema),
                    )
                )
            except (TypeError, ValueError) as error:
                del error
                issues.append(
                    McpErrorSummary(
                        "invalid_tool_schema",
                        f"工具 {public_name} 的 Schema 无效。",
                    )
                )
        return McpDiscoveryResult(
            self.config.name, protocol_version, tuple(descriptors), tuple(issues)
        )

    async def _close(self) -> None:
        if self._state is not McpConnectionState.CLOSED:
            self._state = McpConnectionState.CLOSING
            self._close_requested.set()
            for task in tuple(self._inflight):
                task.cancel()
            if self._inflight:
                await asyncio.gather(*self._inflight, return_exceptions=True)
            if self._owner_task is not None:
                await self._owner_task
            await self._transport_factory.close()
            self._state = McpConnectionState.CLOSED

    def _mark_disconnected(self) -> None:
        self._state = McpConnectionState.DISCONNECTED
        self._last_error = McpErrorSummary("mcp_connection_error", "MCP 连接已断开。")
        self._close_requested.set()
