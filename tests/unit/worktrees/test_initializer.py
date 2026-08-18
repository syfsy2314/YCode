import subprocess
from pathlib import Path

import pytest

from ycode.config.models import WorktreeConfig
from ycode.worktrees import GitWorktreeClient, WorktreeInitializer


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
    (path / ".gitignore").write_text(
        "settings.local.json\nignored/\nnode_modules/\n",
        encoding="utf-8",
    )
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    run_git(path, "add", ".gitignore", "tracked.txt")
    run_git(path, "commit", "-m", "base")
    return run_git(path, "rev-parse", "HEAD")


@pytest.mark.asyncio
async def test_initializer_copies_ignored_files_and_creates_directory_link(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    base = initialize_repo(project)
    (project / "settings.local.json").write_text('{"local":true}\n', encoding="utf-8")
    ignored = project / "ignored" / "nested" / "data.json"
    ignored.parent.mkdir(parents=True)
    ignored.write_text('{"fixture":true}\n', encoding="utf-8")
    dependency = project / "node_modules"
    dependency.mkdir()
    (dependency / "package.txt").write_text("dependency\n", encoding="utf-8")
    worktree = project / ".ycode" / "worktrees" / "agents" / "writer-a"
    git = GitWorktreeClient(project)
    await git.create(worktree, "ycode/agents--writer-a", base, "session/task")
    initializer = WorktreeInitializer(
        project,
        WorktreeConfig(
            copy_files=("settings.local.json",),
            ignored_file_globs=("ignored/**/*.json",),
            link_directories=("node_modules",),
        ),
        git,
    )

    result = await initializer.initialize(worktree)

    assert result.warnings == ()
    assert (worktree / "settings.local.json").read_text(encoding="utf-8") == ('{"local":true}\n')
    assert (worktree / "ignored" / "nested" / "data.json").is_file()
    assert (worktree / "node_modules" / "package.txt").read_text(encoding="utf-8") == (
        "dependency\n"
    )
    assert len(result.links) == 1
    assert result.links[0].source == dependency.resolve()


@pytest.mark.asyncio
async def test_initializer_warns_without_overwriting_or_copying_nonignored(
    tmp_path: Path,
) -> None:
    project = tmp_path / "repo"
    base = initialize_repo(project)
    (project / "settings.local.json").write_text("source\n", encoding="utf-8")
    worktree = project / ".ycode" / "worktrees" / "agents" / "writer-a"
    git = GitWorktreeClient(project)
    await git.create(worktree, "ycode/agents--writer-a", base, "session/task")
    (worktree / "settings.local.json").write_text("target\n", encoding="utf-8")
    initializer = WorktreeInitializer(
        project,
        WorktreeConfig(
            copy_files=("settings.local.json", "missing.local"),
            ignored_file_globs=("tracked.txt",),
            link_directories=("missing_modules",),
        ),
        git,
    )

    result = await initializer.initialize(worktree)

    assert (worktree / "settings.local.json").read_text(encoding="utf-8") == "target\n"
    codes = {warning.code for warning in result.warnings}
    assert codes == {"target_exists", "source_missing", "not_ignored", "link_source_missing"}
    assert all("source\n" not in warning.render() for warning in result.warnings)
