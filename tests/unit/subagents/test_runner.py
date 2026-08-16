import asyncio
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, SecretStr

from ycode.agent import (
    AgentMode,
    AgentRequestSnapshot,
    AgentTermination,
    AgentTurnResult,
    AgentTurnStream,
    FinalResponseEvent,
)
from ycode.config import ProviderConfig, ProviderProtocol
from ycode.core import (
    AgentModelRequest,
    ChatMessage,
    TokenUsage,
)
from ycode.security import PermissionMode
from ycode.subagents import (
    SubagentCreationMode,
    SubagentInvocation,
    SubagentProviderPool,
    SubagentRoleConfig,
    SubagentRoleSnapshot,
    SubagentRunMode,
    SubagentRunner,
    SubagentStatus,
)
from ycode.tools import (
    PydanticToolArguments,
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRegistry,
)


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadTool:
    timeout_seconds = 1.0
    definition = ToolDefinition(
        "read_file",
        "read",
        ToolAccess.READ,
        PydanticToolArguments(NoArguments),
    )

    async def execute(
        self,
        arguments: NoArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult("ok")


class FakeProvider:
    async def stream_chat(self, messages):
        del messages
        if False:
            yield

    async def stream_agent(self, request):
        del request
        if False:
            yield

    async def close(self) -> None:
        pass


class FakeLoop:
    def __init__(
        self,
        termination: AgentTermination = AgentTermination.COMPLETED,
        text: str = "done",
        *,
        block: asyncio.Event | None = None,
    ) -> None:
        self.termination = termination
        self.text = text
        self.block = block
        self.started = None
        self.closed = False

    def start_turn(self, history, user_message, mode):
        self.started = ("defined", tuple(history), user_message, mode)
        return self._turn(user_message)

    def start_seeded_turn(self, request, mode):
        self.started = ("fork", request, mode)
        return self._turn(request.continuation_messages[-1])

    def _turn(self, user_message):
        async def produce(turn):
            if self.block is not None:
                await turn.run_child(self.block.wait())
            assistant = ChatMessage.assistant_text(self.text)
            messages = (user_message, assistant)
            if self.termination is AgentTermination.COMPLETED:
                turn.complete(
                    AgentTurnResult(
                        self.termination,
                        messages,
                        assistant,
                        usage=TokenUsage(10, 3, 4, 5),
                    )
                )
                yield FinalResponseEvent(assistant)
                return
            turn.complete(
                AgentTurnResult(
                    self.termination,
                    messages,
                    error_code="fake_error" if self.termination is AgentTermination.ERROR else "",
                    error_message="stopped",
                    usage=TokenUsage(10, 3, 4, 5),
                )
            )
            if False:
                yield

        return AgentTurnStream(produce)

    async def close(self) -> None:
        self.closed = True


def provider_config(name: str = "current") -> ProviderConfig:
    return ProviderConfig(
        name=name,
        protocol=ProviderProtocol.ANTHROPIC,
        model=name,
        base_url="https://example.com",
        api_key=SecretStr("secret"),
    )


def make_runner(loop: FakeLoop):
    registry = ToolRegistry()
    registry.register(ReadTool())
    provider = FakeProvider()
    pool = SubagentProviderPool(
        provider_config(),
        provider,  # type: ignore[arg-type]
        lambda name: provider_config(name),
        lambda item: FakeProvider(),
    )
    runtimes = []

    def factory(runtime):
        runtimes.append(runtime)
        return loop

    runner = SubagentRunner(
        pool,
        registry,
        factory,  # type: ignore[arg-type]
        frozenset({"read_file"}),
        clock=lambda: datetime(2026, 8, 16, tzinfo=UTC),
    )
    parent_request = AgentModelRequest(
        messages=(ChatMessage.user_text("parent"),),
        system_prompt=("stable",),
        supplements=("runtime",),
        tools=(ReadTool.definition,),
    )
    parent = AgentRequestSnapshot(
        "turn-1",
        parent_request,
        AgentMode.AGENT,
        PermissionMode.STRICT,
        frozenset({"read_file"}),
    )
    return runner, parent, runtimes


def defined_invocation(role: SubagentRoleSnapshot) -> SubagentInvocation:
    return SubagentInvocation(
        "inspect",
        role,
        SubagentCreationMode.DEFINED,
        SubagentRunMode.SYNC,
        "turn-1",
    )


async def test_defined_runner_uses_role_runtime_and_maps_usage() -> None:
    loop = FakeLoop()
    runner, parent, runtimes = make_runner(loop)
    role = SubagentRoleSnapshot(
        SubagentRoleConfig(
            "review",
            "review",
            "review carefully",
            max_rounds=7,
            permission=PermissionMode.ALLOW,
        ),
        "review.md",
    )

    result = await runner.run("task-1", defined_invocation(role), parent)

    assert result.status is SubagentStatus.COMPLETED
    assert result.result == "done"
    assert result.usage == TokenUsage(10, 3, 4, 5)
    assert runtimes[0].role is role
    assert runtimes[0].permission_mode is PermissionMode.STRICT
    assert runtimes[0].max_rounds == 7
    assert loop.started[0] == "defined"
    assert loop.closed


async def test_fork_runner_preserves_parent_and_adds_strong_task_message() -> None:
    loop = FakeLoop()
    runner, parent, runtimes = make_runner(loop)
    invocation = SubagentInvocation(
        "find evidence",
        None,
        SubagentCreationMode.FORK,
        SubagentRunMode.ASYNC,
        "turn-1",
    )

    result = await runner.run("task-2", invocation, parent)

    assert result.status is SubagentStatus.COMPLETED
    seed = loop.started[1]
    assert seed.messages == parent.request.messages
    assert seed.system_prompt == parent.request.system_prompt
    assert seed.supplements == parent.request.supplements
    assert seed.tools == parent.request.tools
    task = seed.continuation_messages[-1].text
    assert "不得创建任何子 Agent" in task
    assert "find evidence" in task
    assert runtimes[0].preserve_seed_prefix
    assert runtimes[0].max_rounds == 10


async def test_runner_maps_limit_error_and_last_text() -> None:
    loop = FakeLoop(AgentTermination.LIMIT_REACHED, "last useful text")
    runner, parent, _ = make_runner(loop)
    role = SubagentRoleSnapshot(SubagentRoleConfig("review", "review", "work"), "role.md")

    result = await runner.run("task-3", defined_invocation(role), parent)

    assert result.status is SubagentStatus.LIMIT_REACHED
    assert result.result == "last useful text"
    assert result.error is not None and result.error.code == "limit_reached"


async def test_runner_converts_external_cancellation_to_cancelled() -> None:
    blocker = asyncio.Event()
    loop = FakeLoop(block=blocker)
    runner, parent, _ = make_runner(loop)
    role = SubagentRoleSnapshot(SubagentRoleConfig("review", "review", "work"), "role.md")

    task = asyncio.create_task(runner.run("task-4", defined_invocation(role), parent))
    await asyncio.sleep(0)
    task.cancel()
    result = await task

    assert result.status is SubagentStatus.CANCELLED
    assert result.error is not None and result.error.code == "cancelled"
