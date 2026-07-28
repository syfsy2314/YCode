"""Agent 对上层暴露的供应商无关事件。"""

from dataclasses import dataclass

from ycode.agent.contracts import AgentMode
from ycode.core.messages import ChatMessage, ToolCallBlock
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


type AgentEvent = (
    UserMessageEvent
    | AgentThinkingDelta
    | AgentTextDelta
    | ToolExecutionStarted
    | ToolExecutionCompleted
    | ToolExecutionCancelled
    | ModeChangedEvent
    | FinalResponseEvent
    | AgentLimitReachedEvent
    | AgentCancelledEvent
    | AgentErrorEvent
)
