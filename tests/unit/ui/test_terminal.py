import asyncio
from io import StringIO

import pytest
from rich.console import Console

from tests.support.fake_provider import FakeProvider
from ycode.agent import AgentMode, PlainChatRunner, ToolExecutionCompleted
from ycode.config.models import ProviderConfig
from ycode.core import (
    StopReason,
    StreamEnd,
    TextDelta,
    ThinkingBlock,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallBlock,
)
from ycode.errors import ProviderError
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


class InterruptingInput(FakeInput):
    def __init__(self, values: list[str]) -> None:
        super().__init__(values)
        self._interrupted = False

    async def wait_for_interrupt(self) -> None:
        if not self._interrupted:
            self._interrupted = True
            return
        await asyncio.Future()


class FakeRenderer:
    def __init__(self) -> None:
        self.started = 0
        self.thinking: list[tuple[int, str]] = []
        self.text: list[tuple[int, str]] = []
        self.tools: list[str] = []
        self.completed: list[str] = []
        self.failures: list[str] = []
        self.cancelled = 0

    async def start(self) -> None:
        self.started += 1

    def append_thinking(self, text: str, round_number: int) -> None:
        self.thinking.append((round_number, text))

    def append_text(self, text: str, round_number: int) -> None:
        self.text.append((round_number, text))

    def add_tool_status(self, status: str) -> None:
        self.tools.append(status)

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
