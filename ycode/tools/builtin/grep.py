"""逐行正则搜索工作区文本文件。"""

import bisect
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ycode.tools.async_utils import check_thread_cancelled, run_cancellable_thread
from ycode.tools.contracts import (
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)
from ycode.tools.errors import ToolError
from ycode.tools.ignore import IgnoreMatcher, WorkspaceFileWalker, compile_posix_glob
from ycode.tools.paths import WorkspacePathResolver


class GrepArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1, description="逐行匹配的 Python 正则表达式")
    path: str = Field(default=".", min_length=1, description="工作区内的文件或目录")
    file_pattern: str | None = Field(
        default=None,
        description="可选的工作区相对 POSIX 文件模式",
    )
    case_sensitive: bool = Field(default=True, description="是否区分大小写")
    max_results: int = Field(default=100, ge=1, le=500, description="最多返回的匹配数量")


@dataclass(frozen=True, order=True, slots=True)
class _Match:
    path: str
    line_number: int
    line: str

    def render(self) -> str:
        return f"{self.path}:{self.line_number}: {self.line}"


class GrepTool:
    definition = ToolDefinition(
        name="grep",
        description="使用 Python 正则逐行搜索工作区 UTF-8 文本，并遵循根 .gitignore。",
        access=ToolAccess.READ,
        arguments_model=GrepArguments,
    )
    timeout_seconds = 30.0

    def __init__(self, resolver: WorkspacePathResolver) -> None:
        self._resolver = resolver

    async def execute(
        self,
        arguments: GrepArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del context
        flags = 0 if arguments.case_sensitive else re.IGNORECASE
        try:
            expression = re.compile(arguments.pattern, flags)
        except re.error as error:
            raise ToolError("invalid_regex", "搜索表达式不是有效的 Python 正则。") from error
        file_matcher = (
            compile_posix_glob(arguments.file_pattern) if arguments.file_pattern else None
        )
        start = self._resolve_start(arguments.path)
        ignore = IgnoreMatcher(self._resolver.workspace)
        walker = WorkspaceFileWalker(self._resolver, ignore)

        def search(cancelled: threading.Event) -> tuple[list[_Match], dict[str, int]]:
            matches: list[_Match] = []
            counts = {
                "total_matches": 0,
                "skipped_binary": 0,
                "skipped_non_utf8": 0,
                "skipped_unreadable": 0,
            }
            for path, relative in walker.iter_files(start, cancelled):
                if file_matcher and not file_matcher.fullmatch(relative):
                    continue
                local, skipped = self._search_file(
                    path,
                    relative,
                    expression,
                    cancelled,
                )
                if skipped:
                    counts[skipped] += 1
                    continue
                counts["total_matches"] += len(local)
                for match in local:
                    bisect.insort(matches, match)
                    if len(matches) > arguments.max_results:
                        matches.pop()
            return matches, counts

        matches, counts = await run_cancellable_thread(search)
        return ToolExecutionResult(
            content="\n".join(match.render() for match in matches),
            metadata={
                "pattern": arguments.pattern,
                "path": self._resolver.relative_display(start),
                "file_pattern": arguments.file_pattern,
                "case_sensitive": arguments.case_sensitive,
                "returned": len(matches),
                "total_matches": counts["total_matches"],
                "truncated": counts["total_matches"] > len(matches),
                "skipped_binary": counts["skipped_binary"],
                "skipped_non_utf8": counts["skipped_non_utf8"],
                "skipped_unreadable": counts["skipped_unreadable"],
            },
        )

    def _resolve_start(self, value: str) -> Path:
        try:
            return self._resolver.resolve_existing_file(value)
        except ToolError as error:
            if error.code != "not_a_file":
                raise
        return self._resolver.resolve_existing_directory(value)

    @staticmethod
    def _search_file(
        path: Path,
        relative: str,
        expression: re.Pattern[str],
        cancelled: threading.Event,
    ) -> tuple[list[_Match], str | None]:
        local: list[_Match] = []
        try:
            with path.open("r", encoding="utf-8-sig", errors="strict", newline=None) as stream:
                for line_number, line in enumerate(stream, start=1):
                    check_thread_cancelled(cancelled)
                    if "\x00" in line:
                        return [], "skipped_binary"
                    text = line.rstrip("\r\n")
                    if expression.search(text):
                        local.append(_Match(relative, line_number, text))
        except UnicodeDecodeError:
            return [], "skipped_non_utf8"
        except OSError:
            return [], "skipped_unreadable"
        return local, None
