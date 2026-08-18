import asyncio
from io import StringIO

import pytest
from rich.console import Console

from tests.support.fake_provider import FakeProvider
from ycode.agent import (
    AgentMode,
    ContextCompactedEvent,
    ContextCompactionFailedEvent,
    ContextCompactionNotNeededEvent,
    HookNoticeEvent,
    PlainChatRunner,
    ToolApprovalRequested,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    UserMessageEvent,
)
from ycode.commands import CommandDefinition, CommandKind, build_command_runtime
from ycode.config.models import ProviderConfig
from ycode.context import ContextCompactionReport, ContextFailureReport
from ycode.core import (
    ChatMessage,
    StopReason,
    StreamEnd,
    TextDelta,
    ThinkingBlock,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallBlock,
)
from ycode.errors import ProviderError
from ycode.mcp.models import (
    McpConnectionState,
    McpErrorSummary,
    McpServerStatus,
    McpStatusReport,
)
from ycode.security import (
    PermissionAction,
    PermissionDecision,
    PermissionMode,
    PermissionSession,
    PermissionSubject,
)
from ycode.session.chat import ChatSession
from ycode.tools import ToolExecutionRecord, ToolExecutionResult
from ycode.ui.terminal import TerminalUI, _tool_result_summary, _tool_start_summary


class FakeInput:
    def __init__(self, values: list[str]) -> None:
        self.values = values
        self.modes: list[AgentMode] = []

    async def read(self, mode: AgentMode) -> str:
        self.modes.append(mode)
        return self.values.pop(0)


class CommandInput(FakeInput):
    async def read(self, mode: AgentMode, permission_mode=None) -> str:
        del permission_mode
        return await super().read(mode)


class InterruptingInput(FakeInput):
    def __init__(self, values: list[str]) -> None:
        super().__init__(values)
        self._interrupted = False

    async def wait_for_interrupt(self) -> None:
        if not self._interrupted:
            self._interrupted = True
            return
        await asyncio.Future()


class ConfirmingInput(CommandInput):
    def __init__(self, confirmed: bool) -> None:
        super().__init__([])
        self.confirmed = confirmed
        self.previews: list[str] = []

    async def read_confirmation(self, message: str) -> bool:
        self.previews.append(message)
        return self.confirmed


class FakeRenderer:
    def __init__(self) -> None:
        self.started = 0
        self.thinking: list[tuple[int, str]] = []
        self.text: list[tuple[int, str]] = []
        self.tools: list[tuple[int, str, str]] = []
        self.completed: list[str] = []
        self.failures: list[str] = []
        self.cancelled = 0

    async def start(self) -> None:
        self.started += 1

    def append_thinking(self, text: str, round_number: int) -> None:
        self.thinking.append((round_number, text))

    def append_text(self, text: str, round_number: int) -> None:
        self.text.append((round_number, text))

    def set_tool_status(self, round_number: int, call_id: str, status: str) -> None:
        self.tools.append((round_number, call_id, status))

    async def complete(self, message) -> None:
        self.completed.append(message.text)

    async def fail(self, message: str) -> None:
        self.failures.append(message)

    async def cancel(self) -> None:
        self.cancelled += 1


def config() -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "name": "test",
            "protocol": "openai",
            "model": "model-test",
            "base_url": "http://localhost:9000",
            "api_key": "placeholder",
        }
    )


def text_response(value: str):
    return [TextDelta(0, value), StreamEnd(StopReason.END_TURN)]


