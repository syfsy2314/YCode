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
    ContextCompactedEvent,
    FinalResponseEvent,
    ToolApprovalRequested,
    ToolExecutionCompleted,
)
from ycode.config import SecretRedactor
from ycode.context import (
    ContextArtifactStore,
    ContextManager,
    ContextPolicy,
    ConversationCompactor,
)
from ycode.core import (
    ChatMessage,
    StopReason,
    StreamEnd,
    TextDelta,
    TokenUsage,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ycode.prompt import (
    EnvironmentCollector,
    PromptRuntimeContext,
    build_builtin_prompt,
)
from ycode.security import (
    ApprovalChoice,
    CommandSafetyResult,
    PermissionEngine,
    PermissionMode,
    PermissionSession,
    SecurityConfig,
)
from ycode.tools import (
    PydanticToolArguments,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
    ToolScheduler,
)
from ycode.tools.builtin.tool_search import ToolSearchTool
from ycode.tools.paths import WorkspacePathResolver


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CountingTool:
    timeout_seconds = 1.0

    def __init__(
        self,
        name: str,
        access: ToolAccess,
        *,
        fail: bool = False,
        defer_loading: bool = False,
        result_content: str | None = None,
    ) -> None:
        self.definition = ToolDefinition(
            name=name,
            description=f"{name} 测试工具",
            access=access,
            arguments=PydanticToolArguments(NoArguments),
            defer_loading=defer_loading,
        )
        self.calls = 0
        self.fail = fail
        self.result_content = result_content

    async def execute(
        self,
        arguments: NoArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del arguments, context
        self.calls += 1
        return ToolExecutionResult(
            content=(
                self.result_content
                if self.result_content is not None
                else "tool failed"
                if self.fail
                else f"result-{self.calls}"
            ),
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
    permission_mode: PermissionMode | None = None,
    plan_only_mcp_tools: frozenset[str] = frozenset(),
    context_manager: ContextManager | None = None,
) -> AgentLoop:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    if any(tool.definition.defer_loading for tool in tools):
        registry.register(ToolSearchTool(registry))
    executor = ToolExecutor(registry)
    permission_session = PermissionSession(permission_mode) if permission_mode is not None else None

    class SafeChecker:
        async def check(self, command: str) -> CommandSafetyResult:
            return CommandSafetyResult(safe=True)

    permission_engine = None
    if permission_session is not None:
        permission_engine = PermissionEngine(
            registry,
            WorkspacePathResolver(tmp_path),
            SecurityConfig(
                mode=permission_mode,
                plan_only={"allow_mcp_tools": tuple(plan_only_mcp_tools)},
            ),
            SafeChecker(),  # type: ignore[arg-type]
        )

    async def no_git(workspace: Path) -> None:
        del workspace
        return None

    return AgentLoop(
        provider,
        registry,
        ToolScheduler(registry, executor),
        build_builtin_prompt(),
        PromptRuntimeContext(),
        EnvironmentCollector(tmp_path, git_runner=no_git),
        ToolContext(workspace=tmp_path.resolve()),
        permission_engine=permission_engine,
        permission_session=permission_session,
        plan_only_mcp_tools=plan_only_mcp_tools,
        context_manager=context_manager,
        max_rounds=max_rounds,
    )


async def consume(turn) -> list[object]:
    return [event async for event in turn]


def summary_response() -> str:
    headings = (
        "主要请求",
        "关键概念",
        "文件代码",
        "错误修复",
        "解决过程",
        "用户原话",
        "待办",
        "当前工作",
        "下一步",
    )
    body = "\n".join(f"## {heading}\n无" for heading in headings)
    return f"<analysis_draft>草稿</analysis_draft><summary>{body}</summary>"


def create_context_manager(tmp_path: Path, provider: FakeProvider) -> ContextManager:
    policy = ContextPolicy()
    store = ContextArtifactStore(
        tmp_path,
        SecretRedactor(),
        policy,
        session_id="context-test",
    )
    return ContextManager(policy, store, ConversationCompactor(provider))


def multi_tool_turn(*calls: tuple[str, str]):
    events = []
    for index, (call_id, name) in enumerate(calls):
        block = ToolCallBlock(call_id, name, {})
        events.extend(
            [
                ToolCallStart(index, call_id, name),
                ToolCallDelta(index, "{}"),
                ToolCallComplete(index, block),
            ]
        )
    events.append(StreamEnd(StopReason.TOOL_USE, StopReason.TOOL_USE.value))
    return events


@pytest.mark.asyncio
async def test_context_manager_compacts_before_main_request_and_commits(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [TextDelta(0, summary_response()), StreamEnd(StopReason.END_TURN)],
            final_turn("done"),
        ]
    )
    context_manager = create_context_manager(tmp_path, provider)
    loop = create_loop(tmp_path, provider, context_manager=context_manager)
    latest = ChatMessage.user_text("最新用户原文")
    turn = loop.start_turn(
        (ChatMessage.user_text("x" * 510_000),),
        latest,
        AgentMode.AGENT,
    )

    events = await consume(turn)

    assert any(isinstance(event, ContextCompactedEvent) for event in events)
    assert turn.result is not None
    assert turn.result.context_commit is not None
    assert turn.result.context_commit.history[0] is latest
    main_request = provider.agent_requests[1]
    assert main_request.messages[0] is latest
    assert main_request.supplements[0].startswith("<memory>")
    assert main_request.supplements[-1].startswith("<reminder>")
    await context_manager.close()


@pytest.mark.asyncio
async def test_context_manager_externalizes_tool_result_before_next_round(tmp_path: Path) -> None:
    provider = FakeProvider([tool_turn("call-1", "large_tool"), final_turn("done")])
    context_manager = create_context_manager(tmp_path, provider)
    tool = CountingTool(
        "large_tool",
        ToolAccess.READ,
        result_content="x" * 60_000,
    )
    loop = create_loop(tmp_path, provider, tool, context_manager=context_manager)

    await consume(loop.start_turn((), ChatMessage.user_text("run"), AgentMode.AGENT))

    result_block = provider.agent_requests[1].messages[-1].blocks(ToolResultBlock)[0]
    reference = json.loads(result_block.content)
    assert reference["externalized"] is True
    assert reference["tool_name"] == "large_tool"
    await context_manager.close()


@pytest.mark.asyncio
async def test_context_manager_observes_main_request_usage(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [
                TextDelta(0, "done"),
                StreamEnd(
                    StopReason.END_TURN,
                    usage=TokenUsage(input_tokens=1_000_000),
                ),
            ]
        ]
    )
    context_manager = create_context_manager(tmp_path, provider)
    loop = create_loop(tmp_path, provider, context_manager=context_manager)

    await consume(loop.start_turn((), ChatMessage.user_text("hello"), AgentMode.AGENT))

    assert context_manager.estimator.calibration_ratio > 1
    await context_manager.close()


