"""子 Agent 工具执行边界与权限模式收窄。"""

from __future__ import annotations

from collections.abc import Iterable

from ycode.agent import ToolPolicyDecision
from ycode.core.messages import ToolCallBlock
from ycode.security.models import PermissionMode
from ycode.subagents.models import SubagentRoleSnapshot, SubagentRunMode
from ycode.tools.contracts import ToolAccess
from ycode.tools.registry import ToolRegistry

GLOBAL_DENIED_TOOLS = frozenset({"run_subagent", "install_skill", "load_skill"})
_PERMISSION_ORDER = {
    PermissionMode.STRICT: 0,
    PermissionMode.DEFAULT: 1,
    PermissionMode.ALLOW: 2,
}


def stricter_permission_mode(
    parent: PermissionMode,
    role: PermissionMode,
) -> PermissionMode:
    if not isinstance(parent, PermissionMode) or not isinstance(role, PermissionMode):
        raise TypeError("权限模式无效")
    return parent if _PERMISSION_ORDER[parent] <= _PERMISSION_ORDER[role] else role


class SubagentToolPolicy:
    def __init__(
        self,
        registry: ToolRegistry,
        base_tool_names: Iterable[str],
        *,
        role: SubagentRoleSnapshot | None,
        run_mode: SubagentRunMode,
        async_allowed_tools: Iterable[str],
        plan_only: bool = False,
    ) -> None:
        self._access = {tool.definition.name: tool.definition.access for tool in registry}
        self._base_tool_names = frozenset(base_tool_names)
        self._role = role
        self._run_mode = run_mode
        self._async_allowed_tools = frozenset(async_allowed_tools)
        self._plan_only = plan_only

    @property
    def executable_tool_names(self) -> frozenset[str]:
        names = self._base_tool_names - GLOBAL_DENIED_TOOLS
        if self._role is not None:
            allowed = self._role.config.allowed_tools
            if allowed is not None:
                names &= allowed
            names -= self._role.config.denied_tools
        if self._run_mode is SubagentRunMode.ASYNC:
            names &= self._async_allowed_tools
        if self._plan_only:
            names = frozenset(name for name in names if self._access.get(name) is ToolAccess.READ)
        return frozenset(names)

    @property
    def base_tool_names(self) -> frozenset[str]:
        return self._base_tool_names

    @property
    def async_allowed_tools(self) -> frozenset[str]:
        return self._async_allowed_tools

    def evaluate(self, call: ToolCallBlock) -> ToolPolicyDecision:
        name = call.name
        if name == "run_subagent":
            return self._deny("subagent_nesting_denied", "子 Agent 不允许创建子 Agent。")
        if name in {"install_skill", "load_skill"}:
            return self._deny(
                "subagent_runtime_expansion_denied",
                "子 Agent 不允许在运行时安装、加载或激活 Skill。",
            )
        if name not in self._base_tool_names:
            return self._deny(
                "subagent_base_tool_denied",
                f"工具不在当前子 Agent 的基础执行集合：{name}",
            )
        if self._role is not None:
            allowed = self._role.config.allowed_tools
            if allowed is not None and name not in allowed:
                return self._deny(
                    "subagent_role_allowlist_denied",
                    f"角色白名单不允许工具：{name}",
                )
            if name in self._role.config.denied_tools:
                return self._deny(
                    "subagent_role_denylist_denied",
                    f"角色黑名单拒绝工具：{name}",
                )
        if self._run_mode is SubagentRunMode.ASYNC and name not in self._async_allowed_tools:
            return self._deny(
                "subagent_async_tool_denied",
                f"异步任务白名单不允许工具：{name}",
            )
        if self._plan_only and self._access.get(name) is not ToolAccess.READ:
            return self._deny(
                "subagent_plan_only_denied",
                f"plan-only 父任务创建的子 Agent 只能执行只读工具：{name}",
            )
        return ToolPolicyDecision(True)

    @staticmethod
    def _deny(code: str, message: str) -> ToolPolicyDecision:
        return ToolPolicyDecision(False, code, message)