@pytest.mark.asyncio
async def test_command_runtime_routes_local_commands_without_provider() -> None:
    provider = FakeProvider([])
    runner = PlainChatRunner(provider)
    runner.supported_modes = frozenset({AgentMode.AGENT, AgentMode.PLAN_ONLY})
    permission = PermissionSession(PermissionMode.DEFAULT)
    session = ChatSession(
        runner,
        permission,
        command_runtime=build_command_runtime(),
    )
    target = StringIO()
    ui = TerminalUI(
        config(),
        session,
        console=Console(file=target, width=80, color_system=None),
        input_factory=lambda _: CommandInput(
            ["/help", "/unknown", "/plan", "/permission strict", "/exit"]
        ),
    )

    await ui.run()

    output = target.getvalue()
    assert "可用命令" in output
    assert "未知命令" in output
    assert session.mode is AgentMode.PLAN_ONLY
    assert permission.mode is PermissionMode.STRICT
    assert provider.requests == []
    assert session.history == ()


@pytest.mark.asyncio
async def test_worktree_force_delete_requires_confirmation_and_cancel_is_non_mutating() -> None:
    class WorktreeSession:
        command_runtime = None
        permission_mode = PermissionMode.DEFAULT
        mode = AgentMode.AGENT

        def __init__(self) -> None:
            self.deleted: list[tuple[str, bool, bool]] = []

        async def worktree_delete_preview(self, name: str) -> str:
            return f"preview:{name}"

        async def worktree_delete(self, name: str, *, force: bool, confirmed: bool) -> str:
            self.deleted.append((name, force, confirmed))
            return "deleted"

    target = StringIO()
    session = WorktreeSession()
    denied = ConfirmingInput(False)
    ui = TerminalUI(
        config(),
        session,  # type: ignore[arg-type]
        console=Console(file=target, width=80, color_system=None),
        input_factory=lambda _: denied,
    )

    await ui.manage_worktrees("delete", "agents/writer-a", force=True)

    assert denied.previews == ["preview:agents/writer-a"]
    assert session.deleted == []
    assert "已取消" in target.getvalue()


@pytest.mark.asyncio
async def test_hidden_ai_command_displays_raw_and_commits_expanded_prompt() -> None:
    async def review(invocation, controller) -> None:
        await controller.send_user_message(invocation.raw_text, "Review current changes.")

    definition = CommandDefinition(
        "review-test",
        (),
        "test",
        "/review-test",
        CommandKind.AI,
        "",
        review,
        True,
    )
    provider = FakeProvider([text_response("done")])
    session = ChatSession(
        PlainChatRunner(provider),
        command_runtime=build_command_runtime((definition,)),
    )
    target = StringIO()
    ui = TerminalUI(
        config(),
        session,
        console=Console(file=target, width=80, color_system=None),
        input_factory=lambda _: CommandInput(["/review-test", "/exit"]),
        renderer_factory=lambda _: FakeRenderer(),
    )

    await ui.run()

    assert "/review-test" in target.getvalue()
    assert provider.requests[0][0].text == "Review current changes."
    assert session.history[0].text == "Review current changes."


@pytest.mark.asyncio
async def test_two_turn_conversation_consumes_agent_events() -> None:
    first = [
        ThinkingDelta(0, "reason"),
        ThinkingComplete(0, ThinkingBlock("reason")),
        TextDelta(1, "first"),
        StreamEnd(StopReason.END_TURN),
    ]
    provider = FakeProvider([first, text_response("second")])
    session = ChatSession(PlainChatRunner(provider))
    fake_input = FakeInput(["one", "two", "/exit"])
    renderers: list[FakeRenderer] = []
    console = Console(file=StringIO(), width=80, color_system=None)
    ui = TerminalUI(
        config(),
        session,
        console=console,
        input_factory=lambda _: fake_input,
        renderer_factory=lambda _: renderers.append(FakeRenderer()) or renderers[-1],
    )

    await ui.run()

    assert renderers[0].thinking == [(1, "reason")]
    assert renderers[0].text == [(1, "first")]
    assert renderers[0].completed == ["first"]
    assert renderers[1].completed == ["second"]
    assert [message.text for message in provider.requests[1]] == ["one", "first", "two"]
    assert fake_input.modes == [AgentMode.AGENT, AgentMode.AGENT, AgentMode.AGENT]


