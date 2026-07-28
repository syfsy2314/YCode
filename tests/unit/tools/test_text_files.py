import asyncio
import codecs
import os
import threading
from pathlib import Path

import pytest

from ycode.tools import ToolError
from ycode.tools.async_utils import run_cancellable_thread
from ycode.tools.text_files import TextFileService


def temporary_files(directory: Path, target_name: str) -> list[Path]:
    return list(directory.glob(f".{target_name}.*.tmp"))


@pytest.mark.asyncio
async def test_reads_utf8_bom_and_normalizes_crlf(tmp_path: Path) -> None:
    path = tmp_path / "bom.txt"
    path.write_bytes(codecs.BOM_UTF8 + "第一行\r\n第二行\r\n".encode())
    service = TextFileService()

    decoded = await service.read(path)

    assert decoded.text == "第一行\n第二行\n"
    assert decoded.has_bom
    assert decoded.newline == "\r\n"
    assert decoded.total_lines == 2
    assert not decoded.mixed_newlines


@pytest.mark.asyncio
async def test_detects_mixed_newlines_using_first_style(tmp_path: Path) -> None:
    path = tmp_path / "mixed.txt"
    path.write_bytes(b"one\ntwo\r\nthree\r")

    decoded = await TextFileService().read(path)

    assert decoded.text == "one\ntwo\nthree\n"
    assert decoded.newline == "\n"
    assert decoded.mixed_newlines


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "code"),
    [(b"text\x00data", "binary_file"), (b"\xff\xfe", "unsupported_encoding")],
)
async def test_rejects_binary_and_non_utf8(
    tmp_path: Path,
    content: bytes,
    code: str,
) -> None:
    path = tmp_path / "invalid.txt"
    path.write_bytes(content)

    with pytest.raises(ToolError) as error:
        await TextFileService().read(path)
    assert error.value.code == code


@pytest.mark.asyncio
async def test_atomically_creates_utf8_without_bom_and_with_crlf(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"
    service = TextFileService()

    await service.atomic_write(path, "one\ntwo", require_absent=True)

    assert path.read_bytes() == b"one\r\ntwo"
    assert temporary_files(tmp_path, "new.txt") == []


@pytest.mark.asyncio
async def test_require_absent_preserves_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "existing.txt"
    path.write_text("original", encoding="utf-8")

    with pytest.raises(ToolError) as error:
        await TextFileService().atomic_write(path, "replacement", require_absent=True)

    assert error.value.code == "path_exists"
    assert path.read_text(encoding="utf-8") == "original"
    assert temporary_files(tmp_path, "existing.txt") == []


@pytest.mark.asyncio
async def test_replace_failure_preserves_original_and_cleans_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "existing.txt"
    path.write_text("original", encoding="utf-8")

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(ToolError) as error:
        await TextFileService().atomic_write(path, "replacement")

    assert error.value.code == "file_write_failed"
    assert path.read_text(encoding="utf-8") == "original"
    assert temporary_files(tmp_path, "existing.txt") == []


@pytest.mark.asyncio
async def test_cancellable_thread_operation_waits_for_cleanup() -> None:
    started = threading.Event()
    cleaned = threading.Event()

    def operation(cancelled: threading.Event) -> None:
        started.set()
        cancelled.wait(timeout=2)
        cleaned.set()

    task = asyncio.create_task(run_cancellable_thread(operation))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
