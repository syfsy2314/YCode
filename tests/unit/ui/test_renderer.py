from io import StringIO

import pytest
from rich.console import Console

from ycode.core import ChatMessage
from ycode.ui.renderer import LiveResponseRenderer
from ycode.ui.timer import ResponseTimer


class Clock:
    value = 0.0

    def __call__(self) -> float:
        return self.value


def snapshot(renderer: LiveResponseRenderer) -> str:
    target = StringIO()
    console = Console(file=target, width=80, color_system=None)
    console.print(renderer.renderable())
    return target.getvalue()


@pytest.mark.asyncio
async def test_start_and_deltas_are_plain_text() -> None:
    target = StringIO()
    console = Console(file=target, width=80, color_system=None)
    clock = Clock()
    renderer = LiveResponseRenderer(
        console=console, timer=ResponseTimer(clock), refresh_interval=10
    )

    await renderer.start()
    clock.value = 0.4
    renderer.append_thinking("**reason**")
    renderer.append_text("**bo")
    renderer.append_text("ld**")

    output = snapshot(renderer)
    assert "◇ Thinking" in output
    assert "**reason**" in output
    assert "**bold**" in output
    assert renderer.response_text == "**bold**"
    await renderer.cancel()


@pytest.mark.asyncio
async def test_complete_freezes_time_and_renders_markdown_once() -> None:
    target = StringIO()
    console = Console(file=target, width=80, color_system=None)
    clock = Clock()
    renderer = LiveResponseRenderer(
        console=console, timer=ResponseTimer(clock), refresh_interval=10
    )
    await renderer.start()
    renderer.append_text(
        "# Title\n\n**bold**, *italic* and `code`\n\n"
        "```python\nprint('ok')\n```\n\n- item\n\n> quote\n\n[link](https://example.com)"
    )
    clock.value = 1.2
    await renderer.complete()
    clock.value = 9.0

    output = snapshot(renderer)
    assert "Title" in output and "bold" in output and "italic" in output and "code" in output
    assert "print('ok')" in output and "item" in output and "quote" in output and "link" in output
    assert "**bold**" not in output
    assert renderer.elapsed == 1.2


@pytest.mark.asyncio
async def test_failure_keeps_partial_text_and_can_restart() -> None:
    target = StringIO()
    renderer = LiveResponseRenderer(
        console=Console(file=target, width=80, color_system=None), refresh_interval=10
    )
    await renderer.start()
    renderer.append_text("partial")
    await renderer.fail("safe error")
    output = snapshot(renderer)
    assert "partial" in output and "safe error" in output

    await renderer.start()
    assert renderer.response_text == ""
    assert "safe error" not in snapshot(renderer)
    await renderer.cancel()


@pytest.mark.asyncio
async def test_multiple_rounds_keep_process_text_out_of_final_markdown() -> None:
    target = StringIO()
    renderer = LiveResponseRenderer(
        console=Console(file=target, width=80, color_system=None),
        refresh_interval=10,
    )
    await renderer.start()
    renderer.append_text("**process**", round_number=1)
    renderer.add_tool_status("✓ read_file  读取 1 行")
    renderer.append_text("# Final", round_number=2)

    await renderer.complete(ChatMessage.assistant_text("# Final"))

    output = snapshot(renderer)
    assert "**process**" in output
    assert "Final" in output
    assert "# Final" not in output
    assert "read_file" in output
