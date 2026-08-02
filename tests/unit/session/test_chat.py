import asyncio

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.agent import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentMode,
    AgentTextDelta,
    FinalResponseEvent,
    McpStatusEvent,
    ModeChangedEvent,
    PermissionGrantsClearedEvent,
    PermissionModeChangedEvent,
    PlainChatRunner,
    UserMessageEvent,
)
from ycode.core import StopReason, StreamEnd, TextDelta
from ycode.errors import ProviderError
from ycode.mcp.models import McpConnectionState, McpServerStatus, McpStatusReport
from ycode.security import PermissionMode, PermissionSession
from ycode.session.chat import ChatSession


def text_response(*parts: str):
    return [
        *(TextDelta(0, part) for part in parts),
        StreamEnd(StopReason.END_TURN),
    ]


async def collect(session: ChatSession, prompt: str) -> list[object]:
    return [event async for event in session.stream_reply(prompt)]


def session_with(provider: FakeProvider) -> ChatSession:
    return ChatSession(PlainChatRunner(provider))


@pytest.mark.asyncio
async def test_success_commits_complete_turn_before_final_event() -> None:
    provider = FakeProvider([text_response("hello ", "world")])
    session = session_with(provider)

    events = await collect(session, "hi")

    assert isinstance(events[0], UserMessageEvent)
    assert events[1:3] == [
        AgentTextDelta(1, 0, "hello "),
        AgentTextDelta(1, 0, "world"),
    ]
    assert isinstance(events[-1], FinalResponseEvent)
    assert [(item.role, item.text) for item in session.history] == [
        ("user", "hi"),
        ("assistant", "hello world"),
    ]


@pytest.mark.asyncio
async def test_multi_turn_request_contains_only_committed_history() -> None:
    provider = FakeProvider([text_response("first"), text_response("second")])
    session = session_with(provider)

    await collect(session, "one")
    await collect(session, "two")

    assert [(item.role, item.text) for item in provider.requests[1]] == [
        ("user", "one"),
        ("assistant", "first"),
        ("user", "two"),
    ]


@pytest.mark.asyncio
async def test_provider_error_rolls_back_and_retry_uses_clean_history() -> None:
    provider = FakeProvider(
        [
            [TextDelta(0, "partial"), ProviderError("network", "safe error", True)],
            text_response("ok"),
        ]
    )
    session = session_with(provider)

    failed = await collect(session, "failed")
    assert isinstance(failed[-1], AgentErrorEvent)
    assert session.history == ()

    await collect(session, "retry")
    assert [item.text for item in provider.requests[1]] == ["retry"]


@pytest.mark.asyncio
async def test_invalid_response_rolls_back_as_agent_error() -> None:
    session = session_with(FakeProvider([[TextDelta(0, "partial")]]))

    events = await collect(session, "hello")

    assert events[-1] == AgentErrorEvent(
        "invalid_response",
        "模型响应结构无效，请重试。",
    )
    assert session.history == ()


@pytest.mark.asyncio
async def test_stopping_iteration_early_cancels_and_rolls_back() -> None:
    session = session_with(FakeProvider([text_response("first", "second")]))
    stream = session.stream_reply("hello")

    assert isinstance(await anext(stream), UserMessageEvent)
    await stream.aclose()

    assert session.history == ()


@pytest.mark.asyncio
async def test_session_cancel_api_returns_cancel_event_and_rolls_back() -> None:
    provider = FakeProvider([text_response("late")], delay=10)
    session = session_with(provider)
    task = asyncio.create_task(collect(session, "hello"))
    await provider.request_started.wait()

    session.cancel_active_turn()
    events = await task

    assert isinstance(events[-1], AgentCancelledEvent)
    assert session.history == ()


