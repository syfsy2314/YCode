import subprocess
from pathlib import Path

import pytest

from ycode.config import WorktreeConfig
from ycode.worktrees import WorktreeDisposition, WorktreeManager


def git(cwd: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(cwd), *arguments),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()


def initialize(path: Path) -> str:
    subprocess.run(("git", "init", str(path)), check=True, capture_output=True)
    git(path, "config", "user.name", "YCode Test")
    git(path, "config", "user.email", "ycode@example.test")
    (path / ".gitignore").write_text(".ycode/worktrees/\n", encoding="utf-8")
    (path / "tracked.txt").write_text("committed\n", encoding="utf-8")
    git(path, "add", ".gitignore", "tracked.txt")
    git(path, "commit", "-m", "base")
    return git(path, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_manager_isolates_parent_dirty_state_and_retains_only_child_result(
    tmp_path: Path,
) -> None:
    base = initialize(tmp_path)
    (tmp_path / "tracked.txt").write_text("parent dirty\n", encoding="utf-8")
    (tmp_path / "parent-only.txt").write_text("parent\n", encoding="utf-8")
    manager = WorktreeManager(tmp_path, WorktreeConfig(), process_alive=lambda _pid: False)

    lease = await manager.acquire("writer", "session", "task")
    assert lease.record.base_head == base
    assert (lease.path / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert not (lease.path / "parent-only.txt").exists()
    (lease.path / "child-only.txt").write_text("child\n", encoding="utf-8")

    summary = await manager.finalize(lease)

    assert summary.disposition is WorktreeDisposition.RETAINED
    assert summary.status.untracked == ("child-only.txt",)
    assert not (tmp_path / "child-only.txt").exists()
