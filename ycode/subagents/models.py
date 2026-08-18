"""子 Agent 配置、角色和任务状态模型。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from ycode.core.events import TokenUsage
from ycode.security.models import PermissionMode

if TYPE_CHECKING:
    from ycode.worktrees.models import WorktreeLease, WorktreeSummary


class SubagentCreationMode(StrEnum):
    DEFINED = "defined"
    FORK = "fork"


class SubagentRunMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


class SubagentIsolation(StrEnum):
    NONE = "none"
    WORKTREE = "worktree"


class SubagentStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LIMIT_REACHED = "limit_reached"

    @property
    def terminal(self) -> bool:
        return self is not SubagentStatus.RUNNING


@dataclass(frozen=True, slots=True)
class SubagentRoleConfig:
    name: str
    description: str
    prompt: str
    model: str | None = None
    allowed_tools: frozenset[str] | None = None
    denied_tools: frozenset[str] = frozenset()
    max_rounds: int = 10
    permission: PermissionMode = PermissionMode.DEFAULT
    isolation: SubagentIsolation = SubagentIsolation.NONE

    def __post_init__(self) -> None:
        if not self.name or not self.description.strip() or not self.prompt.strip():
            raise ValueError("角色名称、说明和正文不能为空")
        if self.model is not None and not self.model.strip():
            raise ValueError("角色模型名称不能为空")
        if self.allowed_tools is not None:
            object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))
        object.__setattr__(self, "denied_tools", frozenset(self.denied_tools))
        if (
            not isinstance(self.max_rounds, int)
            or isinstance(self.max_rounds, bool)
            or self.max_rounds < 1
        ):
            raise ValueError("角色最大轮次必须是正整数")
        if not isinstance(self.permission, PermissionMode):
            raise TypeError("角色权限模式无效")
        if not isinstance(self.isolation, SubagentIsolation):
            raise TypeError("角色隔离模式无效")


@dataclass(frozen=True, slots=True)
class SubagentRoleSnapshot:
    config: SubagentRoleConfig
    source: str
    builtin: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.config, SubagentRoleConfig) or not self.source:
            raise ValueError("角色快照无效")


@dataclass(frozen=True, slots=True)
class SubagentRoleProblem:
    source: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.source or not self.code or not self.message:
            raise ValueError("角色诊断字段不能为空")


@dataclass(frozen=True, slots=True)
class SubagentRoleCatalogEntry:
    source: str
    normalized_name: str | None
    role: SubagentRoleSnapshot | None
    problems: tuple[SubagentRoleProblem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "problems", tuple(self.problems))
        if not self.source:
            raise ValueError("角色目录来源不能为空")
        if self.role is None and not self.problems:
            raise ValueError("不可用角色必须包含诊断")
        if self.role is not None and self.problems:
            raise ValueError("可用角色不能包含错误诊断")

    @property
    def available(self) -> bool:
        return self.role is not None


@dataclass(frozen=True, slots=True)
class SubagentRoleValidationEnvironment:
    tool_names: frozenset[str]
    provider_names: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_names", frozenset(self.tool_names))
        object.__setattr__(self, "provider_names", frozenset(self.provider_names))


@dataclass(frozen=True, slots=True)
class RunSubagentArguments:
    task: str
    role: str | None = None
    mode: SubagentRunMode | None = None
    shared_fallback_token: str | None = None
    isolation: SubagentIsolation | None = None


@dataclass(frozen=True, slots=True)
class SharedFallbackGrant:
    token: str
    session_id: str
    role: str
    task: str
    mode: SubagentRunMode
    isolation: SubagentIsolation
    issued_turn_id: str

    def __post_init__(self) -> None:
        if not all((self.token, self.session_id, self.role, self.task, self.issued_turn_id)):
            raise ValueError("共享降级授权字段不能为空")


@dataclass(frozen=True, slots=True)
class SubagentInvocation:
    task: str
    role: SubagentRoleSnapshot | None
    creation_mode: SubagentCreationMode
    run_mode: SubagentRunMode
    owner_turn_id: str
    isolation: SubagentIsolation = SubagentIsolation.NONE
    shared_fallback: bool = False
    worktree_lease: WorktreeLease | None = None
    parent_workspace: str | None = None


@dataclass(frozen=True, slots=True)
class SubagentError:
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("子 Agent 错误字段不能为空")


@dataclass(frozen=True, slots=True)
class SubagentTaskView:
    task_id: str
    status: SubagentStatus
    creation_mode: SubagentCreationMode
    run_mode: SubagentRunMode
    role: str | None
    task: str
    result: str | None
    usage: TokenUsage
    started_at: datetime
    finished_at: datetime | None = None
    error: SubagentError | None = None
    worktree: WorktreeSummary | None = None


@dataclass(slots=True)
class ManagedSubagentTask:
    view: SubagentTaskView
    owner_turn_id: str
    runtime_task: asyncio.Task[SubagentTaskView] | None = None
    notification_pending: bool = False


@dataclass(frozen=True, slots=True)
class AgentRuntimeNotification:
    task: SubagentTaskView
    completed_at: datetime = field(compare=True)
