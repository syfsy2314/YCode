"""用户消息在终端滚动区中的展示。"""

from rich.style import Style
from rich.table import Table
from rich.text import Text

from ycode.ui.input_box import ASCII_INDICATOR, UNICODE_INDICATOR, supports_unicode_indicator
from ycode.ui.styles import BLUE

USER_MESSAGE_BACKGROUND = "grey15"
MAX_MESSAGE_WIDTH = 100


def render_user_message(
    message: str,
    terminal_width: int,
    *,
    encoding: str | None = None,
) -> Table:
    width = max(1, min(terminal_width - 1, MAX_MESSAGE_WIDTH))
    indicator = UNICODE_INDICATOR if supports_unicode_indicator(encoding) else ASCII_INDICATOR
    table = Table(
        box=None,
        show_header=False,
        show_edge=False,
        expand=False,
        width=width,
        padding=(0, 1),
        pad_edge=True,
        style=Style(bgcolor=USER_MESSAGE_BACKGROUND),
    )
    table.add_column(width=1, no_wrap=True)
    table.add_column(ratio=1, overflow="fold")
    table.add_row(Text(indicator, style=f"{BLUE} bold"), Text(message))
    return table
