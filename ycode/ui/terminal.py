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
    FinalResponseEvent,
    ModeChangedEvent,
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
        self._console.print(render_header(self._config, self._console.width))
        while True:
            try:
                user_text = await self._input.read(self._session.mode)
            except (EOFError, KeyboardInterrupt):
                return

            if user_text.strip().lower() in {"/exit", "/quit"}:
                return
            if not user_text.strip():
                continue

            renderer = self._renderer_factory(self._console)
            started = False

            async def ensure_started(active_renderer: Any = renderer) -> None:
                nonlocal started
                if not started:
                    await active_renderer.start()
                    started = True

            async def consume_turn(
                active_user_text: str = user_text,
                active_renderer: Any = renderer,
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
                    elif isinstance(event, ModeChangedEvent):
                        self._console.print(f"mode: {event.mode.value}")
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
            turn_task: asyncio.Task[None] | None = None
            try:
                wait_for_interrupt = getattr(self._input, "wait_for_interrupt", None)
                if callable(wait_for_interrupt):
                    turn_task = asyncio.create_task(consume_turn())
                    interrupt_task = asyncio.create_task(wait_for_interrupt())
                    done, _ = await asyncio.wait(
                        {turn_task, interrupt_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if interrupt_task in done and turn_task not in done:
                        self._session.cancel_active_turn()
                    await turn_task
                else:
                    await consume_turn()
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
