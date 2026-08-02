"""MCP 发现目录与仅含安全信息的连接状态模型。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ycode.security.models import SecurityConfigWarning
from ycode.tools.arguments import JsonSchemaToolArguments


class McpConnectionState(StrEnum):
    DISABLED = "disabled"
    INVALID = "invalid"
    STARTING = "starting"
    READY = "ready"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"
    UNAVAILABLE = "unavailable"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class McpErrorSummary:
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("MCP 错误摘要必须包含错误码和消息")


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    public_name: str
    server_name: str
    remote_name: str
    description: str
    arguments: JsonSchemaToolArguments

    def __post_init__(self) -> None:
        if not self.public_name or not self.server_name or not self.remote_name:
            raise ValueError("MCP 工具描述必须包含名称")
        if not self.description.strip():
            raise ValueError("MCP 工具描述不能为空")


@dataclass(frozen=True, slots=True)
class McpDiscoveryResult:
    server_name: str
    protocol_version: str
    tools: tuple[McpToolDescriptor, ...]
    issues: tuple[McpErrorSummary, ...] = ()

    def __post_init__(self) -> None:
        if not self.server_name or not self.protocol_version:
            raise ValueError("MCP 发现结果必须包含 Server 名称和协议版本")


@dataclass(frozen=True, slots=True)
class McpServerStatus:
    name: str
    transport: str
    state: McpConnectionState
    tool_count: int
    last_error: McpErrorSummary | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.transport:
            raise ValueError("MCP Server 状态必须包含名称和传输方式")
        if self.tool_count < 0:
            raise ValueError("MCP 工具数量不能为负数")


@dataclass(frozen=True, slots=True)
class McpStatusReport:
    servers: tuple[McpServerStatus, ...]
    security_warnings: tuple[SecurityConfigWarning, ...] = ()

    @property
    def ready_count(self) -> int:
        return sum(server.state is McpConnectionState.READY for server in self.servers)

    @property
    def failed_count(self) -> int:
        failed_states = {
            McpConnectionState.DISCONNECTED,
            McpConnectionState.INVALID,
            McpConnectionState.UNAVAILABLE,
        }
        return sum(server.state in failed_states for server in self.servers)

    @property
    def disabled_count(self) -> int:
        return sum(server.state is McpConnectionState.DISABLED for server in self.servers)


@runtime_checkable
class McpStatusProvider(Protocol):
    def snapshot(self) -> McpStatusReport: ...
