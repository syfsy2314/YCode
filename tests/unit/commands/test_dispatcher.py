import asyncio

import pytest

from ycode.commands import (
    CommandDefinition,
    CommandDispatcher,
    CommandExecutionError,
    CommandKind,
    CommandRegistry,
    CommandUsageError,
)


class Controller:
    def __init__(self) -> None:
        self.calls = []

    def __getattr__(self, name):
        async def call(*args):
            self.calls.append((name, *args))

        return call


def _dispatcher(handler, kind=CommandKind.LOCAL):
    registry = CommandRegistry()
    registry.register(CommandDefinition("test", (), "test", "/test", kind, "", handler))
    return CommandDispatcher(registry)


@pytest.mark.asyncio
async def test_dispatches_and_displays_local_command() -> None:
    async def handler(invocation, controller):
        await controller.show_system_message(invocation.arguments)

    controller = Controller()
    assert await _dispatcher(handler).try_dispatch("/TEST Value", controller)
    assert controller.calls == [
        ("show_user_input", "/TEST Value"),
        ("show_system_message", "Value"),
    ]


@pytest.mark.asyncio
async def test_non_command_and_unknown_command() -> None:
    controller = Controller()
    dispatcher = CommandDispatcher(CommandRegistry())
    assert not await dispatcher.try_dispatch("hello", controller)
    assert await dispatcher.try_dispatch("/missing", controller)
    assert controller.calls[0] == ("show_user_input", "/missing")
    assert "/help" in controller.calls[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (CommandUsageError(), "用法：/test"),
        (CommandExecutionError("safe"), "safe"),
        (RuntimeError("secret"), "命令执行失败，请稍后重试。"),
    ],
)
async def test_safe_error_boundary(error, expected: str) -> None:
    async def handler(invocation, controller):
        raise error

    controller = Controller()
    await _dispatcher(handler).try_dispatch("/test", controller)
    assert expected in controller.calls[-1][1]
    assert "secret" not in controller.calls[-1][1]


@pytest.mark.asyncio
async def test_cancelled_error_propagates() -> None:
    async def handler(invocation, controller):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _dispatcher(handler).try_dispatch("/test", Controller())
