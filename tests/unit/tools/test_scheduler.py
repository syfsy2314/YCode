import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from ycode.core.messages import ToolCallBlock
from ycode.tools import (
    ScheduledToolCancelled,
    ScheduledToolCompleted,
    ScheduledToolStarted,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRegistry,
    ToolScheduler,
)


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClassifiedTool:
    timeout_seconds = 30.0

    def __init__(self, name: str, access: ToolAccess) -> None:
        self.definition = ToolDefinition(
            name=name,
            description=f"{name} 测试工具",
            access=access,
            arguments_model=NoArguments,
        )

    async def execute(
        self,
        arguments: NoArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(content="unused")


class ControlledExecutor:
    def __init__(self) -> None:
        self.started: asyncio.Queue[str] = asyncio.Queue()
        self.releases: dict[str, asyncio.Event] = {}
        self.running: set[str] = set()
        self.max_running = 0
        self.cancelled: set[str] = set()

    def add(self, call_id: str) -> None:
        self.releases[call_id] = asyncio.Event()

    async def execute(
        self,
        call: ToolCallBlock,
        context: ToolContext,
        allowed_access: frozenset[ToolAccess],
    ) -> ToolExecutionResult:
        del context, allowed_access
        self.running.add(call.id)
        self.max_running = max(self.max_running, len(self.running))
        await self.started.put(call.id)
        try:
            await self.releases[call.id].wait()
        except asyncio.CancelledError:
            self.cancelled.add(call.id)
            raise
        finally:
            self.running.discard(call.id)
        return ToolExecutionResult(content=call.id)


def create_scheduler(
    executor: ControlledExecutor,
) -> ToolScheduler:
    registry = ToolRegistry()
    registry.register(ClassifiedTool("read_tool", ToolAccess.READ))
    registry.register(ClassifiedTool("write_tool", ToolAccess.WRITE))
    return ToolScheduler(registry, executor)  # type: ignore[arg-type]


def calls(*names: str) -> list[ToolCallBlock]:
    return [
        ToolCallBlock(id=f"call-{index}", name=name, arguments={})
        for index, name in enumerate(names)
    ]


def context(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path.resolve())


@pytest.mark.asyncio
async def test_consecutive_reads_run_concurrently_and_complete_in_actual_order(
    tmp_path: Path,
) -> None:
    executor = ControlledExecutor()
    executor.add("call-0")
    executor.add("call-1")
    scheduler = create_scheduler(executor)
    events = []

    async def consume() -> None:
        async for event in scheduler.stream(
            calls("read_tool", "read_tool"),
            context(tmp_path),
            frozenset({ToolAccess.READ}),
        ):
            events.append(event)

    task = asyncio.create_task(consume())
    assert await executor.started.get() == "call-0"
    assert await executor.started.get() == "call-1"
    executor.releases["call-1"].set()
    await asyncio.sleep(0)
    executor.releases["call-0"].set()
    await task

    assert executor.max_running == 2
    assert [event.position for event in events if isinstance(event, ScheduledToolStarted)] == [
        0,
        1,
    ]
    assert [
        event.record.position for event in events if isinstance(event, ScheduledToolCompleted)
    ] == [1, 0]


@pytest.mark.asyncio
async def test_write_is_a_barrier_for_reads_before_and_after(tmp_path: Path) -> None:
    executor = ControlledExecutor()
    for call_id in ("call-0", "call-1", "call-2"):
        executor.add(call_id)
    scheduler = create_scheduler(executor)

    async def consume() -> None:
        async for _ in scheduler.stream(
            calls("read_tool", "write_tool", "read_tool"),
            context(tmp_path),
            frozenset({ToolAccess.READ, ToolAccess.WRITE}),
        ):
            pass

    task = asyncio.create_task(consume())
    assert await executor.started.get() == "call-0"
    assert executor.started.empty()
    executor.releases["call-0"].set()

    assert await executor.started.get() == "call-1"
    assert executor.started.empty()
    executor.releases["call-1"].set()

    assert await executor.started.get() == "call-2"
    executor.releases["call-2"].set()
    await task
    assert executor.max_running == 1


@pytest.mark.asyncio
async def test_multiple_writes_are_serial(tmp_path: Path) -> None:
    executor = ControlledExecutor()
    executor.add("call-0")
    executor.add("call-1")
    scheduler = create_scheduler(executor)

    async def consume() -> None:
        async for _ in scheduler.stream(
            calls("write_tool", "write_tool"),
            context(tmp_path),
            frozenset({ToolAccess.WRITE}),
        ):
            pass

    task = asyncio.create_task(consume())
    assert await executor.started.get() == "call-0"
    executor.releases["call-0"].set()
    assert await executor.started.get() == "call-1"
    executor.releases["call-1"].set()
    await task
    assert executor.max_running == 1


@pytest.mark.asyncio
async def test_cancellation_reports_started_reads_and_starts_nothing_else(
    tmp_path: Path,
) -> None:
    executor = ControlledExecutor()
    for call_id in ("call-0", "call-1", "call-2"):
        executor.add(call_id)
    scheduler = create_scheduler(executor)
    events = []

    async def consume() -> None:
        async for event in scheduler.stream(
            calls("read_tool", "read_tool", "write_tool"),
            context(tmp_path),
            frozenset({ToolAccess.READ, ToolAccess.WRITE}),
        ):
            events.append(event)

    task = asyncio.create_task(consume())
    assert await executor.started.get() == "call-0"
    assert await executor.started.get() == "call-1"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert executor.cancelled == {"call-0", "call-1"}
    assert executor.started.empty()
    assert [event.position for event in events if isinstance(event, ScheduledToolCancelled)] == [
        0,
        1,
    ]
