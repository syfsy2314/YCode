"""创建并运行子 Agent。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ycode.subagents.formatting import format_tool_result, task_payload
from ycode.subagents.manager import SubagentManager, SubagentManagerError
from ycode.subagents.models import RunSubagentArguments as RuntimeArguments
from ycode.subagents.models import SubagentIsolation, SubagentRunMode
from ycode.tools.arguments import PydanticToolArguments
from ycode.tools.contracts import ToolAccess, ToolContext, ToolDefinition, ToolExecutionResult
from ycode.tools.errors import ToolError


class RunSubagentArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, description="交给子 Agent 独立完成的具体任务")
    role: str | None = Field(default=None, description="预定义角色名称；省略时使用 Fork 模式")
    mode: SubagentRunMode | None = Field(
        default=None,
        description="执行方式；定义式默认 sync，Fork 只能使用 async",
    )
    isolation: SubagentIsolation | None = Field(
        default=None,
        description="工作区隔离；省略时使用角色定义，角色也未配置时使用本地工作区",
    )
    shared_fallback_token: str | None = Field(
        default=None,
        description="隔离失败后由系统签发的一次性共享执行授权",
    )

    @field_validator("task", "role", "shared_fallback_token")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("字段不能为空")
        return stripped


class RunSubagentTool:
    definition = ToolDefinition(
        name="run_subagent",
        description=(
            "启动一个子 Agent；指定角色可同步或异步执行，省略角色时 Fork 当前上下文并异步执行。"
            "可用 isolation 为单次任务选择本地工作区或独立 Git Worktree。"
        ),
        access=ToolAccess.READ,
        arguments=PydanticToolArguments(RunSubagentArguments),
    )
    timeout_seconds = 3600.0

    def __init__(self, manager: SubagentManager) -> None:
        self._manager = manager

    async def execute(
        self,
        arguments: RunSubagentArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        scope = context.agent_scope
        if scope is None or scope.current_snapshot is None:
            raise ToolError("subagent_context_missing", "当前任务没有可用的父 Agent 请求快照。")
        try:
            result = await self._manager.start(
                RuntimeArguments(
                    task=arguments.task,
                    role=arguments.role,
                    mode=arguments.mode,
                    shared_fallback_token=arguments.shared_fallback_token,
                    isolation=arguments.isolation,
                ),
                scope.current_snapshot,
            )
        except SubagentManagerError as error:
            raise ToolError(error.code, str(error)) from error
        return ToolExecutionResult(
            format_tool_result(result),
            metadata={"subagent": task_payload(result)},
        )


__all__ = ["RunSubagentArguments", "RunSubagentTool"]
