"""活动 Worktree 的工具路径访问保护。"""

from __future__ import annotations

import os
from pathlib import Path

from ycode.tools.errors import ToolError
from ycode.tools.paths import PathOperation
from ycode.worktrees.models import WorktreeLifecycle
from ycode.worktrees.naming import WorktreeName
from ycode.worktrees.store import WorktreeStore, WorktreeStoreError


class WorktreeAccessGuard:
    def __init__(self, store: WorktreeStore) -> None:
        self._store = store
        self._agents_root = store.worktrees_root / "agents"

    def check(
        self,
        lexical_path: Path,
        resolved_path: Path | None,
        operation: PathOperation,
    ) -> None:
        del resolved_path, operation
        name = self._candidate_name(lexical_path)
        if name is None:
            return
        try:
            record = self._store.get(name)
        except WorktreeStoreError as error:
            raise ToolError("worktree_access_unknown", "活动 Worktree 状态无法确认。") from error
        if record is None:
            raise ToolError("worktree_access_unknown", "受管 Worktree 缺少有效管理记录。")
        if record.lifecycle is WorktreeLifecycle.ACTIVE:
            raise ToolError("worktree_active", "目标 Worktree 正由其他 Agent 任务占用。")

    def excluded_directories(self, search_root: Path) -> tuple[Path, ...]:
        if not _overlaps(search_root, self._agents_root):
            return ()
        try:
            records = self._store.list_records()
        except WorktreeStoreError as error:
            raise ToolError("worktree_access_unknown", "活动 Worktree 状态无法确认。") from error
        return tuple(
            Path(record.path).resolve(strict=False)
            for record in records
            if record.lifecycle is WorktreeLifecycle.ACTIVE
        )

    def _candidate_name(self, path: Path) -> WorktreeName | None:
        try:
            relative = path.relative_to(self._agents_root)
        except ValueError:
            return None
        if not relative.parts:
            return None
        try:
            return WorktreeName(f"agents/{relative.parts[0]}")
        except ValueError as error:
            raise ToolError("worktree_access_unknown", "受管 Worktree 路径无效。") from error


def _overlaps(first: Path, second: Path) -> bool:
    first_key = os.path.normcase(str(first.resolve(strict=False)))
    second_key = os.path.normcase(str(second.resolve(strict=False)))
    try:
        common = os.path.normcase(os.path.commonpath((first_key, second_key)))
    except ValueError:
        return False
    return common in {first_key, second_key}