def tool_turn_with_arguments(call_id: str, name: str, arguments: dict[str, object]):
    block = ToolCallBlock(call_id, name, arguments)
    encoded = json.dumps(arguments)
    return [
        ToolCallStart(0, call_id, name),
        ToolCallDelta(0, encoded),
        ToolCallComplete(0, block),
        StreamEnd(StopReason.TOOL_USE, StopReason.TOOL_USE.value),
    ]


def multi_tool_turn_with_arguments(
    *calls: tuple[str, str, dict[str, object]],
):
    events = []
    for index, (call_id, name, arguments) in enumerate(calls):
        block = ToolCallBlock(call_id, name, arguments)
        events.extend(
            [
                ToolCallStart(index, call_id, name),
                ToolCallDelta(index, json.dumps(arguments)),
                ToolCallComplete(index, block),
            ]
        )
    events.append(StreamEnd(StopReason.TOOL_USE, StopReason.TOOL_USE.value))
    return events


@pytest.mark.asyncio
async def test_permission_approval_blocks_before_tool_and_next_model_request(
    tmp_path: Path,
) -> None:
    tool = CountingTool("write_tool", ToolAccess.WRITE)
    provider = FakeProvider([tool_turn("call-1", "write_tool"), final_turn()])
    loop = create_loop(
        tmp_path,
        provider,
        tool,
        permission_mode=PermissionMode.DEFAULT,
    )
    turn = loop.start_turn((), ChatMessage.user_text("work"), AgentMode.AGENT)

    event = await anext(turn)

    assert isinstance(event, ToolApprovalRequested)
    assert tool.calls == 0
    assert len(provider.requests) == 1
    turn.submit_approval(ApprovalChoice.ALLOW_ONCE)
    remaining = await consume(turn)

    assert tool.calls == 1
    assert len(provider.requests) == 2
    assert isinstance(remaining[-1], FinalResponseEvent)
    assert any(
        "permission mode: default" in item for item in provider.agent_requests[0].supplements
    )


@pytest.mark.asyncio
async def test_allow_session_unblocks_identical_later_call_without_second_prompt(
    tmp_path: Path,
) -> None:
    tool = CountingTool("unknown_tool", ToolAccess.UNKNOWN)
    provider = FakeProvider(
        [
            multi_tool_turn(
                ("call-1", "unknown_tool"),
                ("call-2", "unknown_tool"),
            ),
            final_turn(),
        ]
    )
    turn = create_loop(
        tmp_path,
        provider,
        tool,
        permission_mode=PermissionMode.ALLOW,
    ).start_turn((), ChatMessage.user_text("work"), AgentMode.AGENT)

    first = await anext(turn)
    assert isinstance(first, ToolApprovalRequested)
    turn.submit_approval(ApprovalChoice.ALLOW_SESSION)
    events = await consume(turn)

    assert tool.calls == 2
    assert not any(isinstance(event, ToolApprovalRequested) for event in events)


