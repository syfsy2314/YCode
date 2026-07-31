"""可编排的虚拟 Provider。"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from ycode.core.events import StreamEvent
from ycode.core.messages import ChatMessage
from ycode.core.provider import AgentModelRequest

FakeItem = StreamEvent | BaseException


class FakeProvider:
    def __init__(self, turns: Sequence[Sequence[FakeItem]], *, delay: float = 0.0) -> None:
        self._turns = [list(turn) for turn in turns]
        self.delay = delay
        self.requests: list[tuple[ChatMessage, ...]] = []
        self.agent_requests: list[AgentModelRequest] = []
        self.system_prompts: list[str] = []
        self.tool_definitions: list[tuple[Any, ...]] = []
        self.request_started = asyncio.Event()
        self.closed = False
        self.close_count = 0

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
    ) -> AsyncIterator[StreamEvent]:
        self.requests.append(tuple(messages))
        self.system_prompts.append("")
        self.tool_definitions.append(())
        async for item in self._stream_next():
            yield item

    async def stream_agent(
        self,
        request: AgentModelRequest,
    ) -> AsyncIterator[StreamEvent]:
        self.agent_requests.append(request)
        self.requests.append(request.messages)
        self.system_prompts.append("\n\n".join(request.system_prompt))
        self.tool_definitions.append(request.tools)
        async for item in self._stream_next():
            yield item

    async def _stream_next(self) -> AsyncIterator[StreamEvent]:
        self.request_started.set()
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
