import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from tests.support.fake_provider import FakeProvider
from ycode.agent import ContextCompactedEvent
from ycode.agent.loop import AgentLoop
from ycode.config import SecretRedactor
from ycode.context import (
    ContextArtifactStore,
    ContextManager,
    ContextPolicy,
    ConversationCompactor,
)
from ycode.core import (
    StopReason,
    StreamEnd,
    TextDelta,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ycode.prompt import EnvironmentCollector, PromptRuntimeContext, build_builtin_prompt
from ycode.session import ChatSession
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


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LargeTool:
    definition = ToolDefinition(
        name="large_tool",
        description="返回大型结果",
        access=ToolAccess.READ,
        arguments=PydanticToolArguments(NoArguments),
    )
    timeout_seconds = 1.0

    async def execute(
        self,
        arguments: NoArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult("result:" + "z" * 60_000)


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


def tool_turn() -> list[object]:
    call = ToolCallBlock("call-large", "large_tool", {})
    return [
        ToolCallStart(0, call.id, call.name),
        ToolCallDelta(0, "{}"),
        ToolCallComplete(0, call),
        StreamEnd(StopReason.TOOL_USE),
    ]


@pytest.mark.asyncio
async def test_large_result_auto_compaction_and_followup_request(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            [TextDelta(0, "x" * 510_000), StreamEnd(StopReason.END_TURN)],
            [TextDelta(0, summary_response()), StreamEnd(StopReason.END_TURN)],
            tool_turn(),
            [TextDelta(0, "final answer"), StreamEnd(StopReason.END_TURN)],
        ]
    )
    registry = ToolRegistry()
    registry.register(LargeTool())
    policy = ContextPolicy()
    store = ContextArtifactStore(
        tmp_path,
        SecretRedactor(),
        policy,
        session_id="integration-context",
    )
    manager = ContextManager(policy, store, ConversationCompactor(provider))

    async def no_git(workspace: Path) -> None:
        del workspace

    loop = AgentLoop(
        provider,
        registry,
        ToolScheduler(registry, ToolExecutor(registry)),
        build_builtin_prompt(),
        PromptRuntimeContext(),
        EnvironmentCollector(tmp_path, git_runner=no_git),
        ToolContext(tmp_path),
        context_manager=manager,
    )
    session = ChatSession(loop, context_manager=manager)

    async def collect(text: str) -> list[object]:
        return [event async for event in session.stream_reply(text)]

    await collect("first")
    events = await collect("second")

    assert any(isinstance(event, ContextCompactedEvent) for event in events)
    summary_request = provider.agent_requests[1]
    assert summary_request.tools == ()
    assert summary_request.thinking_enabled is False
    assert summary_request.max_output_tokens == 20_000

    result_block = provider.agent_requests[3].messages[-1].blocks(ToolResultBlock)[0]
    reference = json.loads(result_block.content)
    assert reference["externalized"] is True
    manifest_path = tmp_path / reference["manifest_path"]
    assert manifest_path.is_file()
    assert session.history[-1].text == "final answer"

    await session.close()
    assert not manifest_path.exists()
