"""供应商无关的多轮会话。"""

from collections.abc import AsyncIterator

from ycode.core.events import StreamEvent
from ycode.core.messages import ChatMessage
from ycode.core.provider import ChatProvider
from ycode.errors import MessageAssemblyError, ProviderError
from ycode.session.assembler import ResponseAssembler


class ChatSession:
    def __init__(self, provider: ChatProvider) -> None:
        self._provider = provider
        self._history: list[ChatMessage] = []
        self._closed = False

    @property
    def history(self) -> tuple[ChatMessage, ...]:
        return tuple(self._history)

    async def stream_reply(self, user_text: str) -> AsyncIterator[StreamEvent]:
        if not user_text.strip():
            raise ValueError("消息不能为空")
        if self._closed:
            raise RuntimeError("会话已关闭")

        user_message = ChatMessage.user_text(user_text)
        request_messages = (*self._history, user_message)
        assembler = ResponseAssembler()

        try:
            async for event in self._provider.stream_chat(request_messages):
                assembler.consume(event)
                yield event
            assistant_message = assembler.finish()
        except MessageAssemblyError as error:
            raise ProviderError("stream", "响应流结构无效，请重试。", retryable=True) from error

        self._history.extend((user_message, assistant_message))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._provider.close()
