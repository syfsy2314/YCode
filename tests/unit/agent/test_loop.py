import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from tests.support.fake_provider import FakeProvider
from ycode.agent import (
    AgentCancelledEvent,
    AgentErrorEvent,
    AgentLimitReachedEvent,
    AgentLoop,
    AgentMode,
    AgentTermination,
    AgentTextDelta,
    FinalResponseEvent,
    SystemPromptBuilder,
    ToolExecutionCompleted,
)
from ycode.core import (
    ChatMessage,
    StopReason,
    StreamEnd,
    TextDelta,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ycode.tools import (
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
    ToolScheduler,
)


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CountingTool:
    timeout_seconds = 1.0

    def __init__(self, name: str, access: ToolAccess, *, fail: bool = False) -> None:
        self.definition = ToolDefinition(
            name=name,
            description=f"{name} 测试工具",
            access=access,
            arguments_model=NoArguments,
        )
        self.calls = 0
        self.fail = fail

    async def execute(
        self,
        arguments: NoArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del arguments, context
        self.calls += 1
        return ToolExecutionResult(
            content="tool failed" if self.fail else f"result-{self.calls}",
            is_error=self.fail,
            metadata={"count": self.calls},
        )


def final_turn(text: str = "done", reason: StopReason = StopReason.END_TURN):
    return [TextDelta(0, text), StreamEnd(reason, reason.value)]


def tool_turn(
    call_id: str,
    name: str,
    reason: StopReason = StopReason.TOOL_USE,
):
    block = ToolCallBlock(call_id, name, {})
    return [
        ToolCallStart(0, call_id, name),
        ToolCallDelta(0, "{}"),
        ToolCallComplete(0, block),
        StreamEnd(reason, reason.value),
    ]


def create_loop(
    tmp_path: Path,
    provider: FakeProvider,
    *tools: CountingTool,
    max_rounds: int = 10,
) -> AgentLoop:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    executor = ToolExecutor(registry)
    return AgentLoop(
        provider,
        registry,
        ToolScheduler(registry, executor),
        SystemPromptBuilder(tmp_path),
        ToolContext(workspace=tmp_path.resolve()),
        max_rounds=max_rounds,
    )


async def consume(turn) -> list[object]:
    return [event async for event in turn]


@pytest.mark.asyncio
async def test_first_round_without_tool_call_is_final_response(tmp_path: Path) -> None:
    provider = FakeProvider([final_turn("answer")])
    loop = create_loop(tmp_path, provider)
    user = ChatMessage.user_text("question")
    turn = loop.start_turn((), user, AgentMode.AGENT)

    events = await consume(turn)

    assert events[0] == AgentTextDelta(1, 0, "answer")
    assert isinstance(events[-1], FinalResponseEvent)
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.COMPLETED
    assert turn.result.messages == (user, ChatMessage.assistant_text("answer"))
    assert provider.requests == [(user,)]


@pytest.mark.asyncio
async def test_tool_result_is_fed_back_before_final_round(tmp_path: Path) -> None:
    tool = CountingTool("read_tool", ToolAccess.READ)
    provider = FakeProvider([tool_turn("call-1", "read_tool"), final_turn()])
    loop = create_loop(tmp_path, provider, tool)
    turn = loop.start_turn((), ChatMessage.user_text("work"), AgentMode.AGENT)

    events = await consume(turn)

    assert tool.calls == 1
    assert any(isinstance(event, ToolExecutionCompleted) for event in events)
    assert isinstance(events[-1], FinalResponseEvent)
    assert len(provider.requests) == 2
    second_request = provider.requests[1]
    assert second_request[-2].blocks(ToolCallBlock)[0].id == "call-1"
    result_block = second_request[-1].blocks(ToolResultBlock)[0]
    payload = json.loads(result_block.content)
    assert payload == {"content": "result-1", "metadata": {"count": 1}}
    assert not result_block.is_error


@pytest.mark.asyncio
async def test_tool_failure_is_fed_back_and_loop_continues(tmp_path: Path) -> None:
    tool = CountingTool("read_tool", ToolAccess.READ, fail=True)
    provider = FakeProvider([tool_turn("call-1", "read_tool"), final_turn("adjusted")])
    turn = create_loop(tmp_path, provider, tool).start_turn(
        (),
        ChatMessage.user_text("work"),
        AgentMode.AGENT,
    )

    events = await consume(turn)

    assert isinstance(events[-1], FinalResponseEvent)
    result = provider.requests[1][-1].blocks(ToolResultBlock)[0]
    assert result.is_error
    assert "tool failed" in result.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "code"),
    [
        (tool_turn("call-1", "read_tool", StopReason.END_TURN), "inconsistent_response"),
        ([TextDelta(0, "text"), StreamEnd(StopReason.TOOL_USE)], "missing_tool_calls"),
        (final_turn("partial", StopReason.MAX_TOKENS), "unexpected_stop_reason"),
        (final_turn("filtered", StopReason.CONTENT_FILTER), "unexpected_stop_reason"),
        (final_turn("unknown", StopReason.UNKNOWN), "unexpected_stop_reason"),
    ],
)
async def test_inconsistent_or_abnormal_model_stop_is_error(
    tmp_path: Path,
    events: list[object],
    code: str,
) -> None:
    tool = CountingTool("read_tool", ToolAccess.READ)
    provider = FakeProvider([events])  # type: ignore[list-item]
    turn = create_loop(tmp_path, provider, tool).start_turn(
        (),
        ChatMessage.user_text("work"),
        AgentMode.AGENT,
    )

    result_events = await consume(turn)

    assert isinstance(result_events[-1], AgentErrorEvent)
    assert result_events[-1].code == code
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.ERROR


