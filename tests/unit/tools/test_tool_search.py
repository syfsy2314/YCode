import json
from pathlib import Path

import pytest

from ycode.tools import (
    JsonSchemaToolArguments,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRegistry,
)
from ycode.tools.builtin.tool_search import ToolSearchArguments, ToolSearchTool
from ycode.tools.errors import ToolError
from ycode.tools.exposure import ToolExposureSession


class DeferredTool:
    timeout_seconds = 1.0
    definition = ToolDefinition(
        name="mcp_demo_echo",
        description="  A   compact\n description " + "x" * 200,
        access=ToolAccess.UNKNOWN,
        arguments=JsonSchemaToolArguments(
            {
                "type": "object",
                "properties": {"secret_schema_field": {"type": "string"}},
            }
        ),
        defer_loading=True,
    )

    async def execute(self, arguments: object, context: ToolContext) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult("unused")


@pytest.mark.asyncio
async def test_tool_search_is_local_bounded_and_does_not_return_schema() -> None:
    registry = ToolRegistry()
    registry.register(DeferredTool())
    search = ToolSearchTool(registry)
    exposure = ToolExposureSession(frozenset({"mcp_demo_echo"}))
    context = ToolContext(Path.cwd(), exposure)

    loaded = await search.execute(
        ToolSearchArguments(tool_names=["missing", "mcp_demo_echo"]), context
    )
    repeated = await search.execute(ToolSearchArguments(tool_names=["mcp_demo_echo"]), context)

    items = json.loads(loaded.content)
    assert [item["name"] for item in items] == ["mcp_demo_echo", "missing"]
    assert items[0]["status"] == "loaded"
    assert len(items[0]["description"]) == 160
    assert items[1] == {"name": "missing", "status": "not_found", "description": ""}
    assert "secret_schema_field" not in loaded.content
    assert json.loads(repeated.content)[0]["status"] == "already_loaded"


@pytest.mark.asyncio
async def test_tool_search_hides_registered_but_unsearchable_name() -> None:
    registry = ToolRegistry()
    registry.register(DeferredTool())

    result = await ToolSearchTool(registry).execute(
        ToolSearchArguments(tool_names=["mcp_demo_echo"]),
        ToolContext(Path.cwd(), ToolExposureSession(frozenset())),
    )

    assert json.loads(result.content) == [
        {"name": "mcp_demo_echo", "status": "not_found", "description": ""}
    ]


@pytest.mark.asyncio
async def test_tool_search_requires_task_context() -> None:
    with pytest.raises(ToolError) as caught:
        await ToolSearchTool(ToolRegistry()).execute(
            ToolSearchArguments(tool_names=["anything"]), ToolContext(Path.cwd())
        )

    assert caught.value.code == "tool_search_context_missing"
