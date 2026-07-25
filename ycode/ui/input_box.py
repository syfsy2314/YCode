"""异步终端输入提示区。"""

import sys

from prompt_toolkit import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout import (
    BufferControl,
    Dimension,
    FormattedTextControl,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.processors import (
    AfterInput,
    BeforeInput,
    ConditionalProcessor,
)
from prompt_toolkit.output.base import Output
from rich.console import Console

from ycode.ui.styles import (
    DEFAULT_INPUT_BORDER_STYLE,
    InputBorderStyle,
    create_prompt_style,
)

UNICODE_INDICATOR = "❯"
ASCII_INDICATOR = ">"
PLACEHOLDER = "Send a message..."
HELP_HINT = "? for help"


def supports_unicode_indicator(encoding: str | None = None) -> bool:
    target = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        UNICODE_INDICATOR.encode(target)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


class InputBox:
    def __init__(
        self,
        *,
        console: Console,
        unicode_supported: bool | None = None,
        border_style: InputBorderStyle = DEFAULT_INPUT_BORDER_STYLE,
        input: Input | None = None,
        output: Output | None = None,
    ) -> None:
        self._console = console
        self._unicode_supported = (
            supports_unicode_indicator() if unicode_supported is None else unicode_supported
        )
        self._border_style = border_style
        self._input = input
        self._output = output

    def _create_application(self, width: int) -> Application[str]:
        indicator = UNICODE_INDICATOR if self._unicode_supported else ASCII_INDICATOR

        def accept(buffer: Buffer) -> bool:
            get_app().exit(result=buffer.text, style="class:accepted")
            return True

        buffer = Buffer(multiline=False, accept_handler=accept)

        @Condition
        def display_placeholder() -> bool:
            return buffer.text == ""

        input_control = BufferControl(
            buffer=buffer,
            input_processors=[
                BeforeInput(FormattedText([("class:prompt", f"{indicator} ")])),
                ConditionalProcessor(
                    AfterInput(FormattedText([("class:placeholder", PLACEHOLDER)])),
                    filter=display_placeholder,
                ),
            ],
        )
        exact_width = Dimension.exact(width)

        def make_border() -> Window:
            return Window(
                char="─",
                width=exact_width,
                height=1,
                dont_extend_width=True,
                dont_extend_height=True,
                style="class:input-border",
            )

        input_window = Window(
            content=input_control,
            width=exact_width,
            height=1,
            dont_extend_width=True,
            dont_extend_height=True,
            wrap_lines=False,
        )
        hint_window = Window(
            content=FormattedTextControl(FormattedText([("class:input-hint", HELP_HINT)])),
            width=exact_width,
            height=1,
            dont_extend_width=True,
            dont_extend_height=True,
        )
        root = HSplit([make_border(), input_window, make_border(), hint_window])

        return Application(
            layout=Layout(root, focused_element=input_window),
            key_bindings=load_key_bindings(),
            style=create_prompt_style(self._border_style),
            full_screen=False,
            erase_when_done=True,
            input=self._input,
            output=self._output,
        )

    async def read(self) -> str:
        width = max(1, min(self._console.width - 1, 100))
        return await self._create_application(width).run_async()
