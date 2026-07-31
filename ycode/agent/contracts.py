"""Agent 状态、运行结果和可取消事件流。"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ycode.core.events import TokenUsage
from ycode.core.messages import ChatMessage
from ycode.security.models import ApprovalChoice


class AgentMode(StrEnum):
    AGENT = "agent"
    PLAN_ONLY = "plan-only"


class AgentTermination(StrEnum):
    COMPLETED = "completed"
    LIMIT_REACHED = "limit_reached"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    termination: AgentTermination
    messages: tuple[ChatMessage, ...]
    final_message: ChatMessage | None = None
    error_code: str = ""
    error_message: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)

    def __post_init__(self) -> None:
        if not isinstance(self.termination, AgentTermination):
            raise TypeError("Agent 终止状态无效")
        messages = tuple(self.messages)
        if any(not isinstance(message, ChatMessage) for message in messages):
            raise TypeError("Agent 回合结果只能包含 ChatMessage")
        object.__setattr__(self, "messages", messages)
        if not isinstance(self.usage, TokenUsage):
            raise TypeError("Agent 回合结果必须携带 TokenUsage")

        if self.termination is AgentTermination.COMPLETED:
            if self.final_message is None or self.final_message.role != "assistant":
                raise ValueError("正常完成必须携带最终 Assistant 消息")
            if not messages or messages[-1] != self.final_message:
                raise ValueError("最终回复必须是本轮消息的最后一条")
        elif self.final_message is not None:
            raise ValueError("非正常完成不能携带最终回复")

        if self.termination is AgentTermination.ERROR:
            if not self.error_code or not self.error_message:
                raise ValueError("Agent 异常必须携带错误码和消息")


@runtime_checkable
class AgentTurn(Protocol):
    def __aiter__(self) -> AsyncIterator["AgentEvent"]: ...
    async def __anext__(self) -> "AgentEvent": ...

    @property
    def result(self) -> AgentTurnResult | None: ...

    def cancel(self) -> None: ...
    def submit_approval(self, choice: ApprovalChoice) -> None: ...


@runtime_checkable
class ConversationRunner(Protocol):
    supported_modes: frozenset[AgentMode]

    def start_turn(
        self,
        history: Sequence[ChatMessage],
        user_message: ChatMessage,
        mode: AgentMode,
    ) -> AgentTurn: ...

    async def close(self) -> None: ...


class AgentTurnStream:
    """把运行器生产函数包装为可取消的 AgentEvent 异步迭代器。"""

    def __init__(
        self,
        producer: Callable[["AgentTurnStream"], AsyncIterator["AgentEvent"]],
    ) -> None:
        self._iterator = producer(self)
        self._stored_result: AgentTurnResult | None = None
        self._active_task: asyncio.Future[object] | None = None
        self._cancel_requested = False
        self._exhausted = False
        self._approval: asyncio.Future[ApprovalChoice] | None = None

    def __aiter__(self) -> "AgentTurnStream":
        return self

    async def __anext__(self) -> "AgentEvent":
        try:
            return await anext(self._iterator)
        except StopAsyncIteration:
            self._exhausted = True
            if self._stored_result is None:
                raise RuntimeError("AgentTurn 结束时缺少结果") from None
            raise

    @property
    def result(self) -> AgentTurnResult | None:
        return self._stored_result if self._exhausted else None

    @property
    def cancellation_requested(self) -> bool:
        return self._cancel_requested

    def cancel(self) -> None:
        if self._exhausted:
            return
        self._cancel_requested = True
        if self._active_task is not None:
            self._active_task.cancel()
        if self._approval is not None:
            approval = self._approval
            self._approval = None
            approval.cancel()

    @property
    def approval_pending(self) -> bool:
        return self._approval is not None and not self._approval.done()

    def begin_approval(self) -> None:
        if self._approval is not None:
            raise RuntimeError("同一时刻只能等待一个工具审批")
        self._approval = asyncio.get_running_loop().create_future()

    def submit_approval(self, choice: ApprovalChoice) -> None:
        if not isinstance(choice, ApprovalChoice):
            raise TypeError("工具审批选择无效")
        if self._approval is None or self._approval.done():
            raise RuntimeError("当前没有等待中的工具审批")
        self._approval.set_result(choice)

    async def consume_approval(self) -> ApprovalChoice:
        approval = self._approval
        if approval is None:
            raise RuntimeError("当前没有等待中的工具审批")
        try:
            return await approval
        finally:
            if self._approval is approval:
                self._approval = None

    def complete(self, result: AgentTurnResult) -> None:
        if self._stored_result is not None:
            raise RuntimeError("AgentTurn 结果不能重复设置")
        self._stored_result = result

    async def run_child[ResultT](self, awaitable: Awaitable[ResultT]) -> ResultT:
        task = asyncio.ensure_future(awaitable)
        self._active_task = task
        if self._cancel_requested:
            task.cancel()
        try:
            return await task
        finally:
            if self._active_task is task:
                self._active_task = None


from ycode.agent.events import AgentEvent  # noqa: E402
