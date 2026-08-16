"""消费 AgentEvent 的 TUI 对话循环。"""

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from rich.console import Console

from ycode.agent import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentLimitReachedEvent,
    AgentTextDelta,
    AgentThinkingDelta,
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
    ToolApprovalRequested,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    UserMessageEvent,
)
from ycode.commands import CommandExecutionError
from ycode.config.models import ProviderConfig
from ycode.core.messages import thaw_json
from ycode.memory import MemoryUpdateStatus
from ycode.session.chat import ChatSession
from ycode.ui.command_completion import CommandCompleter
from ycode.ui.header import render_header
from ycode.ui.input_box import InputBox
from ycode.ui.mcp_status import render_mcp_status, render_mcp_summary
from ycode.ui.renderer import LiveResponseRenderer
from ycode.ui.user_message import render_user_message

InputFactory = Callable[[Console], Any]
RendererFactory = Callable[[Console], Any]


class TerminalUI:
    def __init__(
        self,
        config: ProviderConfig,
        session: ChatSession,
        *,
        console: Console | None = None,
        input_factory: InputFactory | None = None,
        renderer_factory: RendererFactory | None = None,
    ) -> None:
        self._config = config
        self._session = session
        self._console = console or Console()
        if input_factory:
            self._input = input_factory(self._console)
        elif getattr(session, "command_runtime", None) is not None:
            self._input = InputBox(
                console=self._console,
                completer=CommandCompleter(session.command_runtime.registry),  # type: ignore[union-attr]
                help_hint="/help for commands",
            )
        else:
            self._input = InputBox(console=self._console)
        self._renderer_factory = renderer_factory or (
            lambda console: LiveResponseRenderer(console=console)
        )
        self._exit_requested = False

    async def run(self) -> None:
        self._console.print(
            render_header(
                self._config,
                self._console.width,
                self._session.permission_mode,
            )
        )
        mcp_status = self._session.mcp_status
        if mcp_status is not None:
            self._console.print(render_mcp_summary(mcp_status))
        for warning in getattr(self._session, "startup_warnings", ()):
            self._console.print(f"warning: {warning}")
        restored = getattr(self._session, "startup_restore_event", None)
        if restored is not None:
            self._render_restored(restored)
        while not self._exit_requested:
            try:
                if self._session.permission_mode is None:
                    user_text = await self._input.read(self._session.mode)
                else:
                    user_text = await self._input.read(
                        self._session.mode,
                        self._session.permission_mode,
                    )
            except (EOFError, KeyboardInterrupt):
                await self.request_exit()
                return

            if not user_text.strip():
                continue
            runtime = getattr(self._session, "command_runtime", None)
            if runtime is not None and await runtime.dispatcher.try_dispatch(user_text, self):
                continue
            if runtime is None and user_text.strip().lower() in {"/exit", "/quit"}:
                await self.request_exit()
                return
            await self.send_user_message(user_text, user_text)

    async def show_user_input(self, text: str) -> None:
        self._console.print(
            render_user_message(
                text,
                self._console.width,
                encoding=getattr(self._console.file, "encoding", None),
            )
        )

    async def show_system_message(self, message: str) -> None:
        self._console.print(message)

    async def send_user_message(self, display_text: str, model_text: str) -> None:
        stream = (
            self._session.stream_reply(model_text)
            if display_text == model_text
            else self._session.stream_reply(model_text, display_text=display_text)
        )
        await self._run_event_stream(stream)

    async def invoke_skill(self, name: str, arguments: str | None, raw_text: str) -> None:
        await self._run_event_stream(self._session.stream_skill(name, arguments, raw_text))

    async def show_skills(self) -> None:
        self._console.print(self._session.skills_status())

    async def show_skill(self, name: str) -> None:
        self._console.print(self._session.skill_status(name))

    async def deactivate_skill(self, name: str) -> None:
        self._console.print(await self._session.deactivate_skill(name))

    async def reload_skills(self) -> None:
        self._console.print(await self._session.reload_skills())

    async def clear_session(self) -> None:
        self._console.print(await self._session.clear_session())

    async def show_tasks(self, task_id: str | None = None) -> None:
        try:
            message = self._session.tasks_status(task_id)
        except (ValueError, RuntimeError) as error:
            raise CommandExecutionError(str(error)) from error
        self._console.print(message)

    async def stop_task(self, task_id: str) -> None:
        try:
            message = await self._session.stop_task(task_id)
        except (ValueError, RuntimeError) as error:
            raise CommandExecutionError(str(error)) from error
        self._console.print(message)

    async def set_mode(self, mode: str) -> None:
        from ycode.agent import AgentMode

        try:
            event = self._session.change_mode(AgentMode(mode))
        except ValueError as error:
            raise CommandExecutionError(str(error)) from error
        await self._render_event(event, None, None)

    async def show_mcp_status(self) -> None:
        status = self._session.mcp_status
        if status is None:
            raise CommandExecutionError("当前没有 MCP 状态信息。")
        self._console.print(render_mcp_status(status))

    async def compact_context(self) -> None:
        await self._run_event_stream(self._session.compact_context())

    async def show_permission_status(self) -> None:
        try:
            event = self._session.permission_status()
        except ValueError as error:
            raise CommandExecutionError(str(error)) from error
        await self._render_event(event, None, None)

    async def set_permission_mode(self, mode: str) -> None:
        from ycode.security import PermissionMode

        try:
            event = self._session.change_permission_mode(PermissionMode(mode))
        except ValueError as error:
            raise CommandExecutionError(str(error)) from error
        await self._render_event(event, None, None)

    async def clear_permission_grants(self) -> None:
        try:
            event = self._session.clear_permission_grants()
        except ValueError as error:
            raise CommandExecutionError(str(error)) from error
        await self._render_event(event, None, None)

    async def resume_session(self, session_id: str) -> None:
        from ycode.session.models import SessionStorageError

        try:
            event = await self._session.restore(session_id)
        except (SessionStorageError, ValueError) as error:
            raise CommandExecutionError("会话恢复失败，当前会话未改变。") from error
        self._render_restored(event)

    async def refresh_status(self) -> None:
        # 输入框每次读取会话状态，无需缓存。
        return None

    async def request_exit(self) -> None:
        if self._exit_requested:
            return
        self._exit_requested = True
        await self._finish_memory()

    async def _run_event_stream(self, stream: AsyncIterator[Any]) -> None:
        renderer = self._renderer_factory(self._console)
        started = False
        approvals: asyncio.Queue[ToolApprovalRequested] = asyncio.Queue()

        async def ensure_started() -> None:
            nonlocal started
            if not started:
                await renderer.start()
                started = True

        async def consume_turn() -> None:
            async for event in stream:
                await self._render_event(event, renderer, approvals, ensure_started)

        interrupt_task: asyncio.Task[None] | None = None
        approval_task: asyncio.Task[ToolApprovalRequested] | None = None
        turn_task: asyncio.Task[None] | None = None
        try:
            wait_for_interrupt = getattr(self._input, "wait_for_interrupt", None)
            read_approval = getattr(self._input, "read_approval", None)
            turn_task = asyncio.create_task(consume_turn())
            approval_task = asyncio.create_task(approvals.get())
            if callable(wait_for_interrupt):
                interrupt_task = asyncio.create_task(wait_for_interrupt())
            while True:
                waiting = {turn_task, approval_task}
                if interrupt_task is not None:
                    waiting.add(interrupt_task)
                done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                if turn_task in done:
                    await turn_task
                    break
                if interrupt_task is not None and interrupt_task in done:
                    self._session.cancel_active_turn()
                    await turn_task
                    break
                if approval_task in done:
                    request = approval_task.result()
                    if interrupt_task is not None:
                        interrupt_task.cancel()
                        await asyncio.gather(interrupt_task, return_exceptions=True)
                        interrupt_task = None
                    pause = getattr(renderer, "pause", None)
                    if callable(pause):
                        await pause()
                    if not callable(read_approval):
                        self._session.cancel_active_turn()
                        await turn_task
                        break
                    try:
                        choice = await read_approval(request.decision)
                    except KeyboardInterrupt:
                        self._session.cancel_active_turn()
                    else:
                        self._session.submit_approval(choice)
                    resume = getattr(renderer, "resume", None)
                    if callable(resume) and not turn_task.done():
                        await resume()
                    approval_task = asyncio.create_task(approvals.get())
                    if callable(wait_for_interrupt) and not turn_task.done():
                        interrupt_task = asyncio.create_task(wait_for_interrupt())
        except KeyboardInterrupt:
            self._session.cancel_active_turn()
            if started:
                await renderer.cancel()
        except asyncio.CancelledError:
            self._session.cancel_active_turn()
            if started:
                await renderer.cancel()
            raise
        finally:
            if interrupt_task is not None and not interrupt_task.done():
                interrupt_task.cancel()
                await asyncio.gather(interrupt_task, return_exceptions=True)
            if approval_task is not None and not approval_task.done():
                approval_task.cancel()
                await asyncio.gather(approval_task, return_exceptions=True)
            if turn_task is not None and not turn_task.done():
                self._session.cancel_active_turn()
                turn_task.cancel()
                await asyncio.gather(turn_task, return_exceptions=True)

    async def _render_event(
        self,
        event: Any,
        renderer: Any | None,
        approvals: asyncio.Queue[ToolApprovalRequested] | None,
        ensure_started: Callable[[], Any] | None = None,
    ) -> None:
        async def start() -> None:
            if ensure_started is not None:
                await ensure_started()

        if isinstance(event, UserMessageEvent):
            await self.show_user_input(event.message.text)
        elif isinstance(event, AgentThinkingDelta):
            await start()
            renderer.append_thinking(event.text, event.round_number)
        elif isinstance(event, AgentTextDelta):
            await start()
            renderer.append_text(event.text, event.round_number)
        elif isinstance(event, ToolExecutionStarted):
            await start()
            renderer.set_tool_status(
                event.round_number, event.call.id, _tool_start_summary(event.call)
            )
        elif isinstance(event, ToolExecutionCompleted):
            await start()
            renderer.set_tool_status(
                event.round_number, event.record.call.id, _tool_result_summary(event)
            )
        elif isinstance(event, ToolExecutionCancelled):
            await start()
            renderer.set_tool_status(
                event.round_number, event.call.id, f"– {event.call.name}  已取消"
            )
        elif isinstance(event, ToolApprovalRequested):
            await start()
            call = event.decision.subject.call
            renderer.set_tool_status(event.round_number, call.id, f"? {call.name}  等待用户确认")
            assert approvals is not None
            await approvals.put(event)
        elif isinstance(event, ModeChangedEvent):
            self._console.print(f"mode: {event.mode.value}")
        elif isinstance(event, PermissionModeChangedEvent):
            self._console.print(f"permission: {event.mode.value}")
        elif isinstance(event, PermissionGrantsClearedEvent):
            self._console.print(f"permission grants cleared: {event.cleared_count}")
        elif isinstance(event, McpStatusEvent):
            self._console.print(render_mcp_status(event.report))
        elif isinstance(event, ContextCompactedEvent):
            self._console.print(
                f"上下文已压缩：{event.report.before_tokens:,} → "
                f"{event.report.after_tokens:,} tokens"
            )
        elif isinstance(event, ContextCompactionFailedEvent):
            message = f"上下文摘要失败（连续 {event.report.failure_count} 次）。"
            if event.report.fuse_open:
                message += "自动摘要已熔断；可使用 /compact 重试。"
            self._console.print(message)
        elif isinstance(event, ContextCompactionNotNeededEvent):
            self._console.print(event.message)
        elif isinstance(event, SessionRestoredEvent):
            self._render_restored(event)
        elif isinstance(event, HookNoticeEvent):
            self._console.print(f"hook: {event.message}")
        elif isinstance(event, FinalResponseEvent):
            await start()
            await renderer.complete(event.message)
        elif isinstance(event, AgentLimitReachedEvent | AgentErrorEvent):
            await start()
            await renderer.fail(event.message)
        elif isinstance(event, AgentCancelledEvent):
            await start()
            await renderer.cancel()
            self._console.print(event.message)

    async def _finish_memory(self) -> None:
        finalize = getattr(self._session, "finalize_memory", None)
        if not callable(finalize):
            return
        report = await finalize()
        if report.status is MemoryUpdateStatus.SKIPPED:
            return
        if report.status is MemoryUpdateStatus.UPDATED:
            self._console.print(f"{report.message}：{report.change_count} 项")
        else:
            self._console.print(report.message)

    def _render_restored(self, event: SessionRestoredEvent) -> None:
        self._console.print(
            f"session restored: {event.session_id} ({event.message_count} messages)"
        )
        for warning in event.warnings:
            self._console.print(f"warning: {warning}")


