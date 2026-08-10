"""Skill 配置、目录和调用期状态模型。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ycode.agent.contracts import AgentMode


class SkillExecutionMode(StrEnum):
    SHARED = "shared"
    ISOLATED = "isolated"


class SkillContextKind(StrEnum):
    CURRENT = "current"
    SUMMARY = "summary"
    RECENT = "recent"
    NONE = "none"


class SkillInvocationSource(StrEnum):
    EXPLICIT = "explicit"
    AUTOMATIC = "automatic"
    NESTED = "nested"


class SkillProblemSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class SkillConfig:
    execution_mode: SkillExecutionMode = SkillExecutionMode.SHARED
    model_name: str | None = None
    context_kind: SkillContextKind = SkillContextKind.CURRENT
    recent_turns: int | None = None
    visible_tools: frozenset[str] | None = None
    allowed_tools: frozenset[str] = frozenset()
    argument_hint: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.execution_mode, SkillExecutionMode):
            raise TypeError("Skill 执行模式无效")
        if not isinstance(self.context_kind, SkillContextKind):
            raise TypeError("Skill 上下文策略无效")
        if self.model_name is not None and not self.model_name.strip():
            raise ValueError("Skill 模型名称不能为空")
        if self.visible_tools is not None:
            object.__setattr__(self, "visible_tools", frozenset(self.visible_tools))
        object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))
        if any(not name for name in self.allowed_tools):
            raise ValueError("Skill 预批准工具名称不能为空")
        if self.visible_tools is not None and any(not name for name in self.visible_tools):
            raise ValueError("Skill 可见工具名称不能为空")
        if self.execution_mode is SkillExecutionMode.SHARED:
            if self.model_name is not None:
                raise ValueError("共享 Skill 不能指定模型")
            if self.context_kind is not SkillContextKind.CURRENT:
                raise ValueError("共享 Skill 必须使用 current 上下文")
            if self.recent_turns is not None:
                raise ValueError("共享 Skill 不能指定最近回合数")
        else:
            if self.context_kind is SkillContextKind.CURRENT:
                raise ValueError("隔离 Skill 必须声明上下文策略")
            if self.context_kind is SkillContextKind.RECENT:
                if (
                    not isinstance(self.recent_turns, int)
                    or isinstance(self.recent_turns, bool)
                    or self.recent_turns < 1
                ):
                    raise ValueError("recent 上下文必须指定正整数回合数")
            elif self.recent_turns is not None:
                raise ValueError("只有 recent 上下文可以指定最近回合数")


@dataclass(frozen=True, slots=True)
class SkillSnapshot:
    name: str
    description: str
    root: Path
    source_path: Path
    instructions: str
    config: SkillConfig = field(default_factory=SkillConfig)
    license: str | None = None
    compatibility: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.description.strip():
            raise ValueError("Skill 名称和说明不能为空")
        if not isinstance(self.instructions, str):
            raise TypeError("Skill 正文必须是字符串")
        if not isinstance(self.root, Path) or not isinstance(self.source_path, Path):
            raise TypeError("Skill 路径必须是 Path")
        if not isinstance(self.config, SkillConfig):
            raise TypeError("Skill 配置无效")
        metadata = dict(self.metadata)
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in metadata.items()
        ):
            raise TypeError("Skill metadata 必须是字符串映射")
        object.__setattr__(self, "metadata", MappingProxyType(metadata))
        if self.fingerprint and len(self.fingerprint) != 64:
            raise ValueError("Skill 指纹必须是 SHA-256 十六进制文本")


@dataclass(frozen=True, slots=True)
class SkillProblem:
    code: str
    message: str
    severity: SkillProblemSeverity

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("Skill 诊断字段不能为空")
        if not isinstance(self.severity, SkillProblemSeverity):
            raise TypeError("Skill 诊断严重级别无效")


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    directory_name: str
    source_path: Path
    snapshot: SkillSnapshot | None
    problems: tuple[SkillProblem, ...] = ()

    def __post_init__(self) -> None:
        if not self.directory_name or not isinstance(self.source_path, Path):
            raise ValueError("Skill 目录条目标识无效")
        problems = tuple(self.problems)
        if any(not isinstance(problem, SkillProblem) for problem in problems):
            raise TypeError("Skill 目录诊断无效")
        object.__setattr__(self, "problems", problems)
        has_error = any(problem.severity is SkillProblemSeverity.ERROR for problem in problems)
        if self.snapshot is None and not has_error:
            raise ValueError("缺少快照的 Skill 必须包含 error 诊断")
        if self.snapshot is not None and has_error:
            raise ValueError("不可用 Skill 不能保留有效快照")

    @property
    def available(self) -> bool:
        return self.snapshot is not None


@dataclass(frozen=True, slots=True)
class SkillCatalogState:
    entries: tuple[SkillCatalogEntry, ...] = ()

    def __post_init__(self) -> None:
        entries = tuple(self.entries)
        if any(not isinstance(entry, SkillCatalogEntry) for entry in entries):
            raise TypeError("Skill 目录状态只能包含目录条目")
        ordered = tuple(sorted(entries, key=lambda item: item.directory_name.casefold()))
        object.__setattr__(self, "entries", ordered)

    @property
    def available(self) -> Mapping[str, SkillSnapshot]:
        return MappingProxyType(
            {
                entry.snapshot.name: entry.snapshot
                for entry in self.entries
                if entry.snapshot is not None
            }
        )


@dataclass(frozen=True, slots=True)
class SkillValidationEnvironment:
    tool_names: frozenset[str]
    provider_names: frozenset[str]
    builtin_commands: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_names", frozenset(self.tool_names))
        object.__setattr__(self, "provider_names", frozenset(self.provider_names))
        object.__setattr__(self, "builtin_commands", frozenset(self.builtin_commands))


@dataclass(frozen=True, slots=True)
class SkillInvocation:
    name: str
    arguments: str | None
    source: SkillInvocationSource

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Skill 调用名称不能为空")
        if not isinstance(self.source, SkillInvocationSource):
            raise TypeError("Skill 调用来源无效")


@dataclass(frozen=True, slots=True)
class SkillCallFrame:
    snapshot: SkillSnapshot
    visible_tools: frozenset[str] | None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, SkillSnapshot):
            raise TypeError("Skill 调用帧必须携带快照")
        if self.visible_tools is not None:
            object.__setattr__(self, "visible_tools", frozenset(self.visible_tools))


@dataclass(slots=True)
class SkillTaskAuthorization:
    preapproved_tools: set[str] = field(default_factory=set)
    approved_skill_fingerprints: dict[str, str] = field(default_factory=dict)

    def clear(self) -> None:
        self.preapproved_tools.clear()
        self.approved_skill_fingerprints.clear()


@dataclass(slots=True)
class SkillTaskScope:
    mode: AgentMode
    active_before_turn: Mapping[str, SkillSnapshot] = field(default_factory=dict)
    pending_shared: dict[str, SkillSnapshot] = field(default_factory=dict)
    call_stack: list[SkillCallFrame] = field(default_factory=list)
    authorization: SkillTaskAuthorization = field(default_factory=SkillTaskAuthorization)
    main_branch: bool = True

    def __post_init__(self) -> None:
        if getattr(self.mode, "value", None) not in {"agent", "plan-only"}:
            raise TypeError("Skill 任务模式无效")
        self.active_before_turn = MappingProxyType(dict(self.active_before_turn))

    @property
    def preapproved_tools(self) -> set[str]:
        return self.authorization.preapproved_tools


@dataclass(frozen=True, slots=True)
class SkillCallResult:
    name: str
    execution_mode: SkillExecutionMode
    activated: bool
    final_handoff: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Skill 调用结果名称不能为空")
        if not isinstance(self.execution_mode, SkillExecutionMode):
            raise TypeError("Skill 调用结果模式无效")
        if self.execution_mode is SkillExecutionMode.SHARED and self.final_handoff is not None:
            raise ValueError("共享 Skill 不返回隔离交接")
