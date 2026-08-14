import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.agent import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentMode,
    AgentTextDelta,
    ContextCompactedEvent,
    ContextCompactionFailedEvent,
    ContextCompactionNotNeededEvent,
    FinalResponseEvent,
    McpStatusEvent,
    ModeChangedEvent,
    PermissionGrantsClearedEvent,
    PermissionModeChangedEvent,
    PlainChatRunner,
    SessionRestoredEvent,
    TurnMessage,
    UserMessageEvent,
)
from ycode.config import SecretRedactor
from ycode.context import (
    ContextArtifactStore,
    ContextManager,
    ContextPolicy,
    ConversationCompactor,
)
from ycode.core import ChatMessage, StopReason, StreamEnd, TextDelta
from ycode.errors import ProviderError
from ycode.hooks import HookContextFactory, HookRule, HookRuntime
from ycode.mcp.models import McpConnectionState, McpServerStatus, McpStatusReport
from ycode.memory import MemoryStore, MemoryUpdater, MemoryUpdateStatus
from ycode.prompt import PromptRuntimeContext
from ycode.security import PermissionMode, PermissionSession
from ycode.session.chat import ChatSession
from ycode.session.manager import SessionManager
from ycode.session.models import SessionStorageError


def text_response(*parts: str):
    return [
        *(TextDelta(0, part) for part in parts),
        StreamEnd(StopReason.END_TURN),
    ]


async def collect(session: ChatSession, prompt: str) -> list[object]:
    return [event async for event in session.stream_reply(prompt)]


def session_with(provider: FakeProvider) -> ChatSession:
    return ChatSession(PlainChatRunner(provider))


def summary_response() -> str:
    headings = (
        "主要请求",
        "关键概念",
        "文件代码",
        "错误修复",
        "解决过程",
        "用户原话",
        "待办",
        "当前工作",
        "下一步",
    )
    body = "\n".join(f"## {heading}\n无" for heading in headings)
    return f"<analysis_draft>草稿</analysis_draft><summary>{body}</summary>"


def context_session(
    tmp_path: Path,
    provider: FakeProvider,
) -> tuple[ChatSession, ContextManager]:
    policy = ContextPolicy()
    manager = ContextManager(
        policy,
        ContextArtifactStore(
            tmp_path,
            SecretRedactor(),
            policy,
            session_id="chat-context",
        ),
        ConversationCompactor(provider),
    )
    return ChatSession(PlainChatRunner(provider), context_manager=manager), manager


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
async def test_display_text_differs_from_model_and_committed_text() -> None:
    provider = FakeProvider([text_response("reviewed")])
    session = session_with(provider)

    events = [
        event
        async for event in session.stream_reply(
            "Review the current changes.",
            display_text="/review",
        )
    ]

    assert events[0].message.text == "/review"  # type: ignore[union-attr]
    assert provider.requests[0][0].text == "Review the current changes."
    assert session.history[0].text == "Review the current changes."
    assert all(message.text != "/review" for message in session.history)


@pytest.mark.asyncio
async def test_dual_text_failure_commits_neither_display_nor_model_text() -> None:
    provider = FakeProvider([[ProviderError("network", "safe error", True)]])
    session = session_with(provider)

    events = [
        event
        async for event in session.stream_reply(
            "Review the current changes.",
            display_text="/review",
        )
    ]

    assert events[0].message.text == "/review"  # type: ignore[union-attr]
    assert isinstance(events[-1], AgentErrorEvent)
    assert session.history == ()


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
async def test_plain_session_keeps_compact_as_normal_user_message() -> None:
    provider = FakeProvider([text_response("answer")])
    session = session_with(provider)

    await collect(session, "/compact")

    assert provider.requests[0][0].text == "/compact"


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


