import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ycode.agent import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentMode,
    AgentTermination,
    AgentTextDelta,
    AgentTurn,
    AgentTurnResult,
    AgentTurnStream,
    ContextCompactedEvent,
    ContextCompactionFailedEvent,
    ContextCompactionNotNeededEvent,
    FinalResponseEvent,
    ModeChangedEvent,
    SessionRestoredEvent,
    ToolApprovalRequested,
    TurnMessage,
)
from ycode.context import (
    ContextCommit,
    ContextCompactionReport,
    ContextFailureReport,
    ConversationMemory,
)
from ycode.core.events import TokenUsage
from ycode.core.messages import ChatMessage, ToolCallBlock
from ycode.security import (
    ApprovalChoice,
    PermissionAction,
    PermissionDecision,
    PermissionSubject,
)


def test_agent_events_are_frozen_and_validate_fields() -> None:
    event = AgentTextDelta(round_number=1, index=0, text="hello")
    with pytest.raises(FrozenInstanceError):
        event.text = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="轮次"):
        AgentTextDelta(round_number=0, index=0, text="hello")
    with pytest.raises(ValueError, match="不能为空"):
        AgentErrorEvent(code="", message="error")

    mode = ModeChangedEvent(AgentMode.AGENT, AgentMode.PLAN_ONLY)
    assert mode.mode is AgentMode.PLAN_ONLY


def test_completed_result_requires_final_message_at_end() -> None:
    user = ChatMessage.user_text("question")
    final = ChatMessage.assistant_text("answer")
    result = AgentTurnResult(
        termination=AgentTermination.COMPLETED,
        messages=(user, final),
        final_message=final,
        usage=TokenUsage(input_tokens=10, output_tokens=5),
    )

    assert result.final_message is final
    assert result.usage == TokenUsage(input_tokens=10, output_tokens=5)
    assert result.messages == (user, final)
    assert all(item.created_at.tzinfo is UTC for item in result.turn_messages)
    with pytest.raises(ValueError, match="最终"):
        AgentTurnResult(
            termination=AgentTermination.COMPLETED,
            messages=(user,),
            final_message=final,
        )
    with pytest.raises(ValueError, match="错误码"):
        AgentTurnResult(termination=AgentTermination.ERROR, messages=())


def test_turn_message_requires_utc_timestamp() -> None:
    message = ChatMessage.user_text("question")
    timestamp = datetime.now(UTC)
    assert TurnMessage(message, timestamp).created_at == timestamp
    with pytest.raises(ValueError, match="UTC"):
        TurnMessage(message, datetime.now())
    with pytest.raises(ValueError, match="UTC"):
        TurnMessage(message, timestamp.astimezone(timezone(timedelta(hours=8))))


def test_context_commit_is_only_allowed_for_completed_result() -> None:
    user = ChatMessage.user_text("question")
    final = ChatMessage.assistant_text("answer")
    commit = ContextCommit((user, final), ConversationMemory("summary"))
    result = AgentTurnResult(
        AgentTermination.COMPLETED,
        (user, final),
        final,
        context_commit=commit,
    )

    assert result.context_commit is commit
    with pytest.raises(ValueError, match="上下文提交"):
        AgentTurnResult(
            AgentTermination.CANCELLED,
            (),
            context_commit=commit,
        )


def test_context_events_validate_reports() -> None:
    compacted = ContextCompactedEvent(ContextCompactionReport(100, 50))
    failed = ContextCompactionFailedEvent(
        ContextFailureReport("summary_invalid", "摘要无效", 1, False, True)
    )
    not_needed = ContextCompactionNotNeededEvent()

    assert compacted.report.after_tokens == 50
    assert failed.report.failure_count == 1
    assert not_needed.code == "compact_not_needed"


def test_session_restored_event_contains_only_safe_summary_fields() -> None:
    event = SessionRestoredEvent("20260803-010203-title", 4, ("已跳过坏行",))
    assert event.message_count == 4
    assert not hasattr(event, "history")
    with pytest.raises(ValueError, match="消息数"):
        SessionRestoredEvent("session", -1)


