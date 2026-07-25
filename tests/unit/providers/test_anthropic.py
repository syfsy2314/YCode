from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ycode.config.models import ProviderConfig
from ycode.core import (
    ChatMessage,
    RedactedThinkingBlock,
    StopReason,
    StreamEnd,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ycode.errors import ProviderError
from ycode.providers.anthropic import MAX_TOKENS, AnthropicProvider


class AsyncEvents:
    def __init__(self, *events: object) -> None:
        self.events = events

    async def __aiter__(self) -> AsyncIterator[object]:
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event


def config(*, thinking: bool = False) -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "name": "claude",
            "protocol": "anthropic",
            "model": "claude-test",
            "base_url": "http://localhost:9000/v1",
            "api_key": "anthropic-secret",
            "thinking": thinking,
        }
    )


def event(event_type: str, **values: object) -> object:
    return SimpleNamespace(type=event_type, **values)


def block_start(index: int, block_type: str, **values: object) -> object:
    return event(
        "content_block_start",
        index=index,
        content_block=SimpleNamespace(type=block_type, **values),
    )


def delta(index: int, delta_type: str, **values: object) -> object:
    return event(
        "content_block_delta",
        index=index,
        delta=SimpleNamespace(type=delta_type, **values),
    )


def completed(reason: str = "end_turn") -> tuple[object, object]:
    return (
        event("message_delta", delta=SimpleNamespace(stop_reason=reason)),
        event("message_stop"),
    )


def client_with(*events: object) -> SimpleNamespace:
    create = AsyncMock(return_value=AsyncEvents(*events))
    return SimpleNamespace(messages=SimpleNamespace(create=create), close=AsyncMock())


@pytest.mark.asyncio
async def test_text_stream_maps_request_and_events() -> None:
    client = client_with(
        event("message_start"),
        block_start(0, "text", text=""),
        delta(0, "text_delta", text="hello"),
        delta(0, "text_delta", text=" world"),
        event("content_block_stop", index=0),
        *completed(),
    )
    provider = AnthropicProvider(config(), client=client)

    events = [event async for event in provider.stream_chat([ChatMessage.user_text("hi")])]

    assert events == [
        TextDelta(0, "hello"),
        TextDelta(0, " world"),
        StreamEnd(StopReason.END_TURN, "end_turn"),
    ]
    assert client.messages.create.await_args.kwargs == {
        "model": "claude-test",
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "thinking": {"type": "disabled"},
    }
    assert provider.client is client
    assert not hasattr(provider, "_client")


@pytest.mark.asyncio
async def test_thinking_signature_and_tool_call_are_structured() -> None:
    client = client_with(
        event("message_start"),
        block_start(0, "thinking", thinking="", signature=""),
        delta(0, "thinking_delta", thinking="reason"),
        delta(0, "signature_delta", signature="sig"),
        event("content_block_stop", index=0),
        block_start(1, "tool_use", id="call-1", name="read", input={}),
        delta(1, "input_json_delta", partial_json='{"path":'),
        delta(1, "input_json_delta", partial_json='"a.py"}'),
        event("content_block_stop", index=1),
        *completed("tool_use"),
    )
    provider = AnthropicProvider(config(thinking=True), client=client)

    events = [event async for event in provider.stream_chat([ChatMessage.user_text("hi")])]

    assert events == [
        ThinkingDelta(0, "reason"),
        ThinkingComplete(0, ThinkingBlock("reason", "sig")),
        ToolCallStart(1, "call-1", "read"),
        ToolCallDelta(1, '{"path":'),
        ToolCallDelta(1, '"a.py"}'),
        ToolCallComplete(1, ToolCallBlock("call-1", "read", {"path": "a.py"})),
        StreamEnd(StopReason.TOOL_USE, "tool_use"),
    ]
    assert client.messages.create.await_args.kwargs["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }


@pytest.mark.asyncio
async def test_disabled_thinking_block_is_filtered_as_a_whole() -> None:
    client = client_with(
        event("message_start"),
        block_start(0, "thinking", thinking="", signature=""),
        delta(0, "thinking_delta", thinking="must-not-appear"),
        event("content_block_stop", index=0),
        block_start(1, "text", text=""),
        delta(1, "text_delta", text="answer"),
        event("content_block_stop", index=1),
        *completed(),
    )
    provider = AnthropicProvider(config(thinking=False), client=client)

    events = [event async for event in provider.stream_chat([ChatMessage.user_text("hi")])]

    assert events == [
        TextDelta(1, "answer"),
        StreamEnd(StopReason.END_TURN, "end_turn"),
    ]


@pytest.mark.asyncio
async def test_redacted_thinking_is_preserved() -> None:
    client = client_with(
        event("message_start"),
        block_start(0, "redacted_thinking", data="encrypted-data"),
        event("content_block_stop", index=0),
        *completed(),
    )
    provider = AnthropicProvider(config(thinking=True), client=client)

    events = [event async for event in provider.stream_chat([ChatMessage.user_text("hi")])]

    assert events == [
        ThinkingComplete(0, RedactedThinkingBlock("encrypted-data")),
        StreamEnd(StopReason.END_TURN, "end_turn"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_json", ['{"secret":"must-not-leak"', "[]"])
async def test_invalid_tool_arguments_are_rejected_safely(partial_json: str) -> None:
    client = client_with(
        event("message_start"),
        block_start(0, "tool_use", id="call-1", name="read", input={}),
        delta(0, "input_json_delta", partial_json=partial_json),
        event("content_block_stop", index=0),
    )
    provider = AnthropicProvider(config(), client=client)

    with pytest.raises(ProviderError, match="工具参数") as caught:
        [event async for event in provider.stream_chat([ChatMessage.user_text("hi")])]

    assert "must-not-leak" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_id", "name"),
    [("", "read"), ("call-1", "")],
)
async def test_empty_tool_identity_is_rejected(tool_id: str, name: str) -> None:
    client = client_with(
        event("message_start"),
        block_start(0, "tool_use", id=tool_id, name=name, input={}),
    )
    provider = AnthropicProvider(config(), client=client)

    with pytest.raises(ProviderError, match="工具调用标识"):
        [event async for event in provider.stream_chat([ChatMessage.user_text("hi")])]


def test_structured_history_converts_to_anthropic_blocks() -> None:
    history = [
        ChatMessage.user_text("read"),
        ChatMessage(
            "assistant",
            (
                ThinkingBlock("reason", "sig"),
                TextBlock("checking"),
                ToolCallBlock("call-1", "read", {"path": "a.py"}),
            ),
        ),
        ChatMessage("user", (ToolResultBlock("call-1", "contents"), TextBlock("continue"))),
    ]

    assert AnthropicProvider._messages(history) == [
        {"role": "user", "content": "read"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "reason", "signature": "sig"},
                {"type": "text", "text": "checking"},
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read",
                    "input": {"path": "a.py"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "contents"},
                {"type": "text", "text": "continue"},
            ],
        },
    ]


@pytest.mark.asyncio
async def test_unexpected_stream_error_is_safe() -> None:
    client = client_with(RuntimeError("anthropic-secret leaked response"))
    provider = AnthropicProvider(config(), client=client)

    with pytest.raises(ProviderError) as caught:
        [event async for event in provider.stream_chat([ChatMessage.user_text("hi")])]
    assert caught.value.code == "stream"
    assert "anthropic-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    client = client_with()
    provider = AnthropicProvider(config(), client=client)
    await provider.close()
    await provider.close()
    client.close.assert_awaited_once()
