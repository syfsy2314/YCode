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
    renderer.set_tool_status(1, "call-1", "✓ read_file  读取 1 行")
    renderer.append_text("# Final", round_number=2)

    await renderer.complete(ChatMessage.assistant_text("# Final"))

    output = snapshot(renderer)
    assert "**process**" in output
    assert "Final" in output
    assert "# Final" not in output
    assert "read_file" in output
    assert output.count("● YCode") == 1
    assert "round 2" not in output


def test_tool_status_replaces_same_call_and_renders_inside_its_round() -> None:
    target = StringIO()
    renderer = LiveResponseRenderer(
        console=Console(file=target, width=80, color_system=None),
        refresh_interval=10,
    )

    renderer.append_text("first round", round_number=1)
    renderer.set_tool_status(1, "call-1", "◇ read_file  a.txt")
    renderer.set_tool_status(1, "call-2", "◇ grep  pattern")
    renderer.set_tool_status(1, "call-1", "? read_file  等待用户确认")
    renderer.set_tool_status(1, "call-1", "✓ read_file  读取 1 行")
    renderer.set_tool_status(1, "call-2", "✗ grep  搜索失败")
    renderer.append_text("second round", round_number=2)
    renderer.set_tool_status(2, "call-3", "– run_command  已取消")

    output = snapshot(renderer)
    assert "◇ read_file" not in output
    assert "等待用户确认" not in output
    assert output.count("read_file") == 1
    assert output.count("grep") == 1
    assert output.count("run_command") == 1
    assert (
        output.index("first round")
        < output.index("read_file")
        < output.index("grep")
        < output.index("second round")
        < output.index("run_command")
    )
    assert output.count("● YCode") == 1
    assert "round 1" not in output
    assert "round 2" not in output
