import asyncio
from io import StringIO

import pytest
from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text
from prompt_toolkit.input import DummyInput, create_pipe_input
from prompt_toolkit.layout import (
    BufferControl,
    FloatContainer,
    FormattedTextControl,
    HSplit,
    Window,
)
from prompt_toolkit.layout.processors import AfterInput, BeforeInput, ConditionalProcessor
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from ycode.agent import AgentMode
from ycode.core.messages import ToolCallBlock
from ycode.security import (
    ApprovalChoice,
    PermissionAction,
    PermissionDecision,
    PermissionMode,
    PermissionSubject,
)
from ycode.ui.input_box import (
    ASCII_INDICATOR,
    HELP_HINT,
    PLACEHOLDER,
    UNICODE_INDICATOR,
    InputBox,
    format_hint,
)
from ycode.ui.styles import InputBorderStyle, create_prompt_style


def console(width: int = 20) -> Console:
    return Console(file=StringIO(), width=width, color_system=None)


@pytest.mark.parametrize(
    ("unicode_supported", "expected"),
    [(True, UNICODE_INDICATOR), (False, ASCII_INDICATOR)],
)
def test_four_line_layout_and_indicator(unicode_supported: bool, expected: str) -> None:
    box = InputBox(
        console=console(),
        unicode_supported=unicode_supported,
        input=DummyInput(),
        output=DummyOutput(),
    )
    application = box._create_application(19)
    root = application.layout.container

    assert isinstance(root, FloatContainer)
    assert isinstance(root.content, HSplit)
    assert len(root.content.children) == 4
    top, input_window, bottom, hint = root.content.children

    assert isinstance(top, Window)
    assert isinstance(input_window, Window)
    assert isinstance(bottom, Window)
    assert isinstance(hint, Window)
    assert top.char == bottom.char == "─"
    assert top.style == bottom.style == "class:input-border"
    assert top.width == bottom.width

    assert isinstance(input_window.content, BufferControl)
    processors = input_window.content.input_processors
    assert isinstance(processors[0], BeforeInput)
    assert expected in fragment_list_to_text(to_formatted_text(processors[0].text))
    assert isinstance(processors[1], ConditionalProcessor)
    assert isinstance(processors[1].processor, AfterInput)
    assert PLACEHOLDER in fragment_list_to_text(to_formatted_text(processors[1].processor.text))

    assert isinstance(hint.content, FormattedTextControl)
    assert "mode: agent" in fragment_list_to_text(to_formatted_text(hint.content.text))


def test_border_color_is_injected_without_changing_hint() -> None:
    style = create_prompt_style(InputBorderStyle(color="ansired"))
    rules = dict(style.style_rules)

    assert rules["input-border"].startswith("ansired ")
    assert "ansired" not in rules["input-hint"]
    assert "noreverse" in rules["input-border"]
    assert "noreverse" in rules["input-hint"]


def test_layout_reserves_last_terminal_column() -> None:
    box = InputBox(
        console=console(width=20),
        unicode_supported=True,
        input=DummyInput(),
        output=DummyOutput(),
    )
    application = box._create_application(19)
    root = application.layout.container
    assert isinstance(root, FloatContainer)
    assert isinstance(root.content, HSplit)
    top, _, bottom, _ = root.content.children

    assert isinstance(top, Window)
    assert isinstance(bottom, Window)
    assert top.width.preferred == 19
    assert bottom.width.preferred == 19


def test_hint_places_mode_on_right_and_prioritizes_it_when_narrow() -> None:
    assert format_hint(40, AgentMode.AGENT).startswith(HELP_HINT)
    assert format_hint(40, AgentMode.AGENT).endswith("mode: agent")
    assert format_hint(15, AgentMode.PLAN_ONLY) == "mode: plan-only"
    assert format_hint(9, AgentMode.PLAN_ONLY) == "plan-only"
    assert format_hint(1, AgentMode.PLAN_ONLY) == "P"
    assert "permission: strict" in format_hint(
        60,
        AgentMode.AGENT,
        PermissionMode.STRICT,
    )
    assert format_hint(40, AgentMode.AGENT, help_hint="/help for commands").startswith(
        "/help for commands"
    )


@pytest.mark.asyncio
async def test_read_submits_plain_text() -> None:
    with create_pipe_input() as pipe_input:
        box = InputBox(
            console=console(),
            unicode_supported=True,
            input=pipe_input,
            output=DummyOutput(),
        )
        pipe_input.send_text("hello\r")

        assert await box.read() == "hello"


@pytest.mark.asyncio
async def test_question_mark_is_plain_input() -> None:
    with create_pipe_input() as pipe_input:
        box = InputBox(
            console=console(),
            unicode_supported=True,
            input=pipe_input,
            output=DummyOutput(),
        )
        pipe_input.send_text("?\r")

        assert await box.read() == "?"


@pytest.mark.asyncio
async def test_approval_accepts_only_direct_three_way_choice() -> None:
    subject = PermissionSubject(
        ToolCallBlock("call-1", "run_command", {"command": "git status"}),
        {"command": "git status", "cwd": "."},
        {"tool": "run_command", "command": "git status", "cwd": "."},
        "command:\ngit status",
    )
    decision = PermissionDecision(
        PermissionAction.ASK,
        subject,
        "mode_default",
        "需要用户确认。",
    )
    with create_pipe_input() as pipe_input:
        box = InputBox(
            console=console(),
            unicode_supported=True,
            input=pipe_input,
            output=DummyOutput(),
        )
        pipe_input.send_text("2")

        assert await box.read_approval(decision) is ApprovalChoice.ALLOW_ONCE


@pytest.mark.asyncio
async def test_approval_without_session_option_does_not_bind_three() -> None:
    subject = PermissionSubject(
        ToolCallBlock("call-1", "mcp_demo_echo", {}),
        {},
        {"tool": "mcp_demo_echo", "arguments": {}},
        "{}",
    )
    decision = PermissionDecision(
        PermissionAction.ASK,
        subject,
        "plan_only_mcp_approval",
        "每次确认。",
        allow_session=False,
    )
    with create_pipe_input() as pipe_input:
        box = InputBox(
            console=console(),
            unicode_supported=True,
            input=pipe_input,
            output=DummyOutput(),
        )
        task = asyncio.create_task(box.read_approval(decision))
        pipe_input.send_text("3")
        await asyncio.sleep(0.05)

        assert not task.done()
        pipe_input.send_text("2")
        assert await task is ApprovalChoice.ALLOW_ONCE


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["\x03", "\x1b"])
async def test_wait_for_interrupt_accepts_ctrl_c_and_escape(key: str) -> None:
    with create_pipe_input() as pipe_input:
        box = InputBox(
            console=console(),
            unicode_supported=True,
            input=pipe_input,
            output=DummyOutput(),
        )
        task = asyncio.create_task(box.wait_for_interrupt())
        pipe_input.send_text(key)
        await asyncio.wait_for(task, timeout=1)
