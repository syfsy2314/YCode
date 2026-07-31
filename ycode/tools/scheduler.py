"""连续读取并发、写入屏障串行的工具调度。"""

import asyncio
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass

from ycode.core.messages import ToolCallBlock
from ycode.tools.contracts import (
    ToolAccess,
    ToolContext,
    ToolExecutionRecord,
    ToolExecutionResult,
)
from ycode.tools.executor import ToolExecutor
from ycode.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class ScheduledToolStarted:
    position: int
    call: ToolCallBlock


@dataclass(frozen=True, slots=True)
class ScheduledToolCompleted:
    record: ToolExecutionRecord


@dataclass(frozen=True, slots=True)
class ScheduledToolCancelled:
    position: int
    call: ToolCallBlock


type ScheduledToolEvent = ScheduledToolStarted | ScheduledToolCompleted | ScheduledToolCancelled


class ToolScheduler:
    def __init__(self, registry: ToolRegistry, executor: ToolExecutor) -> None:
        self._registry = registry
        self._executor = executor

    async def stream(
        self,
        calls: Sequence[ToolCallBlock],
        context: ToolContext,
        allowed_access: frozenset[ToolAccess],
        denied_results: Mapping[int, ToolExecutionResult] | None = None,
    ) -> AsyncIterator[ScheduledToolEvent]:
        denials = denied_results or {}
        position = 0
        while position < len(calls):
            if self._is_read(calls[position]):
                end = position + 1
                while end < len(calls) and self._is_read(calls[end]):
                    end += 1
                async for event in self._read_batch(
                    calls,
                    position,
                    end,
                    context,
                    allowed_access,
                    denials,
                ):
                    yield event
                position = end
                continue

            call = calls[position]
            yield ScheduledToolStarted(position, call)
            try:
                record = await self._execute_one(
                    position,
                    call,
                    context,
                    allowed_access,
                    denials,
                )
            except asyncio.CancelledError:
                yield ScheduledToolCancelled(position, call)
                raise
            yield ScheduledToolCompleted(record)
            position += 1

    def _is_read(self, call: ToolCallBlock) -> bool:
        tool = self._registry.get(call.name)
        return tool is not None and tool.definition.access is ToolAccess.READ

    async def _read_batch(
        self,
        calls: Sequence[ToolCallBlock],
        start: int,
        end: int,
        context: ToolContext,
        allowed_access: frozenset[ToolAccess],
        denied_results: Mapping[int, ToolExecutionResult],
    ) -> AsyncIterator[ScheduledToolEvent]:
        completion_queue: asyncio.Queue[asyncio.Task[ToolExecutionRecord]] = asyncio.Queue()
        tasks: dict[asyncio.Task[ToolExecutionRecord], tuple[int, ToolCallBlock]] = {}
        for position in range(start, end):
            call = calls[position]
            task = asyncio.create_task(
                self._execute_one(
                    position,
                    call,
                    context,
                    allowed_access,
                    denied_results,
                )
            )
            tasks[task] = (position, call)
            task.add_done_callback(completion_queue.put_nowait)
            yield ScheduledToolStarted(position, call)

        completed: set[asyncio.Task[ToolExecutionRecord]] = set()
        try:
            for _ in tasks:
                task = await completion_queue.get()
                record = await task
                completed.add(task)
                yield ScheduledToolCompleted(record)
        except asyncio.CancelledError:
            pending = [task for task in tasks if task not in completed]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in pending:
                position, call = tasks[task]
                yield ScheduledToolCancelled(position, call)
            raise

    async def _execute_one(
        self,
        position: int,
        call: ToolCallBlock,
        context: ToolContext,
        allowed_access: frozenset[ToolAccess],
        denied_results: Mapping[int, ToolExecutionResult],
    ) -> ToolExecutionRecord:
        denied = denied_results.get(position)
        if denied is not None:
            return ToolExecutionRecord(
                position=position,
                call=call,
                result=denied,
                elapsed_seconds=0,
            )
        started_at = time.perf_counter()
        result = await self._executor.execute(call, context, allowed_access)
        return ToolExecutionRecord(
            position=position,
            call=call,
            result=result,
            elapsed_seconds=time.perf_counter() - started_at,
        )
