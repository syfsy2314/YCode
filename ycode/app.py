"""YCode 应用装配。"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ycode.config.discovery import discover_config
from ycode.config.loader import load_config
from ycode.config.models import ProviderConfig
from ycode.core.provider import ChatProvider
from ycode.providers.factory import create_provider
from ycode.session.chat import ChatSession
from ycode.ui.terminal import TerminalUI

ProviderFactory = Callable[[ProviderConfig], ChatProvider]
UIFactory = Callable[[ProviderConfig, ChatSession], Any]


async def run_app(
    config_path: str | Path | None = None,
    *,
    start_dir: str | Path | None = None,
    provider_factory: ProviderFactory = create_provider,
    ui_factory: UIFactory = TerminalUI,
) -> None:
    path = discover_config(config_path, start_dir=start_dir)
    config = load_config(path)
    provider = provider_factory(config.active_provider)
    session = ChatSession(provider)
    try:
        ui = ui_factory(config.active_provider, session)
        await ui.run()
    finally:
        await session.close()
