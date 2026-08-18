import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ycode.worktrees import (
    RECORD_VERSION,
    WorktreeLifecycle,
    WorktreeName,
    WorktreeOwner,
    WorktreeRecord,
    WorktreeStore,
    WorktreeStoreError,
)


def make_record(store: WorktreeStore, name: WorktreeName, owner: WorktreeOwner) -> WorktreeRecord:
    now = datetime.now(UTC)
    return WorktreeRecord(
        RECORD_VERSION,
        name.value,
        str(store.expected_worktree_path(name)),
        name.branch,
        "a" * 40,
        "a" * 40,
        WorktreeLifecycle.ACTIVE,
        now,
        now,
        True,
        ("copy_failed: local.json",),
        owner,
    )


def test_store_round_trips_and_lists_records(tmp_path: Path) -> None:
    store = WorktreeStore(tmp_path)
    owner = WorktreeOwner("session", "task", 10, "instance")
    first = make_record(store, WorktreeName("agents/review-a"), owner)
    second = make_record(store, WorktreeName("agents/write-b"), owner)

    store.save(second)
    store.save(first)

    assert store.get(WorktreeName(first.name)) == first
    assert store.list_records() == (first, second)
    assert not tuple(store.records_root.rglob("*.tmp"))

    store.delete(WorktreeName(first.name))
    assert store.get(WorktreeName(first.name)) is None


def test_store_rejects_tampered_identity_and_unknown_fields(tmp_path: Path) -> None:
    store = WorktreeStore(tmp_path)
    name = WorktreeName("agents/review-a")
    record = make_record(store, name, WorktreeOwner("session", "task", 10, "instance"))

    with pytest.raises(WorktreeStoreError, match="身份"):
        store.save(replace(record, path=str(tmp_path / "outside")))

    store.save(record)
    path = store.records_root / "agents" / "review-a.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["unexpected"] = True
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(WorktreeStoreError, match="损坏"):
        store.get(name)


def test_store_lock_is_exclusive_and_reclaims_dead_owner(tmp_path: Path) -> None:
    store = WorktreeStore(tmp_path, process_alive=lambda process_id: process_id == 111)
    alive = WorktreeOwner("session", "task-a", 111, "alive")
    other = WorktreeOwner("session", "task-b", 333, "other")

    with store.mutation(alive):
        with pytest.raises(WorktreeStoreError, match="占用"):
            with store.mutation(other):
                pass

    store.state_root.mkdir(parents=True, exist_ok=True)
    store._lock_path.write_text('{"process_id":222}', encoding="utf-8")  # noqa: SLF001
    with store.mutation(other):
        assert store._lock_path.is_file()  # noqa: SLF001
    assert not store._lock_path.exists()  # noqa: SLF001


def test_store_fails_closed_for_corrupt_lock(tmp_path: Path) -> None:
    store = WorktreeStore(tmp_path, process_alive=lambda _process_id: False)
    store.state_root.mkdir(parents=True, exist_ok=True)
    store._lock_path.write_text("not-json", encoding="utf-8")  # noqa: SLF001

    with pytest.raises(WorktreeStoreError, match="无法确认"):
        with store.mutation(WorktreeOwner("session", "task", 10, "instance")):
            pass
