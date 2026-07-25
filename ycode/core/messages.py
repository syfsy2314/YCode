"""供应商无关的结构化会话消息。"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, TypeVar, cast

ChatRole = Literal["user", "assistant"]
JsonScalar = str | int | float | bool | None
FrozenJson = JsonScalar | tuple["FrozenJson", ...] | Mapping[str, "FrozenJson"]
FrozenJsonObject = Mapping[str, FrozenJson]


def freeze_json(value: object) -> FrozenJson:
    """递归冻结 SDK 可接受的 JSON 数据。"""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        frozen = {str(key): freeze_json(item) for key, item in value.items()}
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(freeze_json(item) for item in value)
    raise TypeError(f"不支持的 JSON 值类型：{type(value).__name__}")


def thaw_json(value: FrozenJson) -> object:
    """把冻结 JSON 恢复为普通 dict/list。"""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingBlock:
    text: str
    signature: str = ""


@dataclass(frozen=True, slots=True)
class RedactedThinkingBlock:
    data: str

    def __post_init__(self) -> None:
        if not self.data:
            raise ValueError("Redacted Thinking 数据不能为空")


@dataclass(frozen=True, slots=True)
class ToolCallBlock:
    id: str
    name: str
    arguments: FrozenJsonObject

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("工具调用 ID 不能为空")
        if not self.name:
            raise ValueError("工具名称不能为空")
        frozen = freeze_json(self.arguments)
        if not isinstance(frozen, Mapping):
            raise TypeError("工具参数必须是 JSON object")
        object.__setattr__(self, "arguments", frozen)


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    tool_call_id: str
    content: str
    is_error: bool = False

    def __post_init__(self) -> None:
        if not self.tool_call_id:
            raise ValueError("工具结果对应的调用 ID 不能为空")


ContentBlock = TextBlock | ThinkingBlock | RedactedThinkingBlock | ToolCallBlock | ToolResultBlock
BlockT = TypeVar("BlockT", bound=ContentBlock)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """一条具有明确来源和有序内容块的消息。"""

    role: ChatRole
    content: tuple[ContentBlock, ...]

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ValueError(f"不支持的消息角色：{self.role}")
        content = tuple(self.content)
        if not content:
            raise ValueError("消息内容不能为空")

        user_types = (TextBlock, ToolResultBlock)
        assistant_types = (
            TextBlock,
            ThinkingBlock,
            RedactedThinkingBlock,
            ToolCallBlock,
        )
        allowed = user_types if self.role == "user" else assistant_types
        if any(not isinstance(block, allowed) for block in content):
            raise ValueError(f"{self.role} 消息包含不允许的内容块")
        object.__setattr__(self, "content", content)

    @classmethod
    def user_text(cls, text: str) -> "ChatMessage":
        return cls(role="user", content=(TextBlock(text),))

    @classmethod
    def assistant_text(cls, text: str) -> "ChatMessage":
        return cls(role="assistant", content=(TextBlock(text),))

    @property
    def text(self) -> str:
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    def blocks(self, block_type: type[BlockT]) -> tuple[BlockT, ...]:
        return cast(
            tuple[BlockT, ...],
            tuple(block for block in self.content if isinstance(block, block_type)),
        )
