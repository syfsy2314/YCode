from pathlib import Path

import pytest

from ycode.tools import (
    ToolAccess,
    ToolContext,
    ToolExecutionResult,
    ToolRegistry,
    create_builtin_registry,
)
from ycode.tools.builtin import ReadFileArguments, ReadFileTool
from ycode.tools.command import CommandResult
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService


class FakeCommandRunner:
    async def run(self, command: str, cwd: Path) -> CommandResult:
        return CommandResult(0, command, "", 0, False)


def resolved(path: Path) -> Path:
    return path.resolve()


def test_registry_rejects_duplicate_names(tmp_path: Path) -> None:
    resolver = WorkspacePathResolver(tmp_path)
    tool = ReadFileTool(resolver, TextFileService())
    registry = ToolRegistry()
    registry.register(tool)

    with pytest.raises(ValueError, match="重复"):
        registry.register(tool)
    assert registry.get("read_file") is tool
    assert registry.get("missing") is None


def test_builtin_factory_has_stable_names_and_access_filter(tmp_path: Path) -> None:
    registry = create_builtin_registry(
        WorkspacePathResolver(tmp_path),
        TextFileService(),
        FakeCommandRunner(),
    )

    assert tuple(definition.name for definition in registry.definitions()) == (
        "read_file",
        "write_file",
        "edit_file",
        "run_command",
        "glob",
        "grep",
    )
    assert tuple(
        definition.name for definition in registry.definitions(frozenset({ToolAccess.READ}))
    ) == ("read_file", "glob", "grep")
    assert all(definition.input_schema["type"] == "object" for definition in registry.definitions())


@pytest.mark.asyncio
async def test_registered_tool_remains_executable(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("content", encoding="utf-8")
    registry = create_builtin_registry(
        WorkspacePathResolver(tmp_path),
        TextFileService(),
        FakeCommandRunner(),
    )
    tool = registry.get("read_file")

    assert tool is not None
    result = await tool.execute(
        ReadFileArguments(path="file.txt"),
        ToolContext(workspace=resolved(tmp_path)),
    )
    assert isinstance(result, ToolExecutionResult)
    assert result.content == "1: content"
