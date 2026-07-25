import asyncio

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.core import (
    StopReason,
    StreamEnd,
    StreamEvent,
    TextDelta,
)
from ycode.errors import ProviderError
from ycode.session.chat import ChatSession


def text_response(*parts: str) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    events.extend(TextDelta(0, part) for part in parts)
    events.append(StreamEnd(StopReason.END_TURN))
    return events


async def collect(session: ChatSession, prompt: str) -> list[StreamEvent]:
    return [event async for event in session.stream_reply(prompt)]


@pytest.mark.asyncio
async def test_success_commits_complete_turn() -> None:
    provider = FakeProvider([text_response("hello ", "world")])
    session = ChatSession(provider)

    events = await collect(session, "hi")

    assert isinstance(events[-1], StreamEnd)
    assert [(item.role, item.text) for item in session.history] == [
        ("user", "hi"),
        ("assistant", "hello world"),
    ]


@pytest.mark.asyncio
async def test_multi_turn_request_contains_successful_history() -> None:
    provider = FakeProvider([text_response("first"), text_response("second")])
    session = ChatSession(provider)

    await collect(session, "one")
    await collect(session, "two")

    assert [(item.role, item.text) for item in provider.requests[1]] == [
        ("user", "one"),
        ("assistant", "first"),
        ("user", "two"),
    ]


@pytest.mark.asyncio
async def test_provider_error_rolls_back_partial_turn_and_can_retry() -> None:
    error = ProviderError("stream", "连接中断。", retryable=True)
    partial = [TextDelta(0, "partial"), error]
    provider = FakeProvider([partial, text_response("ok")])
    session = ChatSession(provider)

    with pytest.raises(ProviderError, match="连接中断"):
        await collect(session, "failed")
    assert session.history == ()

    await collect(session, "retry")
    assert [item.text for item in provider.requests[1]] == ["retry"]


@pytest.mark.asyncio
async def test_stream_without_completed_is_failure() -> None:
    events = [TextDelta(0, "partial")]
    session = ChatSession(FakeProvider([events]))
    with pytest.raises(ProviderError, match="结构无效"):
        await collect(session, "hello")
    assert session.history == ()


@pytest.mark.asyncio
async def test_event_after_completed_rolls_back() -> None:
    events = [*text_response("complete"), TextDelta(0, "illegal")]
    session = ChatSession(FakeProvider([events]))
    with pytest.raises(ProviderError, match="结构无效"):
        await collect(session, "hello")
    assert session.history == ()


@pytest.mark.asyncio
async def test_stopping_iteration_early_rolls_back() -> None:
    session = ChatSession(FakeProvider([text_response("first", "second")]))
    stream = session.stream_reply("hello")

    assert await anext(stream) == TextDelta(0, "first")
    await stream.aclose()

    assert session.history == ()


@pytest.mark.asyncio
async def test_cancelled_turn_rolls_back() -> None:
    provider = FakeProvider([text_response("first", "second")], delay=0.05)
    session = ChatSession(provider)
    task = asyncio.create_task(collect(session, "hello"))

    await asyncio.sleep(0)
    assert provider.requests
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert session.history == ()


@pytest.mark.asyncio
async def test_blank_input_never_calls_provider() -> None:
    provider = FakeProvider([])
    session = ChatSession(provider)
    with pytest.raises(ValueError, match="不能为空"):
        await collect(session, "   ")
    assert provider.requests == []


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    provider = FakeProvider([])
    session = ChatSession(provider)
    await session.close()
    await session.close()
    assert provider.closed is True
    assert provider.close_count == 1
