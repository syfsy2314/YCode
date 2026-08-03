from datetime import UTC, datetime
from pathlib import Path

import pytest

from ycode.agent import TurnMessage
from ycode.context import ConversationMemory
from ycode.core import ChatMessage, TextBlock, ToolCallBlock, ToolResultBlock
from ycode.session import SessionManager, SessionStorageError
from ycode.session.codec import encode_record
from ycode.session.models import SessionMessageRecord, TurnCommitRecord

NOW = datetime(2026, 8, 3, 1, 2, 3, tzinfo=UTC)


def _turn(text: str = "实现：会话/恢复？") -> tuple[TurnMessage, ...]:
    return (
        TurnMessage(ChatMessage.user_text(text), NOW),
        TurnMessage(ChatMessage.assistant_text("完成"), NOW),
    )


@pytest.mark.asyncio
async def test_id_keeps_chinese_removes_invalid_and_handles_collision(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, clock=lambda: NOW)
    first = await manager.commit_turn(_turn())
    manager.begin_new()
    second = await manager.commit_turn(_turn())

    assert first.session_id == "20260803-010203-实现：会话恢复？"
    assert second.session_id == f"{first.session_id}-2"


@pytest.mark.asyncio
async def test_list_uses_filenames_and_delete_rejects_active(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, clock=lambda: NOW)
    commit = await manager.commit_turn(_turn("列表"))
    (manager.sessions_root / "invalid.jsonl").write_text("", encoding="utf-8")

    listed = await manager.list_sessions()
    assert [item.session_id for item in listed] == [commit.session_id]
    with pytest.raises(SessionStorageError, match="活动"):
        await manager.delete(commit.session_id)
    manager.begin_new()
    await manager.delete(commit.session_id)
    assert await manager.list_sessions() == ()


@pytest.mark.asyncio
async def test_commit_appends_messages_then_boundary_and_flushes(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, clock=lambda: NOW)
    first = await manager.commit_turn(_turn("第一轮"))
    second = await manager.commit_turn(_turn("第二轮"))

    lines = (
        (manager.sessions_root / f"{first.session_id}.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert len(lines) == 6
    assert '"type":"turn_commit"' in lines[2]
    assert second.turn_id == "000002"


@pytest.mark.asyncio
async def test_load_skips_bad_json_and_repairs_incomplete_tail(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, clock=lambda: NOW)
    commit = await manager.commit_turn(_turn())
    path = manager.sessions_root / f"{commit.session_id}.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write("bad json\n")
        stream.write('{"version":1,"type":"message"}\n')

    snapshot = await manager.load(commit.session_id)

    assert snapshot.history == tuple(item.message for item in _turn())
    assert {warning.code for warning in snapshot.warnings} == {"invalid_json", "repaired_tail"}
    size = path.stat().st_size
    await manager.load(commit.session_id)
    assert path.stat().st_size == size


@pytest.mark.asyncio
async def test_load_repairs_unmatched_tool_use(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, clock=lambda: NOW)
    first = await manager.commit_turn(_turn("完整"))
    broken = (
        TurnMessage(ChatMessage.user_text("工具"), NOW),
        TurnMessage(
            ChatMessage("assistant", (ToolCallBlock("call-1", "read_file", {"path": "x"}),)),
            NOW,
        ),
        TurnMessage(ChatMessage("user", (ToolResultBlock("other", "bad"),)), NOW),
        TurnMessage(ChatMessage("assistant", (TextBlock("done"),)), NOW),
    )
    path = manager.sessions_root / f"{first.session_id}.jsonl"
    before = path.stat().st_size
    records = [
        SessionMessageRecord(1, first.session_id, "000002", item.created_at, item.message)
        for item in broken
    ]
    commit = TurnCommitRecord(1, first.session_id, "000002", NOW, len(broken))
    with path.open("a", encoding="utf-8") as stream:
        for record in (*records, commit):
            stream.write(encode_record(record) + "\n")

    snapshot = await manager.load(first.session_id)

    assert snapshot.last_turn_id == "000001"
    assert path.stat().st_size == before
    assert snapshot.warnings[-1].code == "repaired_tail"


@pytest.mark.asyncio
async def test_checkpoint_replaces_covered_history_on_restore(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path, clock=lambda: NOW)
    commit = await manager.commit_turn(_turn("旧历史"))
    retained = (ChatMessage.user_text("保留"), ChatMessage.assistant_text("保留回复"))
    await manager.append_checkpoint(ConversationMemory("summary"), retained)

    snapshot = await manager.load(commit.session_id)

    assert snapshot.memory == ConversationMemory("summary")
    assert snapshot.history == retained
