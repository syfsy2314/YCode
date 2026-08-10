"""隔离 Skill 的主会话上下文构造。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from ycode.context.models import ConversationMemory, SummaryResult, SummarySource
from ycode.core.messages import ChatMessage, ToolResultBlock
from ycode.skills.models import SkillContextKind


class SkillConversationCompactor(Protocol):
    async def compact(self, source: SummarySource) -> SummaryResult: ...


@dataclass(frozen=True, slots=True)
class IsolatedSkillContext:
    history: tuple[ChatMessage, ...]
    summary: ConversationMemory | None
    user_task: ChatMessage

    def __post_init__(self) -> None:
        history = tuple(self.history)
        if any(not isinstance(message, ChatMessage) for message in history):
            raise TypeError("隔离 Skill 历史只能包含 ChatMessage")
        if self.user_task.role != "user":
            raise ValueError("隔离 Skill 当前任务必须是用户消息")
        object.__setattr__(self, "history", history)


def recent_complete_turns(
    history: Sequence[ChatMessage],
    count: int,
) -> tuple[ChatMessage, ...]:
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("最近回合数必须是正整数")
    groups: list[list[ChatMessage]] = []
    current: list[ChatMessage] | None = None
    for message in history:
        if _starts_user_turn(message):
            current = [message]
            groups.append(current)
        elif current is not None:
            current.append(message)
    return tuple(message for group in groups[-count:] for message in group)


class SkillContextBuilder:
    def __init__(self, compactor: SkillConversationCompactor) -> None:
        self._compactor = compactor

    async def build(
        self,
        kind: SkillContextKind,
        history: Sequence[ChatMessage],
        memory: ConversationMemory | None,
        user_task: ChatMessage,
        *,
        recent_turns: int | None = None,
    ) -> IsolatedSkillContext:
        committed = tuple(history)
        if kind is SkillContextKind.SUMMARY:
            result = await self._compactor.compact(SummarySource(memory, committed))
            return IsolatedSkillContext((), result.summary, user_task)
        if kind is SkillContextKind.RECENT:
            if recent_turns is None:
                raise ValueError("recent 上下文缺少回合数")
            return IsolatedSkillContext(
                recent_complete_turns(committed, recent_turns),
                None,
                user_task,
            )
        if kind is SkillContextKind.NONE:
            return IsolatedSkillContext((), None, user_task)
        raise ValueError("隔离 Skill 不支持 current 上下文")


def _starts_user_turn(message: ChatMessage) -> bool:
    return message.role == "user" and not message.blocks(ToolResultBlock)


__all__ = ["IsolatedSkillContext", "SkillContextBuilder", "recent_complete_turns"]
