"""TUI 对话循环。"""

import asyncio
from collections.abc import Callable
from typing import Any

from rich.console import Console

from ycode.config.models import ProviderConfig
from ycode.core.events import StreamEnd, TextDelta, ThinkingDelta
from ycode.errors import ProviderError
from ycode.session.chat import ChatSession
from ycode.ui.header import render_header
from ycode.ui.input_box import InputBox
from ycode.ui.renderer import LiveResponseRenderer
from ycode.ui.user_message import render_user_message

InputFactory = Callable[[Console], Any]
RendererFactory = Callable[[Console], Any]


class TerminalUI:
    def __init__(
        self,
        config: ProviderConfig,
        session: ChatSession,
        *,
        console: Console | None = None,
        input_factory: InputFactory | None = None,
        renderer_factory: RendererFactory | None = None,
    ) -> None:
        self._config = config
        self._session = session
        self._console = console or Console()
        self._input = (
            input_factory(self._console) if input_factory else InputBox(console=self._console)
        )
        self._renderer_factory = renderer_factory or (
            lambda console: LiveResponseRenderer(console=console)
        )

    async def run(self) -> None:
        self._console.print(render_header(self._config, self._console.width))
        while True:
            try:
                user_text = await self._input.read()
            except (EOFError, KeyboardInterrupt):
                return

            if user_text.strip().lower() in {"/exit", "/quit"}:
                return
            if not user_text.strip():
                continue

            self._console.print(
                render_user_message(
                    user_text,
                    self._console.width,
                    encoding=getattr(self._console.file, "encoding", None),
                )
            )
            renderer = self._renderer_factory(self._console)
            await renderer.start()
            try:
                async for event in self._session.stream_reply(user_text):
                    if isinstance(event, ThinkingDelta):
                        renderer.append_thinking(event.text)
                    elif isinstance(event, TextDelta):
                        renderer.append_text(event.text)
                    elif isinstance(event, StreamEnd):
                        await renderer.complete()
            except ProviderError as error:
                await renderer.fail(error.user_message)
            except (KeyboardInterrupt, EOFError):
                await renderer.cancel()
                return
            except asyncio.CancelledError:
                await renderer.cancel()
                raise
