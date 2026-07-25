import time

import pytest

from tests.support.sse_server import SSETestServer, StreamResponse, sse_event
from ycode.config.models import ProviderConfig
from ycode.core import (
    StopReason,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCallBlock,
    thaw_json,
)
from ycode.core.messages import ChatMessage
from ycode.errors import ProviderError
from ycode.providers.openai import OpenAIProvider
from ycode.session.assembler import ResponseAssembler


def config(server: SSETestServer) -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "name": "openai-local",
            "protocol": "openai",
            "model": "gpt-test",
            "base_url": server.base_url,
            "api_key": "openai-placeholder",
        }
    )


def chunk(content: str | None, finish_reason: str | None = None) -> str:
    return sse_event(
        None,
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "delta": {} if content is None else {"content": content},
                    "finish_reason": finish_reason,
                }
            ],
        },
    )


def normal_events() -> list[str]:
    return [chunk("你"), chunk("好"), chunk(None, "stop"), sse_event(None, "[DONE]")]


@pytest.mark.asyncio
async def test_official_sdk_streams_text_and_records_request(sse_server: SSETestServer) -> None:
    sse_server.enqueue(StreamResponse(events=normal_events(), delay=0.03))
    provider = OpenAIProvider(config(sse_server))
    seen: list[tuple[StreamEvent, float]] = []
    started = time.perf_counter()
    try:
        async for event in provider.stream_chat([ChatMessage.user_text("hello")]):
            seen.append((event, time.perf_counter() - started))
    finally:
        await provider.close()

    assert [
        (type(event), event.text if isinstance(event, TextDelta) else "") for event, _ in seen
    ] == [
        (TextDelta, "你"),
        (TextDelta, "好"),
        (StreamEnd, ""),
    ]
    assert seen[0][1] < seen[-1][1]
    request = sse_server.requests[0]
    assert request.path == "/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer openai-placeholder"
    assert request.json["model"] == "gpt-test"
    assert request.json["messages"] == [{"role": "user", "content": "hello"}]
    assert request.json["stream"] is True


@pytest.mark.asyncio
async def test_official_sdk_streams_parallel_tool_calls(sse_server: SSETestServer) -> None:
    def tool_chunk(tool_calls: list[dict[str, object]], finish_reason: str | None = None) -> str:
        return sse_event(
            None,
            {
                "id": "chatcmpl-tools",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"tool_calls": tool_calls},
                        "finish_reason": finish_reason,
                    }
                ],
            },
        )

    events = [
        tool_chunk(
            [
                {
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"path":'},
                }
            ]
        ),
        tool_chunk(
            [
                {
                    "index": 1,
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "write", "arguments": '{"path":'},
                },
                {"index": 0, "function": {"arguments": '"a.py"}'}},
            ]
        ),
        tool_chunk([{"index": 1, "function": {"arguments": '"b.py"}'}}]),
        chunk(None, "tool_calls"),
        sse_event(None, "[DONE]"),
    ]
    sse_server.enqueue(StreamResponse(events=events))
    provider = OpenAIProvider(config(sse_server))
    assembler = ResponseAssembler()
    completed: StreamEnd | None = None
    try:
        async for event in provider.stream_chat([ChatMessage.user_text("work")]):
            assembler.consume(event)
            if isinstance(event, StreamEnd):
                completed = event
    finally:
        await provider.close()

    tools = assembler.finish().blocks(ToolCallBlock)
    assert [(tool.id, tool.name, thaw_json(tool.arguments)) for tool in tools] == [
        ("call-1", "read", {"path": "a.py"}),
        ("call-2", "write", {"path": "b.py"}),
    ]
    assert completed is not None
    assert completed.stop_reason is StopReason.TOOL_USE


@pytest.mark.asyncio
async def test_authentication_error_is_mapped_without_key(sse_server: SSETestServer) -> None:
    sse_server.enqueue(
        StreamResponse(
            status=401,
            error_body={
                "error": {
                    "message": "bad key",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
        )
    )
    provider = OpenAIProvider(config(sse_server))
    try:
        with pytest.raises(ProviderError) as caught:
            [event async for event in provider.stream_chat([ChatMessage.user_text("hello")])]
    finally:
        await provider.close()
    assert caught.value.code == "authentication"
    assert "openai-placeholder" not in str(caught.value)


@pytest.mark.asyncio
async def test_missing_finish_reason_is_stream_error(sse_server: SSETestServer) -> None:
    sse_server.enqueue(StreamResponse(events=[chunk("partial")]))
    provider = OpenAIProvider(config(sse_server))
    try:
        with pytest.raises(ProviderError, match="意外结束"):
            [event async for event in provider.stream_chat([ChatMessage.user_text("hello")])]
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "expected_code"), [(429, "rate_limit"), (500, "server")])
async def test_status_errors_are_mapped(
    sse_server: SSETestServer, status: int, expected_code: str
) -> None:
    sse_server.enqueue(
        StreamResponse(
            status=status,
            error_body={"error": {"message": "test", "type": "api_error", "code": expected_code}},
        )
    )
    provider = OpenAIProvider(config(sse_server))
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
            "protocol": "openai",
            "model": "gpt-test",
            "base_url": "http://127.0.0.1:1/v1",
            "api_key": "placeholder",
        }
    )
    provider = OpenAIProvider(unavailable)
    try:
        with pytest.raises(ProviderError) as caught:
            [event async for event in provider.stream_chat([ChatMessage.user_text("hello")])]
    finally:
        await provider.close()
    assert caught.value.code == "network"
    assert caught.value.retryable is True
