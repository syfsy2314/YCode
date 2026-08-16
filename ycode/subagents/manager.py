"""会话级子 Agent 任务管理器。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
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
    SubagentCreationMode,
    SubagentError,
    SubagentInvocation,
    SubagentRunMode,
    SubagentStatus,
    SubagentTaskView,
)
from ycode.subagents.runner import SubagentRunner


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
    ) -> None:
        self._config = config
        self._catalog = catalog
        self._clock = clock
        self._id_factory = id_factory
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
        )
