"""Worktree 生命周期与状态模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class WorktreeLifecycle(StrEnum):
    CREATING = "creating"
    ACTIVE = "active"
    RETAINED = "retained"
    INTERRUPTED = "interrupted"


class WorktreeDisposition(StrEnum):
    CLEANED = "cleaned"
    RETAINED = "retained"


@dataclass(frozen=True, slots=True)
class WorktreeOwner:
    session_id: str
    task_id: str
    process_id: int
    process_instance_id: str

    def __post_init__(self) -> None:
        if not self.session_id or not self.task_id or not self.process_instance_id:
            raise ValueError("Worktree owner 字段不能为空")
        if (
            not isinstance(self.process_id, int)
            or isinstance(self.process_id, bool)
            or self.process_id < 1
        ):
            raise ValueError("Worktree owner 进程 ID 必须是正整数")


@dataclass(frozen=True, slots=True)
class WorktreeCommit:
    oid: str
    subject: str

    def __post_init__(self) -> None:
        if not self.oid or not self.subject:
            raise ValueError("Worktree commit 字段不能为空")


@dataclass(frozen=True, slots=True)
class WorktreeStatusSnapshot:
    head: str | None
    staged: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    untracked: tuple[str, ...] = ()
    commits: tuple[WorktreeCommit, ...] = ()
    diff_stat: str = ""
    upstream: str | None = None
    unpushed_commits: tuple[WorktreeCommit, ...] = ()
    checked_at: datetime | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "staged", tuple(self.staged))
        object.__setattr__(self, "modified", tuple(self.modified))
        object.__setattr__(self, "untracked", tuple(self.untracked))
        object.__setattr__(self, "commits", tuple(self.commits))
        object.__setattr__(self, "unpushed_commits", tuple(self.unpushed_commits))
        if self.checked_at is not None and self.checked_at.utcoffset() is None:
            raise ValueError("Worktree 状态时间必须包含时区")

    @property
    def dirty(self) -> bool:
        return bool(self.staged or self.modified or self.untracked)

    @property
    def has_changes(self) -> bool:
        return bool(self.commits) or self.dirty


@dataclass(frozen=True, slots=True)
class WorktreeRecord:
    version: int
    name: str
    path: str
    branch: str
    base_head: str
    current_head: str | None
    lifecycle: WorktreeLifecycle
    created_at: datetime
    last_activity_at: datetime
    initialization_complete: bool
    initialization_warnings: tuple[str, ...]
    owner: WorktreeOwner
    last_status: WorktreeStatusSnapshot | None = None
    hooks_path: str | None = None
    custom_hooks: bool = False
    linked_directories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.version < 1
            or not self.name
            or not self.path
            or not self.branch
            or not self.base_head
        ):
            raise ValueError("Worktree 记录身份字段无效")
        if self.created_at.utcoffset() is None or self.last_activity_at.utcoffset() is None:
            raise ValueError("Worktree 记录时间必须包含时区")
        if self.last_activity_at < self.created_at:
            raise ValueError("Worktree 最后活动时间不能早于创建时间")
        object.__setattr__(self, "initialization_warnings", tuple(self.initialization_warnings))
        object.__setattr__(self, "linked_directories", tuple(self.linked_directories))
        if self.custom_hooks and not self.hooks_path:
            raise ValueError("自定义 Git Hooks 必须记录有效路径")


@dataclass(frozen=True, slots=True)
class WorktreeLease:
    record: WorktreeRecord
    git_environment: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.record.lifecycle is not WorktreeLifecycle.ACTIVE:
            raise ValueError("Worktree lease 必须指向 active 记录")
        object.__setattr__(self, "git_environment", tuple(self.git_environment))

    @property
    def path(self) -> Path:
        return Path(self.record.path)

    @property
    def environment(self) -> dict[str, str]:
        return dict(self.git_environment)


@dataclass(frozen=True, slots=True)
class WorktreeSummary:
    name: str
    path: str
    branch: str
    base_head: str
    current_head: str | None
    disposition: WorktreeDisposition
    status: WorktreeStatusSnapshot
    initialization_warnings: tuple[str, ...] = ()
    blocking_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "initialization_warnings", tuple(self.initialization_warnings))
        object.__setattr__(self, "blocking_reasons", tuple(self.blocking_reasons))


@dataclass(frozen=True, slots=True)
class WorktreeDeleteDecision:
    allowed: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if self.allowed and self.reasons:
            raise ValueError("允许删除时不能包含阻止原因")
        if not self.allowed and not self.reasons:
            raise ValueError("拒绝删除时必须包含阻止原因")
