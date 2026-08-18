from datetime import UTC, datetime
from pathlib import Path

import pytest

from ycode.tools import ToolContext, ToolError
from ycode.tools.builtin import GlobArguments, GlobTool, GrepArguments, GrepTool
from ycode.tools.paths import WorkspaceMount, WorkspacePathResolver
from ycode.worktrees import (
    RECORD_VERSION,
    WorktreeAccessGuard,
    WorktreeLifecycle,
    WorktreeName,
    WorktreeOwner,
    WorktreeRecord,
    WorktreeStore,
)


def save_record(
    store: WorktreeStore,
    name: WorktreeName,
    lifecycle: WorktreeLifecycle,
) -> Path:
    path = store.expected_worktree_path(name)
    path.mkdir(parents=True)
    now = datetime.now(UTC)
    store.save(
        WorktreeRecord(
            RECORD_VERSION,
            name.value,
            str(path),
            name.branch,
            "a" * 40,
            "a" * 40,
            lifecycle,
            now,
            now,
            True,
            (),
            WorktreeOwner("session", name.value, 10, "instance"),
        )
    )
    return path


def resolved(path: Path) -> Path:
    return path.resolve()


def test_guard_denies_active_and_unknown_but_allows_retained(tmp_path: Path) -> None:
    store = WorktreeStore(tmp_path)
    active = save_record(store, WorktreeName("agents/active-a"), WorktreeLifecycle.ACTIVE)
    retained = save_record(
        store,
        WorktreeName("agents/retained-a"),
        WorktreeLifecycle.RETAINED,
    )
    (active / "secret.txt").write_text("active\n", encoding="utf-8")
    (retained / "result.txt").write_text("retained\n", encoding="utf-8")
    unknown = store.worktrees_root / "agents" / "unknown-a"
    unknown.mkdir()
    (unknown / "file.txt").write_text("unknown\n", encoding="utf-8")
    resolver = WorkspacePathResolver(tmp_path, policy=WorktreeAccessGuard(store))

    with pytest.raises(ToolError) as active_error:
        resolver.resolve_existing_file(active / "secret.txt")
    assert active_error.value.code == "worktree_active"
    assert resolver.resolve_existing_file(retained / "result.txt").is_file()
    with pytest.raises(ToolError) as unknown_error:
        resolver.resolve_existing_file(unknown / "file.txt")
    assert unknown_error.value.code == "worktree_access_unknown"


@pytest.mark.asyncio
async def test_search_tools_exclude_active_worktree_from_parent_search(tmp_path: Path) -> None:
    store = WorktreeStore(tmp_path)
    active = save_record(store, WorktreeName("agents/active-a"), WorktreeLifecycle.ACTIVE)
    retained = save_record(
        store,
        WorktreeName("agents/retained-a"),
        WorktreeLifecycle.RETAINED,
    )
    (active / "secret.txt").write_text("shared-marker active\n", encoding="utf-8")
    (retained / "result.txt").write_text("shared-marker retained\n", encoding="utf-8")
    resolver = WorkspacePathResolver(tmp_path, policy=WorktreeAccessGuard(store))
    context = ToolContext(resolved(tmp_path))

    glob_result = await GlobTool(resolver).execute(
        GlobArguments(pattern=".ycode/worktrees/agents/**/*.txt"),
        context,
    )
    grep_result = await GrepTool(resolver).execute(
        GrepArguments(pattern="shared-marker"),
        context,
    )

    assert "retained-a/result.txt" in glob_result.content
    assert "active-a/secret.txt" not in glob_result.content
    assert "retained" in grep_result.content
    assert "active-a/secret.txt" not in grep_result.content


@pytest.mark.asyncio
async def test_virtual_memory_mount_is_searchable_without_being_writable(tmp_path: Path) -> None:
    workspace = tmp_path / "worktree"
    memory = tmp_path / "memory"
    workspace.mkdir()
    memory.mkdir()
    (memory / "topic.md").write_text("memory-marker\n", encoding="utf-8")
    resolver = WorkspacePathResolver(
        workspace,
        mounts=(WorkspaceMount(Path(".ycode/memory"), memory, virtual=True),),
    )
    context = ToolContext(resolved(workspace))

    glob_result = await GlobTool(resolver).execute(
        GlobArguments(pattern=".ycode/memory/**/*.md"),
        context,
    )
    grep_result = await GrepTool(resolver).execute(
        GrepArguments(pattern="memory-marker", path=".ycode/memory"),
        context,
    )

    assert glob_result.content == ".ycode/memory/topic.md"
    assert ".ycode/memory/topic.md:1" in grep_result.content
