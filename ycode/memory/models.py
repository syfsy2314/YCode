"""项目记忆的数据模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class MemoryType(StrEnum):
    USER_PREFERENCE = "user_preference"
    CORRECTION_FEEDBACK = "correction_feedback"
    PROJECT_KNOWLEDGE = "project_knowledge"
    REFERENCE = "reference"


_TYPE_PREFIXES = {
    MemoryType.USER_PREFERENCE: "user-",
    MemoryType.CORRECTION_FEEDBACK: "feedback-",
    MemoryType.PROJECT_KNOWLEDGE: "project-",
    MemoryType.REFERENCE: "reference-",
}
_MEMORY_FILENAME = re.compile(
    r"^(?:user|feedback|project|reference)-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.md$"
)


def _validate_memory_path(value: str) -> None:
    if not isinstance(value, str):
        raise TypeError("记忆路径必须是字符串")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or not _MEMORY_FILENAME.fullmatch(value):
        raise ValueError("记忆文件必须是单层、带分类前缀的小写 Markdown 文件")


@dataclass(frozen=True, slots=True)
class MemoryWarning:
    code: str
    path: str
    message: str

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.path.strip() or not self.message.strip():
            raise ValueError("记忆告警字段不能为空")


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    path: str
    name: str
    description: str
    type: MemoryType
    body: str

    def __post_init__(self) -> None:
        _validate_memory_path(self.path)
        if not isinstance(self.type, MemoryType):
            raise TypeError("记忆 type 必须是 MemoryType")
        if not self.path.startswith(_TYPE_PREFIXES[self.type]):
            raise ValueError("记忆文件名前缀必须与 type 一致")
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or "\n" in self.name
            or "\r" in self.name
            or "[" in self.name
            or "]" in self.name
        ):
            raise ValueError("记忆 name 必须是非空单行文本")
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or "\n" in self.description
            or "\r" in self.description
        ):
            raise ValueError("记忆 description 必须是非空单行文本")
        if not isinstance(self.body, str) or not self.body.strip():
            raise ValueError("记忆正文不能为空")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "body", self.body.strip())


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    index_content: str = ""
    entries: tuple[MemoryEntry, ...] = ()
    warnings: tuple[MemoryWarning, ...] = ()

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        warnings = tuple(self.warnings)
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "warnings", warnings)
        paths = [entry.path for entry in entries]
        if len(paths) != len(set(paths)):
            raise ValueError("记忆快照不能包含重复路径")


class MemoryAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True, slots=True)
class MemoryOperation:
    action: MemoryAction
    path: str
    entry: MemoryEntry | None = None

    def __post_init__(self) -> None:
        _validate_memory_path(self.path)
        if self.action in {MemoryAction.CREATE, MemoryAction.UPDATE}:
            if self.entry is None or self.entry.path != self.path:
                raise ValueError("创建或更新操作必须携带同路径的 entry")
        elif self.entry is not None:
            raise ValueError("删除操作不能携带 entry")


@dataclass(frozen=True, slots=True)
class MemoryUpdatePlan:
    operations: tuple[MemoryOperation, ...] = ()

    def __post_init__(self) -> None:
        operations = tuple(self.operations)
        object.__setattr__(self, "operations", operations)
        paths = [operation.path for operation in operations]
        if len(paths) != len(set(paths)):
            raise ValueError("同一更新计划不能重复操作同一路径")


class MemoryUpdateStatus(StrEnum):
    SKIPPED = "skipped"
    NO_CHANGE = "no_change"
    UPDATED = "updated"
    TIMEOUT = "timeout"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MemoryUpdateReport:
    status: MemoryUpdateStatus
    change_count: int = 0
    message: str = ""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.change_count, int)
            or isinstance(self.change_count, bool)
            or self.change_count < 0
        ):
            raise ValueError("记忆变更数量必须是非负整数")
        if self.status is MemoryUpdateStatus.UPDATED and self.change_count < 1:
            raise ValueError("已更新报告必须包含变更")
