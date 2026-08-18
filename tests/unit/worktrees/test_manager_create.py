from pathlib import Path

import pytest

from ycode.config.models import WorktreeConfig
from ycode.worktrees import WorktreeManager, WorktreeName, managed_worktree_name

from .manager_helpers import initialize_repo, manager, run_git


@pytest.mark.asyncio
async def test_acquire_creates_locked_worktree_from_committed_head(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    base = initialize_repo(project)
    (project / "base.txt").write_text("parent dirty\n", encoding="utf-8")
    (project / "untracked.txt").write_text("parent only\n", encoding="utf-8")

    lease = await manager(project).acquire("writer", "session", "task")

    assert lease.record.base_head == base
    assert (lease.path / "base.txt").read_text(encoding="utf-8") == "base\n"
    assert not (lease.path / "untracked.txt").exists()
    assert "locked" in run_git(project, "worktree", "list", "--porcelain")


@pytest.mark.asyncio
async def test_acquire_retries_branch_conflict_without_taking_it_over(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    first = managed_worktree_name("writer", "task")
    run_git(project, "branch", first.branch)

    lease = await manager(project).acquire("writer", "session", "task")

    assert lease.record.name == managed_worktree_name("writer", "task", attempt=1).value
    assert run_git(project, "show-ref", "--verify", f"refs/heads/{first.branch}")


@pytest.mark.asyncio
async def test_existing_matching_directory_recovers_without_git_subprocess(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    first_manager = manager(project, instance="first")
    lease = await first_manager.acquire("writer", "session", "task")

    class ExplodingGit:
        def __getattr__(self, name: str):
            raise AssertionError(f"快速恢复不得访问 Git client：{name}")

    recovered = WorktreeManager(
        project,
        WorktreeConfig(),
        store=first_manager.store,
        git=ExplodingGit(),  # type: ignore[arg-type]
        process_id=9002,
        process_instance_id="second",
        process_alive=lambda _pid: False,
    )
    second = await recovered.acquire("writer", "session", "task")

    assert second.path == lease.path
    assert second.record.owner.process_instance_id == "second"


@pytest.mark.asyncio
async def test_existing_unrecorded_directory_is_not_adopted(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    target = manager(project)
    name = managed_worktree_name("writer", "task")
    target.store.expected_worktree_path(name).mkdir(parents=True)

    lease = await target.acquire("writer", "session", "task")

    assert lease.record.name != name.value
    assert target.store.get(WorktreeName(name.value)) is None
