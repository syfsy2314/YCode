import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from ycode.config.models import WorktreeConfig
from ycode.worktrees import WorktreeLifecycle, WorktreeManager


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
    (path / ".gitignore").write_text(".ycode/worktrees/\n", encoding="utf-8")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    run_git(path, "add", ".gitignore", "base.txt")
    run_git(path, "commit", "-m", "base")
    return run_git(path, "rev-parse", "HEAD")


def manager(
    project: Path,
    *,
    ttl: int = 24,
    clock=None,
    process_alive=lambda _pid: False,
    instance: str = "manager-instance",
) -> WorktreeManager:
    options = {
        "process_id": 9001,
        "process_instance_id": instance,
        "process_alive": process_alive,
    }
    if clock is not None:
        options["clock"] = clock
    return WorktreeManager(
        project,
        WorktreeConfig(cleanup_ttl_hours=ttl),
        **options,
    )


def age_record(manager: WorktreeManager, name: str, then: datetime) -> None:
    record = manager.store.get(
        __import__("ycode.worktrees", fromlist=["WorktreeName"]).WorktreeName(name)
    )
    assert record is not None
    manager.store.save(
        replace(
            record,
            lifecycle=WorktreeLifecycle.RETAINED,
            created_at=min(record.created_at, then),
            last_activity_at=then.astimezone(UTC),
        )
    )