@pytest.mark.asyncio
async def test_max_rounds_executes_last_tool_then_stops(tmp_path: Path) -> None:
    tool = CountingTool("read_tool", ToolAccess.READ)
    provider = FakeProvider(
        [
            tool_turn("call-1", "read_tool"),
            tool_turn("call-2", "read_tool"),
        ]
    )
    turn = create_loop(tmp_path, provider, tool, max_rounds=2).start_turn(
        (),
        ChatMessage.user_text("work"),
        AgentMode.AGENT,
    )

    events = await consume(turn)

    assert tool.calls == 2
    assert len(provider.requests) == 2
    assert isinstance(events[-1], AgentLimitReachedEvent)
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.LIMIT_REACHED


@pytest.mark.asyncio
async def test_plan_only_advertises_reads_and_blocks_forged_write(tmp_path: Path) -> None:
    read_tool = CountingTool("read_tool", ToolAccess.READ)
    write_tool = CountingTool("write_tool", ToolAccess.WRITE)
    provider = FakeProvider([tool_turn("call-1", "write_tool"), final_turn("plan")])
    turn = create_loop(tmp_path, provider, read_tool, write_tool).start_turn(
        (),
        ChatMessage.user_text("plan"),
        AgentMode.PLAN_ONLY,
    )

    await consume(turn)

    assert [definition.name for definition in provider.tool_definitions[0]] == ["read_tool"]
    assert "Plan-only mode" in provider.system_prompts[0]
    assert write_tool.calls == 0
    blocked = provider.requests[1][-1].blocks(ToolResultBlock)[0]
    assert blocked.is_error
    assert "access_denied" in blocked.content


@pytest.mark.asyncio
async def test_user_cancel_stops_provider_and_produces_cancel_event(tmp_path: Path) -> None:
    provider = FakeProvider([final_turn()], delay=10)
    turn = create_loop(tmp_path, provider).start_turn(
        (),
        ChatMessage.user_text("wait"),
        AgentMode.AGENT,
    )
    next_event = asyncio.create_task(anext(turn))
    await asyncio.sleep(0)

    turn.cancel()

    assert isinstance(await next_event, AgentCancelledEvent)
    with pytest.raises(StopAsyncIteration):
        await anext(turn)
    assert turn.result is not None
    assert turn.result.termination is AgentTermination.CANCELLED
