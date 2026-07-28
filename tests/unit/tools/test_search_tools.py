from pathlib import Path

import pytest

from ycode.tools import ToolContext, ToolError
from ycode.tools.builtin import GlobArguments, GlobTool, GrepArguments, GrepTool
from ycode.tools.paths import WorkspacePathResolver


def create_search_workspace(root: Path) -> None:
    (root / "src").mkdir()
    (root / ".hidden").mkdir()
    (root / ".git").mkdir()
    (root / "ignored").mkdir()
    (root / ".gitignore").write_text("ignored/\nignored.py\n", encoding="utf-8")
    (root / "root.py").write_text("Needle at root\n", encoding="utf-8")
    (root / "ignored.py").write_text("Needle ignored\n", encoding="utf-8")
    (root / "src" / "a.py").write_text("first\nneedle lower\nlast\n", encoding="utf-8")
    (root / "src" / "b.txt").write_text("Needle text\n", encoding="utf-8")
    (root / ".hidden" / "visible.py").write_text("Needle hidden\n", encoding="utf-8")
    (root / ".git" / "config").write_text("Needle git\n", encoding="utf-8")
    (root / "ignored" / "skip.py").write_text("Needle ignored dir\n", encoding="utf-8")


def context(workspace: Path) -> ToolContext:
    return ToolContext(workspace=workspace.resolve())


@pytest.mark.asyncio
async def test_glob_supports_recursive_posix_patterns_and_ignore_rules(tmp_path: Path) -> None:
    create_search_workspace(tmp_path)
    tool = GlobTool(WorkspacePathResolver(tmp_path))

    result = await tool.execute(
        GlobArguments(pattern="**/*.py"),
        context(tmp_path),
    )

    assert result.content.splitlines() == [
        ".hidden/visible.py",
        "root.py",
        "src/a.py",
    ]
    assert result.metadata["total_matches"] == 3
    assert result.metadata["truncated"] is False


@pytest.mark.asyncio
async def test_glob_is_stable_and_enforces_result_limit(tmp_path: Path) -> None:
    for name in ["z.txt", "a.txt", "m.txt"]:
        (tmp_path / name).write_text(name, encoding="utf-8")
    tool = GlobTool(WorkspacePathResolver(tmp_path))

    result = await tool.execute(
        GlobArguments(pattern="*.txt", max_results=2),
        context(tmp_path),
    )

    assert result.content.splitlines() == ["a.txt", "m.txt"]
    assert result.metadata["total_matches"] == 3
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("pattern", [r"src\*.py", "/src/*.py", "../*.py", "./*.py"])
async def test_glob_rejects_non_posix_or_escaping_patterns(
    tmp_path: Path,
    pattern: str,
) -> None:
    tool = GlobTool(WorkspacePathResolver(tmp_path))

    with pytest.raises(ToolError) as error:
        await tool.execute(GlobArguments(pattern=pattern), context(tmp_path))
    assert error.value.code == "invalid_pattern"


@pytest.mark.asyncio
async def test_grep_searches_lines_with_case_and_file_pattern(tmp_path: Path) -> None:
    create_search_workspace(tmp_path)
    tool = GrepTool(WorkspacePathResolver(tmp_path))

    result = await tool.execute(
        GrepArguments(
            pattern="needle",
            file_pattern="**/*.py",
            case_sensitive=False,
        ),
        context(tmp_path),
    )

    assert result.content.splitlines() == [
        ".hidden/visible.py:1: Needle hidden",
        "root.py:1: Needle at root",
        "src/a.py:2: needle lower",
    ]
    assert result.metadata["total_matches"] == 3
    assert result.metadata["skipped_binary"] == 0


@pytest.mark.asyncio
async def test_grep_supports_single_file_path_and_result_limit(tmp_path: Path) -> None:
    path = tmp_path / "lines.txt"
    path.write_text("match 3\nmatch 1\nmatch 2\n", encoding="utf-8")
    tool = GrepTool(WorkspacePathResolver(tmp_path))

    result = await tool.execute(
        GrepArguments(pattern="match", path="lines.txt", max_results=2),
        context(tmp_path),
    )

    assert result.content.splitlines() == [
        "lines.txt:1: match 3",
        "lines.txt:2: match 1",
    ]
    assert result.metadata["total_matches"] == 3
    assert result.metadata["truncated"] is True


@pytest.mark.asyncio
async def test_grep_reports_invalid_regex(tmp_path: Path) -> None:
    tool = GrepTool(WorkspacePathResolver(tmp_path))

    with pytest.raises(ToolError) as error:
        await tool.execute(GrepArguments(pattern="["), context(tmp_path))
    assert error.value.code == "invalid_regex"


@pytest.mark.asyncio
async def test_grep_skips_binary_and_non_utf8_files(tmp_path: Path) -> None:
    (tmp_path / "binary.txt").write_bytes(b"match\x00data")
    (tmp_path / "encoded.txt").write_bytes(b"\xff\xfe")
    (tmp_path / "valid.txt").write_text("match\n", encoding="utf-8")
    tool = GrepTool(WorkspacePathResolver(tmp_path))

    result = await tool.execute(GrepArguments(pattern="match"), context(tmp_path))

    assert result.content == "valid.txt:1: match"
    assert result.metadata["skipped_binary"] == 1
    assert result.metadata["skipped_non_utf8"] == 1
