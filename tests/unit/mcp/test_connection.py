import asyncio

import pytest

from ycode.config.environment import SecretRedactor
from ycode.config.mcp import HttpMcpServerConfig, StdioMcpServerConfig
from ycode.mcp.connection import McpTransportFactory, RedactingStderrSink
from ycode.mcp.models import McpConnectionState
from ycode.tools.errors import ToolError


def test_stdio_transport_uses_configured_command_arguments_and_minimal_environment() -> None:
    config = StdioMcpServerConfig.model_validate(
        {
            "name": "local",
            "transport": "stdio",
            "command": "python",
            "args": ["-m", "server"],
            "env": {"TOKEN": "secret"},
        }
    )
    factory = McpTransportFactory(config, SecretRedactor())

    factory.create()
    parameters = factory.stdio_parameters

    assert parameters is not None
    assert parameters.command == "python"
    assert parameters.args == ["-m", "server"]
    assert parameters.env == {"TOKEN": "secret"}


@pytest.mark.asyncio
async def test_http_transport_uses_headers_timeouts_and_no_redirects() -> None:
    config = HttpMcpServerConfig.model_validate(
        {
            "name": "remote",
            "transport": "streamable_http",
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer secret"},
            "startup_timeout_seconds": 3,
            "tool_timeout_seconds": 7,
        }
    )
    factory = McpTransportFactory(config, SecretRedactor())

    factory.create()
    client = factory.http_client

    assert client is not None
    assert client.headers["Authorization"] == "Bearer secret"
    assert client.follow_redirects is False
    assert client.timeout.connect == 3
    assert client.timeout.read == 8
    await factory.close()


def test_stderr_sink_is_bounded_and_redacted() -> None:
    redactor = SecretRedactor()
    redactor.add("secret")
    sink = RedactingStderrSink(redactor)

    sink.write("server secret diagnostics")
    sink.write("x" * 9000)

    assert "secret" not in sink.value
    assert "[REDACTED]" not in sink.value
    assert len(sink.value.encode("utf-8")) <= 8 * 1024


@pytest.mark.asyncio
async def test_connection_keeps_client_open_until_idempotent_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = 0
    exited = 0

    class FakeClient:
        protocol_version = "2026-07-28"

        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "FakeClient":
            nonlocal entered
            entered += 1
            return self

        async def __aexit__(self, *args: object) -> None:
            nonlocal exited
            del args
            exited += 1

        async def list_tools(self, **kwargs: object) -> object:
            del kwargs
            from mcp.types import ListToolsResult

            return ListToolsResult(tools=[])

    monkeypatch.setattr("ycode.mcp.connection.Client", FakeClient)
    from ycode.mcp.connection import McpConnection

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )

    discovery = await connection.start()
    assert discovery.protocol_version == "2026-07-28"
    assert connection.state is McpConnectionState.READY

    await connection.close()
    await connection.close()

    assert entered == exited == 1
    assert connection.state is McpConnectionState.CLOSED


@pytest.mark.asyncio
async def test_ready_connection_is_not_limited_by_startup_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        protocol_version = "2026-07-28"

        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def list_tools(self, **kwargs: object) -> object:
            del kwargs
            from mcp.types import ListToolsResult

            return ListToolsResult(tools=[])

    monkeypatch.setattr("ycode.mcp.connection.Client", FakeClient)
    from ycode.mcp.connection import McpConnection

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {
                "name": "local",
                "transport": "stdio",
                "command": "python",
                "startup_timeout_seconds": 0.01,
            }
        ),
        SecretRedactor(),
    )

    await connection.start()
    await asyncio.sleep(0.03)

    assert connection.state is McpConnectionState.READY
    await connection.close()


@pytest.mark.asyncio
async def test_connection_converts_initial_failure_to_unavailable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "FailingClient":
            raise RuntimeError("secret transport failure")

        async def __aexit__(self, *args: object) -> None:
            del args

    monkeypatch.setattr("ycode.mcp.connection.Client", FailingClient)
    from ycode.mcp.connection import McpConnection

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )

    discovery = await connection.start()

    assert discovery.tools == ()
    assert connection.status.state is McpConnectionState.UNAVAILABLE
    assert connection.status.last_error is not None
    assert "secret" not in connection.status.last_error.message


@pytest.mark.asyncio
async def test_startup_timeout_covers_client_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "SlowClient":
            await asyncio.sleep(1)
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

    monkeypatch.setattr("ycode.mcp.connection.Client", SlowClient)
    from ycode.mcp.connection import McpConnection

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {
                "name": "local",
                "transport": "stdio",
                "command": "python",
                "startup_timeout_seconds": 0.03,
            }
        ),
        SecretRedactor(),
    )
    started = asyncio.get_running_loop().time()

    discovery = await connection.start()

    assert asyncio.get_running_loop().time() - started < 0.2
    assert discovery.tools == ()
    assert connection.state is McpConnectionState.UNAVAILABLE
    await connection.close()


@pytest.mark.asyncio
async def test_close_cancels_connection_still_entering_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    class BlockingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "BlockingClient":
            entered.set()
            await asyncio.Event().wait()
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

    monkeypatch.setattr("ycode.mcp.connection.Client", BlockingClient)
    from ycode.mcp.connection import McpConnection

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {
                "name": "local",
                "transport": "stdio",
                "command": "python",
                "startup_timeout_seconds": 10,
            }
        ),
        SecretRedactor(),
    )
    start_task = asyncio.create_task(connection.start())
    await entered.wait()

    await asyncio.wait_for(connection.close(), timeout=0.2)
    await start_task

    assert connection.state is McpConnectionState.CLOSED


