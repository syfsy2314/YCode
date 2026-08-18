"""子 Agent 角色与运行时公共模型。"""

from ycode.config.models import SubagentConfig
from ycode.subagents.catalog import SubagentRoleCatalog
from ycode.subagents.formatting import (
    format_runtime_notification,
    format_task_detail,
    format_task_list,
    format_tool_result,
    task_payload,
)
from ycode.subagents.loader import SubagentRoleLoader
from ycode.subagents.manager import SubagentManager, SubagentManagerError
from ycode.subagents.models import (
    AgentRuntimeNotification,
    ManagedSubagentTask,
    RunSubagentArguments,
    SharedFallbackGrant,
    SubagentCreationMode,
    SubagentError,
    SubagentInvocation,
    SubagentIsolation,
    SubagentRoleCatalogEntry,
    SubagentRoleConfig,
    SubagentRoleProblem,
    SubagentRoleSnapshot,
    SubagentRoleValidationEnvironment,
    SubagentRunMode,
    SubagentStatus,
    SubagentTaskView,
)
from ycode.subagents.policy import SubagentToolPolicy, stricter_permission_mode
from ycode.subagents.providers import SubagentProviderPool
from ycode.subagents.runner import SubagentRunner, SubagentRuntimeRequest

__all__ = [
    "AgentRuntimeNotification",
    "ManagedSubagentTask",
    "RunSubagentArguments",
    "SharedFallbackGrant",
    "SubagentConfig",
    "SubagentCreationMode",
    "SubagentError",
    "SubagentInvocation",
    "SubagentIsolation",
    "SubagentManager",
    "SubagentManagerError",
    "SubagentRoleCatalog",
    "SubagentRoleCatalogEntry",
    "SubagentRoleConfig",
    "SubagentRoleLoader",
    "SubagentRoleProblem",
    "SubagentRoleSnapshot",
    "SubagentRoleValidationEnvironment",
    "SubagentProviderPool",
    "SubagentRunner",
    "SubagentRuntimeRequest",
    "SubagentRunMode",
    "SubagentStatus",
    "SubagentTaskView",
    "SubagentToolPolicy",
    "format_runtime_notification",
    "format_task_detail",
    "format_task_list",
    "format_tool_result",
    "stricter_permission_mode",
    "task_payload",
]
