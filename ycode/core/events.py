"""供应商无关的语义流事件。"""

from dataclasses import dataclass
from enum import StrEnum

from ycode.core.messages import RedactedThinkingBlock, ThinkingBlock, ToolCallBlock


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


def _validate_index(index: int) -> None:
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("内容块索引必须是非负整数")


def _validate_delta(value: str) -> None:
    if not value:
        raise ValueError("增量内容不能为空")


@dataclass(frozen=True, slots=True)
class TextDelta:
    index: int
    text: str

    def __post_init__(self) -> None:
        _validate_index(self.index)
        _validate_delta(self.text)


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    index: int
    text: str

    def __post_init__(self) -> None:
        _validate_index(self.index)
        _validate_delta(self.text)


@dataclass(frozen=True, slots=True)
class ThinkingComplete:
    index: int
    block: ThinkingBlock | RedactedThinkingBlock

    def __post_init__(self) -> None:
        _validate_index(self.index)
        if not isinstance(self.block, ThinkingBlock | RedactedThinkingBlock):
            raise TypeError("Thinking 完成事件必须携带完整 Thinking 块")


@dataclass(frozen=True, slots=True)
class ToolCallStart:
    index: int
    id: str
    name: str

    def __post_init__(self) -> None:
        _validate_index(self.index)
        if not self.id:
            raise ValueError("工具调用 ID 不能为空")
        if not self.name:
            raise ValueError("工具名称不能为空")


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    index: int
    arguments_delta: str

    def __post_init__(self) -> None:
        _validate_index(self.index)
        _validate_delta(self.arguments_delta)


@dataclass(frozen=True, slots=True)
class ToolCallComplete:
    index: int
    block: ToolCallBlock

    def __post_init__(self) -> None:
        _validate_index(self.index)
        if not isinstance(self.block, ToolCallBlock):
            raise TypeError("工具完成事件必须携带完整 ToolCallBlock")


@dataclass(frozen=True, slots=True)
class StreamEnd:
    stop_reason: StopReason
    provider_reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.stop_reason, StopReason):
            raise TypeError("响应结束事件必须携带 StopReason")


type StreamEvent = (
    TextDelta
    | ThinkingDelta
    | ThinkingComplete
    | ToolCallStart
    | ToolCallDelta
    | ToolCallComplete
    | StreamEnd
)
