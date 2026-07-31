"""启动头部和猫图标。"""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from ycode.config.models import ProviderConfig
from ycode.security import PermissionMode
from ycode.ui.styles import BLUE, MUTED

CAT_LINES = (" /\\_/\\", "( o.o )   YCode", " > ^ <")


def _cat() -> Text:
    return Text("\n".join(CAT_LINES), style=f"bold {BLUE}")


def _details(
    config: ProviderConfig,
    permission_mode: PermissionMode | None = None,
) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style=MUTED, no_wrap=True)
    table.add_column()
    table.add_row("Provider", config.name)
    table.add_row("Protocol", config.protocol.value)
    table.add_row("Model", config.model)
    table.add_row("Thinking", "on" if config.thinking else "off")
    if permission_mode is not None:
        table.add_row("Permission", permission_mode.value)
    return table


def render_header(
    config: ProviderConfig,
    width: int,
    permission_mode: PermissionMode | None = None,
) -> RenderableType:
    if width >= 60:
        layout = Table.grid(padding=(0, 5))
        layout.add_column(no_wrap=True)
        layout.add_column()
        layout.add_row(_cat(), _details(config, permission_mode))
        return layout
    return Group(_cat(), Text(""), _details(config, permission_mode))
