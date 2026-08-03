"""消费 AgentEvent 的 TUI 对话循环。"""

import asyncio
from collections.abc import Callable
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
    McpStatusEvent,
    ModeChangedEvent,
    PermissionGrantsClearedEvent,
    PermissionModeChangedEvent,
    ToolApprovalRequested,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    UserMessageEvent,
)
from ycode.config.models import ProviderConfig
from ycode.core.messages import thaw_json
from ycode.session.chat import ChatSession
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
        self._input = (
            input_factory(self._console) if input_factory else InputBox(console=self._console)
        )
        self._renderer_factory = renderer_factory or (
            lambda console: LiveResponseRenderer(console=console)
        )

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
        while True:
            try:
                if self._session.permission_mode is None:
                    user_text = await self._input.read(self._session.mode)
                else:
                    user_text = await self._input.read(
                        self._session.mode,
                        self._session.permission_mode,
                    )
            except (EOFError, KeyboardInterrupt):
                return

            if user_text.strip().lower() in {"/exit", "/quit"}:
                return
            if not user_text.strip():
                continue

            renderer = self._renderer_factory(self._console)
            started = False
            approvals: asyncio.Queue[ToolApprovalRequested] = asyncio.Queue()

            async def ensure_started(active_renderer: Any = renderer) -> None:
                nonlocal started
                if not started:
                    await active_renderer.start()
                    started = True

            async def consume_turn(
                active_user_text: str = user_text,
                active_renderer: Any = renderer,
                active_approvals: asyncio.Queue[ToolApprovalRequested] = approvals,
            ) -> None:
                async for event in self._session.stream_reply(active_user_text):
                    if isinstance(event, UserMessageEvent):
                        self._console.print(
                            render_user_message(
                                event.message.text,
                                self._console.width,
                                encoding=getattr(self._console.file, "encoding", None),
                            )
                        )
                    elif isinstance(event, AgentThinkingDelta):
                        await ensure_started()
                        active_renderer.append_thinking(event.text, event.round_number)
                    elif isinstance(event, AgentTextDelta):
                        await ensure_started()
                        active_renderer.append_text(event.text, event.round_number)
                    elif isinstance(event, ToolExecutionStarted):
                        await ensure_started()
                        active_renderer.add_tool_status(_tool_start_summary(event.call))
                    elif isinstance(event, ToolExecutionCompleted):
                        await ensure_started()
                        active_renderer.add_tool_status(_tool_result_summary(event))
                    elif isinstance(event, ToolExecutionCancelled):
                        await ensure_started()
                        active_renderer.add_tool_status(f"– {event.call.name}  已取消")
                    elif isinstance(event, ToolApprovalRequested):
                        await ensure_started()
                        active_renderer.add_tool_status(
                            f"? {event.decision.subject.call.name}  等待用户确认"
                        )
                        await active_approvals.put(event)
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
                    elif isinstance(event, FinalResponseEvent):
                        await ensure_started()
                        await active_renderer.complete(event.message)
                    elif isinstance(event, AgentLimitReachedEvent | AgentErrorEvent):
                        await ensure_started()
                        await active_renderer.fail(event.message)
                    elif isinstance(event, AgentCancelledEvent):
                        await ensure_started()
                        await active_renderer.cancel()
                        self._console.print(event.message)

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
                    done, _ = await asyncio.wait(
                        waiting,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
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
                            await asyncio.gather(
                                interrupt_task,
                                return_exceptions=True,
                            )
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
                continue
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
