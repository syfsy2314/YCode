import subprocess
from pathlib import Path

import pytest

from ycode.worktrees import GitWorktreeClient, deletion_decision
from ycode.worktrees.models import WorktreeLifecycle


def run_git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(cwd), *arguments),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def initialize_repo(path: Path) -> str:
    path.mkdir()
    subprocess.run(("git", "init", str(path)), check=True, capture_output=True)
    run_git(path, "config", "user.name", "YCode Test")
    run_git(path, "config", "user.email", "ycode@example.test")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    run_git(path, "add", "base.txt")
    run_git(path, "commit", "-m", "base")
    return run_git(path, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_client_creates_inspects_and_removes_local_worktree(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    base = initialize_repo(project)
    client = GitWorktreeClient(project)
    worktree = project / ".ycode" / "worktrees" / "agents" / "writer-a"
    branch = "ycode/agents--writer-a"

    assert await client.ensure_repository() == base
    assert not await client.branch_exists(branch)
    await client.create(worktree, branch, base, "session/task")

    entries = await client.list_worktrees()
    linked = next(item for item in entries if item.branch == branch)
    assert linked.path == worktree.resolve()
    assert linked.locked

    clean = await client.status(worktree, base)
    assert not clean.has_changes

    (worktree / "change.txt").write_text("change\n", encoding="utf-8")
    dirty = await client.status(worktree, base)
    assert dirty.untracked == ("change.txt",)

    run_git(worktree, "add", "change.txt")
    run_git(worktree, "commit", "-m", "isolated change")
    committed = await client.status(worktree, base)
    assert len(committed.commits) == 1
    assert not deletion_decision(WorktreeLifecycle.RETAINED, committed).allowed

    remote_ref = "refs/remotes/origin/agents--writer-a"
    run_git(project, "update-ref", remote_ref, committed.head or "")
    run_git(project, "config", "remote.origin.url", "https://example.invalid/repo.git")
    run_git(
        project,
        "config",
        "remote.origin.fetch",
        "+refs/heads/*:refs/remotes/origin/*",
    )
    run_git(project, "config", f"branch.{branch}.remote", "origin")
    run_git(project, "config", f"branch.{branch}.merge", "refs/heads/agents--writer-a")
    pushed = await client.status(worktree, base)
    assert pushed.upstream == "origin/agents--writer-a"
    assert pushed.unpushed_commits == ()
    assert deletion_decision(WorktreeLifecycle.RETAINED, pushed).allowed

    await client.unlock(worktree)
    await client.remove(worktree)
    await client.delete_branch(branch)
    assert not worktree.exists()
    assert not await client.branch_exists(branch)
