"""会话级补充状态与模式提醒。"""

from collections.abc import Sequence
from dataclasses import dataclass

from ycode.prompt.models import (
    SupplementKind,
    SupplementScope,
    SystemSupplement,
)

_SUPPORTED_MODES = frozenset({"agent", "plan-only"})
_SESSION_SUPPLEMENT_ORDER = {
    SupplementKind.PROJECT_INSTRUCTIONS: 0,
    SupplementKind.PROJECT_MEMORY: 1,
    SupplementKind.MEMORY: 2,
    SupplementKind.TOOL_STATE: 3,
    SupplementKind.REMINDER: 4,
    SupplementKind.ENVIRONMENT: 5,
    SupplementKind.MODE: 6,
    SupplementKind.TOOL_CATALOG: 7,
}


@dataclass(frozen=True, slots=True)
class PromptTurnContext:
    mode: str
    full_mode_instruction: bool
    supplements: tuple[SystemSupplement, ...]

    def __post_init__(self) -> None:
        if self.mode not in _SUPPORTED_MODES:
            raise ValueError("提示词任务模式无效")
        supplements = tuple(self.supplements)
        if any(not isinstance(item, SystemSupplement) for item in supplements):
            raise TypeError("回合提示词上下文只能包含 SystemSupplement")
        object.__setattr__(self, "supplements", supplements)


def _mode_supplement(mode: str, full: bool) -> SystemSupplement:
    if mode == "plan-only":
        content = (
            "Current task mode: plan-only.\n"
            "Investigate with read tools only, make no changes, and finish with an "
            "implementation plan for user approval."
            if full
            else "Mode reminder: plan-only. Use read tools only and make no changes."
        )
    else:
        content = (
            "Current task mode: agent.\n"
            "Use the available tools as needed to complete the user's task."
            if full
            else "Mode reminder: agent. Continue completing the task with available tools."
        )
    return SystemSupplement(SupplementKind.MODE, content)


class PromptRuntimeContext:
    def __init__(self) -> None:
        self._session_supplements: dict[SupplementKind, SystemSupplement] = {}
        self._last_mode: str | None = None

    @property
    def session_supplements(self) -> tuple[SystemSupplement, ...]:
        return tuple(
            self._session_supplements[kind]
            for kind in sorted(
                self._session_supplements,
                key=lambda item: (_SESSION_SUPPLEMENT_ORDER[item], item.value),
            )
        )

    def set_session_supplement(self, supplement: SystemSupplement) -> None:
        if not isinstance(supplement, SystemSupplement):
            raise TypeError("会话补充必须是 SystemSupplement")
        if supplement.scope is not SupplementScope.SESSION:
            raise ValueError("只有 session 生命周期的补充可以保存到会话")
        self._session_supplements[supplement.kind] = supplement

    def remove_session_supplement(self, kind: SupplementKind) -> None:
        if not isinstance(kind, SupplementKind):
            raise TypeError("会话补充类型必须是 SupplementKind")
        self._session_supplements.pop(kind, None)

    def reset_mode(self) -> None:
        """会话切换后让下一轮重新注入完整模式说明。"""

        self._last_mode = None

    def begin_turn(
        self,
        mode: str,
        request_supplements: Sequence[SystemSupplement] = (),
    ) -> PromptTurnContext:
        if mode not in _SUPPORTED_MODES:
            raise ValueError("提示词任务模式无效")
        request_items = tuple(request_supplements)
        if any(not isinstance(item, SystemSupplement) for item in request_items):
            raise TypeError("请求补充只能包含 SystemSupplement")
        if any(item.scope is not SupplementScope.REQUEST for item in request_items):
            raise ValueError("begin_turn 只接受 request 生命周期的补充")

        full = self._last_mode is None or self._last_mode != mode
        self._last_mode = mode
        supplements = (
            *self.session_supplements,
            *request_items,
            _mode_supplement(mode, full),
        )
        return PromptTurnContext(mode, full, supplements)
