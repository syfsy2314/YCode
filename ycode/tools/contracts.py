"""工具系统的供应商无关契约。"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ycode.core.messages import FrozenJsonObject, ToolCallBlock, freeze_json
from ycode.tools.arguments import ToolArguments

if TYPE_CHECKING:
    from ycode.skills.models import SkillTaskScope
    from ycode.tools.exposure import ToolExposureSession

_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ToolAccess(StrEnum):
    """工具的固定二级访问分类。"""

    READ = "read"
    WRITE = "write"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ToolDefinition[ArgumentsT]:
    """工具元信息及其参数适配器。"""

    name: str
    description: str
    access: ToolAccess
    arguments: ToolArguments[ArgumentsT]
    defer_loading: bool = False
    timeout_error_code: str = "timeout"

    def __post_init__(self) -> None:
        if not _TOOL_NAME_PATTERN.fullmatch(self.name):
            raise ValueError("工具名称必须使用 snake_case")
        if not self.description.strip():
            raise ValueError("工具描述不能为空")
        if not isinstance(self.access, ToolAccess):
            raise TypeError("工具访问分类必须是 ToolAccess")
        if not isinstance(self.arguments, ToolArguments):
            raise TypeError("工具参数必须满足 ToolArguments 协议")
        if not isinstance(self.defer_loading, bool):
            raise TypeError("延迟加载标记必须是布尔值")
        if not self.timeout_error_code:
            raise ValueError("超时错误码不能为空")

    @property
    def input_schema(self) -> FrozenJsonObject:
        schema = freeze_json(self.arguments.input_schema)
        if not isinstance(schema, Mapping):
            raise TypeError("工具参数 Schema 必须是 JSON object")
        return schema


@dataclass(frozen=True, slots=True)
class ToolContext:
    """单次工具执行共享的工作区上下文。"""

    workspace: Path
    exposure: "ToolExposureSession | None" = None
    skill_scope: "SkillTaskScope | None" = None

    def __post_init__(self) -> None:
        if not isinstance(self.workspace, Path):
            raise TypeError("工具工作区必须是 Path")


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """工具返回给 Agent 的完整结构化结果。"""

    content: str
    is_error: bool = False
    metadata: FrozenJsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("工具结果内容必须是字符串")
        if not isinstance(self.is_error, bool):
            raise TypeError("工具结果错误标记必须是布尔值")
        metadata = freeze_json(self.metadata)
        if not isinstance(metadata, Mapping):
            raise TypeError("工具结果元信息必须是 JSON object")
        object.__setattr__(self, "metadata", metadata)


@dataclass(frozen=True, slots=True)
class ToolExecutionRecord:
    """保留模型位置和执行耗时的单次工具记录。"""

    position: int
    call: ToolCallBlock
    result: ToolExecutionResult
    elapsed_seconds: float

    def __post_init__(self) -> None:
        if (
            not isinstance(self.position, int)
            or isinstance(self.position, bool)
            or self.position < 0
        ):
            raise ValueError("工具调用位置必须是非负整数")
        if not isinstance(self.call, ToolCallBlock):
            raise TypeError("工具执行记录必须携带 ToolCallBlock")
        if not isinstance(self.result, ToolExecutionResult):
            raise TypeError("工具执行记录必须携带 ToolExecutionResult")
        if (
            not isinstance(self.elapsed_seconds, int | float)
            or isinstance(self.elapsed_seconds, bool)
            or self.elapsed_seconds < 0
        ):
            raise ValueError("工具执行耗时必须是非负数")


@runtime_checkable
class Tool[ArgumentsT](Protocol):
    """所有工具必须结构化满足的统一接口。"""

    definition: ToolDefinition[ArgumentsT]
    timeout_seconds: float

    async def execute(
        self,
        arguments: ArgumentsT,
        context: ToolContext,
    ) -> ToolExecutionResult: ...
