"""在工作区内运行固定 PowerShell 命令。"""

from pydantic import BaseModel, ConfigDict, Field

from ycode.tools.arguments import PydanticToolArguments
from ycode.tools.command import CommandRunner
from ycode.tools.contracts import (
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)
from ycode.tools.paths import WorkspacePathResolver


class RunCommandArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, description="要由 PowerShell 执行的命令")
    cwd: str = Field(default=".", min_length=1, description="工作区内的执行目录")


class RunCommandTool:
    definition = ToolDefinition(
        name="run_command",
        description=(
            "在工作区目录内使用 PowerShell 执行命令，并返回退出码和输出；"
            "存在专用文件或搜索工具时应优先使用专用工具。"
        ),
        access=ToolAccess.WRITE,
        arguments=PydanticToolArguments(RunCommandArguments),
    )
    timeout_seconds = 120.0

    def __init__(
        self,
        resolver: WorkspacePathResolver,
        runner: CommandRunner,
    ) -> None:
        self._resolver = resolver
        self._runner = runner

    async def execute(
        self,
        arguments: RunCommandArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del context
        cwd = self._resolver.resolve_command_directory(arguments.cwd)
        command_result = await self._runner.run(arguments.command, cwd)
        content = (
            f"exit_code: {command_result.exit_code}\n"
            f"stdout:\n{command_result.stdout}\n"
            f"stderr:\n{command_result.stderr}"
        )
        return ToolExecutionResult(
            content=content,
            is_error=command_result.exit_code != 0,
            metadata={
                "cwd": self._resolver.relative_display(cwd),
                "exit_code": command_result.exit_code,
                "stdout": command_result.stdout,
                "stderr": command_result.stderr,
                "elapsed_seconds": command_result.elapsed_seconds,
                "truncated": command_result.truncated,
            },
        )