@pytest.mark.asyncio
async def test_turn_result_is_visible_only_after_stream_exhaustion() -> None:
    final = ChatMessage.assistant_text("done")

    async def producer(turn: AgentTurnStream):
        turn.complete(
            AgentTurnResult(
                termination=AgentTermination.COMPLETED,
                messages=(final,),
                final_message=final,
            )
        )
        yield FinalResponseEvent(final)

    turn = AgentTurnStream(producer)
    assert isinstance(turn, AgentTurn)
    assert turn.result is None
    assert isinstance(await anext(turn), FinalResponseEvent)
    assert turn.result is None
    with pytest.raises(StopAsyncIteration):
        await anext(turn)
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.COMPLETED


@pytest.mark.asyncio
async def test_turn_cancel_interrupts_child_and_can_finish_with_cancel_event() -> None:
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def child() -> None:
        try:
            started.set()
            await asyncio.sleep(10)
        finally:
            cleaned.set()

    async def producer(turn: AgentTurnStream):
        try:
            await turn.run_child(child())
        except asyncio.CancelledError:
            turn.complete(
                AgentTurnResult(
                    termination=AgentTermination.CANCELLED,
                    messages=(),
                )
            )
            yield AgentCancelledEvent("已取消。")

    turn = AgentTurnStream(producer)
    next_event = asyncio.create_task(anext(turn))
    await started.wait()
    turn.cancel()

    assert isinstance(await next_event, AgentCancelledEvent)
    with pytest.raises(StopAsyncIteration):
        await anext(turn)
    assert cleaned.is_set()
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.CANCELLED


@pytest.mark.asyncio
async def test_turn_allows_only_one_blocking_approval() -> None:
    subject = PermissionSubject(
        ToolCallBlock(id="call-1", name="write_file", arguments={"path": "a"}),
        {"path": "a"},
        {"tool": "write_file", "path": "a", "overwrite": False},
        "写入 a",
    )
    decision = PermissionDecision(
        PermissionAction.ASK,
        subject,
        "mode_default",
        "需要确认",
    )

    async def producer(turn: AgentTurnStream):
        turn.begin_approval()
        yield ToolApprovalRequested(1, 0, decision)
        choice = await turn.run_child(turn.consume_approval())
        assert choice is ApprovalChoice.ALLOW_ONCE
        final = ChatMessage.assistant_text("done")
        turn.complete(
            AgentTurnResult(
                AgentTermination.COMPLETED,
                (final,),
                final,
            )
        )
        yield FinalResponseEvent(final)

    turn = AgentTurnStream(producer)
    assert isinstance(await anext(turn), ToolApprovalRequested)
    assert turn.approval_pending
    with pytest.raises(RuntimeError, match="只能等待一个"):
        turn.begin_approval()
    turn.submit_approval(ApprovalChoice.ALLOW_ONCE)
    assert isinstance(await anext(turn), FinalResponseEvent)
    with pytest.raises(RuntimeError, match="没有等待"):
        turn.submit_approval(ApprovalChoice.DENY)
    with pytest.raises(StopAsyncIteration):
        await anext(turn)


@pytest.mark.asyncio
async def test_cancelling_pending_approval_clears_it_without_choice() -> None:
    async def producer(turn: AgentTurnStream):
        turn.begin_approval()
        yield AgentTextDelta(1, 0, "等待审批")
        try:
            await turn.run_child(turn.consume_approval())
        except asyncio.CancelledError:
            turn.complete(
                AgentTurnResult(
                    AgentTermination.CANCELLED,
                    (),
                )
            )
            yield AgentCancelledEvent("已取消。")

    turn = AgentTurnStream(producer)
    assert isinstance(await anext(turn), AgentTextDelta)
    turn.cancel()

    assert isinstance(await anext(turn), AgentCancelledEvent)
    assert not turn.approval_pending
    with pytest.raises(StopAsyncIteration):
        await anext(turn)
