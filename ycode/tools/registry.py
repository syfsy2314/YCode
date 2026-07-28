"""显式工具注册与六个内建工具装配。"""

from collections.abc import Iterator
from typing import Any

from ycode.tools.builtin import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ReadFileTool,
    RunCommandTool,
    WriteFileTool,
)
from ycode.tools.command import CommandRunner
from ycode.tools.contracts import Tool, ToolAccess, ToolDefinition
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None:
        if not isinstance(tool, Tool):
            raise TypeError("注册对象必须满足 Tool Protocol")
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"工具名称重复：{name}")
        self._tools[name] = tool

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def definitions(
        self,
        allowed_access: frozenset[ToolAccess] | None = None,
    ) -> tuple[ToolDefinition[Any], ...]:
        return tuple(
            tool.definition
            for tool in self._tools.values()
            if allowed_access is None or tool.definition.access in allowed_access
        )

    def __iter__(self) -> Iterator[Tool[Any]]:
        return iter(self._tools.values())


def create_builtin_registry(
    resolver: WorkspacePathResolver,
    text_files: TextFileService,
    command_runner: CommandRunner,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        ReadFileTool(resolver, text_files),
        WriteFileTool(resolver, text_files),
        EditFileTool(resolver, text_files),
        RunCommandTool(resolver, command_runner),
        GlobTool(resolver),
        GrepTool(resolver),
    ):
        registry.register(tool)
    return registry
