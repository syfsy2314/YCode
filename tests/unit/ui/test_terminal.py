from io import StringIO

import pytest
from rich.console import Console

from tests.support.fake_provider import FakeProvider
from ycode.config.models import ProviderConfig
from ycode.core import (
    StopReason,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingBlock,
    ThinkingComplete,
    ThinkingDelta,
)
from ycode.errors import ProviderError
from ycode.session.chat import ChatSession
from ycode.ui.terminal import TerminalUI


class FakeInput:
    def __init__(self, values: list[str]) -> None:
        self.values = values

    async def read(self) -> str:
        return self.values.pop(0)


class FakeRenderer:
    def __init__(self) -> None:
        self.started = 0
        self.thinking: list[str] = []
        self.text: list[str] = []
        self.completed = 0
        self.failures: list[str] = []

    async def start(self) -> None:
        self.started += 1

    def append_thinking(self, text: str) -> None:
        self.thinking.append(text)

    def append_text(self, text: str) -> None:
        self.text.append(text)

    async def complete(self) -> None:
        self.completed += 1

    async def fail(self, message: str) -> None:
        self.failures.append(message)

    async def cancel(self) -> None:
        return


def config() -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "name": "test",
            "protocol": "anthropic",
            "model": "claude-test",
            "base_url": "http://localhost:9000",
            "api_key": "placeholder",
            "thinking": True,
        }
    )


def text_response(value: str) -> list[StreamEvent]:
    return [
        TextDelta(0, value),
        StreamEnd(StopReason.END_TURN),
    ]


@pytest.mark.asyncio
async def test_two_turn_conversation_uses_unified_events() -> None:
    first: list[StreamEvent] = [
        ThinkingDelta(0, "reason"),
        ThinkingComplete(0, ThinkingBlock("reason")),
        TextDelta(1, "first"),
        StreamEnd(StopReason.END_TURN),
    ]
    provider = FakeProvider([first, text_response("second")])
    renderers: list[FakeRenderer] = []
    console = Console(file=StringIO(), width=80, color_system=None)
    ui = TerminalUI(
        config(),
        ChatSession(provider),
        console=console,
        input_factory=lambda _: FakeInput(["one", "two", "/exit"]),
        renderer_factory=lambda _: renderers.append(FakeRenderer()) or renderers[-1],
    )

    await ui.run()

    assert renderers[0].thinking == ["reason"]
    assert renderers[0].text == ["first"]
    assert renderers[1].text == ["second"]
    assert all(renderer.completed == 1 for renderer in renderers)
    assert [message.text for message in provider.requests[1]] == ["one", "first", "two"]
    assert "You" not in console.file.getvalue()


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
        ChatSession(provider),
        console=Console(file=StringIO(), width=80, color_system=None),
        input_factory=lambda _: FakeInput([" ", "failed", "retry", "/quit"]),
        renderer_factory=lambda _: renderers.append(FakeRenderer()) or renderers[-1],
    )

    await ui.run()

    assert len(provider.requests) == 2
    assert renderers[0].failures == ["safe failure"]
    assert renderers[1].completed == 1
    assert [message.text for message in provider.requests[1]] == ["retry"]
