"""按需加载项目 Skill。"""

from pydantic import BaseModel, ConfigDict, Field

from ycode.skills.models import SkillInvocationSource
from ycode.skills.runtime import SkillRuntime, SkillRuntimeError
from ycode.tools.arguments import PydanticToolArguments
from ycode.tools.contracts import ToolAccess, ToolContext, ToolDefinition, ToolExecutionResult
from ycode.tools.errors import ToolError


class LoadSkillArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="启动目录中披露的 Skill 名称")
    arguments: str | None = Field(default=None, description="原样传给 Skill 的本次任务参数")


class LoadSkillTool:
    definition = ToolDefinition(
        name="load_skill",
        description="加载启动目录中已披露的项目 Skill，并执行其标准工作流程。",
        access=ToolAccess.READ,
        arguments=PydanticToolArguments(LoadSkillArguments),
    )
    timeout_seconds = 300.0

    def __init__(self, runtime: SkillRuntime) -> None:
        self._runtime = runtime

    async def execute(
        self,
        arguments: LoadSkillArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        scope = context.skill_scope
        if scope is None:
            raise ToolError("skill_context_missing", "当前任务没有 Skill 调用上下文。")
        source = (
            SkillInvocationSource.NESTED if scope.call_stack else SkillInvocationSource.AUTOMATIC
        )
        try:
            result = await self._runtime.invoke(
                arguments.name,
                arguments.arguments,
                source,
                scope,
            )
        except SkillRuntimeError as error:
            raise ToolError(error.code, str(error)) from error
        if result.final_handoff is not None:
            return ToolExecutionResult(
                result.final_handoff,
                metadata={"skill": result.name, "mode": result.execution_mode.value},
            )
        status = "activated" if result.activated else "already_active"
        return ToolExecutionResult(
            f'Skill "{result.name}" {status}. Continue the task using its instructions.',
            metadata={
                "skill": result.name,
                "mode": result.execution_mode.value,
                "status": status,
            },
        )


__all__ = ["LoadSkillArguments", "LoadSkillTool"]
