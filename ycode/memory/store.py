"""项目记忆目录的安全读取与写入。"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import yaml

from ycode.errors import YCodeError
from ycode.memory.models import (
    MemoryAction,
    MemoryEntry,
    MemorySnapshot,
    MemoryType,
    MemoryUpdatePlan,
    MemoryWarning,
)
from ycode.tools.errors import ToolError
from ycode.tools.paths import WorkspacePathResolver

_INDEX_LINE = re.compile(r"^- \[([^\]\r\n]+)\]\(([^)\r\n]+)\) — ([^\r\n]+)$")
_FRONTMATTER_KEYS = {"name", "description", "type"}


class MemoryStoreError(YCodeError):
    """记忆变更无法安全应用。"""


class MemoryStore:
    """维护 `.ycode/memory/` 下的索引与主题文件。"""

    def __init__(self, project_root: str | Path) -> None:
        self._resolver = WorkspacePathResolver(project_root)
        self._memory_root = self._resolver.workspace / ".ycode" / "memory"
        self._index_path = self._memory_root / "MEMORY.md"

    @property
    def memory_root(self) -> Path:
        return self._memory_root

    @property
    def index_path(self) -> Path:
        return self._index_path

    def load(self) -> MemorySnapshot:
        if not self._index_path.exists():
            return MemorySnapshot()

        warnings: list[MemoryWarning] = []
        entries: list[MemoryEntry] = []
        seen: set[str] = set()
        try:
            memory_root = self._validated_root()
            index_path = self._resolver.resolve_existing_file(self._index_path)
            index_path.relative_to(memory_root)
            content = index_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ToolError, ValueError):
            return MemorySnapshot(
                warnings=(self._warning("index_unreadable", "MEMORY.md", "记忆索引无法读取"),)
            )

        for line_number, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            match = _INDEX_LINE.fullmatch(line)
            if match is None:
                warnings.append(
                    self._warning("invalid_index_line", "MEMORY.md", f"第 {line_number} 行格式无效")
                )
                continue
            name, target, description = match.groups()
            if target in seen:
                warnings.append(self._warning("duplicate_entry", target, "记忆索引路径重复"))
                continue
            seen.add(target)
            entry = self._load_entry(target, name, description, warnings)
            if entry is not None:
                entries.append(entry)

        normalized = "\n".join(self._render_index_line(entry) for entry in entries)
        if normalized:
            normalized += "\n"
        return MemorySnapshot(normalized, tuple(entries), tuple(warnings))

    def apply(self, plan: MemoryUpdatePlan) -> MemorySnapshot:
        current = self.load()
        entries = {entry.path: entry for entry in current.entries}
        order = [entry.path for entry in current.entries]

        for operation in plan.operations:
            exists = operation.path in entries
            if operation.action is MemoryAction.CREATE:
                target = self._memory_root / operation.path
                if exists or target.exists() or target.is_symlink():
                    raise MemoryStoreError("不能覆盖已有或未索引的记忆文件")
                assert operation.entry is not None
                entries[operation.path] = operation.entry
                order.append(operation.path)
            elif operation.action is MemoryAction.UPDATE:
                if not exists:
                    raise MemoryStoreError("只能更新索引中已有的记忆")
                assert operation.entry is not None
                old = entries[operation.path]
                if (
                    old.name,
                    old.description,
                    old.type,
                ) != (
                    operation.entry.name,
                    operation.entry.description,
                    operation.entry.type,
                ):
                    raise MemoryStoreError("更新记忆时不能修改既有元数据")
                entries[operation.path] = operation.entry
            else:
                if not exists:
                    raise MemoryStoreError("只能删除索引中已有的记忆")
                del entries[operation.path]
                order.remove(operation.path)

        self._memory_root.mkdir(parents=True, exist_ok=True)
        try:
            self._validated_root()
        except (ToolError, ValueError) as error:
            raise MemoryStoreError("记忆目录路径无效") from error
        staged: list[tuple[Path, Path]] = []
        try:
            for operation in plan.operations:
                if operation.action is MemoryAction.DELETE:
                    continue
                assert operation.entry is not None
                target = self._safe_write_target(operation.path)
                staged.append((self._stage(self._render_entry(operation.entry)), target))

            index = "\n".join(self._render_index_line(entries[path]) for path in order)
            if index:
                index += "\n"
            staged_index = self._stage(index)

            for temporary, target in staged:
                os.replace(temporary, target)
            os.replace(staged_index, self._index_path)
        except (OSError, ToolError) as error:
            raise MemoryStoreError("记忆文件写入失败") from error
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)
            if "staged_index" in locals():
                staged_index.unlink(missing_ok=True)

        for operation in plan.operations:
            if operation.action is MemoryAction.DELETE:
                try:
                    (self._memory_root / operation.path).unlink(missing_ok=True)
                except OSError:
                    pass
        return self.load()

    def _load_entry(
        self,
        target: str,
        index_name: str,
        index_description: str,
        warnings: list[MemoryWarning],
    ) -> MemoryEntry | None:
        try:
            candidate = Path(target)
            if candidate.is_absolute() or len(candidate.parts) != 1:
                raise ValueError
            path = self._resolver.resolve_existing_file(self._memory_root / candidate)
            memory_root = self._validated_root()
            path.relative_to(memory_root)
            text = path.read_text(encoding="utf-8")
            entry = self._parse_entry(target, text)
            if entry.name != index_name or entry.description != index_description:
                warnings.append(
                    self._warning("metadata_mismatch", target, "索引与 frontmatter 不一致")
                )
                return None
            return entry
        except (OSError, UnicodeError, ToolError, ValueError, yaml.YAMLError):
            warnings.append(self._warning("invalid_entry", target, "记忆主题文件无效或越界"))
            return None

    @staticmethod
    def _parse_entry(path: str, text: str) -> MemoryEntry:
        lines = text.splitlines()
        if not lines or lines[0] != "---":
            raise ValueError
        try:
            end = lines.index("---", 1)
        except ValueError as error:
            raise ValueError from error
        metadata = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(metadata, dict) or set(metadata) != _FRONTMATTER_KEYS:
            raise ValueError
        if not all(isinstance(metadata[key], str) for key in _FRONTMATTER_KEYS):
            raise ValueError
        return MemoryEntry(
            path=path,
            name=metadata["name"],
            description=metadata["description"],
            type=MemoryType(metadata["type"]),
            body="\n".join(lines[end + 1 :]).strip(),
        )

    def _safe_write_target(self, relative_path: str) -> Path:
        target = self._resolver.resolve_write_target(self._memory_root / relative_path)
        target.relative_to(self._memory_root.resolve(strict=True))
        return target

    def _validated_root(self) -> Path:
        ycode_root = self._resolver.workspace / ".ycode"
        if ycode_root.is_symlink() or self._memory_root.is_symlink():
            raise ValueError("记忆目录不能是符号链接")
        return self._resolver.resolve_existing_directory(self._memory_root)

    def _stage(self, content: str) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=".memory-", suffix=".tmp", dir=self._memory_root)
        path = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
                stream.flush()
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path

    @staticmethod
    def _render_entry(entry: MemoryEntry) -> str:
        metadata = yaml.safe_dump(
            {"name": entry.name, "description": entry.description, "type": entry.type.value},
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        return f"---\n{metadata}\n---\n{entry.body.rstrip()}\n"

    @staticmethod
    def _render_index_line(entry: MemoryEntry) -> str:
        return f"- [{entry.name}]({entry.path}) — {entry.description}"

    @staticmethod
    def _warning(code: str, path: str, message: str) -> MemoryWarning:
        return MemoryWarning(code, path or "MEMORY.md", message)
