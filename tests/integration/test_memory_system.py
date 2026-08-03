import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.agent import TurnMessage
from ycode.core import ChatMessage, StopReason, StreamEnd, TextDelta
from ycode.memory import MemoryStore, MemoryUpdater
from ycode.prompt import ProjectContextLoader, SupplementKind
from ycode.session import SessionManager

NOW = datetime(2026, 8, 3, 1, 2, 3, tzinfo=UTC)


@pytest.mark.asyncio
async def test_project_context_sessions_and_exit_memory_update_work_together(
    tmp_path: Path,
) -> None:
    (tmp_path / "YCODE.md").write_text("Use repository conventions.", encoding="utf-8")
    memory = MemoryStore(tmp_path)
    project = ProjectContextLoader(tmp_path, memory).load()
    assert project.supplements[0].kind is SupplementKind.PROJECT_INSTRUCTIONS

    sessions = SessionManager(tmp_path, clock=lambda: NOW)
    first = await sessions.commit_turn(
        (
            TurnMessage(ChatMessage.user_text("prefer any"), NOW),
            TurnMessage(ChatMessage.assistant_text("understood"), NOW),
        )
    )
    sessions.begin_new()
    second = await sessions.commit_turn(
        (
            TurnMessage(ChatMessage.user_text("remember this across sessions"), NOW),
            TurnMessage(ChatMessage.assistant_text("done"), NOW),
        )
    )
    assert (await sessions.load(first.session_id)).history[0].text == "prefer any"
    assert (await sessions.load(second.session_id)).history[0].text.startswith("remember")

    response = json.dumps(
        {
            "operations": [
                {
                    "action": "create",
                    "path": "user-prefers-any.md",
                    "entry": {
                        "path": "user-prefers-any.md",
                        "name": "Prefer any",
                        "description": "Use any syntax",
                        "type": "user_preference",
                        "body": "Use `any` instead of `interface{}`.",
                    },
                }
            ]
        }
    )
    provider = FakeProvider([[TextDelta(0, response), StreamEnd(StopReason.END_TURN)]])
    plan = await MemoryUpdater(provider).analyze(memory.load(), (first, second))
    snapshot = memory.apply(plan)

    assert snapshot.entries[0].path == "user-prefers-any.md"
    transcript = json.loads(provider.agent_requests[0].messages[0].text)
    assert [item["session_id"] for item in transcript["new_conversations"]] == [
        first.session_id,
        second.session_id,
    ]