@pytest.mark.asyncio
async def test_denied_tool_is_not_executed_and_error_is_fed_back(
    tmp_path: Path,
) -> None:
    tool = CountingTool("write_tool", ToolAccess.WRITE)
    provider = FakeProvider([tool_turn("call-1", "write_tool"), final_turn()])
    turn = create_loop(
        tmp_path,
        provider,
        tool,
        permission_mode=PermissionMode.DEFAULT,
    ).start_turn((), ChatMessage.user_text("work"), AgentMode.AGENT)

    assert isinstance(await anext(turn), ToolApprovalRequested)
    turn.submit_approval(ApprovalChoice.DENY)
    events = await consume(turn)

    assert tool.calls == 0
    completed = next(event for event in events if isinstance(event, ToolExecutionCompleted))
    assert completed.record.result.is_error
    result = provider.requests[1][-1].blocks(ToolResultBlock)[0]
    assert result.is_error
    assert json.loads(result.content)["metadata"]["code"] == "permission_denied"


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
    assert provider.agent_requests[0].supplements == provider.agent_requests[1].supplements
    second_request = provider.requests[1]
    assert second_request[-2].blocks(ToolCallBlock)[0].id == "call-1"
    result_block = second_request[-1].blocks(ToolResultBlock)[0]
    payload = json.loads(result_block.content)
    assert payload == {"content": "result-1", "metadata": {"count": 1}}
    assert not result_block.is_error


@pytest.mark.asyncio
async def test_mode_instruction_is_full_first_compact_then_full_after_change(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([final_turn("one"), final_turn("two"), final_turn("plan")])
    loop = create_loop(tmp_path, provider)

    await consume(loop.start_turn((), ChatMessage.user_text("one"), AgentMode.AGENT))
    await consume(loop.start_turn((), ChatMessage.user_text("two"), AgentMode.AGENT))
    await consume(loop.start_turn((), ChatMessage.user_text("plan"), AgentMode.PLAN_ONLY))

    assert "Current task mode: agent" in provider.agent_requests[0].supplements[-1]
    assert "Mode reminder: agent" in provider.agent_requests[1].supplements[-1]
    assert "Current task mode: plan-only" in provider.agent_requests[2].supplements[-1]


@pytest.mark.asyncio
async def test_agent_aggregates_usage_across_tool_rounds(tmp_path: Path) -> None:
    tool = CountingTool("read_tool", ToolAccess.READ)
    first = tool_turn("call-1", "read_tool")
    first[-1] = StreamEnd(
        StopReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=3),
    )
    final = final_turn("done")
    final[-1] = StreamEnd(
        StopReason.END_TURN,
        usage=TokenUsage(input_tokens=20, output_tokens=5, cache_read_input_tokens=8),
    )
    provider = FakeProvider([first, final])
    turn = create_loop(tmp_path, provider, tool).start_turn(
        (),
        ChatMessage.user_text("work"),
        AgentMode.AGENT,
    )

    await consume(turn)

    assert turn.result is not None
    assert turn.result.usage == TokenUsage(
        input_tokens=30,
        output_tokens=8,
        cache_read_input_tokens=8,
    )


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

    request = provider.agent_requests[0]
    assert [definition.name for definition in request.tools] == ["read_tool"]
    assert any("Current task mode: plan-only" in item for item in request.supplements)
    assert write_tool.calls == 0
    blocked = provider.requests[1][-1].blocks(ToolResultBlock)[0]
    assert blocked.is_error
    assert "access_denied" in blocked.content


@pytest.mark.asyncio
async def test_deferred_tool_appears_on_next_round_after_search(tmp_path: Path) -> None:
    tool = CountingTool("mcp_demo_echo", ToolAccess.UNKNOWN, defer_loading=True)
    provider = FakeProvider(
        [
            tool_turn_with_arguments("search-1", "tool_search", {"tool_names": ["mcp_demo_echo"]}),
            tool_turn("call-1", "mcp_demo_echo"),
            final_turn(),
        ]
    )
    loop = create_loop(tmp_path, provider, tool)

    await consume(loop.start_turn((), ChatMessage.user_text("use MCP"), AgentMode.AGENT))

    first_names = [item.name for item in provider.agent_requests[0].tools]
    second_names = [item.name for item in provider.agent_requests[1].tools]
    assert first_names == ["tool_search"]
    assert second_names == ["mcp_demo_echo", "tool_search"]
    assert tool.calls == 1
    assert provider.agent_requests[0].supplements == provider.agent_requests[1].supplements


