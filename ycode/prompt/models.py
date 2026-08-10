"""提示词章节与动态系统补充模型。"""

import re
from dataclasses import dataclass
from enum import StrEnum

_SECTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True, slots=True)
class PromptSection:
    id: str
    priority: int
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _SECTION_ID_PATTERN.fullmatch(self.id):
            raise ValueError("提示词章节 ID 必须使用小写 kebab-case")
        if (
            not isinstance(self.priority, int)
            or isinstance(self.priority, bool)
            or self.priority < 0
        ):
            raise ValueError("提示词章节优先级必须是非负整数")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("提示词章节正文不能为空")


@dataclass(frozen=True, slots=True)
class PromptBundle:
    sections: tuple[PromptSection, ...]

    def __post_init__(self) -> None:
        sections = tuple(self.sections)
        if not sections:
            raise ValueError("提示词包至少需要一个章节")
        if any(not isinstance(section, PromptSection) for section in sections):
            raise TypeError("提示词包只能包含 PromptSection")

        ids = [section.id for section in sections]
        duplicates = sorted({section_id for section_id in ids if ids.count(section_id) > 1})
        if duplicates:
            raise ValueError(f"提示词章节 ID 重复：{', '.join(duplicates)}")

        ordered = tuple(sorted(sections, key=lambda section: (section.priority, section.id)))
        object.__setattr__(self, "sections", ordered)

    @property
    def section_ids(self) -> tuple[str, ...]:
        return tuple(section.id for section in self.sections)

    @property
    def content_blocks(self) -> tuple[str, ...]:
        return tuple(section.content for section in self.sections)

    @property
    def text(self) -> str:
        return "\n\n".join(self.content_blocks)


class SupplementKind(StrEnum):
    ENVIRONMENT = "environment_context"
    MODE = "task_mode"
    TOOL_STATE = "tool_state"
    MEMORY = "memory"
    PROJECT_INSTRUCTIONS = "project_instructions"
    PROJECT_MEMORY = "project_memory"
    SKILL_CATALOG = "available_skills"
    SKILL_INSTRUCTIONS = "active_skills"
    REMINDER = "reminder"
    TOOL_CATALOG = "tool_catalog"


class SupplementScope(StrEnum):
    REQUEST = "request"
    SESSION = "session"


@dataclass(frozen=True, slots=True)
class SystemSupplement:
    kind: SupplementKind
    content: str
    scope: SupplementScope = SupplementScope.REQUEST

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SupplementKind):
            raise TypeError("系统补充类型必须是 SupplementKind")
        if not isinstance(self.scope, SupplementScope):
            raise TypeError("系统补充生命周期必须是 SupplementScope")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("系统补充内容不能为空")

    @property
    def tagged_content(self) -> str:
        tag = self.kind.value
        return f"<{tag}>\n{self.content}\n</{tag}>"


@dataclass(frozen=True, slots=True)
class ProjectContextWarning:
    """项目上下文加载时可忽略的告警。"""

    code: str
    path: str
    message: str

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("项目上下文告警 code 不能为空")
        if not self.path.strip():
            raise ValueError("项目上下文告警 path 不能为空")
        if not self.message.strip():
            raise ValueError("项目上下文告警 message 不能为空")


@dataclass(frozen=True, slots=True)
class ProjectContextSnapshot:
    """应用启动时读取的项目上下文快照。"""

    supplements: tuple[SystemSupplement, ...] = ()
    warnings: tuple[ProjectContextWarning, ...] = ()

    def __post_init__(self) -> None:
        if any(item.scope is not SupplementScope.SESSION for item in self.supplements):
            raise ValueError("项目上下文补充消息必须使用 session 作用域")