@pytest.mark.asyncio
async def test_mode_commands_do_not_call_provider_or_enter_history() -> None:
    provider = FakeProvider([])
    runner = PlainChatRunner(provider)
    runner.supported_modes = frozenset({AgentMode.AGENT, AgentMode.PLAN_ONLY})
    session = ChatSession(runner)

    plan_events = await collect(session, "/PLAN")
    agent_events = await collect(session, "/agent")

    assert isinstance(plan_events[-1], ModeChangedEvent)
    assert plan_events[-1].mode is AgentMode.PLAN_ONLY
    assert isinstance(agent_events[-1], ModeChangedEvent)
    assert session.mode is AgentMode.AGENT
    assert session.history == ()
    assert provider.requests == []


@pytest.mark.asyncio
async def test_plain_runner_rejects_plan_mode_without_provider_call() -> None:
    provider = FakeProvider([])
    session = session_with(provider)

    events = await collect(session, "/plan")

    assert events[-1] == AgentErrorEvent(
        "unsupported_mode",
        "当前对话运行器不支持 plan-only 模式。",
    )
    assert session.mode is AgentMode.AGENT
    assert provider.requests == []


@pytest.mark.asyncio
async def test_non_exact_plan_text_is_sent_as_user_message() -> None:
    provider = FakeProvider([text_response("answer")])
    session = session_with(provider)

    await collect(session, "/plan this")

    assert provider.requests[0][0].text == "/plan this"


@pytest.mark.asyncio
async def test_permission_commands_change_runtime_state_without_provider_call() -> None:
    provider = FakeProvider([])
    permission = PermissionSession(PermissionMode.DEFAULT)
    permission.grant({"tool": "read_file", "path": "a.txt"})
    session = ChatSession(PlainChatRunner(provider), permission)

    status = await collect(session, "/permission")
    strict = await collect(session, "/permission strict")
    cleared = await collect(session, "/permission clear")

    assert status[-1] == PermissionModeChangedEvent(
        PermissionMode.DEFAULT,
        PermissionMode.DEFAULT,
    )
    assert strict[-1] == PermissionModeChangedEvent(
        PermissionMode.DEFAULT,
        PermissionMode.STRICT,
    )
    assert permission.mode is PermissionMode.STRICT
    assert cleared[-1] == PermissionGrantsClearedEvent(1)
    assert permission.grant_count == 0
    assert provider.requests == []
    assert session.history == ()


@pytest.mark.asyncio
async def test_plain_session_keeps_permission_text_as_normal_user_message() -> None:
    provider = FakeProvider([text_response("answer")])
    session = session_with(provider)

    await collect(session, "/permission allow")

    assert provider.requests[0][0].text == "/permission allow"


@pytest.mark.asyncio
async def test_mcp_command_returns_snapshot_without_model_or_history() -> None:
    provider = FakeProvider([])
    report = McpStatusReport((McpServerStatus("demo", "stdio", McpConnectionState.READY, 2),))

    class StatusProvider:
        def snapshot(self) -> McpStatusReport:
            return report

    session = ChatSession(PlainChatRunner(provider), mcp_status_provider=StatusProvider())

    events = await collect(session, "/MCP")

    assert isinstance(events[0], UserMessageEvent)
    assert events[1] == McpStatusEvent(report)
    assert provider.requests == []
    assert session.history == ()


@pytest.mark.asyncio
async def test_mcp_without_provider_is_local_error_but_non_exact_is_message() -> None:
    unavailable_provider = FakeProvider([])
    unavailable = session_with(unavailable_provider)

    events = await collect(unavailable, "/mcp")

    assert events[-1] == AgentErrorEvent("mcp_unavailable", "当前没有 MCP 状态信息。")
    assert unavailable_provider.requests == []

    normal_provider = FakeProvider([text_response("answer")])
    normal = session_with(normal_provider)
    await collect(normal, "/mcp status")
    assert normal_provider.requests[0][0].text == "/mcp status"


@pytest.mark.asyncio
async def test_blank_input_and_idempotent_close() -> None:
    provider = FakeProvider([])
    session = session_with(provider)

    with pytest.raises(ValueError, match="不能为空"):
        await collect(session, "   ")
    await session.close()
    await session.close()

    assert provider.requests == []
    assert provider.close_count == 1
