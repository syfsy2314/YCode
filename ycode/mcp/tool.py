"""MCP 工具结果的安全转换。"""

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from mcp.types import (
    AudioContent,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
)

from ycode.config.environment import SecretRedactor
from ycode.core.messages import FrozenJsonObject, freeze_json, thaw_json
from ycode.mcp.models import McpToolDescriptor
from ycode.tools.contracts import ToolAccess, ToolContext, ToolDefinition, ToolExecutionResult
from ycode.tools.errors import ToolError

if TYPE_CHECKING:
    from ycode.mcp.connection import McpConnection


def convert_call_tool_result(
    result: CallToolResult, redactor: SecretRedactor
) -> ToolExecutionResult:
    """将官方 SDK 调用结果转换为不含二进制正文的工具结果。"""

    parts = [_content_summary(content) for content in result.content]
    parts = [part for part in parts if part]
    metadata: dict[str, object] = {}
    if result.structured_content is not None:
        try:
            structured = freeze_json(result.structured_content)
        except TypeError:
            parts.append("[MCP structured_content: 不支持的非 JSON 内容]")
        else:
            redacted = redactor.redact_json(structured)
            metadata["structured_content"] = redacted
            parts.append(
                "structured_content:\n"
                + json.dumps(thaw_json(redacted), ensure_ascii=False, sort_keys=True, indent=2)
            )
    content = "\n".join(parts) if parts else "MCP 工具未返回可见内容。"
    return ToolExecutionResult(
        content=redactor.redact_text(content),
        is_error=result.is_error,
        metadata={**metadata, **({"error_code": "mcp_tool_error"} if result.is_error else {})},
    )


def _content_summary(content: object) -> str:
    if isinstance(content, TextContent):
        return content.text
    if isinstance(content, ImageContent):
        return f"[MCP image: {content.mime_type}]"
    if isinstance(content, AudioContent):
        return f"[MCP audio: {content.mime_type}]"
    if isinstance(content, ResourceLink):
        return _resource_summary("resource_link", content.mime_type, content.uri)
    if isinstance(content, EmbeddedResource):
        resource = content.resource
        return _resource_summary("resource", resource.mime_type, resource.uri)
    content_type = getattr(content, "type", type(content).__name__)
    return f"[MCP unsupported content: {content_type}]"


def _resource_summary(kind: str, mime_type: str | None, uri: str) -> str:
    mime = mime_type or "unknown"
    return f"[MCP {kind}: {mime}; {uri}]"


class MCPToolWrapper:
    """将启动时发现的远端工具包装为延迟加载的本地工具。"""

    def __init__(
        self,
        descriptor: McpToolDescriptor,
        connection: "McpConnection",
        redactor: SecretRedactor,
    ) -> None:
        self._descriptor = descriptor
        self._connection = connection
        self._redactor = redactor
        self.definition = ToolDefinition(
            name=descriptor.public_name,
            description=descriptor.description,
            access=ToolAccess.UNKNOWN,
            arguments=descriptor.arguments,
            defer_loading=True,
            timeout_error_code="mcp_timeout",
        )
        self.timeout_seconds = connection.config.tool_timeout_seconds

    async def execute(
        self, arguments: FrozenJsonObject, context: ToolContext
    ) -> ToolExecutionResult:
        del context
        raw_arguments = thaw_json(arguments)
        if not isinstance(raw_arguments, Mapping):
            raise ToolError("invalid_arguments", "MCP 工具参数必须是 JSON object。")
        result = await self._connection.call_tool(self._descriptor.remote_name, dict(raw_arguments))
        if not isinstance(result, CallToolResult):
            raise ToolError("mcp_invalid_result", "MCP Server 返回了无效的工具结果。")
        return convert_call_tool_result(result, self._redactor)
