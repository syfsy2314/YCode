from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.config import SecretRedactor
from ycode.context import (
    ContextArtifactStore,
    ContextCompactionError,
    ContextCompactionNotNeeded,
    ContextLimitError,
    ContextManager,
    ContextPolicy,
    ConversationCompactor,
)
from ycode.core import (
    AgentModelRequest,
    ChatMessage,
    StopReason,
    StreamEnd,
    TextDelta,
    ToolResultBlock,
)


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


def make_manager(
    tmp_path: Path,
    turns: list[list[object]],
) -> tuple[ContextManager, FakeProvider]:
    provider = FakeProvider(turns)  # type: ignore[arg-type]
    store = ContextArtifactStore(
        tmp_path,
        SecretRedactor(),
        ContextPolicy(),
        session_id="session",
    )
    manager = ContextManager(
        ContextPolicy(),
        store,
        ConversationCompactor(provider),
    )
    return manager, provider


def large_request(latest: ChatMessage, *, size: int = 510_000) -> AgentModelRequest:
    return AgentModelRequest(
        messages=(ChatMessage.user_text("x" * size), latest),
        system_prompt=("identity",),
    )


@pytest.mark.asyncio
async def test_auto_compaction_is_transactional_and_retains_latest_user(tmp_path: Path) -> None:
    manager, _ = make_manager(
        tmp_path,
        [[TextDelta(0, summary_response()), StreamEnd(StopReason.END_TURN)]],
    )
    latest = ChatMessage.user_text("最新请求原文")
    transaction = manager.begin_turn((), latest)

    prepared = await transaction.prepare_request(large_request(latest))

    assert prepared.messages == (latest,)
    assert prepared.compaction_report is not None
    assert prepared.request.supplements[0].startswith("<conversation_memory>")
    assert prepared.request.supplements[-1].startswith("<reminder>")
    assert manager.memory is None

    commit = transaction.create_commit((latest, ChatMessage.assistant_text("完成")))
    manager.commit(commit)
    assert manager.memory is commit.memory


@pytest.mark.asyncio
async def test_prepare_request_preserves_and_externalizes_continuation(tmp_path: Path) -> None:
    manager, _ = make_manager(tmp_path, [])
    latest = ChatMessage.user_text("latest")
    continuation = ChatMessage(
        "user",
        (ToolResultBlock("call-1", "x" * 60_000),),
    )
    request = AgentModelRequest(
        messages=(latest,),
        continuation_messages=(continuation,),
    )

    prepared = await manager.begin_turn((), latest).prepare_request(request)

    result = prepared.request.continuation_messages[0].blocks(ToolResultBlock)[0]
    assert '"externalized":true' in result.content


@pytest.mark.asyncio
async def test_preserved_first_request_fails_without_rewriting_prefix(tmp_path: Path) -> None:
    manager, provider = make_manager(tmp_path, [])
    task = ChatMessage.user_text("task")
    request = AgentModelRequest(
        messages=(ChatMessage.user_text("x" * 510_000),),
        continuation_messages=(task,),
    )

    with pytest.raises(ContextLimitError, match="继承前缀"):
        await manager.begin_turn((), task).prepare_request(
            request,
            preserve_messages=True,
        )

    assert provider.agent_requests == []


@pytest.mark.asyncio
async def test_preserved_compaction_only_replaces_continuation(tmp_path: Path) -> None:
    manager, _ = make_manager(
        tmp_path,
        [[TextDelta(0, summary_response()), StreamEnd(StopReason.END_TURN)]],
    )
    parent = ChatMessage.user_text("parent prefix")
    task = ChatMessage.user_text("task")
    request = AgentModelRequest(
        messages=(parent,),
        supplements=("parent supplement",),
        continuation_messages=(ChatMessage.assistant_text("x" * 510_000), task),
    )

    prepared = await manager.begin_turn((), task).prepare_request(
        request,
        preserve_messages=True,
        allow_preserved_compaction=True,
    )

    assert prepared.request.messages == (parent,)
    assert prepared.request.supplements[0] == "parent supplement"
    assert prepared.request.continuation_messages == (task,)
    assert prepared.compaction_report is not None


@pytest.mark.asyncio
async def test_failed_summary_continues_then_fuses_after_three_attempts(tmp_path: Path) -> None:
    invalid = [TextDelta(0, "invalid"), StreamEnd(StopReason.END_TURN)]
    manager, provider = make_manager(tmp_path, [invalid, invalid, invalid])
    latest = ChatMessage.user_text("latest")

    for expected in range(1, 4):
        prepared = await manager.begin_turn((), latest).prepare_request(large_request(latest))
        assert prepared.failure_report is not None
        assert prepared.failure_report.failure_count == expected
        assert prepared.failure_report.request_continues

    assert manager.auto_compaction_fused
    prepared = await manager.begin_turn((), latest).prepare_request(large_request(latest))
    assert prepared.failure_report is None
    assert len(provider.agent_requests) == 3


