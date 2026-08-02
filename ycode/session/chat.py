"""AgentEvent 会话、模式与整轮历史事务。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

from ycode.agent.contracts import (
    AgentMode,
    AgentTermination,
    AgentTurn,
    ConversationRunner,
)
from ycode.agent.events import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentEvent,
    AgentLimitReachedEvent,
    FinalResponseEvent,
    McpStatusEvent,
    ModeChangedEvent,
    PermissionGrantsClearedEvent,
    PermissionModeChangedEvent,
    UserMessageEvent,
)
from ycode.core.messages import ChatMessage
from ycode.mcp.models import McpStatusProvider
from ycode.security import ApprovalChoice, PermissionMode, PermissionSession

type _TerminalEvent = (
    FinalResponseEvent | AgentLimitReachedEvent | AgentCancelledEvent | AgentErrorEvent
)


class ChatSession:
    def __init__(
        self,
        runner: ConversationRunner,
        permission_session: PermissionSession | None = None,
        mcp_status_provider: McpStatusProvider | None = None,
    ) -> None:
        self._runner = runner
        self._history: list[ChatMessage] = []
        self._mode = AgentMode.AGENT
        self._permission_session = permission_session
        self._mcp_status_provider = mcp_status_provider
        self._active_turn: AgentTurn | None = None
        self._turn_finished = asyncio.Event()
        self._turn_finished.set()
        self._closed = False

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

    async def stream_reply(self, user_text: str) -> AsyncIterator[AgentEvent]:
        if not user_text.strip():
            raise ValueError("消息不能为空")
        if self._closed:
            raise RuntimeError("会话已关闭")
        if self._active_turn is not None:
            raise RuntimeError("已有 Agent 回合正在运行")

        user_message = ChatMessage.user_text(user_text)
        command = user_text.strip().lower()
        if command in {"/plan", "/agent"}:
            yield UserMessageEvent(user_message)
            target = AgentMode.PLAN_ONLY if command == "/plan" else AgentMode.AGENT
            if target not in self._runner.supported_modes:
                yield AgentErrorEvent(
                    "unsupported_mode",
                    "当前对话运行器不支持 plan-only 模式。",
                )
                return
            previous = self._mode
            self._mode = target
            yield ModeChangedEvent(previous, target)
            return
        if command == "/mcp":
            yield UserMessageEvent(user_message)
            if self._mcp_status_provider is None:
                yield AgentErrorEvent("mcp_unavailable", "当前没有 MCP 状态信息。")
            else:
                yield McpStatusEvent(self._mcp_status_provider.snapshot())
            return
        if self._permission_session is not None and command.startswith("/permission"):
            yield UserMessageEvent(user_message)
            parts = command.split()
            if len(parts) == 1:
                yield PermissionModeChangedEvent(
                    self._permission_session.mode,
                    self._permission_session.mode,
                )
                return
            if len(parts) != 2:
                yield AgentErrorEvent(
                    "invalid_permission_command",
                    "用法：/permission [strict|default|allow|clear]",
                )
                return
            argument = parts[1]
            if argument == "clear":
                count = self._permission_session.grant_count
                self._permission_session.clear()
                yield PermissionGrantsClearedEvent(count)
                return
            try:
                target_permission = PermissionMode(argument)
            except ValueError:
                yield AgentErrorEvent(
                    "invalid_permission_command",
                    "用法：/permission [strict|default|allow|clear]",
                )
                return
            previous_permission = self._permission_session.mode
            self._permission_session.set_mode(target_permission)
            yield PermissionModeChangedEvent(previous_permission, target_permission)
            return

        turn = self._runner.start_turn(
            tuple(self._history),
            user_message,
            self._mode,
        )
        self._active_turn = turn
        self._turn_finished.clear()
        terminal_event: _TerminalEvent | None = None
        try:
            yield UserMessageEvent(user_message)
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

    def cancel_active_turn(self) -> None:
        if self._active_turn is not None:
            self._active_turn.cancel()

    def submit_approval(self, choice: ApprovalChoice) -> None:
        if self._active_turn is None:
            raise RuntimeError("当前没有运行中的 Agent 回合")
        self._active_turn.submit_approval(choice)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._active_turn is not None:
            self._active_turn.cancel()
            await self._turn_finished.wait()
        await self._runner.close()
