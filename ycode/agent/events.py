"""Agent 对上层暴露的供应商无关事件。"""

from dataclasses import dataclass

from ycode.agent.contracts import AgentMode
from ycode.context.models import ContextCompactionReport, ContextFailureReport
from ycode.core.messages import ChatMessage, ToolCallBlock
from ycode.mcp.models import McpStatusReport
from ycode.security.models import PermissionDecision, PermissionMode
from ycode.tools.contracts import ToolExecutionRecord


def _validate_round(round_number: int) -> None:
    if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 1:
        raise ValueError("Agent 轮次必须是正整数")


def _validate_position(position: int) -> None:
    if not isinstance(position, int) or isinstance(position, bool) or position < 0:
        raise ValueError("工具位置必须是非负整数")


@dataclass(frozen=True, slots=True)
class UserMessageEvent:
    message: ChatMessage

    def __post_init__(self) -> None:
        if not isinstance(self.message, ChatMessage) or self.message.role != "user":
            raise TypeError("用户消息事件必须携带用户 ChatMessage")


@dataclass(frozen=True, slots=True)
class AgentThinkingDelta:
    round_number: int
    index: int
    text: str

    def __post_init__(self) -> None:
        _validate_round(self.round_number)
        _validate_position(self.index)
        if not self.text:
            raise ValueError("Thinking 增量不能为空")


@dataclass(frozen=True, slots=True)
class AgentTextDelta:
    round_number: int
    index: int
    text: str

    def __post_init__(self) -> None:
        _validate_round(self.round_number)
        _validate_position(self.index)
        if not self.text:
            raise ValueError("文本增量不能为空")


@dataclass(frozen=True, slots=True)
class ToolExecutionStarted:
    round_number: int
    position: int
    call: ToolCallBlock

    def __post_init__(self) -> None:
        _validate_round(self.round_number)
        _validate_position(self.position)
        if not isinstance(self.call, ToolCallBlock):
            raise TypeError("工具开始事件必须携带 ToolCallBlock")


@dataclass(frozen=True, slots=True)
class ToolApprovalRequested:
    round_number: int
    position: int
    decision: PermissionDecision

    def __post_init__(self) -> None:
        _validate_round(self.round_number)
        _validate_position(self.position)
        if not isinstance(self.decision, PermissionDecision):
            raise TypeError("工具审批事件必须携带权限决策")


@dataclass(frozen=True, slots=True)
class ToolExecutionCompleted:
    round_number: int
    record: ToolExecutionRecord

    def __post_init__(self) -> None:
        _validate_round(self.round_number)
        if not isinstance(self.record, ToolExecutionRecord):
            raise TypeError("工具完成事件必须携带 ToolExecutionRecord")


@dataclass(frozen=True, slots=True)
class ToolExecutionCancelled:
    round_number: int
    position: int
    call: ToolCallBlock

    def __post_init__(self) -> None:
        _validate_round(self.round_number)
        _validate_position(self.position)
        if not isinstance(self.call, ToolCallBlock):
            raise TypeError("工具取消事件必须携带 ToolCallBlock")


@dataclass(frozen=True, slots=True)
class ModeChangedEvent:
    previous_mode: AgentMode
    mode: AgentMode

    def __post_init__(self) -> None:
        if not isinstance(self.previous_mode, AgentMode) or not isinstance(self.mode, AgentMode):
            raise TypeError("模式事件必须携带 AgentMode")


@dataclass(frozen=True, slots=True)
class PermissionModeChangedEvent:
    previous_mode: PermissionMode
    mode: PermissionMode

    def __post_init__(self) -> None:
        if not isinstance(self.previous_mode, PermissionMode) or not isinstance(
            self.mode, PermissionMode
        ):
            raise TypeError("权限模式事件必须携带 PermissionMode")


@dataclass(frozen=True, slots=True)
class PermissionGrantsClearedEvent:
    cleared_count: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.cleared_count, int)
            or isinstance(self.cleared_count, bool)
            or self.cleared_count < 0
        ):
            raise ValueError("清除的会话授权数量必须是非负整数")


@dataclass(frozen=True, slots=True)
class FinalResponseEvent:
    message: ChatMessage

    def __post_init__(self) -> None:
        if not isinstance(self.message, ChatMessage) or self.message.role != "assistant":
            raise TypeError("最终回复事件必须携带 Assistant ChatMessage")


@dataclass(frozen=True, slots=True)
class AgentLimitReachedEvent:
    max_rounds: int
    message: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_rounds, int)
            or isinstance(self.max_rounds, bool)
            or self.max_rounds < 1
        ):
            raise ValueError("最大轮数必须是正整数")
        if not self.message:
            raise ValueError("轮数上限消息不能为空")


@dataclass(frozen=True, slots=True)
class AgentCancelledEvent:
    message: str

    def __post_init__(self) -> None:
        if not self.message:
            raise ValueError("取消消息不能为空")


@dataclass(frozen=True, slots=True)
class AgentErrorEvent:
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("Agent 错误码和消息不能为空")


@dataclass(frozen=True, slots=True)
class McpStatusEvent:
    report: McpStatusReport

    def __post_init__(self) -> None:
        if not isinstance(self.report, McpStatusReport):
            raise TypeError("MCP 状态事件必须携带 McpStatusReport")


@dataclass(frozen=True, slots=True)
class ContextCompactedEvent:
    report: ContextCompactionReport

    def __post_init__(self) -> None:
        if not isinstance(self.report, ContextCompactionReport):
            raise TypeError("上下文压缩事件必须携带压缩报告")


@dataclass(frozen=True, slots=True)
class ContextCompactionFailedEvent:
    report: ContextFailureReport

    def __post_init__(self) -> None:
        if not isinstance(self.report, ContextFailureReport):
            raise TypeError("上下文失败事件必须携带失败报告")


@dataclass(frozen=True, slots=True)
class ContextCompactionNotNeededEvent:
    code: str = "compact_not_needed"
    message: str = "当前没有可压缩的对话历史。"

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("无需压缩事件字段不能为空")


@dataclass(frozen=True, slots=True)
class SessionRestoredEvent:
    session_id: str
    message_count: int
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("恢复事件会话 ID 不能为空")
        if (
            not isinstance(self.message_count, int)
            or isinstance(self.message_count, bool)
            or self.message_count < 0
        ):
            raise ValueError("恢复事件消息数必须是非负整数")
        warnings = tuple(self.warnings)
        if any(not isinstance(item, str) or not item.strip() for item in warnings):
            raise ValueError("恢复事件警告必须是非空摘要")
        object.__setattr__(self, "warnings", warnings)


type AgentEvent = (
    UserMessageEvent
    | AgentThinkingDelta
    | AgentTextDelta
    | ToolApprovalRequested
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | ToolExecutionCancelled
    | ModeChangedEvent
    | PermissionModeChangedEvent
    | PermissionGrantsClearedEvent
    | FinalResponseEvent
    | AgentLimitReachedEvent
    | AgentCancelledEvent
    | AgentErrorEvent
    | McpStatusEvent
    | ContextCompactedEvent
    | ContextCompactionFailedEvent
    | ContextCompactionNotNeededEvent
    | SessionRestoredEvent
)