async def test_session_hook_start_notice_and_end_notice(tmp_path, capsys) -> None:
    provider = FakeProvider([])
    runtime = HookRuntime(
        tuple(
            HookRule.model_validate({"id": rule_id, "event": event, "action": {"type": "agent"}})
            for rule_id, event in (("started", "session.start"), ("ended", "session.end"))
        ),
        tmp_path,
    )
    session = ChatSession(
        PlainChatRunner(provider),
        hook_runtime=runtime,
        hook_context=HookContextFactory(tmp_path, "session-test"),
    )

    await session.start_hooks()
    await session.close()
    await session.close()

    assert session.startup_warnings == ("子 Agent Hook 尚未实现：started",)
    assert capsys.readouterr().out.count("hook: 子 Agent Hook 尚未实现：ended") == 1
    assert provider.close_count == 1


@pytest.mark.asyncio
async def test_compact_command_summarizes_committed_history_without_entering_it(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        [
            text_response("answer"),
            [TextDelta(0, summary_response()), StreamEnd(StopReason.END_TURN)],
        ]
    )
    session, manager = context_session(tmp_path, provider)
    await collect(session, "question")

    events = await collect(session, "/COMPACT")

    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[-1], ContextCompactedEvent)
    assert events[-1].report.manual
    assert session.history == ()
    assert manager.memory is not None
    assert len(provider.agent_requests) == 1
    await session.close()


@pytest.mark.asyncio
async def test_compact_empty_history_does_not_call_provider(tmp_path: Path) -> None:
    provider = FakeProvider([])
    session, manager = context_session(tmp_path, provider)

    events = await collect(session, "/compact")

    assert isinstance(events[-1], ContextCompactionNotNeededEvent)
    assert provider.agent_requests == []
    assert manager.failure_count == 0
    await session.close()


@pytest.mark.asyncio
async def test_compact_failure_keeps_history_and_reports_count(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            text_response("answer"),
            [TextDelta(0, "invalid"), StreamEnd(StopReason.END_TURN)],
        ]
    )
    session, manager = context_session(tmp_path, provider)
    await collect(session, "question")
    original_history = session.history

    events = await collect(session, "/compact")

    assert isinstance(events[-1], ContextCompactionFailedEvent)
    assert events[-1].report.failure_count == 1
    assert session.history == original_history
    assert manager.memory is None
    await session.close()


@pytest.mark.asyncio
async def test_compact_can_be_cancelled_without_failure_or_commit(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            text_response("answer"),
            [TextDelta(0, summary_response()), StreamEnd(StopReason.END_TURN)],
        ]
    )
    session, manager = context_session(tmp_path, provider)
    await collect(session, "question")
    original_history = session.history
    provider.delay = 10
    provider.request_started.clear()
    task = asyncio.create_task(collect(session, "/compact"))
    await provider.request_started.wait()

    session.cancel_active_turn()
    events = await task

    assert isinstance(events[-1], AgentCancelledEvent)
    assert session.history == original_history
    assert manager.memory is None
    assert manager.failure_count == 0
    await session.close()


@pytest.mark.asyncio
async def test_success_persists_before_history_and_storage_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider([text_response("saved"), text_response("not saved")])
    manager = SessionManager(tmp_path)
    session = ChatSession(PlainChatRunner(provider), session_manager=manager)

    events = await collect(session, "first")
    assert isinstance(events[-1], FinalResponseEvent)
    assert len((await manager.load(manager.active_session_id)).history) == 2  # type: ignore[arg-type]

    def fail_write(path: Path, lines: tuple[str, ...]) -> None:
        del path, lines
        raise SessionStorageError("injected")

    monkeypatch.setattr(manager, "_append_lines", fail_write)
    events = await collect(session, "second")

    assert events[-1] == AgentErrorEvent(
        "session_storage_error",
        "会话保存失败，本轮未提交到当前历史。",
    )
    assert [message.text for message in session.history] == ["first", "saved"]


