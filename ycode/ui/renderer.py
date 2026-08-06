"""多轮 Agent 响应、工具状态与最终 Markdown 渲染。"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from ycode.core.messages import ChatMessage
from ycode.ui.styles import BLUE, ERROR, MUTED
from ycode.ui.timer import ResponseTimer


@dataclass(slots=True)
class _RoundContent:
    thinking_parts: list[str] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    tool_statuses: dict[str, str] = field(default_factory=dict)

    @property
    def thinking(self) -> str:
        return "".join(self.thinking_parts)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


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
        self._rounds: dict[int, _RoundContent] = {}
        self._error: str | None = None
        self._final_message: ChatMessage | None = None
        self._live: Live | None = None
        self._refresh_task: asyncio.Task[None] | None = None

    @property
    def thinking_text(self) -> str:
        return "".join(content.thinking for content in self._rounds.values())

    @property
    def response_text(self) -> str:
        return "".join(content.text for content in self._rounds.values())

    @property
    def elapsed(self) -> float:
        return self._timer.elapsed

    def _title(self) -> Text:
        title = Text("● YCode", style=f"bold {BLUE}")
        title.append(f"  {self.elapsed:.1f}s", style=MUTED)
        return title

    def renderable(self) -> RenderableType:
        items: list[RenderableType] = []
        final_round = max(self._rounds, default=1)
        if self._rounds:
            items.append(self._title())
        for round_number, content in self._rounds.items():
            if content.thinking:
                items.extend(
                    [
                        Text("◇ Thinking", style=f"bold {BLUE}"),
                        Text(content.thinking),
                        Text(""),
                    ]
                )
            if self._final_message is not None and round_number == final_round:
                items.append(Markdown(self._final_message.text))
            else:
                items.append(Text(content.text))
            for status in content.tool_statuses.values():
                items.append(Text(status))
            items.append(Text(""))
        if self._error:
            items.extend([Text(""), Text(self._error, style=ERROR)])
        return Group(*items)

    async def start(self) -> None:
        self._rounds.clear()
        self._error = None
        self._final_message = None
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

    def _round(self, round_number: int) -> _RoundContent:
        return self._rounds.setdefault(round_number, _RoundContent())

    def append_thinking(self, text: str, round_number: int = 1) -> None:
        self._round(round_number).thinking_parts.append(text)
        self._update()

    def append_text(self, text: str, round_number: int = 1) -> None:
        self._round(round_number).text_parts.append(text)
        self._update()

    def set_tool_status(self, round_number: int, call_id: str, status: str) -> None:
        self._round(round_number).tool_statuses[call_id] = status
        self._update()

    def _update(self) -> None:
        if self._live is not None:
            self._live.update(self.renderable(), refresh=True)

    async def _stop_refresh(self) -> None:
        if self._refresh_task is None:
            return
        self._refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._refresh_task
        self._refresh_task = None

    async def complete(self, message: ChatMessage | None = None) -> None:
        await self._stop_refresh()
        self._timer.stop()
        self._final_message = message or ChatMessage.assistant_text(self.response_text)
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

    async def pause(self) -> None:
        await self._stop_refresh()
        self._stop_live()

    async def resume(self) -> None:
        if self._live is not None:
            return
        self._live = Live(
            self.renderable(),
            console=self._console,
            refresh_per_second=10,
            transient=False,
            auto_refresh=False,
        )
        self._live.start(refresh=True)
        self._refresh_task = asyncio.create_task(self._refresh_timer())

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None
