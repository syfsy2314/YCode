from pathlib import Path

import pytest

from ycode.memory import (
    MemoryAction,
    MemoryEntry,
    MemoryOperation,
    MemoryStore,
    MemoryStoreError,
    MemoryType,
    MemoryUpdatePlan,
)


def _write_entry(
    root: Path,
    path: str = "user-prefers-any.md",
    name: str = "偏好 any 语法",
    description: str = "用户要求用 any 替代 interface{}",
    memory_type: str = "user_preference",
    body: str = "使用 any。",
) -> None:
    memory = root / ".ycode" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    (memory / path).write_text(
        f"---\nname: {name}\ndescription: {description}\ntype: {memory_type}\n---\n{body}\n",
        encoding="utf-8",
    )


def _entry(body: str = "使用 any。") -> MemoryEntry:
    return MemoryEntry(
        "user-prefers-any.md",
        "偏好 any 语法",
        "用户要求用 any 替代 interface{}",
        MemoryType.USER_PREFERENCE,
        body,
    )


def test_load_missing_index_returns_empty_snapshot(tmp_path: Path) -> None:
    assert MemoryStore(tmp_path).load().entries == ()


def test_load_returns_normalized_valid_index(tmp_path: Path) -> None:
    _write_entry(tmp_path)
    index = tmp_path / ".ycode" / "memory" / "MEMORY.md"
    index.write_text(
        "\n- [偏好 any 语法](user-prefers-any.md) — 用户要求用 any 替代 interface{}\n",
        encoding="utf-8",
    )

    snapshot = MemoryStore(tmp_path).load()

    assert snapshot.entries == (_entry(),)
    assert snapshot.index_content.startswith("- [偏好 any 语法]")
    assert snapshot.warnings == ()


def test_load_skips_bad_lines_mismatches_and_escape(tmp_path: Path) -> None:
    _write_entry(tmp_path)
    index = tmp_path / ".ycode" / "memory" / "MEMORY.md"
    index.write_text(
        "bad line\n"
        "- [错误名称](user-prefers-any.md) — 用户要求用 any 替代 interface{}\n"
        "- [逃逸](../outside.md) — 不应读取\n",
        encoding="utf-8",
    )

    snapshot = MemoryStore(tmp_path).load()

    assert snapshot.entries == ()
    assert len(snapshot.warnings) == 3


def test_apply_creates_updates_and_deletes_indexed_entries(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    created = _entry()
    store.apply(MemoryUpdatePlan((MemoryOperation(MemoryAction.CREATE, created.path, created),)))

    updated = _entry("始终使用 any。")
    snapshot = store.apply(
        MemoryUpdatePlan((MemoryOperation(MemoryAction.UPDATE, updated.path, updated),))
    )
    assert snapshot.entries[0].body == "始终使用 any。"

    snapshot = store.apply(MemoryUpdatePlan((MemoryOperation(MemoryAction.DELETE, updated.path),)))
    assert snapshot.entries == ()
    assert not (store.memory_root / updated.path).exists()


def test_apply_rejects_metadata_change_and_unindexed_delete(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    entry = _entry()
    store.apply(MemoryUpdatePlan((MemoryOperation(MemoryAction.CREATE, entry.path, entry),)))
    changed = MemoryEntry(
        entry.path,
        "新名称",
        entry.description,
        entry.type,
        entry.body,
    )

    with pytest.raises(MemoryStoreError):
        store.apply(MemoryUpdatePlan((MemoryOperation(MemoryAction.UPDATE, entry.path, changed),)))
    with pytest.raises(MemoryStoreError):
        store.apply(MemoryUpdatePlan((MemoryOperation(MemoryAction.DELETE, "user-missing.md"),)))


def test_apply_index_replace_failure_leaves_old_index_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MemoryStore(tmp_path)
    entry = _entry()
    store.apply(MemoryUpdatePlan((MemoryOperation(MemoryAction.CREATE, entry.path, entry),)))
    real_replace = __import__("os").replace

    def fail_index(source: str | Path, target: str | Path) -> None:
        if Path(target).name == "MEMORY.md":
            raise OSError("injected")
        real_replace(source, target)

    monkeypatch.setattr("ycode.memory.store.os.replace", fail_index)
    with pytest.raises(MemoryStoreError):
        store.apply(
            MemoryUpdatePlan(
                (MemoryOperation(MemoryAction.UPDATE, entry.path, _entry("新正文。")),)
            )
        )

    assert store.load().entries[0].name == entry.name
