from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from ycode.core.messages import ToolCallBlock, thaw_json
from ycode.tools import (
    Tool,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolError,
    ToolExecutionRecord,
    ToolExecutionResult,
)


class ExampleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=20)


EXAMPLE_DEFINITION = ToolDefinition(
    name="example_tool",
    description="用于测试的工具",
    access=ToolAccess.READ,
    arguments_model=ExampleArguments,
)


class ExampleTool:
    definition = EXAMPLE_DEFINITION
    timeout_seconds = 30.0

    async def execute(
        self,
        arguments: ExampleArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=f"{context.workspace}:{arguments.path}",
            metadata={"limit": arguments.limit},
        )


def test_definition_generates_frozen_schema_from_pydantic_model() -> None:
    schema = EXAMPLE_DEFINITION.input_schema

    assert schema["type"] == "object"
    assert thaw_json(schema)["properties"]["limit"]["maximum"] == 20
    with pytest.raises(TypeError):
        schema["type"] = "array"  # type: ignore[index]


@pytest.mark.parametrize("name", ["", "ReadFile", "read-file", "_read_file", "read file"])
def test_definition_rejects_non_snake_case_name(name: str) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        ToolDefinition(
            name=name,
            description="描述",
            access=ToolAccess.READ,
            arguments_model=ExampleArguments,
        )


def test_definition_rejects_invalid_metadata() -> None:
    with pytest.raises(ValueError, match="描述"):
        ToolDefinition(
            name="empty_description",
            description=" ",
            access=ToolAccess.READ,
            arguments_model=ExampleArguments,
        )

    with pytest.raises(TypeError, match="BaseModel"):
        ToolDefinition(
            name="invalid_model",
            description="描述",
            access=ToolAccess.READ,
            arguments_model=dict,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_plain_class_structurally_satisfies_tool_protocol(tmp_path: Path) -> None:
    tool = ExampleTool()

    assert isinstance(tool, Tool)
    result = await tool.execute(
        ExampleArguments(path="file.txt"),
        ToolContext(workspace=tmp_path),
    )

    assert not result.is_error
    assert result.metadata["limit"] == 10


def test_result_recursively_freezes_metadata_without_aliasing() -> None:
    source = {"items": [{"name": "first"}]}
    result = ToolExecutionResult(content="", metadata=source)
    source["items"][0]["name"] = "changed"

    assert result.metadata["items"][0]["name"] == "first"  # type: ignore[index]
    with pytest.raises(TypeError):
        result.metadata["new"] = True  # type: ignore[index]


def test_execution_record_validates_position_call_result_and_elapsed() -> None:
    call = ToolCallBlock(id="call-1", name="example_tool", arguments={"path": "a.txt"})
    result = ToolExecutionResult(content="ok")
    record = ToolExecutionRecord(
        position=0,
        call=call,
        result=result,
        elapsed_seconds=0.25,
    )

    assert record.position == 0
    assert record.call is call
    assert record.result is result

    with pytest.raises(ValueError, match="位置"):
        ToolExecutionRecord(
            position=-1,
            call=call,
            result=result,
            elapsed_seconds=0,
        )
    with pytest.raises(ValueError, match="耗时"):
        ToolExecutionRecord(
            position=0,
            call=call,
            result=result,
            elapsed_seconds=-0.1,
        )


def test_tool_error_keeps_safe_frozen_fields() -> None:
    source = {"path": "file.txt"}
    error = ToolError("not_found", "文件不存在", metadata=source)
    source["path"] = "changed"

    assert str(error) == "文件不存在"
    assert error.code == "not_found"
    assert error.message == "文件不存在"
    assert error.metadata["path"] == "file.txt"
    with pytest.raises(TypeError):
        error.metadata["path"] = "other"  # type: ignore[index]
