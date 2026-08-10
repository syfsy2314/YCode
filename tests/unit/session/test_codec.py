import json
from datetime import UTC, datetime

import pytest

from ycode.context import ConversationMemory
from ycode.core import (
    ChatMessage,
    RedactedThinkingBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from ycode.session.codec import (
    SessionCodecError,
    decode_message,
    decode_record,
    encode_message,
    encode_record,
)
from ycode.session.models import (
    ContextCheckpointRecord,
    SessionMessageRecord,
    SkillStateRecord,
    TurnCommitRecord,
)

NOW = datetime(2026, 8, 3, 1, 2, 3, tzinfo=UTC)
SESSION_ID = "20260803-090203-codec"


@pytest.mark.parametrize(
    "message",
    [
        ChatMessage.user_text("hello"),
        ChatMessage(
            "assistant",
            (
                ThinkingBlock("thought", "signature"),
                RedactedThinkingBlock("hidden"),
                TextBlock("text"),
                ToolCallBlock("call-1", "read_file", {"path": "README.md"}),
            ),
        ),
        ChatMessage("user", (ToolResultBlock("call-1", "result", True),)),
    ],
)
def test_message_codec_round_trips_all_blocks(message: ChatMessage) -> None:
    assert decode_message(encode_message(message)) == message


def test_record_codec_round_trips_all_record_types() -> None:
    message = ChatMessage.user_text("hello")
    records = (
        SessionMessageRecord(1, SESSION_ID, "000001", NOW, message),
        ContextCheckpointRecord(
            1,
            SESSION_ID,
            "000001",
            NOW,
            ConversationMemory("summary"),
            (message,),
        ),
        TurnCommitRecord(1, SESSION_ID, "000001", NOW, 1),
        SkillStateRecord(1, SESSION_ID, "000001", NOW, ("review", "commit")),
    )
    assert tuple(decode_record(encode_record(record)) for record in records) == records


@pytest.mark.parametrize(
    "data",
    [
        "not json",
        json.dumps({"version": 2, "type": "message"}),
        json.dumps({"version": 1, "type": "unknown"}),
        json.dumps({"version": 1, "type": "message", "extra": True}),
    ],
)
def test_record_codec_rejects_invalid_or_unknown_records(data: str) -> None:
    with pytest.raises(SessionCodecError):
        decode_record(data)
