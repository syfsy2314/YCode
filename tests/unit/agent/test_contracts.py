import asyncio
from dataclasses import FrozenInstanceError

import pytest

from ycode.agent import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentMode,
    AgentTermination,
    AgentTextDelta,
    AgentTurn,
    AgentTurnResult,
    AgentTurnStream,
    FinalResponseEvent,
    ModeChangedEvent,
)
from ycode.core.messages import ChatMessage


def test_agent_events_are_frozen_and_validate_fields() -> None:
    event = AgentTextDelta(round_number=1, index=0, text="hello")
    with pytest.raises(FrozenInstanceError):
        event.text = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="轮次"):
        AgentTextDelta(round_number=0, index=0, text="hello")
    with pytest.raises(ValueError, match="不能为空"):
        AgentErrorEvent(code="", message="error")

    mode = ModeChangedEvent(AgentMode.AGENT, AgentMode.PLAN_ONLY)
    assert mode.mode is AgentMode.PLAN_ONLY


def test_completed_result_requires_final_message_at_end() -> None:
    user = ChatMessage.user_text("question")
    final = ChatMessage.assistant_text("answer")
    result = AgentTurnResult(
        termination=AgentTermination.COMPLETED,
        messages=(user, final),
        final_message=final,
    )

    assert result.final_message is final
    with pytest.raises(ValueError, match="最终"):
        AgentTurnResult(
            termination=AgentTermination.COMPLETED,
            messages=(user,),
            final_message=final,
        )
    with pytest.raises(ValueError, match="错误码"):
        AgentTurnResult(termination=AgentTermination.ERROR, messages=())


@pytest.mark.asyncio
async def test_turn_result_is_visible_only_after_stream_exhaustion() -> None:
    final = ChatMessage.assistant_text("done")

    async def producer(turn: AgentTurnStream):
        turn.complete(
            AgentTurnResult(
                termination=AgentTermination.COMPLETED,
                messages=(final,),
                final_message=final,
            )
        )
        yield FinalResponseEvent(final)

    turn = AgentTurnStream(producer)
    assert isinstance(turn, AgentTurn)
    assert turn.result is None
    assert isinstance(await anext(turn), FinalResponseEvent)
    assert turn.result is None
    with pytest.raises(StopAsyncIteration):
        await anext(turn)
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.COMPLETED


@pytest.mark.asyncio
async def test_turn_cancel_interrupts_child_and_can_finish_with_cancel_event() -> None:
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def child() -> None:
        try:
            started.set()
            await asyncio.sleep(10)
        finally:
            cleaned.set()

    async def producer(turn: AgentTurnStream):
        try:
            await turn.run_child(child())
        except asyncio.CancelledError:
            turn.complete(
                AgentTurnResult(
                    termination=AgentTermination.CANCELLED,
                    messages=(),
                )
            )
            yield AgentCancelledEvent("已取消。")

    turn = AgentTurnStream(producer)
    next_event = asyncio.create_task(anext(turn))
    await started.wait()
    turn.cancel()

    assert isinstance(await next_event, AgentCancelledEvent)
    with pytest.raises(StopAsyncIteration):
        await anext(turn)
    assert cleaned.is_set()
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.CANCELLED
