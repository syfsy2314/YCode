"""AgentEvent 会话、模式与整轮历史事务。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ycode.agent.contracts import (
    AgentMode,
    AgentTermination,
    AgentTurn,
    ConversationRunner,
    TurnMessage,
)
from ycode.agent.events import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentEvent,
    AgentLimitReachedEvent,
    ContextCompactedEvent,
    ContextCompactionFailedEvent,
    ContextCompactionNotNeededEvent,
    FinalResponseEvent,
    HookNoticeEvent,
    McpStatusEvent,
    ModeChangedEvent,
    PermissionGrantsClearedEvent,
    PermissionModeChangedEvent,
    SessionRestoredEvent,
    UserMessageEvent,
)
from ycode.context import (
    ContextCompactionError,
    ContextCompactionNotNeeded,
    ContextManager,
    RestoreContextResult,
)
from ycode.core.messages import ChatMessage
from ycode.hooks import HookContextFactory, HookEventName, HookRuntime
from ycode.mcp.models import McpStatusProvider
from ycode.memory import (
    MemoryStore,
    MemoryUpdater,
    MemoryUpdateReport,
    MemoryUpdateStatus,
)
from ycode.prompt import PromptRuntimeContext
from ycode.prompt.models import SupplementKind, SystemSupplement
from ycode.security import ApprovalChoice, PermissionMode, PermissionSession
from ycode.session.manager import SessionManager
from ycode.session.models import SessionCommit, SessionStorageError
from ycode.skills.commands import build_skill_command_definitions
from ycode.skills.models import SkillInvocationSource, SkillTaskScope
from ycode.skills.runtime import SkillRuntime, SkillRuntimeError

if TYPE_CHECKING:
    from ycode.commands import CommandRuntime

type _TerminalEvent = (
    FinalResponseEvent | AgentLimitReachedEvent | AgentCancelledEvent | AgentErrorEvent
)


def _expanded_skill_task(name: str, arguments: str | None) -> str:
    detail = (
        f"Invocation arguments:\n{arguments}"
        if arguments is not None and arguments.strip()
        else "No arguments were provided."
    )
    return f'Use the "{name}" skill for this task.\n\n{detail}'


class ChatSession:
    def __init__(
        self,
        runner: ConversationRunner,
        permission_session: PermissionSession | None = None,
        mcp_status_provider: McpStatusProvider | None = None,
        context_manager: ContextManager | None = None,
        session_manager: SessionManager | None = None,
        prompt_runtime: PromptRuntimeContext | None = None,
        memory_store: MemoryStore | None = None,
        memory_updater: MemoryUpdater | None = None,
        startup_warnings: tuple[str, ...] = (),
        command_runtime: CommandRuntime | None = None,
        skill_runtime: SkillRuntime | None = None,
        hook_runtime: HookRuntime | None = None,
        hook_context: HookContextFactory | None = None,
    ) -> None:
        self._runner = runner
        self._history: list[ChatMessage] = []
        self._mode = AgentMode.AGENT
        self._permission_session = permission_session
        self._mcp_status_provider = mcp_status_provider
        self._context_manager = context_manager
        self._session_manager = session_manager
        self._prompt_runtime = prompt_runtime
        self._memory_store = memory_store
        self._memory_updater = memory_updater
        self._startup_warnings = tuple(startup_warnings)
        self._command_runtime = command_runtime
        self._skill_runtime = skill_runtime
        if (hook_runtime is None) != (hook_context is None):
            raise ValueError("Hook 运行时和上下文工厂必须同时提供")
        self._hook_runtime = hook_runtime
        self._hook_context = hook_context
        self._active_turn: AgentTurn | None = None
        self._active_compaction: asyncio.Task[object] | None = None
        self._turn_finished = asyncio.Event()
        self._turn_finished.set()
        self._closed = False
        self._new_commits: list[SessionCommit] = []
        self._memory_update_task: asyncio.Task[MemoryUpdateReport] | None = None
        self._startup_restore_event: SessionRestoredEvent | None = None

    @property
    def history(self) -> tuple[ChatMessage, ...]:
        return tuple(self._history)

    @property
    def mode(self) -> AgentMode:
        return self._mode

    @property
    def permission_mode(self) -> PermissionMode | None:
        return self._permission_session.mode if self._permission_session is not None else None

    @property
    def mcp_status(self):
        if self._mcp_status_provider is None:
            return None
        return self._mcp_status_provider.snapshot()

    @property
    def new_commits(self) -> tuple[SessionCommit, ...]:
        return tuple(self._new_commits)

    @property
    def startup_warnings(self) -> tuple[str, ...]:
        return self._startup_warnings

    async def start_hooks(self) -> None:
        if self._hook_runtime is None or self._hook_context is None:
            return
        result = await self._hook_runtime.dispatch(
            self._hook_context.simple(HookEventName.SESSION_START)
        )
        self._startup_warnings = (*self._startup_warnings, *result.notices)

    @property
    def startup_restore_event(self) -> SessionRestoredEvent | None:
        return self._startup_restore_event

    @property
    def command_runtime(self) -> CommandRuntime | None:
        return self._command_runtime

    def change_mode(self, target: AgentMode) -> ModeChangedEvent:
        if target not in self._runner.supported_modes:
            raise ValueError("当前对话运行器不支持 plan-only 模式。")
        previous = self._mode
        self._mode = target
        return ModeChangedEvent(previous, target)

    def permission_status(self) -> PermissionModeChangedEvent:
        if self._permission_session is None:
            raise ValueError("当前对话未启用权限管理。")
        return PermissionModeChangedEvent(
            self._permission_session.mode,
            self._permission_session.mode,
        )

    def change_permission_mode(self, target: PermissionMode) -> PermissionModeChangedEvent:
        if self._permission_session is None:
            raise ValueError("当前对话未启用权限管理。")
        previous = self._permission_session.mode
        self._permission_session.set_mode(target)
        return PermissionModeChangedEvent(previous, target)

    def clear_permission_grants(self) -> PermissionGrantsClearedEvent:
        if self._permission_session is None:
            raise ValueError("当前对话未启用权限管理。")
        count = self._permission_session.grant_count
        self._permission_session.clear()
        return PermissionGrantsClearedEvent(count)

    async def restore(self, session_id: str | None = None) -> SessionRestoredEvent:
        if self._session_manager is None:
            raise SessionStorageError("当前对话未启用持久化会话")
        snapshot = (
            await self._session_manager.load(session_id)
            if session_id is not None
            else await self._session_manager.load_latest()
        )
        candidate = (
            await self._context_manager.prepare_restore(snapshot.history, snapshot.memory)
            if self._context_manager is not None
            else RestoreContextResult(snapshot.history, snapshot.memory)
        )
        if candidate.checkpoint_required:
            assert candidate.memory is not None
            await self._session_manager.append_checkpoint(
                candidate.memory,
                candidate.history,
                session_id=snapshot.session_id,
                covered_turn_id=snapshot.last_turn_id,
            )
        self._session_manager.activate(snapshot)
        if self._context_manager is not None:
            self._context_manager.activate_restore(candidate)
        self._history[:] = candidate.history
        skill_warnings: tuple[str, ...] = ()
        if self._skill_runtime is not None:
            skill_warnings = await self._skill_runtime.restore(snapshot.active_skill_names)
        self._mode = AgentMode.AGENT
        if self._permission_session is not None:
            self._permission_session.clear()
        if self._prompt_runtime is not None:
            self._prompt_runtime.reset_mode()
        now = datetime.now(UTC)
        clear_queued = getattr(self._runner, "clear_queued_request_supplements", None)
        if callable(clear_queued):
            clear_queued(SupplementKind.REMINDER)
        elapsed = now - snapshot.last_active_at
        if elapsed.total_seconds() >= 24 * 60 * 60:
            queue = getattr(self._runner, "queue_request_supplement", None)
            if callable(queue):
                last_active = snapshot.last_active_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
                current = now.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
                queue(
                    SystemSupplement(
                        SupplementKind.REMINDER,
                        "This restored session has a long time gap. "
                        f"Last active: {last_active}; current time: {current}; "
                        f"elapsed: {elapsed.days} days. Re-check time-sensitive facts.",
                    )
                )
        event = SessionRestoredEvent(
            snapshot.session_id,
            len(candidate.history),
            (*tuple(warning.message for warning in snapshot.warnings), *skill_warnings),
        )
        self._startup_restore_event = event
        return event

    async def compact_context(self) -> AsyncIterator[AgentEvent]:
        if self._context_manager is None:
            yield ContextCompactionNotNeededEvent()
            return
        task = asyncio.create_task(
            self._context_manager.prepare_manual_compaction(tuple(self._history))
        )
        self._active_compaction = task
        self._turn_finished.clear()
        try:
            try:
                candidate = await task
            except ContextCompactionNotNeeded:
                yield ContextCompactionNotNeededEvent()
            except ContextCompactionError as error:
                yield ContextCompactionFailedEvent(error.report)
            except asyncio.CancelledError:
                yield AgentCancelledEvent("当前上下文压缩已取消。")
            else:
                try:
                    if self._session_manager is not None:
                        await self._session_manager.append_checkpoint(
                            candidate.memory,
                            candidate.history,
                        )
                except (SessionStorageError, ValueError):
                    yield AgentErrorEvent(
                        "session_storage_error",
                        "上下文检查点保存失败，当前历史未改变。",
                    )
                else:
                    self._context_manager.activate_compaction(candidate)
                    self._history[:] = candidate.history
                    if self._hook_runtime is not None and self._hook_context is not None:
                        hook_result = await self._hook_runtime.dispatch(
                            self._hook_context.compacted("manual", candidate.report)
                        )
                        for notice in hook_result.notices:
                            yield HookNoticeEvent(notice)
                    yield ContextCompactedEvent(candidate.report)
        finally:
            if not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            self._active_compaction = None
            self._turn_finished.set()

    async def stream_reply(
        self,
        model_text: str,
        *,
        display_text: str | None = None,
        skill_scope: SkillTaskScope | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if not model_text.strip():
            raise ValueError("消息不能为空")
        if self._closed:
            raise RuntimeError("会话已关闭")
        if self._active_turn is not None or self._active_compaction is not None:
            raise RuntimeError("已有 Agent 回合正在运行")

        user_message = ChatMessage.user_text(model_text)
        display_message = ChatMessage.user_text(display_text or model_text)
        stripped = model_text.strip()
        command = stripped.lower()
        if command == "/resume" or command.startswith("/resume "):
            yield UserMessageEvent(display_message)
            session_id = stripped[len("/resume") :].strip()
            if not session_id:
                yield AgentErrorEvent("invalid_resume_command", "用法：/resume <session-id>")
                return
            try:
                yield await self.restore(session_id)
            except (SessionStorageError, ValueError):
                yield AgentErrorEvent("session_restore_failed", "会话恢复失败，当前会话未改变。")
            return
        if command in {"/plan", "/agent"}:
            yield UserMessageEvent(display_message)
            target = AgentMode.PLAN_ONLY if command == "/plan" else AgentMode.AGENT
            try:
                event = self.change_mode(target)
            except ValueError:
                yield AgentErrorEvent(
                    "unsupported_mode",
                    "当前对话运行器不支持 plan-only 模式。",
                )
                return
            yield event
            return
        if command == "/mcp":
            yield UserMessageEvent(display_message)
            if self._mcp_status_provider is None:
                yield AgentErrorEvent("mcp_unavailable", "当前没有 MCP 状态信息。")
            else:
                yield McpStatusEvent(self._mcp_status_provider.snapshot())
            return
        if command == "/compact" and self._context_manager is not None:
            yield UserMessageEvent(display_message)
            async for event in self.compact_context():
                yield event
            return
        if self._permission_session is not None and command.startswith("/permission"):
            yield UserMessageEvent(display_message)
            parts = command.split()
            if len(parts) == 1:
                yield self.permission_status()
                return
            if len(parts) != 2:
                yield AgentErrorEvent(
                    "invalid_permission_command",
                    "用法：/permission [strict|default|allow|clear]",
                )
                return
            argument = parts[1]
            if argument == "clear":
                yield self.clear_permission_grants()
                return
            try:
                target_permission = PermissionMode(argument)
            except ValueError:
                yield AgentErrorEvent(
                    "invalid_permission_command",
                    "用法：/permission [strict|default|allow|clear]",
                )
                return
            yield self.change_permission_mode(target_permission)
            return

        scoped_start = getattr(self._runner, "start_turn_with_skill_scope", None)
        turn = (
            scoped_start(tuple(self._history), user_message, self._mode, skill_scope)
            if skill_scope is not None and callable(scoped_start)
            else self._runner.start_turn(tuple(self._history), user_message, self._mode)
        )
        self._active_turn = turn
        self._turn_finished.clear()
        terminal_event: _TerminalEvent | None = None
        try:
            yield UserMessageEvent(display_message)
            async for event in turn:
                if isinstance(
                    event,
                    FinalResponseEvent
                    | AgentLimitReachedEvent
                    | AgentCancelledEvent
                    | AgentErrorEvent,
                ):
                    terminal_event = event
                else:
                    yield event

            result = turn.result
            if result is None:
                raise RuntimeError("AgentTurn 结束时缺少结果")
            if result.termination is AgentTermination.COMPLETED:
                if self._session_manager is not None:
                    checkpoint = None
                    if (
                        result.context_commit is not None
                        and result.context_commit.checkpoint_required
                        and result.context_commit.memory is not None
                    ):
                        checkpoint = (
                            result.context_commit.memory,
                            result.context_commit.history,
                        )
                    try:
                        commit = await self._session_manager.commit_turn(
                            result.turn_messages,
                            checkpoint=checkpoint,
                            active_skill_names=(
                                result.active_skill_names
                                if self._skill_runtime is not None
                                else None
                            ),
                        )
                    except (SessionStorageError, ValueError):
                        if self._skill_runtime is not None and result.skill_scope is not None:
                            self._skill_runtime.discard_task(result.skill_scope)
                        yield AgentErrorEvent(
                            "session_storage_error",
                            "会话保存失败，本轮未提交到当前历史。",
                        )
                        return
                    self._new_commits.append(commit)
                if self._skill_runtime is not None and result.skill_scope is not None:
                    self._skill_runtime.commit_task(result.skill_scope)
                if result.context_commit is not None and self._context_manager is not None:
                    self._context_manager.commit(result.context_commit)
                    self._history[:] = result.context_commit.history
                else:
                    self._history.extend(result.messages)
            if terminal_event is None:
                raise RuntimeError("AgentTurn 结束时缺少终态事件")
            yield terminal_event
        finally:
            if turn.result is None:
                turn.cancel()
                with suppress(asyncio.CancelledError, RuntimeError):
                    async for _ in turn:
                        pass
            self._active_turn = None
            self._turn_finished.set()

    async def stream_skill(
        self,
        name: str,
        arguments: str | None,
        raw_text: str,
    ) -> AsyncIterator[AgentEvent]:
        if self._skill_runtime is None:
            yield UserMessageEvent(ChatMessage.user_text(raw_text))
            yield AgentErrorEvent("skills_unavailable", "当前会话未启用 Skill。")
            return
        scope = self._skill_runtime.begin_task(self._mode)
        try:
            snapshot = self._skill_runtime.load_current(name)
            isolated = snapshot.config.execution_mode.value == "isolated"
            if isolated:
                yield UserMessageEvent(ChatMessage.user_text(raw_text))
            result = await self._skill_runtime.invoke(
                name,
                arguments,
                SkillInvocationSource.EXPLICIT,
                scope,
            )
        except SkillRuntimeError as error:
            self._skill_runtime.discard_task(scope)
            if "isolated" not in locals() or not isolated:
                yield UserMessageEvent(ChatMessage.user_text(raw_text))
            yield AgentErrorEvent(error.code, str(error))
            return
        if result.final_handoff is not None:
            user_message = ChatMessage.user_text(_expanded_skill_task(name, arguments))
            final_message = ChatMessage.assistant_text(result.final_handoff)
            now = datetime.now(UTC)
            messages = (TurnMessage(user_message, now), TurnMessage(final_message, now))
            try:
                if self._session_manager is not None:
                    commit = await self._session_manager.commit_turn(
                        messages,
                        active_skill_names=self._skill_runtime.active_names,
                    )
                    self._new_commits.append(commit)
            except (SessionStorageError, ValueError):
                self._skill_runtime.discard_task(scope)
                yield AgentErrorEvent(
                    "session_storage_error",
                    "会话保存失败，本轮未提交到当前历史。",
                )
                return
            self._skill_runtime.discard_task(scope)
            self._history.extend((user_message, final_message))
            yield FinalResponseEvent(final_message)
            return
        async for event in self.stream_reply(
            _expanded_skill_task(name, arguments),
            display_text=raw_text,
            skill_scope=scope,
        ):
            yield event

    def skills_status(self) -> str:
        if self._skill_runtime is None:
            return "当前会话未启用 Skill。"
        lines = ["Project Skills:"]
        for entry in self._skill_runtime.catalog_entries:
            if entry.snapshot is None:
                reasons = "; ".join(problem.message for problem in entry.problems)
                lines.append(f"- {entry.directory_name}: unavailable — {reasons}")
                continue
            state = (
                "active" if entry.snapshot.name in self._skill_runtime.active_names else "available"
            )
            lines.append(f"- {entry.snapshot.name}: {state} — {entry.snapshot.description}")
        if len(lines) == 1:
            lines.append("- none")
        return "\n".join(lines)

    def skill_status(self, name: str) -> str:
        if self._skill_runtime is None:
            return "当前会话未启用 Skill。"
        entry = next(
            (
                item
                for item in self._skill_runtime.catalog_entries
                if item.directory_name.casefold() == name.casefold()
                or (item.snapshot is not None and item.snapshot.name.casefold() == name.casefold())
            ),
            None,
        )
        if entry is None:
            return f"Skill 不存在：{name}"
        if entry.snapshot is None:
            details = "\n".join(
                f"- {problem.severity.value}: {problem.message}" for problem in entry.problems
            )
            return f"Skill {entry.directory_name}: unavailable\n{details}"
        snapshot = entry.snapshot
        config = snapshot.config
        state = "active" if snapshot.name in self._skill_runtime.active_names else "available"
        return (
            f"Skill {snapshot.name}\n"
            f"description: {snapshot.description}\n"
            f"state: {state}\n"
            f"execution: {config.execution_mode.value}\n"
            f"context: {config.context_kind.value}\n"
            f"model: {config.model_name or 'current'}\n"
            f"allowed-tools: {', '.join(sorted(config.allowed_tools)) or 'none'}"
        )

    async def deactivate_skill(self, name: str) -> str:
        if self._skill_runtime is None:
            return "当前会话未启用 Skill。"
        try:
            changed = await self._skill_runtime.deactivate(name)
        except (SessionStorageError, ValueError):
            return "Skill 停用状态保存失败，当前状态未改变。"
        return f"Skill 已停用：{name}" if changed else f"Skill 未激活：{name}"

    async def reload_skills(self) -> str:
        if self._skill_runtime is None:
            return "当前会话未启用 Skill。"
        try:
            candidate = self._skill_runtime.scan_catalog_candidate()
            remaining = tuple(
                name for name in self._skill_runtime.active_names if name in candidate.available
            )
            if (
                remaining != self._skill_runtime.active_names
                and self._session_manager is not None
                and self._session_manager.active_session_id is not None
            ):
                await self._session_manager.append_skill_state(remaining)
            removed = self._skill_runtime.commit_catalog(candidate)
            if self._command_runtime is not None:
                snapshots = tuple(
                    entry.snapshot for entry in candidate.entries if entry.snapshot is not None
                )
                self._command_runtime.registry.replace_dynamic(
                    build_skill_command_definitions(snapshots)
                )
        except Exception:
            return "Skill reload 失败，已保留原状态。"
        suffix = f"；自动停用：{', '.join(removed)}" if removed else ""
        return f"Skill reload 完成：{len(candidate.available)} 个可用{suffix}"

    async def clear_session(self) -> str:
        if self._active_turn is not None or self._active_compaction is not None:
            raise RuntimeError("活动任务期间不能清空会话")
        self._history.clear()
        self._mode = AgentMode.AGENT
        self._startup_restore_event = None
        if self._session_manager is not None:
            self._session_manager.begin_new()
        if self._context_manager is not None:
            self._context_manager.activate_restore(RestoreContextResult((), None))
        if self._skill_runtime is not None:
            self._skill_runtime.clear()
        if self._permission_session is not None:
            self._permission_session.clear()
        if self._prompt_runtime is not None:
            self._prompt_runtime.reset_mode()
        clear_queued = getattr(self._runner, "clear_queued_request_supplements", None)
        if callable(clear_queued):
            clear_queued(SupplementKind.REMINDER)
        return "当前会话已清空。"

    def cancel_active_turn(self) -> None:
        if self._active_turn is not None:
            self._active_turn.cancel()
        if self._active_compaction is not None:
            self._active_compaction.cancel()

    def submit_approval(self, choice: ApprovalChoice) -> None:
        if self._active_turn is None:
            raise RuntimeError("当前没有运行中的 Agent 回合")
        self._active_turn.submit_approval(choice)

    async def finalize_memory(self) -> MemoryUpdateReport:
        """幂等地整理本次进程中新提交的会话记忆。"""

        if self._memory_update_task is None:
            self._memory_update_task = asyncio.create_task(self._finalize_memory())
        return await asyncio.shield(self._memory_update_task)

    async def _finalize_memory(self) -> MemoryUpdateReport:
        if not self._new_commits or self._memory_store is None or self._memory_updater is None:
            return MemoryUpdateReport(MemoryUpdateStatus.SKIPPED, message="本次运行没有新增对话")
        try:
            current = await asyncio.to_thread(self._memory_store.load)
            plan = await asyncio.wait_for(
                self._memory_updater.analyze(current, tuple(self._new_commits)),
                timeout=30,
            )
            if not plan.operations:
                return MemoryUpdateReport(MemoryUpdateStatus.NO_CHANGE, message="无需更新项目记忆")
            await asyncio.to_thread(self._memory_store.apply, plan)
            return MemoryUpdateReport(
                MemoryUpdateStatus.UPDATED,
                len(plan.operations),
                "项目记忆已更新",
            )
        except TimeoutError:
            return MemoryUpdateReport(MemoryUpdateStatus.TIMEOUT, message="项目记忆整理超时")
        except Exception:
            return MemoryUpdateReport(MemoryUpdateStatus.FAILED, message="项目记忆整理失败")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._active_turn is not None or self._active_compaction is not None:
            self.cancel_active_turn()
            await self._turn_finished.wait()
        try:
            if self._hook_runtime is not None and self._hook_context is not None:
                result = await self._hook_runtime.dispatch(
                    self._hook_context.simple(HookEventName.SESSION_END)
                )
                for notice in result.notices:
                    print(f"hook: {notice}")
                await self._hook_runtime.close()
            await self._runner.close()
        finally:
            if self._context_manager is not None:
                await self._context_manager.close()
