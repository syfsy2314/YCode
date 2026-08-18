import asyncio
import subprocess
from pathlib import Path

import pytest

from ycode.config.models import WorktreeConfig
from ycode.tools.command import PowerShellCommandRunner
from ycode.worktrees import (
    GitWorktreeClient,
    WorktreeInitializationError,
    WorktreeInitializer,
    git_config_environment,
)


def run_git(cwd: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(cwd), *arguments),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def setup_project(tmp_path: Path) -> tuple[Path, str]:
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(("git", "init", str(project)), check=True, capture_output=True)
    run_git(project, "config", "user.name", "YCode Test")
    run_git(project, "config", "user.email", "ycode@example.test")
    (project / "base.txt").write_text("base\n", encoding="utf-8")
    run_git(project, "add", "base.txt")
    run_git(project, "commit", "-m", "base")
    base = run_git(project, "rev-parse", "HEAD")
    return project, base


async def make_worktree(tmp_path: Path) -> tuple[Path, Path, GitWorktreeClient]:
    project, base = await asyncio.to_thread(setup_project, tmp_path)
    worktree = project / ".ycode" / "worktrees" / "agents" / "writer-a"
    git = GitWorktreeClient(project)
    await git.create(worktree, "ycode/agents--writer-a", base, "session/task")
    return project, worktree, git


def resolved(path: str | Path) -> Path:
    return Path(path).resolve()


@pytest.mark.asyncio
async def test_default_hooks_directory_is_shared(tmp_path: Path) -> None:
    project, worktree, git = await make_worktree(tmp_path)

    result = await WorktreeInitializer(project, WorktreeConfig(), git).initialize(worktree)

    assert result.git_environment == {}
    assert result.hooks_path == (project / ".git" / "hooks").resolve()


@pytest.mark.asyncio
async def test_custom_hooks_path_is_injected_into_child_git_environment(tmp_path: Path) -> None:
    project, worktree, git = await make_worktree(tmp_path)
    hooks = project / ".githooks"
    hooks.mkdir()
    run_git(project, "config", "core.hooksPath", ".githooks")

    result = await WorktreeInitializer(project, WorktreeConfig(), git).initialize(worktree)
    command = await PowerShellCommandRunner(result.git_environment).run(
        "git config --path --get core.hooksPath",
        worktree,
    )

    assert result.hooks_path == resolved(hooks)
    assert resolved(command.stdout.strip()) == resolved(hooks)


def test_git_config_environment_fails_closed_for_invalid_existing_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "invalid")

    with pytest.raises(WorktreeInitializationError, match="无法安全扩展"):
        git_config_environment("core.hooksPath", "C:/hooks")
