from dataclasses import FrozenInstanceError

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.core import (
    AgentModelRequest,
    ChatMessage,
    ChatProvider,
    RedactedThinkingBlock,
    StopReason,
    StreamEnd,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingComplete,
    ThinkingDelta,
    TokenUsage,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    freeze_json,
    thaw_json,
)
from ycode.errors import ConfigError, ProviderError, UIError


def test_message_and_events_are_immutable() -> None:
    message = ChatMessage.user_text("hello")
    event = TextDelta(0, "hi")

    with pytest.raises(FrozenInstanceError):
        message.content = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        event.text = "changed"  # type: ignore[misc]


def test_message_preserves_order_and_derives_views() -> None:
    message = ChatMessage(
        role="assistant",
        content=(
            ThinkingBlock("reason", "signature"),
            TextBlock("first"),
            ToolCallBlock("call-1", "read", {"path": "a.py"}),
            TextBlock("second"),
        ),
    )

    assert message.text == "firstsecond"
    assert [block.id for block in message.blocks(ToolCallBlock)] == ["call-1"]
    assert not hasattr(message, "tool_uses")


def test_message_role_rejects_incompatible_blocks() -> None:
    with pytest.raises(ValueError, match="不允许"):
        ChatMessage(role="assistant", content=(ToolResultBlock("call-1", "result"),))
    with pytest.raises(ValueError, match="不允许"):
        ChatMessage(role="user", content=(ThinkingBlock("reason"),))
    with pytest.raises(ValueError, match="不能为空"):
        ChatMessage(role="user", content=())


def test_frozen_json_round_trip_is_isolated() -> None:
    source = {"items": [{"value": 1}], "enabled": True}
    frozen = freeze_json(source)
    source["items"][0]["value"] = 2  # type: ignore[index]

    assert thaw_json(frozen) == {"items": [{"value": 1}], "enabled": True}
    thawed = thaw_json(frozen)
    assert isinstance(thawed, dict)
    thawed["enabled"] = False
    assert thaw_json(frozen)["enabled"] is True  # type: ignore[index]


def test_tool_call_freezes_constructor_arguments() -> None:
    arguments = {"path": ["a.py"]}
    block = ToolCallBlock("call-1", "read", arguments)
    arguments["path"].append("b.py")

    assert thaw_json(block.arguments) == {"path": ["a.py"]}
    with pytest.raises(TypeError):
        block.arguments["other"] = "value"  # type: ignore[index]


def test_stream_end_keeps_only_stop_reasons() -> None:
    event = StreamEnd(StopReason.TOOL_USE, "tool_calls")
    assert event.stop_reason is StopReason.TOOL_USE
    assert event.provider_reason == "tool_calls"
    assert not hasattr(event, "message")
    assert not hasattr(event, "content")
    assert not hasattr(event, "kind")
    assert event.usage == TokenUsage()


def test_token_usage_adds_provider_neutral_counters() -> None:
    first = TokenUsage(10, 2, 5, 0)
    second = TokenUsage(3, 4, 0, 8)

    combined = first + second

    assert combined == TokenUsage(13, 6, 5, 8)
    assert combined.total_input_tokens == 26
    with pytest.raises(ValueError, match="非负"):
        TokenUsage(input_tokens=-1)


def test_agent_model_request_freezes_separate_channels() -> None:
    user = ChatMessage.user_text("hello")
    request = AgentModelRequest(
        messages=(user,),
        system_prompt=("stable",),
        supplements=("<environment_context>context</environment_context>",),
    )

    assert request.messages == (user,)
    assert request.system_prompt == ("stable",)
    assert request.supplements[0].startswith("<environment_context>")


def test_semantic_events_have_precise_payloads() -> None:
    thinking = ThinkingBlock("reason", "signature")
    redacted = RedactedThinkingBlock("encrypted")
    tool = ToolCallBlock("call-1", "read", {"path": "a.py"})

    events = (
        TextDelta(0, "answer"),
        ThinkingDelta(1, "reason"),
        ThinkingComplete(1, thinking),
        ThinkingComplete(2, redacted),
        ToolCallStart(3, "call-1", "read"),
        ToolCallDelta(3, '{"path":"a.py"}'),
        ToolCallComplete(3, tool),
        StreamEnd(StopReason.TOOL_USE),
    )

    assert all(not hasattr(event, "kind") for event in events)
    assert events[2].block is thinking
    assert events[3].block is redacted
    assert events[6].block is tool


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TextDelta(-1, "text"),
        lambda: ThinkingDelta(0, ""),
        lambda: ToolCallStart(0, "", "read"),
        lambda: ToolCallStart(0, "call-1", ""),
        lambda: ToolCallDelta(0, ""),
    ],
)
def test_semantic_events_reject_invalid_payloads(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


def test_fake_provider_satisfies_runtime_protocol() -> None:
    provider = FakeProvider([])
    assert isinstance(provider, ChatProvider)


def test_provider_error_exposes_only_safe_message() -> None:
    error = ProviderError("authentication", "认证失败。", retryable=False)
    error.__cause__ = RuntimeError("secret-key-in-cause")

    assert str(error) == "认证失败。"
    assert "secret-key" not in str(error)
    assert error.code == "authentication"
    assert error.retryable is False


def test_error_hierarchy_is_available() -> None:
    assert isinstance(ConfigError("bad config"), Exception)
    assert isinstance(UIError("bad terminal"), Exception)
