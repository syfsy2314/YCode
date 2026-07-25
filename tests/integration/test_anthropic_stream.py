import time

import pytest

from tests.support.sse_server import SSETestServer, StreamResponse, sse_event
from ycode.config.models import ProviderConfig
from ycode.core import (
    StopReason,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingBlock,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    thaw_json,
)
from ycode.core.messages import ChatMessage
from ycode.errors import ProviderError
from ycode.providers.anthropic import AnthropicProvider
from ycode.session.assembler import ResponseAssembler
from ycode.session.chat import ChatSession


def config(server: SSETestServer, *, thinking: bool = False) -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "name": "claude-local",
            "protocol": "anthropic",
            "model": "claude-test",
            "base_url": server.origin,
            "api_key": "anthropic-placeholder",
            "thinking": thinking,
        }
    )


def message_start() -> str:
    return sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": "claude-test",
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        },
    )


def text_delta(text: str, *, index: int = 0) -> str:
    return sse_event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        },
    )


def normal_events() -> list[str]:
    return [
        message_start(),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        text_delta("你"),
        text_delta("好"),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 2},
            },
        ),
        sse_event("message_stop", {"type": "message_stop"}),
    ]


def visible(events: list[StreamEvent]) -> list[tuple[type[object], str]]:
    return [
        (type(event), event.text if isinstance(event, TextDelta | ThinkingDelta) else "")
        for event in events
        if isinstance(event, TextDelta | ThinkingDelta | StreamEnd)
    ]


@pytest.mark.asyncio
async def test_official_sdk_streams_text_and_records_request(sse_server: SSETestServer) -> None:
    sse_server.enqueue(StreamResponse(events=normal_events(), delay=0.03))
    provider = AnthropicProvider(config(sse_server))
    seen: list[tuple[StreamEvent, float]] = []
    started = time.perf_counter()

    try:
        async for event in provider.stream_chat([ChatMessage.user_text("hello")]):
            seen.append((event, time.perf_counter() - started))
    finally:
        await provider.close()

    assert visible([event for event, _ in seen]) == [
        (TextDelta, "你"),
        (TextDelta, "好"),
        (StreamEnd, ""),
    ]
    assert seen[0][1] < seen[-1][1]
    request = sse_server.requests[0]
    assert request.path == "/v1/messages"
    assert request.headers["x-api-key"] == "anthropic-placeholder"
    assert request.json["model"] == "claude-test"
    assert request.json["messages"] == [{"role": "user", "content": "hello"}]
    assert request.json["stream"] is True
    assert request.json["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_official_sdk_streams_thinking_separately(sse_server: SSETestServer) -> None:
    events = [
        message_start(),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "分析"},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig-test"},
            },
        ),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        text_delta("答案", index=1),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 1}),
        sse_event("message_stop", {"type": "message_stop"}),
    ]
    sse_server.enqueue(StreamResponse(events=events))
    provider = AnthropicProvider(config(sse_server, thinking=True))
    try:
        result = [event async for event in provider.stream_chat([ChatMessage.user_text("why")])]
    finally:
        await provider.close()

    assert visible(result) == [
        (ThinkingDelta, "分析"),
        (TextDelta, "答案"),
        (StreamEnd, ""),
    ]
    assert ThinkingComplete(0, ThinkingBlock("分析", "sig-test")) in result
    assert sse_server.requests[0].json["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
    }


