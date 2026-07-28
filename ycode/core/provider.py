"""Provider 的统一接口。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ycode.core.events import StreamEvent
from ycode.core.messages import ChatMessage

if TYPE_CHECKING:
    from ycode.tools.contracts import ToolDefinition


@runtime_checkable
class ChatProvider(Protocol):
    """把统一消息适配到具体大模型协议。"""

    def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamEvent]: ...

    async def close(self) -> None: ...


@runtime_checkable
class AgentChatProvider(ChatProvider, Protocol):
    """支持 system prompt 和工具定义的单次模型请求接口。"""

    def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        system_prompt: str = "",
        tools: Sequence[ToolDefinition[Any]] = (),
    ) -> AsyncIterator[StreamEvent]: ...
