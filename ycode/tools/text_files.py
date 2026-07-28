"""UTF-8 文本读取与同目录原子写入。"""

import codecs
import os
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ycode.tools.async_utils import (
    ThreadOperationCancelled,
    check_thread_cancelled,
    run_cancellable_thread,
)
from ycode.tools.errors import ToolError

_CHUNK_SIZE = 64 * 1024
_VALID_NEWLINES = frozenset({"\n", "\r\n", "\r"})


@dataclass(frozen=True, slots=True)
class DecodedTextFile:
    text: str
    has_bom: bool
    newline: str
    total_lines: int
    mixed_newlines: bool = False


class TextFileService:
    """在线程中执行文件 I/O，并在提交前响应协作取消。"""

    async def read(self, path: Path) -> DecodedTextFile:
        return await run_cancellable_thread(lambda cancelled: self._read_sync(path, cancelled))

    async def atomic_write(
        self,
        path: Path,
        content: str,
        *,
        has_bom: bool = False,
        newline: str = "\r\n",
        require_absent: bool = False,
    ) -> None:
        if not isinstance(content, str):
            raise TypeError("写入内容必须是字符串")
        if not isinstance(has_bom, bool):
            raise TypeError("BOM 标记必须是布尔值")
        if newline not in _VALID_NEWLINES:
            raise ValueError("换行风格必须是 LF、CRLF 或 CR")
        if not isinstance(require_absent, bool):
            raise TypeError("目标存在策略必须是布尔值")

        await run_cancellable_thread(
            lambda cancelled: self._atomic_write_sync(
                path,
                content,
                has_bom=has_bom,
                newline=newline,
                require_absent=require_absent,
                cancelled=cancelled,
            )
        )

    def _read_sync(self, path: Path, cancelled: threading.Event) -> DecodedTextFile:
        data = bytearray()
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(_CHUNK_SIZE):
                    self._check_cancelled(cancelled)
                    data.extend(chunk)
            self._check_cancelled(cancelled)
        except ThreadOperationCancelled:
            raise
        except (OSError, PermissionError) as error:
            raise ToolError("file_read_failed", "无法读取目标文件。") from error

        has_bom = data.startswith(codecs.BOM_UTF8)
        payload = bytes(data[len(codecs.BOM_UTF8) :] if has_bom else data)
        if b"\x00" in payload:
            raise ToolError("binary_file", "目标文件是二进制文件。")
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ToolError("unsupported_encoding", "目标文件不是 UTF-8 文本。") from error

        newline, mixed = self._detect_newline(decoded)
        normalized = decoded.replace("\r\n", "\n").replace("\r", "\n")
        return DecodedTextFile(
            text=normalized,
            has_bom=has_bom,
            newline=newline,
            total_lines=len(normalized.splitlines()),
            mixed_newlines=mixed,
        )

    def _atomic_write_sync(
        self,
        path: Path,
        content: str,
        *,
        has_bom: bool,
        newline: str,
        require_absent: bool,
        cancelled: threading.Event,
    ) -> None:
        if not path.parent.is_dir():
            raise ToolError("parent_not_found", "目标父目录不存在。")
        if path.is_dir():
            raise ToolError("not_a_file", "目标不是普通文件。")
        if require_absent and (path.exists() or path.is_symlink()):
            raise ToolError("path_exists", "目标文件已经存在。")

        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        encoded = normalized.replace("\n", newline).encode("utf-8")
        if has_bom:
            encoded = codecs.BOM_UTF8 + encoded

        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                for offset in range(0, len(encoded), _CHUNK_SIZE):
                    self._check_cancelled(cancelled)
                    stream.write(encoded[offset : offset + _CHUNK_SIZE])
                stream.flush()
                os.fsync(stream.fileno())

            self._check_cancelled(cancelled)
            if require_absent:
                try:
                    os.rename(temporary, path)
                except FileExistsError as error:
                    raise ToolError("path_exists", "目标文件已经存在。") from error
            else:
                os.replace(temporary, path)
            temporary = None
        except (ToolError, ThreadOperationCancelled):
            raise
        except OSError as error:
            raise ToolError("file_write_failed", "无法写入目标文件。") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink()

    @staticmethod
    def _detect_newline(text: str) -> tuple[str, bool]:
        styles: list[str] = []
        index = 0
        while index < len(text):
            character = text[index]
            if character == "\r":
                style = "\r\n" if index + 1 < len(text) and text[index + 1] == "\n" else "\r"
                styles.append(style)
                index += len(style)
                continue
            if character == "\n":
                styles.append("\n")
            index += 1

        if not styles:
            return "\r\n", False
        return styles[0], len(set(styles)) > 1

    @staticmethod
    def _check_cancelled(cancelled: threading.Event) -> None:
        check_thread_cancelled(cancelled)
