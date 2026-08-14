"""YCode 项目 Hook 系统。"""

from ycode.hooks.config import (
    discover_hook_config,
    format_hook_diagnostic,
    load_hook_config,
)
from ycode.hooks.context import HookContextFactory
from ycode.hooks.models import (
    HookConfigLoadResult,
    HookDiagnostic,
    HookDispatchResult,
    HookEvent,
    HookEventName,
    HookPermissionDecision,
    HookRule,
)
from ycode.hooks.runtime import HookRuntime

__all__ = [
    "HookConfigLoadResult",
    "HookContextFactory",
    "HookDiagnostic",
    "HookDispatchResult",
    "HookEvent",
    "HookEventName",
    "HookPermissionDecision",
    "HookRule",
    "HookRuntime",
    "discover_hook_config",
    "format_hook_diagnostic",
    "load_hook_config",
]