@pytest.mark.asyncio
async def test_discovery_collects_pages_and_keeps_invalid_schema_as_issue() -> None:
    from mcp.types import ListToolsResult, Tool

    from ycode.mcp.connection import McpConnection

    class PagedClient:
        async def list_tools(self, *, cursor: str | None = None) -> ListToolsResult:
            if cursor is None:
                return ListToolsResult(
                    tools=[
                        Tool(name="ReadFile", description="read", inputSchema={"type": "object"})
                    ],
                    nextCursor="next",
                )
            return ListToolsResult(
                tools=[
                    Tool(name="broken", description="bad", inputSchema={"$ref": "https://bad.test"})
                ]
            )

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )

    discovery = await connection._discover(PagedClient(), "2026-07-28")  # type: ignore[arg-type]

    assert [tool.public_name for tool in discovery.tools] == ["mcp_local_read_file"]
    assert [issue.code for issue in discovery.issues] == ["invalid_tool_schema"]


@pytest.mark.asyncio
async def test_discovery_rejects_repeated_cursor_without_partial_result() -> None:
    from mcp.types import ListToolsResult, Tool

    from ycode.mcp.connection import McpConnection

    class RepeatedCursorClient:
        async def list_tools(self, *, cursor: str | None = None) -> ListToolsResult:
            del cursor
            return ListToolsResult(
                tools=[Tool(name="echo", inputSchema={"type": "object"})],
                nextCursor="same",
            )

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )

    with pytest.raises(RuntimeError, match="重复 cursor"):
        await connection._discover(RepeatedCursorClient(), "2026-07-28")  # type: ignore[arg-type]
    assert connection.status.tool_count == 0


@pytest.mark.asyncio
async def test_call_tool_sends_once_and_propagates_cancellation() -> None:
    from mcp.types import CallToolResult

    from ycode.mcp.connection import McpConnection

    started = asyncio.Event()
    cancelled = asyncio.Event()
    calls: list[tuple[str, dict[str, object]]] = []

    class SlowClient:
        async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult:
            calls.append((name, arguments))
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return CallToolResult(content=[])

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )
    connection._state = McpConnectionState.READY
    connection._client = SlowClient()  # type: ignore[assignment]
    task = asyncio.create_task(connection.call_tool("remote_name", {"value": "one"}))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == [("remote_name", {"value": "one"})]
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_call_tool_rejects_when_not_ready() -> None:
    from ycode.mcp.connection import McpConnection

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )

    with pytest.raises(ToolError, match="不可用") as caught:
        await connection.call_tool("remote_name", {})
    assert caught.value.code == "mcp_unavailable"


@pytest.mark.asyncio
async def test_call_tool_classifies_protocol_error_without_disconnect() -> None:
    from mcp.shared.exceptions import MCPError

    from ycode.mcp.connection import McpConnection

    class ProtocolErrorClient:
        async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            del name, arguments
            raise MCPError(-32602, "invalid params")

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )
    connection._state = McpConnectionState.READY
    connection._client = ProtocolErrorClient()  # type: ignore[assignment]

    with pytest.raises(ToolError) as caught:
        await connection.call_tool("echo", {})
    assert caught.value.code == "mcp_protocol_error"
    assert connection.state is McpConnectionState.READY


@pytest.mark.asyncio
async def test_reconnect_happens_only_on_next_call_without_rediscovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mcp.types import CallToolResult, ListToolsResult

    from ycode.mcp.connection import McpConnection

    entered = 0
    list_calls = 0
    tool_calls = 0

    class SequencedClient:
        protocol_version = "2026-07-28"

        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            nonlocal entered
            entered += 1
            self.sequence = entered

        async def __aenter__(self) -> "SequencedClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def list_tools(self, **kwargs: object) -> ListToolsResult:
            del kwargs
            nonlocal list_calls
            list_calls += 1
            return ListToolsResult(tools=[])

        async def call_tool(self, name: str, arguments: dict[str, object]) -> CallToolResult:
            del name, arguments
            nonlocal tool_calls
            tool_calls += 1
            if self.sequence == 1:
                raise RuntimeError("transport dropped")
            return CallToolResult(content=[])

    monkeypatch.setattr("ycode.mcp.connection.Client", SequencedClient)
    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )
    await connection.start()

    with pytest.raises(ToolError) as caught:
        await connection.call_tool("echo", {})
    assert caught.value.code == "mcp_connection_error"
    assert entered == 1
    assert tool_calls == 1

    result = await connection.call_tool("echo", {})

    assert isinstance(result, CallToolResult)
    assert entered == 2
    assert tool_calls == 2
    assert list_calls == 1
    await connection.close()


@pytest.mark.asyncio
async def test_close_prevents_reconnect() -> None:
    from ycode.mcp.connection import McpConnection

    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )
    connection._state = McpConnectionState.DISCONNECTED
    await connection.close()

    with pytest.raises(ToolError) as caught:
        await connection.call_tool("echo", {})
    assert caught.value.code == "mcp_unavailable"


@pytest.mark.asyncio
async def test_reconnect_failure_remains_disconnected_and_can_try_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ycode.mcp.connection import McpConnection

    entered = 0

    class FailingClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "FailingClient":
            nonlocal entered
            entered += 1
            raise OSError("offline")

        async def __aexit__(self, *args: object) -> None:
            del args

    monkeypatch.setattr("ycode.mcp.connection.Client", FailingClient)
    connection = McpConnection(
        StdioMcpServerConfig.model_validate(
            {"name": "local", "transport": "stdio", "command": "python"}
        ),
        SecretRedactor(),
    )
    connection._state = McpConnectionState.DISCONNECTED

    for _ in range(2):
        with pytest.raises(ToolError) as caught:
            await connection.call_tool("echo", {})
        assert caught.value.code == "mcp_connection_error"
        assert connection.state is McpConnectionState.DISCONNECTED
    assert entered == 2
    await connection.close()
