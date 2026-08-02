import pytest
from mcp.types import AudioContent, CallToolResult, ImageContent, TextContent

from ycode.config.environment import SecretRedactor
from ycode.core.messages import thaw_json
from ycode.mcp.models import McpToolDescriptor
from ycode.mcp.tool import convert_call_tool_result
from ycode.tools.arguments import JsonSchemaToolArguments
from ycode.tools.contracts import ToolAccess, ToolContext


def test_converter_keeps_text_and_structured_json_with_redaction() -> None:
    redactor = SecretRedactor()
    redactor.add("secret-token")

    converted = convert_call_tool_result(
        CallToolResult(
            content=[TextContent(text="first secret-token"), TextContent(text="second")],
            structuredContent={"token": "secret-token", "count": 1},
        ),
        redactor,
    )

    assert converted.content.startswith("first [REDACTED]\nsecond")
    assert "secret-token" not in converted.content
    assert thaw_json(converted.metadata["structured_content"]) == {
        "token": "[REDACTED]",
        "count": 1,
    }


def test_converter_summarizes_binary_content_without_base64() -> None:
    converted = convert_call_tool_result(
        CallToolResult(
            content=[
                ImageContent(data="base64-image-do-not-return", mimeType="image/png"),
                AudioContent(data="base64-audio-do-not-return", mimeType="audio/mpeg"),
            ],
            isError=True,
        ),
        SecretRedactor(),
    )

    assert converted.is_error
    assert "image/png" in converted.content
    assert "audio/mpeg" in converted.content
    assert "base64" not in converted.content
    assert converted.metadata["error_code"] == "mcp_tool_error"


def test_converter_handles_empty_visible_content() -> None:
    converted = convert_call_tool_result(CallToolResult(content=[]), SecretRedactor())

    assert converted.content == "MCP 工具未返回可见内容。"


async def test_wrapper_uses_remote_name_and_is_unknown_deferred(tmp_path) -> None:
    from mcp.types import CallToolResult, TextContent

    from ycode.mcp.tool import MCPToolWrapper

    calls = []

    class FakeConnection:
        class config:
            tool_timeout_seconds = 60.0

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return CallToolResult(content=[TextContent(text="ok")])

    descriptor = McpToolDescriptor(
        "mcp_server_public",
        "server",
        "RemoteName",
        "description",
        JsonSchemaToolArguments({"type": "object"}),
    )
    tool = MCPToolWrapper(descriptor, FakeConnection(), SecretRedactor())

    result = await tool.execute({}, ToolContext(workspace=tmp_path))

    assert tool.definition.access is ToolAccess.UNKNOWN
    assert tool.definition.defer_loading is True
    assert tool.definition.timeout_error_code == "mcp_timeout"
    assert calls == [("RemoteName", {})]
    assert result.content == "ok"


async def test_wrapper_rejects_non_call_tool_result(tmp_path) -> None:
    from ycode.mcp.tool import MCPToolWrapper
    from ycode.tools.errors import ToolError

    class FakeConnection:
        class config:
            tool_timeout_seconds = 60.0

        async def call_tool(self, name, arguments):
            del name, arguments
            return {"invalid": "result"}

    descriptor = McpToolDescriptor(
        "mcp_server_public",
        "server",
        "RemoteName",
        "description",
        JsonSchemaToolArguments({"type": "object"}),
    )

    with pytest.raises(ToolError) as caught:
        await MCPToolWrapper(descriptor, FakeConnection(), SecretRedactor()).execute(
            {}, ToolContext(workspace=tmp_path)
        )
    assert caught.value.code == "mcp_invalid_result"
