import json
import sys
from pathlib import Path

import pytest

from tests.support.fake_provider import FakeProvider
from ycode.agent import (
    AgentLoop,
    AgentMode,
    FinalResponseEvent,
    ToolApprovalRequested,
)
from ycode.config.environment import SecretRedactor
from ycode.config.mcp import McpConfigSet, StdioMcpServerConfig
from ycode.core import (
    ChatMessage,
    StopReason,
    StreamEnd,
    TextDelta,
    ToolCallBlock,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)
from ycode.mcp.manager import McpManager
from ycode.prompt import EnvironmentCollector, PromptRuntimeContext, build_builtin_prompt
from ycode.security import (
    ApprovalChoice,
    CommandSafetyResult,
    PermissionEngine,
    PermissionSession,
    SecurityConfig,
)
from ycode.tools import ToolContext, ToolExecutor, ToolRegistry, ToolScheduler
from ycode.tools.builtin.tool_search import ToolSearchTool
from ycode.tools.paths import WorkspacePathResolver

SERVER_PATH = Path(__file__).parents[1] / "support" / "mcp_stdio_server.py"


def tool_turn(call_id: str, name: str, arguments: dict[str, object]):
    block = ToolCallBlock(call_id, name, arguments)
    return [
        ToolCallStart(0, call_id, name),
        ToolCallDelta(0, json.dumps(arguments)),
        ToolCallComplete(0, block),
        StreamEnd(StopReason.TOOL_USE, StopReason.TOOL_USE.value),
    ]


def records(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.asyncio
async def test_real_mcp_tool_search_approval_call_and_final_answer(tmp_path: Path) -> None:
    state_file = tmp_path / "agent-mcp.jsonl"
    config = StdioMcpServerConfig.model_validate(
        {
            "name": "fixture",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(SERVER_PATH)],
            "env": {"YCODE_MCP_STATE_FILE": str(state_file)},
            "startup_timeout_seconds": 5,
        }
    )
    registry = ToolRegistry()
    manager = McpManager(McpConfigSet((config,)), registry, SecretRedactor())
    await manager.start()
    registry.register(ToolSearchTool(registry))
    provider = FakeProvider(
        [
            tool_turn("search", "tool_search", {"tool_names": ["mcp_fixture_echo"]}),
            tool_turn("remote", "mcp_fixture_echo", {"value": "from-agent"}),
            [TextDelta(0, "final answer"), StreamEnd(StopReason.END_TURN)],
        ]
    )
    resolver = WorkspacePathResolver(tmp_path)

    class SafeChecker:
        async def check(self, command: str) -> CommandSafetyResult:
            del command
            return CommandSafetyResult(True)

    permission_session = PermissionSession()

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
        permission_engine=PermissionEngine(
            registry,
            resolver,
            SecurityConfig(),
            SafeChecker(),  # type: ignore[arg-type]
        ),
        permission_session=permission_session,
        resource_manager=manager,
    )
    turn = loop.start_turn((), ChatMessage.user_text("use remote echo"), AgentMode.AGENT)
    events = []
    async for event in turn:
        events.append(event)
        if isinstance(event, ToolApprovalRequested):
            assert not any(
                item.get("event") == "call" and item.get("tool") == "echo"
                for item in records(state_file)
            )
            turn.submit_approval(ApprovalChoice.ALLOW_ONCE)

    assert isinstance(events[-1], FinalResponseEvent)
    assert events[-1].message.text == "final answer"
    first_tools = [item.name for item in provider.agent_requests[0].tools]
    second_tools = [item.name for item in provider.agent_requests[1].tools]
    assert first_tools == ["tool_search"]
    assert "mcp_fixture_echo" in second_tools
    assert "mcp_fixture_structured" not in second_tools
    assert any(
        "mcp_fixture_echo" in supplement for supplement in provider.agent_requests[0].supplements
    )
    assert any(
        item.get("event") == "call" and item.get("tool") == "echo" for item in records(state_file)
    )
    await loop.close()
