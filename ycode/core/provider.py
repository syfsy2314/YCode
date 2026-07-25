"""Provider 的统一接口。"""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from ycode.core.events import StreamEvent
from ycode.core.messages import ChatMessage


@runtime_checkable
class ChatProvider(Protocol):
    """把统一消息适配到具体大模型协议。"""

    def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamEvent]: ...

    async def close(self) -> None: ...
