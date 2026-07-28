"""使用原文字面唯一匹配编辑文本文件。"""

from pydantic import BaseModel, ConfigDict, Field

from ycode.tools.builtin.read_file import _newline_name
from ycode.tools.contracts import (
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)
from ycode.tools.errors import ToolError
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService


class EditFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="工作区内的现有文本文件路径")
    old_text: str = Field(min_length=1, description="必须在文件中恰好出现一次的原文")
    new_text: str = Field(description="用于替换原文的新文本")


class EditFileTool:
    definition = ToolDefinition(
        name="edit_file",
        description="按字面原文唯一匹配编辑 UTF-8 文件，并保留 BOM 与换行风格。",
        access=ToolAccess.WRITE,
        arguments_model=EditFileArguments,
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
        arguments: EditFileArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del context
        path = self._resolver.resolve_existing_file(arguments.path)
        decoded = await self._text_files.read(path)
        old_text = _normalize_newlines(arguments.old_text)
        new_text = _normalize_newlines(arguments.new_text)
        if old_text == new_text:
            raise ToolError("no_change", "新文本与原文本相同，文件未修改。")

        match_count = decoded.text.count(old_text)
        if match_count == 0:
            raise ToolError("match_not_found", "原文在目标文件中没有匹配，文件未修改。")
        if match_count > 1:
            raise ToolError(
                "multiple_matches",
                "原文在目标文件中匹配多次，文件未修改。",
                metadata={"match_count": match_count},
            )

        updated = decoded.text.replace(old_text, new_text, 1)
        await self._text_files.atomic_write(
            path,
            updated,
            has_bom=decoded.has_bom,
            newline=decoded.newline,
        )
        return ToolExecutionResult(
            content="文件编辑成功。",
            metadata={
                "path": self._resolver.relative_display(path),
                "match_count": 1,
                "has_bom": decoded.has_bom,
                "newline": _newline_name(decoded.newline),
                "newline_normalized": decoded.mixed_newlines,
            },
        )


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")
