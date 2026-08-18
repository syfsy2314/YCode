"""按 POSIX Glob 查找工作区文件。"""

import bisect
import threading

from pydantic import BaseModel, ConfigDict, Field

from ycode.tools.arguments import PydanticToolArguments
from ycode.tools.async_utils import run_cancellable_thread
from ycode.tools.contracts import (
    ToolAccess,
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
)
from ycode.tools.ignore import IgnoreMatcher, WorkspaceFileWalker, compile_posix_glob
from ycode.tools.paths import WorkspacePathResolver


class GlobArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str = Field(min_length=1, description="工作区相对的 POSIX Glob 模式")
    max_results: int = Field(default=200, ge=1, le=1000, description="最多返回的文件数量")


class GlobTool:
    definition = ToolDefinition(
        name="glob",
        description="按工作区相对 POSIX Glob 查找文件，并遵循根 .gitignore。",
        access=ToolAccess.READ,
        arguments=PydanticToolArguments(GlobArguments),
    )
    timeout_seconds = 30.0

    def __init__(self, resolver: WorkspacePathResolver) -> None:
        self._resolver = resolver

    async def execute(
        self,
        arguments: GlobArguments,
        context: ToolContext,
    ) -> ToolExecutionResult:
        del context
        matcher = compile_posix_glob(arguments.pattern)
        ignore = IgnoreMatcher(self._resolver.workspace)
        walker = WorkspaceFileWalker(
            self._resolver,
            ignore,
            self._resolver.search_exclusions(self._resolver.workspace),
        )

        def search(cancelled: threading.Event) -> tuple[list[str], int]:
            matches: list[str] = []
            total_matches = 0
            for _, relative in walker.iter_files(self._resolver.workspace, cancelled):
                if not matcher.fullmatch(relative):
                    continue
                total_matches += 1
                bisect.insort(matches, relative)
                if len(matches) > arguments.max_results:
                    matches.pop()
            return matches, total_matches

        matches, total_matches = await run_cancellable_thread(search)
        return ToolExecutionResult(
            content="\n".join(matches),
            metadata={
                "pattern": arguments.pattern,
                "returned": len(matches),
                "total_matches": total_matches,
                "truncated": total_matches > len(matches),
            },
        )
