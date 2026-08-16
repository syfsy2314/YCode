"""异步终端输入提示区。"""

import asyncio
import sys

from prompt_toolkit import Application
from prompt_toolkit.application.current import get_app, get_app_session
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input.base import Input
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    BufferControl,
    Dimension,
    Float,
    FloatContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import (
    AfterInput,
    BeforeInput,
    ConditionalProcessor,
)
from prompt_toolkit.output.base import Output
from rich.console import Console

from ycode.agent import AgentMode
from ycode.security import ApprovalChoice, PermissionDecision, PermissionMode
from ycode.ui.styles import (
    DEFAULT_INPUT_BORDER_STYLE,
    InputBorderStyle,
    create_prompt_style,
)

UNICODE_INDICATOR = "❯"
ASCII_INDICATOR = ">"
PLACEHOLDER = "Send a message..."
HELP_HINT = "? for help"


def format_hint(
    width: int,
    mode: AgentMode,
    permission_mode: PermissionMode | None = None,
    help_hint: str = HELP_HINT,
) -> str:
    full_mode = f"mode: {mode.value}"
    if permission_mode is not None:
        full_mode += f"  permission: {permission_mode.value}"
    if width >= len(help_hint) + len(full_mode) + 1:
        return f"{help_hint}{' ' * (width - len(help_hint) - len(full_mode))}{full_mode}"
    if width >= len(full_mode):
        return full_mode.rjust(width)
    if width >= len(mode.value):
        return mode.value.rjust(width)
    compact = "A" if mode is AgentMode.AGENT else "P"
    return compact.rjust(width)


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
        completer: Completer | None = None,
        help_hint: str = HELP_HINT,
    ) -> None:
        self._console = console
        self._unicode_supported = (
            supports_unicode_indicator() if unicode_supported is None else unicode_supported
        )
        self._border_style = border_style
        self._input = input
        self._output = output
        self._completer = completer
        self._help_hint = help_hint

    def _create_application(
        self,
        width: int,
        mode: AgentMode = AgentMode.AGENT,
        permission_mode: PermissionMode | None = None,
    ) -> Application[str]:
        indicator = UNICODE_INDICATOR if self._unicode_supported else ASCII_INDICATOR

        def accept(buffer: Buffer) -> bool:
            get_app().exit(result=buffer.text, style="class:accepted")
            return True

        buffer = Buffer(
            multiline=False,
            accept_handler=accept,
            completer=self._completer,
            complete_while_typing=False,
        )

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
            content=FormattedTextControl(
                FormattedText(
                    [
                        (
                            "class:input-hint",
                            format_hint(width, mode, permission_mode, self._help_hint),
                        )
                    ]
                )
            ),
            width=exact_width,
            height=1,
            dont_extend_width=True,
            dont_extend_height=True,
        )
        content = HSplit([make_border(), input_window, make_border(), hint_window])
        root = FloatContainer(
            content=content,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=8, scroll_offset=1),
                )
            ],
        )
        bindings = KeyBindings()

        @bindings.add("c-c")
        def exit_application(event) -> None:
            event.app.exit(exception=KeyboardInterrupt())

        @bindings.add("escape")
        def escape_application(event) -> None:
            event.app.exit(exception=KeyboardInterrupt())

        return Application(
            layout=Layout(root, focused_element=input_window),
            key_bindings=merge_key_bindings([load_key_bindings(), bindings]),
            style=create_prompt_style(self._border_style),
            full_screen=False,
            erase_when_done=True,
            input=self._input,
            output=self._output,
        )

    async def read(
        self,
        mode: AgentMode = AgentMode.AGENT,
        permission_mode: PermissionMode | None = None,
    ) -> str:
        width = max(1, min(self._console.width - 1, 100))
        return await self._create_application(
            width,
            mode,
            permission_mode,
        ).run_async()

    async def read_approval(self, decision: PermissionDecision) -> ApprovalChoice:
        approval_prompt = (
            f"\n工具审批：{decision.subject.call.name}\n"
            f"原因：{decision.message}\n"
            f"{decision.subject.approval_summary}\n"
            + (
                "[1] 拒绝  [2] 本次允许  [3] 本会话允许"
                if decision.allow_session
                else "[1] 拒绝  [2] 本次允许"
            )
        )
        bindings = KeyBindings()

        @bindings.add("1")
        def deny(event) -> None:
            event.app.exit(result=ApprovalChoice.DENY)

        @bindings.add("2")
        def allow_once(event) -> None:
            event.app.exit(result=ApprovalChoice.ALLOW_ONCE)

        if decision.allow_session:

            @bindings.add("3")
            def allow_session(event) -> None:
                event.app.exit(result=ApprovalChoice.ALLOW_SESSION)

        @bindings.add("c-c")
        def cancel(event) -> None:
            event.app.exit(exception=KeyboardInterrupt())

        @bindings.add("escape")
        def cancel_escape(event) -> None:
            event.app.exit(exception=KeyboardInterrupt())

        application: Application[ApprovalChoice] = Application(
            layout=Layout(
                Window(
                    FormattedTextControl(
                        FormattedText(
                            [
                                (
                                    "class:input-hint",
                                    (
                                        "请选择 1、2 或 3（Ctrl+C 取消）"
                                        if decision.allow_session
                                        else "请选择 1 或 2（Ctrl+C 取消）"
                                    ),
                                )
                            ]
                        )
                    )
                )
            ),
            key_bindings=bindings,
            style=create_prompt_style(self._border_style),
            full_screen=False,
            erase_when_done=True,
            input=self._input,
            output=self._output,
        )
        # 等审批应用完成输入接管后再显示提示，避免快速按键落在监听切换间隙。
        return await application.run_async(pre_run=lambda: self._console.print(approval_prompt))

    async def wait_for_interrupt(self) -> None:
        """响应期间只监听 ESC 或 Ctrl+C，不接受普通文本输入。"""
        input_device = self._input or get_app_session().input
        loop = asyncio.get_running_loop()
        interrupted = loop.create_future()
        flush_handle: asyncio.TimerHandle | None = None

        def consume(keys) -> None:
            for key_press in keys:
                if key_press.key in {Keys.ControlC, Keys.Escape} and not interrupted.done():
                    interrupted.set_result(None)

        def flush_escape() -> None:
            if not interrupted.done():
                consume(input_device.flush_keys())

        def input_ready() -> None:
            nonlocal flush_handle
            consume(input_device.read_keys())
            if not interrupted.done():
                # VT100 解析器需要一个短暂等待来区分单独 ESC 与转义序列。
                if flush_handle is not None:
                    flush_handle.cancel()
                flush_handle = loop.call_later(0.05, flush_escape)

        with input_device.raw_mode(), input_device.attach(input_ready):
            try:
                await interrupted
            finally:
                if flush_handle is not None:
                    flush_handle.cancel()
