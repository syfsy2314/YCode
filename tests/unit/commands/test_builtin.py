import pytest

from ycode.commands import CommandDefinition, CommandKind, build_command_runtime


class Controller:
    def __init__(self) -> None:
        self.calls = []

    def __getattr__(self, name):
        async def call(*args):
            self.calls.append((name, *args))

        return call


@pytest.mark.asyncio
async def test_production_definitions_and_help_come_from_registry() -> None:
    runtime = build_command_runtime()
    assert [item.name for item in runtime.registry.definitions] == [
        "help",
        "exit",
        "plan",
        "agent",
        "mcp",
        "compact",
        "permission",
        "resume",
    ]
    assert runtime.registry.resolve("QUIT").name == "exit"
    controller = Controller()
    await runtime.dispatcher.try_dispatch("/help", controller)
    output = controller.calls[-1][1]
    for definition in runtime.registry.visible_definitions():
        assert f"/{definition.name}" in output


@pytest.mark.asyncio
async def test_help_alias_detail_and_hidden_command() -> None:
    async def hidden_handler(invocation, controller):
        await controller.show_system_message("ran")

    hidden = CommandDefinition(
        "secret", ("s",), "hidden", "/secret", CommandKind.AI, "", hidden_handler, True
    )
    runtime = build_command_runtime((hidden,))
    controller = Controller()
    await runtime.dispatcher.try_dispatch("/help quit", controller)
    assert "/exit" in controller.calls[-1][1]
    await runtime.dispatcher.try_dispatch("/help secret", controller)
    assert "未知命令" in controller.calls[-1][1]
    await runtime.dispatcher.try_dispatch("/s", controller)
    assert controller.calls[-1] == ("show_system_message", "ran")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/plan", ("set_mode", "plan-only")),
        ("/agent", ("set_mode", "agent")),
        ("/mcp", ("show_mcp_status",)),
        ("/compact", ("compact_context",)),
        ("/permission strict", ("set_permission_mode", "strict")),
        ("/permission clear", ("clear_permission_grants",)),
        ("/resume Session-AbC", ("resume_session", "Session-AbC")),
        ("/resume Session With Spaces", ("resume_session", "Session With Spaces")),
        ("/quit", ("request_exit",)),
    ],
)
async def test_builtin_controller_calls(command: str, expected: tuple) -> None:
    controller = Controller()
    await build_command_runtime().dispatcher.try_dispatch(command, controller)
    assert expected in controller.calls
