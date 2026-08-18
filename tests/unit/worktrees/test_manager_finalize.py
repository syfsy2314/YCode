from pathlib import Path

import pytest

from ycode.worktrees import WorktreeDisposition, WorktreeLifecycle, WorktreeName

from .manager_helpers import initialize_repo, manager, run_git


@pytest.mark.asyncio
async def test_finalize_deletes_worktree_without_changes(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    target = manager(project)
    lease = await target.acquire("writer", "session", "clean")

    summary = await target.finalize(lease)

    assert summary.disposition is WorktreeDisposition.CLEANED
    assert not lease.path.exists()
    assert target.store.get(WorktreeName(lease.record.name)) is None
    assert not run_git(project, "branch", "--list", lease.record.branch)


@pytest.mark.asyncio
async def test_finalize_retains_dirty_and_committed_results(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    target = manager(project)
    dirty = await target.acquire("writer", "session", "dirty")
    (dirty.path / "result.txt").write_text("result\n", encoding="utf-8")

    dirty_summary = await target.finalize(dirty)
    committed = await target.acquire("writer", "session", "committed")
    (committed.path / "result.txt").write_text("result\n", encoding="utf-8")
    run_git(committed.path, "add", "result.txt")
    run_git(committed.path, "commit", "-m", "result")
    committed_summary = await target.finalize(committed)

    assert dirty_summary.disposition is WorktreeDisposition.RETAINED
    assert dirty_summary.status.untracked == ("result.txt",)
    assert committed_summary.disposition is WorktreeDisposition.RETAINED
    assert committed_summary.status.commits
    assert target.store.get(WorktreeName(dirty.record.name)).lifecycle is WorktreeLifecycle.RETAINED  # type: ignore[union-attr]
