from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ycode.prompt import EnvironmentCollector, SupplementKind

FIXED_TIME = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone(timedelta(hours=8), "CST"))


@pytest.mark.asyncio
async def test_environment_collector_renders_compact_git_snapshot(tmp_path: Path) -> None:
    async def git_runner(workspace: Path) -> str:
        assert workspace == tmp_path
        return "## main...origin/main\nM  staged.py\n M modified.py\n?? new.py\n"

    snapshot = await EnvironmentCollector(
        tmp_path,
        git_runner=git_runner,
    ).collect(now=FIXED_TIME)

    assert snapshot.git is not None
    assert snapshot.git.branch == "main"
    assert snapshot.git.staged == 1
    assert snapshot.git.modified == 1
    assert snapshot.git.untracked == 1
    assert "Workspace: " + str(tmp_path) in snapshot.content
    assert "Operating system:" in snapshot.content
    assert "Shell: PowerShell" in snapshot.content
    assert "2026-07-30T12:34:56+08:00" in snapshot.content
    assert "Git status: dirty (staged=1, modified=1, untracked=1)" in snapshot.content
    assert snapshot.to_supplement().kind is SupplementKind.ENVIRONMENT


@pytest.mark.asyncio
async def test_environment_collector_omits_git_when_unavailable(tmp_path: Path) -> None:
    async def git_runner(workspace: Path) -> None:
        del workspace
        return None

    snapshot = await EnvironmentCollector(
        tmp_path,
        git_runner=git_runner,
    ).collect(now=FIXED_TIME)

    assert snapshot.git is None
    assert "Git " not in snapshot.content
    assert "Local time:" in snapshot.content


@pytest.mark.asyncio
async def test_environment_collector_isolates_git_failure(tmp_path: Path) -> None:
    async def git_runner(workspace: Path) -> str:
        del workspace
        raise RuntimeError("git failed with sensitive details")

    snapshot = await EnvironmentCollector(
        tmp_path,
        git_runner=git_runner,
    ).collect(now=FIXED_TIME)

    assert snapshot.git is None
    assert "sensitive" not in snapshot.content


@pytest.mark.asyncio
async def test_environment_collector_reports_clean_detached_repository(tmp_path: Path) -> None:
    async def git_runner(workspace: Path) -> str:
        del workspace
        return "## HEAD (no branch)\n"

    snapshot = await EnvironmentCollector(
        tmp_path,
        git_runner=git_runner,
    ).collect(now=FIXED_TIME)

    assert snapshot.git is not None
    assert snapshot.git.branch == "HEAD (detached)"
    assert not snapshot.git.dirty
    assert "Git status: clean" in snapshot.content