def _tool_start_summary(call) -> str:
    arguments = thaw_json(call.arguments)
    value = ""
    if isinstance(arguments, dict):
        key = "command" if call.name == "run_command" else "path"
        if call.name in {"glob", "grep"}:
            key = "pattern"
        raw = arguments.get(key, "")
        if isinstance(raw, str):
            value = _one_line(raw)
    return f"◇ {call.name}{f'  {value}' if value else ''}"


def _tool_result_summary(event: ToolExecutionCompleted) -> str:
    record = event.record
    result = record.result
    metadata = thaw_json(result.metadata)
    if result.is_error:
        return f"✗ {record.call.name}  {_one_line(result.content)}"
    detail = "完成"
    if isinstance(metadata, dict):
        if "returned_lines" in metadata:
            detail = f"读取 {metadata['returned_lines']} 行"
        elif "returned" in metadata:
            detail = f"返回 {metadata['returned']} 条"
        elif "exit_code" in metadata:
            detail = f"退出码 {metadata['exit_code']}"
        if metadata.get("truncated"):
            detail += "（已截断）"
    return f"✓ {record.call.name}  {detail}"


def _one_line(value: str, limit: int = 80) -> str:
    flattened = " ".join(value.splitlines())
    return flattened if len(flattened) <= limit else f"{flattened[: limit - 1]}…"
