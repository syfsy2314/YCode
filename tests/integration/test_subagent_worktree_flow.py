import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.app import run_app
from ycode.core import (
    StopReason,
    StreamEnd,
    TextDelta,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)


def tool_turn(call_id: str, name: str, arguments: dict[str, object]):
    block = ToolCallBlock(call_id, name, arguments)
    return [
        ToolCallStart(0, block.id, block.name),
        ToolCallDelta(0, json.dumps(arguments, ensure_ascii=False)),
        ToolCallComplete(0, block),
        StreamEnd(StopReason.TOOL_USE),
    ]


def text_turn(text: str):
    return [TextDelta(0, text), StreamEnd(StopReason.END_TURN)]


def initialize_project(path: Path) -> None:
    subprocess.run(("git", "init", str(path)), check=True, capture_output=True)
    for key, value in (("user.name", "YCode Test"), ("user.email", "ycode@example.test")):
        subprocess.run(("git", "-C", str(path), "config", key, value), check=True)
    (path / ".gitignore").write_text(".ycode/worktrees/\n", encoding="utf-8")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(path), "add", ".gitignore", "base.txt"), check=True)
    subprocess.run(("git", "-C", str(path), "commit", "-m", "base"), check=True)


@pytest.mark.asyncio
async def test_two_defined_agents_write_to_distinct_retained_worktrees(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    config = tmp_path / ".ycode" / "config.yaml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(
        "active: main\nproviders:\n  - name: main\n    protocol: anthropic\n"
        "    model: main-test\n    base_url: http://localhost:9000/v1\n"
        "    api_key: placeholder\n",
        encoding="utf-8",
    )
    (tmp_path / ".ycode" / "security.yaml").write_text("mode: allow\n", encoding="utf-8")
    agents = tmp_path / ".ycode" / "agents"
    agents.mkdir()
    (agents / "writer.md").write_text(
        "---\nname: writer\ndescription: 隔离写作\nallowed-tools: [write_file]\n"
        "permission: allow\nisolation: worktree\n---\n只完成指定写作任务。\n",
        encoding="utf-8",
    )
    provider = FakeProvider(
        [
            tool_turn("parent-1", "run_subagent", {"task": "写第一份", "role": "writer"}),
            tool_turn("child-1", "write_file", {"path": "first.txt", "content": "one\n"}),
            text_turn("第一份完成"),
            text_turn("父任务一完成"),
            tool_turn("parent-2", "run_subagent", {"task": "写第二份", "role": "writer"}),
            tool_turn("child-2", "write_file", {"path": "second.txt", "content": "two\n"}),
            text_turn("第二份完成"),
            text_turn("父任务二完成"),
        ]
    )
    observed: dict[str, str] = {}

    class UI:
        def __init__(self, config, session) -> None:
            del config
            self.session = session

        async def run(self) -> None:
            async for _ in self.session.stream_reply("第一项"):
                pass
            async for _ in self.session.stream_reply("第二项"):
                pass
            observed["tasks"] = self.session.tasks_status()
            observed["worktrees"] = self.session.worktree_list()

    process_cwd = os.getcwd()
    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda _config: provider,
        ui_factory=UI,
    )
    assert os.getcwd() == process_cwd

    assert not (tmp_path / "first.txt").exists()
    assert not (tmp_path / "second.txt").exists()
    assert observed["tasks"].count("retained") >= 2
    paths = sorted((tmp_path / ".ycode" / "worktrees" / "agents").iterdir())
    assert len(paths) == 2
    assert any((path / "first.txt").is_file() for path in paths)
    assert any((path / "second.txt").is_file() for path in paths)
