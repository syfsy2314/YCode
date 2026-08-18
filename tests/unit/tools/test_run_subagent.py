from pathlib import Path
from types import SimpleNamespace

import pytest

from ycode.agent import AgentToolScope
from ycode.subagents.models import RunSubagentArguments as RuntimeArguments
from ycode.subagents.models import SubagentIsolation
from ycode.tools.builtin.run_subagent import RunSubagentArguments, RunSubagentTool
from ycode.tools.contracts import ToolContext
from ycode.tools.errors import ToolError


@pytest.mark.asyncio
async def test_run_subagent_uses_current_parent_snapshot() -> None:
    parent = object()
    task = SimpleNamespace(task_id="task-1")
    manager = SimpleNamespace(start=lambda *_: None)

    async def start(arguments: RuntimeArguments, snapshot: object) -> object:
        assert arguments == RuntimeArguments(
            task="检查代码",
            role="explore",
            isolation=SubagentIsolation.WORKTREE,
        )
        assert snapshot is parent
        return task

    manager.start = start
    tool = RunSubagentTool(manager)  # type: ignore[arg-type]
    tool_result = SimpleNamespace(content="ok", metadata={})

    # 格式化依赖完整任务视图，此处只验证入口转发，替换模块级函数保持测试聚焦。
    import ycode.tools.builtin.run_subagent as module

    original_format = module.format_tool_result
    original_payload = module.task_payload
    module.format_tool_result = lambda value: "ok"
    module.task_payload = lambda value: {"task_id": value.task_id}
    try:
        result = await tool.execute(
            RunSubagentArguments(
                task="  检查代码  ",
                role=" explore ",
                isolation="worktree",
            ),
            ToolContext(Path.cwd(), agent_scope=AgentToolScope(current_snapshot=parent)),  # type: ignore[arg-type]
        )
    finally:
        module.format_tool_result = original_format
        module.task_payload = original_payload
    assert result.content == tool_result.content
    assert result.metadata["subagent"]["task_id"] == "task-1"


def test_run_subagent_isolation_schema_accepts_only_supported_values() -> None:
    assert RunSubagentArguments(task="检查", isolation="none").isolation is SubagentIsolation.NONE
    assert (
        RunSubagentArguments(task="检查", isolation="worktree").isolation
        is SubagentIsolation.WORKTREE
    )
    with pytest.raises(ValueError, match="isolation"):
        RunSubagentArguments(task="检查", isolation="container")


@pytest.mark.asyncio
async def test_run_subagent_requires_parent_snapshot() -> None:
    tool = RunSubagentTool(SimpleNamespace())  # type: ignore[arg-type]

    with pytest.raises(ToolError, match="父 Agent") as caught:
        await tool.execute(RunSubagentArguments(task="检查代码"), ToolContext(Path.cwd()))

    assert caught.value.code == "subagent_context_missing"