@pytest.mark.asyncio
async def test_disabled_thinking_filters_compatible_server_delta(
    sse_server: SSETestServer,
) -> None:
    events = [
        message_start(),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {
                    "type": "thinking_delta",
                    "thinking": "THINKING-MUST-NOT-APPEAR",
                },
            },
        ),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        text_delta("visible answer", index=1),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 1}),
        sse_event("message_stop", {"type": "message_stop"}),
    ]
    sse_server.enqueue(StreamResponse(events=events))
    provider = AnthropicProvider(config(sse_server, thinking=False))
    try:
        result = [event async for event in provider.stream_chat([ChatMessage.user_text("why")])]
    finally:
        await provider.close()

    assert visible(result) == [
        (TextDelta, "visible answer"),
        (StreamEnd, ""),
    ]
    assert sse_server.requests[0].json["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_official_sdk_streams_two_tool_calls_into_structured_message(
    sse_server: SSETestServer,
) -> None:
    events = [
        message_start(),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read",
                    "input": {},
                },
            },
        ),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "call-2",
                    "name": "write",
                    "input": {},
                },
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
            },
        ),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '"a.py"}'},
            },
        ),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '"b.py"}'},
            },
        ),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 1}),
        sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 4},
            },
        ),
        sse_event("message_stop", {"type": "message_stop"}),
    ]
    sse_server.enqueue(StreamResponse(events=events))
    provider = AnthropicProvider(config(sse_server))
    assembler = ResponseAssembler()
    result: list[StreamEvent] = []
    try:
        async for event in provider.stream_chat([ChatMessage.user_text("work")]):
            result.append(event)
            assembler.consume(event)
    finally:
        await provider.close()

    message = assembler.finish()
    tools = message.blocks(ToolCallBlock)
    assert [(tool.id, tool.name, thaw_json(tool.arguments)) for tool in tools] == [
        ("call-1", "read", {"path": "a.py"}),
        ("call-2", "write", {"path": "b.py"}),
    ]
    assert [type(event) for event in result] == [
        ToolCallStart,
        ToolCallStart,
        ToolCallDelta,
        ToolCallDelta,
        ToolCallDelta,
        ToolCallComplete,
        ToolCallDelta,
        ToolCallComplete,
        StreamEnd,
    ]
    assert isinstance(result[-1], StreamEnd)
    assert result[-1].stop_reason is StopReason.TOOL_USE


@pytest.mark.asyncio
async def test_interleaved_text_is_visible_in_arrival_order_and_stored_by_index(
    sse_server: SSETestServer,
) -> None:
    events = [
        message_start(),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        text_delta("one-", index=1),
        sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        text_delta("zero-", index=0),
        text_delta("end", index=1),
        text_delta("end", index=0),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 1}),
        sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
        sse_event("message_stop", {"type": "message_stop"}),
    ]
    sse_server.enqueue(StreamResponse(events=events, delay=0.01))
    session = ChatSession(AnthropicProvider(config(sse_server)))

    try:
        result = [event async for event in session.stream_reply("hello")]
    finally:
        await session.close()

    assert [event.text for event in result if isinstance(event, TextDelta)] == [
        "one-",
        "zero-",
        "end",
        "end",
    ]
    assert [block.text for block in session.history[-1].content] == [
        "zero-end",
        "one-end",
    ]


@pytest.mark.asyncio
async def test_authentication_error_is_mapped_without_key(sse_server: SSETestServer) -> None:
    sse_server.enqueue(
        StreamResponse(
            status=401,
            error_body={
                "type": "error",
                "error": {"type": "authentication_error", "message": "bad key"},
            },
        )
    )
    provider = AnthropicProvider(config(sse_server))
    try:
        with pytest.raises(ProviderError) as caught:
            [event async for event in provider.stream_chat([ChatMessage.user_text("hello")])]
    finally:
        await provider.close()
    assert caught.value.code == "authentication"
    assert "anthropic-placeholder" not in str(caught.value)


@pytest.mark.asyncio
async def test_missing_message_stop_is_stream_error(sse_server: SSETestServer) -> None:
    sse_server.enqueue(StreamResponse(events=normal_events()[:4]))
    provider = AnthropicProvider(config(sse_server))
    try:
        with pytest.raises(ProviderError, match="意外结束"):
            [event async for event in provider.stream_chat([ChatMessage.user_text("hello")])]
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type", "expected_code"),
    [(429, "rate_limit_error", "rate_limit"), (500, "api_error", "server")],
)
async def test_status_errors_are_mapped(
    sse_server: SSETestServer, status: int, error_type: str, expected_code: str
) -> None:
    sse_server.enqueue(
        StreamResponse(
            status=status,
            error_body={"type": "error", "error": {"type": error_type, "message": "test"}},
        )
    )
    provider = AnthropicProvider(config(sse_server))
    try:
        with pytest.raises(ProviderError) as caught:
            [event async for event in provider.stream_chat([ChatMessage.user_text("hello")])]
    finally:
        await provider.close()
    assert caught.value.code == expected_code
    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_connection_failure_is_network_error() -> None:
    unavailable = ProviderConfig.model_validate(
        {
            "name": "offline",
            "protocol": "anthropic",
            "model": "claude-test",
            "base_url": "http://127.0.0.1:1",
            "api_key": "placeholder",
        }
    )
    provider = AnthropicProvider(unavailable)
    try:
        with pytest.raises(ProviderError) as caught:
            [event async for event in provider.stream_chat([ChatMessage.user_text("hello")])]
    finally:
        await provider.close()
    assert caught.value.code == "network"
    assert caught.value.retryable is True
