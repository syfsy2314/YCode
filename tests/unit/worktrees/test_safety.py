from datetime import UTC, datetime

import pytest

from ycode.worktrees import (
    WorktreeCommit,
    WorktreeLifecycle,
    WorktreeStatusSnapshot,
    deletion_decision,
)


def status(**overrides: object) -> WorktreeStatusSnapshot:
    values: dict[str, object] = {
        "head": "a" * 40,
        "checked_at": datetime.now(UTC),
    }
    values.update(overrides)
    return WorktreeStatusSnapshot(**values)  # type: ignore[arg-type]


def test_clean_base_is_safe_without_upstream() -> None:
    decision = deletion_decision(WorktreeLifecycle.RETAINED, status())

    assert decision.allowed


@pytest.mark.parametrize(
    ("snapshot", "reason"),
    [
        (status(modified=("app.py",)), "worktree_dirty"),
        (
            status(commits=(WorktreeCommit("b" * 40, "change"),)),
            "upstream_missing",
        ),
        (
            status(
                commits=(WorktreeCommit("b" * 40, "change"),),
                upstream="origin/change",
                unpushed_commits=(WorktreeCommit("b" * 40, "change"),),
            ),
            "commits_unpushed",
        ),
        (status(head=None, error="failed"), "git_status_unknown"),
    ],
)
def test_delete_is_fail_closed(snapshot: WorktreeStatusSnapshot, reason: str) -> None:
    decision = deletion_decision(WorktreeLifecycle.RETAINED, snapshot)

    assert not decision.allowed
    assert reason in decision.reasons


def test_active_worktree_is_never_normally_deleted() -> None:
    decision = deletion_decision(WorktreeLifecycle.ACTIVE, status())

    assert decision.reasons == ("worktree_active",)


def test_pushed_commits_are_safe_when_clean() -> None:
    decision = deletion_decision(
        WorktreeLifecycle.RETAINED,
        status(
            commits=(WorktreeCommit("b" * 40, "change"),),
            upstream="origin/change",
        ),
    )

    assert decision.allowed
