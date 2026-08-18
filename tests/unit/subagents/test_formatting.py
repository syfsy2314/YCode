import json
from datetime import UTC, datetime, timedelta

from ycode.core import TokenUsage
from ycode.subagents import (
    SubagentCreationMode,
    SubagentRunMode,
    SubagentStatus,
    SubagentTaskView,
    format_runtime_notification,
    format_task_detail,
    format_task_list,
    format_tool_result,
)


def task() -> SubagentTaskView:
    started = datetime(2026, 8, 16, tzinfo=UTC)
    return SubagentTaskView(
        "abcdef1234567890",
        SubagentStatus.COMPLETED,
        SubagentCreationMode.DEFINED,
        SubagentRunMode.ASYNC,
        "review",
        "inspect",
        "done",
        TokenUsage(10, 3, 4, 5),
        started,
        started + timedelta(seconds=2),
    )


def test_tool_detail_and_notification_share_runtime_fields() -> None:
    view = task()
    payload = json.loads(format_tool_result(view))
    detail = json.loads(format_task_detail(view))
    notice = format_runtime_notification(view)

    expected = {
        "task_id",
        "status",
        "creation_mode",
        "role",
        "result",
        "usage",
        "started_at",
        "finished_at",
        "error",
        "worktree",
    }
    assert set(payload) == expected
    assert all(detail[name] == payload[name] for name in expected)
    assert view.task_id in notice.content


def test_task_list_handles_empty_and_shows_duration_and_total_tokens() -> None:
    assert "没有" in format_task_list(())

    text = format_task_list((task(),))

    assert "abcdef123456" in text
    assert "completed" in text
    assert "2.0s" in text
    assert "22" in text
