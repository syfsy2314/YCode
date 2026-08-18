from datetime import UTC, datetime

import pytest

from ycode.worktrees import (
    WorktreeDeleteDecision,
    WorktreeLifecycle,
    WorktreeOwner,
    WorktreeRecord,
    WorktreeStatusSnapshot,
)


def test_status_reports_dirty_and_changes() -> None:
    clean = WorktreeStatusSnapshot("a" * 40)
    dirty = WorktreeStatusSnapshot("a" * 40, modified=("app.py",))

    assert not clean.dirty
    assert not clean.has_changes
    assert dirty.dirty
    assert dirty.has_changes


def test_record_requires_aware_ordered_times() -> None:
    owner = WorktreeOwner("session", "task", 12, "process")
    now = datetime.now(UTC)
    record = WorktreeRecord(
        1,
        "agents/review-a",
        "C:/project/.ycode/worktrees/agents/review-a",
        "ycode/agents--review-a",
        "a" * 40,
        "a" * 40,
        WorktreeLifecycle.ACTIVE,
        now,
        now,
        True,
        (),
        owner,
    )

    assert record.owner is owner
    with pytest.raises(ValueError, match="时区"):
        WorktreeRecord(
            1,
            record.name,
            record.path,
            record.branch,
            record.base_head,
            record.current_head,
            record.lifecycle,
            datetime.now(),
            datetime.now(),
            True,
            (),
            owner,
        )


def test_delete_decision_is_consistent() -> None:
    assert WorktreeDeleteDecision(True).allowed
    assert not WorktreeDeleteDecision(False, ("dirty",)).allowed
    with pytest.raises(ValueError, match="阻止原因"):
        WorktreeDeleteDecision(False)
