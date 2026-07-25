from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ycode.config.models import ProviderConfig
from ycode.core import (
    ChatMessage,
    StopReason,
    StreamEnd,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ycode.errors import ProviderError
from ycode.providers.openai import OpenAIProvider


class AsyncChunks:
    def __init__(self, *chunks: object) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[object]:
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


def config() -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "name": "openai",
            "protocol": "openai",
            "model": "gpt-test",
            "base_url": "http://localhost:9000/v1",
            "api_key": "openai-secret",
        }
    )


def tool_delta(
    index: int,
    *,
    tool_id: str = "",
    name: str = "",
    arguments: str = "",
) -> object:
    return SimpleNamespace(
        index=index,
        id=tool_id or None,
        function=SimpleNamespace(name=name or None, arguments=arguments or None),
    )


def chunk(
    content: object = None,
    finish_reason: str | None = None,
    *,
    tool_calls: list[object] | None = None,
    choice_index: int = 0,
) -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=choice_index,
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ]
    )


def client_with(*chunks: object) -> SimpleNamespace:
    create = AsyncMock(return_value=AsyncChunks(*chunks))
    chat = SimpleNamespace(completions=SimpleNamespace(create=create))
    return SimpleNamespace(chat=chat, close=AsyncMock())


@pytest.mark.asyncio
async def test_text_stream_maps_request_and_events() -> None:
    client = client_with(chunk("hello"), chunk(None), chunk(" world"), chunk(None, "stop"))
    provider = OpenAIProvider(config(), client=client)
    history = [ChatMessage.user_text("hi"), ChatMessage.assistant_text("old")]

    events = [event async for event in provider.stream_chat(history)]

    assert events == [
        TextDelta(0, "hello"),
        TextDelta(0, " world"),
        StreamEnd(StopReason.END_TURN, "stop"),
    ]
    assert client.chat.completions.create.await_args.kwargs == {
        "model": "gpt-test",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "old"},
        ],
        "stream": True,
    }
    assert provider.client is client
    assert not hasattr(provider, "_client")


@pytest.mark.asyncio
async def test_parallel_tool_calls_keep_independent_indices() -> None:
    client = client_with(
        chunk(tool_calls=[tool_delta(0, tool_id="call-1", name="re")]),
        chunk(
            tool_calls=[
                tool_delta(1, tool_id="call-2", name="write", arguments='{"p":'),
                tool_delta(0, name="ad", arguments='{"path":'),
            ]
        ),
        chunk(
            tool_calls=[
                tool_delta(0, arguments='"a.py"}'),
                tool_delta(1, arguments='"b.py"}'),
            ]
        ),
        chunk(finish_reason="tool_calls"),
    )
    provider = OpenAIProvider(config(), client=client)

    events = [event async for event in provider.stream_chat([ChatMessage.user_text("work")])]

    assert events == [
        ToolCallStart(1, "call-1", "read"),
        ToolCallDelta(1, '{"path":'),
        ToolCallDelta(1, '"a.py"}'),
        ToolCallComplete(1, ToolCallBlock("call-1", "read", {"path": "a.py"})),
        ToolCallStart(2, "call-2", "write"),
        ToolCallDelta(2, '{"p":'),
        ToolCallDelta(2, '"b.py"}'),
        ToolCallComplete(2, ToolCallBlock("call-2", "write", {"p": "b.py"})),
        StreamEnd(StopReason.TOOL_USE, "tool_calls"),
    ]


def test_structured_history_converts_to_openai_messages() -> None:
    history = [
        ChatMessage(
            "assistant",
            (
                TextBlock("checking"),
                ToolCallBlock("call-1", "read", {"path": "a.py"}),
            ),
        ),
        ChatMessage("user", (ToolResultBlock("call-1", "contents"), TextBlock("continue"))),
    ]

    assert OpenAIProvider._messages(history) == [
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path":"a.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "contents"},
        {"role": "user", "content": "continue"},
    ]


def test_thinking_history_is_rejected() -> None:
    history = [ChatMessage("assistant", (ThinkingBlock("reason", "sig"),))]
    with pytest.raises(ProviderError, match="Thinking"):
        OpenAIProvider._messages(history)


@pytest.mark.asyncio
async def test_multiple_effective_choices_are_rejected() -> None:
    mixed = SimpleNamespace(
        choices=[
            SimpleNamespace(
                index=0,
                delta=SimpleNamespace(content="a", tool_calls=None),
                finish_reason=None,
            ),
            SimpleNamespace(
                index=1,
                delta=SimpleNamespace(content="b", tool_calls=None),
                finish_reason=None,
            ),
        ]
    )
    provider = OpenAIProvider(config(), client=client_with(mixed))
    with pytest.raises(ProviderError, match="多个有效 choice"):
        [event async for event in provider.stream_chat([ChatMessage.user_text("hi")])]


@pytest.mark.asyncio
async def test_unexpected_stream_error_is_safe() -> None:
    provider = OpenAIProvider(
        config(), client=client_with(RuntimeError("openai-secret leaked response"))
    )
    with pytest.raises(ProviderError) as caught:
        [event async for event in provider.stream_chat([ChatMessage.user_text("hi")])]
    assert caught.value.code == "stream"
    assert "openai-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    client = client_with()
    provider = OpenAIProvider(config(), client=client)
    await provider.close()
    await provider.close()
    client.close.assert_awaited_once()
