import pytest

from ycode.memory import (
    MemoryAction,
    MemoryEntry,
    MemoryOperation,
    MemorySnapshot,
    MemoryType,
    MemoryUpdatePlan,
)


def _entry(**changes: object) -> MemoryEntry:
    values: dict[str, object] = {
        "path": "user-prefers-any.md",
        "name": "偏好 any 语法",
        "description": "用户要求用 any 替代 interface{}",
        "type": MemoryType.USER_PREFERENCE,
        "body": "在 Go 代码中使用 `any`。",
    }
    values.update(changes)
    return MemoryEntry(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("memory_type", "path"),
    [
        (MemoryType.USER_PREFERENCE, "user-style.md"),
        (MemoryType.CORRECTION_FEEDBACK, "feedback-api-shape.md"),
        (MemoryType.PROJECT_KNOWLEDGE, "project-migrations.md"),
        (MemoryType.REFERENCE, "reference-design-doc.md"),
    ],
)
def test_memory_entry_accepts_all_categories(memory_type: MemoryType, path: str) -> None:
    assert _entry(type=memory_type, path=path).type is memory_type


@pytest.mark.parametrize(
    "path",
    ["../user-escape.md", "nested/user-note.md", "/user-note.md", "user-NOTE.md"],
)
def test_memory_entry_rejects_unsafe_path(path: str) -> None:
    with pytest.raises(ValueError):
        _entry(path=path)


def test_memory_entry_requires_matching_type_prefix() -> None:
    with pytest.raises(ValueError, match="前缀"):
        _entry(type=MemoryType.REFERENCE)


def test_memory_operation_validates_payload() -> None:
    entry = _entry()
    assert MemoryOperation(MemoryAction.CREATE, entry.path, entry).entry is entry
    with pytest.raises(ValueError):
        MemoryOperation(MemoryAction.CREATE, entry.path)
    with pytest.raises(ValueError):
        MemoryOperation(MemoryAction.DELETE, entry.path, entry)


def test_memory_snapshot_and_plan_reject_duplicate_paths() -> None:
    entry = _entry()
    with pytest.raises(ValueError, match="重复"):
        MemorySnapshot(entries=(entry, entry))
    operation = MemoryOperation(MemoryAction.UPDATE, entry.path, entry)
    with pytest.raises(ValueError, match="重复"):
        MemoryUpdatePlan((operation, operation))
