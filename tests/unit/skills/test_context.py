import asyncio

import pytest

from ycode.context.models import ConversationMemory, SummaryResult
from ycode.core.messages import ChatMessage, ToolCallBlock, ToolResultBlock
from ycode.skills import SkillContextBuilder, SkillContextKind, recent_complete_turns


class Compactor:
    def __init__(self) -> None:
        self.sources = []
        self.cancelled = False

    async def compact(self, source):
        self.sources.append(source)
        return SummaryResult(ConversationMemory("latest summary"))


def _tool_turn(label: str) -> tuple[ChatMessage, ...]:
    return (
        ChatMessage.user_text(label),
        ChatMessage(
            "assistant",
            (ToolCallBlock(f"call-{label}", "read_file", {"path": "README.md"}),),
        ),
        ChatMessage("user", (ToolResultBlock(f"call-{label}", "content"),)),
        ChatMessage.assistant_text(f"done {label}"),
    )


def test_recent_turns_preserves_tool_call_and_result_pair() -> None:
    history = (*_tool_turn("one"), *_tool_turn("two"), *_tool_turn("three"))

    recent = recent_complete_turns(history, 2)

    assert recent == (*_tool_turn("two"), *_tool_turn("three"))


def test_recent_turns_ignores_prefix_without_user_turn() -> None:
    history = (ChatMessage.assistant_text("retained prefix"), *_tool_turn("one"))

    assert recent_complete_turns(history, 5) == _tool_turn("one")


@pytest.mark.asyncio
async def test_summary_uses_committed_memory_and_history_without_mutating_them() -> None:
    compactor = Compactor()
    builder = SkillContextBuilder(compactor)
    history = (ChatMessage.user_text("old task"), ChatMessage.assistant_text("old reply"))
    memory = ConversationMemory("previous summary")
    user_task = ChatMessage.user_text("current task")

    context = await builder.build(SkillContextKind.SUMMARY, history, memory, user_task)

    assert context.history == ()
    assert context.summary == ConversationMemory("latest summary")
    assert context.user_task is user_task
    assert compactor.sources[0].previous_memory is memory
    assert compactor.sources[0].messages == history


@pytest.mark.asyncio
async def test_recent_and_none_always_keep_current_task() -> None:
    builder = SkillContextBuilder(Compactor())
    history = (*_tool_turn("one"), *_tool_turn("two"))
    user_task = ChatMessage.user_text("current task")

    recent = await builder.build(
        SkillContextKind.RECENT,
        history,
        None,
        user_task,
        recent_turns=1,
    )
    none = await builder.build(SkillContextKind.NONE, history, None, user_task)

    assert recent.history == _tool_turn("two")
    assert recent.user_task is user_task
    assert none.history == ()
    assert none.summary is None
    assert none.user_task is user_task


@pytest.mark.asyncio
async def test_summary_cancellation_propagates() -> None:
    class BlockingCompactor:
        async def compact(self, source):
            await asyncio.Future()

    task = asyncio.create_task(
        SkillContextBuilder(BlockingCompactor()).build(
            SkillContextKind.SUMMARY,
            (),
            None,
            ChatMessage.user_text("current"),
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_rejects_current_context_for_isolated_skill() -> None:
    with pytest.raises(ValueError, match="current"):
        await SkillContextBuilder(Compactor()).build(
            SkillContextKind.CURRENT,
            (),
            None,
            ChatMessage.user_text("current"),
        )
