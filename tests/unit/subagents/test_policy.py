from pydantic import BaseModel, ConfigDict

from ycode.core import ToolCallBlock
from ycode.security import PermissionMode
from ycode.subagents import (
    SubagentRoleConfig,
    SubagentRoleSnapshot,
    SubagentRunMode,
)
from ycode.subagents.policy import SubagentToolPolicy, stricter_permission_mode
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


class NamedTool:
    timeout_seconds = 1.0

    def __init__(self, name: str, access: ToolAccess) -> None:
        self.definition = ToolDefinition(
            name,
            name,
            access,
            PydanticToolArguments(NoArguments),
        )

    async def execute(
        self,
        arguments: NoArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult("ok")


def registry() -> ToolRegistry:
    result = ToolRegistry()
    for name, access in (
        ("read_file", ToolAccess.READ),
        ("write_file", ToolAccess.WRITE),
        ("run_command", ToolAccess.WRITE),
        ("mcp_remote", ToolAccess.UNKNOWN),
        ("run_subagent", ToolAccess.READ),
        ("load_skill", ToolAccess.READ),
    ):
        result.register(NamedTool(name, access))
    return result


def call(name: str) -> ToolCallBlock:
    return ToolCallBlock("call-1", name, {})


def role(
    *,
    allowed: frozenset[str] | None = None,
    denied: frozenset[str] = frozenset(),
) -> SubagentRoleSnapshot:
    return SubagentRoleSnapshot(
        SubagentRoleConfig(
            "custom",
            "custom",
            "work",
            allowed_tools=allowed,
            denied_tools=denied,
        ),
        "custom.md",
    )


def policy(
    *,
    selected_role: SubagentRoleSnapshot | None = None,
    run_mode: SubagentRunMode = SubagentRunMode.SYNC,
    async_allowed: frozenset[str] = frozenset({"read_file", "write_file", "run_command"}),
    plan_only: bool = False,
) -> SubagentToolPolicy:
    return SubagentToolPolicy(
        registry(),
        {
            "read_file",
            "write_file",
            "run_command",
            "mcp_remote",
            "run_subagent",
            "load_skill",
        },
        role=selected_role,
        run_mode=run_mode,
        async_allowed_tools=async_allowed,
        plan_only=plan_only,
    )


def test_global_nesting_and_runtime_expansion_are_always_denied() -> None:
    current = policy(selected_role=role(allowed=frozenset({"run_subagent", "load_skill"})))

    assert current.evaluate(call("run_subagent")).code == "subagent_nesting_denied"
    assert current.evaluate(call("load_skill")).code == "subagent_runtime_expansion_denied"


def test_base_set_role_allowlist_then_denylist_shrink_execution() -> None:
    current = SubagentToolPolicy(
        registry(),
        {"read_file", "write_file"},
        role=role(
            allowed=frozenset({"read_file", "write_file", "run_command"}),
            denied=frozenset({"write_file"}),
        ),
        run_mode=SubagentRunMode.SYNC,
        async_allowed_tools=(),
    )

    assert current.evaluate(call("read_file")).allowed
    assert current.evaluate(call("write_file")).code == "subagent_role_denylist_denied"
    assert current.evaluate(call("run_command")).code == "subagent_base_tool_denied"


def test_async_external_tool_requires_explicit_global_allowlist() -> None:
    denied = policy(run_mode=SubagentRunMode.ASYNC)
    allowed = policy(
        run_mode=SubagentRunMode.ASYNC,
        async_allowed=frozenset({"read_file", "mcp_remote"}),
    )

    assert denied.evaluate(call("mcp_remote")).code == "subagent_async_tool_denied"
    assert allowed.evaluate(call("mcp_remote")).allowed


def test_plan_only_hard_cap_rejects_write_and_unknown_access() -> None:
    current = policy(plan_only=True)

    assert current.evaluate(call("read_file")).allowed
    assert current.evaluate(call("write_file")).code == "subagent_plan_only_denied"
    assert current.evaluate(call("mcp_remote")).code == "subagent_plan_only_denied"
    assert current.executable_tool_names == frozenset({"read_file"})


def test_defined_permission_can_only_become_stricter() -> None:
    assert (
        stricter_permission_mode(PermissionMode.DEFAULT, PermissionMode.ALLOW)
        is PermissionMode.DEFAULT
    )
    assert (
        stricter_permission_mode(PermissionMode.ALLOW, PermissionMode.STRICT)
        is PermissionMode.STRICT
    )
    assert (
        stricter_permission_mode(PermissionMode.DEFAULT, PermissionMode.STRICT)
        is PermissionMode.STRICT
    )