@pytest.mark.asyncio
async def test_error_returns_to_input_and_blank_is_ignored() -> None:
    provider = FakeProvider(
        [
            [ProviderError("network", "safe failure", True)],
            text_response("ok"),
        ]
    )
    renderers: list[FakeRenderer] = []
    ui = TerminalUI(
        config(),
        ChatSession(PlainChatRunner(provider)),
        console=Console(file=StringIO(), width=80, color_system=None),
        input_factory=lambda _: FakeInput([" ", "failed", "retry", "/quit"]),
        renderer_factory=lambda _: renderers.append(FakeRenderer()) or renderers[-1],
    )

    await ui.run()

    assert len(provider.requests) == 2
    assert renderers[0].failures == ["safe failure"]
    assert renderers[1].completed == ["ok"]
    assert [message.text for message in provider.requests[1]] == ["retry"]


@pytest.mark.asyncio
async def test_mode_command_updates_next_input_and_prints_confirmation() -> None:
    provider = FakeProvider([])
    runner = PlainChatRunner(provider)
    runner.supported_modes = frozenset({AgentMode.AGENT, AgentMode.PLAN_ONLY})
    fake_input = FakeInput(["/plan", "/agent", "/exit"])
    target = StringIO()
    ui = TerminalUI(
        config(),
        ChatSession(runner),
        console=Console(file=target, width=80, color_system=None),
        input_factory=lambda _: fake_input,
    )

    await ui.run()

    assert fake_input.modes == [
        AgentMode.AGENT,
        AgentMode.PLAN_ONLY,
        AgentMode.AGENT,
    ]
    output = target.getvalue()
    assert "mode: plan-only" in output
    assert "mode: agent" in output
    assert provider.requests == []


@pytest.mark.asyncio
async def test_ctrl_c_listener_cancels_active_turn_and_recovers_input() -> None:
    provider = FakeProvider([text_response("too late")], delay=0.1)
    renderer = FakeRenderer()
    target = StringIO()
    ui = TerminalUI(
        config(),
        ChatSession(PlainChatRunner(provider)),
        console=Console(file=target, width=80, color_system=None),
        input_factory=lambda _: InterruptingInput(["cancel me", "/exit"]),
        renderer_factory=lambda _: renderer,
    )

    await ui.run()

    assert len(provider.requests) == 1
    assert "已取消" in target.getvalue()


def test_tool_summaries_hide_write_content_and_show_result_metadata() -> None:
    call = ToolCallBlock(
        "call-1",
        "write_file",
        {"path": "a.txt", "content": "MUST-NOT-APPEAR"},
    )
    record = ToolExecutionRecord(
        position=0,
        call=call,
        result=ToolExecutionResult(
            content="文件写入成功。",
            metadata={"path": "a.txt", "truncated": False},
        ),
        elapsed_seconds=0.1,
    )

    assert _tool_start_summary(call) == "◇ write_file  a.txt"
    assert "MUST-NOT-APPEAR" not in _tool_start_summary(call)
    assert _tool_result_summary(ToolExecutionCompleted(1, record)) == "✓ write_file  完成"


