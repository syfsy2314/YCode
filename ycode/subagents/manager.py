"""会话级子 Agent 任务管理器。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import uuid4

from ycode.agent import AgentRequestSnapshot
from ycode.config.models import SubagentConfig
from ycode.core.events import TokenUsage
from ycode.prompt.models import SystemSupplement
from ycode.subagents.catalog import SubagentRoleCatalog
from ycode.subagents.formatting import format_runtime_notification
from ycode.subagents.models import (
    ManagedSubagentTask,
    RunSubagentArguments,
    SharedFallbackGrant,
    SubagentCreationMode,
    SubagentError,
    SubagentInvocation,
    SubagentIsolation,
    SubagentRunMode,
    SubagentStatus,
    SubagentTaskView,
)
from ycode.subagents.runner import SubagentRunner
from ycode.worktrees.manager import WorktreeManager, WorktreeManagerError


class SubagentManagerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SubagentManager:
    def __init__(
        self,
        config: SubagentConfig,
        catalog: SubagentRoleCatalog,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = lambda: uuid4().hex,
        session_id_provider: Callable[[], str | None] | None = None,
        worktree_manager: WorktreeManager | None = None,
        fallback_token_factory: Callable[[], str] = lambda: token_urlsafe(24),
    ) -> None:
        self._config = config
        self._catalog = catalog
        self._clock = clock
        self._id_factory = id_factory
        self._session_id_provider = session_id_provider or (lambda: None)
        self._worktree_manager = worktree_manager
        self._fallback_token_factory = fallback_token_factory
        self._fallback_grants: dict[str, SharedFallbackGrant] = {}
        self._runner: SubagentRunner | None = None
        self._tasks: dict[str, ManagedSubagentTask] = {}

    def bind(self, runner: SubagentRunner) -> None:
        if self._runner is not None:
            raise RuntimeError("子 Agent 管理器不能重复绑定")
        self._runner = runner

    @property
    def tasks(self) -> tuple[SubagentTaskView, ...]:
        return tuple(record.view for record in self._tasks.values())

    async def start(
        self,
        arguments: RunSubagentArguments,
        parent: AgentRequestSnapshot,
    ) -> SubagentTaskView:
        runner = self._runner
        if runner is None:
            raise SubagentManagerError("manager_not_ready", "子 Agent 管理器尚未完成初始化。")
        invocation = self._invocation(arguments, parent)
        if self._running_count() >= self._config.max_concurrent:
            raise SubagentManagerError(
                "concurrency_limit",
                f"子 Agent 并发已达到上限 {self._config.max_concurrent}。",
            )
        task_id = self._new_task_id()
        invocation = await self._prepare_workspace(arguments, invocation, parent, task_id)
        started_at = self._clock()
        initial = SubagentTaskView(
            task_id,
            SubagentStatus.RUNNING,
            invocation.creation_mode,
            invocation.run_mode,
            invocation.role.config.name if invocation.role is not None else None,
            invocation.task,
            None,
            TokenUsage(),
            started_at,
        )
        record = ManagedSubagentTask(initial, invocation.owner_turn_id)
        self._tasks[task_id] = record
        runtime_task = asyncio.create_task(self._execute(record, invocation, parent))
        record.runtime_task = runtime_task
        if invocation.run_mode is SubagentRunMode.ASYNC:
            return initial
        await runtime_task
        return record.view

    async def _prepare_workspace(
        self,
        arguments: RunSubagentArguments,
        invocation: SubagentInvocation,
        parent: AgentRequestSnapshot,
        task_id: str,
    ) -> SubagentInvocation:
        isolated = (
            invocation.role is not None
            and invocation.role.config.isolation is SubagentIsolation.WORKTREE
        )
        token = arguments.shared_fallback_token
        if token is not None:
            if not isolated:
                raise SubagentManagerError(
                    "fallback_not_applicable", "当前子 Agent 任务不需要共享降级授权。"
                )
            self._consume_fallback(token, invocation, parent)
            return replace(invocation, shared_fallback=True)
        if not isolated:
            return invocation
        session_id = self._session_id_provider()
        if session_id is None:
            raise SubagentManagerError("session_unavailable", "当前会话尚未取得稳定 ID。")
        try:
            if self._worktree_manager is None:
                raise WorktreeManagerError("worktree_unavailable", "Worktree 功能未装配。")
            lease = await self._worktree_manager.acquire(
                invocation.role.config.name,
                session_id,
                task_id,
            )
        except WorktreeManagerError as error:
            grant = self._issue_fallback(session_id, invocation, parent)
            raise SubagentManagerError(
                "isolation_unavailable",
                f"隔离环境不可用：{error} 请先询问用户是否允许共享执行；授权 token：{grant.token}",
            ) from error
        return replace(
            invocation,
            worktree_lease=lease,
            parent_workspace=str(self._worktree_manager.project_root),
        )

    def _issue_fallback(
        self,
        session_id: str,
        invocation: SubagentInvocation,
        parent: AgentRequestSnapshot,
    ) -> SharedFallbackGrant:
        assert invocation.role is not None
        token = self._fallback_token_factory()
        grant = SharedFallbackGrant(
            token,
            session_id,
            invocation.role.config.name,
            invocation.task,
            invocation.run_mode,
            parent.turn_id,
        )
        self._fallback_grants[token] = grant
        return grant

    def _consume_fallback(
        self,
        token: str,
        invocation: SubagentInvocation,
        parent: AgentRequestSnapshot,
    ) -> None:
        grant = self._fallback_grants.get(token)
        session_id = self._session_id_provider()
        role = invocation.role.config.name if invocation.role is not None else ""
        if grant is None:
            raise SubagentManagerError("fallback_token_invalid", "共享降级授权无效或已使用。")
        if parent.turn_id == grant.issued_turn_id:
            raise SubagentManagerError("fallback_same_turn", "共享降级必须在后续用户回合执行。")
        if (
            session_id != grant.session_id
            or role != grant.role
            or invocation.task != grant.task
            or invocation.run_mode is not grant.mode
        ):
            raise SubagentManagerError("fallback_mismatch", "共享降级授权与当前任务不匹配。")
        del self._fallback_grants[token]

    def get(self, task_id: str) -> SubagentTaskView:
        return self._resolve(task_id).view

    async def stop(self, task_id: str) -> SubagentTaskView:
        record = self._resolve(task_id)
        if record.view.status.terminal:
            raise SubagentManagerError("task_not_running", "指定子 Agent 任务已经结束。")
        await self._cancel_records((record,))
        return record.view

    async def cancel_owned(self, turn_id: str) -> None:
        records = tuple(
            record
            for record in self._tasks.values()
            if record.owner_turn_id == turn_id and not record.view.status.terminal
        )
        await self._cancel_records(records)

    async def clear(self) -> None:
        await self._cancel_records(
            tuple(record for record in self._tasks.values() if not record.view.status.terminal)
        )
        self._tasks.clear()
        self._fallback_grants.clear()

    def take_pending(self) -> tuple[SystemSupplement, ...]:
        records = sorted(
            (record for record in self._tasks.values() if record.notification_pending),
            key=lambda record: record.view.finished_at or record.view.started_at,
        )
        for record in records:
            record.notification_pending = False
        return tuple(format_runtime_notification(record.view) for record in records)

    async def _execute(
        self,
        record: ManagedSubagentTask,
        invocation: SubagentInvocation,
        parent: AgentRequestSnapshot,
    ) -> None:
        assert self._runner is not None
        try:
            result = await self._runner.run(record.view.task_id, invocation, parent)
        except asyncio.CancelledError:
            result = self._cancelled_view(record.view)
        except Exception as error:
            result = self._failed_view(record.view, error)
        if invocation.worktree_lease is not None and self._worktree_manager is not None:
            try:
                summary = await self._worktree_manager.finalize(invocation.worktree_lease)
                result = replace(result, worktree=summary)
            except Exception as error:
                result = replace(
                    result,
                    error=SubagentError(
                        str(getattr(error, "code", "worktree_finalize_failed")),
                        str(error),
                    ),
                )
        record.view = result
        if invocation.run_mode is SubagentRunMode.ASYNC:
            record.notification_pending = True

    def _invocation(
        self,
        arguments: RunSubagentArguments,
        parent: AgentRequestSnapshot,
    ) -> SubagentInvocation:
        task = arguments.task.strip()
        if not task:
            raise SubagentManagerError("task_empty", "子 Agent 任务不能为空。")
        role_name = arguments.role.strip() if arguments.role is not None else None
        if role_name:
            role = self._catalog.get_available(role_name)
            if role is None:
                raise SubagentManagerError(
                    "role_not_found",
                    f"子 Agent 角色不存在或不可用：{role_name}",
                )
            run_mode = arguments.mode or SubagentRunMode.SYNC
            creation_mode = SubagentCreationMode.DEFINED
        else:
            role = None
            if arguments.mode is SubagentRunMode.SYNC:
                raise SubagentManagerError("fork_sync_invalid", "Fork 子 Agent 必须异步执行。")
            run_mode = SubagentRunMode.ASYNC
            creation_mode = SubagentCreationMode.FORK
        return SubagentInvocation(
            task,
            role,
            creation_mode,
            run_mode,
            parent.turn_id,
        )

    def _resolve(self, task_id: str) -> ManagedSubagentTask:
        value = task_id.strip()
        if value in self._tasks:
            return self._tasks[value]
        matches = [record for key, record in self._tasks.items() if key.startswith(value)]
        if not matches:
            raise SubagentManagerError("task_not_found", f"子 Agent 任务不存在：{value}")
        if len(matches) > 1:
            raise SubagentManagerError("task_id_ambiguous", f"任务 ID 前缀不唯一：{value}")
        return matches[0]

    async def _cancel_records(self, records: tuple[ManagedSubagentTask, ...]) -> None:
        runtime_tasks = tuple(
            record.runtime_task
            for record in records
            if record.runtime_task is not None and not record.runtime_task.done()
        )
        for task in runtime_tasks:
            task.cancel()
        if runtime_tasks:
            await asyncio.gather(*runtime_tasks, return_exceptions=True)
        for record in records:
            if record.view.status.terminal:
                continue
            record.view = self._cancelled_view(record.view)
            if record.view.run_mode is SubagentRunMode.ASYNC:
                record.notification_pending = True

    def _running_count(self) -> int:
        return sum(not record.view.status.terminal for record in self._tasks.values())

    def _new_task_id(self) -> str:
        while True:
            task_id = self._id_factory()
            if task_id and task_id not in self._tasks:
                return task_id

    def _cancelled_view(self, task: SubagentTaskView) -> SubagentTaskView:
        return SubagentTaskView(
            task.task_id,
            SubagentStatus.CANCELLED,
            task.creation_mode,
            task.run_mode,
            task.role,
            task.task,
            task.result,
            task.usage,
            task.started_at,
            self._clock(),
            SubagentError("cancelled", "子 Agent 已取消。"),
            task.worktree,
        )

    def _failed_view(self, task: SubagentTaskView, error: Exception) -> SubagentTaskView:
        return SubagentTaskView(
            task.task_id,
            SubagentStatus.FAILED,
            task.creation_mode,
            task.run_mode,
            task.role,
            task.task,
            None,
            task.usage,
            task.started_at,
            self._clock(),
            SubagentError(str(getattr(error, "code", "subagent_failed")), str(error)),
            task.worktree,
        )