@pytest.mark.asyncio
async def test_resume_switches_session_and_clears_session_state(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    provider = FakeProvider([text_response("one"), text_response("two")])
    first_session = ChatSession(PlainChatRunner(provider), session_manager=manager)
    await collect(first_session, "first")
    first_id = manager.active_session_id
    assert first_id is not None
    manager.begin_new()
    second_session = ChatSession(PlainChatRunner(provider), session_manager=manager)
    await collect(second_session, "second")

    permission = PermissionSession(PermissionMode.DEFAULT)
    permission.grant({"tool": "read_file", "path": "a"})
    runtime = PromptRuntimeContext()
    runtime.begin_turn("agent")
    session = ChatSession(
        PlainChatRunner(FakeProvider([])),
        permission,
        session_manager=manager,
        prompt_runtime=runtime,
    )
    events = await collect(session, f"/resume {first_id}")

    assert isinstance(events[-1], SessionRestoredEvent)
    assert [message.text for message in session.history] == ["first", "one"]
    assert manager.active_session_id == first_id
    assert permission.grant_count == 0
    assert runtime.begin_turn("agent").full_mode_instruction


@pytest.mark.asyncio
async def test_resume_failure_preserves_current_session(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    provider = FakeProvider([text_response("current")])
    session = ChatSession(PlainChatRunner(provider), session_manager=manager)
    await collect(session, "hello")
    current_id = manager.active_session_id
    current_history = session.history

    events = await collect(session, "/resume 20260803-010203-missing")

    assert events[-1].code == "session_restore_failed"  # type: ignore[union-attr]
    assert manager.active_session_id == current_id
    assert session.history == current_history


@pytest.mark.asyncio
async def test_finalize_memory_reloads_and_applies_model_plan(tmp_path: Path) -> None:
    response = json.dumps(
        {
            "operations": [
                {
                    "action": "create",
                    "path": "user-prefers-any.md",
                    "entry": {
                        "path": "user-prefers-any.md",
                        "name": "偏好 any",
                        "description": "用户要求使用 any",
                        "type": "user_preference",
                        "body": "使用 any 替代 interface{}。",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    provider = FakeProvider(
        [text_response("ok"), [TextDelta(0, response), StreamEnd(StopReason.END_TURN)]]
    )
    manager = SessionManager(tmp_path)
    store = MemoryStore(tmp_path)
    session = ChatSession(
        PlainChatRunner(provider),
        session_manager=manager,
        memory_store=store,
        memory_updater=MemoryUpdater(provider),
    )
    await collect(session, "remember")

    report = await session.finalize_memory()

    assert report.status is MemoryUpdateStatus.UPDATED
    assert store.load().entries[0].path == "user-prefers-any.md"
    assert await session.finalize_memory() is report


@pytest.mark.asyncio
async def test_finalize_memory_skips_without_new_commits(tmp_path: Path) -> None:
    provider = FakeProvider([])
    session = ChatSession(
        PlainChatRunner(provider),
        session_manager=SessionManager(tmp_path),
        memory_store=MemoryStore(tmp_path),
        memory_updater=MemoryUpdater(provider),
    )

    report = await session.finalize_memory()

    assert report.status is MemoryUpdateStatus.SKIPPED
    assert provider.requests == []


@pytest.mark.asyncio
async def test_stale_resume_queues_reminder_for_only_next_normal_turn(tmp_path: Path) -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC)
    manager = SessionManager(tmp_path, clock=lambda: old)
    saved = await manager.commit_turn(
        (
            TurnMessage(ChatMessage.user_text("old"), old),
            TurnMessage(ChatMessage.assistant_text("answer"), old),
        )
    )

    class ReminderRunner(PlainChatRunner):
        def __init__(self, provider: FakeProvider) -> None:
            super().__init__(provider)
            self.queued = []
            self.used = []

        def queue_request_supplement(self, supplement) -> None:
            self.queued.append(supplement)

        def clear_queued_request_supplements(self, kind) -> None:
            self.queued[:] = [item for item in self.queued if item.kind is not kind]

        def start_turn(self, history, user_message, mode):
            self.used.append(tuple(self.queued))
            self.queued.clear()
            return super().start_turn(history, user_message, mode)

    runner = ReminderRunner(FakeProvider([text_response("first"), text_response("second")]))
    session = ChatSession(runner, session_manager=manager)
    await session.restore(saved.session_id)
    await collect(session, "new turn")
    await collect(session, "another turn")

    assert len(runner.used[0]) == 1
    assert "Last active:" in runner.used[0][0].content
    assert runner.used[1] == ()


@pytest.mark.asyncio
async def test_explicit_shared_skill_displays_raw_but_commits_expanded_task(
    tmp_path: Path,
) -> None:
    from ycode.agent import AgentTermination, AgentTurnResult, AgentTurnStream
    from ycode.skills import (
        SkillCatalog,
        SkillLoader,
        SkillRuntime,
        SkillValidationEnvironment,
    )

    skill_dir = tmp_path / ".ycode" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review changes\n---\nReview carefully.\n",
        encoding="utf-8",
    )
    catalog = SkillCatalog(
        tmp_path,
        SkillLoader(),
        SkillValidationEnvironment(frozenset(), frozenset(), frozenset()),
    )
    catalog.commit(catalog.scan_candidate())
    runtime = SkillRuntime(catalog, PromptRuntimeContext())

    class ScopedRunner:
        supported_modes = frozenset({AgentMode.AGENT})

        def start_turn_with_skill_scope(self, history, user_message, mode, scope):
            async def produce(turn):
                final = ChatMessage.assistant_text("reviewed")
                turn.complete(
                    AgentTurnResult(
                        AgentTermination.COMPLETED,
                        (user_message, final),
                        final,
                        active_skill_names=runtime.candidate_active_names(scope),
                        skill_scope=scope,
                    )
                )
                yield FinalResponseEvent(final)

            return AgentTurnStream(produce)

        def start_turn(self, history, user_message, mode):
            raise AssertionError("显式 Skill 必须传递候选作用域")

        async def close(self):
            pass

    manager = SessionManager(tmp_path)
    session = ChatSession(
        ScopedRunner(),  # type: ignore[arg-type]
        session_manager=manager,
        skill_runtime=runtime,
    )

    events = [event async for event in session.stream_skill("review", "parser", "/review parser")]

    assert isinstance(events[0], UserMessageEvent)
    assert events[0].message.text == "/review parser"
    assert session.history[0].text == (
        'Use the "review" skill for this task.\n\nInvocation arguments:\nparser'
    )
    assert runtime.active_names == ("review",)
    restored = await manager.load(manager.active_session_id or "")
    assert restored.active_skill_names == ("review",)


@pytest.mark.asyncio
async def test_explicit_isolated_skill_commits_only_expanded_task_and_handoff(
    tmp_path: Path,
) -> None:
    from ycode.skills import (
        SkillCatalog,
        SkillLoader,
        SkillRuntime,
        SkillValidationEnvironment,
    )

    skill_dir = tmp_path / ".ycode" / "skills" / "audit"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: audit\ndescription: Audit changes\n"
        "metadata:\n  ycode-execution-mode: isolated\n"
        "  ycode-context: none\n---\nAudit carefully.\n",
        encoding="utf-8",
    )
    catalog = SkillCatalog(
        tmp_path,
        SkillLoader(),
        SkillValidationEnvironment(frozenset(), frozenset(), frozenset()),
    )
    catalog.commit(catalog.scan_candidate())

    class Executor:
        async def run(self, snapshot, scope, arguments):
            return "isolated handoff"

    runtime = SkillRuntime(
        catalog,
        PromptRuntimeContext(),
        isolated_executor=Executor(),
    )
    manager = SessionManager(tmp_path)
    session = ChatSession(
        PlainChatRunner(FakeProvider([])),
        session_manager=manager,
        skill_runtime=runtime,
    )

    events = [event async for event in session.stream_skill("audit", None, "/audit")]

    assert isinstance(events[0], UserMessageEvent)
    assert isinstance(events[-1], FinalResponseEvent)
    assert [(item.role, item.text) for item in session.history] == [
        (
            "user",
            'Use the "audit" skill for this task.\n\nNo arguments were provided.',
        ),
        ("assistant", "isolated handoff"),
    ]
    assert runtime.active_names == ()