@pytest.mark.asyncio
async def test_tool_events_update_renderer_with_stable_call_id() -> None:
    ui = TerminalUI(
        config(),
        ChatSession(PlainChatRunner(FakeProvider([]))),
        console=Console(file=StringIO(), width=80, color_system=None),
    )
    renderer = FakeRenderer()
    approvals: asyncio.Queue[ToolApprovalRequested] = asyncio.Queue()
    call = ToolCallBlock("call-1", "write_file", {"path": "a.txt", "content": "x"})
    subject = PermissionSubject(
        call,
        {"path": "a.txt", "content": "x"},
        {"tool": "write_file", "path": "a.txt"},
        "写入 a.txt",
    )
    decision = PermissionDecision(PermissionAction.ASK, subject, "test", "需要确认")
    record = ToolExecutionRecord(
        position=0,
        call=call,
        result=ToolExecutionResult(content="完成", metadata={"path": "a.txt"}),
        elapsed_seconds=0.1,
    )

    await ui._render_event(ToolExecutionStarted(1, 0, call), renderer, approvals)
    await ui._render_event(ToolApprovalRequested(1, 0, decision), renderer, approvals)
    await ui._render_event(ToolExecutionCompleted(1, record), renderer, approvals)
    await ui._render_event(ToolExecutionCancelled(1, 0, call), renderer, approvals)

    assert [round_number for round_number, _, _ in renderer.tools] == [1] * 4
    assert [call_id for _, call_id, _ in renderer.tools] == ["call-1"] * 4
    assert [status[0] for _, _, status in renderer.tools] == ["◇", "?", "✓", "–"]
    assert (await approvals.get()).decision is decision


@pytest.mark.asyncio
async def test_terminal_renders_mcp_startup_summary_and_status_command() -> None:
    report = McpStatusReport(
        (
            McpServerStatus("ready", "stdio", McpConnectionState.READY, 2),
            McpServerStatus(
                "entry_2",
                "invalid",
                McpConnectionState.INVALID,
                0,
                McpErrorSummary("invalid_config", "配置无效"),
            ),
        )
    )

    class StatusProvider:
        def snapshot(self) -> McpStatusReport:
            return report

    target = StringIO()
    ui = TerminalUI(
        config(),
        ChatSession(PlainChatRunner(FakeProvider([])), mcp_status_provider=StatusProvider()),
        console=Console(file=target, width=80, color_system=None),
        input_factory=lambda _: FakeInput(["/mcp", "/exit"]),
    )

    await ui.run()

    output = target.getvalue()
    assert "MCP: 可用 1 / 失败 1 / 未启用 0" in output
    assert "MCP Servers" in output
    assert "entry_2" in output
    assert "invalid_config" in output


@pytest.mark.asyncio
async def test_terminal_mcp_summary_shows_background_connections() -> None:
    report = McpStatusReport((McpServerStatus("slow", "stdio", McpConnectionState.STARTING, 0),))

    class StatusProvider:
        def snapshot(self) -> McpStatusReport:
            return report

    target = StringIO()
    ui = TerminalUI(
        config(),
        ChatSession(PlainChatRunner(FakeProvider([])), mcp_status_provider=StatusProvider()),
        console=Console(file=target, width=80, color_system=None),
        input_factory=lambda _: FakeInput(["/exit"]),
    )

    await ui.run()

    assert "MCP: 后台连接 1 / 可用 0 / 失败 0 / 未启用 0" in target.getvalue()


@pytest.mark.asyncio
async def test_terminal_renders_context_status_events() -> None:
    class EventSession:
        mode = AgentMode.AGENT
        permission_mode = None
        mcp_status = None

        async def stream_reply(self, text: str):
            yield UserMessageEvent(ChatMessage.user_text(text))
            yield HookNoticeEvent("placeholder")
            yield ContextCompactedEvent(ContextCompactionReport(170_000, 12_000))
            yield ContextCompactionFailedEvent(
                ContextFailureReport("summary_invalid", "摘要无效", 3, True, False)
            )
            yield ContextCompactionNotNeededEvent()

        def cancel_active_turn(self) -> None:
            return

    target = StringIO()
    ui = TerminalUI(
        config(),
        EventSession(),  # type: ignore[arg-type]
        console=Console(file=target, width=80, color_system=None),
        input_factory=lambda _: FakeInput(["/compact", "/exit"]),
    )

    await ui.run()

    output = target.getvalue()
    assert "hook: placeholder" in output
    assert "170,000 → 12,000 tokens" in output
    assert "连续 3 次" in output
    assert "自动摘要已熔断" in output
    assert "没有可压缩" in output
