import asyncio
import json
from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.agent import FinalResponseEvent
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


def write_config(path: Path, *, child: bool = False) -> None:
    providers = (
        "  - name: child\n"
        "    protocol: anthropic\n"
        "    model: child-test\n"
        "    base_url: http://localhost:9001/v1\n"
        "    api_key: placeholder\n"
        if child
        else ""
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "active: main\nproviders:\n"
        "  - name: main\n"
        "    protocol: anthropic\n"
        "    model: main-test\n"
        "    base_url: http://localhost:9000/v1\n"
        "    api_key: placeholder\n" + providers,
        encoding="utf-8",
    )


def text_turn(text: str):
    return [TextDelta(0, text), StreamEnd(StopReason.END_TURN)]


def subagent_turn(arguments: dict[str, object]):
    payload = json.dumps(arguments, ensure_ascii=False)
    block = ToolCallBlock("call-subagent", "run_subagent", arguments)
    return [
        ToolCallStart(0, block.id, block.name),
        ToolCallDelta(0, payload),
        ToolCallComplete(0, block),
        StreamEnd(StopReason.TOOL_USE),
    ]


@pytest.mark.asyncio
async def test_defined_sync_subagent_runs_to_completion_inside_parent_tool(tmp_path: Path) -> None:
    write_config(tmp_path / ".ycode" / "config.yaml")
    provider = FakeProvider(
        [
            subagent_turn({"task": "探索入口", "role": "explore"}),
            text_turn("子任务结论"),
            text_turn("父任务完成"),
        ]
    )
    observed: dict[str, object] = {}

    class UI:
        def __init__(self, config, session) -> None:
            del config
            self.session = session

        async def run(self) -> None:
            events = [event async for event in self.session.stream_reply("检查项目")]
            observed["events"] = events
            observed["tasks"] = self.session.tasks_status()

    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: provider,
        ui_factory=UI,
    )

    assert isinstance(observed["events"][-1], FinalResponseEvent)  # type: ignore[index]
    assert observed["events"][-1].message.text == "父任务完成"  # type: ignore[index,union-attr]
    assert "completed" in observed["tasks"]  # type: ignore[operator]
    assert provider.agent_requests[1].messages[-1].text == "探索入口"
    assert "你负责探索当前项目" in "\n".join(provider.agent_requests[1].system_prompt)
    parent_followup = provider.agent_requests[2].messages[-1]
    assert "子任务结论" in parent_followup.content[0].content  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_defined_async_subagent_notifies_next_safe_parent_request(tmp_path: Path) -> None:
    write_config(tmp_path / ".ycode" / "config.yaml", child=True)
    agents = tmp_path / ".ycode" / "agents"
    agents.mkdir()
    (agents / "worker.md").write_text(
        "---\nname: worker\ndescription: 后台工作\nmodel: child\n"
        "allowed-tools: [read_file]\npermission: strict\n---\n后台独立完成任务。\n",
        encoding="utf-8",
    )
    parent = FakeProvider(
        [
            subagent_turn({"task": "后台检查", "role": "worker", "mode": "async"}),
            text_turn("主回合继续"),
            text_turn("已接收通知"),
        ]
    )
    child = FakeProvider([text_turn("后台完成")], delay=0.01)
    observed: dict[str, str] = {}

    class UI:
        def __init__(self, config, session) -> None:
            del config
            self.session = session

        async def run(self) -> None:
            async for _ in self.session.stream_reply("启动后台检查"):
                pass
            for _ in range(100):
                detail = self.session.tasks_status()
                if "completed" in detail:
                    break
                await asyncio.sleep(0.01)
            observed["tasks"] = detail
            async for _ in self.session.stream_reply("继续"):
                pass

    providers = {"main": parent, "child": child}
    await run_app(
        start_dir=tmp_path,
        provider_factory=lambda config: providers[config.name],
        ui_factory=UI,
    )

    assert "completed" in observed["tasks"]
    notification_request = parent.agent_requests[2]
    assert any("异步子 Agent 任务已进入终态" in item for item in notification_request.supplements)
    assert not any(
        "异步子 Agent 任务已进入终态" in item for item in parent.agent_requests[1].supplements
    )
    assert child.close_count == 1
