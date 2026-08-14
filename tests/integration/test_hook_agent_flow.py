from pathlib import Path

from tests.support.fake_provider import FakeProvider
from tests.unit.agent.test_loop import (
    CountingTool,
    consume,
    create_loop,
    final_turn,
    tool_turn,
)
from ycode.agent import AgentMode, HookNoticeEvent, ToolApprovalRequested
from ycode.core import ChatMessage, ToolResultBlock
from ycode.hooks import HookContextFactory, HookRule, HookRuntime
from ycode.security import ApprovalChoice, PermissionMode
from ycode.tools import ToolAccess


def _runtime(tmp_path: Path, *rules: dict[str, object]) -> HookRuntime:
    return HookRuntime(tuple(HookRule.model_validate(rule) for rule in rules), tmp_path)


async def test_hook_deny_reaches_model_and_prevents_side_effect(tmp_path) -> None:
    provider = FakeProvider([tool_turn("call-1", "mutate"), final_turn("safe alternative")])
    tool = CountingTool("mutate", ToolAccess.WRITE)
    runtime = _runtime(
        tmp_path,
        {
            "id": "deny-mutation",
            "event": "tool.before_execute",
            "permission": "deny",
            "action": {"type": "agent"},
        },
    )
    loop = create_loop(
        tmp_path,
        provider,
        tool,
        permission_mode=PermissionMode.ALLOW,
        hook_runtime=runtime,
        hook_context=HookContextFactory(tmp_path, "session-test"),
    )

    events = await consume(loop.start_turn((), ChatMessage.user_text("change it"), AgentMode.AGENT))

    assert tool.calls == 0
    assert any(isinstance(event, HookNoticeEvent) for event in events)
    result = provider.agent_requests[1].messages[-1].blocks(ToolResultBlock)[0]
    assert result.is_error is True
    assert "Hook 规则 deny-mutation" in result.content
    await runtime.close()


async def test_hook_ask_prompts_once_without_session_grant(tmp_path) -> None:
    provider = FakeProvider([tool_turn("call-1", "mutate"), final_turn()])
    tool = CountingTool("mutate", ToolAccess.WRITE)
    runtime = _runtime(
        tmp_path,
        {
            "id": "ask-mutation",
            "event": "tool.before_execute",
            "permission": "ask",
            "action": {"type": "agent"},
        },
    )
    loop = create_loop(
        tmp_path,
        provider,
        tool,
        permission_mode=PermissionMode.ALLOW,
        hook_runtime=runtime,
        hook_context=HookContextFactory(tmp_path, "session-test"),
    )
    turn = loop.start_turn((), ChatMessage.user_text("change it"), AgentMode.AGENT)
    events = []
    async for event in turn:
        events.append(event)
        if isinstance(event, ToolApprovalRequested):
            assert event.decision.allow_session is False
            turn.submit_approval(ApprovalChoice.ALLOW_ONCE)

    assert sum(isinstance(event, ToolApprovalRequested) for event in events) == 1
    assert tool.calls == 1
    assert loop._permission_session is not None
    assert loop._permission_session.grant_count == 0
    await runtime.close()


async def test_after_hook_reminder_is_next_request_only(tmp_path) -> None:
    provider = FakeProvider([tool_turn("call-1", "inspect"), final_turn()])
    tool = CountingTool("inspect", ToolAccess.READ)
    runtime = _runtime(
        tmp_path,
        {
            "id": "review-result",
            "event": "tool.after_execute",
            "action": {"type": "reminder", "content": "review {{ tool.result.content }}"},
        },
    )
    loop = create_loop(
        tmp_path,
        provider,
        tool,
        permission_mode=PermissionMode.ALLOW,
        hook_runtime=runtime,
        hook_context=HookContextFactory(tmp_path, "session-test"),
    )
    turn = loop.start_turn((), ChatMessage.user_text("inspect"), AgentMode.AGENT)

    await consume(turn)

    assert not any("system-reminder" in item for item in provider.agent_requests[0].supplements)
    reminders = [
        item for item in provider.agent_requests[1].supplements if "system-reminder" in item
    ]
    assert len(reminders) == 1
    assert "review result-1" in reminders[0]
    assert turn.result is not None
    assert all(
        "system-reminder" not in message.message.text for message in turn.result.turn_messages
    )
    assert runtime.take_reminders() == ()
    await runtime.close()


async def test_disabled_and_once_rules_across_repeated_tool_events(tmp_path) -> None:
    provider = FakeProvider(
        [tool_turn("call-1", "inspect"), tool_turn("call-2", "inspect"), final_turn()]
    )
    tool = CountingTool("inspect", ToolAccess.READ)
    runtime = _runtime(
        tmp_path,
        {
            "id": "disabled",
            "enabled": False,
            "event": "tool.after_execute",
            "action": {"type": "agent"},
        },
        {
            "id": "once-notice",
            "once": True,
            "event": "tool.after_execute",
            "action": {"type": "agent"},
        },
    )
    loop = create_loop(
        tmp_path,
        provider,
        tool,
        permission_mode=PermissionMode.ALLOW,
        hook_runtime=runtime,
        hook_context=HookContextFactory(tmp_path, "session-test"),
    )

    events = await consume(
        loop.start_turn((), ChatMessage.user_text("inspect twice"), AgentMode.AGENT)
    )

    notices = [event.message for event in events if isinstance(event, HookNoticeEvent)]
    assert notices == ["子 Agent Hook 尚未实现：once-notice"]
    assert tool.calls == 2
    assert runtime.rules[0].executed is False
    assert runtime.rules[1].executed is True
    await runtime.close()
