from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ycode.worktrees import WorktreeLifecycle, WorktreeName

from .manager_helpers import initialize_repo, manager


@pytest.mark.asyncio
async def test_cleanup_deletes_only_expired_safe_candidates(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    now = datetime(2026, 8, 18, tzinfo=UTC)
    target = manager(project, ttl=2, clock=lambda: now)
    expired = await target.acquire("writer", "session", "expired")
    fresh = await target.acquire("writer", "session", "fresh")
    target.store.save(
        replace(
            expired.record,
            lifecycle=WorktreeLifecycle.RETAINED,
            created_at=now - timedelta(hours=4),
            last_activity_at=now - timedelta(hours=3),
        )
    )
    target.store.save(replace(fresh.record, lifecycle=WorktreeLifecycle.RETAINED))

    report = await target.cleanup()

    assert report.deleted == (expired.record.name,)
    assert fresh.path.exists()


@pytest.mark.asyncio
async def test_cleanup_keeps_dirty_and_continues_after_corrupt_record(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    now = datetime(2026, 8, 18, tzinfo=UTC)
    target = manager(project, ttl=1, clock=lambda: now)
    dirty = await target.acquire("writer", "session", "dirty")
    safe = await target.acquire("writer", "session", "safe")
    for lease in (dirty, safe):
        target.store.save(
            replace(
                lease.record,
                lifecycle=WorktreeLifecycle.RETAINED,
                created_at=now - timedelta(hours=3),
                last_activity_at=now - timedelta(hours=2),
            )
        )
    (dirty.path / "result.txt").write_text("result\n", encoding="utf-8")
    corrupt = target.store.records_root / "agents" / "corrupt.json"
    corrupt.write_text("not json", encoding="utf-8")

    report = await target.cleanup()

    assert report.deleted == (safe.record.name,)
    assert dirty.path.exists()
    assert any("corrupt" in warning for warning in report.warnings)
    assert any("worktree_dirty" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_startup_marks_dead_active_owner_interrupted(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    target = manager(project, process_alive=lambda _pid: False)
    lease = await target.acquire("writer", "session", "dead")

    report = await target.reconcile_startup()

    record = target.store.get(WorktreeName(lease.record.name))
    assert report.interrupted == (lease.record.name,)
    assert record is not None and record.lifecycle is WorktreeLifecycle.INTERRUPTED


def test_starting_inside_managed_worktree_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    initialize_repo(project)
    target = manager(project)
    nested = project / ".ycode" / "worktrees" / "agents" / "writer" / "nested"
    nested.mkdir(parents=True)

    with pytest.raises(Exception, match="不能从"):
        target.ensure_start_allowed(nested)
