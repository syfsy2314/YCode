"""创建或完整覆盖工作区文本文件。"""

from pydantic import BaseModel, ConfigDict, Field

from ycode.tools.contracts import (
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)
from ycode.tools.errors import ToolError
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService


class WriteFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="工作区内的目标文件路径")
    content: str = Field(description="要写入的完整文本内容")
    overwrite: bool = Field(default=False, description="是否允许覆盖已有文件")


class WriteFileTool:
    definition = ToolDefinition(
        name="write_file",
        description="创建 UTF-8 文本文件，或在明确允许时完整覆盖已有文件。",
        access=ToolAccess.WRITE,
        arguments_model=WriteFileArguments,
    )
    timeout_seconds = 30.0

    def __init__(
        self,
        resolver: WorkspacePathResolver,
        text_files: TextFileService,
    ) -> None:
        self._resolver = resolver
        self._text_files = text_files

    async def execute(
        self,
        arguments: WriteFileArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del context
        path = self._resolver.resolve_write_target(arguments.path)
        existed = path.exists() or path.is_symlink()
        if path.is_dir():
            raise ToolError("not_a_file", "目标不是普通文件。")
        if existed and not arguments.overwrite:
            raise ToolError("path_exists", "目标文件已经存在；如需替换请明确允许覆盖。")

        await self._text_files.atomic_write(
            path,
            arguments.content,
            has_bom=False,
            newline="\r\n",
            require_absent=not existed and not arguments.overwrite,
        )
        return ToolExecutionResult(
            content="文件写入成功。",
            metadata={
                "path": self._resolver.relative_display(path),
                "overwritten": existed,
                "encoding": "utf-8",
                "has_bom": False,
                "newline": "CRLF",
                "characters": len(arguments.content),
            },
        )
