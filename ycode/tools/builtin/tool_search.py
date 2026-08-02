"""在本地目录中发现本任务可用的延迟工具。"""

import json
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from ycode.tools.arguments import PydanticToolArguments
from ycode.tools.contracts import ToolAccess, ToolContext, ToolDefinition, ToolExecutionResult
from ycode.tools.errors import ToolError

if TYPE_CHECKING:
    from ycode.tools.registry import ToolRegistry


class ToolSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_names: list[str] = Field(min_length=1)


class ToolSearchTool:
    definition = ToolDefinition(
        name="tool_search",
        description="从本地 MCP 工具目录加载指定工具。",
        access=ToolAccess.READ,
        arguments=PydanticToolArguments(ToolSearchArguments),
    )
    timeout_seconds = 5.0

    def __init__(self, registry: "ToolRegistry") -> None:
        self._registry = registry

    async def execute(
        self, arguments: ToolSearchArguments, context: ToolContext
    ) -> ToolExecutionResult:
        if context.exposure is None:
            raise ToolError("tool_search_context_missing", "当前任务没有工具发现上下文。")
        states = context.exposure.activate(arguments.tool_names)
        items = []
        for name, status in states.items():
            tool = self._registry.get(name) if status != "not_found" else None
            description = ""
            if tool is not None:
                description = re.sub(r"\s+", " ", tool.definition.description).strip()[:160]
            items.append({"name": name, "status": status, "description": description})
        return ToolExecutionResult(
            content=json.dumps(items, ensure_ascii=False), metadata={"tools": items}
        )
