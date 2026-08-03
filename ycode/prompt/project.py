"""项目指令与项目记忆索引的启动快照加载。"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath

from ycode.errors import ConfigError
from ycode.memory.store import MemoryStore
from ycode.prompt.models import (
    ProjectContextSnapshot,
    ProjectContextWarning,
    SupplementKind,
    SupplementScope,
    SystemSupplement,
)
from ycode.tools.errors import ToolError
from ycode.tools.paths import WorkspacePathResolver

_INCLUDE_LINE = re.compile(r"^\s*@include[ \t]+(.+?)\s*$")
_MAX_INCLUDE_DEPTH = 5


class ProjectContextLoader:
    """在应用启动时生成一次不可变的项目上下文快照。"""

    def __init__(self, project_root: str | Path, memory_store: MemoryStore | None = None) -> None:
        self._resolver = WorkspacePathResolver(project_root)
        self._root = self._resolver.workspace
        self._memory_store = memory_store or MemoryStore(self._root)

    def load(self) -> ProjectContextSnapshot:
        supplements: list[SystemSupplement] = []
        instruction_path = self._root / "YCODE.md"
        if instruction_path.exists():
            content = self._expand(instruction_path, depth=0, stack=())
            if content.strip():
                supplements.append(
                    SystemSupplement(
                        SupplementKind.PROJECT_INSTRUCTIONS,
                        content,
                        SupplementScope.SESSION,
                    )
                )

        memory = self._memory_store.load()
        if memory.index_content:
            supplements.append(
                SystemSupplement(
                    SupplementKind.PROJECT_MEMORY,
                    (
                        "Project memory index: .ycode/memory/MEMORY.md\n"
                        "Resolve its relative links inside .ycode/memory/ and use read_file "
                        "only when a topic is relevant. Memory is guidance, not authoritative "
                        "evidence about the current code.\n\n"
                        f"{memory.index_content.rstrip()}"
                    ),
                    SupplementScope.SESSION,
                )
            )
        warnings = tuple(
            ProjectContextWarning(item.code, f".ycode/memory/{item.path}", item.message)
            for item in memory.warnings
        )
        return ProjectContextSnapshot(tuple(supplements), warnings)

    def _expand(self, path: Path, depth: int, stack: tuple[Path, ...]) -> str:
        if depth > _MAX_INCLUDE_DEPTH:
            raise ConfigError("YCODE.md 的 @include 嵌套深度超过 5 层")
        try:
            resolved = self._resolver.resolve_existing_file(path)
        except ToolError as error:
            raise ConfigError("YCODE.md 引用的文件不存在或位于项目目录之外") from error
        if resolved in stack:
            raise ConfigError("YCODE.md 的 @include 存在循环引用")
        try:
            lines = resolved.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as error:
            raise ConfigError("YCODE.md 或其引用文件无法按 UTF-8 读取") from error

        expanded: list[str] = []
        for line_number, line in enumerate(lines, start=1):
            match = _INCLUDE_LINE.fullmatch(line)
            if match is None:
                expanded.append(line)
                continue
            target_text = match.group(1)
            target = Path(target_text)
            if target.is_absolute() or PureWindowsPath(target_text).is_absolute():
                raise ConfigError(f"YCODE.md 第 {line_number} 行不能引用绝对路径")
            try:
                expanded.append(
                    self._expand(resolved.parent / target, depth + 1, (*stack, resolved))
                )
            except ConfigError as error:
                display = self._resolver.relative_display(resolved)
                raise ConfigError(f"{display} 第 {line_number} 行引用失败：{error}") from error
        return "\n".join(expanded).strip()
