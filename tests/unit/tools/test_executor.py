import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from ycode.core.messages import ToolCallBlock
from ycode.tools import (
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolError,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
)


class FakeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1)
    count: int = Field(default=2, ge=1, le=3)


class FakeTool:
    definition = ToolDefinition(
        name="fake_tool",
        description="测试统一执行器",
        access=ToolAccess.READ,
        arguments_model=FakeArguments,
    )
    timeout_seconds = 1.0

    def __init__(self) -> None:
        self.arguments: FakeArguments | None = None

    async def execute(
        self,
        arguments: FakeArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        self.arguments = arguments
        return ToolExecutionResult(
            content=arguments.value * arguments.count,
            metadata={"workspace": context.workspace.name},
        )


class ErrorTool(FakeTool):
    async def execute(
        self,
        arguments: FakeArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del arguments, context
        raise ToolError("expected_failure", "可重试失败", metadata={"retry": True})


class CrashTool(FakeTool):
    async def execute(
        self,
        arguments: FakeArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del arguments, context
        raise RuntimeError("secret traceback data")


class SlowTool(FakeTool):
    timeout_seconds = 0.01

    def __init__(self) -> None:
        super().__init__()
        self.cleaned = asyncio.Event()

    async def execute(
        self,
        arguments: FakeArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del arguments, context
        try:
            await asyncio.sleep(10)
        finally:
            self.cleaned.set()
        return ToolExecutionResult(content="never")


def executor_with(tool: FakeTool) -> ToolExecutor:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolExecutor(registry)


def context(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path.resolve())


@pytest.mark.asyncio
async def test_executor_validates_arguments_and_applies_defaults(tmp_path: Path) -> None:
    tool = FakeTool()
    result = await executor_with(tool).execute(
        ToolCallBlock(id="1", name="fake_tool", arguments={"value": "ok"}),
        context(tmp_path),
        frozenset({ToolAccess.READ}),
    )

    assert result.content == "okok"
    assert not result.is_error
    assert tool.arguments == FakeArguments(value="ok", count=2)


@pytest.mark.asyncio
async def test_executor_returns_unknown_and_access_denied_without_execution(
    tmp_path: Path,
) -> None:
    unknown = await ToolExecutor(ToolRegistry()).execute(
        ToolCallBlock(id="1", name="missing", arguments={}),
        context(tmp_path),
        frozenset({ToolAccess.READ}),
    )
    denied_tool = FakeTool()
    denied = await executor_with(denied_tool).execute(
        ToolCallBlock(id="2", name="fake_tool", arguments={"value": "ok"}),
        context(tmp_path),
        frozenset({ToolAccess.WRITE}),
    )

    assert unknown.metadata["error_code"] == "unknown_tool"
    assert denied.metadata["error_code"] == "access_denied"
    assert denied_tool.arguments is None


@pytest.mark.asyncio
async def test_executor_returns_safe_validation_details(tmp_path: Path) -> None:
    result = await executor_with(FakeTool()).execute(
        ToolCallBlock(
            id="1",
            name="fake_tool",
            arguments={"value": "sensitive", "count": 9, "extra": "secret"},
        ),
        context(tmp_path),
        frozenset({ToolAccess.READ}),
    )

    assert result.metadata["error_code"] == "invalid_arguments"
    assert "sensitive" not in result.content
    assert "secret" not in repr(result.metadata)
    fields = {
        detail["field"]  # type: ignore[index]
        for detail in result.metadata["details"]  # type: ignore[union-attr]
    }
    assert fields == {"count", "extra"}


@pytest.mark.asyncio
async def test_executor_converts_controlled_and_unexpected_errors(tmp_path: Path) -> None:
    controlled = await executor_with(ErrorTool()).execute(
        ToolCallBlock(id="1", name="fake_tool", arguments={"value": "ok"}),
        context(tmp_path),
        frozenset({ToolAccess.READ}),
    )
    crashed = await executor_with(CrashTool()).execute(
        ToolCallBlock(id="2", name="fake_tool", arguments={"value": "ok"}),
        context(tmp_path),
        frozenset({ToolAccess.READ}),
    )

    assert controlled.metadata["error_code"] == "expected_failure"
    assert controlled.metadata["retry"] is True
    assert crashed.metadata["error_code"] == "internal_error"
    assert "secret" not in crashed.content


@pytest.mark.asyncio
async def test_executor_timeout_waits_for_tool_cleanup(tmp_path: Path) -> None:
    tool = SlowTool()

    result = await executor_with(tool).execute(
        ToolCallBlock(id="1", name="fake_tool", arguments={"value": "ok"}),
        context(tmp_path),
        frozenset({ToolAccess.READ}),
    )

    assert result.metadata["error_code"] == "timeout"
    assert result.metadata["timeout_seconds"] == 0.01
    assert tool.cleaned.is_set()


@pytest.mark.asyncio
async def test_executor_propagates_external_cancellation(tmp_path: Path) -> None:
    tool = SlowTool()
    tool.timeout_seconds = 10
    task = asyncio.create_task(
        executor_with(tool).execute(
            ToolCallBlock(id="1", name="fake_tool", arguments={"value": "ok"}),
            context(tmp_path),
            frozenset({ToolAccess.READ}),
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert tool.cleaned.is_set()
