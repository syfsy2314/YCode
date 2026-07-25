"""可编排的虚拟 Provider。"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from ycode.core.events import StreamEvent
from ycode.core.messages import ChatMessage

FakeItem = StreamEvent | BaseException


class FakeProvider:
    def __init__(self, turns: Sequence[Sequence[FakeItem]], *, delay: float = 0.0) -> None:
        self._turns = [list(turn) for turn in turns]
        self.delay = delay
        self.requests: list[tuple[ChatMessage, ...]] = []
        self.closed = False
        self.close_count = 0

    async def stream_chat(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamEvent]:
        self.requests.append(tuple(messages))
        if not self._turns:
            raise AssertionError("FakeProvider 没有剩余的预设轮次")
        for item in self._turns.pop(0):
            if self.delay:
                await asyncio.sleep(self.delay)
            if isinstance(item, BaseException):
                raise item
            yield item

    async def close(self) -> None:
        self.close_count += 1
        self.closed = True
