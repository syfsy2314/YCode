"""本阶段最小可用的 Agent 系统提示。"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ycode.agent.contracts import AgentMode
from ycode.tools.contracts import ToolDefinition


class SystemPromptBuilder:
    def __init__(self, workspace: Path, shell: str = "PowerShell") -> None:
        self._workspace = workspace.resolve()
        self._shell = shell

    def build(
        self,
        mode: AgentMode,
        tools: Sequence[ToolDefinition[Any]],
    ) -> str:
        tool_names = ", ".join(definition.name for definition in tools) or "none"
        lines = [
            "You are YCode, a terminal coding assistant.",
            f"Workspace: {self._workspace}",
            f"Shell: {self._shell}",
            f"Available tools: {tool_names}.",
            "Use tools when needed. Read tool failures and adjust your next action.",
        ]
        if mode is AgentMode.PLAN_ONLY:
            lines.append(
                "Plan-only mode: investigate with read tools only, make no changes, "
                "and finish with an implementation plan for user approval."
            )
        else:
            lines.append("Agent mode: use the available tools to complete the user's task.")
        return "\n".join(lines)
