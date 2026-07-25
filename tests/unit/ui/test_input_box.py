from io import StringIO

import pytest
from prompt_toolkit.formatted_text import fragment_list_to_text, to_formatted_text
from prompt_toolkit.input import DummyInput, create_pipe_input
from prompt_toolkit.layout import BufferControl, FormattedTextControl, HSplit, Window
from prompt_toolkit.layout.processors import AfterInput, BeforeInput, ConditionalProcessor
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from ycode.ui.input_box import (
    ASCII_INDICATOR,
    HELP_HINT,
    PLACEHOLDER,
    UNICODE_INDICATOR,
    InputBox,
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

    assert isinstance(root, HSplit)
    assert len(root.children) == 4
    top, input_window, bottom, hint = root.children

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
    assert HELP_HINT in fragment_list_to_text(to_formatted_text(hint.content.text))


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
    top, _, bottom, _ = root.children

    assert isinstance(top, Window)
    assert isinstance(bottom, Window)
    assert top.width.preferred == 19
    assert bottom.width.preferred == 19


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
