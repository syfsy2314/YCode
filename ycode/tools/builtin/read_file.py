"""读取工作区 UTF-8 文本文件。"""

from pydantic import BaseModel, ConfigDict, Field

from ycode.tools.arguments import PydanticToolArguments
from ycode.tools.contracts import (
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService

_MAX_OUTPUT_BYTES = 100 * 1024


class ReadFileArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="工作区内的文本文件路径")
    offset: int = Field(default=1, ge=1, description="从 1 开始的起始行号")
    limit: int = Field(default=2000, ge=1, le=2000, description="最多返回的行数")


class ReadFileTool:
    definition = ToolDefinition(
        name="read_file",
        description="读取工作区内的 UTF-8 文本文件，返回带行号的分页内容。",
        access=ToolAccess.READ,
        arguments=PydanticToolArguments(ReadFileArguments),
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
        arguments: ReadFileArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del context
        path = self._resolver.resolve_existing_file(arguments.path)
        decoded = await self._text_files.read(path)
        lines = decoded.text.splitlines()
        selected = lines[arguments.offset - 1 : arguments.offset - 1 + arguments.limit]

        output_parts: list[str] = []
        output_bytes = 0
        returned_lines = 0
        truncated_by_bytes = False
        for line_number, line in enumerate(selected, start=arguments.offset):
            rendered = f"{line_number}: {line}"
            separator = "\n" if output_parts else ""
            available = _MAX_OUTPUT_BYTES - output_bytes - len(separator.encode())
            encoded = rendered.encode("utf-8")
            if available <= 0:
                truncated_by_bytes = True
                break
            if len(encoded) > available:
                prefix = _truncate_utf8(rendered, available)
                if prefix:
                    output_parts.append(f"{separator}{prefix}")
                returned_lines += 1
                output_bytes = _MAX_OUTPUT_BYTES
                truncated_by_bytes = True
                break
            output_parts.append(f"{separator}{rendered}")
            output_bytes += len(separator.encode()) + len(encoded)
            returned_lines += 1

        requested_end = arguments.offset - 1 + arguments.limit
        truncated = truncated_by_bytes or requested_end < len(lines)
        returned_start = arguments.offset if returned_lines else None
        returned_end = arguments.offset + returned_lines - 1 if returned_lines else None
        return ToolExecutionResult(
            content="".join(output_parts),
            metadata={
                "path": self._resolver.relative_display(path),
                "requested_offset": arguments.offset,
                "requested_limit": arguments.limit,
                "returned_start": returned_start,
                "returned_end": returned_end,
                "returned_lines": returned_lines,
                "total_lines": decoded.total_lines,
                "truncated": truncated,
                "truncated_by_bytes": truncated_by_bytes,
                "has_bom": decoded.has_bom,
                "newline": _newline_name(decoded.newline),
                "mixed_newlines": decoded.mixed_newlines,
            },
        )


def _truncate_utf8(value: str, maximum_bytes: int) -> str:
    return value.encode("utf-8")[:maximum_bytes].decode("utf-8", errors="ignore")


def _newline_name(value: str) -> str:
    return {"\n": "LF", "\r\n": "CRLF", "\r": "CR"}[value]
