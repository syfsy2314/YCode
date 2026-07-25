"""单轮流式响应与最终 Markdown 渲染。"""

import asyncio
from contextlib import suppress

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from ycode.ui.styles import BLUE, ERROR, MUTED
from ycode.ui.timer import ResponseTimer


class LiveResponseRenderer:
    def __init__(
        self,
        *,
        console: Console,
        timer: ResponseTimer | None = None,
        refresh_interval: float = 0.1,
    ) -> None:
        self._console = console
        self._timer = timer or ResponseTimer()
        self._refresh_interval = refresh_interval
        self._thinking_parts: list[str] = []
        self._text_parts: list[str] = []
        self._error: str | None = None
        self._final = False
        self._live: Live | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def thinking_text(self) -> str:
        return "".join(self._thinking_parts)

    @property
    def response_text(self) -> str:
        return "".join(self._text_parts)

    @property
    def elapsed(self) -> float:
        return self._timer.elapsed

    def _title(self) -> Text:
        title = Text("● YCode", style=f"bold {BLUE}")
        title.append(f"  {self.elapsed:.1f}s", style=MUTED)
        return title

    def renderable(self) -> RenderableType:
        items: list[RenderableType] = []
        if self.thinking_text:
            items.extend(
                [
                    Text("◇ Thinking", style=f"bold {BLUE}"),
                    Text(self.thinking_text),
                    Text(""),
                ]
            )
        items.append(self._title())
        if self._final:
            items.append(Markdown(self.response_text))
        else:
            items.append(Text(self.response_text))
        if self._error:
            items.extend([Text(""), Text(self._error, style=ERROR)])
        return Group(*items)

    async def start(self) -> None:
        self._thinking_parts.clear()
        self._text_parts.clear()
        self._error = None
        self._final = False
        self._timer.start()
        self._live = Live(
            self.renderable(),
            console=self._console,
            refresh_per_second=10,
            transient=False,
            auto_refresh=False,
        )
        self._live.start(refresh=True)
        self._refresh_task = asyncio.create_task(self._refresh_timer())

    async def _refresh_timer(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._refresh_interval)
                self._update()
        except asyncio.CancelledError:
            raise

    def _update(self) -> None:
        if self._live is not None:
            self._live.update(self.renderable(), refresh=True)

    def append_thinking(self, text: str) -> None:
        self._thinking_parts.append(text)
        self._update()

    def append_text(self, text: str) -> None:
        self._text_parts.append(text)
        self._update()

    async def _stop_refresh(self) -> None:
        if self._refresh_task is None:
            return
        self._refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._refresh_task
        self._refresh_task = None

    async def complete(self) -> None:
        await self._stop_refresh()
        self._timer.stop()
        self._final = True
        self._update()
        self._stop_live()

    async def fail(self, message: str) -> None:
        await self._stop_refresh()
        self._timer.stop()
        self._error = message
        self._update()
        self._stop_live()

    async def cancel(self) -> None:
        await self._stop_refresh()
        self._timer.stop()
        self._update()
        self._stop_live()

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
