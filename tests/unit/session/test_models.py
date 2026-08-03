from datetime import UTC, datetime

import pytest

from ycode.agent import TurnMessage
from ycode.context import ConversationMemory
from ycode.core import ChatMessage
from ycode.session import (
    ContextCheckpointRecord,
    SessionCommit,
    SessionDescriptor,
    SessionMessageRecord,
    SessionSnapshot,
    TurnCommitRecord,
)

NOW = datetime(2026, 8, 3, 1, 2, 3, tzinfo=UTC)
SESSION_ID = "20260803-090203-中文标题"


def test_session_record_models_accept_valid_values() -> None:
    message = ChatMessage.user_text("hello")
    assert SessionMessageRecord(1, SESSION_ID, "000001", NOW, message).message is message
    assert TurnCommitRecord(1, SESSION_ID, "000001", NOW, 2).message_count == 2
    checkpoint = ContextCheckpointRecord(
        1,
        SESSION_ID,
        "000001",
        NOW,
        ConversationMemory("summary"),
        (message,),
    )
    assert checkpoint.retained_history == (message,)


@pytest.mark.parametrize("session_id", ["", "bad/id", "20260803-090203-."])
def test_session_models_reject_invalid_session_id(session_id: str) -> None:
    with pytest.raises(ValueError, match="会话 ID"):
        SessionDescriptor(session_id, NOW)


def test_session_models_reject_time_turn_and_count_errors() -> None:
    message = ChatMessage.user_text("hello")
    with pytest.raises(ValueError, match="UTC"):
        SessionMessageRecord(1, SESSION_ID, "000001", datetime.now(), message)
    with pytest.raises(ValueError, match="回合 ID"):
        SessionMessageRecord(1, SESSION_ID, "1", NOW, message)
    with pytest.raises(ValueError, match="正整数"):
        TurnCommitRecord(1, SESSION_ID, "000001", NOW, 0)


def test_snapshot_and_commit_normalize_sequences() -> None:
    user = ChatMessage.user_text("hello")
    final = ChatMessage.assistant_text("done")
    snapshot = SessionSnapshot(SESSION_ID, (user, final), None, "000001", NOW)
    commit = SessionCommit(
        SESSION_ID,
        "000001",
        (TurnMessage(user, NOW), TurnMessage(final, NOW)),
    )
    assert snapshot.history == (user, final)
    assert commit.messages[-1].message is final
