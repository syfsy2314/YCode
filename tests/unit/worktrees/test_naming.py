import pytest

from ycode.worktrees import (
    WorktreeName,
    managed_worktree_name,
    worktree_name_from_branch,
)


def test_name_branch_mapping_is_reversible() -> None:
    name = WorktreeName("agents/review-task_1")

    assert name.branch == "ycode/agents--review-task_1"
    assert worktree_name_from_branch(name.branch) == name


@pytest.mark.parametrize(
    "value",
    [
        "",
        "agents//task",
        "agents/./task",
        "agents/../task",
        "agents\\task",
        "agents/CON",
        "agents/con.txt",
        "agents/task.",
        "agents/task--two",
        "agents/UPPER",
        "agents/has space",
        "a/b/c/d/e",
        f"agents/{'a' * 33}",
        "a" * 97,
    ],
)
def test_name_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        WorktreeName(value)


def test_managed_name_uses_only_role_and_task_identity() -> None:
    first = managed_worktree_name("Review--Writer", "task-123")
    same = managed_worktree_name("Review--Writer", "task-123")
    retry = managed_worktree_name("Review--Writer", "task-123", attempt=1)

    assert first == same
    assert first != retry
    assert first.value.startswith("agents/review-writer-")
    assert all(len(segment) <= 32 for segment in first.segments)
    assert worktree_name_from_branch(first.branch) == first
