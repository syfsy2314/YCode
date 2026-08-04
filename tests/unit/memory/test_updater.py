import json
from datetime import UTC, datetime

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.agent import TurnMessage
from ycode.core import (
    ChatMessage,
    StopReason,
    StreamEnd,
    TextDelta,
    ThinkingDelta,
)
from ycode.memory import MemorySnapshot, MemoryType, MemoryUpdater
from ycode.memory.updater import (
    MemoryUpdateError,
    build_memory_transcript,
    load_memory_update_prompt,
    parse_memory_update,
)
from ycode.session import SessionCommit

NOW = datetime(2026, 8, 3, 1, 2, 3, tzinfo=UTC)


def _commit() -> SessionCommit:
    return SessionCommit(
        "20260803-010203-memory",
        "000001",
        (
            TurnMessage(ChatMessage.user_text("偏好 any"), NOW),
            TurnMessage(ChatMessage.assistant_text("已记录"), NOW),
        ),
    )


def test_memory_transcript_contains_session_time_boundaries() -> None:
    transcript = json.loads(build_memory_transcript(MemorySnapshot(), (_commit(),)))
    turn = transcript["new_conversations"][0]
    assert turn["session_id"] == "20260803-010203-memory"
    assert turn["turn_id"] == "000001"
    assert turn["messages"][0]["timestamp"].endswith("Z")


def test_memory_update_prompt_declares_filename_contract() -> None:
    prompt = load_memory_update_prompt()

    assert "user_preference: `user-<slug>.md`" in prompt
    assert "correction_feedback: `feedback-<slug>.md`" in prompt
    assert "project_knowledge: `project-<slug>.md`" in prompt
    assert "reference: `reference-<slug>.md`" in prompt
    assert "`path` and `entry.path` must be identical" in prompt
    assert "`user_language`" in prompt


def test_parse_memory_update_accepts_noop_and_valid_create() -> None:
    assert parse_memory_update('{"operations":[]}').operations == ()
    plan = parse_memory_update(
        json.dumps(
            {
                "operations": [
                    {
                        "action": "create",
                        "path": "user-prefers-any.md",
                        "entry": {
                            "path": "user-prefers-any.md",
                            "name": "偏好 any",
                            "description": "使用 any",
                            "type": "user_preference",
                            "body": "使用 any 替代 interface{}。",
                        },
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    assert plan.operations[0].entry is not None
    assert plan.operations[0].entry.type is MemoryType.USER_PREFERENCE


@pytest.mark.parametrize(
    "response",
    [
        "text",
        '{"operations":[]} extra',
        '{"operations":[{"action":"delete","path":"x","entry":{}}]}',
    ],
)
def test_parse_memory_update_rejects_extra_or_invalid_content(response: str) -> None:
    with pytest.raises(MemoryUpdateError):
        parse_memory_update(response)


@pytest.mark.asyncio
async def test_updater_uses_isolated_no_tool_request() -> None:
    provider = FakeProvider([[TextDelta(0, '{"operations":[]}'), StreamEnd(StopReason.END_TURN)]])
    updater = MemoryUpdater(provider)

    plan = await updater.analyze(MemorySnapshot(), (_commit(),))

    assert plan.operations == ()
    request = provider.agent_requests[0]
    assert request.tools == ()
    assert request.thinking_enabled is False


@pytest.mark.asyncio
async def test_updater_rejects_thinking_and_abnormal_stop() -> None:
    thinking = MemoryUpdater(
        FakeProvider([[ThinkingDelta(0, "no"), StreamEnd(StopReason.END_TURN)]])
    )
    with pytest.raises(MemoryUpdateError):
        await thinking.analyze(MemorySnapshot(), (_commit(),))

    abnormal = MemoryUpdater(FakeProvider([[StreamEnd(StopReason.MAX_TOKENS)]]))
    with pytest.raises(MemoryUpdateError):
        await abnormal.analyze(MemorySnapshot(), (_commit(),))
