import codecs
from pathlib import Path

import pytest
from pydantic import ValidationError

from ycode.tools import ToolContext, ToolError
from ycode.tools.builtin import (
    EditFileArguments,
    EditFileTool,
    ReadFileArguments,
    ReadFileTool,
    WriteFileArguments,
    WriteFileTool,
)
from ycode.tools.paths import WorkspacePathResolver
from ycode.tools.text_files import TextFileService


def create_tools(workspace: Path) -> tuple[ReadFileTool, WriteFileTool, EditFileTool]:
    resolver = WorkspacePathResolver(workspace)
    text_files = TextFileService()
    return (
        ReadFileTool(resolver, text_files),
        WriteFileTool(resolver, text_files),
        EditFileTool(resolver, text_files),
    )


def tool_context(workspace: Path) -> ToolContext:
    return ToolContext(workspace=workspace.resolve())


@pytest.mark.asyncio
async def test_read_file_returns_numbered_page_and_metadata(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_bytes(codecs.BOM_UTF8 + "一\r\n二\r\n三\r\n四\r\n".encode())
    read_tool, _, _ = create_tools(tmp_path)

    result = await read_tool.execute(
        ReadFileArguments(path="sample.txt", offset=2, limit=2),
        tool_context(tmp_path),
    )

    assert result.content == "2: 二\n3: 三"
    assert result.metadata["path"] == "sample.txt"
    assert result.metadata["returned_start"] == 2
    assert result.metadata["returned_end"] == 3
    assert result.metadata["total_lines"] == 4
    assert result.metadata["truncated"] is True
    assert result.metadata["has_bom"] is True
    assert result.metadata["newline"] == "CRLF"


@pytest.mark.asyncio
async def test_read_file_enforces_line_and_byte_caps(tmp_path: Path) -> None:
    line_path = tmp_path / "lines.txt"
    line_path.write_text("\n".join(str(index) for index in range(2001)), encoding="utf-8")
    byte_path = tmp_path / "large.txt"
    byte_path.write_text("汉" * 50000, encoding="utf-8")
    read_tool, _, _ = create_tools(tmp_path)

    line_result = await read_tool.execute(
        ReadFileArguments(path="lines.txt"),
        tool_context(tmp_path),
    )
    byte_result = await read_tool.execute(
        ReadFileArguments(path="large.txt"),
        tool_context(tmp_path),
    )

    assert line_result.metadata["returned_lines"] == 2000
    assert line_result.metadata["truncated"] is True
    assert len(byte_result.content.encode("utf-8")) <= 100 * 1024
    assert byte_result.metadata["truncated_by_bytes"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("binary.txt", b"a\x00b", "binary_file"),
        ("encoded.txt", b"\xff\xfe", "unsupported_encoding"),
    ],
)
async def test_read_file_rejects_invalid_text(
    tmp_path: Path,
    name: str,
    content: bytes,
    code: str,
) -> None:
    (tmp_path / name).write_bytes(content)
    read_tool, _, _ = create_tools(tmp_path)

    with pytest.raises(ToolError) as error:
        await read_tool.execute(ReadFileArguments(path=name), tool_context(tmp_path))
    assert error.value.code == code


def test_file_argument_models_reject_extra_fields_and_invalid_ranges() -> None:
    with pytest.raises(ValidationError):
        ReadFileArguments.model_validate({"path": "a.txt", "unknown": True})
    with pytest.raises(ValidationError):
        ReadFileArguments(path="a.txt", offset=0)
    with pytest.raises(ValidationError):
        ReadFileArguments(path="a.txt", limit=2001)
    with pytest.raises(ValidationError):
        EditFileArguments(path="a.txt", old_text="", new_text="new")


@pytest.mark.asyncio
async def test_write_file_creates_crlf_utf8_without_bom(tmp_path: Path) -> None:
    _, write_tool, _ = create_tools(tmp_path)

    result = await write_tool.execute(
        WriteFileArguments(path="created.txt", content="one\ntwo"),
        tool_context(tmp_path),
    )

    assert (tmp_path / "created.txt").read_bytes() == b"one\r\ntwo"
    assert result.metadata["path"] == "created.txt"
    assert result.metadata["overwritten"] is False
    assert result.metadata["has_bom"] is False


@pytest.mark.asyncio
async def test_write_file_requires_explicit_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "existing.txt"
    path.write_text("original", encoding="utf-8")
    _, write_tool, _ = create_tools(tmp_path)

    with pytest.raises(ToolError) as error:
        await write_tool.execute(
            WriteFileArguments(path="existing.txt", content="replacement"),
            tool_context(tmp_path),
        )
    assert error.value.code == "path_exists"
    assert path.read_text(encoding="utf-8") == "original"

    result = await write_tool.execute(
        WriteFileArguments(path="existing.txt", content="replacement", overwrite=True),
        tool_context(tmp_path),
    )
    assert path.read_text(encoding="utf-8") == "replacement"
    assert result.metadata["overwritten"] is True


@pytest.mark.asyncio
async def test_write_file_rejects_missing_parent_and_directory(tmp_path: Path) -> None:
    directory = tmp_path / "folder"
    directory.mkdir()
    _, write_tool, _ = create_tools(tmp_path)

    with pytest.raises(ToolError) as missing:
        await write_tool.execute(
            WriteFileArguments(path="missing/file.txt", content="text"),
            tool_context(tmp_path),
        )
    assert missing.value.code == "parent_not_found"

    with pytest.raises(ToolError) as wrong_type:
        await write_tool.execute(
            WriteFileArguments(path="folder", content="text", overwrite=True),
            tool_context(tmp_path),
        )
    assert wrong_type.value.code == "not_a_file"


@pytest.mark.asyncio
async def test_edit_file_unique_match_preserves_bom_and_crlf(tmp_path: Path) -> None:
    path = tmp_path / "edit.txt"
    path.write_bytes(codecs.BOM_UTF8 + b"before\r\nold text\r\nafter\r\n")
    _, _, edit_tool = create_tools(tmp_path)

    result = await edit_tool.execute(
        EditFileArguments(
            path="edit.txt",
            old_text="before\nold text",
            new_text="before\nnew text",
        ),
        tool_context(tmp_path),
    )

    assert path.read_bytes() == codecs.BOM_UTF8 + b"before\r\nnew text\r\nafter\r\n"
    assert result.metadata["match_count"] == 1
    assert result.metadata["has_bom"] is True
    assert result.metadata["newline"] == "CRLF"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("old_text", "new_text", "code", "match_count"),
    [
        ("missing", "new", "match_not_found", None),
        ("same", "new", "multiple_matches", 2),
        ("same", "same", "no_change", None),
    ],
)
async def test_edit_file_failures_leave_file_unchanged(
    tmp_path: Path,
    old_text: str,
    new_text: str,
    code: str,
    match_count: int | None,
) -> None:
    path = tmp_path / "edit.txt"
    original = b"same\nmiddle\nsame\n"
    path.write_bytes(original)
    _, _, edit_tool = create_tools(tmp_path)

    with pytest.raises(ToolError) as error:
        await edit_tool.execute(
            EditFileArguments(path="edit.txt", old_text=old_text, new_text=new_text),
            tool_context(tmp_path),
        )

    assert error.value.code == code
    assert path.read_bytes() == original
    if match_count is not None:
        assert error.value.metadata["match_count"] == match_count
