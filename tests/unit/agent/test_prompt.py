from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ycode.agent import AgentMode, SystemPromptBuilder
from ycode.tools import ToolAccess, ToolDefinition


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


READ_DEFINITION = ToolDefinition(
    name="read_file",
    description="读取文件",
    access=ToolAccess.READ,
    arguments_model=NoArguments,
)
WRITE_DEFINITION = ToolDefinition(
    name="write_file",
    description="写入文件",
    access=ToolAccess.WRITE,
    arguments_model=NoArguments,
)


def test_agent_prompt_contains_minimum_runtime_context(tmp_path: Path) -> None:
    prompt = SystemPromptBuilder(tmp_path).build(
        AgentMode.AGENT,
        (READ_DEFINITION, WRITE_DEFINITION),
    )

    assert str(tmp_path.resolve()) in prompt
    assert "PowerShell" in prompt
    assert "read_file, write_file" in prompt
    assert "Agent mode" in prompt
    assert "Plan-only" not in prompt


def test_plan_prompt_requires_read_only_investigation(tmp_path: Path) -> None:
    prompt = SystemPromptBuilder(tmp_path).build(
        AgentMode.PLAN_ONLY,
        (READ_DEFINITION,),
    )

    assert "read_file" in prompt
    assert "write_file" not in prompt
    assert "Plan-only mode" in prompt
    assert "read tools only" in prompt
    assert "implementation plan" in prompt
