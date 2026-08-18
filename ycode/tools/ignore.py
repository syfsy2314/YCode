"""根 .gitignore 与工作区文件遍历。"""

import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from pathspec import GitIgnoreSpec

from ycode.tools.async_utils import check_thread_cancelled
from ycode.tools.errors import ToolError
from ycode.tools.paths import WorkspacePathResolver


class IgnoreMatcher:
    def __init__(self, workspace: Path) -> None:
        ignore_file = workspace / ".gitignore"
        try:
            lines = (
                ignore_file.read_text(encoding="utf-8-sig").splitlines()
                if ignore_file.is_file()
                else ()
            )
        except (OSError, UnicodeDecodeError) as error:
            raise ToolError("invalid_gitignore", "无法读取工作区根 .gitignore。") from error
        self._spec = GitIgnoreSpec.from_lines(lines)

    def is_ignored(self, relative: str, *, is_directory: bool = False) -> bool:
        parts = PurePosixPath(relative).parts
        if parts and parts[0] == ".git":
            return True
        candidate = f"{relative.rstrip('/')}/" if is_directory else relative
        return self._spec.match_file(candidate)


class WorkspaceFileWalker:
    def __init__(
        self,
        resolver: WorkspacePathResolver,
        ignore: IgnoreMatcher,
        exclusions: tuple[Path, ...] = (),
    ) -> None:
        self._resolver = resolver
        self._ignore = ignore
        self._exclusions = tuple(path.resolve(strict=False) for path in exclusions)

    def iter_files(
        self,
        start: Path,
        cancelled: threading.Event,
    ) -> Iterator[tuple[Path, str]]:
        yield from self._iter_root(
            start,
            cancelled,
            respect_ignore=_within(start, self._resolver.workspace),
        )
        for virtual_root in self._resolver.virtual_search_roots(start):
            yield from self._iter_root(virtual_root, cancelled, respect_ignore=False)

    def _iter_root(
        self,
        start: Path,
        cancelled: threading.Event,
        *,
        respect_ignore: bool,
    ) -> Iterator[tuple[Path, str]]:
        if start.is_file():
            relative = self._resolver.relative_display(start)
            if not respect_ignore or not self._ignore.is_ignored(relative):
                yield start, relative
            return

        for directory, directory_names, file_names in os.walk(start, followlinks=False):
            check_thread_cancelled(cancelled)
            current = Path(directory)
            kept_directories: list[str] = []
            for name in sorted(directory_names):
                candidate = current / name
                if any(_within(candidate, excluded) for excluded in self._exclusions):
                    continue
                if candidate.is_symlink() or os.path.isjunction(candidate):
                    continue
                try:
                    relative = self._resolver.relative_display(candidate)
                except ToolError:
                    continue
                if not respect_ignore or not self._ignore.is_ignored(relative, is_directory=True):
                    kept_directories.append(name)
            directory_names[:] = kept_directories

            for name in sorted(file_names):
                check_thread_cancelled(cancelled)
                candidate = current / name
                if any(_within(candidate, excluded) for excluded in self._exclusions):
                    continue
                try:
                    resolved = self._resolver.resolve_discovered_file(candidate)
                    relative = self._resolver.relative_display(resolved)
                except ToolError:
                    continue
                if not respect_ignore or not self._ignore.is_ignored(relative):
                    yield resolved, relative


def _within(path: Path, root: Path) -> bool:
    try:
        root_key = os.path.normcase(str(root.resolve(strict=False)))
        path_key = os.path.normcase(str(path.resolve(strict=False)))
        common = os.path.commonpath((root_key, path_key))
    except (OSError, RuntimeError, ValueError):
        return False
    return os.path.normcase(common) == root_key


def compile_posix_glob(pattern: str) -> re.Pattern[str]:
    if not pattern or "\\" in pattern:
        raise ToolError("invalid_pattern", "Glob 模式必须使用非空 POSIX 路径。")
    pure = PurePosixPath(pattern)
    if (
        pattern.startswith(("/", "./"))
        or "/./" in pattern
        or ".." in pure.parts
        or "." in pure.parts
    ):
        raise ToolError("invalid_pattern", "Glob 模式必须是工作区相对路径且不能包含 . 或 ..。")

    parts: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    parts.append("(?:.*/)?")
                    index += 1
                else:
                    parts.append(".*")
                continue
            parts.append("[^/]*")
        elif character == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(character))
        index += 1
    parts.append("$")
    return re.compile("".join(parts))
