"""Provider 的统一接口。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ycode.core.events import StreamEvent
from ycode.core.messages import ChatMessage

if TYPE_CHECKING:
    from ycode.tools.contracts import ToolDefinition


@dataclass(frozen=True, slots=True)
class AgentModelRequest:
    messages: tuple[ChatMessage, ...]
    system_prompt: tuple[str, ...] = ()
    supplements: tuple[str, ...] = ()
    tools: tuple[ToolDefinition[Any], ...] = ()

    def __post_init__(self) -> None:
        messages = tuple(self.messages)
        system_prompt = tuple(self.system_prompt)
        supplements = tuple(self.supplements)
        tools = tuple(self.tools)
        if not messages or any(not isinstance(message, ChatMessage) for message in messages):
            raise ValueError("Agent 模型请求必须包含 ChatMessage")
        if any(not isinstance(block, str) or not block.strip() for block in system_prompt):
            raise ValueError("System Prompt 内容块不能为空")
        if any(not isinstance(content, str) or not content.strip() for content in supplements):
            raise ValueError("系统补充内容不能为空")
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "system_prompt", system_prompt)
        object.__setattr__(self, "supplements", supplements)
        object.__setattr__(self, "tools", tools)


@runtime_checkable
class ChatProvider(Protocol):
    """把统一消息适配到具体大模型协议。"""

    def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamEvent]: ...

    async def close(self) -> None: ...


@runtime_checkable
class AgentChatProvider(ChatProvider, Protocol):
    """支持 system prompt 和工具定义的单次模型请求接口。"""

    def stream_agent(self, request: AgentModelRequest) -> AsyncIterator[StreamEvent]: ...
