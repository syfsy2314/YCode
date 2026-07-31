"""YCode 提示词系统。"""

from ycode.prompt.builder import PromptBuilder, PromptResource, build_builtin_prompt
from ycode.prompt.environment import EnvironmentCollector, EnvironmentSnapshot, GitStatus
from ycode.prompt.models import (
    PromptBundle,
    PromptSection,
    SupplementKind,
    SupplementScope,
    SystemSupplement,
)
from ycode.prompt.runtime import PromptRuntimeContext, PromptTurnContext

__all__ = [
    "EnvironmentCollector",
    "EnvironmentSnapshot",
    "GitStatus",
    "PromptBuilder",
    "PromptBundle",
    "PromptResource",
    "PromptRuntimeContext",
    "PromptSection",
    "PromptTurnContext",
    "SupplementKind",
    "SupplementScope",
    "SystemSupplement",
    "build_builtin_prompt",
]