@pytest.mark.asyncio
@pytest.mark.parametrize("search_first", [True, False])
async def test_same_batch_hidden_tool_is_not_discovered(tmp_path: Path, search_first: bool) -> None:
    tool = CountingTool("mcp_demo_echo", ToolAccess.UNKNOWN, defer_loading=True)
    search = ("search-1", "tool_search", {"tool_names": ["mcp_demo_echo"]})
    hidden = ("call-1", "mcp_demo_echo", {})
    calls = (search, hidden) if search_first else (hidden, search)
    provider = FakeProvider([multi_tool_turn_with_arguments(*calls), final_turn()])

    await consume(
        create_loop(tmp_path, provider, tool).start_turn(
            (), ChatMessage.user_text("use MCP"), AgentMode.AGENT
        )
    )

    assert tool.calls == 0
    results = provider.requests[1][-1].blocks(ToolResultBlock)
    hidden_result = next(item for item in results if item.tool_call_id == "call-1")
    assert json.loads(hidden_result.content)["metadata"]["code"] == "tool_not_discovered"
    assert "mcp_demo_echo" in [item.name for item in provider.agent_requests[1].tools]


@pytest.mark.asyncio
async def test_deferred_exposure_resets_between_tasks(tmp_path: Path) -> None:
    tool = CountingTool("mcp_demo_echo", ToolAccess.UNKNOWN, defer_loading=True)
    provider = FakeProvider(
        [
            tool_turn_with_arguments("search-1", "tool_search", {"tool_names": ["mcp_demo_echo"]}),
            final_turn("first"),
            final_turn("second"),
        ]
    )
    loop = create_loop(tmp_path, provider, tool)

    await consume(loop.start_turn((), ChatMessage.user_text("first"), AgentMode.AGENT))
    await consume(loop.start_turn((), ChatMessage.user_text("second"), AgentMode.AGENT))

    assert "mcp_demo_echo" in [item.name for item in provider.agent_requests[1].tools]
    assert "mcp_demo_echo" not in [item.name for item in provider.agent_requests[2].tools]


@pytest.mark.asyncio
async def test_plan_only_mcp_catalog_uses_whitelist(tmp_path: Path) -> None:
    allowed = CountingTool("mcp_demo_allowed", ToolAccess.UNKNOWN, defer_loading=True)
    hidden = CountingTool("mcp_demo_hidden", ToolAccess.UNKNOWN, defer_loading=True)
    empty_provider = FakeProvider([final_turn()])

    await consume(
        create_loop(tmp_path, empty_provider, allowed, hidden).start_turn(
            (), ChatMessage.user_text("plan"), AgentMode.PLAN_ONLY
        )
    )

    assert [item.name for item in empty_provider.agent_requests[0].tools] == []
    assert not any(
        "mcp_demo_" in supplement for supplement in empty_provider.agent_requests[0].supplements
    )

    provider = FakeProvider(
        [
            tool_turn_with_arguments(
                "search-1", "tool_search", {"tool_names": ["mcp_demo_allowed"]}
            ),
            final_turn(),
        ]
    )
    await consume(
        create_loop(
            tmp_path,
            provider,
            allowed,
            hidden,
            plan_only_mcp_tools=frozenset({"mcp_demo_allowed"}),
        ).start_turn((), ChatMessage.user_text("plan"), AgentMode.PLAN_ONLY)
    )

    assert [item.name for item in provider.agent_requests[0].tools] == ["tool_search"]
    assert "mcp_demo_allowed" in provider.agent_requests[0].supplements[1]
    assert "mcp_demo_hidden" not in provider.agent_requests[0].supplements[1]
    assert [item.name for item in provider.agent_requests[1].tools] == [
        "mcp_demo_allowed",
        "tool_search",
    ]


@pytest.mark.asyncio
async def test_plan_only_whitelisted_mcp_executes_after_one_time_approval(
    tmp_path: Path,
) -> None:
    tool = CountingTool("mcp_demo_allowed", ToolAccess.UNKNOWN, defer_loading=True)
    provider = FakeProvider(
        [
            tool_turn_with_arguments("search", "tool_search", {"tool_names": ["mcp_demo_allowed"]}),
            tool_turn("remote", "mcp_demo_allowed"),
            final_turn(),
        ]
    )
    turn = create_loop(
        tmp_path,
        provider,
        tool,
        permission_mode=PermissionMode.ALLOW,
        plan_only_mcp_tools=frozenset({"mcp_demo_allowed"}),
    ).start_turn((), ChatMessage.user_text("plan"), AgentMode.PLAN_ONLY)

    events = []
    async for event in turn:
        events.append(event)
        if isinstance(event, ToolApprovalRequested):
            assert event.decision.allow_session is False
            turn.submit_approval(ApprovalChoice.ALLOW_ONCE)

    assert sum(isinstance(event, ToolApprovalRequested) for event in events) == 1
    assert tool.calls == 1


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