@pytest.mark.asyncio
async def test_summary_failure_above_continue_limit_stops_request(tmp_path: Path) -> None:
    invalid = [TextDelta(0, "invalid"), StreamEnd(StopReason.END_TURN)]
    manager, _ = make_manager(tmp_path, [invalid])
    latest = ChatMessage.user_text("latest")

    with pytest.raises(ContextLimitError, match="/compact") as captured:
        await manager.begin_turn((), latest).prepare_request(large_request(latest, size=600_000))
    assert captured.value.failure_report is not None
    assert manager.failure_count == 1


@pytest.mark.asyncio
async def test_uncompressible_request_does_not_call_provider(tmp_path: Path) -> None:
    manager, provider = make_manager(tmp_path, [])
    latest = ChatMessage.user_text("latest")
    request = AgentModelRequest(messages=(latest,), system_prompt=("x" * 510_000,))

    with pytest.raises(ContextLimitError) as captured:
        await manager.begin_turn((), latest).prepare_request(request)
    assert captured.value.code == "context_uncompressible"
    assert provider.agent_requests == []
    assert manager.failure_count == 0


@pytest.mark.asyncio
async def test_manual_compaction_handles_empty_success_and_failure(tmp_path: Path) -> None:
    valid = [TextDelta(0, summary_response()), StreamEnd(StopReason.END_TURN)]
    invalid = [TextDelta(0, "invalid"), StreamEnd(StopReason.END_TURN)]
    manager, _ = make_manager(tmp_path, [invalid, valid])

    with pytest.raises(ContextCompactionNotNeeded):
        await manager.compact_committed_history(())
    with pytest.raises(ContextCompactionError):
        await manager.compact_committed_history((ChatMessage.user_text("history"),))
    assert manager.failure_count == 1

    report = await manager.compact_committed_history((ChatMessage.user_text("history"),))
    assert report.manual
    assert manager.memory is not None
    assert manager.failure_count == 0


@pytest.mark.asyncio
async def test_repeated_compaction_rolls_previous_memory_into_one_replacement(
    tmp_path: Path,
) -> None:
    first_summary = summary_response().replace("## 主要请求\n无", "## 主要请求\nfirst")
    second_summary = summary_response().replace("## 主要请求\n无", "## 主要请求\nsecond")
    manager, provider = make_manager(
        tmp_path,
        [
            [TextDelta(0, first_summary), StreamEnd(StopReason.END_TURN)],
            [TextDelta(0, second_summary), StreamEnd(StopReason.END_TURN)],
        ],
    )

    await manager.compact_committed_history((ChatMessage.user_text("first history"),))
    await manager.compact_committed_history((ChatMessage.user_text("second history"),))

    assert manager.memory is not None
    assert "second" in manager.memory.summary
    assert "first" not in manager.memory.summary
    assert "first" in provider.agent_requests[1].messages[0].text


@pytest.mark.asyncio
async def test_prepare_restore_does_not_mutate_until_activated(tmp_path: Path) -> None:
    manager, _ = make_manager(
        tmp_path,
        [[TextDelta(0, summary_response()), StreamEnd(StopReason.END_TURN)]],
    )
    history = (ChatMessage.user_text("x" * 510_000),)

    candidate = await manager.prepare_restore(history, None)

    assert candidate.checkpoint_required
    assert manager.memory is None
    manager.activate_restore(candidate)
    assert manager.memory is candidate.memory


@pytest.mark.asyncio
async def test_failed_restore_preserves_current_runtime_state(tmp_path: Path) -> None:
    invalid = [TextDelta(0, "invalid"), StreamEnd(StopReason.END_TURN)]
    manager, _ = make_manager(tmp_path, [invalid])
    manager.failure_count = 2
    manager.auto_compaction_fused = True

    with pytest.raises(ContextCompactionError):
        await manager.prepare_restore((ChatMessage.user_text("x" * 510_000),), None)

    assert manager.failure_count == 2
    assert manager.auto_compaction_fused


@pytest.mark.asyncio
async def test_manual_candidate_requires_explicit_activation(tmp_path: Path) -> None:
    manager, _ = make_manager(
        tmp_path,
        [[TextDelta(0, summary_response()), StreamEnd(StopReason.END_TURN)]],
    )

    candidate = await manager.prepare_manual_compaction((ChatMessage.user_text("history"),))

    assert manager.memory is None
    manager.activate_compaction(candidate)
    assert manager.memory is candidate.memory
