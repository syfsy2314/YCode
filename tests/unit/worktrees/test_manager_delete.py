from dataclasses import replace
from pathlib import Path

import pytest

from ycode.worktrees import WorktreeLifecycle, WorktreeManagerError

from .manager_helpers import initialize_repo, manager, run_git


@pytest.mark.asyncio
async def test_delete_allows_clean_retained_worktree_without_upstream(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    target = manager(project)
    lease = await target.acquire("writer", "session", "clean")
    target.store.save(replace(lease.record, lifecycle=WorktreeLifecycle.RETAINED))

    await target.delete(lease.record.name)

    assert not lease.path.exists()


@pytest.mark.asyncio
async def test_delete_rejects_dirty_and_requires_confirmation_for_force(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    target = manager(project)
    lease = await target.acquire("writer", "session", "dirty")
    (lease.path / "result.txt").write_text("result\n", encoding="utf-8")
    await target.finalize(lease)

    with pytest.raises(WorktreeManagerError, match="拒绝删除"):
        await target.delete(lease.record.name)
    with pytest.raises(WorktreeManagerError, match="确认"):
        await target.delete(lease.record.name, force=True)
    await target.delete(lease.record.name, force=True, confirmed=True)
    assert not lease.path.exists()


@pytest.mark.asyncio
async def test_delete_rejects_unpushed_commit_and_active_even_with_force(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    target = manager(project)
    committed = await target.acquire("writer", "session", "commit")
    (committed.path / "result.txt").write_text("result\n", encoding="utf-8")
    run_git(committed.path, "add", "result.txt")
    run_git(committed.path, "commit", "-m", "result")
    await target.finalize(committed)

    with pytest.raises(WorktreeManagerError, match="拒绝删除"):
        await target.delete(committed.record.name)
    active = await target.acquire("writer", "session", "active")
    with pytest.raises(WorktreeManagerError, match="活动"):
        await target.delete(active.record.name, force=True, confirmed=True)
