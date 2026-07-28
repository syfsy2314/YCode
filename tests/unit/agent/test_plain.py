import asyncio

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.agent import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentMode,
    AgentTermination,
    AgentTextDelta,
    FinalResponseEvent,
    PlainChatRunner,
)
from ycode.core import ChatMessage, StopReason, StreamEnd, TextDelta
from ycode.errors import ProviderError


def text_response(text: str):
    return [TextDelta(0, text), StreamEnd(StopReason.END_TURN)]


@pytest.mark.asyncio
async def test_plain_runner_wraps_single_provider_response() -> None:
    provider = FakeProvider([text_response("answer")])
    runner = PlainChatRunner(provider)
    user = ChatMessage.user_text("question")
    turn = runner.start_turn((), user, AgentMode.AGENT)

    events = [event async for event in turn]

    assert events == [
        AgentTextDelta(1, 0, "answer"),
        FinalResponseEvent(ChatMessage.assistant_text("answer")),
    ]
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.COMPLETED
    assert provider.system_prompts == [""]
    assert provider.tool_definitions == [()]


@pytest.mark.asyncio
async def test_plain_runner_converts_provider_error_to_agent_event() -> None:
    provider = FakeProvider([[ProviderError("network", "safe error", True)]])
    turn = PlainChatRunner(provider).start_turn(
        (),
        ChatMessage.user_text("question"),
        AgentMode.AGENT,
    )

    events = [event async for event in turn]

    assert events == [AgentErrorEvent("network", "safe error")]
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.ERROR


@pytest.mark.asyncio
async def test_plain_runner_user_cancel_produces_cancel_event() -> None:
    provider = FakeProvider([text_response("late")], delay=10)
    turn = PlainChatRunner(provider).start_turn(
        (),
        ChatMessage.user_text("question"),
        AgentMode.AGENT,
    )
    next_event = asyncio.create_task(anext(turn))
    await asyncio.sleep(0)

    turn.cancel()

    assert isinstance(await next_event, AgentCancelledEvent)
    with pytest.raises(StopAsyncIteration):
        await anext(turn)
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.CANCELLED
